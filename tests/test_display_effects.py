"""Hardware-independent tests for the global foreground rainbow effect."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nextuxlinq.models_v2 import (
    CanvasConfig,
    DisplayEffectMode,
    ForegroundRainbowEffect,
    NoDisplayEffect,
    ProfileConfigV2,
    RainbowDirection,
    RegionConfig,
)
from nextuxlinq.rendering_v2.compositor import Compositor
from nextuxlinq.rendering_v2.effects import (
    apply_display_effect,
    rainbow_phase,
    rainbow_rgb,
)
from nextuxlinq.rendering_v2.registry import RenderContext, create_widget_registry
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.update_policy import UpdatePolicy
from nextuxlinq.rendering_v2.widgets.system_metric import render_metric_bar
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import MetricBarSettings, WidgetConfig, WidgetType

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _profile(
    regions: tuple[RegionConfig, ...] = (),
    *,
    effect=NoDisplayEffect(),
    background: str = "#102030",
) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(90, 10, background),
        regions=regions,
        effect=effect,
    )


def _region(
    region_id: str,
    x: int,
    width: int,
    widget_type: WidgetType,
    *,
    y: int = 0,
    height: int = 10,
    background: str | None = "#203040",
    **settings: object,
) -> RegionConfig:
    definition = create_widget_registry().resolve(widget_type)
    values = definition.default_settings.to_dict()
    values.update(settings)
    return RegionConfig(
        id=region_id,
        name=None,
        x=x,
        y=y,
        width=width,
        height=height,
        enabled=True,
        background_color=background,
        widget=WidgetConfig(
            widget_type,
            definition.settings_model.from_dict(values),
        ),
    )


def _render(profile: ProfileConfigV2, animation_time: float = 0.0, metrics=None):
    return Compositor(create_widget_registry()).render(
        profile,
        generation_time=NOW,
        animation_time=animation_time,
        metrics_snapshot=metrics,
    )


def _pixel(framebuffer: bytes, x: int, y: int) -> bytes:
    offset = (y * 90 + x) * 3
    return framebuffer[offset : offset + 3]


def _snapshot(cpu_usage: float = 50.0) -> SystemMetricsSnapshot:
    return SystemMetricsSnapshot(
        1.0,
        NOW,
        {MetricId.CPU_USAGE: cpu_usage},
        {
            metric: (
                MetricAvailability.AVAILABLE
                if metric is MetricId.CPU_USAGE
                else MetricAvailability.UNAVAILABLE
            )
            for metric in MetricId
        },
        "partial",
    )


def test_old_profile_defaults_to_immutable_none_and_round_trips() -> None:
    raw = {
        "brightness": None,
        "canvas": {"width": 90, "height": 10, "background_color": "#000000"},
        "regions": [],
    }
    profile = ProfileConfigV2.from_dict(raw)
    assert profile.effect == NoDisplayEffect()
    assert profile.effect.mode is DisplayEffectMode.NONE
    assert profile.to_dict()["effect"] == {"mode": "none"}
    assert ProfileConfigV2.from_dict(profile.to_dict()) == profile
    with pytest.raises(AttributeError):
        profile.effect.mode = DisplayEffectMode.FOREGROUND_RAINBOW  # type: ignore[misc]


@pytest.mark.parametrize(
    "effect",
    [
        {"mode": "unknown"},
        None,
        {"mode": "foreground_rainbow", "direction": "sideways", "speed": 0.1},
        {"mode": "foreground_rainbow", "direction": "rear_to_front", "speed": -0.01},
        {"mode": "foreground_rainbow", "direction": "rear_to_front", "speed": 2.01},
        {"mode": "foreground_rainbow", "direction": "rear_to_front", "speed": True},
        {"mode": "foreground_rainbow", "direction": "rear_to_front", "speed": float("nan")},
        {"mode": "foreground_rainbow", "direction": "rear_to_front", "speed": float("inf")},
    ],
)
def test_invalid_effect_settings_are_rejected(effect: object) -> None:
    raw = _profile().to_dict()
    raw["effect"] = effect
    with pytest.raises((TypeError, ValueError)):
        ProfileConfigV2.from_dict(raw)


def test_foreground_rainbow_serialization_is_exact() -> None:
    effect = ForegroundRainbowEffect(
        RainbowDirection.FRONT_TO_REAR, 0.25
    )
    assert effect.to_dict() == {
        "mode": "foreground_rainbow",
        "direction": "front_to_rear",
        "speed": 0.25,
    }
    assert ProfileConfigV2.from_dict(
        _profile(effect=effect).to_dict()
    ).effect == effect


def test_integer_color_wheel_is_deterministic_and_full_saturation() -> None:
    assert [rainbow_rgb(index / 6) for index in range(6)] == [
        0xFF0000,
        0xFFFF00,
        0x00FF00,
        0x00FFFF,
        0x0000FF,
        0xFF00FF,
    ]
    assert rainbow_rgb(0.0) == rainbow_rgb(1.0)
    assert rainbow_rgb(0.12345) == rainbow_rgb(0.12345)


def test_spatial_phase_covers_one_cycle_and_directions_move_oppositely() -> None:
    rear = ForegroundRainbowEffect(RainbowDirection.REAR_TO_FRONT, 0.1)
    front = ForegroundRainbowEffect(RainbowDirection.FRONT_TO_REAR, 0.1)
    assert rainbow_phase(0, 90, 0, rear) == 0
    assert rainbow_phase(89, 90, 0, rear) == pytest.approx(89 / 90)
    assert rainbow_phase(20, 90, 1, rear) < rainbow_phase(20, 90, 0, rear)
    assert rainbow_phase(20, 90, 1, front) > rainbow_phase(20, 90, 0, front)
    # A color at viewer x=20 moves right by nine pixels after one second.
    assert rainbow_rgb(rainbow_phase(29, 90, 1, rear)) == rainbow_rgb(
        rainbow_phase(20, 90, 0, rear)
    )
    assert rainbow_rgb(rainbow_phase(11, 90, 1, front)) == rainbow_rgb(
        rainbow_phase(20, 90, 0, front)
    )


def test_speed_zero_and_identical_context_are_static() -> None:
    source = bytes.fromhex("112233") * 90
    mask = bytes((1,)) * 90
    effect = ForegroundRainbowEffect(speed=0)
    first = apply_display_effect(
        source, mask, effect, animation_time=0, width=90, height=1
    )
    second = apply_display_effect(
        source, mask, effect, animation_time=99, width=90, height=1
    )
    assert first == second
    assert first == apply_display_effect(
        source, mask, effect, animation_time=0, width=90, height=1
    )


def test_effect_wheel_runs_at_most_once_per_eligible_column(monkeypatch) -> None:
    from nextuxlinq.rendering_v2 import effects

    calls = 0
    original = effects.rainbow_rgb

    def counted(phase: float) -> int:
        nonlocal calls
        calls += 1
        return original(phase)

    monkeypatch.setattr(effects, "rainbow_rgb", counted)
    effects.apply_display_effect(
        bytes(90 * 2 * 3),
        bytes((1, 0, 1, *([0] * 86), 1)) * 2,
        ForegroundRainbowEffect(speed=0),
        animation_time=0,
        width=90,
        height=2,
    )
    assert calls == 3


def test_surface_mask_is_immutable_clipped_and_tracks_overwrites() -> None:
    surface = RegionSurface(3, 2, RgbColor(1, 2, 3))
    assert surface.foreground_mask() == bytes(6)
    assert surface.set_pixel(1, 0, RgbColor(1, 2, 3)) is True
    assert surface.set_pixel(5, 0, RgbColor(9, 9, 9)) is False
    exported = surface.foreground_mask()
    assert exported == bytes((0, 1, 0, 0, 0, 0))
    surface.fill_rect(1, 0, 1, 1, RgbColor(1, 2, 3))
    assert exported == bytes((0, 1, 0, 0, 0, 0))
    assert surface.foreground_mask() == bytes(6)


def test_equal_foreground_and_background_colors_remain_semantically_distinct() -> None:
    color = RgbColor(17, 34, 51)
    surface = RegionSurface(2, 1, color)
    surface.set_pixel(0, 0, color)
    effected = apply_display_effect(
        surface.to_bytes(),
        surface.foreground_mask(),
        ForegroundRainbowEffect(speed=0),
        animation_time=0,
        width=2,
        height=1,
    )
    assert effected[:3] != color.to_bytes()
    assert effected[3:] == color.to_bytes()


def test_metric_bar_marks_fill_and_border_but_not_empty_area() -> None:
    render_context = RenderContext(
        wall_clock=NOW,
        animation_time=0,
        canvas_width=90,
        canvas_height=10,
        region_id="bar",
        region_width=4,
        region_height=3,
        metrics_snapshot=_snapshot(),
    )
    surface = RegionSurface(4, 3)
    render_metric_bar(surface, MetricBarSettings(border_enabled=True), render_context)
    mask = surface.foreground_mask()
    assert mask == bytes((1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1))


@pytest.mark.parametrize(
    ("widget_type", "settings", "metrics"),
    [
        (WidgetType.CLOCK, {"font": "3x5"}, None),
        (WidgetType.STATIC_TEXT, {"font": "3x5", "content": "A"}, None),
        (WidgetType.SCROLLING_TEXT, {"font": "3x5", "content": "A"}, None),
        (WidgetType.DATE, {"font": "3x5", "format": "day_month"}, None),
        (
            WidgetType.SYSTEM_METRIC,
            {"font": "3x5", "display_mode": "value_only"},
            _snapshot(),
        ),
    ],
)
def test_text_widget_glyphs_are_eligible_but_backgrounds_are_not(
    widget_type: WidgetType, settings: dict[str, object], metrics
) -> None:
    region = _region("target", 0, 40, widget_type, height=5, **settings)
    off = _render(
        _profile((region,)), animation_time=1, metrics=metrics
    ).logical_framebuffer
    on = _render(
        _profile((region,), effect=ForegroundRainbowEffect(speed=0)),
        animation_time=1,
        metrics=metrics,
    ).logical_framebuffer
    assert off != on
    assert any(
        off[offset : offset + 3] == bytes.fromhex("203040")
        and on[offset : offset + 3] == bytes.fromhex("203040")
        for offset in range(0, len(off), 3)
    )
    assert _pixel(on, 89, 9) == bytes.fromhex("102030")
    assert all(
        _pixel(on, x, y) == _pixel(off, x, y)
        for y in range(10)
        for x in range(90)
        if x >= 40 or y >= 5
    )


def test_blank_canvas_and_region_background_are_never_recolored() -> None:
    blank = _region("blank", 10, 10, WidgetType.BLANK)
    profile = _profile((blank,), effect=ForegroundRainbowEffect())
    result = _render(profile, animation_time=1)
    assert result.logical_framebuffer == (
        bytes.fromhex("102030") * 10
        + bytes.fromhex("203040") * 10
        + bytes.fromhex("102030") * 70
    ) * 10


def test_global_phase_does_not_restart_across_regions_or_unused_gaps() -> None:
    regions = (
        _region("one", 0, 2, WidgetType.METRIC_BAR, height=1, minimum=0, maximum=1),
        _region("two", 2, 3, WidgetType.METRIC_BAR, height=1, minimum=0, maximum=1),
        _region("three", 10, 1, WidgetType.METRIC_BAR, height=1, minimum=0, maximum=1),
    )
    result = _render(
        _profile(regions, effect=ForegroundRainbowEffect(speed=0)),
        metrics=_snapshot(cpu_usage=100),
    )
    for logical_x in (0, 1, 2, 3, 4, 10):
        expected = rainbow_rgb((89 - logical_x) / 90)
        assert _pixel(result.logical_framebuffer, logical_x, 0) == expected.to_bytes(3)
    assert _pixel(result.logical_framebuffer, 9, 0) == bytes.fromhex("102030")
    reordered = _render(
        _profile(tuple(reversed(regions)), effect=ForegroundRainbowEffect(speed=0)),
        metrics=_snapshot(cpu_usage=100),
    )
    assert reordered.logical_framebuffer == result.logical_framebuffer

    combined = _region(
        "combined", 0, 5, WidgetType.METRIC_BAR,
        height=1, minimum=0, maximum=1,
    )
    combined_result = _render(
        _profile((combined,), effect=ForegroundRainbowEffect(speed=0)),
        metrics=_snapshot(cpu_usage=100),
    )
    assert combined_result.logical_framebuffer[: 5 * 3] == (
        result.logical_framebuffer[: 5 * 3]
    )


def test_none_preserves_logical_and_device_framebuffers_exactly() -> None:
    region = _region("clock", 7, 30, WidgetType.CLOCK)
    implicit = ProfileConfigV2.from_dict({
        "brightness": None,
        "canvas": {"width": 90, "height": 10, "background_color": "#102030"},
        "regions": [region.to_dict()],
    })
    explicit = _profile((region,), effect=NoDisplayEffect())
    left = _render(implicit)
    right = _render(explicit)
    assert left.logical_framebuffer == right.logical_framebuffer
    assert left.device_framebuffer == right.device_framebuffer


def test_effected_pixels_follow_existing_device_orientation_exactly_once() -> None:
    bar = _region(
        "front-bar", 0, 5, WidgetType.METRIC_BAR,
        height=1, minimum=0, maximum=1,
    )
    result = _render(
        _profile((bar,), effect=ForegroundRainbowEffect(speed=0)),
        metrics=_snapshot(cpu_usage=100),
    )
    for offset in range(5):
        assert _pixel(result.device_framebuffer, 85 + offset, 0) == _pixel(
            result.logical_framebuffer, offset, 0
        )
    assert _pixel(result.device_framebuffer, 84, 0) == bytes.fromhex("102030")


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (NoDisplayEffect(), UpdatePolicy.MINUTE_BOUNDARY),
        (ForegroundRainbowEffect(speed=0), UpdatePolicy.MINUTE_BOUNDARY),
        (ForegroundRainbowEffect(speed=0.1), UpdatePolicy.CONTINUOUS),
    ],
)
def test_effect_policy_aggregates_with_widget_policy(effect, expected) -> None:
    result = _render(
        _profile((_region("clock", 0, 30, WidgetType.CLOCK),), effect=effect)
    )
    assert result.update_policy is expected
    assert result.animated_content is (expected is UpdatePolicy.CONTINUOUS)


def test_animated_rainbow_makes_blank_only_profile_continuous() -> None:
    result = _render(_profile(effect=ForegroundRainbowEffect(speed=0.1)))
    assert result.update_policy is UpdatePolicy.CONTINUOUS
    assert result.animated_content is True
    assert result.logical_framebuffer == bytes.fromhex("102030") * 900
