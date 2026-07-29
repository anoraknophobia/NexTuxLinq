"""High-level control operations for the Phanteks Matrix900."""

from __future__ import annotations

import select
from dataclasses import dataclass
from pathlib import Path

from nextuxlinq.protocol import build_brightness_report
from nextuxlinq.transport import open_hidraw, read_report, write_report


@dataclass(frozen=True)
class CommandResult:
    """Result of a Matrix900 command."""

    report: bytes
    response: bytes | None


def set_brightness(
    device_path: Path,
    percentage: int,
    timeout: float = 1.0,
) -> CommandResult:
    """Set Matrix900 brightness and return the device response."""
    report = build_brightness_report(percentage)

    with open_hidraw(device_path) as file_descriptor:
        write_report(file_descriptor, report)
        readable, _, _ = select.select([file_descriptor], [], [], timeout)
        response = read_report(file_descriptor) if readable else None

    return CommandResult(report=report, response=response)
