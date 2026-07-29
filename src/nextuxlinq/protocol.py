"""Verified protocol primitives for the Phanteks Matrix900."""

from __future__ import annotations

MIN_BRIGHTNESS_PERCENT = 10
MAX_BRIGHTNESS_PERCENT = 100
BRIGHTNESS_STEP_PERCENT = 10


def encode_brightness(percentage: int) -> int:
    """Encode a NexLinq brightness percentage as a Matrix900 device value.

    The official NexLinq application exposes brightness in ten-percent steps.
    The conversion is based on USB captures from the Windows application.
    """
    if isinstance(percentage, bool) or not isinstance(percentage, int):
        raise TypeError("Brightness percentage must be an integer.")

    if not MIN_BRIGHTNESS_PERCENT <= percentage <= MAX_BRIGHTNESS_PERCENT:
        raise ValueError("Brightness percentage must be between 10 and 100.")

    if percentage % BRIGHTNESS_STEP_PERCENT != 0:
        raise ValueError("Brightness percentage must use ten-percent steps.")

    numerator = (60 * 99) + ((percentage - 1) * 120)
    return (numerator + 49) // 99


HID_REPORT_SIZE = 64
REPORT_ID = 0x01
COMMAND_DISPLAY_SETTINGS = 0x04


def build_brightness_report(percentage: int) -> bytes:
    """Build the verified 64-byte Matrix900 display-settings report."""
    report = bytearray(HID_REPORT_SIZE)
    report[0] = REPORT_ID
    report[1] = COMMAND_DISPLAY_SETTINGS
    report[5] = 0x01
    report[6] = encode_brightness(percentage)
    return bytes(report)


COMMAND_TELEMETRY = 0x01
TELEMETRY_PAYLOAD_LENGTH = 0x14


def build_telemetry_report(
    cpu_temperature: int,
    gpu_temperature: int,
    fan_rpm: int,
    timestamp: int,
) -> bytes:
    """Build a verified Matrix900 telemetry report."""
    values = {
        "CPU temperature": (cpu_temperature, 0xFF),
        "GPU temperature": (gpu_temperature, 0xFF),
        "fan RPM": (fan_rpm, 0xFFFF),
        "timestamp": (timestamp, 0xFFFFFFFF),
    }

    for name, (value, maximum) in values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer.")
        if not 0 <= value <= maximum:
            raise ValueError(f"{name} must be between 0 and {maximum}.")

    report = bytearray(HID_REPORT_SIZE)
    report[0] = REPORT_ID
    report[1] = COMMAND_TELEMETRY
    report[5] = TELEMETRY_PAYLOAD_LENGTH
    report[6] = cpu_temperature
    report[7] = gpu_temperature
    report[9:11] = fan_rpm.to_bytes(2, "big")
    report[21:25] = timestamp.to_bytes(4, "big")
    report[25] = 0x07
    return bytes(report)
