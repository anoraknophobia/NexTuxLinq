"""Tests for the parallel free-layout schema-v2 domain model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nextuxlinq.models_v2 import (
    MAX_REGIONS_PER_DOCUMENT,
    MAX_REGIONS_PER_PROFILE,
    SCHEMA_VERSION_V2,
    CanvasConfig,
    ConfigurationDocumentV2,
    ProfileConfigV2,
    RegionConfig,
)
from nextuxlinq.widget_models import (
    MAX_TIME_ZONE_LENGTH,
    MAX_TRAILING_GAP,
    BlankSettings,
    ClockFormat,
    ClockSettings,
    DateFormat,
    DateSettings,
    FontChoice,
    HorizontalAlignment,
    ScrollDirection,
    ScrollingTextSettings,
    StaticTextSettings,
    TextOverflow,
    VerticalAlignment,
    WidgetConfig,
    WidgetType,
)


def blank_widget() -> WidgetConfig:
    return WidgetConfig(WidgetType.BLANK, BlankSettings())


def region(
    region_id: str = "region-1",
    **changes: object,
) -> RegionConfig:
    values = {
        "id": region_id,
        "name": None,
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
        "enabled": True,
        "background_color": None,
        "widget": blank_widget(),
        **changes,
    }
    return RegionConfig(**values)  # type: ignore[arg-type]


def profile(
    regions: tuple[RegionConfig, ...] = (),
    **changes: object,
) -> ProfileConfigV2:
    values = {
        "brightness": None,
        "canvas": CanvasConfig(90, 10, "#000000"),
        "regions": regions,
        **changes,
    }
    return ProfileConfigV2(**values)  # type: ignore[arg-type]


def document(
    profiles: dict[str, ProfileConfigV2] | None = None,
    **changes: object,
) -> ConfigurationDocumentV2:
    values = {
        "schema_version": SCHEMA_VERSION_V2,
        "active_profile": "default",
        "profiles": {"default": profile()} if profiles is None else profiles,
        **changes,
    }
    return ConfigurationDocumentV2(**values)  # type: ignore[arg-type]


def region_dict(**changes: object) -> dict[str, object]:
    values = region().to_dict()
    values.update(changes)
    return values


def profile_dict(regions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "brightness": None,
        "canvas": {
            "width": 90,
            "height": 10,
            "background_color": "#000000",
        },
        "regions": regions,
    }


def clock_settings_dict(**changes: object) -> dict[str, object]:
    values = {
        "format": "24h",
        "show_seconds": False,
        "blink_separator": False,
        "time_zone": "local",
        "color": "#FFFFFF",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "font": "auto",
    }
    values.update(changes)
    return values


def static_text_settings_dict(**changes: object) -> dict[str, object]:
    values = {
        "content": "TEXT",
        "color": "#FFFFFF",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "font": "auto",
        "uppercase": True,
        "overflow": "error",
    }
    values.update(changes)
    return values


def scrolling_text_settings_dict(**changes: object) -> dict[str, object]:
    values = {
        "content": "TEXT",
        "direction": "left",
        "speed": 5.0,
        "color": "#FFFFFF",
        "initial_alignment": "end",
        "vertical_alignment": "center",
        "font": "auto",
        "uppercase": True,
        "trailing_gap": 6,
    }
    values.update(changes)
    return values


def date_settings_dict(**changes: object) -> dict[str, object]:
    values = {
        "format": "day_month_full_year",
        "time_zone": "local",
        "color": "#FFFFFF",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "font": "auto",
        "overflow": "error",
    }
    values.update(changes)
    return values


def test_empty_layout_and_unused_canvas_pixels_are_valid() -> None:
    configuration = document()

    assert configuration.active.regions == ()
    assert configuration.active.canvas == CanvasConfig(90, 10, "#000000")


def test_one_full_canvas_region_is_valid() -> None:
    full = region(width=90, height=10)

    assert profile((full,)).regions == (full,)


def test_horizontal_regions_may_leave_gaps() -> None:
    regions = (
        region("left", width=30, height=10),
        region("middle", x=31, width=30, height=10),
        region("right", x=62, width=28, height=10),
    )

    assert profile(regions).regions == regions


def test_two_five_pixel_rows_are_valid() -> None:
    regions = (
        region("top", width=90, height=5),
        region("bottom", y=5, width=90, height=5),
    )

    assert profile(regions).regions == regions


def test_mixed_horizontal_and_vertical_rectangles_are_valid() -> None:
    regions = (
        region("left", width=45, height=10),
        region("top-right", x=45, width=45, height=5),
        region("bottom-right", x=45, y=5, width=45, height=5),
    )

    assert profile(regions).regions == regions


def test_edge_touching_rectangles_are_valid() -> None:
    regions = (
        region("one", width=10, height=5),
        region("two", x=10, width=10, height=5),
        region("three", y=5, width=10, height=5),
    )

    assert profile(regions).regions == regions


@pytest.mark.parametrize(
    "regions",
    [
        (
            region("left", x=0, y=0, width=10, height=4),
            region("right", x=9, y=0, width=10, height=4),
        ),
        (
            region("top", x=0, y=0, width=10, height=5),
            region("bottom", x=0, y=4, width=10, height=5),
        ),
        (
            region("outer", x=0, y=0, width=20, height=10),
            region("inner", x=2, y=2, width=5, height=5),
        ),
        (
            region("one", x=1, y=1, width=10, height=5),
            region("two", x=1, y=1, width=10, height=5),
        ),
    ],
    ids=[
        "horizontal-partial",
        "vertical-partial",
        "containment",
        "identical",
    ],
)
def test_all_rectangle_intersection_shapes_are_rejected(
    regions: tuple[RegionConfig, RegionConfig],
) -> None:
    with pytest.raises(ValueError, match="overlap"):
        profile(regions)


@pytest.mark.parametrize(
    "regions",
    [
        (
            region("one", x=0, y=0, width=5, height=5),
            region("two", x=5, y=5, width=5, height=5),
        ),
        (
            region("one", x=0, y=0, width=5, height=5),
            region("two", x=5, y=0, width=5, height=5),
        ),
        (
            region("one", x=0, y=0, width=5, height=5),
            region("two", x=0, y=5, width=5, height=5),
        ),
        (
            region("one", x=0, y=0, width=5, height=5),
            region("two", x=6, y=0, width=5, height=5),
        ),
    ],
    ids=["corner-touching", "horizontal-edge", "vertical-edge", "unused-gap"],
)
def test_nonintersecting_rectangle_boundaries_are_accepted(
    regions: tuple[RegionConfig, RegionConfig],
) -> None:
    assert profile(regions).regions == regions


def test_overlap_result_is_independent_of_region_order() -> None:
    outer = region("outer", x=0, y=0, width=20, height=10)
    inner = region("inner", x=2, y=2, width=5, height=5)

    for regions in ((outer, inner), (inner, outer)):
        with pytest.raises(ValueError, match="overlap"):
            profile(regions)


@pytest.mark.parametrize(
    ("outer_enabled", "inner_enabled"),
    [(False, True), (True, False), (False, False)],
    ids=[
        "disabled-contains-enabled",
        "enabled-contains-disabled",
        "both-disabled",
    ],
)
def test_disabled_regions_reserve_contained_geometry(
    outer_enabled: bool,
    inner_enabled: bool,
) -> None:
    regions = (
        region(
            "outer",
            x=0,
            y=0,
            width=20,
            height=10,
            enabled=outer_enabled,
        ),
        region(
            "inner",
            x=2,
            y=2,
            width=5,
            height=5,
            enabled=inner_enabled,
        ),
    )

    with pytest.raises(ValueError, match="overlap"):
        profile(regions)


@pytest.mark.parametrize("enabled", [True, False])
def test_overlapping_regions_are_rejected_even_when_disabled(
    enabled: bool,
) -> None:
    regions = (
        region("one", width=10, height=10),
        region("two", x=9, width=10, height=10, enabled=enabled),
    )

    with pytest.raises(ValueError, match="overlap"):
        profile(regions)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("width", 0, ValueError),
        ("height", 0, ValueError),
        ("x", -1, ValueError),
        ("y", -1, ValueError),
        ("x", 1.5, TypeError),
        ("y", "0", TypeError),
        ("width", True, TypeError),
        ("height", False, TypeError),
    ],
)
def test_invalid_region_geometry_is_rejected(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        region(**{field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"x": 89, "width": 2},
        {"y": 9, "height": 2},
        {"width": 91},
        {"height": 11},
    ],
)
def test_out_of_bounds_geometry_is_rejected(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        region(**changes)


@pytest.mark.parametrize(
    ("width", "height"),
    [(89, 10), (90, 9), (True, 10), (90, False)],
)
def test_canvas_is_strictly_matrix900_sized(
    width: object,
    height: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        CanvasConfig(width, height, "#000000")  # type: ignore[arg-type]


def test_duplicate_region_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicated"):
        profile((region("same"), region("same", x=1)))


@pytest.mark.parametrize(
    "region_id",
    ["", "-leading", "_leading", "contains space", "slash/name", "a" * 65],
)
def test_unsafe_region_ids_are_rejected(region_id: str) -> None:
    with pytest.raises(ValueError, match="region id"):
        region(region_id)


def test_printable_duplicate_display_names_are_accepted() -> None:
    regions = (
        region("one", name="Same printable name"),
        region("two", x=1, name="Same printable name"),
    )

    assert [item.name for item in profile(regions).regions] == [
        "Same printable name",
        "Same printable name",
    ]


def test_normal_printable_and_unicode_display_names_are_accepted() -> None:
    assert region(name="Front clock").name == "Front clock"
    assert region(name="Überblick 東京").name == "Überblick 東京"


def test_optional_region_name_and_background_may_be_omitted() -> None:
    raw = region().to_dict()
    del raw["name"]
    del raw["background_color"]

    parsed = RegionConfig.from_dict(raw)

    assert parsed.name is None
    assert parsed.background_color is None


@pytest.mark.parametrize("name", [" leading", "trailing "])
def test_display_name_leading_or_trailing_whitespace_is_rejected(
    name: str,
) -> None:
    with pytest.raises(ValueError, match="leading or trailing"):
        region(name=name)


@pytest.mark.parametrize("name", ["bad\nname", "bad\u200bname", "\x00"])
def test_control_or_nonprinting_display_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="printable"):
        region(name=name)


def test_profile_region_limit_accepts_32_and_rejects_33() -> None:
    regions = tuple(
        region(f"region-{index}", x=index)
        for index in range(MAX_REGIONS_PER_PROFILE)
    )

    assert len(profile(regions).regions) == 32
    with pytest.raises(ValueError, match="at most 32"):
        profile(regions + (region("region-32", x=32),))


def test_document_region_limit_accepts_128_and_rejects_129() -> None:
    full_profiles = {
        f"profile{profile_index}": profile(
            tuple(
                region(f"region-{index}", x=index)
                for index in range(MAX_REGIONS_PER_PROFILE)
            )
        )
        for profile_index in range(4)
    }
    configuration = document(
        full_profiles,
        active_profile="profile0",
    )

    assert sum(len(item.regions) for item in configuration.profiles.values()) == (
        MAX_REGIONS_PER_DOCUMENT
    )
    over_limit = {
        **full_profiles,
        "extra": profile((region("extra"),)),
    }
    with pytest.raises(ValueError, match="at most 128"):
        document(over_limit, active_profile="profile0")


def test_colors_are_canonicalized() -> None:
    canvas = CanvasConfig(90, 10, "#aabbcc")
    colored = region(background_color="#123abc")
    clock = ClockSettings(
        format="24h",  # type: ignore[arg-type]
        show_seconds=False,
        blink_separator=False,
        time_zone="UTC",
        color="#abcdef",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
    )

    assert canvas.background_color == "#AABBCC"
    assert colored.background_color == "#123ABC"
    assert clock.color == "#ABCDEF"


def test_every_initial_widget_has_a_strict_typed_model() -> None:
    widgets = (
        WidgetConfig(WidgetType.BLANK, BlankSettings()),
        WidgetConfig(
            WidgetType.CLOCK,
            ClockSettings(
                ClockFormat.HOUR_24,
                False,
                False,
                "local",
                "#FFFFFF",
                HorizontalAlignment.CENTER,
                VerticalAlignment.CENTER,
                FontChoice.AUTO,
            ),
        ),
        WidgetConfig(
            WidgetType.STATIC_TEXT,
            StaticTextSettings(
                "TEXT",
                "#FFFFFF",
                HorizontalAlignment.CENTER,
                VerticalAlignment.CENTER,
                FontChoice.AUTO,
                True,
                TextOverflow.ERROR,
            ),
        ),
        WidgetConfig(
            WidgetType.SCROLLING_TEXT,
            ScrollingTextSettings(
                "TEXT",
                ScrollDirection.LEFT,
                5.0,
                "#FFFFFF",
                HorizontalAlignment.END,
                VerticalAlignment.CENTER,
                FontChoice.AUTO,
                True,
                6,
            ),
        ),
        WidgetConfig(
            WidgetType.DATE,
            DateSettings(
                DateFormat.DAY_MONTH_FULL_YEAR,
                "local",
                "#FFFFFF",
                HorizontalAlignment.CENTER,
                VerticalAlignment.CENTER,
                FontChoice.AUTO,
                TextOverflow.ERROR,
            ),
        ),
    )

    assert [item.type.value for item in widgets] == [
        "blank",
        "clock",
        "static_text",
        "scrolling_text",
        "date",
    ]
    assert all(WidgetConfig.from_dict(item.to_dict()) == item for item in widgets)


@pytest.mark.parametrize("value", ["hours24", "12", "", 24, None])
def test_clock_format_rejects_unsupported_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ClockSettings.from_dict(clock_settings_dict(format=value))


@pytest.mark.parametrize("field", ["show_seconds", "blink_separator"])
@pytest.mark.parametrize("value", [0, 1, "true", "false", None])
def test_clock_boolean_fields_are_strict(
    field: str,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="boolean"):
        ClockSettings.from_dict(clock_settings_dict(**{field: value}))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A" * (MAX_TIME_ZONE_LENGTH + 1),
        "UTC\n",
        "UTC\x00",
        "UTC\u200b",
    ],
)
def test_clock_time_zone_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError, match="time_zone"):
        ClockSettings.from_dict(clock_settings_dict(time_zone=value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("horizontal_alignment", "middle"),
        ("vertical_alignment", "middle"),
        ("font", "7x9"),
        ("color", "FFFFFF"),
    ],
)
def test_clock_enum_and_color_fields_are_strict(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        ClockSettings.from_dict(clock_settings_dict(**{field: value}))


def test_clock_valid_boundaries_and_color_canonicalization() -> None:
    for clock_format in ("12h", "24h"):
        settings = ClockSettings.from_dict(
            clock_settings_dict(
                format=clock_format,
                show_seconds=True,
                blink_separator=True,
                time_zone="A" * MAX_TIME_ZONE_LENGTH,
                color="#abcdef",
                horizontal_alignment="start",
                vertical_alignment="bottom",
                font="5x7",
            )
        )
        assert settings.format.value == clock_format
        assert settings.color == "#ABCDEF"


def test_static_text_content_boundaries_and_printability() -> None:
    assert StaticTextSettings.from_dict(
        static_text_settings_dict(content="A" * 64)
    ).content == "A" * 64
    for content in ("A" * 65, "TEXT\n", "TEXT\u200b", ""):
        with pytest.raises(ValueError):
            StaticTextSettings.from_dict(
                static_text_settings_dict(content=content)
            )


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_static_text_uppercase_is_a_strict_boolean(value: object) -> None:
    with pytest.raises(TypeError, match="boolean"):
        StaticTextSettings.from_dict(
            static_text_settings_dict(uppercase=value)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("overflow", "wrap"),
        ("horizontal_alignment", "middle"),
        ("vertical_alignment", "middle"),
        ("font", "7x9"),
        ("color", "#GG0000"),
    ],
)
def test_static_text_enum_and_color_fields_are_strict(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        StaticTextSettings.from_dict(
            static_text_settings_dict(**{field: value})
        )


def test_static_text_valid_enum_boundaries_remain_accepted() -> None:
    for overflow in ("error", "truncate", "clip"):
        settings = StaticTextSettings.from_dict(
            static_text_settings_dict(
                overflow=overflow,
                uppercase=False,
                horizontal_alignment="end",
                vertical_alignment="top",
                font="3x5",
                color="#abcdef",
            )
        )
        assert settings.overflow.value == overflow
        assert settings.color == "#ABCDEF"


@pytest.mark.parametrize("value", ["up", "down", "", 1, None])
def test_scrolling_text_direction_rejects_unsupported_values(
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(direction=value)
        )


def test_scrolling_text_speed_boundaries_are_accepted() -> None:
    for speed in (0.5, 20.0):
        assert ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(speed=speed)
        ).speed == speed


@pytest.mark.parametrize(
    "speed",
    [0.49, 20.01, float("nan"), float("inf"), -float("inf"), True, "5.0"],
)
def test_scrolling_text_speed_rejects_unsafe_values(speed: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(speed=speed)
        )


def test_scrolling_text_trailing_gap_boundaries_are_accepted() -> None:
    for gap in (0, MAX_TRAILING_GAP):
        assert ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(trailing_gap=gap)
        ).trailing_gap == gap


@pytest.mark.parametrize(
    "gap",
    [-1, MAX_TRAILING_GAP + 1, True, 1.0, "1"],
)
def test_scrolling_text_trailing_gap_rejects_unsafe_values(
    gap: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(trailing_gap=gap)
        )


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_scrolling_text_uppercase_is_a_strict_boolean(value: object) -> None:
    with pytest.raises(TypeError, match="boolean"):
        ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(uppercase=value)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_alignment", "middle"),
        ("vertical_alignment", "middle"),
        ("font", "7x9"),
        ("color", "#GG0000"),
    ],
)
def test_scrolling_text_enum_and_color_fields_are_strict(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        ScrollingTextSettings.from_dict(
            scrolling_text_settings_dict(**{field: value})
        )


def test_scrolling_text_content_boundaries_and_printability() -> None:
    assert ScrollingTextSettings.from_dict(
        scrolling_text_settings_dict(content="A" * 64)
    ).content == "A" * 64
    for content in ("A" * 65, "TEXT\n", "TEXT\u200b", ""):
        with pytest.raises(ValueError):
            ScrollingTextSettings.from_dict(
                scrolling_text_settings_dict(content=content)
            )


@pytest.mark.parametrize("value", ["long", "%Y-%m-%d", "", 1, None])
def test_date_format_rejects_unsupported_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        DateSettings.from_dict(date_settings_dict(format=value))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A" * (MAX_TIME_ZONE_LENGTH + 1),
        "UTC\n",
        "UTC\x00",
        "UTC\u200b",
    ],
)
def test_date_time_zone_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError, match="time_zone"):
        DateSettings.from_dict(date_settings_dict(time_zone=value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("horizontal_alignment", "middle"),
        ("vertical_alignment", "middle"),
        ("font", "7x9"),
        ("overflow", "wrap"),
        ("color", "#GG0000"),
    ],
)
def test_date_enum_and_color_fields_are_strict(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        DateSettings.from_dict(date_settings_dict(**{field: value}))


def test_date_valid_enum_boundaries_and_color_canonicalization() -> None:
    for date_format in (
        "day_month_full_year",
        "day_month_short_year",
        "day_month",
        "weekday_day_month",
    ):
        settings = DateSettings.from_dict(
            date_settings_dict(
                format=date_format,
                time_zone="A" * MAX_TIME_ZONE_LENGTH,
                color="#abcdef",
                horizontal_alignment="start",
                vertical_alignment="bottom",
                font="3x5",
                overflow="clip",
            )
        )
        assert settings.format.value == date_format
        assert settings.color == "#ABCDEF"


@pytest.mark.parametrize("field", ["color", "unexpected", "content"])
def test_blank_settings_reject_every_unexpected_field(field: str) -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        BlankSettings.from_dict({field: True})


@pytest.mark.parametrize(
    ("widget_type", "settings"),
    [
        ("blank", clock_settings_dict()),
        ("clock", static_text_settings_dict()),
        ("static_text", scrolling_text_settings_dict()),
        ("scrolling_text", date_settings_dict()),
        ("date", {}),
    ],
)
def test_widget_discriminator_rejects_another_widgets_settings(
    widget_type: str,
    settings: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        WidgetConfig.from_dict({"type": widget_type, "settings": settings})


@pytest.mark.parametrize(
    ("widget_type", "settings", "missing_field"),
    [
        ("clock", clock_settings_dict(), "format"),
        ("static_text", static_text_settings_dict(), "content"),
        ("scrolling_text", scrolling_text_settings_dict(), "speed"),
        ("date", date_settings_dict(), "time_zone"),
    ],
)
def test_widget_settings_reject_missing_required_fields(
    widget_type: str,
    settings: dict[str, object],
    missing_field: str,
) -> None:
    del settings[missing_field]
    with pytest.raises(ValueError, match="missing fields"):
        WidgetConfig.from_dict({"type": widget_type, "settings": settings})


def test_unknown_widget_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="widget type"):
        WidgetConfig.from_dict({"type": "sensor", "settings": {}})


def test_wrong_settings_for_discriminator_are_rejected() -> None:
    with pytest.raises(TypeError, match="ClockSettings"):
        WidgetConfig(WidgetType.CLOCK, BlankSettings())
    with pytest.raises(ValueError, match="missing fields"):
        WidgetConfig.from_dict({"type": "clock", "settings": {}})


def test_unknown_widget_settings_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        WidgetConfig.from_dict(
            {"type": "blank", "settings": {"unexpected": True}}
        )


def test_unknown_configuration_field_is_rejected_independently() -> None:
    raw = document().to_dict()
    raw["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ConfigurationDocumentV2.from_dict(raw)


def test_unknown_profile_field_is_rejected_independently() -> None:
    raw = document().to_dict()
    raw["profiles"]["default"]["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ConfigurationDocumentV2.from_dict(raw)


def test_unknown_canvas_field_is_rejected_independently() -> None:
    raw = document().to_dict()
    raw["profiles"]["default"]["canvas"]["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ConfigurationDocumentV2.from_dict(raw)


def test_unknown_region_field_is_rejected_independently() -> None:
    raw = document({"default": profile((region(),))}).to_dict()
    raw["profiles"]["default"]["regions"][0]["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ConfigurationDocumentV2.from_dict(raw)


def test_unknown_widget_wrapper_field_is_rejected_independently() -> None:
    raw = document({"default": profile((region(),))}).to_dict()
    raw["profiles"]["default"]["regions"][0]["widget"]["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ConfigurationDocumentV2.from_dict(raw)


@pytest.mark.parametrize(
    ("widget_type", "settings"),
    [
        ("blank", {}),
        ("clock", clock_settings_dict()),
        ("static_text", static_text_settings_dict()),
        ("scrolling_text", scrolling_text_settings_dict()),
        ("date", date_settings_dict()),
    ],
)
def test_unknown_field_is_rejected_for_each_widget_settings_object(
    widget_type: str,
    settings: dict[str, object],
) -> None:
    settings["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        WidgetConfig.from_dict({"type": widget_type, "settings": settings})


def test_unknown_and_missing_v2_fields_are_rejected() -> None:
    raw = document().to_dict()
    raw["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ConfigurationDocumentV2.from_dict(raw)

    raw = document().to_dict()
    del raw["profiles"]["default"]["canvas"]  # type: ignore[index]
    with pytest.raises(ValueError, match="missing fields"):
        ConfigurationDocumentV2.from_dict(raw)


def test_schema_version_is_exact_and_boolean_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be 2"):
        document(schema_version=1)
    with pytest.raises(TypeError):
        document(schema_version=True)


def test_serialization_is_stable_and_preserves_region_order() -> None:
    second = profile(
        (
            region("z-region", x=1, background_color="#aabbcc"),
            region("a-region", x=0),
        )
    )
    configuration = document(
        {"z-profile": second, "a-profile": profile()},
        active_profile="z-profile",
    )

    first = configuration.to_dict()
    second_serialization = configuration.to_dict()
    reparsed = ConfigurationDocumentV2.from_dict(first)

    assert first == second_serialization == reparsed.to_dict()
    assert list(first["profiles"]) == ["a-profile", "z-profile"]
    assert [
        item["id"] for item in first["profiles"]["z-profile"]["regions"]
    ] == ["z-region", "a-region"]
    assert first["profiles"]["z-profile"]["regions"][0][
        "background_color"
    ] == "#AABBCC"


def test_domain_collections_and_values_are_immutable() -> None:
    configuration = document()

    with pytest.raises(TypeError):
        configuration.profiles["other"] = profile()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        configuration.active.brightness = 50  # type: ignore[misc]
