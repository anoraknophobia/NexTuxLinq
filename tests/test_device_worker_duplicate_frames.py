"""Tests for duplicate Matrix900 framebuffer suppression.

Every test drives the worker with a fake transport. No test opens a hidraw
device, touches real hardware, or restarts a service.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nextuxlinq.creator import FRAME_SIZE, build_creator_upload_reports
from nextuxlinq.device import Matrix900Device
from nextuxlinq.device_worker import DeviceWorker
from nextuxlinq.models_v2 import (
    CanvasConfig,
    ForegroundRainbowEffect,
    ProfileConfigV2,
    RainbowDirection,
    RegionConfig,
)
from nextuxlinq.rendering_v2.compositor import Compositor
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import (
    ClockSettings,
    MetricDisplayMode,
    StaticTextSettings,
    SystemMetricSettings,
    WidgetConfig,
    WidgetType,
)
from nextuxlinq.writer_lock import WriterLock

CREATOR_REPORT_COUNT = len(build_creator_upload_reports(bytes(FRAME_SIZE)))
ACTIVATION_COMMAND = 0x03
BRIGHTNESS_COMMAND = 0x04


class FakeConnection:
    """Records every report and echoes it unless configured to fail."""

    def __init__(
        self,
        *,
        name: str = "connection",
        fail_at_index: int | None = None,
        events: dict[str, threading.Event] | None = None,
    ) -> None:
        self.name = name
        self.reports: list[bytes] = []
        self.closed = False
        self.close_calls = 0
        self.fail_at_index = fail_at_index
        self.events = events or {}
        self.activations = 0
        self.brightness_reports = 0

    def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
        if (
            self.fail_at_index is not None
            and len(self.reports) == self.fail_at_index
        ):
            self.fail_at_index = None
            raise OSError("private transport detail")
        self.reports.append(report)
        if report[1] == ACTIVATION_COMMAND:
            self.activations += 1
            event = self.events.get(f"frame{self.activations}")
            if event is not None:
                event.set()
            frames = self.events.get("frames")
            if frames is not None:
                frames.set()
        if report[1] == BRIGHTNESS_COMMAND:
            self.brightness_reports += 1
            event = self.events.get("brightness")
            if event is not None:
                event.set()
        return report

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    def frame_report_count(self) -> int:
        return len(self.reports) - self.brightness_reports


def device(path: str) -> Matrix900Device:
    return Matrix900Device(Path(path), Path(f"/sys/{Path(path).name}"))


def build_worker(tmp_path: Path, **overrides) -> DeviceWorker:
    arguments = {
        "writer_lock": WriterLock(tmp_path / "matrix900.lock"),
        "discover": lambda: (device("/dev/hidraw0"),),
        "reconnect_delays": (0.0,),
    }
    arguments.update(overrides)
    return DeviceWorker(**arguments)


def frame(fill: int) -> bytes:
    return bytes([fill]) * FRAME_SIZE


def wait(event: threading.Event) -> None:
    assert event.wait(5.0)


# --------------------------------------------------------------------------
# Baseline behavior
# --------------------------------------------------------------------------


def test_first_frame_is_always_transmitted(tmp_path: Path) -> None:
    first = threading.Event()
    connection = FakeConnection(events={"frame1": first})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.submit_frame(frame(1))
        wait(first)
    finally:
        worker.stop()

    assert connection.frame_report_count() == CREATOR_REPORT_COUNT
    snapshot = worker.snapshot()
    assert snapshot.successful_uploads == 1
    assert snapshot.duplicate_frames_skipped == 0


def test_identical_second_frame_produces_zero_hid_reports(
    tmp_path: Path,
) -> None:
    first = threading.Event()
    connection = FakeConnection(events={"frame1": first})
    skipped = threading.Event()

    def callback(snapshot) -> None:
        if snapshot.duplicate_frames_skipped >= 1:
            skipped.set()

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
    )

    worker.start()
    try:
        worker.submit_frame(frame(1))
        wait(first)
        reports_after_first = len(connection.reports)
        worker.submit_frame(frame(1), revision=2)
        wait(skipped)
    finally:
        worker.stop()

    assert len(connection.reports) == reports_after_first
    snapshot = worker.snapshot()
    assert snapshot.successful_uploads == 1
    assert snapshot.duplicate_frames_skipped == 1
    assert snapshot.frame_submissions_received == 2


def test_changed_second_frame_is_transmitted(tmp_path: Path) -> None:
    first = threading.Event()
    second = threading.Event()
    connection = FakeConnection(events={"frame1": first, "frame2": second})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.submit_frame(frame(1))
        wait(first)
        worker.submit_frame(frame(2))
        wait(second)
    finally:
        worker.stop()

    assert connection.frame_report_count() == 2 * CREATOR_REPORT_COUNT
    snapshot = worker.snapshot()
    assert snapshot.successful_uploads == 2
    assert snapshot.duplicate_frames_skipped == 0


def test_sent_frame_still_produces_the_complete_creator_report_count(
    tmp_path: Path,
) -> None:
    first = threading.Event()
    second = threading.Event()
    connection = FakeConnection(events={"frame1": first, "frame2": second})
    skipped = threading.Event()

    def callback(snapshot) -> None:
        if snapshot.duplicate_frames_skipped >= 1:
            skipped.set()

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
    )

    worker.start()
    try:
        worker.submit_frame(frame(1))
        wait(first)
        worker.submit_frame(frame(1), revision=9)
        wait(skipped)
        worker.submit_frame(frame(2))
        wait(second)
    finally:
        worker.stop()

    commands = [report[1] for report in connection.reports]
    assert commands.count(0x05) == 2 * (CREATOR_REPORT_COUNT - 1)
    assert commands.count(ACTIVATION_COMMAND) == 2
    # Exactly two complete transactions, no partial upload anywhere.
    assert len(connection.reports) == 2 * CREATOR_REPORT_COUNT


# --------------------------------------------------------------------------
# Rendered-content equivalence
# --------------------------------------------------------------------------


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def metric_snapshot(value: float) -> SystemMetricsSnapshot:
    return SystemMetricsSnapshot(
        10.0,
        NOW,
        {MetricId.CPU_USAGE: value},
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


def region(region_id: str, widget: WidgetConfig, *, width: int = 40) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=None,
        x=0,
        y=0,
        width=width,
        height=10,
        enabled=True,
        background_color=None,
        widget=widget,
    )


def profile_with(
    widget: WidgetConfig,
    *,
    effect=None,
    width: int = 40,
) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(region("region", widget, width=width),),
        **({} if effect is None else {"effect": effect}),
    )


def render(profile: ProfileConfigV2, *, snapshot=None, animation_time=0.0, now=NOW):
    compositor = Compositor(create_widget_registry())
    return compositor.render(
        profile,
        generation_time=now,
        animation_time=animation_time,
        metrics_snapshot=snapshot,
    ).device_framebuffer


def static_text(content: str) -> StaticTextSettings:
    return StaticTextSettings(
        content=content,
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
        uppercase=True,
        overflow="clip",  # type: ignore[arg-type]
    )


def clock_settings() -> ClockSettings:
    return ClockSettings(
        format="24h",  # type: ignore[arg-type]
        show_seconds=False,
        blink_separator=False,
        time_zone="local",
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
    )


def metric_widget() -> WidgetConfig:
    return WidgetConfig(
        type=WidgetType.SYSTEM_METRIC,
        settings=SystemMetricSettings(
            metric=MetricId.CPU_USAGE,
            display_mode=MetricDisplayMode.VALUE_ONLY,
            precision=0,
        ),
    )


def test_metric_value_change_rendering_identical_pixels_is_skipped(
    tmp_path: Path,
) -> None:
    profile = profile_with(metric_widget())
    first = render(profile, snapshot=metric_snapshot(14.1))
    second = render(profile, snapshot=metric_snapshot(14.4))

    # Both render as "14%", so the device bytes are identical.
    assert first == second

    frame1 = threading.Event()
    connection = FakeConnection(events={"frame1": frame1})
    skipped = threading.Event()

    def callback(snapshot) -> None:
        if snapshot.duplicate_frames_skipped >= 1:
            skipped.set()

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
    )

    worker.start()
    try:
        worker.submit_frame(first)
        wait(frame1)
        count = len(connection.reports)
        worker.submit_frame(second, revision=2)
        wait(skipped)
    finally:
        worker.stop()

    assert len(connection.reports) == count
    assert worker.snapshot().duplicate_frames_skipped == 1


def test_changed_configuration_rendering_identical_bytes_is_skipped(
    tmp_path: Path,
) -> None:
    # Two different region names produce identical visible pixels.
    widget = WidgetConfig(
        type=WidgetType.STATIC_TEXT,
        settings=static_text("NEX"),
    )
    named = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            RegionConfig(
                id="region",
                name="First name",
                x=0,
                y=0,
                width=40,
                height=10,
                enabled=True,
                background_color=None,
                widget=widget,
            ),
        ),
    )
    renamed = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            RegionConfig(
                id="region",
                name="Second name",
                x=0,
                y=0,
                width=40,
                height=10,
                enabled=True,
                background_color=None,
                widget=widget,
            ),
        ),
    )

    first = render(named)
    second = render(renamed)
    assert first == second

    frame1 = threading.Event()
    connection = FakeConnection(events={"frame1": frame1})
    skipped = threading.Event()

    def callback(snapshot) -> None:
        if snapshot.duplicate_frames_skipped >= 1:
            skipped.set()

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
    )

    worker.start()
    try:
        worker.submit_frame(first)
        wait(frame1)
        count = len(connection.reports)
        worker.submit_frame(second, revision=7)
        wait(skipped)
    finally:
        worker.stop()

    assert len(connection.reports) == count


def test_changed_clock_minute_is_transmitted(tmp_path: Path) -> None:
    profile = profile_with(
        WidgetConfig(type=WidgetType.CLOCK, settings=clock_settings()),
        width=40,
    )
    first = render(profile, now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc))
    second = render(profile, now=datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc))

    assert first != second

    frame2 = threading.Event()
    connection = FakeConnection(events={"frame2": frame2})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    frame1 = threading.Event()
    connection.events["frame1"] = frame1

    worker.start()
    try:
        worker.submit_frame(first)
        wait(frame1)
        worker.submit_frame(second)
        wait(frame2)
    finally:
        worker.stop()

    assert worker.snapshot().successful_uploads == 2
    assert worker.snapshot().duplicate_frames_skipped == 0


def test_changed_rainbow_pixels_are_transmitted(tmp_path: Path) -> None:
    profile = profile_with(
        WidgetConfig(
            type=WidgetType.STATIC_TEXT,
            settings=static_text("NEX"),
        ),
        effect=ForegroundRainbowEffect(
            direction=RainbowDirection.REAR_TO_FRONT, speed=1.0
        ),
    )
    first = render(profile, animation_time=0.0)
    second = render(profile, animation_time=0.2)

    assert first != second

    frame2 = threading.Event()
    connection = FakeConnection(events={"frame2": frame2})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    frame1 = threading.Event()
    connection.events["frame1"] = frame1

    worker.start()
    try:
        worker.submit_frame(first)
        wait(frame1)
        worker.submit_frame(second)
        wait(frame2)
    finally:
        worker.stop()

    assert worker.snapshot().successful_uploads == 2


def test_orientation_conversion_is_part_of_the_compared_bytes() -> None:
    widget = WidgetConfig(
        type=WidgetType.STATIC_TEXT,
        settings=static_text("NEX"),
    )
    left = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            RegionConfig(
                id="region", name=None, x=0, y=0, width=40, height=10,
                enabled=True, background_color=None, widget=widget,
            ),
        ),
    )
    right = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            RegionConfig(
                id="region", name=None, x=50, y=0, width=40, height=10,
                enabled=True, background_color=None, widget=widget,
            ),
        ),
    )

    # Mirrored placement changes the device bytes, so it is never suppressed.
    assert render(left) != render(right)
    assert len(render(left)) == FRAME_SIZE


# --------------------------------------------------------------------------
# Baseline invalidation
# --------------------------------------------------------------------------


def test_failed_transaction_never_becomes_the_baseline(tmp_path: Path) -> None:
    connections: list[FakeConnection] = []
    first_done = threading.Event()
    retried = threading.Event()

    def factory(path: Path) -> FakeConnection:
        events = (
            {"frames": first_done}
            if not connections
            else {"frames": retried}
        )
        connection = FakeConnection(
            name=f"connection{len(connections)}",
            fail_at_index=5 if not connections else None,
            events=events,
        )
        connections.append(connection)
        return connection

    worker = build_worker(tmp_path, connection_factory=factory)

    worker.start()
    try:
        worker.submit_frame(frame(3))
        wait(retried)
    finally:
        worker.stop()

    # The first attempt aborted at report five and never reached activation.
    assert connections[0].activations == 0
    assert ACTIVATION_COMMAND not in [r[1] for r in connections[0].reports]
    # The reconnect replayed the same frame in full instead of suppressing it.
    assert connections[1].frame_report_count() == CREATOR_REPORT_COUNT
    snapshot = worker.snapshot()
    assert snapshot.frame_transactions_failed == 1
    assert snapshot.successful_uploads == 1
    assert snapshot.duplicate_frames_skipped == 0


def test_failed_transaction_invalidates_an_earlier_baseline(
    tmp_path: Path,
) -> None:
    connections: list[FakeConnection] = []
    first_frame = threading.Event()
    replayed = threading.Event()

    def factory(path: Path) -> FakeConnection:
        if not connections:
            connection = FakeConnection(
                name="first", events={"frame1": first_frame}
            )
        else:
            connection = FakeConnection(name="second", events={"frames": replayed})
        connections.append(connection)
        return connection

    worker = build_worker(tmp_path, connection_factory=factory)

    worker.start()
    try:
        worker.submit_frame(frame(4))
        wait(first_frame)
        # Force a failure on the established connection.
        connections[0].fail_at_index = len(connections[0].reports) + 2
        worker.submit_frame(frame(5))
        wait(replayed)
    finally:
        worker.stop()

    # After the failure the worker reconnected and re-sent the latest frame
    # completely, even though its bytes were never successfully transmitted.
    assert connections[1].frame_report_count() == CREATOR_REPORT_COUNT
    assert worker.snapshot().duplicate_frames_skipped == 0


def test_reconnect_forces_an_equal_frame_to_be_resent(tmp_path: Path) -> None:
    """After a reconnect the latest frame is re-sent even if its bytes match.

    The disconnect is triggered by a failing brightness report so that the
    frame path itself is untouched: the worker still holds frame(6) as its
    latest frame and had already transmitted exactly those bytes. A surviving
    baseline would suppress the replay; an invalidated one must not.
    """
    connections: list[FakeConnection] = []
    first_frame = threading.Event()
    replayed = threading.Event()

    def factory(path: Path) -> FakeConnection:
        if not connections:
            connection = FakeConnection(events={"frame1": first_frame})
        else:
            connection = FakeConnection(events={"frames": replayed})
        connections.append(connection)
        return connection

    worker = build_worker(tmp_path, connection_factory=factory)

    worker.start()
    try:
        worker.submit_frame(frame(6))
        wait(first_frame)
        assert connections[0].activations == 1

        # Drop the connection without touching the frame pipeline.
        connections[0].fail_at_index = len(connections[0].reports)
        worker.set_brightness(80)
        wait(replayed)
    finally:
        worker.stop()

    assert len(connections) >= 2
    # The reconnect replayed the identical frame as a complete transaction.
    assert connections[1].activations >= 1
    assert connections[1].frame_report_count() >= CREATOR_REPORT_COUNT
    assert worker.snapshot().duplicate_frames_skipped == 0


def test_device_path_change_forces_an_equal_frame_to_be_resent(
    tmp_path: Path,
) -> None:
    """A different selected Matrix900 path never inherits the old baseline."""
    connections: list[FakeConnection] = []
    paths = ["/dev/hidraw0", "/dev/hidraw3"]
    first_frame = threading.Event()
    second_session = threading.Event()

    def discover():
        return (device(paths[min(len(connections), 1)]),)

    def factory(path: Path) -> FakeConnection:
        if not connections:
            connection = FakeConnection(events={"frame1": first_frame})
        else:
            connection = FakeConnection(events={"frames": second_session})
        connections.append(connection)
        return connection

    worker = build_worker(
        tmp_path, discover=discover, connection_factory=factory
    )

    worker.start()
    try:
        worker.submit_frame(frame(8))
        wait(first_frame)
        assert worker.snapshot().device_path == "/dev/hidraw0"

        connections[0].fail_at_index = len(connections[0].reports)
        worker.set_brightness(90)
        wait(second_session)
    finally:
        worker.stop()

    assert worker.snapshot().device_path in (None, "/dev/hidraw3")
    assert connections[1].frame_report_count() >= CREATOR_REPORT_COUNT
    assert worker.snapshot().duplicate_frames_skipped == 0


def test_worker_restart_forces_an_equal_frame_to_be_resent(
    tmp_path: Path,
) -> None:
    events = {"frames": threading.Event()}
    connection = FakeConnection(events=events)
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.submit_frame(frame(7))
        wait(events["frames"])
    finally:
        worker.stop()
    first_total = connection.frame_report_count()

    events["frames"].clear()
    worker.start()
    try:
        worker.submit_frame(frame(7))
        wait(events["frames"])
    finally:
        worker.stop()

    assert first_total == CREATOR_REPORT_COUNT
    assert connection.frame_report_count() == 2 * CREATOR_REPORT_COUNT
    assert worker.snapshot().duplicate_frames_skipped == 0


# --------------------------------------------------------------------------
# Brightness
# --------------------------------------------------------------------------


def test_brightness_command_is_never_suppressed(tmp_path: Path) -> None:
    applied = threading.Event()
    connection = FakeConnection(events={"brightness": applied})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.set_brightness(50)
        wait(applied)
        applied.clear()
        worker.set_brightness(50)
        wait(applied)
    finally:
        worker.stop()

    assert connection.brightness_reports == 2


def test_brightness_change_invalidates_the_frame_baseline(
    tmp_path: Path,
) -> None:
    events = {
        "frame1": threading.Event(),
        "brightness": threading.Event(),
        "frame2": threading.Event(),
    }
    connection = FakeConnection(events=events)
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.submit_frame(frame(9))
        wait(events["frame1"])
        worker.set_brightness(60)
        wait(events["brightness"])
        # The same bytes must be resent because brightness may have changed
        # what the firmware retains.
        worker.submit_frame(frame(9), revision=4)
        wait(events["frame2"])
    finally:
        worker.stop()

    assert connection.activations == 2
    assert worker.snapshot().duplicate_frames_skipped == 0
    assert worker.snapshot().successful_uploads == 2


def test_frame_and_brightness_commands_do_not_interleave(
    tmp_path: Path,
) -> None:
    events = {"frame2": threading.Event(), "brightness": threading.Event()}
    connection = FakeConnection(events=events)
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        events["frame1"] = threading.Event()
        worker.submit_frame(frame(10))
        wait(events["frame1"])
        worker.set_brightness(70)
        wait(events["brightness"])
        worker.submit_frame(frame(11))
        wait(events["frame2"])
    finally:
        worker.stop()

    commands = [report[1] for report in connection.reports]
    # Every Creator transaction is a contiguous 0x05 run ending in 0x03.
    index = 0
    transactions = 0
    while index < len(commands):
        if commands[index] == BRIGHTNESS_COMMAND:
            index += 1
            continue
        block = commands[index : index + CREATOR_REPORT_COUNT]
        assert len(block) == CREATOR_REPORT_COUNT
        assert block[0] == 0x05
        assert block[-1] == ACTIVATION_COMMAND
        assert ACTIVATION_COMMAND not in block[:-1]
        assert BRIGHTNESS_COMMAND not in block
        transactions += 1
        index += CREATOR_REPORT_COUNT
    assert transactions == 2


# --------------------------------------------------------------------------
# Coalescing and diagnostics
# --------------------------------------------------------------------------


def test_concurrent_duplicate_submissions_produce_one_transaction(
    tmp_path: Path,
) -> None:
    connection = FakeConnection(events={"frames": threading.Event()})
    settled = threading.Event()

    def callback(snapshot) -> None:
        if (
            snapshot.pending_commands == 0
            and snapshot.frame_submissions_received >= 16
            and snapshot.successful_uploads >= 1
        ):
            settled.set()

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
    )
    payload = frame(12)

    worker.start()
    try:
        barrier = threading.Barrier(8)

        def submit() -> None:
            barrier.wait(5.0)
            for _ in range(2):
                worker.submit_frame(payload)

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5.0)
        wait(settled)
    finally:
        worker.stop()

    snapshot = worker.snapshot()
    assert snapshot.frame_submissions_received == 16
    assert snapshot.successful_uploads == 1
    assert connection.activations == 1
    assert connection.frame_report_count() == CREATOR_REPORT_COUNT


def test_latest_changed_frame_is_retained_during_coalescing(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    reached = threading.Event()
    final = threading.Event()

    class BlockingConnection(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            if len(self.reports) == 0 and not reached.is_set():
                reached.set()
                assert release.wait(5.0)
            return super().transact(report, timeout)

    connection = BlockingConnection(events={"frames": final})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.submit_frame(frame(20))
        assert reached.wait(5.0)
        # While the first transaction is in flight, queue an intermediate and
        # then a final frame. Only the newest must survive coalescing.
        worker.submit_frame(frame(21))
        worker.submit_frame(frame(22))
        release.set()
        deadline = threading.Event()
        for _ in range(200):
            if connection.activations >= 2:
                break
            deadline.wait(0.01)
    finally:
        worker.stop()

    activation_indexes = [
        index for index, report in enumerate(connection.reports)
        if report[1] == ACTIVATION_COMMAND
    ]
    assert len(activation_indexes) == 2
    # The second transaction carries the newest frame, not the intermediate.
    second_chunk = connection.reports[
        activation_indexes[0] + 1 : activation_indexes[1]
    ]
    payload = bytes()
    for report in second_chunk[1:]:
        payload += report[8 : 8 + (report[5] - 2)]
    assert payload == frame(22)


def test_diagnostics_expose_counters_without_framebuffer_data(
    tmp_path: Path,
) -> None:
    first = threading.Event()
    connection = FakeConnection(events={"frame1": first})
    skipped = threading.Event()

    def callback(snapshot) -> None:
        if snapshot.duplicate_frames_skipped >= 1:
            skipped.set()

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
        monotonic=lambda: 42.0,
    )

    worker.start()
    try:
        worker.submit_frame(frame(13))
        wait(first)
        worker.submit_frame(frame(13))
        wait(skipped)
    finally:
        worker.stop()

    snapshot = worker.snapshot()
    assert snapshot.frame_submissions_received == 2
    assert snapshot.successful_uploads == 1
    assert snapshot.duplicate_frames_skipped == 1
    assert snapshot.frame_transactions_failed == 0
    assert snapshot.last_frame_transmission_monotonic == 42.0

    serialized = repr(snapshot)
    assert frame(13).hex() not in serialized
    assert "\\r" not in serialized
    for field in ("framebuffer", "hash", "digest", "serial", "pixels"):
        assert field not in serialized.lower()


def test_baseline_is_never_published_in_the_snapshot(tmp_path: Path) -> None:
    first = threading.Event()
    connection = FakeConnection(events={"frame1": first})
    worker = build_worker(tmp_path, connection_factory=lambda path: connection)

    worker.start()
    try:
        worker.submit_frame(frame(14))
        wait(first)
        snapshot = worker.snapshot()
    finally:
        worker.stop()

    assert not any(
        isinstance(getattr(snapshot, field), (bytes, bytearray))
        for field in snapshot.__dataclass_fields__
    )


def test_pending_queue_stays_bounded_under_duplicate_pressure(
    tmp_path: Path,
) -> None:
    connection = FakeConnection(events={"frames": threading.Event()})
    observed: list[int] = []

    def callback(snapshot) -> None:
        observed.append(snapshot.pending_commands)

    worker = build_worker(
        tmp_path,
        connection_factory=lambda path: connection,
        status_callback=callback,
    )

    worker.start()
    try:
        for index in range(50):
            worker.submit_frame(frame(15))
        wait(connection.events["frames"])
    finally:
        worker.stop()

    assert observed
    assert max(observed) <= 2
