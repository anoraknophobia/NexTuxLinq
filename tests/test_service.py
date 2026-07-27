"""Hardware-independent tests for NexLinq service orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from nexlinq.config_store import ConfigLoadResult
from nexlinq.defaults import default_configuration, default_profile
from nexlinq.device_worker import DeviceWorker, DeviceWorkerSnapshot
from nexlinq.models import ConfigurationDocument, ProfileConfig
from nexlinq.rendering import render_profile_at_elapsed
from nexlinq.service import NexLinqService
from nexlinq.state import DaemonState
from nexlinq.writer_lock import WriterLock


class FakeStore:
    def __init__(self, configuration: ConfigurationDocument) -> None:
        self.configuration = configuration

    def load(self) -> ConfigLoadResult:
        return ConfigLoadResult(self.configuration)


class FakeWorker:
    def __init__(self) -> None:
        self.callback = None
        self.started = False
        self.stopped = False
        self.frames: list[tuple[bytes, int]] = []
        self.brightness: list[tuple[int, int]] = []

    def set_status_callback(self, callback) -> None:
        self.callback = callback

    def start(self) -> None:
        self.started = True

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append((frame, revision))

    def set_brightness(self, percentage: int, revision: int = 0) -> None:
        self.brightness.append((percentage, revision))

    def stop(self, timeout: float | None = 5.0) -> None:
        self.stopped = True


class Monotonic:
    def __init__(self) -> None:
        self.values = iter((0.0, 0.0, 1.0, 1.0))

    def __call__(self) -> float:
        return next(self.values)


def configuration_with(**changes: object) -> ConfigurationDocument:
    raw = default_profile().to_dict()
    raw.update(changes)
    profile = ProfileConfig.from_dict(raw)
    return ConfigurationDocument(1, "default", {"default": profile})


def test_service_uses_profile_speed_and_preserves_rendered_output() -> None:
    async def scenario() -> None:
        worker = FakeWorker()
        rendered = asyncio.Event()

        async def sleep(delay: float) -> None:
            rendered.set()
            await asyncio.Future()

        configuration = configuration_with(
            scrolling_text="matrix",
            scroll_speed=3.0,
        )
        now = datetime(2026, 7, 27, 20, 36)
        service = NexLinqService(
            config_store=FakeStore(configuration),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=Monotonic(),
            wall_clock=lambda: now,
            sleep=sleep,
        )
        await service.start()
        await asyncio.wait_for(rendered.wait(), 1)
        expected = render_profile_at_elapsed(configuration.active, now, 1.0)

        assert worker.frames == [(expected.to_bytes(), 1)]
        assert worker.brightness == []
        assert service.snapshot().last_frame_generation_time == 1.0
        await service.stop()
        assert worker.stopped

    asyncio.run(scenario())


def test_service_applies_selected_brightness_but_not_null() -> None:
    async def run_one(configuration: ConfigurationDocument) -> list[tuple[int, int]]:
        worker = FakeWorker()
        block = asyncio.Event()

        async def sleep(delay: float) -> None:
            block.set()
            await asyncio.Future()

        service = NexLinqService(
            config_store=FakeStore(configuration),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=Monotonic(),
            sleep=sleep,
        )
        await service.start()
        await asyncio.wait_for(block.wait(), 1)
        await service.stop()
        return worker.brightness

    assert asyncio.run(run_one(default_configuration())) == []
    assert asyncio.run(run_one(configuration_with(brightness=70))) == [(70, 1)]


def test_worker_upload_status_updates_immutable_service_state() -> None:
    worker = FakeWorker()
    monotonic = Monotonic()
    service = NexLinqService(
        config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
        device_worker=worker,  # type: ignore[arg-type]
        monotonic=monotonic,
    )

    service.handle_worker_snapshot(
        DeviceWorkerSnapshot(
            running=True,
            device_available=True,
            device_path="/dev/fake0",
            successful_uploads=1,
        )
    )
    snapshot = service.snapshot()

    assert snapshot.device_available
    assert snapshot.device_path == "/dev/fake0"
    assert snapshot.last_successful_upload_time == 0.0


def test_render_failure_updates_service_state() -> None:
    async def scenario() -> None:
        worker = FakeWorker()

        async def fail_sleep(delay: float) -> None:
            raise RuntimeError("private renderer detail")

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=Monotonic(),
            sleep=fail_sleep,
        )
        await service.start()
        task = service.render_task
        assert task is not None
        with pytest.raises(RuntimeError, match="private renderer detail"):
            await task
        snapshot = service.snapshot()
        assert snapshot.lifecycle_state == "failed"
        assert snapshot.rendering_state == "failed"
        assert snapshot.last_display_error == "Matrix900 rendering failed."
        await service.stop()

    asyncio.run(scenario())


def test_worker_stop_timeout_marks_service_failed() -> None:
    class TimeoutWorker(FakeWorker):
        def stop(self, timeout: float | None = 5.0) -> None:
            raise TimeoutError("private blocked thread detail")

    async def scenario() -> None:
        worker = TimeoutWorker()
        rendered = asyncio.Event()

        async def sleep(delay: float) -> None:
            rendered.set()
            await asyncio.Future()

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            sleep=sleep,
            worker_stop_timeout=0,
        )
        await service.start()
        await asyncio.wait_for(rendered.wait(), 1)
        with pytest.raises(TimeoutError):
            await service.stop()
        snapshot = service.snapshot()
        assert snapshot.lifecycle_state == "failed"
        assert snapshot.rendering_state == "failed"
        assert snapshot.last_display_error == (
            "Matrix900 worker shutdown timed out."
        )

    asyncio.run(scenario())


def test_startup_failure_after_worker_start_stops_worker_and_can_retry(
    tmp_path: Path,
) -> None:
    class BrightnessFailureWorker(DeviceWorker):
        def __init__(self, lock: WriterLock) -> None:
            super().__init__(
                writer_lock=lock,
                discover=lambda: [],
                connection_factory=lambda path: (_ for _ in ()).throw(
                    AssertionError()
                ),
                waiter=lambda delay, stop: stop.wait(1000),
                reconnect_delays=(0.0,),
            )
            self.fail = True

        def set_brightness(self, percentage: int, revision: int = 0) -> None:
            if self.fail:
                self.fail = False
                raise RuntimeError("private brightness startup failure")
            super().set_brightness(percentage, revision)

    async def scenario() -> None:
        lock_path = tmp_path / "matrix900.lock"
        worker = BrightnessFailureWorker(WriterLock(lock_path))
        service = NexLinqService(
            config_store=FakeStore(configuration_with(brightness=50)),  # type: ignore[arg-type]
            device_worker=worker,
        )
        with pytest.raises(RuntimeError, match="private brightness startup failure"):
            await service.start()
        assert not worker.snapshot().running
        assert not service.owns_resources
        assert service.snapshot().lifecycle_state == "failed"
        replacement = WriterLock(lock_path)
        replacement.acquire()
        replacement.release()

        await service.start()
        assert service.owns_resources
        await service.stop()
        assert not worker.snapshot().running

    asyncio.run(scenario())


def test_render_task_creation_failure_unwinds_started_worker() -> None:
    async def scenario() -> None:
        worker = FakeWorker()

        def fail_task_creation(coroutine):
            raise RuntimeError("private task creation failure")

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            task_factory=fail_task_creation,
        )
        with pytest.raises(RuntimeError, match="private task creation failure"):
            await service.start()

        assert worker.started
        assert worker.stopped
        assert not service.owns_resources
        assert service.snapshot().last_display_error == (
            "NexLinq service startup failed."
        )

    asyncio.run(scenario())


def test_late_startup_failure_cancels_render_task_and_stops_worker() -> None:
    class LateFailureState(DaemonState):
        def update(self, **changes: object):
            if changes.get("lifecycle_state") == "running":
                raise RuntimeError("private late startup failure")
            return super().update(**changes)

    async def scenario() -> None:
        worker = FakeWorker()
        tasks: list[asyncio.Task[None]] = []

        async def sleep(delay: float) -> None:
            await asyncio.Future()

        def create_task(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            state=LateFailureState(),
            sleep=sleep,
            task_factory=create_task,
        )
        with pytest.raises(RuntimeError, match="private late startup failure"):
            await service.start()

        assert worker.stopped
        assert not service.owns_resources
        assert len(tasks) == 1
        assert tasks[0].cancelled()
        assert service.snapshot().lifecycle_state == "failed"

    asyncio.run(scenario())
