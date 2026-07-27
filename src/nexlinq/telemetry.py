"""Linux hardware telemetry for the Phanteks Matrix900."""

from __future__ import annotations

import select
import time
from dataclasses import dataclass
from pathlib import Path

from nexlinq.controller import CommandResult
from nexlinq.protocol import build_telemetry_report
from nexlinq.transport import open_hidraw, read_report, write_report

HWMON_ROOT = Path("/sys/class/hwmon")


@dataclass(frozen=True)
class TelemetrySample:
    """A single set of Matrix900 telemetry values."""

    cpu_temperature: int
    gpu_temperature: int
    fan_rpm: int
    timestamp: int


def find_hwmon_chip(name: str, root: Path = HWMON_ROOT) -> Path:
    """Find a hwmon chip by its stable kernel name."""
    for path in sorted(root.glob("hwmon*")):
        try:
            if (path / "name").read_text(encoding="utf-8").strip() == name:
                return path
        except OSError:
            continue

    raise RuntimeError(f"hwmon chip not found: {name}")


def read_labeled_temperature(
    chip_name: str,
    label: str,
    root: Path = HWMON_ROOT,
) -> int:
    """Read and round a labeled hwmon temperature to whole degrees Celsius."""
    chip = find_hwmon_chip(chip_name, root)

    for input_path in sorted(chip.glob("temp*_input")):
        label_path = input_path.with_name(
            input_path.name.replace("_input", "_label")
        )

        try:
            if label_path.read_text(encoding="utf-8").strip() != label:
                continue
            millidegrees = int(input_path.read_text(encoding="utf-8").strip())
            return round(millidegrees / 1000)
        except OSError:
            continue

    raise RuntimeError(f"temperature sensor not found: {chip_name}/{label}")


def read_highest_fan_rpm(
    chip_name: str,
    root: Path = HWMON_ROOT,
) -> int:
    """Read the highest reported fan or pump speed from a hwmon chip."""
    chip = find_hwmon_chip(chip_name, root)
    values: list[int] = []

    for input_path in sorted(chip.glob("fan*_input")):
        try:
            values.append(int(input_path.read_text(encoding="utf-8").strip()))
        except OSError:
            continue

    if not values:
        raise RuntimeError(f"no fan sensors found: {chip_name}")

    return max(values)


def collect_default_telemetry(
    root: Path = HWMON_ROOT,
    timestamp: int | None = None,
) -> TelemetrySample:
    """Collect telemetry using the currently verified Linux sensor mapping."""
    return TelemetrySample(
        cpu_temperature=read_labeled_temperature("k10temp", "Tctl", root),
        gpu_temperature=read_labeled_temperature("amdgpu", "edge", root),
        fan_rpm=read_highest_fan_rpm("nct6798", root),
        timestamp=int(time.time()) if timestamp is None else timestamp,
    )


def send_telemetry(
    device_path: Path,
    sample: TelemetrySample,
    timeout: float = 1.0,
) -> CommandResult:
    """Send one telemetry sample and return the device response."""
    report = build_telemetry_report(
        sample.cpu_temperature,
        sample.gpu_temperature,
        sample.fan_rpm,
        sample.timestamp,
    )

    with open_hidraw(device_path) as file_descriptor:
        write_report(file_descriptor, report)
        readable, _, _ = select.select([file_descriptor], [], [], timeout)
        response = read_report(file_descriptor) if readable else None

    return CommandResult(report=report, response=response)
