"""Tests for strict Matrix900 display configuration models."""

import math
from dataclasses import FrozenInstanceError

import pytest

from nexlinq.defaults import default_configuration, default_profile
from nexlinq.models import (
    SUPPORTED_TEXT_CHARACTERS,
    ConfigurationDocument,
    LayoutConfig,
    ProfileConfig,
    normalize_color,
    normalize_display_text,
)


def profile(**changes: object) -> ProfileConfig:
    values = default_profile().to_dict()
    values.update(changes)
    return ProfileConfig.from_dict(values)


def test_built_in_default_preserves_current_display() -> None:
    configuration = default_configuration()

    assert configuration.schema_version == 1
    assert configuration.active_profile == "default"
    assert configuration.active.scrolling_text == "CACHYOS"
    assert configuration.active.text_color == "#00D4C7"
    assert configuration.active.clock_color == "#FFFFFF"
    assert configuration.active.scroll_speed == 5.0
    assert configuration.active.brightness is None
    assert configuration.active.layout == LayoutConfig(24, 4, 31, 31)


def test_text_is_normalized_to_uppercase() -> None:
    assert normalize_display_text("nexlinq 900!") == "NEXLINQ 900!"


def test_supported_font_scope_is_explicit() -> None:
    assert set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-_:!?/") == set(
        SUPPORTED_TEXT_CHARACTERS
    )


@pytest.mark.parametrize("value", ["", "A\nB", "GRÜSSE", "A" * 65])
def test_invalid_text_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_display_text(value)


def test_colors_are_canonicalized() -> None:
    assert normalize_color("#00d4c7") == "#00D4C7"
    assert profile(text_color="#abcdef").text_color == "#ABCDEF"


@pytest.mark.parametrize("value", ["00D4C7", "#12345", "#GG0000", 123])
def test_invalid_colors_are_rejected(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        normalize_color(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0.4, 20.1, math.inf, math.nan, True])
def test_invalid_scroll_speeds_are_rejected(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        profile(scroll_speed=value)


@pytest.mark.parametrize("value", [0, 55, 110, True])
def test_invalid_brightness_values_are_rejected(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        profile(brightness=value)


def test_null_brightness_is_valid() -> None:
    assert profile(brightness=None).brightness is None


def test_width_total_and_fixed_v1_geometry_are_enforced() -> None:
    with pytest.raises(ValueError):
        LayoutConfig(24, 4, 31, 30)
    with pytest.raises(ValueError):
        LayoutConfig(23, 5, 31, 31)


def test_unknown_and_missing_json_fields_are_rejected() -> None:
    raw = default_configuration().to_dict()
    raw["unexpected"] = True
    with pytest.raises(ValueError):
        ConfigurationDocument.from_dict(raw)

    raw = default_profile().to_dict()
    del raw["clock_color"]
    with pytest.raises(ValueError):
        ProfileConfig.from_dict(raw)


def test_profile_name_must_be_safe_and_active() -> None:
    with pytest.raises(ValueError):
        ConfigurationDocument(1, "../default", {"default": default_profile()})
    with pytest.raises(ValueError):
        ConfigurationDocument(1, "missing", {"default": default_profile()})


def test_validated_configuration_profiles_are_immutable() -> None:
    configuration = default_configuration()

    with pytest.raises(TypeError):
        configuration.profiles["other"] = default_profile()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        configuration.active.brightness = 50  # type: ignore[misc]

    assert configuration == default_configuration()
    assert configuration.to_dict() == default_configuration().to_dict()


def test_huge_scroll_speed_is_a_normal_validation_failure() -> None:
    with pytest.raises(ValueError):
        profile(scroll_speed=10**1000)
