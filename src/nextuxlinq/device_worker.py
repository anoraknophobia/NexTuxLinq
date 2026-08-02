"""Single-threaded Matrix900 discovery and HID transaction ownership."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from nextuxlinq.configuration import ReportConnection
from nextuxlinq.creator import FRAME_SIZE, build_creator_upload_reports
from nextuxlinq.device import Matrix900Device, find_matrix900
from nextuxlinq.protocol import build_brightness_report
from nextuxlinq.transport import HidrawConnection
from nextuxlinq.writer_lock import WriterLock

RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0)

# Conservative engineering defaults, not physically measured values.
#
# A complete Creator transaction is 51 reports. The Matrix900 interrupt
# endpoints use a one-millisecond polling interval, so a healthy echo returns
# in single-digit milliseconds and a complete transaction finishes well below
# one second. Both limits therefore leave a large margin for normal operation
# while bounding the worst case: without a total deadline, 51 reports could
# otherwise consume the per-report timeout each and stall the worker for
# roughly half a minute.
CREATOR_REPORT_TIMEOUT_SECONDS = 0.5
CREATOR_TRANSACTION_DEADLINE_SECONDS = 5.0

LOGGER = logging.getLogger(__name__)


class ClosableReportConnection(ReportConnection, Protocol):
    """Persistent report transport owned by the worker."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class UploadFrameCommand:
    """Upload one complete Creator frame as an indivisible transaction."""

    frame: bytes
    revision: int = 0

    def __post_init__(self) -> None:
        if len(self.frame) != FRAME_SIZE:
            raise ValueError(f"frame must contain exactly {FRAME_SIZE} bytes.")


@dataclass(frozen=True, slots=True)
class SetBrightnessCommand:
    """Apply one verified Matrix900 brightness setting."""

    percentage: int
    revision: int = 0

    def __post_init__(self) -> None:
        build_brightness_report(self.percentage)


class DeviceCommunicationError(OSError):
    """Internal failure to complete one verified Matrix900 transaction."""


class WorkerNotAcceptingError(RuntimeError):
    """Raised when a command is submitted outside the running lifecycle."""


@dataclass(frozen=True, slots=True)
class DeviceWorkerSnapshot:
    """Immutable public state for the device worker.

    The counters describe transmission activity only. They never expose
    framebuffer contents, framebuffer hashes, or device identity beyond the
    already published device path.
    """

    running: bool = False
    device_available: bool = False
    device_path: str | None = None
    multiple_device_warning: str | None = None
    last_display_error: str | None = None
    pending_commands: int = 0
    successful_uploads: int = 0
    frame_submissions_received: int = 0
    duplicate_frames_skipped: int = 0
    frame_transactions_failed: int = 0
    last_frame_transmission_monotonic: float | None = None


StatusCallback = Callable[[DeviceWorkerSnapshot], None]
ConnectionFactory = Callable[[Path], ClosableReportConnection]
Discover = Callable[[], Sequence[Matrix900Device]]
Waiter = Callable[[float, threading.Event], bool]
ThreadFactory = Callable[..., threading.Thread]


def _default_waiter(delay: float, stop_event: threading.Event) -> bool:
    return stop_event.wait(delay)


class DeviceWorker:
    """The only daemon component allowed to open or write Matrix900 hidraw."""

    def __init__(
        self,
        *,
        writer_lock: WriterLock,
        discover: Discover = find_matrix900,
        connection_factory: ConnectionFactory = HidrawConnection,
        status_callback: StatusCallback | None = None,
        waiter: Waiter = _default_waiter,
        reconnect_delays: Sequence[float] = RECONNECT_DELAYS,
        thread_factory: ThreadFactory = threading.Thread,
        monotonic: Callable[[], float] = time.monotonic,
        report_timeout: float = CREATOR_REPORT_TIMEOUT_SECONDS,
        transaction_deadline: float = CREATOR_TRANSACTION_DEADLINE_SECONDS,
    ) -> None:
        if not reconnect_delays or any(delay < 0 for delay in reconnect_delays):
            raise ValueError("reconnect_delays must contain non-negative values.")
        if report_timeout <= 0 or transaction_deadline <= 0:
            raise ValueError(
                "report_timeout and transaction_deadline must be positive."
            )
        self._monotonic = monotonic
        self._report_timeout = float(report_timeout)
        self._transaction_deadline = float(transaction_deadline)
        self._writer_lock = writer_lock
        self._discover = discover
        self._connection_factory = connection_factory
        self._status_callback = status_callback
        self._waiter = waiter
        self._reconnect_delays = tuple(reconnect_delays)
        self._thread_factory = thread_factory
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_frame: UploadFrameCommand | None = None
        self._pending_brightness: SetBrightnessCommand | None = None
        self._latest_frame: UploadFrameCommand | None = None
        self._latest_brightness: SetBrightnessCommand | None = None
        self._snapshot = DeviceWorkerSnapshot()
        self._lifecycle = "new"
        self._last_command_kind: str | None = None
        # Worker-thread-only baseline: the exact device framebuffer of the
        # last complete and successful Creator transaction on the currently
        # trusted connection. ``None`` means the retained device framebuffer
        # is unknown, so the next frame must always be transmitted. It is
        # never published, persisted, or read by any other thread.
        self._transmitted_frame: bytes | None = None

    def snapshot(self) -> DeviceWorkerSnapshot:
        with self._condition:
            return self._snapshot

    def set_status_callback(self, callback: StatusCallback | None) -> None:
        """Set the status consumer before the worker starts."""
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("Cannot change callback after worker start.")
            self._status_callback = callback

    def start(self) -> None:
        snapshot: DeviceWorkerSnapshot
        startup_error: BaseException | None = None
        with self._condition:
            if self._lifecycle not in ("new", "stopped"):
                raise RuntimeError("DeviceWorker is already started.")
            self._writer_lock.acquire()
            # A new worker session knows nothing about the retained device
            # framebuffer, so the first frame is always transmitted.
            self._invalidate_transmitted_frame()
            self._stop_event.clear()
            self._lifecycle = "starting"
            self._thread = self._thread_factory(
                target=self._run,
                name="nextuxlinq-device-worker",
                daemon=False,
            )
            snapshot = self._replace_snapshot_locked(
                running=True,
                last_display_error=None,
            )
            try:
                self._thread.start()
            except BaseException as error:
                self._thread = None
                self._lifecycle = "stopped"
                snapshot = self._replace_snapshot_locked(running=False)
                self._writer_lock.release()
                startup_error = error
            else:
                self._lifecycle = "running"
        self._publish(snapshot)
        if startup_error is not None:
            raise startup_error

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        """Queue the latest frame; duplicates are resolved by the worker thread.

        No duplicate filtering happens here. Coalescing keeps only the newest
        submission, and comparing before enqueueing could let an older
        duplicate hide a newer changed frame.
        """
        command = UploadFrameCommand(bytes(frame), revision)
        with self._condition:
            self._require_accepting_locked()
            self._latest_frame = command
            self._pending_frame = command
            self._snapshot = replace(
                self._snapshot,
                frame_submissions_received=(
                    self._snapshot.frame_submissions_received + 1
                ),
            )
            snapshot = self._update_pending_count_locked()
            self._condition.notify()
        self._publish(snapshot)

    def set_brightness(self, percentage: int, revision: int = 0) -> None:
        command = SetBrightnessCommand(percentage, revision)
        with self._condition:
            self._require_accepting_locked()
            self._latest_brightness = command
            self._pending_brightness = command
            snapshot = self._update_pending_count_locked()
            self._condition.notify()
        self._publish(snapshot)

    def stop(self, timeout: float | None = 5.0) -> None:
        with self._condition:
            thread = self._thread
            if thread is None:
                return
            self._lifecycle = "stopping"
            self._stop_event.set()
            self._condition.notify_all()
        thread.join(timeout)
        if thread.is_alive():
            with self._condition:
                snapshot = self._replace_snapshot_locked(
                    last_display_error="Matrix900 worker shutdown timed out."
                )
            self._publish(snapshot)
            raise TimeoutError("DeviceWorker did not stop within the timeout.")
        with self._condition:
            self._thread = None

    def _publish(self, snapshot: DeviceWorkerSnapshot) -> None:
        callback = self._status_callback
        if callback is not None:
            try:
                callback(snapshot)
            except Exception:
                pass

    def _replace_snapshot_locked(self, **changes: object) -> DeviceWorkerSnapshot:
        self._snapshot = replace(self._snapshot, **changes)
        return self._snapshot

    def _update_pending_count_locked(self) -> DeviceWorkerSnapshot:
        return self._replace_snapshot_locked(
            pending_commands=int(self._pending_frame is not None)
            + int(self._pending_brightness is not None)
        )

    def _require_accepting_locked(self) -> None:
        if self._lifecycle != "running":
            raise WorkerNotAcceptingError(
                "DeviceWorker is not accepting commands."
            )

    def _run(self) -> None:
        connection: ClosableReportConnection | None = None
        retry_index = 0
        primary_error: BaseException | None = None

        def reset_backoff() -> None:
            nonlocal retry_index
            retry_index = 0

        try:
            while not self._stop_event.is_set():
                if connection is None:
                    connection = self._connect()
                    if connection is None:
                        retry_index = self._wait_after_failure(retry_index)
                        continue
                    try:
                        self._reapply_latest(connection, reset_backoff)
                    except Exception:
                        self._disconnect(connection)
                        connection = None
                        retry_index = self._wait_after_failure(retry_index)
                        continue

                command = self._next_command()
                if command is None:
                    continue
                try:
                    if isinstance(command, UploadFrameCommand):
                        self._upload_frame(connection, command)
                    else:
                        self._set_device_brightness(connection, command)
                    retry_index = 0
                except Exception:
                    self._disconnect(connection)
                    connection = None
                    retry_index = self._wait_after_failure(retry_index)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_error: BaseException | None = None
            try:
                if connection is not None:
                    try:
                        connection.close()
                    except BaseException as error:
                        cleanup_error = error
            finally:
                try:
                    self._invalidate_transmitted_frame()
                    with self._condition:
                        self._pending_frame = None
                        self._pending_brightness = None
                        self._lifecycle = "stopped"
                        snapshot = self._replace_snapshot_locked(
                            running=False,
                            device_available=False,
                            device_path=None,
                            pending_commands=0,
                            last_display_error=(
                                "Matrix900 connection cleanup failed."
                                if cleanup_error is not None
                                and primary_error is None
                                else self._snapshot.last_display_error
                            ),
                        )
                    self._publish(snapshot)
                finally:
                    self._writer_lock.release()

    def _wait_after_failure(self, retry_index: int) -> int:
        delay = self._reconnect_delays[
            min(retry_index, len(self._reconnect_delays) - 1)
        ]
        self._waiter(delay, self._stop_event)
        return retry_index + 1

    def _update_status(self, **changes: object) -> None:
        with self._condition:
            snapshot = self._replace_snapshot_locked(**changes)
        self._publish(snapshot)

    def _connect(self) -> ClosableReportConnection | None:
        try:
            devices = sorted(self._discover(), key=lambda item: str(item.device_path))
        except Exception:
            self._invalidate_transmitted_frame()
            self._update_status(
                device_available=False,
                device_path=None,
                last_display_error="Matrix900 discovery failed.",
            )
            return None
        if not devices:
            self._invalidate_transmitted_frame()
            self._update_status(
                device_available=False,
                device_path=None,
                multiple_device_warning=None,
            )
            return None
        selected = devices[0]
        warning = (
            f"Multiple Matrix900 devices detected; using {selected.device_path}."
            if len(devices) > 1
            else None
        )
        try:
            connection = self._connection_factory(selected.device_path)
        except Exception:
            self._invalidate_transmitted_frame()
            self._update_status(
                device_available=False,
                device_path=None,
                multiple_device_warning=warning,
                last_display_error="Could not open the Matrix900 device.",
            )
            return None
        # Any newly opened connection, including one to a different device
        # path or a replaced device identity, has an unknown retained
        # framebuffer, so the next frame is always transmitted in full.
        self._invalidate_transmitted_frame()
        self._update_status(
            device_available=True,
            device_path=str(selected.device_path),
            multiple_device_warning=warning,
            last_display_error=None,
        )
        return connection

    def _next_command(
        self,
    ) -> UploadFrameCommand | SetBrightnessCommand | None:
        with self._condition:
            while (
                not self._stop_event.is_set()
                and self._pending_frame is None
                and self._pending_brightness is None
            ):
                self._condition.wait()
            if self._stop_event.is_set():
                return None
            both_pending = (
                self._pending_frame is not None
                and self._pending_brightness is not None
            )
            choose_frame = self._pending_frame is not None and (
                not both_pending or self._last_command_kind == "brightness"
            )
            if choose_frame:
                command = self._pending_frame
                self._pending_frame = None
                self._last_command_kind = "frame"
            else:
                command = self._pending_brightness
                self._pending_brightness = None
                self._last_command_kind = "brightness"
            snapshot = self._update_pending_count_locked()
        self._publish(snapshot)
        return command

    def _reapply_latest(
        self,
        connection: ClosableReportConnection,
        transaction_succeeded: Callable[[], None],
    ) -> None:
        with self._condition:
            frame = self._latest_frame
            brightness = self._latest_brightness
        if frame is not None:
            self._upload_frame(connection, frame)
            transaction_succeeded()
            with self._condition:
                self._last_command_kind = "frame"
                if self._pending_frame == frame:
                    self._pending_frame = None
                    snapshot = self._update_pending_count_locked()
                else:
                    snapshot = self._snapshot
            self._publish(snapshot)
        if brightness is not None:
            self._set_device_brightness(connection, brightness)
            transaction_succeeded()
            with self._condition:
                self._last_command_kind = "brightness"
                if self._pending_brightness == brightness:
                    self._pending_brightness = None
                    snapshot = self._update_pending_count_locked()
                else:
                    snapshot = self._snapshot
            self._publish(snapshot)

    def _require_echoes(
        self,
        connection: ClosableReportConnection,
        reports: Sequence[bytes],
    ) -> None:
        """Send one complete transaction within a single monotonic deadline.

        The deadline is established once, before the first report, so the
        elapsed time of every report counts against the same budget. Any
        failure aborts the remaining reports, which means the activation
        report is never sent after an earlier report failed.
        """
        deadline = self._monotonic() + self._transaction_deadline
        for report in reports:
            if self._stop_event.is_set():
                raise DeviceCommunicationError(
                    "Matrix900 transaction stopped before completion."
                )
            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                raise DeviceCommunicationError(
                    "Matrix900 transaction exceeded its deadline."
                )
            try:
                response = connection.transact(
                    report, min(self._report_timeout, remaining)
                )
            except Exception as error:
                raise DeviceCommunicationError(
                    "Matrix900 report transaction failed."
                ) from error
            if response != report:
                raise DeviceCommunicationError(
                    "Matrix900 report echo was missing or invalid."
                )

    def _invalidate_transmitted_frame(self) -> None:
        """Forget the baseline because the retained device frame is unknown."""
        self._transmitted_frame = None

    def _upload_frame(
        self,
        connection: ClosableReportConnection,
        command: UploadFrameCommand,
    ) -> None:
        """Transmit one complete Creator transaction unless it is a duplicate.

        The comparison uses the final device framebuffer: the exact immutable
        bytes produced after widget rendering, effects, foreground-mask
        processing, logical composition, and logical-to-device orientation
        conversion, which are the same bytes consumed by
        ``build_creator_upload_reports``. Nothing else participates, so a
        refreshed metric sample or a changed profile revision that renders to
        identical pixels transmits nothing.
        """
        frame = command.frame
        if self._transmitted_frame is not None and frame == self._transmitted_frame:
            with self._condition:
                snapshot = self._replace_snapshot_locked(
                    duplicate_frames_skipped=(
                        self._snapshot.duplicate_frames_skipped + 1
                    ),
                    last_display_error=None,
                )
            self._publish(snapshot)
            return

        # The device framebuffer becomes unknown the moment the first report
        # of a new transaction is sent, so an interrupted or failed upload can
        # never leave a stale baseline behind.
        self._invalidate_transmitted_frame()
        try:
            self._require_echoes(
                connection, build_creator_upload_reports(frame)
            )
        except Exception:
            with self._condition:
                snapshot = self._replace_snapshot_locked(
                    frame_transactions_failed=(
                        self._snapshot.frame_transactions_failed + 1
                    ),
                )
            self._publish(snapshot)
            raise

        # Only a complete transaction, including the activation echo, adopts a
        # new baseline.
        self._transmitted_frame = frame
        with self._condition:
            snapshot = self._replace_snapshot_locked(
                successful_uploads=self._snapshot.successful_uploads + 1,
                last_display_error=None,
                last_frame_transmission_monotonic=self._monotonic(),
            )
        self._publish(snapshot)

    def _set_device_brightness(
        self,
        connection: ClosableReportConnection,
        command: SetBrightnessCommand,
    ) -> None:
        # Brightness commands are never suppressed. Whether the firmware
        # retains the uploaded framebuffer across a brightness change has not
        # been verified on hardware, so the baseline is dropped conservatively
        # and the next frame is transmitted in full.
        self._invalidate_transmitted_frame()
        self._require_echoes(
            connection,
            (build_brightness_report(command.percentage),),
        )
        self._update_status(last_display_error=None)

    def _disconnect(self, connection: ClosableReportConnection) -> None:
        self._invalidate_transmitted_frame()
        try:
            connection.close()
        except Exception:
            LOGGER.warning(
                "Matrix900 connection cleanup failed after a communication failure."
            )
        self._update_status(
            device_available=False,
            device_path=None,
            last_display_error="Matrix900 communication failed.",
        )
