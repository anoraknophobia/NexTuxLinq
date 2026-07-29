"""Tests for the inactive schema-v2 generic region compositor."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nextuxlinq.creator import FRAME_SIZE
from nextuxlinq.models_v2 import CanvasConfig, ProfileConfigV2, RegionConfig
from nextuxlinq.rendering_v2.compositor import (
    Compositor,
    CompositorError,
    CompositorFailure,
    PixelFormat,
    RenderResult,
)
from nextuxlinq.rendering_v2.registry import (
    RenderContext,
    WidgetDefinition,
    WidgetRegistry,
    create_phase2_widget_registry,
)
from nextuxlinq.rendering_v2.surface import (
    RegionSurface,
    RgbColor,
)
from nextuxlinq.widget_models import (
    BlankSettings,
    ClockSettings,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 7, 29, 14, 30, tzinfo=timezone.utc)
BLACK = bytes((0, 0, 0))


def clock_settings() -> ClockSettings:
    return ClockSettings(
        format="24h",  # type: ignore[arg-type]
        show_seconds=False,
        blink_separator=False,
        time_zone="local",
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
    )


def blank_widget() -> WidgetConfig:
    return WidgetConfig(
        type=WidgetType.BLANK,
        settings=BlankSettings(),
    )


def clock_widget() -> WidgetConfig:
    return WidgetConfig(
        type=WidgetType.CLOCK,
        settings=clock_settings(),
    )


def region(
    region_id: str,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    widget: WidgetConfig | None = None,
    name: str | None = None,
    enabled: bool = True,
    background_color: str | None = None,
) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=name,
        x=x,
        y=y,
        width=width,
        height=height,
        enabled=enabled,
        background_color=background_color,
        widget=blank_widget() if widget is None else widget,
    )


def profile(
    regions: tuple[RegionConfig, ...] = (),
    *,
    background_color: str = "#000000",
) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(
            width=90,
            height=10,
            background_color=background_color,
        ),
        regions=regions,
    )


def render(
    candidate: ProfileConfigV2,
    registry: WidgetRegistry | None = None,
    *,
    animation_time: float = 2.5,
) -> RenderResult:
    return Compositor(
        create_phase2_widget_registry() if registry is None else registry
    ).render(
        candidate,
        generation_time=NOW,
        animation_time=animation_time,
    )


def pixel(payload: bytes, x: int, y: int) -> bytes:
    offset = (y * 90 + x) * 3
    return payload[offset : offset + 3]


def clock_definition(
    renderer,
    *,
    animated: bool = False,
) -> WidgetDefinition:
    return WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=clock_settings(),
        renderer=renderer,
        animated=animated,
        minimum_width=1,
        minimum_height=1,
    )


def registry_with_clock(
    renderer,
    *,
    animated: bool = False,
    include_blank: bool = True,
) -> WidgetRegistry:
    definitions = [clock_definition(renderer, animated=animated)]
    if include_blank:
        definitions = [
            *create_phase2_widget_registry().definitions,
            *definitions,
        ]
    return WidgetRegistry(definitions)


def test_empty_black_canvas_has_exact_immutable_outputs() -> None:
    result = render(profile())

    assert result.logical_framebuffer == bytes(FRAME_SIZE)
    assert result.device_framebuffer == bytes(FRAME_SIZE)
    assert len(result.logical_framebuffer) == FRAME_SIZE
    assert len(result.device_framebuffer) == FRAME_SIZE
    assert isinstance(result.logical_framebuffer, bytes)
    assert isinstance(result.device_framebuffer, bytes)
    assert result.width == 90
    assert result.height == 10
    assert result.pixel_format is PixelFormat.RGB888
    assert result.regions == ()
    assert result.generation_time == NOW
    assert result.animation_time == 2.5
    assert result.animated_content is False


def test_empty_nonblack_canvas_fills_logical_and_device_outputs() -> None:
    result = render(profile(background_color="#112233"))
    expected = bytes.fromhex("112233") * 900

    assert result.logical_framebuffer == expected
    assert result.device_framebuffer == expected


def test_one_full_canvas_blank_region_inherits_canvas_background() -> None:
    candidate = profile(
        (
            region(
                "full",
                x=0,
                y=0,
                width=90,
                height=10,
                name="Complete canvas",
            ),
        ),
        background_color="#102030",
    )

    result = render(candidate)

    assert result.logical_framebuffer == bytes.fromhex("102030") * 900
    assert result.device_framebuffer == result.logical_framebuffer
    assert result.regions[0].widget_type is WidgetType.BLANK


def test_region_background_and_uncovered_pixels_remain_distinct() -> None:
    candidate = profile(
        (
            region(
                "colored-gap",
                x=10,
                y=2,
                width=4,
                height=3,
                background_color="#445566",
            ),
        ),
        background_color="#010203",
    )

    result = render(candidate)

    assert pixel(result.logical_framebuffer, 10, 2) == bytes.fromhex(
        "445566"
    )
    assert pixel(result.logical_framebuffer, 13, 4) == bytes.fromhex(
        "445566"
    )
    assert pixel(result.logical_framebuffer, 9, 2) == bytes.fromhex(
        "010203"
    )
    assert pixel(result.logical_framebuffer, 14, 4) == bytes.fromhex(
        "010203"
    )


def test_disabled_region_ignores_its_background_and_unsupported_widget() -> None:
    candidate = profile(
        (
            region(
                "disabled-clock",
                x=0,
                y=0,
                width=20,
                height=10,
                widget=clock_widget(),
                enabled=False,
                background_color="#FF0000",
            ),
        ),
        background_color="#010203",
    )

    result = render(candidate)

    assert result.logical_framebuffer == bytes.fromhex("010203") * 900
    assert result.device_framebuffer == result.logical_framebuffer
    assert result.regions[0].enabled is False
    assert result.regions[0].widget_type is WidgetType.CLOCK
    assert result.animated_content is False


def test_explicit_blank_gap_and_uncovered_gap_are_both_supported() -> None:
    candidate = profile(
        (
            region(
                "explicit-gap",
                x=20,
                y=0,
                width=4,
                height=10,
                background_color="#0A0B0C",
            ),
        ),
        background_color="#000000",
    )

    result = render(candidate)

    for y in range(10):
        for x in range(20, 24):
            assert pixel(result.logical_framebuffer, x, y) == bytes.fromhex(
                "0A0B0C"
            )
        assert pixel(result.logical_framebuffer, 19, y) == BLACK
        assert pixel(result.logical_framebuffer, 24, y) == BLACK


@pytest.mark.parametrize(
    "regions",
    [
        (
            region("left", x=0, y=0, width=30, height=10),
            region("right", x=35, y=0, width=55, height=10),
        ),
        (
            region("top", x=0, y=0, width=90, height=5),
            region("bottom", x=0, y=5, width=90, height=5),
        ),
        (
            region("left", x=0, y=0, width=45, height=10),
            region("top-right", x=45, y=0, width=45, height=5),
            region("bottom-right", x=45, y=5, width=45, height=5),
        ),
        (
            region("top-left", x=0, y=0, width=25, height=4),
            region("bottom-left", x=0, y=4, width=25, height=6),
            region("middle", x=30, y=1, width=20, height=8),
            region("right", x=60, y=0, width=30, height=10),
        ),
    ],
)
def test_arbitrary_nonoverlapping_geometry_composes(
    regions: tuple[RegionConfig, ...],
) -> None:
    result = render(profile(regions, background_color="#123456"))

    assert len(result.logical_framebuffer) == FRAME_SIZE
    assert len(result.device_framebuffer) == FRAME_SIZE
    assert tuple(item.id for item in result.regions) == tuple(
        item.id for item in regions
    )


def test_metadata_preserves_geometry_names_order_and_disabled_entries() -> None:
    candidate = profile(
        (
            region(
                "third",
                x=60,
                y=0,
                width=30,
                height=10,
                name="Printable 東京",
            ),
            region(
                "first",
                x=0,
                y=0,
                width=20,
                height=5,
                name=None,
                enabled=False,
            ),
            region(
                "second",
                x=20,
                y=5,
                width=40,
                height=5,
                name="Second",
            ),
        )
    )

    metadata = render(candidate).regions

    assert [
        (
            item.id,
            item.name,
            item.x,
            item.y,
            item.width,
            item.height,
            item.enabled,
            item.widget_type,
        )
        for item in metadata
    ] == [
        ("third", "Printable 東京", 60, 0, 30, 10, True, WidgetType.BLANK),
        ("first", None, 0, 0, 20, 5, False, WidgetType.BLANK),
        ("second", "Second", 20, 5, 40, 5, True, WidgetType.BLANK),
    ]


def test_one_region_invokes_exactly_one_selected_renderer() -> None:
    calls: list[str] = []

    def clock_renderer(surface, settings, context):
        calls.append(context.region_id)

    candidate = profile(
        (
            region(
                "only",
                x=0,
                y=0,
                width=90,
                height=10,
                widget=clock_widget(),
            ),
        )
    )

    render(candidate, registry_with_clock(clock_renderer))

    assert calls == ["only"]


def test_changing_widget_selection_preserves_region_geometry() -> None:
    geometry = {
        "x": 7,
        "y": 2,
        "width": 50,
        "height": 6,
    }
    blank_profile = profile(
        (region("same", **geometry, widget=blank_widget()),)
    )
    clock_profile = profile(
        (region("same", **geometry, widget=clock_widget()),)
    )

    blank_metadata = render(blank_profile).regions[0]
    clock_metadata = render(
        clock_profile,
        registry_with_clock(lambda surface, settings, context: None),
    ).regions[0]

    assert (
        blank_metadata.x,
        blank_metadata.y,
        blank_metadata.width,
        blank_metadata.height,
    ) == (
        clock_metadata.x,
        clock_metadata.y,
        clock_metadata.width,
        clock_metadata.height,
    ) == (7, 2, 50, 6)
    assert blank_metadata.widget_type is WidgetType.BLANK
    assert clock_metadata.widget_type is WidgetType.CLOCK


def test_full_width_widget_pattern_is_not_stretched() -> None:
    color = RgbColor(1, 2, 3)

    def intrinsic_renderer(surface, settings, context):
        assert surface.width == 90
        assert surface.height == 10
        surface.set_pixel(44, 5, color)
        surface.set_pixel(45, 5, RgbColor(4, 5, 6))

    candidate = profile(
        (
            region(
                "wide",
                x=0,
                y=0,
                width=90,
                height=10,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(intrinsic_renderer))

    assert pixel(result.logical_framebuffer, 44, 5) == color.to_bytes()
    assert pixel(result.logical_framebuffer, 45, 5) == bytes((4, 5, 6))
    assert sum(
        pixel(result.logical_framebuffer, x, y) != BLACK
        for y in range(10)
        for x in range(90)
    ) == 2


def test_renderer_cannot_write_outside_its_local_surface() -> None:
    results: list[bool] = []

    def clipped_renderer(surface, settings, context):
        results.extend(
            (
                surface.set_pixel(-1, 0, RgbColor(255, 0, 0)),
                surface.set_pixel(surface.width, 0, RgbColor(255, 0, 0)),
                surface.set_pixel(0, -1, RgbColor(255, 0, 0)),
                surface.set_pixel(0, surface.height, RgbColor(255, 0, 0)),
                surface.set_pixel(0, 0, RgbColor(0, 255, 0)),
            )
        )

    candidate = profile(
        (
            region(
                "small",
                x=30,
                y=3,
                width=2,
                height=2,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(clipped_renderer))

    assert results == [False, False, False, False, True]
    assert pixel(result.logical_framebuffer, 30, 3) == bytes((0, 255, 0))
    assert pixel(result.logical_framebuffer, 29, 3) == BLACK
    assert pixel(result.logical_framebuffer, 32, 3) == BLACK
    assert pixel(result.logical_framebuffer, 30, 2) == BLACK
    assert pixel(result.logical_framebuffer, 30, 5) == BLACK


def test_enabled_unsupported_widget_fails_without_partial_result() -> None:
    candidate = profile(
        (
            region(
                "first",
                x=0,
                y=0,
                width=10,
                height=10,
                background_color="#FF0000",
            ),
            region(
                "unsupported",
                x=10,
                y=0,
                width=20,
                height=10,
                widget=clock_widget(),
            ),
        )
    )

    with pytest.raises(CompositorError) as captured:
        render(candidate)

    assert captured.value.category is (
        CompositorFailure.MISSING_WIDGET_DEFINITION
    )
    assert captured.value.region_id == "unsupported"
    assert not hasattr(captured.value, "partial_result")


def test_asymmetric_region_block_reversal_preserves_content_order() -> None:
    left = RgbColor(1, 2, 3)
    right = RgbColor(4, 5, 6)

    def asymmetric(surface, settings, context):
        surface.set_pixel(0, 1, left)
        surface.set_pixel(surface.width - 1, 1, right)

    candidate = profile(
        (
            region(
                "front",
                x=0,
                y=0,
                width=3,
                height=4,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(asymmetric))

    assert pixel(result.logical_framebuffer, 0, 1) == left.to_bytes()
    assert pixel(result.logical_framebuffer, 2, 1) == right.to_bytes()
    assert pixel(result.device_framebuffer, 87, 1) == left.to_bytes()
    assert pixel(result.device_framebuffer, 89, 1) == right.to_bytes()
    assert pixel(result.device_framebuffer, 87, 0) == BLACK


@pytest.mark.parametrize(
    "logical_x,expected_raw_x",
    [(0, 89), (89, 0)],
)
def test_width_one_regions_map_to_opposite_edges(
    logical_x: int, expected_raw_x: int
) -> None:
    color = RgbColor(7, 8, 9)

    def renderer(surface, settings, context):
        surface.set_pixel(0, 0, color)

    candidate = profile(
        (
            region(
                "edge",
                x=logical_x,
                y=0,
                width=1,
                height=1,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(renderer))

    assert pixel(result.logical_framebuffer, logical_x, 0) == color.to_bytes()
    assert pixel(result.device_framebuffer, expected_raw_x, 0) == (
        color.to_bytes()
    )


def test_full_width_region_has_identity_orientation_without_row_reversal() -> None:
    colors = (RgbColor(1, 0, 0), RgbColor(0, 2, 0), RgbColor(0, 0, 3))

    def renderer(surface, settings, context):
        surface.set_pixel(0, 0, colors[0])
        surface.set_pixel(45, 5, colors[1])
        surface.set_pixel(89, 9, colors[2])

    candidate = profile(
        (
            region(
                "full",
                x=0,
                y=0,
                width=90,
                height=10,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(renderer))

    assert result.device_framebuffer == result.logical_framebuffer
    assert pixel(result.device_framebuffer, 0, 0) == colors[0].to_bytes()
    assert pixel(result.device_framebuffer, 45, 5) == colors[1].to_bytes()
    assert pixel(result.device_framebuffer, 89, 9) == colors[2].to_bytes()


def test_multiple_regions_reverse_blocks_without_mirroring_or_y_reversal() -> None:
    colors = {
        "front": RgbColor(10, 0, 0),
        "middle": RgbColor(0, 20, 0),
        "rear": RgbColor(0, 0, 30),
    }

    def renderer(surface, settings, context):
        surface.set_pixel(0, 0, colors[context.region_id])
        surface.set_pixel(
            surface.width - 1,
            surface.height - 1,
            colors[context.region_id],
        )

    candidate = profile(
        (
            region(
                "front",
                x=0,
                y=0,
                width=20,
                height=10,
                widget=clock_widget(),
            ),
            region(
                "middle",
                x=25,
                y=2,
                width=30,
                height=4,
                widget=clock_widget(),
            ),
            region(
                "rear",
                x=60,
                y=5,
                width=30,
                height=5,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(renderer))

    assert pixel(result.device_framebuffer, 70, 0) == colors[
        "front"
    ].to_bytes()
    assert pixel(result.device_framebuffer, 89, 9) == colors[
        "front"
    ].to_bytes()
    assert pixel(result.device_framebuffer, 35, 2) == colors[
        "middle"
    ].to_bytes()
    assert pixel(result.device_framebuffer, 64, 5) == colors[
        "middle"
    ].to_bytes()
    assert pixel(result.device_framebuffer, 0, 5) == colors[
        "rear"
    ].to_bytes()
    assert pixel(result.device_framebuffer, 29, 9) == colors[
        "rear"
    ].to_bytes()


def test_two_vertical_rows_keep_y_coordinates_and_row_order() -> None:
    top = RgbColor(100, 0, 0)
    bottom = RgbColor(0, 100, 0)

    def renderer(surface, settings, context):
        color = top if context.region_id == "top" else bottom
        surface.set_pixel(0, 0, color)
        surface.set_pixel(surface.width - 1, surface.height - 1, color)

    candidate = profile(
        (
            region(
                "top",
                x=10,
                y=0,
                width=30,
                height=5,
                widget=clock_widget(),
            ),
            region(
                "bottom",
                x=10,
                y=5,
                width=30,
                height=5,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(renderer))
    raw_x = 90 - 10 - 30

    assert pixel(result.device_framebuffer, raw_x, 0) == top.to_bytes()
    assert pixel(result.device_framebuffer, raw_x + 29, 4) == top.to_bytes()
    assert pixel(result.device_framebuffer, raw_x, 5) == bottom.to_bytes()
    assert pixel(result.device_framebuffer, raw_x + 29, 9) == (
        bottom.to_bytes()
    )


def test_legacy_region_geometry_uses_verified_creator_block_origins() -> None:
    def renderer(surface, settings, context):
        surface.set_pixel(0, 0, RgbColor(1, 2, 3))
        surface.set_pixel(
            surface.width - 1, 0, RgbColor(4, 5, 6)
        )

    candidate = profile(
        (
            region(
                "front",
                x=0,
                y=0,
                width=24,
                height=10,
                widget=clock_widget(),
            ),
            region(
                "scroll",
                x=28,
                y=0,
                width=31,
                height=10,
                widget=clock_widget(),
            ),
            region(
                "rear",
                x=59,
                y=0,
                width=31,
                height=10,
                widget=clock_widget(),
            ),
        )
    )
    result = render(candidate, registry_with_clock(renderer))

    for raw_origin, width in ((66, 24), (31, 31), (0, 31)):
        assert pixel(result.device_framebuffer, raw_origin, 0) == bytes(
            (1, 2, 3)
        )
        assert pixel(
            result.device_framebuffer, raw_origin + width - 1, 0
        ) == bytes((4, 5, 6))
    for x in range(62, 66):
        assert pixel(result.device_framebuffer, x, 0) == BLACK


def test_device_mapping_can_be_reconstructed_for_region_pixels() -> None:
    def renderer(surface, settings, context):
        for y in range(surface.height):
            for x in range(surface.width):
                surface.set_pixel(
                    x,
                    y,
                    RgbColor((x + 1) % 256, (y + 1) % 256, 17),
                )

    candidate = profile(
        (
            region(
                "pattern",
                x=13,
                y=2,
                width=7,
                height=4,
                widget=clock_widget(),
            ),
        ),
        background_color="#090909",
    )
    result = render(candidate, registry_with_clock(renderer))
    raw_x = 90 - 13 - 7

    for y in range(4):
        for local_x in range(7):
            assert pixel(
                result.logical_framebuffer, 13 + local_x, 2 + y
            ) == pixel(
                result.device_framebuffer, raw_x + local_x, 2 + y
            )


def test_animated_content_uses_only_enabled_rendered_definitions() -> None:
    registry = registry_with_clock(
        lambda surface, settings, context: None,
        animated=True,
    )
    disabled = profile(
        (
            region(
                "disabled",
                x=0,
                y=0,
                width=10,
                height=10,
                widget=clock_widget(),
                enabled=False,
            ),
        )
    )
    enabled = profile(
        (
            region(
                "enabled",
                x=0,
                y=0,
                width=10,
                height=10,
                widget=clock_widget(),
            ),
        )
    )

    assert render(disabled, registry).animated_content is False
    assert render(enabled, registry).animated_content is True


def test_invalid_generation_and_animation_inputs_are_sanitized() -> None:
    candidate = profile()

    for generation_time, animation_time in (
        (datetime(2026, 7, 29), 1.0),
        (NOW, float("nan")),
        (NOW, float("inf")),
        (NOW, -1.0),
        (NOW, True),
    ):
        with pytest.raises(CompositorError) as captured:
            Compositor(create_phase2_widget_registry()).render(
                candidate,
                generation_time=generation_time,
                animation_time=animation_time,
            )
        assert captured.value.category is CompositorFailure.INVALID_CONTEXT
        assert "2026" not in str(captured.value)
        assert "nan" not in str(captured.value).lower()


def test_invalid_drawing_input_is_sanitized() -> None:
    def renderer(surface, settings, context):
        surface.set_pixel(0, 0, (1, 2, 3))  # type: ignore[arg-type]

    candidate = profile(
        (
            region(
                "bad-drawing",
                x=0,
                y=0,
                width=10,
                height=10,
                widget=clock_widget(),
            ),
        )
    )

    with pytest.raises(CompositorError) as captured:
        render(candidate, registry_with_clock(renderer))

    assert captured.value.category is CompositorFailure.INVALID_DRAWING
    assert "RgbColor" not in str(captured.value)


def test_corrupt_local_buffer_fails_complete_frame() -> None:
    def renderer(surface, settings, context):
        surface._data.pop()  # type: ignore[attr-defined]

    candidate = profile(
        (
            region(
                "corrupt",
                x=0,
                y=0,
                width=10,
                height=10,
                widget=clock_widget(),
            ),
        )
    )

    with pytest.raises(CompositorError) as captured:
        render(candidate, registry_with_clock(renderer))

    assert captured.value.category is CompositorFailure.INVALID_LOCAL_BUFFER
    assert not hasattr(captured.value, "partial_result")


def test_render_result_copies_mutable_inputs_and_rejects_wrong_sizes() -> None:
    rendered = render(profile())
    logical = bytearray(rendered.logical_framebuffer)
    device = bytearray(rendered.device_framebuffer)
    copied = RenderResult(
        logical_framebuffer=logical,  # type: ignore[arg-type]
        device_framebuffer=device,  # type: ignore[arg-type]
        width=90,
        height=10,
        pixel_format=PixelFormat.RGB888,
        regions=list(rendered.regions),  # type: ignore[arg-type]
        generation_time=NOW,
        animation_time=0.0,
        animated_content=False,
    )
    logical[0] = 255
    device[0] = 255

    assert copied.logical_framebuffer[0] == 0
    assert copied.device_framebuffer[0] == 0
    assert isinstance(copied.regions, tuple)

    with pytest.raises(ValueError, match="2700"):
        RenderResult(
            logical_framebuffer=b"",
            device_framebuffer=bytes(FRAME_SIZE),
            width=90,
            height=10,
            pixel_format=PixelFormat.RGB888,
            regions=(),
            generation_time=NOW,
            animation_time=0.0,
            animated_content=False,
        )
