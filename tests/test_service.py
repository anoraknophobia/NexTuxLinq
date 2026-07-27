"""Hardware-independent tests for NexLinq service orchestration."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from pathlib import Path

import pytest

from nexlinq.config_store import ConfigLoadResult
from nexlinq.defaults import default_configuration, default_profile
from nexlinq.device_worker import DeviceWorker, DeviceWorkerSnapshot
from nexlinq.models import ConfigurationDocument, ProfileConfig
from nexlinq.rendering import render_profile_at_elapsed
from nexlinq.service import NexLinqService, WorkerSubmissionError
from nexlinq.state import DaemonState
from nexlinq.writer_lock import WriterLock


class FakeStore:
    def __init__(self, configuration: ConfigurationDocument) -> None:
        self.configuration = configuration
        self.saved: list[ConfigurationDocument] = []

    def load(self) -> ConfigLoadResult:
        return ConfigLoadResult(self.configuration)

    def save(self, configuration: ConfigurationDocument) -> None:
        self.saved.append(configuration)


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

    def snapshot(self) -> DeviceWorkerSnapshot:
        return DeviceWorkerSnapshot(running=self.started and not self.stopped)


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


def test_unexpected_worker_exit_signals_service_failure() -> None:
    async def scenario() -> None:
        worker = FakeWorker()
        block = asyncio.Event()

        async def sleep(delay: float) -> None:
            block.set()
            await asyncio.Future()

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=Monotonic(),
            sleep=sleep,
        )
        await service.start()
        await asyncio.wait_for(block.wait(), 1)
        failure = asyncio.create_task(service.wait_for_worker_failure())
        service.handle_worker_snapshot(DeviceWorkerSnapshot(running=False))
        await asyncio.wait_for(failure, 1)

        assert service.snapshot().lifecycle_state == "failed"
        assert service.snapshot().rendering_state == "failed"
        assert service.snapshot().last_display_error == (
            "Matrix900 device worker stopped unexpectedly."
        )
        await service.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure_phase",
    ["after-thread-start", "during-initialization", "before-running"],
)
def test_worker_exit_during_startup_rolls_back(
    failure_phase: str,
) -> None:
    class StartupExitWorker(FakeWorker):
        def start(self) -> None:
            super().start()
            if failure_phase == "after-thread-start":
                self.stopped = True
                self.callback(DeviceWorkerSnapshot(running=False))

        def set_brightness(self, percentage: int, revision: int = 0) -> None:
            if failure_phase == "during-initialization":
                self.stopped = True
                self.callback(DeviceWorkerSnapshot(running=False))
            super().set_brightness(percentage, revision)

    async def scenario() -> None:
        worker = StartupExitWorker()

        def create_task(coroutine):
            if failure_phase == "before-running":
                worker.stopped = True
                worker.callback(DeviceWorkerSnapshot(running=False))
            return asyncio.create_task(coroutine)

        service = NexLinqService(
            config_store=FakeStore(configuration_with(brightness=50)),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            task_factory=create_task,
        )
        with pytest.raises(RuntimeError, match="worker stopped"):
            await service.start()
        assert worker.stopped
        assert not service.owns_resources
        assert service.snapshot().lifecycle_state == "failed"
        assert service.snapshot().last_display_error == (
            "NexLinq service startup failed."
        )

    asyncio.run(scenario())


def test_worker_exit_between_verification_and_running_publication_rolls_back() -> None:
    verification_reached = threading.Event()
    callback_started = threading.Event()
    exit_thread: threading.Thread | None = None

    class PublicationBarrierState(DaemonState):
        def update(self, **changes: object):
            if changes.get("lifecycle_state") == "running":
                verification_reached.set()
                assert callback_started.wait(1)
            return super().update(**changes)

    class BarrierWorker(FakeWorker):
        def start(self) -> None:
            nonlocal exit_thread
            super().start()

            def exit_after_verification() -> None:
                assert verification_reached.wait(1)
                self.stopped = True
                callback_started.set()
                self.callback(DeviceWorkerSnapshot(running=False))

            exit_thread = threading.Thread(target=exit_after_verification)
            exit_thread.start()

    async def scenario() -> None:
        worker = BarrierWorker()

        async def sleep(delay: float) -> None:
            await asyncio.Future()

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            state=PublicationBarrierState(),
            sleep=sleep,
        )
        with pytest.raises(RuntimeError, match="worker stopped"):
            await service.start()
        assert not service.owns_resources
        assert service.snapshot().lifecycle_state == "failed"
        assert service.render_task is None

    asyncio.run(scenario())
    assert exit_thread is not None
    exit_thread.join(1)
    assert not exit_thread.is_alive()


def test_worker_exit_immediately_after_running_revokes_service_state() -> None:
    class ExitAfterRunningState(DaemonState):
        worker: FakeWorker

        def update(self, **changes: object):
            snapshot = super().update(**changes)
            if changes.get("lifecycle_state") == "running":
                self.worker.stopped = True
                self.worker.callback(DeviceWorkerSnapshot(running=False))
            return snapshot

    async def scenario() -> None:
        worker = FakeWorker()
        state = ExitAfterRunningState()
        state.worker = worker
        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            state=state,
        )
        with pytest.raises(RuntimeError, match="worker stopped"):
            await service.start()
        assert service.snapshot().lifecycle_state == "failed"
        assert not service.owns_resources

    asyncio.run(scenario())


def test_concurrent_applies_preserve_publication_and_command_order() -> None:
    async def scenario() -> None:
        worker = FakeWorker()
        entered = asyncio.Event()
        release = asyncio.Event()
        gate_calls = 0

        async def gate(kind: str, revision: int) -> None:
            nonlocal gate_calls
            gate_calls += 1
            if gate_calls == 1:
                entered.set()
                await release.wait()

        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            mutation_gate=gate,
        )
        service._configuration = default_configuration()
        service._saved_configuration = default_configuration()
        service._saved_revision = 1
        service._saved_revision_counter = 1
        service.state.update(
            lifecycle_state="running",
            active_configuration_revision=1,
        )
        first = asyncio.create_task(
            service.apply_profile(
                "default",
                configuration_with(scrolling_text="FIRST").active,
                expected_revision=1,
            )
        )
        await entered.wait()
        second = asyncio.create_task(
            service.apply_profile(
                "default",
                configuration_with(
                    scrolling_text="SECOND",
                    brightness=70,
                ).active,
                expected_revision=2,
            )
        )
        await asyncio.sleep(0)
        assert not second.done()
        assert service.snapshot().active_configuration_revision == 1
        release.set()
        await asyncio.gather(first, second)

        assert [revision for _, revision in worker.frames] == [2, 3]
        assert worker.brightness == [(70, 3)]
        assert service.configuration.active.scrolling_text == "SECOND"

    asyncio.run(scenario())


def test_apply_then_save_apply_preserves_command_order() -> None:
    async def scenario() -> None:
        worker = FakeWorker()
        store = FakeStore(default_configuration())
        entered = asyncio.Event()
        release = asyncio.Event()
        first_gate = True

        async def gate(kind: str, revision: int) -> None:
            nonlocal first_gate
            if first_gate:
                first_gate = False
                entered.set()
                await release.wait()

        service = NexLinqService(
            config_store=store,  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            mutation_gate=gate,
        )
        service._configuration = default_configuration()
        service._saved_configuration = default_configuration()
        service._saved_revision = 1
        service._saved_revision_counter = 1
        service.state.update(
            lifecycle_state="running",
            active_configuration_revision=1,
        )
        first = asyncio.create_task(
            service.apply_profile(
                "default",
                configuration_with(scrolling_text="FIRST").active,
                expected_revision=1,
            )
        )
        await entered.wait()
        saved_configuration = configuration_with(
            scrolling_text="SAVED",
            brightness=80,
        )
        second = asyncio.create_task(
            service.save_configuration(
                saved_configuration,
                expected_active_revision=2,
                expected_saved_revision=1,
                apply=True,
            )
        )
        await asyncio.sleep(0)
        assert not second.done()
        release.set()
        await asyncio.gather(first, second)

        assert [revision for _, revision in worker.frames] == [2, 3]
        assert worker.brightness == [(80, 3)]
        assert service.configuration == saved_configuration
        assert store.saved == [saved_configuration]

    asyncio.run(scenario())


def test_submission_rejection_rolls_back_runtime_publication() -> None:
    class RejectingWorker(FakeWorker):
        def submit_frame(self, frame: bytes, revision: int = 0) -> None:
            raise RuntimeError("private worker state")

    async def scenario() -> None:
        worker = RejectingWorker()
        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
        )
        service._configuration = default_configuration()
        service._saved_configuration = default_configuration()
        service.state.update(
            lifecycle_state="running",
            active_configuration_revision=1,
        )
        with pytest.raises(WorkerSubmissionError):
            await service.apply_profile(
                "default",
                configuration_with(scrolling_text="REJECTED").active,
                expected_revision=1,
            )
        assert service.configuration.active.scrolling_text == "CACHYOS"
        assert service.snapshot().active_configuration_revision == 1

    asyncio.run(scenario())


def test_partial_submission_failure_marks_service_failed_without_publication() -> None:
    class BrightnessRejectingWorker(FakeWorker):
        def set_brightness(self, percentage: int, revision: int = 0) -> None:
            raise RuntimeError("private brightness queue state")

    async def scenario() -> None:
        worker = BrightnessRejectingWorker()
        service = NexLinqService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
        )
        service._configuration = default_configuration()
        service._saved_configuration = default_configuration()
        service.state.update(
            lifecycle_state="running",
            active_configuration_revision=1,
        )
        with pytest.raises(WorkerSubmissionError):
            await service.apply_profile(
                "default",
                configuration_with(
                    scrolling_text="PARTIAL",
                    brightness=60,
                ).active,
                expected_revision=1,
            )

        assert [revision for _, revision in worker.frames] == [2]
        assert service.configuration.active.scrolling_text == "CACHYOS"
        snapshot = service.snapshot()
        assert snapshot.active_configuration_revision == 1
        assert snapshot.lifecycle_state == "failed"
        assert "submission failed" in snapshot.last_display_error.lower()

    asyncio.run(scenario())


def test_atomic_configuration_and_preview_capture_wait_for_publication() -> None:
    service = NexLinqService(
        config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
        device_worker=FakeWorker(),  # type: ignore[arg-type]
        monotonic=lambda: 10.0,
    )
    service._configuration = default_configuration()
    service._saved_configuration = default_configuration()
    service._saved_revision = 1
    service.state.update(
        lifecycle_state="running",
        active_configuration_revision=1,
    )
    candidate = configuration_with(scrolling_text="ATOMIC")
    partial = threading.Event()
    release = threading.Event()

    def publish() -> None:
        with service._coordination_lock:
            service._configuration = candidate
            partial.set()
            assert release.wait(1)
            service.state.update(active_configuration_revision=2)

    thread = threading.Thread(target=publish)
    thread.start()
    assert partial.wait(1)
    captured: list[object] = []

    def read() -> None:
        captured.extend((service.get_configuration(), service.get_preview()))

    reader = threading.Thread(target=read)
    reader.start()
    assert reader.is_alive()
    release.set()
    thread.join(1)
    reader.join(1)

    configuration, preview = captured
    assert configuration.configuration.active.scrolling_text == "ATOMIC"
    assert configuration.active_revision == 2
    assert preview.profile.scrolling_text == "ATOMIC"
    assert preview.active_revision == 2


def test_render_loop_submits_one_atomic_profile_revision_pair() -> None:
    partial = threading.Event()
    capture_attempted = threading.Event()
    worker = FakeWorker()
    candidate = configuration_with(scrolling_text="ATOMIC")
    now = datetime(2026, 7, 27, 20, 36)

    class CoordinatedService(NexLinqService):
        def _capture_active_profile(self):
            capture_attempted.set()
            return super()._capture_active_profile()

    async def scenario() -> None:
        rendered = asyncio.Event()

        async def sleep(delay: float) -> None:
            rendered.set()
            await asyncio.Future()

        service = CoordinatedService(
            config_store=FakeStore(default_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=lambda: 0.0,
            wall_clock=lambda: now,
            sleep=sleep,
        )
        service._configuration = default_configuration()
        service.state.update(
            lifecycle_state="running",
            active_configuration_revision=1,
        )

        def publish() -> None:
            with service._coordination_lock:
                service._configuration = candidate
                partial.set()
                assert capture_attempted.wait(1)
                service.state.update(active_configuration_revision=2)

        publisher = threading.Thread(target=publish)
        publisher.start()
        assert partial.wait(1)
        task = asyncio.create_task(service._render_loop())
        await asyncio.wait_for(rendered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        publisher.join(1)

        expected = render_profile_at_elapsed(candidate.active, now, 0.0)
        assert worker.frames == [(expected.to_bytes(), 2)]

    asyncio.run(scenario())
