"""Deterministic inactive rendering for Scrolling Text widgets."""

from __future__ import annotations

import math

from nextuxlinq.rendering_v2.fonts import (
    FontTextError,
    create_builtin_font_registry,
    draw_glyph,
    resolve_font,
    vertical_origin,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.widget_models import (
    HorizontalAlignment,
    ScrollDirection,
    ScrollingTextSettings,
)


def _font_failure(error: FontTextError) -> tuple[str, str]:
    mapping = {
        "unknown_font": ("unknown_font", "font"),
        "unsupported_glyph": ("unsupported_glyph", "content"),
        "content_too_tall": ("content_too_tall", "font"),
        "content_too_wide": ("content_too_wide", "content"),
        "renderer_invariant": ("renderer_invariant", "renderer"),
    }
    return mapping.get(
        error.category, ("renderer_invariant", "renderer")
    )


def scrolling_pixel_phase(animation_time: float, speed: float) -> int:
    """Return ``floor(animation_time * speed)`` as a finite integer."""
    if (
        isinstance(animation_time, bool)
        or not isinstance(animation_time, (int, float))
        or isinstance(speed, bool)
        or not isinstance(speed, (int, float))
    ):
        raise WidgetRenderingError(
            "renderer_invariant", field="animation_time"
        )
    try:
        product = float(animation_time) * float(speed)
    except (OverflowError, TypeError, ValueError):
        invalid = True
        product = 0.0
    else:
        invalid = not math.isfinite(product) or product < 0
    if invalid:
        raise WidgetRenderingError(
            "renderer_invariant", field="animation_time"
        )
    return math.floor(product)


def scrolling_text_origin(
    *,
    region_width: int,
    text_width: int,
    trailing_gap: int,
    initial_alignment: HorizontalAlignment,
    direction: ScrollDirection,
    pixel_phase: int,
) -> int:
    """Return one integer local origin for a scrolling text cycle."""
    values = (region_width, text_width, trailing_gap, pixel_phase)
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in values
    ):
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if (
        region_width <= 0
        or text_width <= 0
        or trailing_gap < 0
        or pixel_phase < 0
    ):
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if not isinstance(initial_alignment, HorizontalAlignment):
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if not isinstance(direction, ScrollDirection):
        raise WidgetRenderingError("renderer_invariant", field="settings")

    cycle = region_width + text_width + trailing_gap
    phase = pixel_phase % cycle
    if initial_alignment is HorizontalAlignment.START:
        base_origin = -text_width
    elif initial_alignment is HorizontalAlignment.CENTER:
        base_origin = (region_width - text_width) // 2
    else:
        base_origin = region_width
    if phase == 0:
        return base_origin
    if direction is ScrollDirection.LEFT:
        minimum_origin = -text_width - trailing_gap + 1
        return (
            (base_origin - phase - minimum_origin) % cycle
        ) + minimum_origin
    minimum_origin = -text_width
    return ((base_origin + phase - minimum_origin) % cycle) + minimum_origin


def _validate_inputs(
    surface: RegionSurface,
    settings: ScrollingTextSettings,
    context: RenderContext,
) -> None:
    if not isinstance(surface, RegionSurface):
        raise WidgetRenderingError("renderer_invariant", field="surface")
    if type(settings) is not ScrollingTextSettings:
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if type(context) is not RenderContext:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if (
        surface.width != context.region_width
        or surface.height != context.region_height
    ):
        raise WidgetRenderingError("renderer_invariant", field="surface")


def render_scrolling_text(
    surface: RegionSurface,
    settings: ScrollingTextSettings,
    context: RenderContext,
) -> None:
    """Render one intrinsic scrolling string clipped to its local surface."""
    font_failure: tuple[str, str] | None = None
    unexpected_failure = False
    try:
        _validate_inputs(surface, settings, context)
        resolved = resolve_font(
            create_builtin_font_registry(),
            settings.font,
            settings.content,
            uppercase=settings.uppercase,
            region_width=surface.width,
            region_height=surface.height,
            require_complete_width=False,
        )
        phase = scrolling_pixel_phase(
            context.animation_time, settings.speed
        )
        origin_x = scrolling_text_origin(
            region_width=surface.width,
            text_width=resolved.measurement.width,
            trailing_gap=settings.trailing_gap,
            initial_alignment=settings.initial_alignment,
            direction=settings.direction,
            pixel_phase=phase,
        )
        origin_y = vertical_origin(
            surface.height,
            resolved.measurement.height,
            settings.vertical_alignment,
        )
        color = RgbColor.from_schema_color(settings.color)

        cursor_x = origin_x
        for index, character in enumerate(resolved.normalized_text):
            draw_glyph(
                surface,
                resolved.font.glyphs[character],
                origin_x=cursor_x,
                origin_y=origin_y,
                color=color,
            )
            cursor_x += resolved.font.glyphs[character].width
            if index + 1 < len(resolved.normalized_text):
                cursor_x += resolved.font.character_spacing
        return None
    except WidgetRenderingError:
        raise
    except FontTextError as error:
        font_failure = _font_failure(error)
    except Exception:
        unexpected_failure = True

    if font_failure is not None:
        category, field = font_failure
        raise WidgetRenderingError(category, field=field)
    if unexpected_failure:
        raise WidgetRenderingError(
            "unexpected_renderer_failure", field="renderer"
        )
    raise WidgetRenderingError("renderer_invariant", field="renderer")
