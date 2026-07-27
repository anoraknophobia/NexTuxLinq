"""Pure configurable rendering for the Matrix900 dynamic display."""

from __future__ import annotations

import math
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from nexlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH, FrameBuffer, RGB
from nexlinq.models import ProfileConfig, color_to_rgb

BACKGROUND: RGB = (0, 0, 0)
MARQUEE_TRAILING_GAP = 6
Glyph = tuple[str, ...]

_FONT_3X7: dict[str, Glyph] = {
    "0": ("111", "101", "101", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "010", "010", "111"),
    "2": ("111", "001", "001", "111", "100", "100", "111"),
    "3": ("111", "001", "001", "111", "001", "001", "111"),
    "4": ("101", "101", "101", "111", "001", "001", "001"),
    "5": ("111", "100", "100", "111", "001", "001", "111"),
    "6": ("111", "100", "100", "111", "101", "101", "111"),
    "7": ("111", "001", "001", "010", "010", "010", "010"),
    "8": ("111", "101", "101", "111", "101", "101", "111"),
    "9": ("111", "101", "101", "111", "001", "001", "111"),
    ":": ("0", "1", "0", "0", "1", "0", "0"),
}

_FONT_5X7: dict[str, Glyph] = {
    " ": ("000",) * 7,
    "!": ("1", "1", "1", "1", "1", "0", "1"),
    "-": ("000", "000", "000", "111", "000", "000", "000"),
    ".": ("0", "0", "0", "0", "0", "0", "1"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ":": ("0", "1", "0", "0", "1", "0", "0"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("111", "010", "010", "010", "010", "010", "111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
}


def validate_font(font: Mapping[str, Glyph]) -> None:
    """Validate seven-row glyph geometry and binary pixel data."""
    for character, glyph in font.items():
        if len(glyph) != 7:
            raise ValueError(f"Glyph {character!r} must contain exactly seven rows.")
        widths = {len(row) for row in glyph}
        if len(widths) != 1 or not widths or 0 in widths:
            raise ValueError(f"Glyph {character!r} has inconsistent row widths.")
        if any(set(row) - {"0", "1"} for row in glyph):
            raise ValueError(f"Glyph {character!r} contains non-binary pixels.")


validate_font(_FONT_3X7)
validate_font(_FONT_5X7)
FONT_3X7: Mapping[str, Glyph] = MappingProxyType(_FONT_3X7)
FONT_5X7: Mapping[str, Glyph] = MappingProxyType(_FONT_5X7)


def text_width(text: str, font: Mapping[str, Glyph], spacing: int = 1) -> int:
    if not text:
        return 0
    return sum(len(font[character][0]) for character in text) + spacing * (
        len(text) - 1
    )


def set_region_pixel(
    frame: FrameBuffer, *, region_x: int, region_width: int,
    x: int, y: int, color: RGB
) -> None:
    """Map a physical region to Creator placement without mirroring content."""
    if region_width <= 0:
        raise ValueError("Region width must be positive")
    if not 0 <= region_x < MATRIX_WIDTH or region_x + region_width > MATRIX_WIDTH:
        raise ValueError("Region must fit inside the Matrix900 width")
    if not region_x <= x < region_x + region_width or not 0 <= y < MATRIX_HEIGHT:
        return
    raw_x = MATRIX_WIDTH - region_x - region_width + (x - region_x)
    frame.set_pixel(raw_x, y, color)


def draw_text_clipped(
    frame: FrameBuffer, text: str, *, font: Mapping[str, Glyph],
    origin_x: int, origin_y: int, clip_x: int, clip_width: int,
    color: RGB, spacing: int = 1
) -> None:
    cursor_x = origin_x
    for character in text:
        try:
            glyph = font[character]
        except KeyError as error:
            raise ValueError(
                f"Unsupported display character: {character!r}"
            ) from error
        for row, pixels in enumerate(glyph):
            for column, enabled in enumerate(pixels):
                physical_x = cursor_x + column
                if enabled == "1" and clip_x <= physical_x < clip_x + clip_width:
                    set_region_pixel(
                        frame, region_x=clip_x, region_width=clip_width,
                        x=physical_x, y=origin_y + row, color=color
                    )
        cursor_x += len(glyph[0]) + spacing


def render_profile_frame(
    profile: ProfileConfig, now: datetime, scroll_offset: int
) -> FrameBuffer:
    """Render a validated profile at an integer scrolling pixel offset."""
    if isinstance(scroll_offset, bool) or not isinstance(scroll_offset, int):
        raise TypeError("scroll_offset must be an integer.")
    if scroll_offset < 0:
        raise ValueError("scroll_offset must not be negative.")
    layout = profile.layout
    scroll_x = layout.front_clock_width + layout.gap_width
    rear_x = scroll_x + layout.scroll_text_width
    frame = FrameBuffer()
    frame.fill(BACKGROUND)
    clock_text = now.strftime("%H:%M")
    front_width = text_width(clock_text, FONT_3X7)
    draw_text_clipped(
        frame, clock_text, font=FONT_3X7,
        origin_x=(layout.front_clock_width - front_width) // 2,
        origin_y=1, clip_x=0, clip_width=layout.front_clock_width,
        color=color_to_rgb(profile.clock_color)
    )
    marquee_width = text_width(profile.scrolling_text, FONT_5X7)
    cycle = layout.scroll_text_width + marquee_width + MARQUEE_TRAILING_GAP
    draw_text_clipped(
        frame, profile.scrolling_text, font=FONT_5X7,
        origin_x=scroll_x + layout.scroll_text_width - scroll_offset % cycle,
        origin_y=1, clip_x=scroll_x, clip_width=layout.scroll_text_width,
        color=color_to_rgb(profile.text_color)
    )
    rear_width = text_width(clock_text, FONT_5X7)
    draw_text_clipped(
        frame, clock_text, font=FONT_5X7,
        origin_x=rear_x + (layout.rear_clock_width - rear_width) // 2,
        origin_y=1, clip_x=rear_x, clip_width=layout.rear_clock_width,
        color=color_to_rgb(profile.clock_color)
    )
    return frame


def calculate_scroll_offset(elapsed_seconds: float, speed: float) -> int:
    """Calculate a whole-pixel offset from elapsed monotonic time."""
    if isinstance(elapsed_seconds, bool) or not isinstance(
        elapsed_seconds, (int, float)
    ):
        raise ValueError("elapsed_seconds must be a finite non-negative number.")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ValueError("speed must be a finite non-negative number.")
    try:
        normalized_elapsed = float(elapsed_seconds)
        normalized_speed = float(speed)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(
            "Scrolling inputs must be finite non-negative numbers."
        ) from error
    if not math.isfinite(normalized_elapsed) or normalized_elapsed < 0:
        raise ValueError("elapsed_seconds must be finite and non-negative.")
    if not math.isfinite(normalized_speed) or normalized_speed < 0:
        raise ValueError("speed must be finite and non-negative.")
    offset = normalized_elapsed * normalized_speed
    if not math.isfinite(offset):
        raise ValueError("Calculated scrolling offset must be finite.")
    return int(offset)


def profile_scroll_offset(profile: ProfileConfig, elapsed_seconds: float) -> int:
    """Calculate scrolling offset using the validated profile speed."""
    if not isinstance(profile, ProfileConfig):
        raise TypeError("profile must be a ProfileConfig.")
    return calculate_scroll_offset(elapsed_seconds, profile.scroll_speed)


def render_profile_at_elapsed(
    profile: ProfileConfig,
    now: datetime,
    elapsed_seconds: float,
) -> FrameBuffer:
    """Render a profile using its speed and elapsed monotonic time."""
    return render_profile_frame(
        profile,
        now,
        profile_scroll_offset(profile, elapsed_seconds),
    )
