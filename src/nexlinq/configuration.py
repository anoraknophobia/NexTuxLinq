"""Sending Matrix900 configuration reports."""

from __future__ import annotations

import select
from pathlib import Path

from nexlinq.controller import CommandResult
from nexlinq.layout import COMMAND_WIDGET_CONFIGURATION
from nexlinq.protocol import HID_REPORT_SIZE, REPORT_ID
from nexlinq.transport import open_hidraw, read_report, write_report


def send_configuration_report(
    device_path: Path,
    report: bytes,
    timeout: float = 1.0,
) -> CommandResult:
    """Send one command 0x03 configuration report."""
    if not isinstance(report, bytes):
        raise TypeError("configuration report must be bytes.")
    if len(report) != HID_REPORT_SIZE:
        raise ValueError(
            f"configuration report must contain {HID_REPORT_SIZE} bytes."
        )
    if report[0] != REPORT_ID:
        raise ValueError("configuration report has an invalid report ID.")
    if report[1] != COMMAND_WIDGET_CONFIGURATION:
        raise ValueError("configuration report is not command 0x03.")

    with open_hidraw(device_path) as file_descriptor:
        write_report(file_descriptor, report)
        readable, _, _ = select.select([file_descriptor], [], [], timeout)
        response = read_report(file_descriptor) if readable else None

    return CommandResult(report=report, response=response)
