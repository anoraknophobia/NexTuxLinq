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
