"""Hardware-independent tests for immutable native built-in icons."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from nextuxlinq.models_v2 import (
    CanvasConfig,
    ForegroundRainbowEffect,
    ProfileConfigV2,
    RegionConfig,
)
from nextuxlinq.profile_presets import get_builtin_presets
from nextuxlinq.rendering_v2.compositor import Compositor
from nextuxlinq.rendering_v2.icons import (
    BUILT_IN_ICON_CATALOG,
    CACHYOS_15X10_BITMAP,
)
from nextuxlinq.rendering_v2.registry import RenderContext, create_widget_registry
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.built_in_icon import render_built_in_icon
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.widget_models import (
    BuiltInIconSettings,
    StaticTextSettings,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
TURQUOISE = bytes.fromhex("1DC7B5")
BACKGROUND = bytes.fromhex("102030")


def context(width: int, height: int) -> RenderContext:
    return RenderContext(
        wall_clock=NOW,
        animation_time=0.0,
        canvas_width=90,
        canvas_height=10,
        region_id="icon",
        region_width=width,
        region_height=height,
    )


def icon_widget(**changes: object) -> WidgetConfig:
    values = BuiltInIconSettings().to_dict()
    values.update(changes)
    return WidgetConfig.from_dict({"type": "built_in_icon", "settings": values})


def icon_region(*, x: int = 0, width: int = 15, height: int = 10) -> RegionConfig:
    return RegionConfig(
        id="icon",
        name="CachyOS Logo",
        x=x,
        y=0,
        width=width,
        height=height,
        enabled=True,
        background_color="#102030",
        widget=icon_widget(),
    )


def test_cachyos_catalog_and_bitmap_are_exact_immutable_native_fixtures() -> None:
    assert isinstance(BUILT_IN_ICON_CATALOG, MappingProxyType)
    definition = BUILT_IN_ICON_CATALOG[next(iter(BUILT_IN_ICON_CATALOG))]
    assert (definition.width, definition.height) == (15, 10)
    assert definition.rows is CACHYOS_15X10_BITMAP
    assert CACHYOS_15X10_BITMAP == (
        "000111111111000",
        "001110000011100",
        "011100000001110",
        "111000111000111",
        "110001111100011",
        "110001100000000",
        "111000111100000",
        "011100001110110",
        "001110000011100",
        "000111111111000",
    )
    assert definition.fixed_color == "#1DC7B5"
    assert definition.fixed_color_label == "CachyOS Turquoise"
    assert definition.effect_eligible is False


def test_renderer_uses_only_turquoise_and_preserves_transparent_background() -> None:
    surface = RegionSurface(15, 10, RgbColor.from_schema_color("#102030"))
    render_built_in_icon(surface, BuiltInIconSettings(), context(15, 10))
    payload = surface.to_bytes()
    mask = surface.foreground_mask()
    for y, row in enumerate(CACHYOS_15X10_BITMAP):
        for x, active in enumerate(row):
            offset = (y * 15 + x) * 3
            assert payload[offset : offset + 3] == (
                TURQUOISE if active == "1" else BACKGROUND
            )
    assert mask == bytes(15 * 10)


@pytest.mark.parametrize(
    ("alignment", "expected_origin"),
    [("start", 0), ("center", 2), ("end", 5)],
)
def test_larger_region_aligns_without_scaling(alignment: str, expected_origin: int) -> None:
    surface = RegionSurface(20, 10, RgbColor.from_schema_color("#102030"))
    settings = BuiltInIconSettings(horizontal_alignment=alignment)  # type: ignore[arg-type]
    render_built_in_icon(surface, settings, context(20, 10))
    active = {
        (index % 20, index // 20)
        for index, marked in enumerate(
            pixel == TURQUOISE
            for pixel in (
                surface.to_bytes()[offset : offset + 3]
                for offset in range(0, len(surface.to_bytes()), 3)
            )
        )
        if marked
    }
    assert min(x for x, _ in active) == expected_origin
    assert max(x for x, _ in active) < expected_origin + 15


@pytest.mark.parametrize(
    ("width", "height", "category"),
    [(14, 10, "content_too_wide"), (15, 9, "content_too_tall")],
)
def test_too_small_icon_has_authoritative_intrinsic_geometry(
    width: int, height: int, category: str
) -> None:
    with pytest.raises(WidgetRenderingError) as captured:
        render_built_in_icon(
            RegionSurface(width, height), BuiltInIconSettings(), context(width, height)
        )
    assert captured.value.category == category
    definition = create_widget_registry().resolve(WidgetType.BUILT_IN_ICON)
    assert (definition.minimum_width, definition.minimum_height) == (15, 10)


def test_rainbow_never_recolors_icon_but_still_recolors_neighboring_text() -> None:
    text = RegionConfig(
        id="text",
        name="Text",
        x=20,
        y=0,
        width=3,
        height=5,
        enabled=True,
        background_color="#000000",
        widget=WidgetConfig(
            WidgetType.STATIC_TEXT,
                StaticTextSettings(
                    content="A", font="3x5", horizontal_alignment="start",
                    vertical_alignment="top", overflow="error",
                    color="#FFFFFF", uppercase=True,
            ),  # type: ignore[arg-type]
        ),
    )
    compositor = Compositor(create_widget_registry())
    icon_frames = []
    text_frames = []
    for direction in ("rear_to_front", "front_to_rear"):
        for phase in (0.0, 1.0, 7.25):
            profile = ProfileConfigV2(
                brightness=None,
                canvas=CanvasConfig(90, 10, "#000000"),
                regions=(icon_region(), text),
                effect=ForegroundRainbowEffect(direction=direction, speed=0.1),  # type: ignore[arg-type]
            )
            result = compositor.render(
                profile, generation_time=NOW, animation_time=phase
            )
            icon_frames.append(b"".join(
                result.logical_framebuffer[y * 270 : y * 270 + 45]
                for y in range(10)
            ))
            text_frames.append(b"".join(
                result.logical_framebuffer[y * 270 + 60 : y * 270 + 69]
                for y in range(5)
            ))
    assert len(set(icon_frames)) == 1
    assert len(set(text_frames)) > 1
    assert TURQUOISE in icon_frames[0]


def test_device_and_preview_orientation_keep_exact_logo_rows() -> None:
    profile = ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(90, 10, "#000000"),
        regions=(icon_region(),),
    )
    result = Compositor(create_widget_registry()).render(
        profile, generation_time=NOW, animation_time=0.0
    )
    logical = b"".join(
        result.logical_framebuffer[y * 270 : y * 270 + 45] for y in range(10)
    )
    device = b"".join(
        result.device_framebuffer[y * 270 + 225 : y * 270 + 270]
        for y in range(10)
    )
    assert device == logical


def test_icon_schema_round_trip_has_no_configurable_color_and_unknown_id_fails() -> None:
    serialized = icon_widget().to_dict()
    assert serialized["settings"] == {
        "icon": "cachyos",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
    }
    assert WidgetConfig.from_dict(serialized).to_dict() == serialized
    with pytest.raises(ValueError, match="icon must be one of"):
        WidgetConfig.from_dict({
            "type": "built_in_icon",
            "settings": {**serialized["settings"], "icon": "unknown"},
        })


def test_cachyos_system_monitor_preset_is_immutable_valid_and_colorless() -> None:
    preset = next(
        item for item in get_builtin_presets() if item.id == "cachyos-system-monitor"
    )
    logo = next(
        region for region in preset.profile.regions
        if region.widget.type is WidgetType.BUILT_IN_ICON
    )
    assert (logo.width, logo.height) == (15, 10)
    assert "color" not in logo.widget.settings.to_dict()
    result = Compositor(create_widget_registry()).render(
        preset.profile, generation_time=NOW, animation_time=0.0
    )
    assert TURQUOISE in result.logical_framebuffer
