"""Sanitized controlled failures for inactive widget renderers."""

from __future__ import annotations

from nextuxlinq.rendering_v2.registry import WidgetRenderError

_MESSAGES = {
    "unknown_font": "The requested font is unavailable.",
    "unsupported_glyph": "The requested font lacks a required glyph.",
    "content_too_tall": "The widget content is too tall.",
    "content_too_wide": "The widget content is too wide.",
    "time_zone_unavailable": "The requested time zone is unavailable.",
    "date_format_unavailable": "The requested date format is unavailable.",
    "renderer_invariant": "The widget renderer received invalid state.",
    "unexpected_renderer_failure": "The widget renderer failed unexpectedly.",
}

_FIELDS = frozenset(
    {
        "animation_time",
        "color",
        "content",
        "context",
        "font",
        "format",
        "renderer",
        "settings",
        "surface",
        "time_zone",
    }
)


class WidgetRenderingError(WidgetRenderError):
    """One stable category and field without host-specific details."""

    def __init__(self, category: str, *, field: str) -> None:
        if category not in _MESSAGES:
            category = "renderer_invariant"
        if field not in _FIELDS:
            field = "renderer"
        self.category = category
        self.field = field
        super().__init__(_MESSAGES[category])
