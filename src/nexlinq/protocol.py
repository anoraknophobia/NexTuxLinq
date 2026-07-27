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
