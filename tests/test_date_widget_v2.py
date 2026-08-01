"""Focused tests for the inactive Phase 3B German Date renderer."""

from __future__ import annotations

import locale
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.fonts import (
    FontTextError,
    create_builtin_font_registry,
)
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets import date as date_module
from nextuxlinq.rendering_v2.widgets.date import (
    GERMAN_WEEKDAY_ABBREVIATIONS,
    format_date,
    render_date,
    resolve_date_font,
)
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.widget_models import (
    DateFormat,
    DateSettings,
    FontChoice,
    HorizontalAlignment,
    TextOverflow,
    VerticalAlignment,
)

NOW = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)
BACKGROUND = RgbColor(7, 8, 9)
WHITE = bytes.fromhex("FFFFFF")
SECRET_MARKER = "PHASE3B_SECRET_DATE_9C2E"


def settings(**changes: object) -> DateSettings:
    values = {
        "format": DateFormat.DAY_MONTH_FULL_YEAR,
        "time_zone": "local",
        "color": "#FFFFFF",
        "horizontal_alignment": HorizontalAlignment.CENTER,
        "vertical_alignment": VerticalAlignment.CENTER,
        "font": FontChoice.AUTO,
        "overflow": TextOverflow.ERROR,
        **changes,
    }
    return DateSettings(**values)  # type: ignore[arg-type]


def context(
    width: int,
    height: int,
    *,
    wall_clock: datetime = NOW,
) -> RenderContext:
    return RenderContext(
        wall_clock=wall_clock,
        animation_time=0.0,
        canvas_width=90,
        canvas_height=10,
        region_id="date",
        region_width=width,
        region_height=height,
    )


def render(
    *,
    width: int = 90,
    height: int = 10,
    wall_clock: datetime = NOW,
    initial_color: RgbColor = BACKGROUND,
    **changes: object,
) -> RegionSurface:
    surface = RegionSurface(width, height, initial_color)
    assert (
        render_date(
            surface,
            settings(**changes),
            context(width, height, wall_clock=wall_clock),
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
    prohibited: tuple[str, ...],
) -> None:
    assert error.category == category
    assert error.field == field
    assert error.__context__ is None
    assert error.__cause__ is None

    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None:
        assert current not in chain
        chain.append(current)
        current = (
            current.__cause__
            if current.__cause__ is not None
            else current.__context__
        )
    assert chain == [error]
    assert all(value is not original for value in vars(error).values())
    assert all(value is not original for value in error.args)
    assert not any(
        isinstance(value, BaseException)
        for value in (*vars(error).values(), *error.args)
    )

    formatted = "".join(traceback.format_exception(error))
    for value in prohibited:
        assert value not in str(error)
        assert value not in repr(error)
        assert value not in formatted


@pytest.mark.parametrize(
    ("date_format", "expected"),
    [
        (DateFormat.DAY_MONTH_FULL_YEAR, "29.07.2026"),
        (DateFormat.DAY_MONTH_SHORT_YEAR, "29.07.26"),
        (DateFormat.DAY_MONTH, "29.07."),
        (DateFormat.WEEKDAY_DAY_MONTH, "MI. 29.07."),
    ],
)
def test_every_german_date_format(
    date_format: DateFormat, expected: str
) -> None:
    assert format_date(NOW, date_format) == expected


def test_every_fixed_german_weekday_label() -> None:
    monday = datetime(2026, 7, 27, tzinfo=timezone.utc)

    assert GERMAN_WEEKDAY_ABBREVIATIONS == (
        "MO.",
        "DI.",
        "MI.",
        "DO.",
        "FR.",
        "SA.",
        "SO.",
    )
    assert tuple(
        format_date(
            monday + timedelta(days=offset),
            DateFormat.WEEKDAY_DAY_MONTH,
        ).split(" ", 1)[0]
        for offset in range(7)
    ) == GERMAN_WEEKDAY_ABBREVIATIONS


def test_date_defaults_are_german_full_year_centered_and_erroring() -> None:
    default = DateSettings()

    assert default == settings()
    assert format_date(NOW, default.format) == "29.07.2026"
    assert default.to_dict() == {
        "format": "day_month_full_year",
        "time_zone": "local",
        "color": "#FFFFFF",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "font": "auto",
        "overflow": "error",
    }


@pytest.mark.parametrize(
    "removed",
    [
        "iso",
        "day_month_year",
        "month_day_year",
        "day_month_short",
        "YYYY-MM-DD",
        "MM/DD/YYYY",
        "%Y-%m-%d",
    ],
)
def test_removed_and_arbitrary_date_formats_fail_strictly(
    removed: str,
) -> None:
    with pytest.raises(ValueError):
        DateSettings.from_dict(
            {
                **settings().to_dict(),
                "format": removed,
            }
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2024, 2, 29, tzinfo=timezone.utc),
            "29.02.2024",
        ),
        (
            datetime(2027, 1, 1, tzinfo=timezone.utc),
            "01.01.2027",
        ),
        (
            datetime(2026, 12, 31, tzinfo=timezone.utc),
            "31.12.2026",
        ),
    ],
)
def test_leap_day_and_year_boundaries(
    value: datetime, expected: str
) -> None:
    assert (
        format_date(value, DateFormat.DAY_MONTH_FULL_YEAR) == expected
    )


def test_timezone_conversion_precedes_date_formatting() -> None:
    value = datetime(2026, 7, 29, 23, 30, tzinfo=timezone.utc)
    named = render(
        wall_clock=value,
        time_zone="Europe/Berlin",
        font=FontChoice.COMPACT_3X5,
    )
    next_day = render(
        wall_clock=datetime(
            2026, 7, 30, 1, 30, tzinfo=timezone.utc
        ),
        time_zone="local",
        font=FontChoice.COMPACT_3X5,
    )

    assert named.to_bytes() == next_day.to_bytes()


def test_unavailable_timezone_is_sanitized_and_does_not_draw() -> None:
    surface = RegionSurface(90, 10, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(
            surface,
            settings(time_zone="Unavailable/Phase3B"),
            context(90, 10),
        )

    assert raised.value.category == "time_zone_unavailable"
    assert raised.value.field == "time_zone"
    assert surface.to_bytes() == before
    assert "Unavailable" not in str(raised.value)


def test_formatting_is_independent_of_process_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> str:
        raise AssertionError("locale access is forbidden")

    monkeypatch.setattr(locale, "getlocale", forbidden)
    monkeypatch.setattr(locale, "setlocale", forbidden)
    monkeypatch.setattr(locale, "nl_langinfo", forbidden)

    assert (
        format_date(NOW, DateFormat.WEEKDAY_DAY_MONTH)
        == "MI. 29.07."
    )
    render(format=DateFormat.WEEKDAY_DAY_MONTH)


@pytest.mark.parametrize(
    "font",
    [
        FontChoice.COMPACT_3X5,
        FontChoice.LEGACY_5X7,
        FontChoice.NATIVE_4X9,
        FontChoice.NATIVE_5X9,
        FontChoice.NATIVE_5X10,
    ],
)
def test_supported_explicit_fonts(font: FontChoice) -> None:
    assert colored_pixels(render(font=font))


def test_unsupported_explicit_font_is_controlled() -> None:
    with pytest.raises(WidgetRenderingError) as raised:
        render(font=FontChoice.LEGACY_3X7)

    assert raised.value.category == "unsupported_glyph"
    assert raised.value.field == "font"


def test_auto_font_uses_largest_fitting_native_font() -> None:
    assert render(font=FontChoice.AUTO).to_bytes() == render(
        font=FontChoice.NATIVE_5X10
    ).to_bytes()
    assert resolve_date_font(
        create_builtin_font_registry(),
        settings(font=FontChoice.AUTO),
        region_width=90,
        region_height=10,
    ).font_id is FontChoice.NATIVE_5X10


def test_five_pixel_row_supports_day_and_month_when_width_permits() -> None:
    surface = render(
        width=19,
        height=5,
        format=DateFormat.DAY_MONTH,
        font=FontChoice.AUTO,
    )

    assert colored_pixels(surface)
    assert surface.to_bytes() == render(
        width=19,
        height=5,
        format=DateFormat.DAY_MONTH,
        font=FontChoice.COMPACT_3X5,
    ).to_bytes()


def test_error_overflow_preflights_widest_complete_output() -> None:
    surface = RegionSurface(34, 5, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(
            surface,
            settings(
                font=FontChoice.COMPACT_3X5,
                overflow=TextOverflow.ERROR,
            ),
            context(34, 5),
        )

    assert raised.value.category == "content_too_wide"
    assert surface.to_bytes() == before


def test_truncate_draws_only_a_complete_prefix() -> None:
    truncated = colored_pixels(
        render(
            width=10,
            height=5,
            font=FontChoice.COMPACT_3X5,
            overflow=TextOverflow.TRUNCATE,
            horizontal_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    two_digits = colored_pixels(
        render(
            width=10,
            height=5,
            wall_clock=datetime(
                2026, 7, 29, tzinfo=timezone.utc
            ),
            format=DateFormat.DAY_MONTH,
            font=FontChoice.COMPACT_3X5,
            overflow=TextOverflow.TRUNCATE,
            horizontal_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )

    assert truncated
    assert truncated == two_digits
    assert max(x for x, _ in truncated) <= 8


def test_clip_uses_full_text_alignment_and_region_clipping() -> None:
    clipped = render(
        width=10,
        height=7,
        font=FontChoice.LEGACY_5X7,
        overflow=TextOverflow.CLIP,
    )

    assert colored_pixels(clipped)
    assert clipped.to_bytes() != render(
        width=10,
        height=7,
        font=FontChoice.LEGACY_5X7,
        overflow=TextOverflow.TRUNCATE,
    ).to_bytes()


def test_truncate_and_clip_auto_selection_do_not_shrink_for_width() -> None:
    for overflow in (TextOverflow.TRUNCATE, TextOverflow.CLIP):
        automatic = render(
            width=20,
            height=7,
            overflow=overflow,
            font=FontChoice.AUTO,
        )
        explicit = render(
            width=20,
            height=7,
            overflow=overflow,
            font=FontChoice.LEGACY_5X7,
        )
        assert automatic.to_bytes() == explicit.to_bytes()


@pytest.mark.parametrize(
    ("alignment", "expected_shift"),
    [
        (HorizontalAlignment.START, 0),
        (HorizontalAlignment.CENTER, 19),
        (HorizontalAlignment.END, 39),
    ],
)
def test_horizontal_alignments(
    alignment: HorizontalAlignment, expected_shift: int
) -> None:
    start = colored_pixels(
        render(
            width=90,
            font=FontChoice.LEGACY_5X7,
            horizontal_alignment=HorizontalAlignment.START,
        )
    )
    actual = colored_pixels(
        render(
            width=90,
            font=FontChoice.LEGACY_5X7,
            horizontal_alignment=alignment,
        )
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
        render(
            vertical_alignment=VerticalAlignment.TOP,
            font=FontChoice.LEGACY_5X7,
        )
    )
    actual = colored_pixels(render(
        vertical_alignment=alignment,
        font=FontChoice.LEGACY_5X7,
    ))

    assert actual == frozenset(
        (x, y + expected_shift) for x, y in top
    )


def test_weekday_format_preflights_every_german_weekday_width() -> None:
    wednesday = datetime(2026, 7, 29, tzinfo=timezone.utc)
    surface = RegionSurface(44, 7, BACKGROUND)

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(
            surface,
            settings(
                format=DateFormat.WEEKDAY_DAY_MONTH,
                font=FontChoice.LEGACY_5X7,
            ),
            context(44, 7, wall_clock=wednesday),
        )

    assert raised.value.category == "content_too_wide"
    assert surface.to_bytes() == RegionSurface(
        44, 7, BACKGROUND
    ).to_bytes()


def test_full_width_date_is_intrinsic_centered_and_not_scaled() -> None:
    full = colored_pixels(
        render(width=90, font=FontChoice.LEGACY_5X7)
    )
    intrinsic = colored_pixels(
        render(width=51, font=FontChoice.LEGACY_5X7)
    )

    assert full == frozenset((x + 19, y) for x, y in intrinsic)


def test_date_output_is_deterministic() -> None:
    assert render(
        wall_clock=NOW,
        format=DateFormat.WEEKDAY_DAY_MONTH,
    ).to_bytes() == render(
        wall_clock=NOW,
        format=DateFormat.WEEKDAY_DAY_MONTH,
    ).to_bytes()


def test_date_uses_color_and_preserves_region_background() -> None:
    color = bytes.fromhex("123456")
    surface = render(color="#123456")
    payload = surface.to_bytes()
    pixels = {
        payload[offset : offset + 3]
        for offset in range(0, len(payload), 3)
    }

    assert color in pixels
    assert BACKGROUND.to_bytes() in pixels


def test_date_rejects_content_too_tall_without_partial_drawing() -> None:
    surface = RegionSurface(90, 4, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(surface, settings(), context(90, 4))

    assert raised.value.category == "content_too_tall"
    assert raised.value.field == "font"
    assert surface.to_bytes() == before


def test_exact_settings_type_is_required() -> None:
    class DerivedDateSettings(DateSettings):
        pass

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(
            RegionSurface(90, 10),
            DerivedDateSettings(),
            context(90, 10),
        )

    assert raised.value.category == "renderer_invariant"
    assert raised.value.field == "settings"


def test_date_format_unavailable_error_is_stable() -> None:
    with pytest.raises(WidgetRenderingError) as raised:
        format_date(NOW, object())  # type: ignore[arg-type]

    assert raised.value.category == "date_format_unavailable"
    assert raised.value.field == "format"


def test_translated_date_font_failure_drops_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecretFontError(FontTextError):
        def __init__(self) -> None:
            self.category = "content_too_wide"
            self.field = "content"
            self.font_id = None
            Exception.__init__(self, SECRET_MARKER)

    original = SecretFontError()

    class FailingFontRegistry:
        def resolve(self, font_id: FontChoice) -> object:
            raise original

    monkeypatch.setattr(
        date_module,
        "create_builtin_font_registry",
        lambda: FailingFontRegistry(),
    )

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(
            RegionSurface(90, 10),
            settings(font=FontChoice.COMPACT_3X5),
            context(90, 10),
        )

    assert_sanitized_error(
        raised.value,
        category="content_too_wide",
        field="content",
        original=original,
        prohibited=("SecretFontError", SECRET_MARKER),
    )


def test_unexpected_date_failure_drops_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError(SECRET_MARKER)

    def fail(*args: object) -> datetime:
        raise original

    monkeypatch.setattr(date_module, "resolve_wall_clock", fail)

    with pytest.raises(WidgetRenderingError) as raised:
        render_date(RegionSurface(90, 10), settings(), context(90, 10))

    assert_sanitized_error(
        raised.value,
        category="unexpected_renderer_failure",
        field="renderer",
        original=original,
        prohibited=("RuntimeError", SECRET_MARKER),
    )


def test_renderers_do_not_contain_system_clock_or_locale_calls() -> None:
    root = (
        Path(__file__).parents[1]
        / "src"
        / "nextuxlinq"
        / "rendering_v2"
        / "widgets"
    )
    source = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("clock.py", "date.py", "timezone.py")
    )

    for forbidden in (
        "datetime.now(",
        "time.time(",
        "time.monotonic(",
        "strftime(",
        "setlocale(",
        "getlocale(",
        "nl_langinfo(",
    ):
        assert forbidden not in source
