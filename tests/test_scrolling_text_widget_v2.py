"""Focused tests for the inactive Phase 3C Scrolling Text renderer."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

import pytest

from nextuxlinq.defaults import default_configuration
from nextuxlinq.migration import migrate_configuration_v1_to_v2
from nextuxlinq.rendering import render_profile_frame
from nextuxlinq.rendering_v2.fonts import FontTextError
from nextuxlinq.rendering_v2.registry import (
    RenderContext,
    create_widget_registry,
)
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets import scrolling_text as scrolling_module
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.scrolling_text import (
    render_scrolling_text,
    scrolling_pixel_phase,
    scrolling_text_origin,
)
from nextuxlinq.widget_models import (
    FontChoice,
    HorizontalAlignment,
    ScrollDirection,
    ScrollingTextSettings,
    VerticalAlignment,
    WidgetType,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
BACKGROUND = RgbColor(7, 8, 9)
WHITE = bytes.fromhex("FFFFFF")
SECRET_MARKER = "PHASE3C_SCROLL_SECRET_4A82"


def settings(**changes: object) -> ScrollingTextSettings:
    values = {
        "content": "A",
        "direction": ScrollDirection.LEFT,
        "speed": 1.0,
        "color": "#FFFFFF",
        "initial_alignment": HorizontalAlignment.END,
        "vertical_alignment": VerticalAlignment.CENTER,
        "font": FontChoice.LEGACY_5X7,
        "uppercase": True,
        "trailing_gap": 6,
        **changes,
    }
    return ScrollingTextSettings(**values)  # type: ignore[arg-type]


def context(
    width: int,
    height: int,
    *,
    animation_time: float = 0.0,
) -> RenderContext:
    return RenderContext(
        wall_clock=NOW,
        animation_time=animation_time,
        canvas_width=90,
        canvas_height=10,
        region_id="scrolling-text",
        region_width=width,
        region_height=height,
    )


def render(
    *,
    width: int = 31,
    height: int = 10,
    animation_time: float = 0.0,
    initial_color: RgbColor = BACKGROUND,
    **changes: object,
) -> RegionSurface:
    surface = RegionSurface(width, height, initial_color)
    assert (
        render_scrolling_text(
            surface,
            settings(**changes),
            context(
                width,
                height,
                animation_time=animation_time,
            ),
        )
        is None
    )
    return surface


def colored_pixels(
    surface: RegionSurface, color: bytes = WHITE
) -> frozenset[tuple[int, int]]:
    payload = surface.to_bytes()
    return frozenset(
        (x, y)
        for y in range(surface.height)
        for x in range(surface.width)
        if payload[(y * surface.width + x) * 3 :][:3] == color
    )


def assert_sanitized_error(
    error: WidgetRenderingError,
    *,
    category: str,
    field: str,
    original: BaseException,
) -> None:
    assert error.category == category
    assert error.field == field
    assert error.__context__ is None
    assert error.__cause__ is None
    assert all(value is not original for value in vars(error).values())
    assert all(value is not original for value in error.args)
    assert not any(
        isinstance(value, BaseException)
        for value in (*vars(error).values(), *error.args)
    )
    formatted = "".join(traceback.format_exception(error))
    for prohibited in (
        type(original).__name__,
        str(original),
        SECRET_MARKER,
    ):
        assert prohibited not in str(error)
        assert prohibited not in repr(error)
        assert prohibited not in formatted


def test_pixel_phase_uses_floor_and_supports_fractional_speed() -> None:
    assert scrolling_pixel_phase(0.0, 5.0) == 0
    assert scrolling_pixel_phase(0.19, 5.0) == 0
    assert scrolling_pixel_phase(0.2, 5.0) == 1
    assert scrolling_pixel_phase(3.9, 2.5) == 9


@pytest.mark.parametrize(
    ("alignment", "expected"),
    [
        (HorizontalAlignment.START, -5),
        (HorizontalAlignment.CENTER, 2),
        (HorizontalAlignment.END, 10),
    ],
)
def test_initial_alignment_origins(
    alignment: HorizontalAlignment, expected: int
) -> None:
    for direction in (ScrollDirection.LEFT, ScrollDirection.RIGHT):
        assert scrolling_text_origin(
            region_width=10,
            text_width=5,
            trailing_gap=6,
            initial_alignment=alignment,
            direction=direction,
            pixel_phase=0,
        ) == expected


def test_phase_zero_start_and_end_are_just_outside_edges() -> None:
    left_end = render(
        width=10,
        height=7,
        vertical_alignment=VerticalAlignment.TOP,
    )
    right_start = render(
        width=10,
        height=7,
        direction=ScrollDirection.RIGHT,
        initial_alignment=HorizontalAlignment.START,
        vertical_alignment=VerticalAlignment.TOP,
    )
    centered = render(
        width=10,
        height=7,
        initial_alignment=HorizontalAlignment.CENTER,
        vertical_alignment=VerticalAlignment.TOP,
    )

    assert not colored_pixels(left_end)
    assert not colored_pixels(right_start)
    assert colored_pixels(centered)


def test_left_and_right_move_by_integer_pixels() -> None:
    left_six = colored_pixels(
        render(
            width=10,
            height=7,
            animation_time=6,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    left_seven = colored_pixels(
        render(
            width=10,
            height=7,
            animation_time=7,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    right_six = colored_pixels(
        render(
            width=10,
            height=7,
            animation_time=6,
            direction=ScrollDirection.RIGHT,
            initial_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    right_seven = colored_pixels(
        render(
            width=10,
            height=7,
            animation_time=7,
            direction=ScrollDirection.RIGHT,
            initial_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )

    assert left_seven == frozenset((x - 1, y) for x, y in left_six)
    assert right_seven == frozenset((x + 1, y) for x, y in right_six)


def test_right_movement_is_symmetric_counterpart_of_legacy_left() -> None:
    for phase in (0, 1, 6, 14, 20):
        left = scrolling_text_origin(
            region_width=10,
            text_width=5,
            trailing_gap=6,
            initial_alignment=HorizontalAlignment.END,
            direction=ScrollDirection.LEFT,
            pixel_phase=phase,
        )
        right = scrolling_text_origin(
            region_width=10,
            text_width=5,
            trailing_gap=6,
            initial_alignment=HorizontalAlignment.START,
            direction=ScrollDirection.RIGHT,
            pixel_phase=phase,
        )
        assert right == 10 - 5 - left


def test_outward_initial_alignments_wrap_and_enter_opposite_edge() -> None:
    left_from_start = scrolling_text_origin(
        region_width=10,
        text_width=5,
        trailing_gap=3,
        initial_alignment=HorizontalAlignment.START,
        direction=ScrollDirection.LEFT,
        pixel_phase=3,
    )
    right_from_end = scrolling_text_origin(
        region_width=10,
        text_width=5,
        trailing_gap=3,
        initial_alignment=HorizontalAlignment.END,
        direction=ScrollDirection.RIGHT,
        pixel_phase=3,
    )

    assert left_from_start == 10
    assert right_from_end == -5


def test_trailing_gap_is_physical_pixels_and_cycle_wraps_exactly() -> None:
    cycle = 10 + 5 + 3
    origin = {
        phase: scrolling_text_origin(
            region_width=10,
            text_width=5,
            trailing_gap=3,
            initial_alignment=HorizontalAlignment.END,
            direction=ScrollDirection.LEFT,
            pixel_phase=phase,
        )
        for phase in (0, 15, 16, 17, 18)
    }

    assert origin == {0: 10, 15: -5, 16: -6, 17: -7, 18: 10}
    assert render(
        width=10,
        height=7,
        animation_time=0,
        trailing_gap=3,
    ).to_bytes() == render(
        width=10,
        height=7,
        animation_time=cycle,
        trailing_gap=3,
    ).to_bytes()


def test_width_never_shrinks_automatic_font_selection() -> None:
    for width, phase in ((1, 1), (90, 90)):
        automatic = render(
            width=width,
            height=7,
            animation_time=phase,
            font=FontChoice.AUTO,
        )
        explicit = render(
            width=width,
            height=7,
            animation_time=phase,
            font=FontChoice.LEGACY_5X7,
        )
        assert automatic.to_bytes() == explicit.to_bytes()

    for width, phase in ((1, 1), (90, 90)):
        automatic = render(
            width=width,
            height=10,
            animation_time=phase,
            font=FontChoice.AUTO,
        )
        explicit = render(
            width=width,
            height=10,
            animation_time=phase,
            font=FontChoice.NATIVE_5X10,
        )
        assert automatic.to_bytes() == explicit.to_bytes()


def test_five_pixel_regions_use_compact_font() -> None:
    automatic = render(
        width=10,
        height=5,
        animation_time=7,
        font=FontChoice.AUTO,
        vertical_alignment=VerticalAlignment.TOP,
    )
    compact = render(
        width=10,
        height=5,
        animation_time=7,
        font=FontChoice.COMPACT_3X5,
        vertical_alignment=VerticalAlignment.TOP,
    )

    assert automatic.to_bytes() == compact.to_bytes()
    assert colored_pixels(automatic)


def test_ascii_uppercase_and_unsupported_content() -> None:
    assert render(
        content="matrix",
        uppercase=True,
        animation_time=31,
    ).to_bytes() == render(
        content="MATRIX",
        uppercase=False,
        animation_time=31,
    ).to_bytes()

    surface = RegionSurface(31, 10, BACKGROUND)
    before = surface.to_bytes()
    with pytest.raises(WidgetRenderingError) as lowercase:
        render_scrolling_text(
            surface,
            settings(content="matrix", uppercase=False),
            context(31, 10, animation_time=31),
        )
    with pytest.raises(WidgetRenderingError) as unicode_error:
        render_scrolling_text(
            RegionSurface(31, 10),
            settings(content="MÄTRIX", uppercase=True),
            context(31, 10),
        )

    assert lowercase.value.category == "unsupported_glyph"
    assert unicode_error.value.category == "unsupported_glyph"
    assert surface.to_bytes() == before


def test_glyphs_are_clipped_locally_without_mirroring() -> None:
    surface = render(
        width=5,
        height=7,
        content="L",
        direction=ScrollDirection.RIGHT,
        initial_alignment=HorizontalAlignment.START,
        animation_time=5,
        vertical_alignment=VerticalAlignment.TOP,
    )
    pixels = colored_pixels(surface)

    assert {x for x, y in pixels if y == 0} == {0}
    assert {x for x, y in pixels if y == 6} == {0, 1, 2, 3, 4}
    assert all(0 <= x < 5 and 0 <= y < 7 for x, y in pixels)


@pytest.mark.parametrize(
    ("alignment", "expected_shift"),
    [
        (VerticalAlignment.TOP, 0),
        (VerticalAlignment.CENTER, 1),
        (VerticalAlignment.BOTTOM, 3),
    ],
)
def test_vertical_alignments(
    alignment: VerticalAlignment, expected_shift: int
) -> None:
    top = colored_pixels(
        render(
            animation_time=31,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    actual = colored_pixels(
        render(animation_time=31, vertical_alignment=alignment)
    )

    assert actual == frozenset(
        (x, y + expected_shift) for x, y in top
    )


def test_color_background_and_deterministic_output() -> None:
    first = render(
        animation_time=12.4,
        speed=2.5,
        color="#123456",
    )
    second = render(
        animation_time=12.4,
        speed=2.5,
        color="#123456",
    )
    colors = {
        first.to_bytes()[offset : offset + 3]
        for offset in range(0, len(first.to_bytes()), 3)
    }

    assert first.to_bytes() == second.to_bytes()
    assert bytes.fromhex("123456") in colors
    assert BACKGROUND.to_bytes() in colors


def _legacy_region_bytes(
    payload: bytes, *, region_x: int, region_width: int
) -> bytes:
    result = bytearray()
    raw_x = 90 - region_x - region_width
    for y in range(10):
        start = (y * 90 + raw_x) * 3
        result.extend(payload[start : start + region_width * 3])
    return bytes(result)


@pytest.mark.parametrize(
    "animation_time",
    [0.0, 0.2, 6.0, 6.2, 14.4, 15.4, 15.6],
)
def test_migrated_scrolling_text_is_exactly_legacy_equivalent(
    animation_time: float,
) -> None:
    source = default_configuration()
    migrated = migrate_configuration_v1_to_v2(source)
    region = migrated.active.regions[1]
    assert type(region.widget.settings) is ScrollingTextSettings
    phase = scrolling_pixel_phase(
        animation_time, region.widget.settings.speed
    )
    surface = RegionSurface(region.width, region.height)

    render_scrolling_text(
        surface,
        region.widget.settings,
        context(
            region.width,
            region.height,
            animation_time=animation_time,
        ),
    )
    active = render_profile_frame(
        source.active,
        NOW,
        scroll_offset=phase,
    ).to_bytes()

    assert surface.to_bytes() == _legacy_region_bytes(
        active,
        region_x=region.x,
        region_width=region.width,
    )


def test_exact_settings_type_and_too_short_region_fail_safely() -> None:
    class DerivedSettings(ScrollingTextSettings):
        pass

    derived = DerivedSettings(
        "A",
        ScrollDirection.LEFT,
        1.0,
        "#FFFFFF",
        HorizontalAlignment.END,
        VerticalAlignment.CENTER,
        FontChoice.LEGACY_5X7,
        True,
        6,
    )
    with pytest.raises(WidgetRenderingError) as wrong_type:
        render_scrolling_text(
            RegionSurface(31, 10), derived, context(31, 10)
        )
    surface = RegionSurface(31, 5, BACKGROUND)
    before = surface.to_bytes()
    with pytest.raises(WidgetRenderingError) as too_tall:
        render_scrolling_text(
            surface,
            settings(font=FontChoice.LEGACY_5X7),
            context(31, 5),
        )

    assert wrong_type.value.category == "renderer_invariant"
    assert wrong_type.value.field == "settings"
    assert too_tall.value.category == "content_too_tall"
    assert too_tall.value.field == "font"
    assert surface.to_bytes() == before


def test_translated_font_failure_is_fully_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecretFontError(FontTextError):
        def __init__(self) -> None:
            self.category = "unknown_font"
            self.field = "font"
            self.font_id = None
            Exception.__init__(self, SECRET_MARKER)

    original = SecretFontError()

    def fail(*args: object, **kwargs: object) -> object:
        raise original

    monkeypatch.setattr(scrolling_module, "resolve_font", fail)
    with pytest.raises(WidgetRenderingError) as raised:
        render_scrolling_text(
            RegionSurface(31, 10), settings(), context(31, 10)
        )

    assert_sanitized_error(
        raised.value,
        category="unknown_font",
        field="font",
        original=original,
    )


def test_unexpected_failure_is_fully_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError(SECRET_MARKER)

    def fail(*args: object, **kwargs: object) -> object:
        raise original

    monkeypatch.setattr(
        scrolling_module, "create_builtin_font_registry", fail
    )
    with pytest.raises(WidgetRenderingError) as raised:
        render_scrolling_text(
            RegionSurface(31, 10), settings(), context(31, 10)
        )

    assert_sanitized_error(
        raised.value,
        category="unexpected_renderer_failure",
        field="renderer",
        original=original,
    )


def test_inactive_registry_integrates_all_canonical_widgets() -> None:
    registry = create_widget_registry()

    assert tuple(
        definition.widget_type for definition in registry.definitions
    ) == tuple(WidgetType)
    assert registry.resolve(WidgetType.STATIC_TEXT).renderer is not None
    assert registry.resolve(WidgetType.SCROLLING_TEXT).renderer is not None
