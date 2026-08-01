"""Focused tests for the inactive Phase 3B Clock renderer."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from nextuxlinq.defaults import default_configuration
from nextuxlinq.migration import migrate_configuration_v1_to_v2
from nextuxlinq.rendering import render_profile_frame
from nextuxlinq.rendering_v2.fonts import (
    FontRegistry,
    FontTextError,
    create_builtin_font_registry,
    measure_normalized_text,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets import clock as clock_module
from nextuxlinq.rendering_v2.widgets import timezone as timezone_module
from nextuxlinq.rendering_v2.widgets.clock import (
    clock_separator_is_visible,
    format_clock,
    render_clock,
    resolve_clock_font,
)
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.timezone import resolve_wall_clock
from nextuxlinq.widget_models import (
    ClockFormat,
    ClockSettings,
    FontChoice,
    HorizontalAlignment,
    VerticalAlignment,
)

NOW = datetime(2026, 7, 29, 1, 5, 9, tzinfo=timezone.utc)
BACKGROUND = RgbColor(7, 8, 9)
WHITE = bytes.fromhex("FFFFFF")
SECRET_MARKER = "PHASE3B_SECRET_CLOCK_7F4A"


def settings(**changes: object) -> ClockSettings:
    values = {
        "format": ClockFormat.HOUR_24,
        "show_seconds": False,
        "blink_separator": False,
        "time_zone": "local",
        "color": "#FFFFFF",
        "horizontal_alignment": HorizontalAlignment.CENTER,
        "vertical_alignment": VerticalAlignment.CENTER,
        "font": FontChoice.COMPACT_3X5,
        **changes,
    }
    return ClockSettings(**values)  # type: ignore[arg-type]


def context(
    width: int,
    height: int,
    *,
    wall_clock: datetime = NOW,
    animation_time: float = 0.0,
) -> RenderContext:
    return RenderContext(
        wall_clock=wall_clock,
        animation_time=animation_time,
        canvas_width=90,
        canvas_height=10,
        region_id="clock",
        region_width=width,
        region_height=height,
    )


def render(
    *,
    width: int = 90,
    height: int = 10,
    wall_clock: datetime = NOW,
    animation_time: float = 0.0,
    initial_color: RgbColor = BACKGROUND,
    **changes: object,
) -> RegionSurface:
    surface = RegionSurface(width, height, initial_color)
    assert (
        render_clock(
            surface,
            settings(**changes),
            context(
                width,
                height,
                wall_clock=wall_clock,
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
    ("value", "clock_format", "show_seconds", "expected"),
    [
        (datetime(2026, 7, 29, 1, 5), ClockFormat.HOUR_24, False, "01:05"),
        (datetime(2026, 7, 29, 1, 5, 9), ClockFormat.HOUR_24, True, "01:05:09"),
        (datetime(2026, 7, 29, 1, 5), ClockFormat.HOUR_12, False, "1:05"),
        (datetime(2026, 7, 29, 1, 5, 9), ClockFormat.HOUR_12, True, "1:05:09"),
        (datetime(2026, 7, 29, 0, 0), ClockFormat.HOUR_24, False, "00:00"),
        (datetime(2026, 7, 29, 0, 0), ClockFormat.HOUR_12, False, "12:00"),
        (datetime(2026, 7, 29, 12, 0), ClockFormat.HOUR_12, False, "12:00"),
        (datetime(2026, 7, 29, 13, 5), ClockFormat.HOUR_12, False, "1:05"),
    ],
)
def test_clock_formatter_rules(
    value: datetime,
    clock_format: ClockFormat,
    show_seconds: bool,
    expected: str,
) -> None:
    result = format_clock(
        value, clock_format, show_seconds=show_seconds
    )

    assert result == expected
    assert "AM" not in result
    assert "PM" not in result


def test_local_time_zone_returns_injected_value_unchanged() -> None:
    value = datetime(
        2026, 7, 29, 23, 45, tzinfo=timezone.utc
    ).astimezone(timezone.utc)
    surface = render(wall_clock=value, time_zone="local")

    assert resolve_wall_clock(value, "local") is value
    assert surface.to_bytes() == render(
        wall_clock=value, time_zone="UTC"
    ).to_bytes()


def test_named_time_zone_conversion_and_day_boundary() -> None:
    value = datetime(2026, 7, 29, 23, 5, tzinfo=timezone.utc)

    named = render(wall_clock=value, time_zone="Europe/Berlin")
    expected = render(
        wall_clock=datetime(
            2026, 7, 30, 1, 5, tzinfo=timezone.utc
        ),
        time_zone="local",
    )

    assert named.to_bytes() == expected.to_bytes()


def test_unavailable_time_zone_is_sanitized_and_does_not_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_zone = "Unavailable/Phase3B"
    original = ZoneInfoNotFoundError(
        f"{SECRET_MARKER}:{unavailable_zone}"
    )

    def unavailable(key: str) -> object:
        assert key == unavailable_zone
        raise original

    monkeypatch.setattr(timezone_module, "ZoneInfo", unavailable)
    surface = RegionSurface(90, 10, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_clock(
            surface,
            settings(time_zone=unavailable_zone),
            context(90, 10),
        )

    assert_sanitized_error(
        raised.value,
        category="time_zone_unavailable",
        field="time_zone",
        original=original,
        prohibited=(
            "ZoneInfoNotFoundError",
            SECRET_MARKER,
            unavailable_zone,
        ),
    )
    assert vars(raised.value) == {
        "category": "time_zone_unavailable",
        "field": "time_zone",
    }
    assert surface.to_bytes() == before


@pytest.mark.parametrize(
    ("animation_time", "expected"),
    [
        (0.0, True),
        (0.499999, True),
        (0.5, False),
        (0.999999, False),
        (1.0, True),
        (1.5, False),
    ],
)
def test_blink_separator_exact_phase_boundaries(
    animation_time: float, expected: bool
) -> None:
    assert clock_separator_is_visible(
        animation_time, blink_separator=True
    ) is expected
    assert clock_separator_is_visible(
        animation_time, blink_separator=False
    )


def test_hidden_separator_retains_layout_and_both_colons_blink() -> None:
    visible = render(
        animation_time=0.0,
        blink_separator=True,
        show_seconds=True,
    )
    hidden = render(
        animation_time=0.5,
        blink_separator=True,
        show_seconds=True,
    )
    steady = render(
        animation_time=0.5,
        blink_separator=False,
        show_seconds=True,
    )
    visible_pixels = colored_pixels(visible)
    hidden_pixels = colored_pixels(hidden)

    assert visible.to_bytes() == steady.to_bytes()
    assert hidden_pixels < visible_pixels
    assert len(visible_pixels - hidden_pixels) == 4
    assert min(x for x, _ in hidden_pixels) == min(
        x for x, _ in visible_pixels
    )
    assert max(x for x, _ in hidden_pixels) == max(
        x for x, _ in visible_pixels
    )


@pytest.mark.parametrize(
    ("alignment", "expected_shift"),
    [
        (HorizontalAlignment.START, 0),
        (HorizontalAlignment.CENTER, 6),
        (HorizontalAlignment.END, 13),
    ],
)
def test_horizontal_alignment(
    alignment: HorizontalAlignment, expected_shift: int
) -> None:
    start = colored_pixels(
        render(
            width=30,
            height=5,
            horizontal_alignment=HorizontalAlignment.START,
        )
    )
    actual = colored_pixels(
        render(
            width=30,
            height=5,
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
        (VerticalAlignment.CENTER, 2),
        (VerticalAlignment.BOTTOM, 5),
    ],
)
def test_vertical_alignment(
    alignment: VerticalAlignment, expected_shift: int
) -> None:
    top = colored_pixels(
        render(vertical_alignment=VerticalAlignment.TOP)
    )
    actual = colored_pixels(render(vertical_alignment=alignment))

    assert actual == frozenset(
        (x, y + expected_shift) for x, y in top
    )


@pytest.mark.parametrize(
    ("font", "height"),
    [
        (FontChoice.COMPACT_3X5, 5),
        (FontChoice.LEGACY_3X7, 7),
        (FontChoice.LEGACY_5X7, 7),
        (FontChoice.NATIVE_4X9, 9),
        (FontChoice.NATIVE_5X9, 9),
        (FontChoice.NATIVE_5X10, 10),
    ],
)
def test_explicit_fonts_render_at_intrinsic_height(
    font: FontChoice, height: int
) -> None:
    pixels = colored_pixels(
        render(
            horizontal_alignment=HorizontalAlignment.START,
            vertical_alignment=VerticalAlignment.TOP,
            font=font,
        )
    )

    assert min(y for _, y in pixels) == 0
    assert max(y for _, y in pixels) == height - 1


def test_auto_font_uses_largest_native_font() -> None:
    automatic = render(font=FontChoice.AUTO)
    explicit = render(font=FontChoice.NATIVE_5X10)

    assert automatic.to_bytes() == explicit.to_bytes()


def test_25_by_10_clock_resolves_true_5x10_without_clipping() -> None:
    chosen = resolve_clock_font(
        create_builtin_font_registry(),
        settings(font=FontChoice.AUTO),
        region_width=25,
        region_height=10,
    )
    surface = render(width=25, height=10, font=FontChoice.AUTO)
    assert chosen.font_id is FontChoice.NATIVE_5X10
    assert chosen.glyph_height == 10
    assert measure_normalized_text(chosen, "23:59").width == 25
    assert max(y for _, y in colored_pixels(surface)) == 9
    assert colored_pixels(surface) == colored_pixels(render(
        width=25, height=10, font=FontChoice.NATIVE_5X10,
    ))


def test_24_by_10_clock_uses_largest_font_that_fully_fits() -> None:
    chosen = resolve_clock_font(
        create_builtin_font_registry(),
        settings(font=FontChoice.AUTO),
        region_width=24,
        region_height=10,
    )
    assert chosen.font_id is FontChoice.NATIVE_4X9


def test_explicit_5x10_clock_seconds_require_39_pixels() -> None:
    with pytest.raises(WidgetRenderingError) as raised:
        render(
            width=38,
            height=10,
            font=FontChoice.NATIVE_5X10,
            show_seconds=True,
        )
    assert raised.value.category == "content_too_wide"
    render(
        width=39,
        height=10,
        font=FontChoice.NATIVE_5X10,
        show_seconds=True,
    )


def test_auto_font_uses_widest_configured_output_not_current_12h_time() -> None:
    surface = RegionSurface(16, 5, BACKGROUND)

    with pytest.raises(WidgetRenderingError) as raised:
        render_clock(
            surface,
            settings(format=ClockFormat.HOUR_12),
            context(16, 5),
        )

    assert raised.value.category == "content_too_wide"


@pytest.mark.parametrize(
    ("width", "height", "category"),
    [
        (90, 4, "content_too_tall"),
        (16, 10, "content_too_wide"),
    ],
)
def test_clock_rejects_regions_that_cannot_fit_complete_output(
    width: int, height: int, category: str
) -> None:
    surface = RegionSurface(width, height, BACKGROUND)
    before = surface.to_bytes()

    with pytest.raises(WidgetRenderingError) as raised:
        render_clock(surface, settings(font=FontChoice.AUTO), context(width, height))

    assert raised.value.category == category
    assert surface.to_bytes() == before


def test_full_width_clock_is_intrinsic_centered_and_not_stretched() -> None:
    full = colored_pixels(
        render(
            width=90,
            height=10,
            font=FontChoice.LEGACY_5X7,
        )
    )
    local = colored_pixels(
        render(
            width=25,
            height=10,
            font=FontChoice.LEGACY_5X7,
        )
    )

    assert full == frozenset((x + 32, y) for x, y in local)


def test_clock_output_is_deterministic() -> None:
    first = render(
        wall_clock=NOW,
        animation_time=19.25,
        blink_separator=True,
        show_seconds=True,
    )
    second = render(
        wall_clock=NOW,
        animation_time=19.25,
        blink_separator=True,
        show_seconds=True,
    )

    assert first.to_bytes() == second.to_bytes()


def test_clock_uses_color_and_preserves_region_background() -> None:
    color = bytes.fromhex("123456")
    surface = render(color="#123456")
    payload = surface.to_bytes()
    pixels = {
        payload[offset : offset + 3]
        for offset in range(0, len(payload), 3)
    }

    assert color in pixels
    assert BACKGROUND.to_bytes() in pixels


def _legacy_region_bytes(
    payload: bytes, *, region_x: int, region_width: int
) -> bytes:
    result = bytearray()
    for y in range(10):
        raw_x = 90 - region_x - region_width
        start = (y * 90 + raw_x) * 3
        result.extend(payload[start : start + region_width * 3])
    return bytes(result)


@pytest.mark.parametrize(
    ("region_index", "region_x", "region_width"),
    [
        (0, 0, 24),
        (2, 59, 31),
    ],
)
def test_migrated_clock_is_exactly_legacy_equivalent(
    region_index: int, region_x: int, region_width: int
) -> None:
    source = default_configuration()
    migrated = migrate_configuration_v1_to_v2(source)
    region = migrated.active.regions[region_index]
    assert type(region.widget.settings) is ClockSettings
    surface = RegionSurface(region_width, 10)
    wall_clock = datetime(
        2026, 7, 29, 1, 5, tzinfo=timezone.utc
    )

    render_clock(
        surface,
        region.widget.settings,
        context(region_width, 10, wall_clock=wall_clock),
    )
    active = render_profile_frame(
        source.active,
        wall_clock,
        scroll_offset=0,
    ).to_bytes()

    assert surface.to_bytes() == _legacy_region_bytes(
        active, region_x=region_x, region_width=region_width
    )


def test_exact_settings_type_is_required() -> None:
    class DerivedClockSettings(ClockSettings):
        pass

    derived = DerivedClockSettings(
        ClockFormat.HOUR_24,
        False,
        False,
        "local",
        "#FFFFFF",
        HorizontalAlignment.CENTER,
        VerticalAlignment.CENTER,
        FontChoice.COMPACT_3X5,
    )

    with pytest.raises(WidgetRenderingError) as raised:
        render_clock(RegionSurface(90, 10), derived, context(90, 10))

    assert raised.value.category == "renderer_invariant"
    assert raised.value.field == "settings"


def test_unknown_font_and_unsupported_glyph_are_controlled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clock_module, "create_builtin_font_registry", lambda: FontRegistry(())
    )

    with pytest.raises(WidgetRenderingError) as unknown:
        render_clock(
            RegionSurface(90, 10),
            settings(font=FontChoice.COMPACT_3X5),
            context(90, 10),
        )
    with pytest.raises(WidgetRenderingError) as unsupported:
        render_clock(
            RegionSurface(90, 10),
            settings(font=FontChoice.AUTO),
            context(90, 10),
        )

    assert unknown.value.category == "unknown_font"
    assert unsupported.value.category == "unsupported_glyph"


def test_translated_clock_font_failure_drops_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecretFontError(FontTextError):
        def __init__(self) -> None:
            self.category = "unsupported_glyph"
            self.field = "font"
            self.font_id = None
            Exception.__init__(self, SECRET_MARKER)

    original = SecretFontError()

    class FailingFontRegistry:
        def resolve(self, font_id: FontChoice) -> object:
            raise original

    monkeypatch.setattr(
        clock_module,
        "create_builtin_font_registry",
        lambda: FailingFontRegistry(),
    )

    with pytest.raises(WidgetRenderingError) as raised:
        render_clock(
            RegionSurface(90, 10),
            settings(font=FontChoice.COMPACT_3X5),
            context(90, 10),
        )

    assert_sanitized_error(
        raised.value,
        category="unsupported_glyph",
        field="font",
        original=original,
        prohibited=("SecretFontError", SECRET_MARKER),
    )


def test_unexpected_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError(SECRET_MARKER)

    def fail(*args: object) -> datetime:
        raise original

    monkeypatch.setattr(clock_module, "resolve_wall_clock", fail)

    with pytest.raises(WidgetRenderingError) as raised:
        render_clock(RegionSurface(90, 10), settings(), context(90, 10))

    assert_sanitized_error(
        raised.value,
        category="unexpected_renderer_failure",
        field="renderer",
        original=original,
        prohibited=("RuntimeError", SECRET_MARKER),
    )
