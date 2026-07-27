"""Low-level Linux hidraw transport for the Phanteks Matrix900."""

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

HID_REPORT_SIZE = 64


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
