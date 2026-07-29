"""Strict configuration models for the Matrix900 dynamic display."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from nextuxlinq.creator import MATRIX_WIDTH, RGB
from nextuxlinq.protocol import BRIGHTNESS_STEP_PERCENT

SCHEMA_VERSION = 1
MAX_PROFILES = 32
MAX_TEXT_LENGTH = 64
MIN_SCROLL_SPEED = 0.5
MAX_SCROLL_SPEED = 20.0
SUPPORTED_TEXT_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-_:!?/"
)
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object.")
    return value


def _fields(
    values: Mapping[str, object], allowed: set[str], name: str
) -> None:
    unknown = sorted(set(values) - allowed)
    missing = sorted(allowed - set(values))
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(unknown)}.")
    if missing:
        raise ValueError(f"{name} is missing fields: {', '.join(missing)}.")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    return value


def normalize_display_text(value: str) -> str:
    """Normalize and validate text supported by the bitmap renderer."""
    if not isinstance(value, str):
        raise TypeError("scrolling_text must be a string.")
    normalized = value.upper()
    if not normalized:
        raise ValueError("scrolling_text must not be empty.")
    if len(normalized) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"scrolling_text must contain at most {MAX_TEXT_LENGTH} characters."
        )
    unsupported = sorted(set(normalized) - SUPPORTED_TEXT_CHARACTERS)
    if unsupported:
        characters = ", ".join(repr(character) for character in unsupported)
        raise ValueError(
            f"scrolling_text contains unsupported characters: {characters}."
        )
    return normalized


def normalize_color(value: str, name: str = "color") -> str:
    """Return a validated color in canonical #RRGGBB form."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string.")
    if not _COLOR.fullmatch(value):
        raise ValueError(f"{name} must use #RRGGBB format.")
    return value.upper()


def color_to_rgb(value: str) -> RGB:
    """Convert a validated #RRGGBB value to an RGB tuple."""
    value = normalize_color(value)
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    """Fixed first-version physical layout for the Matrix900."""

    front_clock_width: int = 24
    gap_width: int = 4
    scroll_text_width: int = 31
    rear_clock_width: int = 31

    def __post_init__(self) -> None:
        widths = (
            _integer(self.front_clock_width, "front_clock_width"),
            _integer(self.gap_width, "gap_width"),
            _integer(self.scroll_text_width, "scroll_text_width"),
            _integer(self.rear_clock_width, "rear_clock_width"),
        )
        if any(width < 0 for width in widths):
            raise ValueError("Layout widths must not be negative.")
        if sum(widths) != MATRIX_WIDTH:
            raise ValueError(f"Layout widths must add up to {MATRIX_WIDTH}.")
        if widths != (24, 4, 31, 31):
            raise ValueError("Schema version 1 requires layout widths 24/4/31/31.")

    @classmethod
    def from_dict(cls, raw: object) -> LayoutConfig:
        values = _mapping(raw, "layout")
        allowed = {
            "front_clock_width", "gap_width",
            "scroll_text_width", "rear_clock_width",
        }
        _fields(values, allowed, "layout")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, int]:
        return {
            "front_clock_width": self.front_clock_width,
            "gap_width": self.gap_width,
            "scroll_text_width": self.scroll_text_width,
            "rear_clock_width": self.rear_clock_width,
        }


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    """Validated settings for one dynamic display profile."""

    scrolling_text: str
    text_color: str
    clock_color: str
    scroll_speed: float
    brightness: int | None
    layout: LayoutConfig

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "scrolling_text", normalize_display_text(self.scrolling_text)
        )
        object.__setattr__(
            self, "text_color", normalize_color(self.text_color, "text_color")
        )
        object.__setattr__(
            self, "clock_color", normalize_color(self.clock_color, "clock_color")
        )
        if isinstance(self.scroll_speed, bool) or not isinstance(
            self.scroll_speed, (int, float)
        ):
            raise TypeError("scroll_speed must be a number.")
        try:
            speed = float(self.scroll_speed)
        except (OverflowError, ValueError) as error:
            raise ValueError("scroll_speed must be a finite number.") from error
        if not math.isfinite(speed):
            raise ValueError("scroll_speed must be finite.")
        if not MIN_SCROLL_SPEED <= speed <= MAX_SCROLL_SPEED:
            raise ValueError(
                f"scroll_speed must be between {MIN_SCROLL_SPEED} "
                f"and {MAX_SCROLL_SPEED}."
            )
        object.__setattr__(self, "scroll_speed", speed)
        if self.brightness is not None:
            brightness = _integer(self.brightness, "brightness")
            if not 10 <= brightness <= 100:
                raise ValueError("brightness must be null or between 10 and 100.")
            if brightness % BRIGHTNESS_STEP_PERCENT:
                raise ValueError("brightness must use ten-percent steps.")
        if not isinstance(self.layout, LayoutConfig):
            raise TypeError("layout must be a LayoutConfig.")

    @classmethod
    def from_dict(cls, raw: object) -> ProfileConfig:
        values = _mapping(raw, "profile")
        allowed = {
            "scrolling_text", "text_color", "clock_color",
            "scroll_speed", "brightness", "layout",
        }
        _fields(values, allowed, "profile")
        return cls(
            scrolling_text=values["scrolling_text"],  # type: ignore[arg-type]
            text_color=values["text_color"],  # type: ignore[arg-type]
            clock_color=values["clock_color"],  # type: ignore[arg-type]
            scroll_speed=values["scroll_speed"],  # type: ignore[arg-type]
            brightness=values["brightness"],  # type: ignore[arg-type]
            layout=LayoutConfig.from_dict(values["layout"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scrolling_text": self.scrolling_text,
            "text_color": self.text_color,
            "clock_color": self.clock_color,
            "scroll_speed": self.scroll_speed,
            "brightness": self.brightness,
            "layout": self.layout.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ConfigurationDocument:
    """Versioned collection of Matrix900 display profiles."""

    schema_version: int
    active_profile: str
    profiles: Mapping[str, ProfileConfig]

    def __post_init__(self) -> None:
        if _integer(self.schema_version, "schema_version") != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}.")
        if not isinstance(self.active_profile, str):
            raise TypeError("active_profile must be a string.")
        if not _PROFILE_NAME.fullmatch(self.active_profile):
            raise ValueError("active_profile has an invalid profile name.")
        if not isinstance(self.profiles, Mapping):
            raise TypeError("profiles must be a mapping.")
        if not 1 <= len(self.profiles) <= MAX_PROFILES:
            raise ValueError(f"profiles must contain 1 to {MAX_PROFILES} entries.")
        profiles: dict[str, ProfileConfig] = {}
        for name, profile in self.profiles.items():
            if not isinstance(name, str) or not _PROFILE_NAME.fullmatch(name):
                raise ValueError(f"Invalid profile name: {name!r}.")
            if not isinstance(profile, ProfileConfig):
                raise TypeError(f"Profile {name!r} must be a ProfileConfig.")
            profiles[name] = profile
        if self.active_profile not in profiles:
            raise ValueError("active_profile must identify an existing profile.")
        object.__setattr__(self, "profiles", MappingProxyType(profiles))

    @property
    def active(self) -> ProfileConfig:
        return self.profiles[self.active_profile]

    @classmethod
    def from_dict(cls, raw: object) -> ConfigurationDocument:
        values = _mapping(raw, "configuration")
        allowed = {"schema_version", "active_profile", "profiles"}
        _fields(values, allowed, "configuration")
        raw_profiles = _mapping(values["profiles"], "profiles")
        profiles: dict[str, ProfileConfig] = {}
        for name, profile in raw_profiles.items():
            if not isinstance(name, str):
                raise ValueError("All profile names must be strings.")
            profiles[name] = ProfileConfig.from_dict(profile)
        return cls(
            schema_version=values["schema_version"],  # type: ignore[arg-type]
            active_profile=values["active_profile"],  # type: ignore[arg-type]
            profiles=profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "active_profile": self.active_profile,
            "profiles": {
                name: profile.to_dict()
                for name, profile in sorted(self.profiles.items())
            },
        }
