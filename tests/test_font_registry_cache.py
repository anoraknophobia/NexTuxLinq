"""Tests for the shared immutable native bitmap-font registries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nextuxlinq.creator import FRAME_SIZE
from nextuxlinq.models_v2 import CanvasConfig, ProfileConfigV2, RegionConfig
from nextuxlinq.rendering_v2.compositor import Compositor
from nextuxlinq.rendering_v2.fonts import builtin as builtin_fonts
from nextuxlinq.rendering_v2.fonts.builtin import (
    AUTO_FONT_PRIORITY,
    _build_builtin_font_registry,
    create_builtin_font_registry,
)
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.rendering_v2.widgets import (
    clock as clock_widget_module,
    date as date_widget_module,
    font_fit,
    scrolling_text as scrolling_text_module,
    static_text as static_text_module,
    system_metric,
)
from nextuxlinq.rendering_v2.widgets.font_fit import analyze_widget_font_fit
from nextuxlinq.rendering_v2.widgets.system_metric import _metric_font_registry
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import (
    BuiltInIconSettings,
    ClockSettings,
    DateFormat,
    DateSettings,
    FontChoice,
    MetricDisplayMode,
    ScrollDirection,
    ScrollingTextSettings,
    StaticTextSettings,
    SystemMetricSettings,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 7, 29, 14, 30, 45, tzinfo=timezone.utc)

# Every widget module resolves the registry through its own module global, so a
# render-equivalence check has to replace all of them at once.
REGISTRY_MODULES = (
    clock_widget_module,
    date_widget_module,
    static_text_module,
    scrolling_text_module,
    system_metric,
    font_fit,
)


@pytest.fixture(autouse=True)
def _clear_registry_caches():
    """Keep every test independent of cache state left by earlier tests."""
    create_builtin_font_registry.cache_clear()
    _metric_font_registry.cache_clear()
    yield
    create_builtin_font_registry.cache_clear()
    _metric_font_registry.cache_clear()


def metrics_snapshot() -> SystemMetricsSnapshot:
    return SystemMetricsSnapshot(
        sample_monotonic=10.0,
        sample_time=NOW,
        values={MetricId.CPU_USAGE: 41.0},
        availability={
            metric_id: (
                MetricAvailability.AVAILABLE
                if metric_id is MetricId.CPU_USAGE
                else MetricAvailability.UNAVAILABLE
            )
            for metric_id in MetricId
        },
        source_status="partial",
    )


def region(
    region_id: str,
    *,
    x: int,
    width: int,
    settings: object,
    widget_type: WidgetType,
    y: int = 0,
    height: int = 10,
) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=None,
        x=x,
        y=y,
        width=width,
        height=height,
        enabled=True,
        background_color=None,
        widget=WidgetConfig(type=widget_type, settings=settings),
    )


def representative_profile() -> ProfileConfigV2:
    """One profile exercising Clock, Date, text, metric, and icon rendering."""
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            region(
                "clock",
                x=0,
                width=25,
                widget_type=WidgetType.CLOCK,
                settings=ClockSettings(
                    format="24h",  # type: ignore[arg-type]
                    show_seconds=False,
                    blink_separator=False,
                    time_zone="local",
                    color="#FFFFFF",
                    horizontal_alignment="center",  # type: ignore[arg-type]
                    vertical_alignment="center",  # type: ignore[arg-type]
                    font="auto",  # type: ignore[arg-type]
                ),
            ),
            region(
                "date",
                x=25,
                width=20,
                widget_type=WidgetType.DATE,
                settings=DateSettings(
                    format=DateFormat.DAY_MONTH,
                    time_zone="local",
                    color="#00FF00",
                    horizontal_alignment="center",  # type: ignore[arg-type]
                    vertical_alignment="center",  # type: ignore[arg-type]
                    font="auto",  # type: ignore[arg-type]
                ),
            ),
            region(
                "text",
                x=45,
                width=15,
                widget_type=WidgetType.STATIC_TEXT,
                settings=StaticTextSettings(
                    content="NEX",
                    uppercase=True,
                    color="#FF00FF",
                    horizontal_alignment="center",  # type: ignore[arg-type]
                    vertical_alignment="center",  # type: ignore[arg-type]
                    font="auto",  # type: ignore[arg-type]
                    overflow="clip",  # type: ignore[arg-type]
                ),
            ),
            region(
                "metric",
                x=60,
                width=15,
                widget_type=WidgetType.SYSTEM_METRIC,
                settings=SystemMetricSettings(
                    metric=MetricId.CPU_USAGE,
                    label="CPU",
                    display_mode=MetricDisplayMode.VALUE_ONLY,
                    precision=0,
                    color="#FFAA00",
                    horizontal_alignment="center",  # type: ignore[arg-type]
                    vertical_alignment="center",  # type: ignore[arg-type]
                    font="auto",  # type: ignore[arg-type]
                ),
            ),
            region(
                "icon",
                x=75,
                width=15,
                widget_type=WidgetType.BUILT_IN_ICON,
                settings=BuiltInIconSettings(),
            ),
        ),
    )


def render_representative() -> tuple[bytes, bytes]:
    compositor = Compositor(create_widget_registry())
    result = compositor.render(
        representative_profile(),
        generation_time=NOW,
        animation_time=0.0,
        metrics_snapshot=metrics_snapshot(),
    )
    return result.logical_framebuffer, result.device_framebuffer


def test_builtin_registry_is_constructed_once(monkeypatch) -> None:
    constructions = 0
    original = _build_builtin_font_registry

    def counting_builder():
        nonlocal constructions
        constructions += 1
        return original()

    monkeypatch.setattr(
        builtin_fonts, "_build_builtin_font_registry", counting_builder
    )
    create_builtin_font_registry.cache_clear()

    first = create_builtin_font_registry()
    for _ in range(24):
        create_builtin_font_registry()

    assert constructions == 1
    assert create_builtin_font_registry() is first


def test_repeated_access_reuses_the_identical_registry() -> None:
    first = create_builtin_font_registry()
    second = create_builtin_font_registry()

    assert first is second
    assert first.definitions is second.definitions
    for definition in first.definitions:
        assert first.resolve(definition.font_id) is second.resolve(
            definition.font_id
        )


def test_metric_registry_is_constructed_once() -> None:
    _metric_font_registry.cache_clear()

    first = _metric_font_registry()
    for _ in range(24):
        _metric_font_registry()

    assert _metric_font_registry.cache_info().misses == 1
    assert _metric_font_registry() is first


def test_rendering_constructs_each_registry_at_most_once(monkeypatch) -> None:
    constructions = 0
    original = _build_builtin_font_registry

    def counting_builder():
        nonlocal constructions
        constructions += 1
        return original()

    monkeypatch.setattr(
        builtin_fonts, "_build_builtin_font_registry", counting_builder
    )
    create_builtin_font_registry.cache_clear()
    _metric_font_registry.cache_clear()

    for _ in range(5):
        render_representative()

    assert constructions == 1
    assert _metric_font_registry.cache_info().misses == 1


def test_callers_cannot_mutate_shared_registry_data() -> None:
    registry = create_builtin_font_registry()
    definition = registry.resolve(FontChoice.NATIVE_5X10)

    with pytest.raises(TypeError):
        definition.glyphs["A"] = definition.glyphs["B"]  # type: ignore[index]
    with pytest.raises(TypeError):
        del definition.glyphs["A"]  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        definition.glyphs.clear()  # type: ignore[attr-defined]
    with pytest.raises(Exception):
        registry.definitions[0] = definition  # type: ignore[index]
    assert isinstance(definition.glyphs["A"].rows, tuple)
    assert all(isinstance(row, tuple) for row in definition.glyphs["A"].rows)

    # The shared registry is still the exact original after every attempt.
    assert create_builtin_font_registry() is registry
    assert registry.resolve(FontChoice.NATIVE_5X10) is definition


def test_metric_registry_does_not_mutate_the_shared_builtin_registry() -> None:
    shared = create_builtin_font_registry()
    builtin_percent_support = {
        definition.font_id: "%" in definition.glyphs
        for definition in shared.definitions
    }

    _metric_font_registry()

    assert create_builtin_font_registry() is shared
    assert {
        definition.font_id: "%" in definition.glyphs
        for definition in create_builtin_font_registry().definitions
    } == builtin_percent_support


def test_every_native_font_remains_byte_identical() -> None:
    cached = create_builtin_font_registry()
    fresh = _build_builtin_font_registry()

    assert [item.font_id for item in cached.definitions] == [
        item.font_id for item in fresh.definitions
    ]
    for cached_font, fresh_font in zip(cached.definitions, fresh.definitions):
        assert cached_font.display_label == fresh_font.display_label
        assert cached_font.character_spacing == fresh_font.character_spacing
        assert cached_font.glyph_height == fresh_font.glyph_height
        assert cached_font.supported_characters == fresh_font.supported_characters
        assert {
            character: glyph.to_strings()
            for character, glyph in cached_font.glyphs.items()
        } == {
            character: glyph.to_strings()
            for character, glyph in fresh_font.glyphs.items()
        }


def test_representative_render_is_byte_identical_to_uncached_construction(
    monkeypatch,
) -> None:
    cached_logical, cached_device = render_representative()
    assert len(cached_logical) == FRAME_SIZE
    assert len(cached_device) == FRAME_SIZE

    # Reproduce the previous behavior: build a fresh registry on every call.
    for module in REGISTRY_MODULES:
        monkeypatch.setattr(
            module,
            "create_builtin_font_registry",
            _build_builtin_font_registry,
        )
    monkeypatch.setattr(
        system_metric, "_metric_font_registry", _metric_font_registry.__wrapped__
    )

    uncached_logical, uncached_device = render_representative()

    assert cached_logical == uncached_logical
    assert cached_device == uncached_device


def test_auto_font_priority_and_fitting_are_unchanged(monkeypatch) -> None:
    settings = ClockSettings(
        format="24h",  # type: ignore[arg-type]
        show_seconds=False,
        blink_separator=False,
        time_zone="local",
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
    )

    assert AUTO_FONT_PRIORITY == (
        FontChoice.NATIVE_5X10,
        FontChoice.NATIVE_5X9,
        FontChoice.NATIVE_4X9,
        FontChoice.LEGACY_5X7,
        FontChoice.LEGACY_3X7,
        FontChoice.COMPACT_3X5,
    )

    cached = analyze_widget_font_fit(
        settings, region_width=25, region_height=10
    )
    monkeypatch.setattr(
        font_fit, "create_builtin_font_registry", _build_builtin_font_registry
    )
    fresh = analyze_widget_font_fit(
        settings, region_width=25, region_height=10
    )

    assert cached is not None
    assert cached.resolved_auto_font is FontChoice.NATIVE_5X10
    assert cached == fresh


def test_cachyos_icon_rendering_is_unaffected_by_font_caching() -> None:
    profile = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            region(
                "icon",
                x=0,
                width=15,
                widget_type=WidgetType.BUILT_IN_ICON,
                settings=BuiltInIconSettings(),
            ),
        ),
    )
    compositor = Compositor(create_widget_registry())

    first = compositor.render(
        profile, generation_time=NOW, animation_time=0.0
    ).device_framebuffer
    create_builtin_font_registry.cache_clear()
    _metric_font_registry.cache_clear()
    second = compositor.render(
        profile, generation_time=NOW, animation_time=0.0
    ).device_framebuffer

    assert first == second
    assert bytes((0x1D, 0xC7, 0xB5)) in first


def test_scrolling_text_render_is_stable_across_cache_state() -> None:
    profile = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=(
            region(
                "scroll",
                x=0,
                width=40,
                widget_type=WidgetType.SCROLLING_TEXT,
                settings=ScrollingTextSettings(
                    content="NEXTUXLINQ MATRIX900",
                    direction=ScrollDirection.LEFT,
                    speed=2.0,
                    color="#FFFFFF",
                    initial_alignment="start",  # type: ignore[arg-type]
                    vertical_alignment="center",  # type: ignore[arg-type]
                    font="auto",  # type: ignore[arg-type]
                    uppercase=True,
                    trailing_gap=4,
                ),
            ),
        ),
    )
    compositor = Compositor(create_widget_registry())

    first = compositor.render(
        profile, generation_time=NOW, animation_time=1.5
    ).device_framebuffer
    create_builtin_font_registry.cache_clear()
    second = compositor.render(
        profile, generation_time=NOW, animation_time=1.5
    ).device_framebuffer

    assert first == second
