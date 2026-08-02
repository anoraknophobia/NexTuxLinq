"""Tests for bounded shared system-metric snapshots.

No test reads /proc, /sys, or any other real system source: every provider is
a controlled fake.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

import pytest

from nextuxlinq.models_v2 import CanvasConfig, ProfileConfigV2, RegionConfig
from nextuxlinq.service import NexTuxLinqService, profile_requires_metrics
from nextuxlinq.system_metrics import (
    MINIMUM_METRIC_SAMPLE_INTERVAL_SECONDS,
    LinuxMetricReader,
    LinuxSystemMetricsProvider,
    MetricAvailability,
    MetricId,
    MetricsSnapshotCoordinator,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import (
    BlankSettings,
    MetricDisplayMode,
    SystemMetricSettings,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def sample(self) -> SystemMetricsSnapshot:
        self.calls += 1
        return SystemMetricsSnapshot(
            float(self.calls),
            NOW,
            {MetricId.CPU_USAGE: float(self.calls)},
            {
                metric: (
                    MetricAvailability.AVAILABLE
                    if metric is MetricId.CPU_USAGE
                    else MetricAvailability.UNAVAILABLE
                )
                for metric in MetricId
            },
            "partial",
        )


def coordinator(
    provider,
    clock: Clock,
    *,
    minimum_interval: float = MINIMUM_METRIC_SAMPLE_INTERVAL_SECONDS,
) -> MetricsSnapshotCoordinator:
    return MetricsSnapshotCoordinator(
        provider,
        monotonic=clock,
        wall_clock=lambda: NOW,
        minimum_interval=minimum_interval,
    )


def metric_region(region_id: str, x: int) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=None,
        x=x,
        y=0,
        width=30,
        height=10,
        enabled=True,
        background_color=None,
        widget=WidgetConfig(
            type=WidgetType.SYSTEM_METRIC,
            settings=SystemMetricSettings(
                metric=MetricId.CPU_USAGE,
                display_mode=MetricDisplayMode.VALUE_ONLY,
            ),
        ),
    )


def blank_region(region_id: str) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=None,
        x=0,
        y=0,
        width=10,
        height=10,
        enabled=True,
        background_color=None,
        widget=WidgetConfig(type=WidgetType.BLANK, settings=BlankSettings()),
    )


def metric_profile(regions: tuple[RegionConfig, ...]) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=regions,
    )


def test_repeated_requests_inside_the_interval_reuse_the_snapshot() -> None:
    clock = Clock()
    provider = CountingProvider()
    subject = coordinator(provider, clock)

    first = subject.sample_if_due()
    for _ in range(20):
        assert subject.sample_if_due() is first
    clock.advance(MINIMUM_METRIC_SAMPLE_INTERVAL_SECONDS - 0.001)
    assert subject.sample_if_due() is first

    assert provider.calls == 1


def test_stale_snapshot_is_replaced_after_the_interval() -> None:
    clock = Clock()
    provider = CountingProvider()
    subject = coordinator(provider, clock)

    first = subject.sample_if_due()
    clock.advance(MINIMUM_METRIC_SAMPLE_INTERVAL_SECONDS)
    second = subject.sample_if_due()

    assert second is not first
    assert provider.calls == 2
    assert subject.current_snapshot() is second


def test_current_snapshot_never_advances_the_provider() -> None:
    clock = Clock()
    provider = CountingProvider()
    subject = coordinator(provider, clock)

    assert subject.current_snapshot() is None
    assert provider.calls == 0

    sampled = subject.sample_if_due()
    clock.advance(600.0)
    for _ in range(10):
        assert subject.current_snapshot() is sampled

    assert provider.calls == 1


def test_snapshot_is_immutable() -> None:
    clock = Clock()
    subject = coordinator(CountingProvider(), clock)

    snapshot = subject.sample_if_due()

    with pytest.raises(Exception):
        snapshot.values[MetricId.CPU_USAGE] = 1.0  # type: ignore[index]
    with pytest.raises(Exception):
        snapshot.availability[MetricId.CPU_USAGE] = (  # type: ignore[index]
            MetricAvailability.UNAVAILABLE
        )
    with pytest.raises(Exception):
        snapshot.source_status = "ok"  # type: ignore[misc]


def test_provider_exceptions_never_escape() -> None:
    clock = Clock()

    class Broken:
        def sample(self) -> SystemMetricsSnapshot:
            raise RuntimeError("PRIVATE_METRIC_PATH_9F21")

    subject = coordinator(Broken(), clock)
    snapshot = subject.sample_if_due()

    assert snapshot.source_status == "unavailable"
    assert all(
        snapshot.value(metric) is None for metric in MetricId
    )
    assert "PRIVATE_METRIC_PATH_9F21" not in repr(snapshot)


def test_non_snapshot_return_value_is_replaced_safely() -> None:
    clock = Clock()

    class Wrong:
        def sample(self) -> object:
            return {"cpu": 42}

    subject = coordinator(Wrong(), clock)  # type: ignore[arg-type]
    snapshot = subject.sample_if_due()

    assert type(snapshot) is SystemMetricsSnapshot
    assert snapshot.source_status == "unavailable"


def test_concurrent_requests_produce_one_coherent_update() -> None:
    clock = Clock()

    class SlowProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.concurrent = 0
            self.max_concurrent = 0

        def sample(self) -> SystemMetricsSnapshot:
            self.calls += 1
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            # Give other threads a real chance to interleave.
            for _ in range(200):
                pass
            self.concurrent -= 1
            return SystemMetricsSnapshot(
                float(self.calls),
                NOW,
                {},
                {metric: MetricAvailability.UNAVAILABLE for metric in MetricId},
                "unavailable",
            )

    provider = SlowProvider()
    subject = coordinator(provider, clock)
    start = threading.Barrier(8)
    results: list[SystemMetricsSnapshot] = []
    results_lock = threading.Lock()

    def worker() -> None:
        start.wait(5.0)
        snapshot = subject.sample_if_due()
        with results_lock:
            results.append(snapshot)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)

    assert provider.calls == 1
    assert provider.max_concurrent == 1
    assert len(results) == 8
    assert all(item is results[0] for item in results)


def test_replacing_the_provider_drops_the_foreign_snapshot() -> None:
    clock = Clock()
    first_provider = CountingProvider()
    subject = coordinator(first_provider, clock)
    first = subject.sample_if_due()

    second_provider = CountingProvider()
    subject.replace_provider(second_provider)

    assert subject.current_snapshot() is None
    replacement = subject.sample_if_due()
    assert replacement is not first
    assert second_provider.calls == 1


def test_invalidate_forces_the_next_request_to_sample() -> None:
    clock = Clock()
    provider = CountingProvider()
    subject = coordinator(provider, clock)

    subject.sample_if_due()
    subject.invalidate()
    subject.sample_if_due()

    assert provider.calls == 2


def test_invalid_minimum_interval_is_rejected() -> None:
    clock = Clock()
    for interval in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            coordinator(CountingProvider(), clock, minimum_interval=interval)


def test_first_sample_remains_unavailable_for_delta_metrics(tmp_path) -> None:
    clock = Clock()
    # An empty sandbox directory stands in for every system source, so the
    # test never reads the real /proc, /sys, or root filesystem.
    empty = tmp_path / "sources"
    empty.mkdir()
    reader = LinuxMetricReader(
        proc_root=empty,
        thermal_root=empty,
        hwmon_root=empty,
        drm_root=empty,
        filesystem_stat=lambda: None,
    )
    provider = LinuxSystemMetricsProvider(
        reader=reader, monotonic=clock, wall_clock=lambda: NOW
    )
    subject = coordinator(provider, clock)

    snapshot = subject.sample_if_due()

    assert snapshot.value(MetricId.CPU_USAGE) is None
    assert snapshot.value(MetricId.NETWORK_RECEIVE_RATE) is None
    assert snapshot.source_status == "unavailable"


def test_first_network_sample_awaits_a_delta_then_uses_the_interval(
    tmp_path,
) -> None:
    clock = Clock()
    interfaces = tmp_path / "proc"
    (interfaces / "net").mkdir(parents=True)

    def write_counters(received: int) -> None:
        (interfaces / "net" / "dev").write_text(
            "Inter-|   Receive                    |  Transmit\n"
            " face |bytes    packets errs drop fifo frame compressed multicast"
            "|bytes    packets errs drop fifo colls carrier compressed\n"
            f"  eth0: {received} 0 0 0 0 0 0 0 4096 0 0 0 0 0 0 0\n",
            encoding="ascii",
        )

    write_counters(1024)
    reader = LinuxMetricReader(
        proc_root=interfaces,
        thermal_root=tmp_path / "missing",
        hwmon_root=tmp_path / "missing",
        drm_root=tmp_path / "missing",
        filesystem_stat=lambda: None,
    )
    provider = LinuxSystemMetricsProvider(
        reader=reader, monotonic=clock, wall_clock=lambda: NOW
    )
    subject = coordinator(provider, clock)

    first = subject.sample_if_due()
    assert first.availability[MetricId.NETWORK_RECEIVE_RATE] is (
        MetricAvailability.AWAITING_INITIAL_DELTA
    )

    write_counters(1024 + 2048)
    clock.advance(2.0)
    second = subject.sample_if_due()

    # 2048 bytes over the 2.0 s that actually elapsed between the two samples.
    assert second.value(MetricId.NETWORK_RECEIVE_RATE) == pytest.approx(1024.0)


def test_profile_requires_metrics_detects_metric_widgets() -> None:
    assert profile_requires_metrics(
        metric_profile((metric_region("metric", 0),))
    )
    assert not profile_requires_metrics(
        metric_profile((blank_region("blank"),))
    )


class UnusedStore:
    def load(self):  # pragma: no cover - never used by these tests
        raise AssertionError("configuration must not be loaded here.")


class FakeWorker:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def set_status_callback(self, callback) -> None:
        return None

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append(bytes(frame))

    def set_brightness(self, percentage: int, revision: int = 0) -> None:
        return None

    def snapshot(self):  # pragma: no cover - never used by these tests
        raise AssertionError("worker snapshot must not be read here.")


def build_service(provider, clock: Clock) -> NexTuxLinqService:
    from nextuxlinq.models_v2 import ConfigurationDocumentV2

    service = NexTuxLinqService(
        config_store=UnusedStore(),  # type: ignore[arg-type]
        device_worker=FakeWorker(),  # type: ignore[arg-type]
        monotonic=clock,
        wall_clock=lambda: NOW,
        metrics_provider=provider,
    )
    profiles = {
        f"profile{index}": metric_profile((metric_region("metric", 0),))
        for index in range(8)
    }
    service._configuration = ConfigurationDocumentV2(2, "profile0", profiles)
    service.state.update(
        lifecycle_state="running", active_configuration_revision=1
    )
    return service


def test_eight_profile_validation_performs_at_most_one_provider_sample() -> None:
    clock = Clock()
    provider = CountingProvider()
    service = build_service(provider, clock)

    service.validate_configuration(service.configuration)

    assert len(service.configuration.profiles) == 8
    assert provider.calls == 1


def test_validation_still_renders_every_inactive_profile() -> None:
    clock = Clock()
    provider = CountingProvider()
    service = build_service(provider, clock)
    rendered: list[str] = []
    original = service._render_profile

    def recording(profile, **kwargs):
        rendered.append(profile.regions[0].id)
        return original(profile, **kwargs)

    service._render_profile = recording  # type: ignore[method-assign]
    service.validate_configuration(service.configuration)

    assert len(rendered) == 8


def test_repeated_previews_inside_the_interval_reuse_the_snapshot() -> None:
    clock = Clock()
    provider = CountingProvider()
    service = build_service(provider, clock)

    for _ in range(10):
        service.get_preview()

    assert provider.calls == 1

    clock.advance(MINIMUM_METRIC_SAMPLE_INTERVAL_SECONDS)
    service.get_preview()

    assert provider.calls == 2


def test_preview_and_render_share_one_snapshot_without_extra_samples() -> None:
    clock = Clock()
    provider = CountingProvider()
    service = build_service(provider, clock)

    async def scenario() -> None:
        service._render_wakeup = asyncio.Event()
        profile, _ = service._capture_active_profile()
        service._render_profile(profile)
        for _ in range(5):
            service.get_preview()
        service._render_profile(profile)

    asyncio.run(scenario())

    assert provider.calls == 1


def test_network_rates_use_the_intended_elapsed_interval() -> None:
    clock = Clock()
    samples: list[float] = []

    class RecordingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def sample(self) -> SystemMetricsSnapshot:
            self.calls += 1
            samples.append(clock.now)
            return SystemMetricsSnapshot(
                clock.now,
                NOW,
                {},
                {
                    metric: MetricAvailability.UNAVAILABLE
                    for metric in MetricId
                },
                "unavailable",
            )

    provider = RecordingProvider()
    service = build_service(provider, clock)

    # A burst of previews must not compress the interval between two samples.
    for _ in range(20):
        service.get_preview()
    clock.advance(1.0)
    service.get_preview()

    assert len(samples) == 2
    assert samples[1] - samples[0] >= MINIMUM_METRIC_SAMPLE_INTERVAL_SECONDS


def test_provider_failure_does_not_break_preview_or_rendering() -> None:
    clock = Clock()

    class Broken:
        def sample(self) -> SystemMetricsSnapshot:
            raise RuntimeError("PRIVATE_PROC_DETAIL_4C77")

    service = build_service(Broken(), clock)

    snapshot = service.get_preview()
    assert len(snapshot.device_framebuffer) == 90 * 10 * 3

    diagnostics = service.get_metric_diagnostics()
    serialized = repr(diagnostics)
    assert "PRIVATE_PROC_DETAIL_4C77" not in serialized
    assert "/proc" not in serialized
    assert "/sys" not in serialized


def test_no_metric_snapshot_is_written_into_saved_profiles() -> None:
    clock = Clock()
    service = build_service(CountingProvider(), clock)

    service.get_preview()
    serialized = repr(service.configuration.to_dict())

    for forbidden in (
        "sample_monotonic",
        "sample_time",
        "availability",
        "source_status",
    ):
        assert forbidden not in serialized
