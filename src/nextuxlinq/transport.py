"""Low-level Linux hidraw transport for the Phanteks Matrix900."""

from __future__ import annotations

import errno
import os
import select
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

HID_REPORT_SIZE = 64


class HidrawConnection:
    """One persistent non-blocking hidraw connection."""

    def __init__(self, device_path: Path) -> None:
        flags = os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC
        self.device_path = Path(device_path)
        self.file_descriptor: int | None = os.open(self.device_path, flags)

    def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None:
        """Write one report and read its response when available."""
        descriptor = self.file_descriptor
        if descriptor is None:
            raise OSError("hidraw connection is closed.")
        write_report(descriptor, report)
        readable, _, _ = select.select([descriptor], [], [], timeout)
        return read_report(descriptor) if readable else None

    def close(self) -> None:
        descriptor = self.file_descriptor
        if descriptor is not None:
            self.file_descriptor = None
            os.close(descriptor)

    def __enter__(self) -> HidrawConnection:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


@contextmanager
def open_hidraw(device_path: Path) -> Iterator[int]:
    """Open a hidraw device for non-blocking read and write access."""
    flags = os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC
    file_descriptor = os.open(device_path, flags)

    try:
        yield file_descriptor
    finally:
        os.close(file_descriptor)


def read_report(file_descriptor: int) -> bytes | None:
    """Read one HID input report without blocking."""
    try:
        return os.read(file_descriptor, HID_REPORT_SIZE)
    except BlockingIOError as error:
        if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return None
        raise


def write_report(file_descriptor: int, report: bytes) -> None:
    """Write exactly one validated 64-byte HID output report."""
    if not isinstance(report, bytes):
        raise TypeError("HID report must be bytes.")

    if len(report) != HID_REPORT_SIZE:
        raise ValueError(f"HID report must be exactly {HID_REPORT_SIZE} bytes.")

    bytes_written = os.write(file_descriptor, report)

    if bytes_written != HID_REPORT_SIZE:
        raise OSError(
            f"Incomplete HID report write: {bytes_written}/{HID_REPORT_SIZE} bytes."
        )
