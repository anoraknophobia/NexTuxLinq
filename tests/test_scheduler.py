"""Hardware-independent Phase 4 update-policy scheduler tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from nextuxlinq.models_v2 import (
    CanvasConfig,
    ProfileConfigV2,
    RegionConfig,
)
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.rendering_v2.update_policy import UpdatePolicy
from nextuxlinq.scheduler import continuous_delay, update_policy_delay
from nextuxlinq.service import NexTuxLinqService
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import WidgetConfig, WidgetType

NOW = datetime(2026, 7, 29, 16, 26, 37, 250000, tzinfo=timezone.utc)


def profile(
    widget_type: WidgetType,
    **changes: object,
) -> ProfileConfigV2:
    definition = create_widget_registry().resolve(widget_type)
    values = definition.default_settings.to_dict()
    values.update(changes)
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(90, 10, "#000000"),
        regions=(
            RegionConfig(
                id="target",
                name="Target",
                x=0,
                y=0,
                width=90,
                height=10,
                enabled=True,
                background_color=None,
                widget=WidgetConfig(
                    widget_type,
                    definition.settings_model.from_dict(values),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (UpdatePolicy.HALF_SECOND, 0.5),
        (UpdatePolicy.SECOND_BOUNDARY, 0.75),
        (UpdatePolicy.MINUTE_BOUNDARY, 22.75),
        (UpdatePolicy.STATIC, None),
    ],
)
def test_exact_policy_boundary_delays(
    policy: UpdatePolicy,
    expected: float | None,
) -> None:
    assert update_policy_delay(
        policy,
        wall_clock=NOW,
        monotonic_now=10.0,
        continuous_epoch=0.0,
        frame_rate=5.0,
        profile=profile(WidgetType.CLOCK),
    ) == expected


def test_continuous_cadence_recalculates_after_scheduler_drift() -> None:
    assert continuous_delay(0.0, 0.0, 5.0) == pytest.approx(0.2)
    assert continuous_delay(0.81, 0.0, 5.0) == pytest.approx(0.19)
    assert continuous_delay(8.03, 3.0, 2.0) == pytest.approx(0.47)


def test_day_boundary_uses_configured_date_timezone_without_polling() -> None:
    date_profile = profile(WidgetType.DATE, time_zone="Europe/Berlin")
    delay = update_policy_delay(
        UpdatePolicy.DAY_BOUNDARY,
        wall_clock=datetime(
            2026,
            7,
            29,
            21,
            30,
            tzinfo=timezone.utc,
        ),
        monotonic_now=0.0,
        continuous_epoch=0.0,
        frame_rate=5.0,
        profile=date_profile,
    )

    assert delay == 1800.0


class FakeWorker:
    def __init__(self) -> None:
        self.frames: list[tuple[bytes, int]] = []

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append((bytes(frame), revision))

    def set_brightness(self, percentage: int, revision: int = 0) -> None:
        raise AssertionError("brightness is not expected")


class UnusedStore:
    def load(self):
        raise AssertionError("load is not expected")

    def save(self, configuration):
        raise AssertionError("save is not expected")


def ready_service(
    configured: ProfileConfigV2,
    *,
    sleep,
    metrics_provider=None,
    monotonic=None,
) -> tuple[NexTuxLinqService, FakeWorker]:
    from nextuxlinq.models_v2 import ConfigurationDocumentV2

    worker = FakeWorker()
    service = NexTuxLinqService(
        config_store=UnusedStore(),  # type: ignore[arg-type]
        device_worker=worker,  # type: ignore[arg-type]
        monotonic=(lambda: 0.0) if monotonic is None else monotonic,
        wall_clock=lambda: NOW,
        sleep=sleep,
        metrics_provider=metrics_provider,
    )
    service._configuration = ConfigurationDocumentV2(
        2,
        "default",
        {"default": configured},
    )
    service._render_wakeup = asyncio.Event()
    service.state.update(
        lifecycle_state="running",
        active_configuration_revision=1,
    )
    return service, worker


def test_static_scheduler_renders_immediately_then_waits_for_change() -> None:
    async def scenario() -> None:
        sleep_calls: list[float] = []

        async def sleep(delay: float) -> None:
            sleep_calls.append(delay)
            await asyncio.Future()

        service, worker = ready_service(
            profile(WidgetType.BLANK),
            sleep=sleep,
        )
        task = asyncio.create_task(service._render_loop())
        for _ in range(10):
            if worker.frames:
                break
            await asyncio.sleep(0)
        assert len(worker.frames) == 1
        assert sleep_calls == []
        assert service.snapshot().update_policy == "static"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_metric_scheduler_renders_immediately_then_refreshes_at_second_boundary() -> None:
    async def scenario() -> None:
        entered_second_wait = asyncio.Event()
        delays: list[float] = []

        class Metrics:
            def __init__(self) -> None:
                self.calls = 0

            def sample(self) -> SystemMetricsSnapshot:
                self.calls += 1
                available = self.calls > 1
                return SystemMetricsSnapshot(
                    float(self.calls), NOW,
                    {MetricId.CPU_USAGE: 42.0} if available else {},
                    {
                        metric: (
                            MetricAvailability.AVAILABLE
                            if metric is MetricId.CPU_USAGE and available
                            else MetricAvailability.AWAITING_INITIAL_CPU_DELTA
                            if metric is MetricId.CPU_USAGE
                            else MetricAvailability.UNAVAILABLE
                        )
                        for metric in MetricId
                    },
                    "partial",
                )

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) == 1:
                return
            entered_second_wait.set()
            await asyncio.Future()

        metrics = Metrics()
        # The two renders are a second apart, so the shared-snapshot
        # coordinator samples again instead of reusing the first snapshot.
        clock = {"now": 0.0}

        def monotonic() -> float:
            value = clock["now"]
            clock["now"] += 0.5
            return value

        service, worker = ready_service(
            profile(WidgetType.METRIC_BAR),
            sleep=sleep,
            metrics_provider=metrics,
            monotonic=monotonic,
        )
        task = asyncio.create_task(service._render_loop())
        await asyncio.wait_for(entered_second_wait.wait(), 1)
        assert len(worker.frames) == 2
        assert metrics.calls == 2
        assert service.snapshot().update_policy == "second_boundary"
        assert delays == [pytest.approx(0.75), pytest.approx(0.75)]
        assert service.get_metric_status()["cpu_usage"] == "available"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_continuous_scheduler_uses_one_wait_and_cancels_cleanly() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)
            entered.set()
            await asyncio.Future()

        service, worker = ready_service(
            profile(WidgetType.SCROLLING_TEXT),
            sleep=sleep,
        )
        task = asyncio.create_task(service._render_loop())
        await asyncio.wait_for(entered.wait(), 1)
        assert len(worker.frames) == 1
        assert delays == [pytest.approx(0.2)]
        assert service.snapshot().update_policy == "continuous"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("widget_type", "changes", "expected_policy", "expected_delay"),
    [
        (
            WidgetType.CLOCK,
            {"blink_separator": True},
            "half_second",
            0.5,
        ),
        (
            WidgetType.CLOCK,
            {"show_seconds": True},
            "second_boundary",
            0.75,
        ),
        (
            WidgetType.CLOCK,
            {},
            "minute_boundary",
            22.75,
        ),
        (
            WidgetType.DATE,
            {"time_zone": "Europe/Berlin"},
            "day_boundary",
            20002.75,
        ),
    ],
)
def test_runtime_scheduler_uses_policy_specific_boundary(
    widget_type: WidgetType,
    changes: dict[str, object],
    expected_policy: str,
    expected_delay: float,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)
            entered.set()
            await asyncio.Future()

        service, worker = ready_service(
            profile(widget_type, **changes),
            sleep=sleep,
        )
        task = asyncio.create_task(service._render_loop())
        await asyncio.wait_for(entered.wait(), 1)

        assert len(worker.frames) == 1
        assert service.snapshot().update_policy == expected_policy
        assert delays == [pytest.approx(expected_delay)]

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
