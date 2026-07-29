"""Pure update-policy boundary calculations for the active renderer."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

from nextuxlinq.models_v2 import ProfileConfigV2
from nextuxlinq.rendering_v2.update_policy import UpdatePolicy
from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock
from nextuxlinq.widget_models import ClockSettings, DateSettings, WidgetType


def continuous_delay(
    monotonic_now: float,
    epoch: float,
    frame_rate: float,
) -> float:
    """Return the next cadence boundary without accumulating drift."""
    if not all(math.isfinite(value) for value in (monotonic_now, epoch)):
        raise ValueError("monotonic values must be finite.")
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        raise ValueError("frame_rate must be positive and finite.")
    interval = 1.0 / frame_rate
    elapsed = max(0.0, monotonic_now - epoch)
    boundary = (math.floor(elapsed / interval) + 1) * interval
    return max(0.0, epoch + boundary - monotonic_now)


def _subsecond_boundary(wall_clock: datetime, frequency: int) -> float:
    fraction = wall_clock.microsecond / 1_000_000
    phase = math.floor(fraction * frequency)
    return ((phase + 1) / frequency) - fraction


def _day_boundary_delay(
    wall_clock: datetime,
    profile: ProfileConfigV2,
) -> float:
    delays: list[float] = []
    for region in profile.regions:
        if not region.enabled or region.widget.type is not WidgetType.DATE:
            continue
        settings = region.widget.settings
        if type(settings) is not DateSettings:
            continue
        resolved = resolve_wall_clock(wall_clock, settings.time_zone)
        tomorrow = resolved.date() + timedelta(days=1)
        boundary = datetime.combine(
            tomorrow,
            time.min,
            tzinfo=resolved.tzinfo,
        )
        delay = (
            boundary.astimezone(timezone.utc)
            - wall_clock.astimezone(timezone.utc)
        ).total_seconds()
        if delay > 0:
            delays.append(delay)
    if not delays:
        raise ValueError("day-boundary policy requires an enabled Date.")
    return min(delays)


def _minute_boundary_delay(
    wall_clock: datetime,
    profile: ProfileConfigV2,
) -> float:
    delays: list[float] = []
    for region in profile.regions:
        if not region.enabled or region.widget.type is not WidgetType.CLOCK:
            continue
        settings = region.widget.settings
        if type(settings) is not ClockSettings:
            continue
        resolved = resolve_wall_clock(wall_clock, settings.time_zone)
        delays.append(
            60
            - resolved.second
            - resolved.microsecond / 1_000_000
        )
    if not delays:
        raise ValueError("minute-boundary policy requires an enabled Clock.")
    return min(delays)


def update_policy_delay(
    policy: UpdatePolicy,
    *,
    wall_clock: datetime,
    monotonic_now: float,
    continuous_epoch: float,
    frame_rate: float,
    profile: ProfileConfigV2,
) -> float | None:
    """Return the next drift-free delay, or None for static content."""
    if type(policy) is not UpdatePolicy:
        raise TypeError("policy must be an UpdatePolicy.")
    if (
        not isinstance(wall_clock, datetime)
        or wall_clock.tzinfo is None
        or wall_clock.utcoffset() is None
    ):
        raise ValueError("wall_clock must be timezone-aware.")
    if policy is UpdatePolicy.STATIC:
        return None
    if policy is UpdatePolicy.CONTINUOUS:
        return continuous_delay(
            monotonic_now,
            continuous_epoch,
            frame_rate,
        )
    if policy is UpdatePolicy.HALF_SECOND:
        return continuous_delay(
            monotonic_now,
            continuous_epoch,
            2.0,
        )
    if policy is UpdatePolicy.SECOND_BOUNDARY:
        return _subsecond_boundary(wall_clock, 1)
    if policy is UpdatePolicy.MINUTE_BOUNDARY:
        return _minute_boundary_delay(wall_clock, profile)
    if policy is UpdatePolicy.DAY_BOUNDARY:
        return _day_boundary_delay(wall_clock, profile)
    raise AssertionError("Unhandled update policy.")
