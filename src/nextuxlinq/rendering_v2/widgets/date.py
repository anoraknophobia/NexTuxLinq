"""Pure German formatting and inactive rendering for Date widgets."""

from __future__ import annotations

from datetime import datetime

from nextuxlinq.rendering_v2.fonts import (
    FontDefinition,
    FontRegistry,
    FontTextError,
    create_builtin_font_registry,
    draw_text,
    measure_normalized_text,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock
from nextuxlinq.widget_models import DateFormat, DateSettings, FontChoice

GERMAN_WEEKDAY_ABBREVIATIONS = (
    "MO.",
    "DI.",
    "MI.",
    "DO.",
    "FR.",
    "SA.",
    "SO.",
)

_AUTO_PRIORITY = (
    FontChoice.LEGACY_5X7,
    FontChoice.LEGACY_3X7,
    FontChoice.COMPACT_3X5,
)
_NUMERIC_DATE_CHARACTERS = frozenset("0123456789")


def format_date(value: datetime, date_format: DateFormat) -> str:
    """Format one date with fixed German labels and punctuation."""
    if not isinstance(value, datetime):
        raise WidgetRenderingError("renderer_invariant", field="context")
    if not isinstance(date_format, DateFormat):
        raise WidgetRenderingError(
            "date_format_unavailable", field="format"
        )
    day_month = f"{value.day:02d}.{value.month:02d}."
    if date_format is DateFormat.DAY_MONTH_FULL_YEAR:
        return f"{day_month}{value.year:04d}"
    if date_format is DateFormat.DAY_MONTH_SHORT_YEAR:
        return f"{day_month}{value.year % 100:02d}"
    if date_format is DateFormat.DAY_MONTH:
        return day_month
    if date_format is DateFormat.WEEKDAY_DAY_MONTH:
        return (
            f"{GERMAN_WEEKDAY_ABBREVIATIONS[value.weekday()]} "
            f"{day_month}"
        )
    raise WidgetRenderingError(
        "date_format_unavailable", field="format"
    )


def _date_candidates(date_format: DateFormat) -> tuple[str, ...]:
    if date_format is DateFormat.DAY_MONTH_FULL_YEAR:
        return ("88.88.8888",)
    if date_format is DateFormat.DAY_MONTH_SHORT_YEAR:
        return ("88.88.88",)
    if date_format is DateFormat.DAY_MONTH:
        return ("88.88.",)
    if date_format is DateFormat.WEEKDAY_DAY_MONTH:
        return tuple(
            f"{weekday} 88.88."
            for weekday in GERMAN_WEEKDAY_ABBREVIATIONS
        )
    raise WidgetRenderingError(
        "date_format_unavailable", field="format"
    )


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


def _font_fits(
    font: FontDefinition,
    *,
    candidates: tuple[str, ...],
    region_width: int,
    region_height: int,
    require_complete_width: bool,
) -> bool:
    required_characters = (
        _NUMERIC_DATE_CHARACTERS | frozenset("".join(candidates))
    )
    if not required_characters.issubset(font.supported_characters):
        raise WidgetRenderingError("unsupported_glyph", field="font")
    if font.glyph_height > region_height:
        raise WidgetRenderingError("content_too_tall", field="font")
    widest = max(
        measure_normalized_text(font, candidate).width
        for candidate in candidates
    )
    if require_complete_width and widest > region_width:
        raise WidgetRenderingError("content_too_wide", field="content")
    return True


def _resolve_date_font(
    registry: FontRegistry,
    settings: DateSettings,
    *,
    region_width: int,
    region_height: int,
) -> FontDefinition:
    candidates = _date_candidates(settings.format)
    require_width = settings.overflow.value == "error"
    if settings.font is not FontChoice.AUTO:
        failure: tuple[str, str] | None = None
        try:
            font = registry.resolve(settings.font)
        except FontTextError as error:
            failure = _font_failure(error)
            font = None
        if failure is not None:
            category, field = failure
            raise WidgetRenderingError(category, field=field)
        assert font is not None
        _font_fits(
            font,
            candidates=candidates,
            region_width=region_width,
            region_height=region_height,
            require_complete_width=require_width,
        )
        return font

    supported = False
    vertically_valid = False
    required_characters = (
        _NUMERIC_DATE_CHARACTERS | frozenset("".join(candidates))
    )
    for font_id in _AUTO_PRIORITY:
        try:
            font = registry.resolve(font_id)
        except FontTextError:
            continue
        if not required_characters.issubset(font.supported_characters):
            continue
        supported = True
        if font.glyph_height > region_height:
            continue
        vertically_valid = True
        if require_width:
            widest = max(
                measure_normalized_text(font, candidate).width
                for candidate in candidates
            )
            if widest > region_width:
                continue
        return font
    if vertically_valid and require_width:
        category = "content_too_wide"
        field = "content"
    elif supported:
        category = "content_too_tall"
        field = "font"
    else:
        category = "unsupported_glyph"
        field = "font"
    raise WidgetRenderingError(category, field=field)


def _validate_inputs(
    surface: RegionSurface,
    settings: DateSettings,
    context: RenderContext,
) -> None:
    if not isinstance(surface, RegionSurface):
        raise WidgetRenderingError("renderer_invariant", field="surface")
    if type(settings) is not DateSettings:
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if type(context) is not RenderContext:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if (
        surface.width != context.region_width
        or surface.height != context.region_height
    ):
        raise WidgetRenderingError("renderer_invariant", field="surface")


def render_date(
    surface: RegionSurface,
    settings: DateSettings,
    context: RenderContext,
) -> None:
    """Render one intrinsic German Date into its supplied local surface."""
    font_failure: tuple[str, str] | None = None
    unexpected_failure = False
    try:
        _validate_inputs(surface, settings, context)
        value = resolve_wall_clock(context.wall_clock, settings.time_zone)
        text = format_date(value, settings.format)
        registry = create_builtin_font_registry()
        font = _resolve_date_font(
            registry,
            settings,
            region_width=surface.width,
            region_height=surface.height,
        )
        color = RgbColor.from_schema_color(settings.color)
        draw_text(
            surface,
            text,
            font=font,
            color=color,
            horizontal_alignment=settings.horizontal_alignment,
            vertical_alignment=settings.vertical_alignment,
            overflow=settings.overflow,
        )
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
