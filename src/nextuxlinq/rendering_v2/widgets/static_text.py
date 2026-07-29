"""Deterministic inactive rendering for Static Text widgets."""

from __future__ import annotations

from nextuxlinq.rendering_v2.fonts import (
    FontTextError,
    create_builtin_font_registry,
    draw_text,
    resolve_font,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.widget_models import StaticTextSettings, TextOverflow


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


def _validate_inputs(
    surface: RegionSurface,
    settings: StaticTextSettings,
    context: RenderContext,
) -> None:
    if not isinstance(surface, RegionSurface):
        raise WidgetRenderingError("renderer_invariant", field="surface")
    if type(settings) is not StaticTextSettings:
        raise WidgetRenderingError("renderer_invariant", field="settings")
    if type(context) is not RenderContext:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if (
        surface.width != context.region_width
        or surface.height != context.region_height
    ):
        raise WidgetRenderingError("renderer_invariant", field="surface")


def render_static_text(
    surface: RegionSurface,
    settings: StaticTextSettings,
    context: RenderContext,
) -> None:
    """Render intrinsic, optionally ASCII-uppercased static text."""
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
            require_complete_width=(
                settings.overflow is TextOverflow.ERROR
            ),
        )
        color = RgbColor.from_schema_color(settings.color)
        draw_text(
            surface,
            resolved.normalized_text,
            font=resolved.font,
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
