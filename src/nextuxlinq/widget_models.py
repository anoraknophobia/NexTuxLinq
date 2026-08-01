"""Strict schema-v2 settings models for software-rendered widgets."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, TypeAlias

from nextuxlinq.models import MAX_SCROLL_SPEED, MAX_TEXT_LENGTH, MIN_SCROLL_SPEED
from nextuxlinq.models import normalize_color
from nextuxlinq.system_metrics import (
    BAR_METRICS,
    MetricId,
    canonical_metric_label,
)

MAX_TIME_ZONE_LENGTH = 128
MAX_TRAILING_GAP = 90
_TIME_ZONE = re.compile(r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$")


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


def _strict_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean.")
    return value


def _strict_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    return value


def _enum(enum_type, value: object, name: str):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string.")
    try:
        return enum_type(value)
    except ValueError as error:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} must be one of: {choices}.") from error


def _content(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string.")
    if not value:
        raise ValueError(f"{name} must not be empty.")
    if len(value) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"{name} must contain at most {MAX_TEXT_LENGTH} characters."
        )
    if not value.isprintable():
        raise ValueError(f"{name} must contain only printable characters.")
    return value


def normalize_time_zone(value: object) -> str:
    """Validate one stable local or IANA-style time-zone identifier."""
    if not isinstance(value, str):
        raise TypeError("time_zone must be a string.")
    if not value or len(value) > MAX_TIME_ZONE_LENGTH:
        raise ValueError(
            f"time_zone must contain 1 to {MAX_TIME_ZONE_LENGTH} characters."
        )
    if value == "local":
        return value
    if not _TIME_ZONE.fullmatch(value):
        raise ValueError("time_zone has an invalid identifier.")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("time_zone has an invalid identifier.")
    return value


class WidgetType(StrEnum):
    BLANK = "blank"
    CLOCK = "clock"
    STATIC_TEXT = "static_text"
    SCROLLING_TEXT = "scrolling_text"
    DATE = "date"
    SYSTEM_METRIC = "system_metric"
    METRIC_BAR = "metric_bar"


class ClockFormat(StrEnum):
    HOUR_12 = "12h"
    HOUR_24 = "24h"


class HorizontalAlignment(StrEnum):
    START = "start"
    CENTER = "center"
    END = "end"


class VerticalAlignment(StrEnum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class FontChoice(StrEnum):
    AUTO = "auto"
    COMPACT_3X5 = "3x5"
    LEGACY_3X7 = "3x7"
    LEGACY_5X7 = "5x7"
    NATIVE_4X9 = "4x9"
    NATIVE_5X9 = "5x9"
    NATIVE_5X10 = "5x10"


class TextOverflow(StrEnum):
    ERROR = "error"
    TRUNCATE = "truncate"
    CLIP = "clip"


class ScrollDirection(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class DateFormat(StrEnum):
    DAY_MONTH_FULL_YEAR = "day_month_full_year"
    DAY_MONTH_SHORT_YEAR = "day_month_short_year"
    DAY_MONTH = "day_month"
    WEEKDAY_DAY_MONTH = "weekday_day_month"


class MetricDisplayMode(StrEnum):
    VALUE_ONLY = "value_only"
    LABEL_AND_VALUE = "label_and_value"


class TemperatureUnit(StrEnum):
    DEGREE_ONLY = "degree_only"
    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"


class BarOrientation(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class BarDirection(StrEnum):
    START_TO_END = "start_to_end"
    END_TO_START = "end_to_start"


@dataclass(frozen=True, slots=True)
class BlankSettings:
    """Settings for a blank region; its common region background is used."""

    @classmethod
    def from_dict(cls, raw: object) -> BlankSettings:
        values = _mapping(raw, "blank settings")
        _fields(values, set(), "blank settings")
        return cls()

    def to_dict(self) -> dict[str, object]:
        return {}


@dataclass(frozen=True, slots=True)
class ClockSettings:
    format: ClockFormat
    show_seconds: bool
    blink_separator: bool
    time_zone: str
    color: str
    horizontal_alignment: HorizontalAlignment
    vertical_alignment: VerticalAlignment
    font: FontChoice

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "format", _enum(ClockFormat, self.format, "format")
        )
        object.__setattr__(
            self,
            "show_seconds",
            _strict_bool(self.show_seconds, "show_seconds"),
        )
        object.__setattr__(
            self,
            "blink_separator",
            _strict_bool(self.blink_separator, "blink_separator"),
        )
        object.__setattr__(
            self, "time_zone", normalize_time_zone(self.time_zone)
        )
        object.__setattr__(
            self, "color", normalize_color(self.color, "color")
        )
        object.__setattr__(
            self,
            "horizontal_alignment",
            _enum(
                HorizontalAlignment,
                self.horizontal_alignment,
                "horizontal_alignment",
            ),
        )
        object.__setattr__(
            self,
            "vertical_alignment",
            _enum(
                VerticalAlignment,
                self.vertical_alignment,
                "vertical_alignment",
            ),
        )
        object.__setattr__(
            self, "font", _enum(FontChoice, self.font, "font")
        )

    @classmethod
    def from_dict(cls, raw: object) -> ClockSettings:
        values = _mapping(raw, "clock settings")
        allowed = {
            "format",
            "show_seconds",
            "blink_separator",
            "time_zone",
            "color",
            "horizontal_alignment",
            "vertical_alignment",
            "font",
        }
        _fields(values, allowed, "clock settings")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format.value,
            "show_seconds": self.show_seconds,
            "blink_separator": self.blink_separator,
            "time_zone": self.time_zone,
            "color": self.color,
            "horizontal_alignment": self.horizontal_alignment.value,
            "vertical_alignment": self.vertical_alignment.value,
            "font": self.font.value,
        }


@dataclass(frozen=True, slots=True)
class StaticTextSettings:
    content: str
    color: str
    horizontal_alignment: HorizontalAlignment
    vertical_alignment: VerticalAlignment
    font: FontChoice
    uppercase: bool
    overflow: TextOverflow

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _content(self.content, "content"))
        object.__setattr__(
            self, "color", normalize_color(self.color, "color")
        )
        object.__setattr__(
            self,
            "horizontal_alignment",
            _enum(
                HorizontalAlignment,
                self.horizontal_alignment,
                "horizontal_alignment",
            ),
        )
        object.__setattr__(
            self,
            "vertical_alignment",
            _enum(
                VerticalAlignment,
                self.vertical_alignment,
                "vertical_alignment",
            ),
        )
        object.__setattr__(
            self, "font", _enum(FontChoice, self.font, "font")
        )
        object.__setattr__(
            self, "uppercase", _strict_bool(self.uppercase, "uppercase")
        )
        object.__setattr__(
            self, "overflow", _enum(TextOverflow, self.overflow, "overflow")
        )

    @classmethod
    def from_dict(cls, raw: object) -> StaticTextSettings:
        values = _mapping(raw, "static_text settings")
        allowed = {
            "content",
            "color",
            "horizontal_alignment",
            "vertical_alignment",
            "font",
            "uppercase",
            "overflow",
        }
        _fields(values, allowed, "static_text settings")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "color": self.color,
            "horizontal_alignment": self.horizontal_alignment.value,
            "vertical_alignment": self.vertical_alignment.value,
            "font": self.font.value,
            "uppercase": self.uppercase,
            "overflow": self.overflow.value,
        }


@dataclass(frozen=True, slots=True)
class ScrollingTextSettings:
    content: str
    direction: ScrollDirection
    speed: float
    color: str
    initial_alignment: HorizontalAlignment
    vertical_alignment: VerticalAlignment
    font: FontChoice
    uppercase: bool
    trailing_gap: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _content(self.content, "content"))
        object.__setattr__(
            self,
            "direction",
            _enum(ScrollDirection, self.direction, "direction"),
        )
        if isinstance(self.speed, bool) or not isinstance(
            self.speed, (int, float)
        ):
            raise TypeError("speed must be a number.")
        try:
            speed = float(self.speed)
        except (OverflowError, ValueError) as error:
            raise ValueError("speed must be a finite number.") from error
        if not math.isfinite(speed):
            raise ValueError("speed must be finite.")
        if not MIN_SCROLL_SPEED <= speed <= MAX_SCROLL_SPEED:
            raise ValueError(
                f"speed must be between {MIN_SCROLL_SPEED} "
                f"and {MAX_SCROLL_SPEED}."
            )
        object.__setattr__(self, "speed", speed)
        object.__setattr__(
            self, "color", normalize_color(self.color, "color")
        )
        object.__setattr__(
            self,
            "initial_alignment",
            _enum(
                HorizontalAlignment,
                self.initial_alignment,
                "initial_alignment",
            ),
        )
        object.__setattr__(
            self,
            "vertical_alignment",
            _enum(
                VerticalAlignment,
                self.vertical_alignment,
                "vertical_alignment",
            ),
        )
        object.__setattr__(
            self, "font", _enum(FontChoice, self.font, "font")
        )
        object.__setattr__(
            self, "uppercase", _strict_bool(self.uppercase, "uppercase")
        )
        gap = _strict_integer(self.trailing_gap, "trailing_gap")
        if not 0 <= gap <= MAX_TRAILING_GAP:
            raise ValueError(
                f"trailing_gap must be between 0 and {MAX_TRAILING_GAP}."
            )

    @classmethod
    def from_dict(cls, raw: object) -> ScrollingTextSettings:
        values = _mapping(raw, "scrolling_text settings")
        allowed = {
            "content",
            "direction",
            "speed",
            "color",
            "initial_alignment",
            "vertical_alignment",
            "font",
            "uppercase",
            "trailing_gap",
        }
        _fields(values, allowed, "scrolling_text settings")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "direction": self.direction.value,
            "speed": self.speed,
            "color": self.color,
            "initial_alignment": self.initial_alignment.value,
            "vertical_alignment": self.vertical_alignment.value,
            "font": self.font.value,
            "uppercase": self.uppercase,
            "trailing_gap": self.trailing_gap,
        }


@dataclass(frozen=True, slots=True)
class DateSettings:
    format: DateFormat = DateFormat.DAY_MONTH_FULL_YEAR
    time_zone: str = "local"
    color: str = "#FFFFFF"
    horizontal_alignment: HorizontalAlignment = HorizontalAlignment.CENTER
    vertical_alignment: VerticalAlignment = VerticalAlignment.CENTER
    font: FontChoice = FontChoice.AUTO
    overflow: TextOverflow = TextOverflow.ERROR

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "format", _enum(DateFormat, self.format, "format")
        )
        object.__setattr__(
            self, "time_zone", normalize_time_zone(self.time_zone)
        )
        object.__setattr__(
            self, "color", normalize_color(self.color, "color")
        )
        object.__setattr__(
            self,
            "horizontal_alignment",
            _enum(
                HorizontalAlignment,
                self.horizontal_alignment,
                "horizontal_alignment",
            ),
        )
        object.__setattr__(
            self,
            "vertical_alignment",
            _enum(
                VerticalAlignment,
                self.vertical_alignment,
                "vertical_alignment",
            ),
        )
        object.__setattr__(
            self, "font", _enum(FontChoice, self.font, "font")
        )
        object.__setattr__(
            self, "overflow", _enum(TextOverflow, self.overflow, "overflow")
        )

    @classmethod
    def from_dict(cls, raw: object) -> DateSettings:
        values = _mapping(raw, "date settings")
        allowed = {
            "format",
            "time_zone",
            "color",
            "horizontal_alignment",
            "vertical_alignment",
            "font",
            "overflow",
        }
        _fields(values, allowed, "date settings")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format.value,
            "time_zone": self.time_zone,
            "color": self.color,
            "horizontal_alignment": self.horizontal_alignment.value,
            "vertical_alignment": self.vertical_alignment.value,
            "font": self.font.value,
            "overflow": self.overflow.value,
        }


@dataclass(frozen=True, slots=True)
class SystemMetricSettings:
    metric: MetricId = MetricId.CPU_USAGE
    display_mode: MetricDisplayMode = MetricDisplayMode.LABEL_AND_VALUE
    label: str = "CPU"
    color: str = "#FFFFFF"
    font: FontChoice = FontChoice.AUTO
    horizontal_alignment: HorizontalAlignment = HorizontalAlignment.CENTER
    vertical_alignment: VerticalAlignment = VerticalAlignment.CENTER
    overflow: TextOverflow = TextOverflow.ERROR
    precision: int = 0
    unavailable_text: str = "--"
    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _enum(MetricId, self.metric, "metric"))
        object.__setattr__(
            self, "display_mode", _enum(MetricDisplayMode, self.display_mode, "display_mode")
        )
        object.__setattr__(
            self,
            "label",
            canonical_metric_label(
                self.metric,
                _content(self.label, "label"),
            ),
        )
        object.__setattr__(self, "color", normalize_color(self.color, "color"))
        object.__setattr__(self, "font", _enum(FontChoice, self.font, "font"))
        object.__setattr__(
            self,
            "horizontal_alignment",
            _enum(HorizontalAlignment, self.horizontal_alignment, "horizontal_alignment"),
        )
        object.__setattr__(
            self,
            "vertical_alignment",
            _enum(VerticalAlignment, self.vertical_alignment, "vertical_alignment"),
        )
        object.__setattr__(self, "overflow", _enum(TextOverflow, self.overflow, "overflow"))
        precision = _strict_integer(self.precision, "precision")
        if not 0 <= precision <= 2:
            raise ValueError("precision must be between 0 and 2.")
        object.__setattr__(
            self, "unavailable_text", _content(self.unavailable_text, "unavailable_text")
        )
        object.__setattr__(
            self,
            "temperature_unit",
            _enum(
                TemperatureUnit,
                self.temperature_unit,
                "temperature_unit",
            ),
        )

    @classmethod
    def from_dict(cls, raw: object) -> SystemMetricSettings:
        values = _mapping(raw, "system_metric settings")
        allowed = {
            "metric", "display_mode", "label", "color", "font",
            "horizontal_alignment", "vertical_alignment", "overflow",
            "precision", "unavailable_text", "temperature_unit",
        }
        unknown = sorted(set(values) - allowed)
        required = allowed - {"temperature_unit"}
        missing = sorted(required - set(values))
        if unknown:
            raise ValueError(
                "system_metric settings contains unknown fields: "
                f"{', '.join(unknown)}."
            )
        if missing:
            raise ValueError(
                "system_metric settings is missing fields: "
                f"{', '.join(missing)}."
            )
        normalized = {key: values[key] for key in required}
        normalized["temperature_unit"] = values.get(
            "temperature_unit", TemperatureUnit.CELSIUS.value
        )
        return cls(**normalized)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric.value,
            "display_mode": self.display_mode.value,
            "label": self.label,
            "color": self.color,
            "font": self.font.value,
            "horizontal_alignment": self.horizontal_alignment.value,
            "vertical_alignment": self.vertical_alignment.value,
            "overflow": self.overflow.value,
            "precision": self.precision,
            "unavailable_text": self.unavailable_text,
            "temperature_unit": self.temperature_unit.value,
        }


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite.")
    return normalized


@dataclass(frozen=True, slots=True)
class MetricBarSettings:
    metric: MetricId = MetricId.CPU_USAGE
    minimum: float = 0.0
    maximum: float = 100.0
    orientation: BarOrientation = BarOrientation.HORIZONTAL
    direction: BarDirection = BarDirection.START_TO_END
    fill_color: str = "#00D4C7"
    empty_color: str = "#000000"
    border_enabled: bool = False
    border_color: str = "#FFFFFF"

    def __post_init__(self) -> None:
        metric = _enum(MetricId, self.metric, "metric")
        if metric not in BAR_METRICS:
            raise ValueError("metric does not support a bar.")
        object.__setattr__(self, "metric", metric)
        minimum = _finite_number(self.minimum, "minimum")
        maximum = _finite_number(self.maximum, "maximum")
        if maximum <= minimum:
            raise ValueError("maximum must be greater than minimum.")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(
            self, "orientation", _enum(BarOrientation, self.orientation, "orientation")
        )
        object.__setattr__(self, "direction", _enum(BarDirection, self.direction, "direction"))
        object.__setattr__(self, "fill_color", normalize_color(self.fill_color, "fill_color"))
        object.__setattr__(self, "empty_color", normalize_color(self.empty_color, "empty_color"))
        object.__setattr__(self, "border_enabled", _strict_bool(self.border_enabled, "border_enabled"))
        object.__setattr__(self, "border_color", normalize_color(self.border_color, "border_color"))

    @classmethod
    def from_dict(cls, raw: object) -> MetricBarSettings:
        values = _mapping(raw, "metric_bar settings")
        allowed = {
            "metric", "minimum", "maximum", "orientation", "direction",
            "fill_color", "empty_color", "border_enabled", "border_color",
        }
        _fields(values, allowed, "metric_bar settings")
        return cls(**{key: values[key] for key in allowed})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "orientation": self.orientation.value,
            "direction": self.direction.value,
            "fill_color": self.fill_color,
            "empty_color": self.empty_color,
            "border_enabled": self.border_enabled,
            "border_color": self.border_color,
        }


WidgetSettings: TypeAlias = (
    BlankSettings
    | ClockSettings
    | StaticTextSettings
    | ScrollingTextSettings
    | DateSettings
    | SystemMetricSettings
    | MetricBarSettings
)

CANONICAL_WIDGET_SETTINGS_MODELS: Mapping[
    WidgetType, type[WidgetSettings]
] = MappingProxyType(
    {
        WidgetType.BLANK: BlankSettings,
        WidgetType.CLOCK: ClockSettings,
        WidgetType.STATIC_TEXT: StaticTextSettings,
        WidgetType.SCROLLING_TEXT: ScrollingTextSettings,
        WidgetType.DATE: DateSettings,
        WidgetType.SYSTEM_METRIC: SystemMetricSettings,
        WidgetType.METRIC_BAR: MetricBarSettings,
    }
)
"""Immutable discriminator-to-settings-model mapping used by schema v2."""


@dataclass(frozen=True, slots=True)
class WidgetConfig:
    """One strictly discriminated software widget and its typed settings."""

    type: WidgetType
    settings: WidgetSettings

    def __post_init__(self) -> None:
        widget_type = _enum(WidgetType, self.type, "widget type")
        object.__setattr__(self, "type", widget_type)
        expected = CANONICAL_WIDGET_SETTINGS_MODELS[widget_type]
        if not isinstance(self.settings, expected):
            raise TypeError(
                f"{widget_type.value} widget settings must be "
                f"{expected.__name__}."
            )

    @classmethod
    def from_dict(cls, raw: object) -> WidgetConfig:
        values = _mapping(raw, "widget")
        _fields(values, {"type", "settings"}, "widget")
        widget_type = _enum(WidgetType, values["type"], "widget type")
        settings_type = CANONICAL_WIDGET_SETTINGS_MODELS[widget_type]
        settings = settings_type.from_dict(values["settings"])
        return cls(type=widget_type, settings=settings)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type.value,
            "settings": self.settings.to_dict(),
        }
