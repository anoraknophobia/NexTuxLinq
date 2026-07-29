"""Behavioral tests for inactive Phase 3A local text drawing."""

from __future__ import annotations

import pytest

from nextuxlinq.rendering_v2.fonts.builtin import (
    create_builtin_font_registry,
)
from nextuxlinq.rendering_v2.fonts.models import FontTextError
from nextuxlinq.rendering_v2.fonts.text import (
    draw_glyph,
    draw_text,
    horizontal_origin,
    vertical_origin,
)
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.widget_models import (
    FontChoice,
    HorizontalAlignment,
    TextOverflow,
    VerticalAlignment,
)

BLACK = RgbColor(0, 0, 0)
WHITE = RgbColor(255, 255, 255)
RED = RgbColor(255, 0, 0)


def pixel(surface: RegionSurface, x: int, y: int) -> bytes:
    raw = surface.to_bytes()
    offset = (y * surface.width + x) * 3
    return raw[offset : offset + 3]


@pytest.mark.parametrize(
    "available,content,alignment,expected",
    [
        (7, 3, HorizontalAlignment.START, 0),
        (7, 3, HorizontalAlignment.CENTER, 2),
        (8, 3, HorizontalAlignment.CENTER, 2),
        (7, 3, HorizontalAlignment.END, 4),
        (3, 3, HorizontalAlignment.CENTER, 0),
        (2, 5, HorizontalAlignment.CENTER, -2),
        (4, 5, HorizontalAlignment.CENTER, -1),
        (2, 5, HorizontalAlignment.END, -3),
    ],
)
def test_horizontal_alignment_uses_integer_floor_rounding(
    available: int,
    content: int,
    alignment: HorizontalAlignment,
    expected: int,
) -> None:
    assert horizontal_origin(available, content, alignment) == expected


@pytest.mark.parametrize(
    "available,content,alignment,expected",
    [
        (10, 5, VerticalAlignment.TOP, 0),
        (10, 5, VerticalAlignment.CENTER, 2),
        (9, 5, VerticalAlignment.CENTER, 2),
        (10, 5, VerticalAlignment.BOTTOM, 5),
        (5, 5, VerticalAlignment.CENTER, 0),
        (4, 5, VerticalAlignment.CENTER, -1),
        (2, 5, VerticalAlignment.BOTTOM, -3),
    ],
)
def test_vertical_alignment_uses_integer_floor_rounding(
    available: int,
    content: int,
    alignment: VerticalAlignment,
    expected: int,
) -> None:
    assert vertical_origin(available, content, alignment) == expected


@pytest.mark.parametrize(
    "function,args",
    [
        (horizontal_origin, (True, 1, HorizontalAlignment.START)),
        (horizontal_origin, (1, 1, "center")),
        (vertical_origin, (1.0, 1, VerticalAlignment.TOP)),
        (vertical_origin, (1, 1, "bottom")),
    ],
)
def test_alignment_rejects_invalid_types(function, args: tuple[object, ...]) -> None:
    with pytest.raises(FontTextError):
        function(*args)


def test_draw_glyph_writes_only_foreground_pixels() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(3, 5, RED)
    written = draw_glyph(
        surface, font.glyphs["A"], origin_x=0, origin_y=0, color=WHITE
    )

    assert written == 10
    assert pixel(surface, 0, 0) == RED.to_bytes()
    assert pixel(surface, 1, 0) == WHITE.to_bytes()
    assert pixel(surface, 0, 1) == WHITE.to_bytes()
    assert pixel(surface, 1, 1) == RED.to_bytes()


@pytest.mark.parametrize(
    "origin_x,origin_y",
    [(-2, 0), (2, 0), (0, -4), (0, 4), (-2, -4), (2, 4)],
)
def test_draw_glyph_clips_on_every_surface_boundary(
    origin_x: int, origin_y: int
) -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(3, 5)

    written = draw_glyph(
        surface,
        font.glyphs["A"],
        origin_x=origin_x,
        origin_y=origin_y,
        color=WHITE,
    )

    assert 0 <= written < 10
    assert len(surface.to_bytes()) == 45


def test_draw_glyph_does_not_write_to_another_surface() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    target = RegionSurface(3, 5)
    other = RegionSurface(3, 5, RED)
    before = other.to_bytes()

    draw_glyph(
        target, font.glyphs["A"], origin_x=-1, origin_y=0, color=WHITE
    )

    assert other.to_bytes() == before


def test_error_overflow_draws_exact_fit_without_stretching() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(7, 5)

    result = draw_text(
        surface,
        "AA",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.CENTER,
        vertical_alignment=VerticalAlignment.CENTER,
        overflow=TextOverflow.ERROR,
    )

    assert result.rendered_text == "AA"
    assert result.measured_width == 7
    assert result.origin_x == 0
    assert result.origin_y == 0
    assert result.truncated is False


def test_error_overflow_rejects_too_wide_without_drawing() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(6, 5, RED)
    before = surface.to_bytes()

    with pytest.raises(FontTextError) as raised:
        draw_text(
            surface,
            "AA",
            font=font,
            color=WHITE,
            horizontal_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
            overflow=TextOverflow.ERROR,
        )

    assert raised.value.category == "content_too_wide"
    assert surface.to_bytes() == before


@pytest.mark.parametrize(
    "width,text,expected,measured",
    [
        (3, "A", "A", 3),
        (3, "AA", "A", 3),
        (4, "AA", "A", 3),
        (6, "AA", "A", 3),
        (7, "AA", "AA", 7),
        (1, "A", "", 0),
        (5, "A!", "A!", 5),
        (4, "A!", "A", 3),
    ],
)
def test_truncate_keeps_only_complete_glyphs_and_internal_spacing(
    width: int, text: str, expected: str, measured: int
) -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(width, 5)
    result = draw_text(
        surface,
        text,
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.CENTER,
        vertical_alignment=VerticalAlignment.CENTER,
        overflow=TextOverflow.TRUNCATE,
    )

    assert result.rendered_text == expected
    assert result.measured_width == measured
    assert result.truncated is (expected != text)
    assert result.origin_x == (width - measured) // 2


def test_truncate_aligns_prefix_instead_of_original_text() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(6, 5)
    result = draw_text(
        surface,
        "AA",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.END,
        vertical_alignment=VerticalAlignment.BOTTOM,
        overflow=TextOverflow.TRUNCATE,
    )
    assert result.rendered_text == "A"
    assert result.origin_x == 3
    assert result.origin_y == 0


def test_clip_draws_partial_left_edge_without_mirroring() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(2, 5)
    result = draw_text(
        surface,
        "A",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.END,
        vertical_alignment=VerticalAlignment.TOP,
        overflow=TextOverflow.CLIP,
    )

    assert result.origin_x == -1
    assert result.rendered_text == "A"
    assert pixel(surface, 0, 0) == WHITE.to_bytes()
    assert pixel(surface, 1, 0) == BLACK.to_bytes()


def test_clip_draws_partial_right_edge_and_wide_content() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(5, 5)
    result = draw_text(
        surface,
        "AA",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.START,
        vertical_alignment=VerticalAlignment.TOP,
        overflow=TextOverflow.CLIP,
    )

    assert result.measured_width == 7
    assert result.origin_x == 0
    assert pixel(surface, 1, 0) == WHITE.to_bytes()
    assert pixel(surface, 4, 1) == WHITE.to_bytes()


def test_five_pixel_row_uses_exact_vertical_fit_and_ascii_uppercase() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(7, 5)
    result = draw_text(
        surface,
        "ab",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.CENTER,
        vertical_alignment=VerticalAlignment.CENTER,
        overflow=TextOverflow.ERROR,
        uppercase=True,
    )

    assert result.normalized_text == "AB"
    assert result.origin_y == 0
    assert any(surface.to_bytes())


def test_text_too_tall_fails_without_modifying_background() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.LEGACY_5X7)
    surface = RegionSurface(5, 5, RED)
    before = surface.to_bytes()
    with pytest.raises(FontTextError) as raised:
        draw_text(
            surface,
            "A",
            font=font,
            color=WHITE,
            horizontal_alignment=HorizontalAlignment.CENTER,
            vertical_alignment=VerticalAlignment.CENTER,
            overflow=TextOverflow.CLIP,
        )
    assert raised.value.category == "content_too_tall"
    assert surface.to_bytes() == before


def test_empty_text_is_a_deterministic_no_op() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    surface = RegionSurface(3, 5, RED)
    before = surface.to_bytes()
    first = draw_text(
        surface,
        "",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.CENTER,
        vertical_alignment=VerticalAlignment.CENTER,
        overflow=TextOverflow.ERROR,
    )
    second_surface = RegionSurface(3, 5, RED)
    second = draw_text(
        second_surface,
        "",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.CENTER,
        vertical_alignment=VerticalAlignment.CENTER,
        overflow=TextOverflow.ERROR,
    )

    assert first == second
    assert first.rendered_text == ""
    assert first.measured_width == 0
    assert first.origin_x == 1
    assert surface.to_bytes() == before == second_surface.to_bytes()


def test_draw_result_is_immutable() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    result = draw_text(
        RegionSurface(3, 5),
        "A",
        font=font,
        color=WHITE,
        horizontal_alignment=HorizontalAlignment.START,
        vertical_alignment=VerticalAlignment.TOP,
        overflow=TextOverflow.ERROR,
    )
    with pytest.raises((AttributeError, TypeError)):
        result.origin_x = 2  # type: ignore[misc]


def test_empty_text_still_validates_foreground_color() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    with pytest.raises(FontTextError) as raised:
        draw_text(
            RegionSurface(3, 5),
            "",
            font=font,
            color=(255, 255, 255),  # type: ignore[arg-type]
            horizontal_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
            overflow=TextOverflow.ERROR,
        )
    assert raised.value.category == "renderer_invariant"
