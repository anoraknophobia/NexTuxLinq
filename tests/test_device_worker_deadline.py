"""Tests for bounded Matrix900 Creator transaction time.

Every test uses a controlled fake clock and a fake transport. No test sleeps,
opens a hidraw device, or touches real hardware.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nextuxlinq.creator import FRAME_SIZE, build_creator_upload_reports
from nextuxlinq.device import Matrix900Device
from nextuxlinq.device_worker import (
    CREATOR_REPORT_TIMEOUT_SECONDS,
    CREATOR_TRANSACTION_DEADLINE_SECONDS,
    DeviceWorker,
)
from nextuxlinq.writer_lock import WriterLock

CREATOR_REPORT_COUNT = len(build_creator_upload_reports(bytes(FRAME_SIZE)))
ACTIVATION_COMMAND = 0x03
INITIALIZATION_COMMAND = 0x05


class FakeClock:
    """A monotonic clock that only advances when a test asks it to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingConnection:
    """A transport that records reports and echoes them by default."""

    def __init__(
        self,
        *,
        clock: FakeClock | None = None,
        elapsed_per_report: float = 0.0,
        stall_from_index: int | None = None,
        fail_at_index: int | None = None,
        silent_from_index: int | None = None,
        completed: threading.Event | None = None,
    ) -> None:
        self.reports: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False
        self.close_calls = 0
        self._clock = clock
        self._elapsed_per_report = elapsed_per_report
        self._stall_from_index = stall_from_index
        self._fail_at_index = fail_at_index
        self._silent_from_index = silent_from_index
        self.completed = completed

    def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
        index = len(self.reports)
        self.timeouts.append(timeout)
        if self._fail_at_index is not None and index == self._fail_at_index:
            raise OSError("private transport detail")
        if self._clock is not None:
            if (
                self._stall_from_index is not None
                and index >= self._stall_from_index
            ):
                # A stalled device consumes the complete supplied timeout.
                self._clock.advance(timeout)
            else:
                self._clock.advance(self._elapsed_per_report)
        self.reports.append(report)
        if (
            self._silent_from_index is not None
            and index >= self._silent_from_index
        ):
            return None
        if self.completed is not None and report[1] == ACTIVATION_COMMAND:
            self.completed.set()
        return report

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    def commands(self) -> list[int]:
        return [report[1] for report in self.reports]


def device(path: str) -> Matrix900Device:
    return Matrix900Device(Path(path), Path(f"/sys/{Path(path).name}"))


def build_worker(
    tmp_path: Path,
    connection,
    *,
    clock: FakeClock,
    waiter=None,
    devices=None,
    **overrides,
) -> DeviceWorker:
    arguments = {
        "writer_lock": WriterLock(tmp_path / "matrix900.lock"),
        "discover": lambda: (devices or (device("/dev/hidraw0"),)),
        "connection_factory": lambda path: connection,
        "reconnect_delays": (0.0,),
        "monotonic": clock,
    }
    if waiter is not None:
        arguments["waiter"] = waiter
    arguments.update(overrides)
    return DeviceWorker(**arguments)


def run_until(worker: DeviceWorker, event: threading.Event) -> None:
    worker.start()
    try:
        assert event.wait(5.0)
    finally:
        worker.stop()


def test_default_constants_bound_a_complete_transaction() -> None:
    assert CREATOR_REPORT_COUNT == 51
    assert 0 < CREATOR_REPORT_TIMEOUT_SECONDS < 1.0
    assert 0 < CREATOR_TRANSACTION_DEADLINE_SECONDS
    # Without the total deadline a stalled device could consume the per-report
    # timeout 51 times over.
    naive_worst_case = CREATOR_REPORT_COUNT * CREATOR_REPORT_TIMEOUT_SECONDS
    assert CREATOR_TRANSACTION_DEADLINE_SECONDS < naive_worst_case


def test_normal_transaction_sends_the_established_report_count(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    completed = threading.Event()
    connection = RecordingConnection(
        clock=clock, elapsed_per_report=0.002, completed=completed
    )
    worker = build_worker(tmp_path, connection, clock=clock)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert completed.wait(5.0)
    finally:
        worker.stop()

    assert len(connection.reports) == CREATOR_REPORT_COUNT
    assert connection.reports[0][1] == INITIALIZATION_COMMAND
    assert connection.reports[-1][1] == ACTIVATION_COMMAND
    assert worker.snapshot().successful_uploads == 1


def test_normal_fast_echoes_use_the_per_report_timeout(tmp_path: Path) -> None:
    clock = FakeClock()
    completed = threading.Event()
    connection = RecordingConnection(
        clock=clock, elapsed_per_report=0.001, completed=completed
    )
    worker = build_worker(tmp_path, connection, clock=clock)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert completed.wait(5.0)
    finally:
        worker.stop()

    # A healthy device never has its budget reduced below the per-report limit.
    assert connection.timeouts
    assert all(
        timeout == pytest.approx(CREATOR_REPORT_TIMEOUT_SECONDS)
        for timeout in connection.timeouts
    )


def test_remaining_deadline_is_passed_once_the_budget_shrinks(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = RecordingConnection(clock=clock, elapsed_per_report=0.4)
    disconnected = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        disconnected.set()
        # Hold the worker here so exactly one transaction attempt is observed.
        return stop_event.wait(60.0)

    worker = build_worker(
        tmp_path,
        connection,
        clock=clock,
        waiter=waiter,
        transaction_deadline=1.0,
        report_timeout=0.5,
    )

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert disconnected.wait(5.0)
    finally:
        worker.stop()

    # 1.0 s budget, 0.4 s consumed per report: 0.5, 0.5, then the 0.2 s
    # remainder, then the budget is exhausted.
    assert connection.timeouts[0] == pytest.approx(0.5)
    assert connection.timeouts[1] == pytest.approx(0.5)
    assert connection.timeouts[2] == pytest.approx(0.2)
    assert all(
        timeout <= 0.5 + 1e-9 for timeout in connection.timeouts
    )


def test_total_deadline_prevents_cumulative_per_report_blocking(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    started = clock.now
    connection = RecordingConnection(clock=clock, stall_from_index=0)
    disconnected = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        disconnected.set()
        # Hold the worker here so exactly one transaction attempt is observed.
        return stop_event.wait(60.0)

    worker = build_worker(tmp_path, connection, clock=clock, waiter=waiter)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert disconnected.wait(5.0)
    finally:
        worker.stop()

    elapsed = clock.now - started
    assert elapsed <= CREATOR_TRANSACTION_DEADLINE_SECONDS + 1e-9
    # The naive worst case for 51 stalled reports is far larger.
    assert elapsed < CREATOR_REPORT_COUNT * CREATOR_REPORT_TIMEOUT_SECONDS
    assert len(connection.reports) < CREATOR_REPORT_COUNT


def test_deadline_expiry_aborts_later_reports_and_skips_activation(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = RecordingConnection(clock=clock, stall_from_index=0)
    disconnected = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        disconnected.set()
        # Hold the worker here so exactly one transaction attempt is observed.
        return stop_event.wait(60.0)

    worker = build_worker(tmp_path, connection, clock=clock, waiter=waiter)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert disconnected.wait(5.0)
    finally:
        worker.stop()

    assert ACTIVATION_COMMAND not in connection.commands()
    assert worker.snapshot().successful_uploads == 0


def test_activation_is_not_sent_after_an_earlier_report_failure(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = RecordingConnection(clock=clock, fail_at_index=10)
    disconnected = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        disconnected.set()
        # Hold the worker here so exactly one transaction attempt is observed.
        return stop_event.wait(60.0)

    worker = build_worker(tmp_path, connection, clock=clock, waiter=waiter)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert disconnected.wait(5.0)
    finally:
        worker.stop()

    assert len(connection.reports) == 10
    assert ACTIVATION_COMMAND not in connection.commands()
    assert worker.snapshot().successful_uploads == 0


def test_missing_echo_aborts_the_remaining_reports(tmp_path: Path) -> None:
    clock = FakeClock()
    connection = RecordingConnection(clock=clock, silent_from_index=5)
    disconnected = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        disconnected.set()
        # Hold the worker here so exactly one transaction attempt is observed.
        return stop_event.wait(60.0)

    worker = build_worker(tmp_path, connection, clock=clock, waiter=waiter)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert disconnected.wait(5.0)
    finally:
        worker.stop()

    assert len(connection.reports) == 6
    assert ACTIVATION_COMMAND not in connection.commands()
    assert worker.snapshot().successful_uploads == 0


def test_timeout_enters_the_existing_disconnect_and_backoff_path(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = RecordingConnection(clock=clock, stall_from_index=0)
    delays: list[float] = []
    observed = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        delays.append(delay)
        observed.set()
        return stop_event.wait(60.0)

    worker = build_worker(tmp_path, connection, clock=clock, waiter=waiter)

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert observed.wait(5.0)
    finally:
        worker.stop()

    assert connection.close_calls >= 1
    assert delays
    snapshot = worker.snapshot()
    assert snapshot.device_available is False
    assert snapshot.last_display_error == "Matrix900 communication failed."


def test_stop_request_is_noticed_between_reports(tmp_path: Path) -> None:
    clock = FakeClock()
    reached = threading.Event()
    release = threading.Event()

    class StoppingConnection(RecordingConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            if len(self.reports) == 3:
                reached.set()
                assert release.wait(5.0)
            return super().transact(report, timeout)

    connection = StoppingConnection(clock=clock, elapsed_per_report=0.001)
    worker = build_worker(tmp_path, connection, clock=clock)

    worker.start()
    worker.submit_frame(bytes(FRAME_SIZE))
    assert reached.wait(5.0)
    stopper = threading.Thread(target=worker.stop)
    stopper.start()
    release.set()
    stopper.join(5.0)

    assert not stopper.is_alive()
    # The transaction aborted well before the complete report count.
    assert len(connection.reports) < CREATOR_REPORT_COUNT
    assert ACTIVATION_COMMAND not in connection.commands()


def test_writer_lock_is_released_after_a_deadline_shutdown(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = RecordingConnection(clock=clock, stall_from_index=0)
    observed = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        observed.set()
        return stop_event.wait(60.0)

    lock = WriterLock(tmp_path / "matrix900.lock")
    worker = build_worker(tmp_path, connection, clock=clock, waiter=waiter)
    worker._writer_lock = lock

    worker.start()
    try:
        worker.submit_frame(bytes(FRAME_SIZE))
        assert observed.wait(5.0)
    finally:
        worker.stop()

    assert lock.acquired is False
    # A fresh writer can take the lock immediately after shutdown.
    replacement = WriterLock(tmp_path / "matrix900.lock")
    replacement.acquire()
    replacement.release()


def test_brightness_transaction_also_uses_a_bounded_timeout(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    applied = threading.Event()

    class BrightnessConnection(RecordingConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            response = super().transact(report, timeout)
            if report[1] == 0x04:
                applied.set()
            return response

    connection = BrightnessConnection(clock=clock, stall_from_index=0)
    worker = build_worker(tmp_path, connection, clock=clock)
    started = clock.now

    worker.start()
    try:
        worker.set_brightness(50)
        assert applied.wait(5.0)
    finally:
        worker.stop()

    # One brightness report is bounded by the per-report timeout alone, so it
    # can never approach the complete-transaction deadline.
    assert connection.timeouts == [pytest.approx(CREATOR_REPORT_TIMEOUT_SECONDS)]
    assert clock.now - started <= CREATOR_REPORT_TIMEOUT_SECONDS + 1e-9
    assert clock.now - started < CREATOR_TRANSACTION_DEADLINE_SECONDS


def test_invalid_timeout_configuration_is_rejected(tmp_path: Path) -> None:
    for overrides in ({"report_timeout": 0.0}, {"transaction_deadline": -1.0}):
        with pytest.raises(ValueError):
            DeviceWorker(
                writer_lock=WriterLock(tmp_path / "matrix900.lock"),
                discover=lambda: (),
                connection_factory=lambda path: RecordingConnection(),
                **overrides,
            )
