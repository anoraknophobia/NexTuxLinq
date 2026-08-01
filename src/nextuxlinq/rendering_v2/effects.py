"""Deterministic profile-level effects for complete logical framebuffers."""

from __future__ import annotations

import math

from nextuxlinq.creator import RGB_CHANNELS
from nextuxlinq.models_v2 import (
    DisplayEffect,
    ForegroundRainbowEffect,
    NoDisplayEffect,
    RainbowDirection,
)

_WHEEL_STEPS = 6 * 256


class EffectInvariantError(RuntimeError):
    """Effect inputs violate a renderer-owned invariant."""


def rainbow_rgb(phase: float) -> int:
    """Return a packed RGB888 color from the integer six-segment wheel."""
    if isinstance(phase, bool) or not isinstance(phase, (int, float)):
        raise EffectInvariantError("Rainbow phase is invalid.")
    normalized = float(phase)
    if not math.isfinite(normalized):
        raise EffectInvariantError("Rainbow phase is invalid.")
    step = int(math.floor((normalized % 1.0) * _WHEEL_STEPS)) % _WHEEL_STEPS
    segment, offset = divmod(step, 256)
    inverse = 255 - offset
    if segment == 0:
        red, green, blue = 255, offset, 0
    elif segment == 1:
        red, green, blue = inverse, 255, 0
    elif segment == 2:
        red, green, blue = 0, 255, offset
    elif segment == 3:
        red, green, blue = 0, inverse, 255
    elif segment == 4:
        red, green, blue = offset, 0, 255
    else:
        red, green, blue = 255, 0, inverse
    return (red << 16) | (green << 8) | blue


def rainbow_phase(
    viewer_x: int,
    canvas_width: int,
    animation_time: float,
    effect: ForegroundRainbowEffect,
) -> float:
    """Return the global phase whose visible motion matches the direction."""
    if (
        isinstance(viewer_x, bool)
        or not isinstance(viewer_x, int)
        or isinstance(canvas_width, bool)
        or not isinstance(canvas_width, int)
        or canvas_width <= 0
        or not 0 <= viewer_x < canvas_width
        or isinstance(animation_time, bool)
        or not isinstance(animation_time, (int, float))
        or not math.isfinite(float(animation_time))
        or float(animation_time) < 0
        or not isinstance(effect, ForegroundRainbowEffect)
    ):
        raise EffectInvariantError("Rainbow coordinates are invalid.")
    temporal = float(animation_time) * effect.speed
    spatial = viewer_x / canvas_width
    # phase(x, t) = x - t travels toward increasing viewer x (Rear -> Front).
    return (
        spatial - temporal
        if effect.direction is RainbowDirection.REAR_TO_FRONT
        else spatial + temporal
    )


def apply_display_effect(
    framebuffer: bytes,
    foreground_mask: bytes,
    effect: DisplayEffect,
    *,
    animation_time: float,
    width: int,
    height: int,
) -> bytes:
    """Apply one complete-width effect and return a new immutable framebuffer."""
    if not isinstance(framebuffer, bytes) or not isinstance(foreground_mask, bytes):
        raise EffectInvariantError("Effect buffers are invalid.")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise EffectInvariantError("Effect buffers are invalid.")
    pixel_count = width * height
    if (
        len(framebuffer) != pixel_count * RGB_CHANNELS
        or len(foreground_mask) != pixel_count
        or any(value not in (0, 1) for value in foreground_mask)
    ):
        raise EffectInvariantError("Effect buffers are invalid.")
    if isinstance(effect, NoDisplayEffect):
        return framebuffer
    if not isinstance(effect, ForegroundRainbowEffect):
        raise EffectInvariantError("Display effect is invalid.")

    output = bytearray(framebuffer)
    column_colors = [-1] * width
    for logical_y in range(height):
        row_offset = logical_y * width
        for logical_x in range(width):
            pixel_index = row_offset + logical_x
            if not foreground_mask[pixel_index]:
                continue
            packed = column_colors[logical_x]
            if packed < 0:
                viewer_x = width - 1 - logical_x
                packed = rainbow_rgb(
                    rainbow_phase(viewer_x, width, animation_time, effect)
                )
                column_colors[logical_x] = packed
            offset = pixel_index * RGB_CHANNELS
            output[offset] = packed >> 16
            output[offset + 1] = (packed >> 8) & 0xFF
            output[offset + 2] = packed & 0xFF
    return bytes(output)
