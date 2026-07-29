"""Inactive widget renderers for future schema-v2 layouts.

Importing this package does not construct a font or widget registry and does
not connect the renderers to the production compositor registry.
"""

from nextuxlinq.rendering_v2.widgets.clock import (
    clock_separator_is_visible,
    format_clock,
    render_clock,
)
from nextuxlinq.rendering_v2.widgets.date import (
    GERMAN_WEEKDAY_ABBREVIATIONS,
    format_date,
    render_date,
)
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.scrolling_text import (
    render_scrolling_text,
    scrolling_pixel_phase,
    scrolling_text_origin,
)
from nextuxlinq.rendering_v2.widgets.static_text import render_static_text
from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock

__all__ = (
    "GERMAN_WEEKDAY_ABBREVIATIONS",
    "WidgetRenderingError",
    "clock_separator_is_visible",
    "format_clock",
    "format_date",
    "render_clock",
    "render_date",
    "render_scrolling_text",
    "render_static_text",
    "resolve_wall_clock",
    "scrolling_pixel_phase",
    "scrolling_text_origin",
)
