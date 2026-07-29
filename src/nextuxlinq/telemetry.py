"""Linux hardware telemetry for the Phanteks Matrix900."""

from __future__ import annotations

import json
import select
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from nextuxlinq.controller import CommandResult
from nextuxlinq.protocol import build_telemetry_report
from nextuxlinq.transport import open_hidraw, read_report, write_report

HWMON_ROOT = Path("/sys/class/hwmon")
OPENLINKHUB_DEVICES_URL = "http://127.0.0.1:27003/api/devices/"

RPM_SOURCE_FAN = "fan"
RPM_SOURCE_PUMP = "pump"
RPM_SOURCES = (RPM_SOURCE_FAN, RPM_SOURCE_PUMP)


@dataclass(frozen=True)
class TelemetrySample:
    """A single set of Matrix900 telemetry values."""

    cpu_temperature: int
    gpu_temperature: int
    fan_rpm: int
    timestamp: int
    rpm_source: str = "unknown"


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

            millidegrees = int(
                input_path.read_text(encoding="utf-8").strip()
            )
            return round(millidegrees / 1000)
        except OSError:
            continue

    raise RuntimeError(f"temperature sensor not found: {chip_name}/{label}")


def read_highest_fan_rpm(
    chip_name: str,
    root: Path = HWMON_ROOT,
) -> int:
    """Read the highest RPM value reported by a hwmon chip."""
    chip = find_hwmon_chip(chip_name, root)
    values: list[int] = []

    for input_path in sorted(chip.glob("fan*_input")):
        try:
            values.append(
                int(input_path.read_text(encoding="utf-8").strip())
            )
        except OSError:
            continue

    if not values:
        raise RuntimeError(f"no RPM sensors found: {chip_name}")

    return max(values)


def extract_openlinkhub_case_fan_rpms(payload: object) -> tuple[int, ...]:
    """Extract physical case-fan RPM values from an OpenLinkHub response."""
    if not isinstance(payload, Mapping):
        raise RuntimeError("OpenLinkHub response is not a JSON object.")

    controllers = payload.get("devices")
    if not isinstance(controllers, Mapping):
        raise RuntimeError("OpenLinkHub response has no devices object.")

    values: list[int] = []

    for controller in controllers.values():
        if not isinstance(controller, Mapping):
            continue

        device_state = controller.get("GetDevice")
        if not isinstance(device_state, Mapping):
            continue

        devices = device_state.get("devices")
        if not isinstance(devices, Mapping):
            continue

        for device in devices.values():
            if not isinstance(device, Mapping):
                continue

            description = str(device.get("description", "")).casefold()
            has_speed = bool(device.get("HasSpeed", False))
            is_aio = bool(device.get("AIO", False))
            is_cpu_block = bool(device.get("IsCpuBlock", False))

            try:
                rpm = int(device.get("rpm", 0))
            except (TypeError, ValueError):
                continue

            if (
                has_speed
                and description == "fan"
                and not is_aio
                and not is_cpu_block
                and rpm > 0
            ):
                values.append(rpm)

    if not values:
        raise RuntimeError("OpenLinkHub reported no active case fans.")

    return tuple(values)


def case_fan_rpm_from_openlinkhub_payload(payload: object) -> int:
    """Return the rounded average speed of all active case fans."""
    values = extract_openlinkhub_case_fan_rpms(payload)
    return round(sum(values) / len(values))


def read_openlinkhub_case_fan_rpm(
    url: str = OPENLINKHUB_DEVICES_URL,
    timeout: float = 1.0,
) -> int:
    """Read the average physical case-fan speed from OpenLinkHub."""
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except (
        OSError,
        TimeoutError,
        URLError,
        json.JSONDecodeError,
    ) as error:
        raise RuntimeError(
            f"failed to read OpenLinkHub telemetry from {url}"
        ) from error

    return case_fan_rpm_from_openlinkhub_payload(payload)


def collect_default_telemetry(
    root: Path = HWMON_ROOT,
    timestamp: int | None = None,
    rpm_source: str = RPM_SOURCE_FAN,
    openlinkhub_url: str = OPENLINKHUB_DEVICES_URL,
) -> TelemetrySample:
    """Collect telemetry using the currently verified Linux sensor mapping."""
    if rpm_source == RPM_SOURCE_FAN:
        rpm = read_openlinkhub_case_fan_rpm(openlinkhub_url)
        source_description = "OpenLinkHub case-fan average"
    elif rpm_source == RPM_SOURCE_PUMP:
        rpm = read_highest_fan_rpm("nct6798", root)
        source_description = "nct6798 highest RPM"
    else:
        raise ValueError(
            f"RPM source must be one of: {chr(44).join(RPM_SOURCES)}."
        )

    return TelemetrySample(
        cpu_temperature=read_labeled_temperature("k10temp", "Tctl", root),
        gpu_temperature=read_labeled_temperature("amdgpu", "edge", root),
        fan_rpm=rpm,
        timestamp=int(time.time()) if timestamp is None else timestamp,
        rpm_source=source_description,
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
