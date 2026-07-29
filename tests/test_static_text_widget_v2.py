"""Focused tests for the inactive Phase 3C Static Text renderer."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

import pytest

from nextuxlinq.rendering_v2.fonts import FontTextError
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets import static_text as static_module
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.static_text import render_static_text
from nextuxlinq.widget_models import (
    FontChoice,
    HorizontalAlignment,
    StaticTextSettings,
    TextOverflow,
    VerticalAlignment,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
BACKGROUND = RgbColor(7, 8, 9)
WHITE = bytes.fromhex("FFFFFF")
SECRET_MARKER = "PHASE3C_STATIC_SECRET_7E19"


def settings(**changes: object) -> StaticTextSettings:
    values = {
        "content": "AB",
        "color": "#FFFFFF",
        "horizontal_alignment": HorizontalAlignment.CENTER,
        "vertical_alignment": VerticalAlignment.CENTER,
        "font": FontChoice.LEGACY_5X7,
        "uppercase": True,
        "overflow": TextOverflow.ERROR,
        **changes,
    }
    return StaticTextSettings(**values)  # type: ignore[arg-type]


def context(width: int, height: int) -> RenderContext:
    return RenderContext(
        wall_clock=NOW,
        animation_time=123.75,
        canvas_width=90,
        canvas_height=10,
        region_id="static-text",
        region_width=width,
        region_height=height,
    )


def render(
    *,
    width: int = 90,
    height: int = 10,
    initial_color: RgbColor = BACKGROUND,
    **changes: object,
) -> RegionSurface:
    surface = RegionSurface(width, height, initial_color)
    assert (
        render_static_text(
            surface,
            settings(**changes),
            context(width, height),
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


def test_ascii_uppercase_is_optional_and_deterministic() -> None:
    uppercase = render(content="a-z_09", uppercase=True)
    canonical = render(content="A-Z_09", uppercase=False)

    assert uppercase.to_bytes() == canonical.to_bytes()
    assert render(content="A-Z_09", uppercase=False).to_bytes() == (
        canonical.to_bytes()
    )


@pytest.mark.parametrize("content", ["lower", "Ä", "é", "🙂"])
def test_preserved_unsupported_content_fails_without_drawing(
    content: str,
) -> None:
    surface = RegionSurface(90, 10, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_static_text(
            surface,
            settings(content=content, uppercase=False),
            context(90, 10),
        )

    assert raised.value.category == "unsupported_glyph"
    assert raised.value.field == "content"
    assert surface.to_bytes() == before
    assert content not in str(raised.value)


@pytest.mark.parametrize(
    ("font", "content", "height"),
    [
        (FontChoice.LEGACY_3X7, "12:3", 7),
        (FontChoice.LEGACY_5X7, "AB", 7),
        (FontChoice.COMPACT_3X5, "AB", 5),
    ],
)
def test_explicit_fonts_render_at_intrinsic_height(
    font: FontChoice, content: str, height: int
) -> None:
    pixels = colored_pixels(
        render(
            content=content,
            font=font,
            horizontal_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )

    assert min(y for _, y in pixels) == 0
    assert max(y for _, y in pixels) == height - 1


def test_auto_font_priority_and_five_pixel_fallback() -> None:
    assert render(font=FontChoice.AUTO).to_bytes() == render(
        font=FontChoice.LEGACY_5X7
    ).to_bytes()
    assert render(height=5, font=FontChoice.AUTO).to_bytes() == render(
        height=5, font=FontChoice.COMPACT_3X5
    ).to_bytes()


@pytest.mark.parametrize(
    ("alignment", "expected_shift"),
    [
        (HorizontalAlignment.START, 0),
        (HorizontalAlignment.CENTER, 9),
        (HorizontalAlignment.END, 19),
    ],
)
def test_horizontal_alignments(
    alignment: HorizontalAlignment, expected_shift: int
) -> None:
    start = colored_pixels(
        render(
            width=30,
            horizontal_alignment=HorizontalAlignment.START,
        )
    )
    actual = colored_pixels(
        render(width=30, horizontal_alignment=alignment)
    )

    assert actual == frozenset(
        (x + expected_shift, y) for x, y in start
    )


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
        render(vertical_alignment=VerticalAlignment.TOP)
    )
    actual = colored_pixels(render(vertical_alignment=alignment))

    assert actual == frozenset(
        (x, y + expected_shift) for x, y in top
    )


def test_error_overflow_exact_fit_and_preflight_failure() -> None:
    assert colored_pixels(render(width=11))
    surface = RegionSurface(10, 10, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_static_text(surface, settings(), context(10, 10))

    assert raised.value.category == "content_too_wide"
    assert raised.value.field == "content"
    assert surface.to_bytes() == before


def test_truncate_draws_largest_complete_glyph_prefix() -> None:
    truncated = render(
        width=5,
        overflow=TextOverflow.TRUNCATE,
        horizontal_alignment=HorizontalAlignment.START,
    )
    prefix = render(
        width=5,
        content="A",
        overflow=TextOverflow.ERROR,
        horizontal_alignment=HorizontalAlignment.START,
    )

    assert truncated.to_bytes() == prefix.to_bytes()


def test_clip_draws_partial_edge_glyph_from_complete_text_alignment() -> None:
    clipped = render(
        width=3,
        overflow=TextOverflow.CLIP,
        horizontal_alignment=HorizontalAlignment.START,
    )
    truncated = render(
        width=3,
        overflow=TextOverflow.TRUNCATE,
        horizontal_alignment=HorizontalAlignment.START,
    )

    assert colored_pixels(clipped)
    assert not colored_pixels(truncated)


def test_truncate_and_clip_auto_font_do_not_shrink_for_width() -> None:
    for overflow in (TextOverflow.TRUNCATE, TextOverflow.CLIP):
        assert render(
            width=5,
            font=FontChoice.AUTO,
            overflow=overflow,
        ).to_bytes() == render(
            width=5,
            font=FontChoice.LEGACY_5X7,
            overflow=overflow,
        ).to_bytes()


def test_background_color_and_intrinsic_width_are_preserved() -> None:
    color = bytes.fromhex("123456")
    intrinsic = colored_pixels(
        render(width=11, color="#123456"), color
    )
    full = colored_pixels(render(width=90, color="#123456"), color)
    payload = render(color="#123456").to_bytes()

    assert full == frozenset((x + 39, y) for x, y in intrinsic)
    assert color in {
        payload[offset : offset + 3]
        for offset in range(0, len(payload), 3)
    }
    assert BACKGROUND.to_bytes() in {
        payload[offset : offset + 3]
        for offset in range(0, len(payload), 3)
    }


def test_rendering_is_deterministic_and_ignores_animation_time() -> None:
    first = render(content="STATIC")
    second = render(content="STATIC")

    assert first.to_bytes() == second.to_bytes()


def test_exact_settings_type_and_short_regions_fail_safely() -> None:
    class DerivedSettings(StaticTextSettings):
        pass

    derived = DerivedSettings(
        "AB",
        "#FFFFFF",
        HorizontalAlignment.CENTER,
        VerticalAlignment.CENTER,
        FontChoice.LEGACY_5X7,
        True,
        TextOverflow.ERROR,
    )
    with pytest.raises(WidgetRenderingError) as wrong_type:
        render_static_text(
            RegionSurface(90, 10), derived, context(90, 10)
        )
    with pytest.raises(WidgetRenderingError) as too_tall:
        render_static_text(
            RegionSurface(90, 5),
            settings(font=FontChoice.LEGACY_5X7),
            context(90, 5),
        )

    assert wrong_type.value.category == "renderer_invariant"
    assert wrong_type.value.field == "settings"
    assert too_tall.value.category == "content_too_tall"
    assert too_tall.value.field == "font"


def test_translated_font_failure_is_fully_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecretFontError(FontTextError):
        def __init__(self) -> None:
            self.category = "unsupported_glyph"
            self.field = "content"
            self.font_id = None
            Exception.__init__(self, SECRET_MARKER)

    original = SecretFontError()

    def fail(*args: object, **kwargs: object) -> object:
        raise original

    monkeypatch.setattr(static_module, "resolve_font", fail)
    with pytest.raises(WidgetRenderingError) as raised:
        render_static_text(
            RegionSurface(90, 10), settings(), context(90, 10)
        )

    assert_sanitized_error(
        raised.value,
        category="unsupported_glyph",
        field="content",
        original=original,
    )


def test_unexpected_failure_is_fully_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError(SECRET_MARKER)

    def fail(*args: object, **kwargs: object) -> object:
        raise original

    monkeypatch.setattr(
        static_module, "create_builtin_font_registry", fail
    )
    with pytest.raises(WidgetRenderingError) as raised:
        render_static_text(
            RegionSurface(90, 10), settings(), context(90, 10)
        )

    assert_sanitized_error(
        raised.value,
        category="unexpected_renderer_failure",
        field="renderer",
        original=original,
    )
