"""Parallel schema-v2 domain models for free-form Matrix900 layouts."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
from nextuxlinq.models import MAX_PROFILES, normalize_color
from nextuxlinq.protocol import BRIGHTNESS_STEP_PERCENT
from nextuxlinq.widget_models import WidgetConfig

SCHEMA_VERSION_V2 = 2
MAX_REGIONS_PER_PROFILE = 32
MAX_REGIONS_PER_DOCUMENT = 128
MAX_REGION_ID_LENGTH = 64
MAX_REGION_NAME_LENGTH = 64
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_REGION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DISPLAY_EFFECT_SPEED_MIN = 0.0
DISPLAY_EFFECT_SPEED_MAX = 2.0
DISPLAY_EFFECT_SPEED_STEP = 0.01
DEFAULT_RAINBOW_SPEED = 0.10


class DisplayEffectMode(StrEnum):
    NONE = "none"
    FOREGROUND_RAINBOW = "foreground_rainbow"


class RainbowDirection(StrEnum):
    REAR_TO_FRONT = "rear_to_front"
    FRONT_TO_REAR = "front_to_rear"


@dataclass(frozen=True, slots=True)
class NoDisplayEffect:
    """The canonical disabled profile-level display effect."""

    mode: DisplayEffectMode = DisplayEffectMode.NONE

    def __post_init__(self) -> None:
        try:
            mode = DisplayEffectMode(self.mode)
        except (TypeError, ValueError):
            raise ValueError("display effect mode must be none.") from None
        if mode is not DisplayEffectMode.NONE:
            raise ValueError("display effect mode must be none.")
        object.__setattr__(self, "mode", mode)

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode.value}


@dataclass(frozen=True, slots=True)
class ForegroundRainbowEffect:
    """A continuous physical-width rainbow applied to foreground pixels."""

    direction: RainbowDirection = RainbowDirection.REAR_TO_FRONT
    speed: float = DEFAULT_RAINBOW_SPEED
    mode: DisplayEffectMode = DisplayEffectMode.FOREGROUND_RAINBOW

    def __post_init__(self) -> None:
        try:
            mode = DisplayEffectMode(self.mode)
        except (TypeError, ValueError):
            raise ValueError("display effect mode must be foreground_rainbow.") from None
        if mode is not DisplayEffectMode.FOREGROUND_RAINBOW:
            raise ValueError("display effect mode must be foreground_rainbow.")
        try:
            direction = RainbowDirection(self.direction)
        except (TypeError, ValueError):
            raise ValueError(
                "display effect direction must be rear_to_front or front_to_rear."
            ) from None
        if isinstance(self.speed, bool) or not isinstance(self.speed, (int, float)):
            raise TypeError("display effect speed must be a finite number.")
        speed = float(self.speed)
        if not math.isfinite(speed):
            raise ValueError("display effect speed must be a finite number.")
        if not DISPLAY_EFFECT_SPEED_MIN <= speed <= DISPLAY_EFFECT_SPEED_MAX:
            raise ValueError(
                "display effect speed must be between 0 and 2 cycles per second."
            )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "speed", speed)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "direction": self.direction.value,
            "speed": self.speed,
        }


DisplayEffect = NoDisplayEffect | ForegroundRainbowEffect


def display_effect_from_dict(raw: object) -> DisplayEffect:
    """Parse one strictly discriminated profile display effect."""
    values = _mapping(raw, "display effect")
    raw_mode = values.get("mode")
    try:
        mode = DisplayEffectMode(raw_mode)
    except (TypeError, ValueError):
        raise ValueError("display effect mode must be none or foreground_rainbow.") from None
    if mode is DisplayEffectMode.NONE:
        _fields(values, {"mode"}, "display effect")
        return NoDisplayEffect()
    _fields(values, {"mode", "direction", "speed"}, "display effect")
    return ForegroundRainbowEffect(
        direction=values["direction"],  # type: ignore[arg-type]
        speed=values["speed"],  # type: ignore[arg-type]
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object.")
    return value


def _fields(
    values: Mapping[str, object],
    allowed: set[str],
    name: str,
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


def _normalize_region_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("region name must be a string or null.")
    if not 1 <= len(value) <= MAX_REGION_NAME_LENGTH:
        raise ValueError(
            f"region name must contain 1 to {MAX_REGION_NAME_LENGTH} characters."
        )
    if value != value.strip() or not value.strip():
        raise ValueError(
            "region name must not have leading or trailing whitespace."
        )
    if not value.isprintable() or any(
        unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("region name must contain only printable characters.")
    return value


@dataclass(frozen=True, slots=True)
class CanvasConfig:
    """The fixed physical Matrix900 canvas declared in schema v2."""

    width: int
    height: int
    background_color: str

    def __post_init__(self) -> None:
        width = _integer(self.width, "canvas width")
        height = _integer(self.height, "canvas height")
        if width != MATRIX_WIDTH:
            raise ValueError(f"canvas width must be exactly {MATRIX_WIDTH}.")
        if height != MATRIX_HEIGHT:
            raise ValueError(f"canvas height must be exactly {MATRIX_HEIGHT}.")
        object.__setattr__(
            self,
            "background_color",
            normalize_color(self.background_color, "background_color"),
        )

    @classmethod
    def from_dict(cls, raw: object) -> CanvasConfig:
        values = _mapping(raw, "canvas")
        allowed = {"width", "height", "background_color"}
        _fields(values, allowed, "canvas")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "background_color": self.background_color,
        }


@dataclass(frozen=True, slots=True)
class RegionConfig:
    """One stable, non-overlapping rectangle on a Matrix900 canvas."""

    id: str
    name: str | None
    x: int
    y: int
    width: int
    height: int
    enabled: bool
    background_color: str | None
    widget: WidgetConfig

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _REGION_ID.fullmatch(self.id):
            raise ValueError(
                "region id must contain 1 to 64 letters, numbers, "
                "hyphens, or underscores and must start with a letter "
                "or number."
            )
        object.__setattr__(self, "name", _normalize_region_name(self.name))
        x = _integer(self.x, "region x")
        y = _integer(self.y, "region y")
        width = _integer(self.width, "region width")
        height = _integer(self.height, "region height")
        if x < 0:
            raise ValueError("region x must not be negative.")
        if y < 0:
            raise ValueError("region y must not be negative.")
        if width <= 0:
            raise ValueError("region width must be positive.")
        if height <= 0:
            raise ValueError("region height must be positive.")
        if x + width > MATRIX_WIDTH:
            raise ValueError("region must fit inside the canvas width.")
        if y + height > MATRIX_HEIGHT:
            raise ValueError("region must fit inside the canvas height.")
        if not isinstance(self.enabled, bool):
            raise TypeError("region enabled must be a boolean.")
        if self.background_color is not None:
            object.__setattr__(
                self,
                "background_color",
                normalize_color(self.background_color, "background_color"),
            )
        if not isinstance(self.widget, WidgetConfig):
            raise TypeError("region widget must be a WidgetConfig.")

    @classmethod
    def from_dict(cls, raw: object) -> RegionConfig:
        values = _mapping(raw, "region")
        allowed = {
            "id",
            "name",
            "x",
            "y",
            "width",
            "height",
            "enabled",
            "background_color",
            "widget",
        }
        unknown = sorted(set(values) - allowed)
        required = allowed - {"name", "background_color"}
        missing = sorted(required - set(values))
        if unknown:
            raise ValueError(
                f"region contains unknown fields: {', '.join(unknown)}."
            )
        if missing:
            raise ValueError(
                f"region is missing fields: {', '.join(missing)}."
            )
        return cls(
            id=values["id"],  # type: ignore[arg-type]
            name=values.get("name"),  # type: ignore[arg-type]
            x=values["x"],  # type: ignore[arg-type]
            y=values["y"],  # type: ignore[arg-type]
            width=values["width"],  # type: ignore[arg-type]
            height=values["height"],  # type: ignore[arg-type]
            enabled=values["enabled"],  # type: ignore[arg-type]
            background_color=values.get("background_color"),  # type: ignore[arg-type]
            widget=WidgetConfig.from_dict(values["widget"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "enabled": self.enabled,
            "background_color": self.background_color,
            "widget": self.widget.to_dict(),
        }


def _rectangles_overlap(left: RegionConfig, right: RegionConfig) -> bool:
    return (
        left.x < right.x + right.width
        and right.x < left.x + left.width
        and left.y < right.y + right.height
        and right.y < left.y + left.height
    )


@dataclass(frozen=True, slots=True)
class ProfileConfigV2:
    """One schema-v2 Matrix900 profile with absolute ordered regions."""

    brightness: int | None
    canvas: CanvasConfig
    regions: tuple[RegionConfig, ...]
    effect: DisplayEffect = NoDisplayEffect()

    def __post_init__(self) -> None:
        if self.brightness is not None:
            brightness = _integer(self.brightness, "brightness")
            if not 10 <= brightness <= 100:
                raise ValueError("brightness must be null or between 10 and 100.")
            if brightness % BRIGHTNESS_STEP_PERCENT:
                raise ValueError("brightness must use ten-percent steps.")
        if not isinstance(self.canvas, CanvasConfig):
            raise TypeError("canvas must be a CanvasConfig.")
        if not isinstance(self.effect, (NoDisplayEffect, ForegroundRainbowEffect)):
            raise TypeError("effect must be a canonical display effect.")
        if isinstance(self.regions, (str, bytes)) or not isinstance(
            self.regions, Sequence
        ):
            raise TypeError("regions must be a sequence.")
        regions = tuple(self.regions)
        if len(regions) > MAX_REGIONS_PER_PROFILE:
            raise ValueError(
                f"regions must contain at most {MAX_REGIONS_PER_PROFILE} entries."
            )
        seen: set[str] = set()
        for index, region in enumerate(regions):
            if not isinstance(region, RegionConfig):
                raise TypeError(f"regions[{index}] must be a RegionConfig.")
            if region.id in seen:
                raise ValueError(f"region id {region.id!r} is duplicated.")
            seen.add(region.id)
        for index, left in enumerate(regions):
            for right in regions[index + 1 :]:
                if _rectangles_overlap(left, right):
                    raise ValueError(
                        f"regions {left.id!r} and {right.id!r} overlap."
                    )
        object.__setattr__(self, "regions", regions)

    @classmethod
    def from_dict(cls, raw: object) -> ProfileConfigV2:
        values = _mapping(raw, "profile")
        allowed = {"brightness", "canvas", "regions", "effect"}
        unknown = sorted(set(values) - allowed)
        missing = sorted({"brightness", "canvas", "regions"} - set(values))
        if unknown:
            raise ValueError(f"profile contains unknown fields: {', '.join(unknown)}.")
        if missing:
            raise ValueError(f"profile is missing fields: {', '.join(missing)}.")
        raw_regions = values["regions"]
        if not isinstance(raw_regions, list):
            raise TypeError("regions must be a JSON array.")
        return cls(
            brightness=values["brightness"],  # type: ignore[arg-type]
            canvas=CanvasConfig.from_dict(values["canvas"]),
            regions=tuple(RegionConfig.from_dict(item) for item in raw_regions),
            effect=(
                NoDisplayEffect()
                if "effect" not in values
                else display_effect_from_dict(values["effect"])
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "brightness": self.brightness,
            "canvas": self.canvas.to_dict(),
            "regions": [region.to_dict() for region in self.regions],
            "effect": self.effect.to_dict(),
        }

    def _legacy_projection(self):
        """Return the strict current-editor projection when representable."""
        from nextuxlinq.legacy_compat import legacy_profile_from_v2

        return legacy_profile_from_v2(self)

    @property
    def scrolling_text(self) -> str:
        return self._legacy_projection().scrolling_text

    @property
    def text_color(self) -> str:
        return self._legacy_projection().text_color

    @property
    def clock_color(self) -> str:
        return self._legacy_projection().clock_color

    @property
    def scroll_speed(self) -> float:
        return self._legacy_projection().scroll_speed

    @property
    def layout(self):
        return self._legacy_projection().layout


@dataclass(frozen=True, slots=True)
class ConfigurationDocumentV2:
    """A strict, immutable schema-v2 Matrix900 configuration."""

    schema_version: int
    active_profile: str
    profiles: Mapping[str, ProfileConfigV2]

    def __post_init__(self) -> None:
        if _integer(self.schema_version, "schema_version") != SCHEMA_VERSION_V2:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION_V2}.")
        if not isinstance(self.active_profile, str):
            raise TypeError("active_profile must be a string.")
        if not _PROFILE_NAME.fullmatch(self.active_profile):
            raise ValueError("active_profile has an invalid profile name.")
        if not isinstance(self.profiles, Mapping):
            raise TypeError("profiles must be a mapping.")
        if not 1 <= len(self.profiles) <= MAX_PROFILES:
            raise ValueError(f"profiles must contain 1 to {MAX_PROFILES} entries.")
        profiles: dict[str, ProfileConfigV2] = {}
        total_regions = 0
        for name, profile in self.profiles.items():
            if not isinstance(name, str) or not _PROFILE_NAME.fullmatch(name):
                raise ValueError(f"Invalid profile name: {name!r}.")
            if not isinstance(profile, ProfileConfigV2):
                raise TypeError(f"Profile {name!r} must be a ProfileConfigV2.")
            profiles[name] = profile
            total_regions += len(profile.regions)
        if total_regions > MAX_REGIONS_PER_DOCUMENT:
            raise ValueError(
                "configuration must contain at most "
                f"{MAX_REGIONS_PER_DOCUMENT} regions."
            )
        if self.active_profile not in profiles:
            raise ValueError("active_profile must identify an existing profile.")
        object.__setattr__(self, "profiles", MappingProxyType(profiles))

    @property
    def active(self) -> ProfileConfigV2:
        return self.profiles[self.active_profile]

    @classmethod
    def from_dict(cls, raw: object) -> ConfigurationDocumentV2:
        values = _mapping(raw, "configuration")
        allowed = {"schema_version", "active_profile", "profiles"}
        _fields(values, allowed, "configuration")
        raw_profiles = _mapping(values["profiles"], "profiles")
        profiles: dict[str, ProfileConfigV2] = {}
        for name, profile in raw_profiles.items():
            if not isinstance(name, str):
                raise ValueError("All profile names must be strings.")
            profiles[name] = ProfileConfigV2.from_dict(profile)
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
                name: self.profiles[name].to_dict()
                for name in sorted(self.profiles)
            },
        }
