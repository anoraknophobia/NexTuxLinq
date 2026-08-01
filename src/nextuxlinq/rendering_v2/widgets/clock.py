"""Pure formatting and inactive rendering for Clock widgets."""

from __future__ import annotations

import math
from datetime import datetime

from nextuxlinq.rendering_v2.fonts import (
    FontDefinition,
    FontFitAnalysis,
    FontFitRequirement,
    FontRegistry,
    FontTextError,
    analyze_font_fit,
    create_builtin_font_registry,
    draw_glyph,
    horizontal_origin,
    measure_normalized_text,
    resolve_analyzed_font,
    vertical_origin,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock
from nextuxlinq.widget_models import ClockFormat, ClockSettings, FontChoice

_CLOCK_CHARACTERS = "0123456789:"


def format_clock(
    value: datetime,
    clock_format: ClockFormat,
    *,
    show_seconds: bool,
) -> str:
    """Format one injected datetime without locale or system-time access."""
    if not isinstance(value, datetime):
        raise WidgetRenderingError("renderer_invariant", field="context")
    if not isinstance(clock_format, ClockFormat):
        raise WidgetRenderingError("renderer_invariant", field="format")
    if not isinstance(show_seconds, bool):
        raise WidgetRenderingError("renderer_invariant", field="settings")

    if clock_format is ClockFormat.HOUR_24:
        hour = f"{value.hour:02d}"
    else:
        twelve_hour = value.hour % 12 or 12
        hour = str(twelve_hour)
    result = f"{hour}:{value.minute:02d}"
    if show_seconds:
        result += f":{value.second:02d}"
    return result


def clock_separator_is_visible(
    animation_time: float, *, blink_separator: bool
) -> bool:
    """Return the exact half-second separator phase."""
    if isinstance(animation_time, bool) or not isinstance(
        animation_time, (int, float)
    ):
        raise WidgetRenderingError(
            "renderer_invariant", field="animation_time"
        )
    try:
        normalized = float(animation_time)
    except (OverflowError, TypeError, ValueError):
        conversion_failed = True
        normalized = 0.0
    else:
        conversion_failed = False
    if conversion_failed:
        raise WidgetRenderingError(
            "renderer_invariant", field="animation_time"
        )
    if not math.isfinite(normalized) or normalized < 0:
        raise WidgetRenderingError(
            "renderer_invariant", field="animation_time"
        )
    if not isinstance(blink_separator, bool):
        raise WidgetRenderingError("renderer_invariant", field="settings")
    return not blink_separator or normalized % 1.0 < 0.5


def _clock_minimum_width(font: FontDefinition, settings: ClockSettings) -> int:
    """Measure the widest reachable configured Clock representation."""
    hours = (
        tuple(f"{hour:02d}" for hour in range(24))
        if settings.format is ClockFormat.HOUR_24
        else tuple(str(hour) for hour in range(1, 13))
    )
    two_digit_values = tuple(f"{value:02d}" for value in range(60))
    hour_width = max(measure_normalized_text(font, value).width for value in hours)
    minute_width = max(
        measure_normalized_text(font, value).width for value in two_digit_values
    )
    separator_width = font.glyphs[":"].width
    width = hour_width + separator_width + minute_width
    width += font.character_spacing * 2
    if settings.show_seconds:
        width += separator_width + minute_width + font.character_spacing * 2
    return width


def _font_failure(error: FontTextError) -> tuple[str, str]:
    category = error.category
    if category not in {
        "unknown_font",
        "unsupported_glyph",
        "content_too_tall",
        "content_too_wide",
        "renderer_invariant",
    }:
        category = "renderer_invariant"
    field = "content" if category == "content_too_wide" else "font"
    return category, field


def analyze_clock_font_fit(
    registry: FontRegistry,
    settings: ClockSettings,
    *,
    region_width: int,
    region_height: int,
) -> FontFitAnalysis:
    """Analyze the stable widest configured Clock representation."""
    def requirement(font: FontDefinition) -> FontFitRequirement:
        coverage = all(
            character in font.glyphs for character in _CLOCK_CHARACTERS
        )
        return FontFitRequirement(
            compatible_with_widget=True,
            complete_glyph_coverage=coverage,
            minimum_width=(
                _clock_minimum_width(font, settings)
                if coverage else None
            ),
            minimum_height=font.glyph_height,
        )

    return analyze_font_fit(
        registry,
        settings.font,
        region_width=region_width,
        region_height=region_height,
        requirement_for=requirement,
    )


def resolve_clock_font(
    registry: FontRegistry,
    settings: ClockSettings,
    *,
    region_width: int,
    region_height: int,
) -> FontDefinition:
    failure: tuple[str, str] | None = None
    try:
        if settings.font is not FontChoice.AUTO:
            registry.resolve(settings.font)
        resolved = resolve_analyzed_font(
            registry,
            analyze_clock_font_fit(
                registry,
                settings,
                region_width=region_width,
                region_height=region_height,
            ),
        )
    except FontTextError as error:
        failure = _font_failure(error)
        resolved = None
    if failure is not None:
        category, field = failure
        raise WidgetRenderingError(category, field=field)
    assert resolved is not None
    return resolved


def _validate_inputs(
    surface: RegionSurface,
    settings: ClockSettings,
    context: RenderContext,
) -> None:
    if not isinstance(surface, RegionSurface):
        raise WidgetRenderingError("renderer_invariant", field="surface")
    if type(settings) is not ClockSettings:
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if type(context) is not RenderContext:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if (
        surface.width != context.region_width
        or surface.height != context.region_height
    ):
        raise WidgetRenderingError("renderer_invariant", field="surface")


def render_clock(
    surface: RegionSurface,
    settings: ClockSettings,
    context: RenderContext,
) -> None:
    """Render one intrinsic Clock into its supplied local surface."""
    font_failure: tuple[str, str] | None = None
    unexpected_failure = False
    try:
        _validate_inputs(surface, settings, context)
        value = resolve_wall_clock(context.wall_clock, settings.time_zone)
        text = format_clock(
            value, settings.format, show_seconds=settings.show_seconds
        )
        registry = create_builtin_font_registry()
        font = resolve_clock_font(
            registry,
            settings,
            region_width=surface.width,
            region_height=surface.height,
        )
        measurement = measure_normalized_text(font, text)
        origin_x = horizontal_origin(
            surface.width,
            measurement.width,
            settings.horizontal_alignment,
        )
        origin_y = vertical_origin(
            surface.height,
            measurement.height,
            settings.vertical_alignment,
        )
        color = RgbColor.from_schema_color(settings.color)
        separators_visible = clock_separator_is_visible(
            context.animation_time,
            blink_separator=settings.blink_separator,
        )

        cursor_x = origin_x
        for index, character in enumerate(text):
            if character != ":" or separators_visible:
                draw_glyph(
                    surface,
                    font.glyphs[character],
                    origin_x=cursor_x,
                    origin_y=origin_y,
                    color=color,
                )
            cursor_x += font.glyphs[character].width
            if index + 1 < len(text):
                cursor_x += font.character_spacing
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
