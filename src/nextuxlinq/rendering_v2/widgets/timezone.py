"""Deterministic time-zone conversion for injected rendering time."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError


def resolve_wall_clock(wall_clock: datetime, time_zone: str) -> datetime:
    """Return the injected aware datetime in local or one named zone."""
    if not isinstance(wall_clock, datetime):
        raise WidgetRenderingError("renderer_invariant", field="context")
    awareness_failed = False
    try:
        aware = (
            wall_clock.tzinfo is not None
            and wall_clock.utcoffset() is not None
        )
    except (OverflowError, ValueError):
        awareness_failed = True
        aware = False
    if awareness_failed:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if not aware:
        raise WidgetRenderingError("renderer_invariant", field="context")
    if not isinstance(time_zone, str) or not time_zone:
        raise WidgetRenderingError(
            "time_zone_unavailable", field="time_zone"
        )
    if time_zone == "local":
        return wall_clock
    conversion_failed = False
    try:
        resolved = wall_clock.astimezone(ZoneInfo(time_zone))
    except Exception:
        conversion_failed = True
        resolved = wall_clock
    if conversion_failed:
        raise WidgetRenderingError(
            "time_zone_unavailable", field="time_zone"
        )
    return resolved
