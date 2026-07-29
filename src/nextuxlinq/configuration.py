"""Sending Matrix900 reports."""

from __future__ import annotations

import select
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from nextuxlinq.controller import CommandResult
from nextuxlinq.layout import COMMAND_WIDGET_CONFIGURATION
from nextuxlinq.protocol import HID_REPORT_SIZE, REPORT_ID
from nextuxlinq.transport import open_hidraw, read_report, write_report


class ReportConnection(Protocol):
    """Persistent transport accepted by report-sequence execution."""

    def transact(self, report: bytes, timeout: float = 1.0) -> bytes | None: ...


def send_report_sequence_on_connection(
    connection: ReportConnection,
    reports: Sequence[bytes],
    timeout: float = 1.0,
) -> tuple[CommandResult, ...]:
    """Send a complete validated sequence on an existing connection."""
    validated_reports = tuple(_validate_report(report) for report in reports)
    if not validated_reports:
        raise ValueError("at least one report is required.")
    return tuple(
        CommandResult(report=report, response=connection.transact(report, timeout))
        for report in validated_reports
    )


def _validate_report(report: bytes) -> bytes:
    """Validate one complete Matrix900 HID report."""
    if not isinstance(report, bytes):
        raise TypeError("report must be bytes.")
    if len(report) != HID_REPORT_SIZE:
        raise ValueError(f"report must contain {HID_REPORT_SIZE} bytes.")
    if report[0] != REPORT_ID:
        raise ValueError("report has an invalid report ID.")
    return report


def send_report_sequence(
    device_path: Path,
    reports: Sequence[bytes],
    timeout: float = 1.0,
) -> tuple[CommandResult, ...]:
    """Send multiple reports through one hidraw connection."""
    validated_reports = tuple(_validate_report(report) for report in reports)

    if not validated_reports:
        raise ValueError("at least one report is required.")

    results: list[CommandResult] = []

    with open_hidraw(device_path) as file_descriptor:
        for report in validated_reports:
            write_report(file_descriptor, report)
            readable, _, _ = select.select([file_descriptor], [], [], timeout)
            response = read_report(file_descriptor) if readable else None
            results.append(CommandResult(report=report, response=response))

    return tuple(results)


def send_configuration_report(
    device_path: Path,
    report: bytes,
    timeout: float = 1.0,
) -> CommandResult:
    """Send one command 0x03 configuration report."""
    report = _validate_report(report)

    if report[1] != COMMAND_WIDGET_CONFIGURATION:
        raise ValueError("configuration report is not command 0x03.")

    return send_report_sequence(device_path, (report,), timeout)[0]
