"""Runtime rendering resilience for an already accepted free-layout profile.

Every test drives the render loop deterministically with an injected clock,
an injected sleep, and a scripted metrics provider. No test sleeps, samples
the host, opens a hidraw device, or touches real hardware.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from nextuxlinq.config_store import ConfigLoadResult
from nextuxlinq.creator import FRAME_SIZE
from nextuxlinq.device_worker import DeviceWorkerSnapshot
from nextuxlinq.models_v2 import ConfigurationDocumentV2
from nextuxlinq.service import CandidateValidationError, NexTuxLinqService
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import SystemMetricSettings

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

# Percentages are capped at 99 for display and can never be too wide, so these
# tests drive the retention path with a Celsius temperature instead: its stable
# two-digit "99°C" envelope in the 3x5 font measures thirteen pixels, and an
# actual 100 °C sample is genuinely too wide for it.
ENVELOPE_WIDTH = 13


def percentage_configuration(width: int = ENVELOPE_WIDTH) -> ConfigurationDocumentV2:
    """Return one accepted profile whose region fits the stable 99 °C envelope."""
    return ConfigurationDocumentV2.from_dict({
        "schema_version": 2,
        "active_profile": "default",
        "profiles": {
            "default": {
                "brightness": None,
                "canvas": {
                    "width": 90, "height": 10, "background_color": "#000000",
                },
                "regions": [{
                    "id": "metric", "name": "Metric",
                    "x": 0, "y": 0, "width": width, "height": 5,
                    "enabled": True, "background_color": None,
                    "widget": {
                        "type": "system_metric",
                        "settings": SystemMetricSettings(
                            metric="cpu_temperature",  # type: ignore[arg-type]
                            font="3x5",  # type: ignore[arg-type]
                            display_mode="value_only",  # type: ignore[arg-type]
                            overflow="error",  # type: ignore[arg-type]
                            precision=0,
                        ).to_dict(),
                    },
                }],
            },
        },
    })


class FakeStore:
    def __init__(self, configuration: ConfigurationDocumentV2) -> None:
        self.configuration = configuration

    def load(self) -> ConfigLoadResult:
        return ConfigLoadResult(self.configuration)


class RecordingWorker:
    """Record every submitted frame without owning a device."""

    def __init__(self) -> None:
        self.callback = None
        self.started = False
        self.stopped = False
        self.frames: list[tuple[bytes, int]] = []

    def set_status_callback(self, callback) -> None:
        self.callback = callback

    def start(self) -> None:
        self.started = True

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append((frame, revision))

    def set_brightness(self, percentage: int, revision: int = 0) -> None:
        pass

    def stop(self, timeout: float | None = 5.0) -> None:
        self.stopped = True

    def snapshot(self) -> DeviceWorkerSnapshot:
        return DeviceWorkerSnapshot(running=self.started and not self.stopped)


class StepMonotonic:
    """Deterministic monotonic clock advancing one second per reading."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        current = self.value
        self.value += 1.0
        return current


class ScriptedMetrics:
    """Return one scripted CPU temperature per sample; the last value repeats."""

    def __init__(self, values) -> None:
        self.values = list(values)
        self.samples = 0

    def sample(self) -> SystemMetricsSnapshot:
        value = self.values[min(self.samples, len(self.values) - 1)]
        self.samples += 1
        return SystemMetricsSnapshot(
            float(self.samples),
            NOW,
            {MetricId.CPU_TEMPERATURE: float(value)},
            {
                metric: (
                    MetricAvailability.AVAILABLE
                    if metric is MetricId.CPU_TEMPERATURE
                    else MetricAvailability.UNAVAILABLE
                )
                for metric in MetricId
            },
            "partial",
        )


class StepSleep:
    """Release a fixed number of render iterations, then park the loop.

    The first call parks until the test releases it, so startup is complete
    and observable before any later value is rendered. ``states`` therefore
    describes the renders after the initial one, newest last.
    """

    def __init__(self, iterations: int, service_ref: list) -> None:
        self.iterations = iterations
        self.calls = 0
        self.states: list[tuple[str, str | None]] = []
        self.first_render = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self._service_ref = service_ref

    async def __call__(self, delay: float) -> None:
        self.calls += 1
        if self.calls == 1:
            self.first_render.set()
            await self.release.wait()
        else:
            snapshot = self._service_ref[0].snapshot()
            self.states.append(
                (snapshot.rendering_state, snapshot.last_display_error)
            )
        if self.calls >= self.iterations:
            self.finished.set()
            await asyncio.Future()
        return None


async def _run_loop(
    values,
    *,
    iterations: int,
    width: int = ENVELOPE_WIDTH,
):
    """Render ``iterations`` frames over the scripted values and stop cleanly."""
    reference: list = []
    worker = RecordingWorker()
    sleep = StepSleep(iterations, reference)
    service = NexTuxLinqService(
        config_store=FakeStore(percentage_configuration(width)),  # type: ignore[arg-type]
        device_worker=worker,  # type: ignore[arg-type]
        monotonic=StepMonotonic(),
        wall_clock=lambda: NOW,
        sleep=sleep,
        metrics_provider=ScriptedMetrics(values),
    )
    reference.append(service)
    await service.start()
    await asyncio.wait_for(sleep.first_render.wait(), 5)
    initial_state = service.snapshot()
    initial_frames = len(worker.frames)
    sleep.release.set()
    task = service.render_task
    assert task is not None
    await _finish_or_propagate(sleep, task)
    assert not task.done()
    await service.stop()
    return service, worker, sleep, initial_state, initial_frames


async def _finish_or_propagate(sleep: StepSleep, task: asyncio.Task) -> None:
    """Wait for the planned iterations, re-raising a renderer failure instead.

    A renderer that stops mid-run reports its own exception here, so a
    regression shows the failing category rather than a timeout.
    """
    finished = asyncio.create_task(sleep.finished.wait())
    try:
        done, _ = await asyncio.wait(
            (finished, task),
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            await task
            raise AssertionError("The render loop stopped unexpectedly.")
        if finished not in done:
            raise AssertionError("The render loop did not reach its iterations.")
    finally:
        finished.cancel()
        await asyncio.gather(finished, return_exceptions=True)


def test_initial_render_succeeds_and_submits_one_frame() -> None:
    """A value inside the stable envelope renders and reaches the worker."""

    async def scenario() -> None:
        _, worker, _, initial_state, initial_frames = await _run_loop(
            [40.0], iterations=1
        )
        assert initial_frames == 1
        assert initial_state.rendering_state == "rendering"
        assert initial_state.last_display_error is None
        assert len(worker.frames[0][0]) == FRAME_SIZE

    asyncio.run(scenario())


def test_three_digit_value_keeps_the_last_frame_instead_of_stopping() -> None:
    """A three-digit sample must not stop the renderer of an accepted profile.

    Before the fix this raised CandidateValidationError out of the render
    loop, which stopped the daemon and produced the systemd restart loop.
    """

    async def scenario() -> None:
        _, worker, sleep, initial_state, initial_frames = await _run_loop(
            [40.0, 100.0, 40.0], iterations=3
        )
        assert initial_state.rendering_state == "rendering"
        assert initial_frames == 1
        # The full-scale sample submits nothing, so the device keeps the last
        # transmitted frame and the worker's duplicate baseline is untouched.
        assert len(worker.frames) == 2
        assert all(len(frame) == FRAME_SIZE for frame, _ in worker.frames)
        assert sleep.states[0] == (
            "degraded",
            "Matrix900 kept the last frame: content_too_wide.",
        )
        assert sleep.states[1] == ("rendering", None)

    asyncio.run(scenario())


def test_repeated_full_scale_samples_stay_stable() -> None:
    """Thousands of alternating in-range and out-of-range values never stop."""

    async def scenario() -> None:
        iterations = 2000
        values = [40.0 if index % 2 == 0 else 100.0 for index in range(iterations)]
        service, worker, sleep, _, _ = await _run_loop(
            values, iterations=iterations
        )
        assert sleep.calls == iterations
        # Exactly the in-range samples are transmitted.
        assert len(worker.frames) == sum(
            1 for value in values[:iterations] if value != 100.0
        )
        assert service.snapshot().rendering_state == "idle"

    asyncio.run(scenario())


def test_permanently_unrenderable_profile_still_fails_at_startup() -> None:
    """A region too small for any value is still rejected before serving."""

    async def scenario() -> None:
        reference: list = []
        worker = RecordingWorker()
        sleep = StepSleep(1, reference)
        service = NexTuxLinqService(
            config_store=FakeStore(percentage_configuration(2)),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=StepMonotonic(),
            wall_clock=lambda: NOW,
            sleep=sleep,
            metrics_provider=ScriptedMetrics([40.0]),
        )
        reference.append(service)
        with pytest.raises(CandidateValidationError):
            await service.start()
        snapshot = service.snapshot()
        assert snapshot.lifecycle_state == "failed"
        assert snapshot.rendering_state == "failed"
        assert worker.frames == []
        assert worker.stopped

    asyncio.run(scenario())


def test_internal_renderer_defect_still_stops_the_renderer() -> None:
    """A category that means the renderer is broken is never retained."""

    async def scenario() -> None:
        reference: list = []
        worker = RecordingWorker()
        sleep = StepSleep(4, reference)
        service = NexTuxLinqService(
            config_store=FakeStore(percentage_configuration()),  # type: ignore[arg-type]
            device_worker=worker,  # type: ignore[arg-type]
            monotonic=StepMonotonic(),
            wall_clock=lambda: NOW,
            sleep=sleep,
            metrics_provider=ScriptedMetrics([40.0]),
        )
        reference.append(service)
        await service.start()
        await asyncio.wait_for(sleep.first_render.wait(), 5)
        genuine = service._render_profile

        def broken(profile, **keywords):
            raise CandidateValidationError(
                "unexpected_renderer_failure",
                region_id="metric",
                field="renderer",
            )

        service._render_profile = broken  # type: ignore[method-assign]
        task = service.render_task
        assert task is not None
        sleep.release.set()
        with pytest.raises(CandidateValidationError):
            await asyncio.wait_for(task, 5)
        assert service.snapshot().rendering_state == "failed"
        assert len(worker.frames) == 1
        service._render_profile = genuine  # type: ignore[method-assign]
        await service.stop()

    asyncio.run(scenario())


def test_repeated_retention_is_reported_once_per_run(caplog) -> None:
    """Oscillating values must not flood the journal with repeated warnings."""

    async def scenario() -> None:
        values = [40.0 if index % 2 == 0 else 100.0 for index in range(20)]
        await _run_loop(values, iterations=20)

    with caplog.at_level("WARNING", logger="nextuxlinq.service"):
        asyncio.run(scenario())

    retained = [
        record for record in caplog.records
        if "kept the last frame" in record.getMessage()
    ]
    assert len(retained) == 1
    assert "content_too_wide" in retained[0].getMessage()


def test_sanitized_category_reaches_the_exception_message() -> None:
    """The generic message now carries the sanitized structured category."""
    error = CandidateValidationError(
        "content_too_wide",
        region_id="metric",
        field="content",
    )
    message = str(error)
    assert "content_too_wide" in message
    assert "metric" in message
    assert error.category == "content_too_wide"
