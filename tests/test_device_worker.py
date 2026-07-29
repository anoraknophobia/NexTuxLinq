"""Hardware-independent tests for exclusive Matrix900 device ownership."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nextuxlinq.creator import FRAME_SIZE
from nextuxlinq.device import Matrix900Device
from nextuxlinq.device_worker import (
    DeviceWorker,
    DeviceWorkerSnapshot,
    WorkerNotAcceptingError,
)
from nextuxlinq.protocol import build_brightness_report
from nextuxlinq.writer_lock import WriterLock


class FakeConnection:
    def __init__(
        self,
        *,
        fail_once: bool = False,
        completed: threading.Event | None = None,
    ) -> None:
        self.reports: list[bytes] = []
        self.closed = False
        self.close_calls = 0
        self.fail_once = fail_once
        self.completed = completed

    def transact(self, report: bytes, timeout: float = 1.0) -> bytes:
        if self.fail_once:
            self.fail_once = False
            raise OSError("private device detail")
        self.reports.append(report)
        if self.completed is not None and report[1] == 0x04:
            self.completed.set()
        return report

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def device(path: str) -> Matrix900Device:
    return Matrix900Device(Path(path), Path(f"/sys/{Path(path).name}"))


def worker(
    tmp_path: Path,
    *,
    discover,
    connection_factory,
    callback=None,
    waiter=None,
) -> DeviceWorker:
    arguments = {
        "writer_lock": WriterLock(tmp_path / "matrix900.lock"),
        "discover": discover,
        "connection_factory": connection_factory,
        "status_callback": callback,
        "reconnect_delays": (0.0,),
    }
    if waiter is not None:
        arguments["waiter"] = waiter
    return DeviceWorker(**arguments)


def test_frame_transaction_is_contiguous_and_brightness_cannot_interleave(
    tmp_path: Path,
) -> None:
    completed = threading.Event()
    discovery_entered = threading.Event()
    release_discovery = threading.Event()
    connection = FakeConnection(completed=completed)
    opened: list[Path] = []

    def gated_discovery():
        discovery_entered.set()
        release_discovery.wait()
        return [device("/dev/fake0")]

    instance = worker(
        tmp_path,
        discover=gated_discovery,
        connection_factory=lambda path: opened.append(path) or connection,
    )
    instance.start()
    assert discovery_entered.wait(1)
    instance.submit_frame(bytes(FRAME_SIZE), revision=1)
    instance.set_brightness(50, revision=1)
    release_discovery.set()
    assert completed.wait(1)
    instance.stop()

    assert opened == [Path("/dev/fake0")]
    assert len(connection.reports) == 52
    assert all(report[1] in (0x03, 0x05) for report in connection.reports[:51])
    assert connection.reports[51] == build_brightness_report(50)


def test_latest_pending_values_win_and_queue_is_bounded(tmp_path: Path) -> None:
    completed = threading.Event()
    discovery_entered = threading.Event()
    release_discovery = threading.Event()
    connection = FakeConnection(completed=completed)

    def gated_discovery():
        discovery_entered.set()
        release_discovery.wait()
        return [device("/dev/fake0")]

    instance = worker(
        tmp_path,
        discover=gated_discovery,
        connection_factory=lambda path: connection,
    )
    old_frame = bytes(FRAME_SIZE)
    new_frame = bytes([7]) * FRAME_SIZE
    instance.start()
    assert discovery_entered.wait(1)
    instance.submit_frame(old_frame)
    instance.submit_frame(new_frame)
    instance.set_brightness(20)
    instance.set_brightness(80)
    assert instance.snapshot().pending_commands == 2
    release_discovery.set()

    assert completed.wait(1)
    instance.stop()

    frame_payload = b"".join(
        report[8 : 8 + report[5] - 2] for report in connection.reports[1:50]
    )
    assert frame_payload == new_frame
    assert connection.reports[-1] == build_brightness_report(80)


def test_disconnect_reconnect_reapplies_latest_frame_and_brightness(
    tmp_path: Path,
) -> None:
    completed = threading.Event()
    first = FakeConnection(fail_once=True)
    second = FakeConnection(completed=completed)
    connections = iter((first, second))
    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: next(connections),
    )
    frame = bytes([3]) * FRAME_SIZE
    instance.start()
    instance.submit_frame(frame)
    instance.set_brightness(60)
    assert completed.wait(1)
    instance.stop()

    assert first.closed
    assert len(second.reports) == 52
    reapplied_frame = b"".join(
        report[8 : 8 + report[5] - 2] for report in second.reports[1:50]
    )
    assert reapplied_frame == frame
    assert second.reports[-1] == build_brightness_report(60)
    assert instance.snapshot().last_display_error is None


def test_no_device_worker_stays_alive_and_stop_is_prompt(tmp_path: Path) -> None:
    waiting = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        waiting.set()
        return stop_event.wait(1000)

    instance = worker(
        tmp_path,
        discover=lambda: [],
        connection_factory=lambda path: (_ for _ in ()).throw(AssertionError()),
        waiter=waiter,
    )
    instance.start()
    for value in range(20):
        instance.submit_frame(bytes([value]) * FRAME_SIZE)
    assert instance.snapshot().pending_commands == 1
    assert waiting.wait(1)
    assert instance.snapshot().running
    assert not instance.snapshot().device_available
    instance.stop(timeout=1)
    assert not instance.snapshot().running


def test_multiple_devices_use_deterministic_first_path(tmp_path: Path) -> None:
    connected = threading.Event()
    selected: list[Path] = []

    def factory(path: Path) -> FakeConnection:
        selected.append(path)
        return FakeConnection()

    def callback(snapshot: DeviceWorkerSnapshot) -> None:
        if snapshot.device_available:
            connected.set()

    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake9"), device("/dev/fake1")],
        connection_factory=factory,
        callback=callback,
    )
    instance.start()
    assert connected.wait(1)
    snapshot = instance.snapshot()
    instance.stop()

    assert selected == [Path("/dev/fake1")]
    assert snapshot.multiple_device_warning is not None
    assert "/dev/fake1" in snapshot.multiple_device_warning


def test_public_errors_are_sanitized(tmp_path: Path) -> None:
    observed = threading.Event()
    snapshots: list[DeviceWorkerSnapshot] = []

    def callback(snapshot: DeviceWorkerSnapshot) -> None:
        snapshots.append(snapshot)
        if snapshot.last_display_error:
            observed.set()

    instance = worker(
        tmp_path,
        discover=lambda: (_ for _ in ()).throw(
            OSError("/private/path and serial")
        ),
        connection_factory=lambda path: FakeConnection(),
        callback=callback,
        waiter=lambda delay, stop: stop.wait(1000),
    )
    instance.start()
    assert observed.wait(1)
    instance.stop()

    assert any(
        snapshot.last_display_error == "Matrix900 discovery failed."
        for snapshot in snapshots
    )


def test_submission_is_rejected_before_start(tmp_path: Path) -> None:
    instance = worker(
        tmp_path,
        discover=lambda: [],
        connection_factory=lambda path: FakeConnection(),
        waiter=lambda delay, stop: stop.wait(1000),
    )
    with pytest.raises(WorkerNotAcceptingError):
        instance.submit_frame(bytes(FRAME_SIZE))
    assert instance.snapshot().pending_commands == 0


def test_submission_is_rejected_after_stop(tmp_path: Path) -> None:
    instance = worker(
        tmp_path,
        discover=lambda: [],
        connection_factory=lambda path: FakeConnection(),
        waiter=lambda delay, stop: stop.wait(1000),
    )
    instance.start()
    instance.stop()
    with pytest.raises(WorkerNotAcceptingError):
        instance.set_brightness(50)
    assert instance.snapshot().pending_commands == 0


@pytest.mark.parametrize("failure_index", [0, 17])
def test_echo_failure_aborts_before_later_reports(
    tmp_path: Path,
    failure_index: int,
) -> None:
    failed = threading.Event()

    class EchoFailureConnection(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            self.reports.append(report)
            if len(self.reports) - 1 == failure_index:
                failed.set()
                return None
            return report

    connection = EchoFailureConnection()
    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: connection,
        waiter=lambda delay, stop: stop.wait(1000),
    )
    instance.start()
    instance.submit_frame(bytes(FRAME_SIZE))
    assert failed.wait(1)
    instance.stop()

    assert len(connection.reports) == failure_index + 1
    assert instance.snapshot().last_display_error == "Matrix900 communication failed."


def test_backoff_progresses_and_resets_after_success(tmp_path: Path) -> None:
    delays: list[float] = []
    frame_replayed = threading.Event()
    second_failure = threading.Event()

    class AlwaysMismatch(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            self.reports.append(report)
            return None

    class ReplayThenBrightnessFailure(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            self.reports.append(report)
            if len(self.reports) == 51:
                frame_replayed.set()
            if report[1] == 0x04:
                second_failure.set()
                return None
            return report

    connections = iter(
        (AlwaysMismatch(), ReplayThenBrightnessFailure(), FakeConnection())
    )

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        delays.append(delay)
        if len(delays) >= 2:
            return stop_event.wait(1000)
        return False

    instance = DeviceWorker(
        writer_lock=WriterLock(tmp_path / "matrix900.lock"),
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: next(connections),
        waiter=waiter,
        reconnect_delays=(1.0, 2.0, 5.0),
    )
    instance.start()
    instance.submit_frame(bytes(FRAME_SIZE))
    assert frame_replayed.wait(1)
    instance.set_brightness(40)
    assert second_failure.wait(1)
    instance.stop()

    assert delays[:2] == [1.0, 1.0]


@pytest.mark.parametrize("failure_kind", ["discovery", "open"])
def test_discovery_and_open_failures_use_bounded_backoff(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    delays: list[float] = []
    completed = threading.Event()

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        delays.append(delay)
        if len(delays) == 4:
            stop_event.set()
            completed.set()
        return stop_event.is_set()

    def discover():
        if failure_kind == "discovery":
            raise OSError("private discovery failure")
        return [device("/dev/fake0")]

    def connect(path: Path):
        raise OSError("private open failure")

    instance = DeviceWorker(
        writer_lock=WriterLock(tmp_path / "matrix900.lock"),
        discover=discover,
        connection_factory=connect,
        waiter=waiter,
        reconnect_delays=(1.0, 2.0, 5.0),
    )
    instance.start()
    assert completed.wait(1)
    instance.stop(timeout=1)

    assert delays == [1.0, 2.0, 5.0, 5.0]


@pytest.mark.parametrize("command_kind", ["frame", "brightness"])
def test_command_and_replay_failures_use_bounded_backoff(
    tmp_path: Path,
    command_kind: str,
) -> None:
    connected = threading.Event()
    completed = threading.Event()
    delays: list[float] = []

    class FailedCommunication(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            return None

    def waiter(delay: float, stop_event: threading.Event) -> bool:
        delays.append(delay)
        if len(delays) == 4:
            stop_event.set()
            completed.set()
        return stop_event.is_set()

    instance = DeviceWorker(
        writer_lock=WriterLock(tmp_path / "matrix900.lock"),
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: connected.set() or FailedCommunication(),
        status_callback=None,
        waiter=waiter,
        reconnect_delays=(1.0, 2.0, 5.0),
    )
    instance.start()
    assert connected.wait(1)
    if command_kind == "frame":
        instance.submit_frame(bytes(FRAME_SIZE))
    else:
        instance.set_brightness(50)
    assert completed.wait(1)
    instance.stop(timeout=1)

    assert delays == [1.0, 2.0, 5.0, 5.0]


def test_close_failure_during_shutdown_still_releases_lock(tmp_path: Path) -> None:
    connected = threading.Event()

    class CloseFailure(FakeConnection):
        def close(self) -> None:
            super().close()
            raise OSError("private close failure")

    connection = CloseFailure()
    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: connected.set() or connection,
    )
    instance.start()
    assert connected.wait(1)
    instance.stop()

    assert not instance.snapshot().running
    assert instance.snapshot().last_display_error == (
        "Matrix900 connection cleanup failed."
    )
    replacement = WriterLock(tmp_path / "matrix900.lock")
    replacement.acquire()
    replacement.release()


def test_close_failure_after_communication_error_releases_lock(
    tmp_path: Path,
    caplog,
) -> None:
    failed = threading.Event()

    class FailureConnection(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
            failed.set()
            return None

        def close(self) -> None:
            super().close()
            raise OSError("private close failure")

    connection = FailureConnection()
    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: connection,
        waiter=lambda delay, stop: stop.wait(1000),
    )
    instance.start()
    instance.submit_frame(bytes(FRAME_SIZE))
    assert failed.wait(1)
    instance.stop()

    assert not instance.snapshot().running
    assert instance.snapshot().last_display_error == "Matrix900 communication failed."
    assert (
        "Matrix900 connection cleanup failed after a communication failure."
        in [record.getMessage() for record in caplog.records]
    )
    assert all(
        "private close failure" not in record.getMessage()
        for record in caplog.records
    )
    replacement = WriterLock(tmp_path / "matrix900.lock")
    replacement.acquire()
    replacement.release()


def test_thread_start_failure_rolls_back_and_worker_can_restart(tmp_path: Path) -> None:
    class FailedThread:
        def start(self) -> None:
            raise RuntimeError("thread creation failed")

    factories = iter(
        (
            lambda **kwargs: FailedThread(),
            lambda **kwargs: threading.Thread(**kwargs),
        )
    )
    lock = WriterLock(tmp_path / "matrix900.lock")
    instance = DeviceWorker(
        writer_lock=lock,
        discover=lambda: [],
        connection_factory=lambda path: FakeConnection(),
        waiter=lambda delay, stop: stop.wait(1000),
        reconnect_delays=(0.0,),
        thread_factory=lambda **kwargs: next(factories)(**kwargs),
    )

    with pytest.raises(RuntimeError, match="thread creation failed"):
        instance.start()
    assert not lock.acquired
    assert not instance.snapshot().running

    instance.start()
    instance.stop()
    assert not instance.snapshot().running


def test_stop_timeout_rejects_new_commands_and_records_error(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockedConnection(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes:
            entered.set()
            release.wait()
            return report

    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: BlockedConnection(),
    )
    instance.start()
    instance.set_brightness(30)
    assert entered.wait(1)
    with pytest.raises(TimeoutError):
        instance.stop(timeout=0)
    with pytest.raises(WorkerNotAcceptingError):
        instance.submit_frame(bytes(FRAME_SIZE))
    assert instance.snapshot().last_display_error == (
        "Matrix900 worker shutdown timed out."
    )
    release.set()
    instance.stop(timeout=1)


def test_callbacks_run_outside_lock_and_failures_are_ignored(tmp_path: Path) -> None:
    callback_entered = threading.Event()
    release_callback = threading.Event()
    callbacks = 0
    instance: DeviceWorker

    def callback(snapshot: DeviceWorkerSnapshot) -> None:
        nonlocal callbacks
        callbacks += 1
        instance.snapshot()
        if callbacks == 1:
            callback_entered.set()
            release_callback.wait(1)
        elif callbacks == 2:
            raise RuntimeError("consumer failure")

    instance = worker(
        tmp_path,
        discover=lambda: [],
        connection_factory=lambda path: FakeConnection(),
        callback=callback,
        waiter=lambda delay, stop: stop.wait(1000),
    )
    starter = threading.Thread(target=instance.start)
    starter.start()
    assert callback_entered.wait(1)
    instance.submit_frame(bytes(FRAME_SIZE))
    release_callback.set()
    starter.join(1)
    assert not starter.is_alive()
    assert instance.snapshot().running
    instance.stop()


def test_pending_frame_runs_after_at_most_one_brightness(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    frame_complete = threading.Event()

    class BlockingFirstConnection(FakeConnection):
        def transact(self, report: bytes, timeout: float = 1.0) -> bytes:
            self.reports.append(report)
            if len(self.reports) == 1:
                entered.set()
                release.wait(1)
            if len(self.reports) == 52:
                frame_complete.set()
            return report

    connection = BlockingFirstConnection()
    instance = worker(
        tmp_path,
        discover=lambda: [device("/dev/fake0")],
        connection_factory=lambda path: connection,
    )
    instance.start()
    instance.set_brightness(10)
    assert entered.wait(1)
    instance.submit_frame(bytes(FRAME_SIZE))
    instance.set_brightness(20)
    release.set()
    assert frame_complete.wait(1)
    instance.stop()

    assert connection.reports[0] == build_brightness_report(10)
    assert all(report[1] in (0x03, 0x05) for report in connection.reports[1:52])
