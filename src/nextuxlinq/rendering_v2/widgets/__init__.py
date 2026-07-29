"""Inactive Clock and Date renderers for future schema-v2 layouts.

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
from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock

__all__ = (
    "GERMAN_WEEKDAY_ABBREVIATIONS",
    "WidgetRenderingError",
    "clock_separator_is_visible",
    "format_clock",
    "format_date",
    "render_clock",
    "render_date",
    "resolve_wall_clock",
)
