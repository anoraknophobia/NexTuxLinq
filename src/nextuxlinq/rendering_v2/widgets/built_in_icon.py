"""Native, unscaled rendering for immutable built-in pixel icons."""

from __future__ import annotations

from nextuxlinq.rendering_v2.icons import resolve_built_in_icon
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.widget_models import (
    BuiltInIconSettings,
    HorizontalAlignment,
    VerticalAlignment,
)


def _aligned_origin(available: int, required: int, alignment: object) -> int:
    if alignment in {HorizontalAlignment.START, VerticalAlignment.TOP}:
        return 0
    if alignment in {HorizontalAlignment.END, VerticalAlignment.BOTTOM}:
        return available - required
    return (available - required) // 2


def render_built_in_icon(
    surface: RegionSurface,
    settings: BuiltInIconSettings,
    context: RenderContext,
) -> None:
    """Render one exact native bitmap with explicit effect eligibility."""
    if not isinstance(surface, RegionSurface):
        raise WidgetRenderingError("renderer_invariant", field="surface")
    if type(settings) is not BuiltInIconSettings:
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if type(context) is not RenderContext:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if surface.width != context.region_width or surface.height != context.region_height:
        raise WidgetRenderingError("renderer_invariant", field="surface")

    icon = resolve_built_in_icon(settings.icon)
    if surface.width < icon.width:
        raise WidgetRenderingError("content_too_wide", field="content")
    if surface.height < icon.height:
        raise WidgetRenderingError("content_too_tall", field="content")
    origin_x = _aligned_origin(
        surface.width, icon.width, settings.horizontal_alignment
    )
    origin_y = _aligned_origin(
        surface.height, icon.height, settings.vertical_alignment
    )
    color = RgbColor.from_schema_color(icon.fixed_color)
    for row_index, row in enumerate(icon.rows):
        for column_index, active in enumerate(row):
            if active == "1":
                surface.set_pixel(
                    origin_x + column_index,
                    origin_y + row_index,
                    color,
                    effect_eligible=icon.effect_eligible,
                )

