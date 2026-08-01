"""Deterministic rendering for System Metric and Metric Bar widgets."""

from __future__ import annotations

import math

from nextuxlinq.rendering_v2.fonts import (
    FontDefinition,
    FontRegistry,
    FontTextError,
    GlyphBitmap,
    create_builtin_font_registry,
    draw_text,
    resolve_font,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.system_metrics import format_metric_value
from nextuxlinq.widget_models import (
    BarDirection,
    BarOrientation,
    MetricBarSettings,
    MetricDisplayMode,
    SystemMetricSettings,
    TextOverflow,
)


def _font_failure(error: FontTextError) -> tuple[str, str]:
    mapping = {
        "unknown_font": ("unknown_font", "font"),
        "unsupported_glyph": ("unsupported_glyph", "content"),
        "content_too_tall": ("content_too_tall", "font"),
        "content_too_wide": ("content_too_wide", "content"),
        "renderer_invariant": ("renderer_invariant", "renderer"),
    }
    return mapping.get(error.category, ("renderer_invariant", "renderer"))


_PERCENT_ROWS = {
    5: ("101", "001", "010", "100", "101"),
    7: ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
}


def _metric_font_registry() -> FontRegistry:
    """Extend fresh built-ins with the canonical ASCII percent suffix only."""
    definitions = []
    for definition in create_builtin_font_registry().definitions:
        glyphs = dict(definition.glyphs)
        rows = _PERCENT_ROWS[definition.glyph_height]
        if definition.glyph_height == 7 and max(
            glyph.width for glyph in definition.glyphs.values()
        ) <= 3:
            rows = ("101", "001", "010", "010", "010", "100", "101")
        glyphs["%"] = GlyphBitmap.from_strings(rows)
        definitions.append(FontDefinition(
            font_id=definition.font_id,
            display_label=definition.display_label,
            glyphs=glyphs,
            character_spacing=definition.character_spacing,
        ))
    return FontRegistry(definitions)


def metric_text(settings: SystemMetricSettings, context: RenderContext) -> str:
    """Return the exact safe display text for one snapshot metric."""
    snapshot = context.metrics_snapshot
    value = None if snapshot is None else snapshot.value(settings.metric)
    rendered = (
        settings.unavailable_text
        if value is None
        else format_metric_value(settings.metric, value, settings.precision)
    )
    if settings.display_mode is MetricDisplayMode.LABEL_AND_VALUE:
        return f"{settings.label} {rendered}"
    return rendered


def render_system_metric(
    surface: RegionSurface,
    settings: SystemMetricSettings,
    context: RenderContext,
) -> None:
    """Render one intrinsic ASCII metric label and value."""
    font_failure: tuple[str, str] | None = None
    unexpected = False
    try:
        if (
            not isinstance(surface, RegionSurface)
            or type(settings) is not SystemMetricSettings
            or type(context) is not RenderContext
            or surface.width != context.region_width
            or surface.height != context.region_height
        ):
            raise WidgetRenderingError("renderer_invariant", field="context")
        content = metric_text(settings, context)
        resolved = resolve_font(
            _metric_font_registry(),
            settings.font,
            content,
            uppercase=False,
            region_width=surface.width,
            region_height=surface.height,
            require_complete_width=settings.overflow is TextOverflow.ERROR,
        )
        draw_text(
            surface,
            content,
            font=resolved.font,
            color=RgbColor.from_schema_color(settings.color),
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
        unexpected = True
    if font_failure is not None:
        category, field = font_failure
        raise WidgetRenderingError(category, field=field)
    if unexpected:
        raise WidgetRenderingError("unexpected_renderer_failure", field="renderer")
    raise WidgetRenderingError("renderer_invariant", field="renderer")


def metric_bar_fill_length(value: float | None, settings: MetricBarSettings, extent: int) -> int:
    """Return nearest-integer fill pixels with halves rounded upward."""
    if value is None:
        return 0
    ratio = (value - settings.minimum) / (settings.maximum - settings.minimum)
    clamped = min(1.0, max(0.0, ratio))
    return min(extent, max(0, int(math.floor(clamped * extent + 0.5))))


def render_metric_bar(
    surface: RegionSurface,
    settings: MetricBarSettings,
    context: RenderContext,
) -> None:
    """Draw one opaque region-local bar without fractional pixels."""
    unexpected = False
    try:
        if (
            not isinstance(surface, RegionSurface)
            or type(settings) is not MetricBarSettings
            or type(context) is not RenderContext
            or surface.width != context.region_width
            or surface.height != context.region_height
        ):
            raise WidgetRenderingError("renderer_invariant", field="context")
        empty = RgbColor.from_schema_color(settings.empty_color)
        fill = RgbColor.from_schema_color(settings.fill_color)
        border = RgbColor.from_schema_color(settings.border_color)
        surface.fill(empty)
        inset = 1 if settings.border_enabled and surface.width > 2 and surface.height > 2 else 0
        if inset:
            surface.fill(border)
            surface.fill_rect(1, 1, surface.width - 2, surface.height - 2, empty)
        inner_width = surface.width - inset * 2
        inner_height = surface.height - inset * 2
        snapshot = context.metrics_snapshot
        value = None if snapshot is None else snapshot.value(settings.metric)
        extent = inner_width if settings.orientation is BarOrientation.HORIZONTAL else inner_height
        length = metric_bar_fill_length(value, settings, extent)
        if settings.orientation is BarOrientation.HORIZONTAL:
            x = inset if settings.direction is BarDirection.START_TO_END else inset + inner_width - length
            surface.fill_rect(x, inset, length, inner_height, fill)
        else:
            # Vertical start-to-end is bottom-to-top; end-to-start is top-to-bottom.
            y = inset + inner_height - length if settings.direction is BarDirection.START_TO_END else inset
            surface.fill_rect(inset, y, inner_width, length, fill)
        return None
    except WidgetRenderingError:
        raise
    except Exception:
        unexpected = True
    if unexpected:
        raise WidgetRenderingError("unexpected_renderer_failure", field="renderer")
    raise WidgetRenderingError("renderer_invariant", field="renderer")
