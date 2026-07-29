"""Pure schema detection and deterministic configuration migration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
from nextuxlinq.models import ConfigurationDocument
from nextuxlinq.models_v2 import (
    SCHEMA_VERSION_V2,
    CanvasConfig,
    ConfigurationDocumentV2,
    ProfileConfigV2,
    RegionConfig,
)
from nextuxlinq.rendering import MARQUEE_TRAILING_GAP
from nextuxlinq.widget_models import (
    ClockFormat,
    ClockSettings,
    FontChoice,
    HorizontalAlignment,
    ScrollDirection,
    ScrollingTextSettings,
    VerticalAlignment,
    WidgetConfig,
    WidgetType,
)

SCHEMA_VERSION_V1 = 1
LEGACY_FRONT_CLOCK_REGION_ID = "legacy-front-clock"
LEGACY_SCROLLING_TEXT_REGION_ID = "legacy-scrolling-text"
LEGACY_REAR_CLOCK_REGION_ID = "legacy-rear-clock"


class SchemaKind(StrEnum):
    PRE_VERSIONED = "schema_0"
    VERSION_1 = "schema_1"
    VERSION_2 = "schema_2"
    UNSUPPORTED_FUTURE = "unsupported_future"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class SchemaDetection:
    """Read-only classification of one raw configuration value."""

    kind: SchemaKind
    version: int | None


class UnsupportedConfigurationVersionError(ValueError):
    """A syntactically valid version is newer than this build supports."""

    def __init__(self, version: int) -> None:
        super().__init__(
            f"Configuration schema {version} is newer than supported "
            f"schema {SCHEMA_VERSION_V2}."
        )
        self.version = version


class MalformedConfigurationError(ValueError):
    """The raw value cannot be classified as a supported schema."""


def detect_configuration_schema(raw: object) -> SchemaDetection:
    """Classify raw input without validating or normalizing its contents."""
    if not isinstance(raw, Mapping):
        return SchemaDetection(SchemaKind.MALFORMED, None)
    if "schema_version" not in raw:
        return SchemaDetection(SchemaKind.PRE_VERSIONED, 0)
    version = raw["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        return SchemaDetection(SchemaKind.MALFORMED, None)
    if version == 0:
        return SchemaDetection(SchemaKind.PRE_VERSIONED, 0)
    if version == SCHEMA_VERSION_V1:
        return SchemaDetection(SchemaKind.VERSION_1, version)
    if version == SCHEMA_VERSION_V2:
        return SchemaDetection(SchemaKind.VERSION_2, version)
    if version > SCHEMA_VERSION_V2:
        return SchemaDetection(SchemaKind.UNSUPPORTED_FUTURE, version)
    return SchemaDetection(SchemaKind.MALFORMED, version)


def _clock_widget(color: str, font: FontChoice) -> WidgetConfig:
    return WidgetConfig(
        type=WidgetType.CLOCK,
        settings=ClockSettings(
            format=ClockFormat.HOUR_24,
            show_seconds=False,
            blink_separator=False,
            time_zone="local",
            color=color,
            horizontal_alignment=HorizontalAlignment.CENTER,
            vertical_alignment=VerticalAlignment.CENTER,
            font=font,
        ),
    )


def migrate_configuration_v1_to_v2(
    configuration: ConfigurationDocument,
) -> ConfigurationDocumentV2:
    """Return a deterministic, file-independent schema-v2 equivalent."""
    if not isinstance(configuration, ConfigurationDocument):
        raise TypeError(
            "configuration must be a validated schema-v1 "
            "ConfigurationDocument."
        )
    profiles: dict[str, ProfileConfigV2] = {}
    for name, profile in configuration.profiles.items():
        profiles[name] = ProfileConfigV2(
            brightness=profile.brightness,
            canvas=CanvasConfig(
                width=MATRIX_WIDTH,
                height=MATRIX_HEIGHT,
                background_color="#000000",
            ),
            regions=(
                RegionConfig(
                    id=LEGACY_FRONT_CLOCK_REGION_ID,
                    name="Front clock",
                    x=0,
                    y=0,
                    width=profile.layout.front_clock_width,
                    height=MATRIX_HEIGHT,
                    enabled=True,
                    background_color=None,
                    widget=_clock_widget(
                        profile.clock_color,
                        FontChoice.LEGACY_3X7,
                    ),
                ),
                RegionConfig(
                    id=LEGACY_SCROLLING_TEXT_REGION_ID,
                    name="Scrolling text",
                    x=(
                        profile.layout.front_clock_width
                        + profile.layout.gap_width
                    ),
                    y=0,
                    width=profile.layout.scroll_text_width,
                    height=MATRIX_HEIGHT,
                    enabled=True,
                    background_color=None,
                    widget=WidgetConfig(
                        type=WidgetType.SCROLLING_TEXT,
                        settings=ScrollingTextSettings(
                            content=profile.scrolling_text,
                            direction=ScrollDirection.LEFT,
                            speed=profile.scroll_speed,
                            color=profile.text_color,
                            initial_alignment=HorizontalAlignment.END,
                            vertical_alignment=VerticalAlignment.CENTER,
                            font=FontChoice.LEGACY_5X7,
                            uppercase=True,
                            trailing_gap=MARQUEE_TRAILING_GAP,
                        ),
                    ),
                ),
                RegionConfig(
                    id=LEGACY_REAR_CLOCK_REGION_ID,
                    name="Rear clock",
                    x=(
                        profile.layout.front_clock_width
                        + profile.layout.gap_width
                        + profile.layout.scroll_text_width
                    ),
                    y=0,
                    width=profile.layout.rear_clock_width,
                    height=MATRIX_HEIGHT,
                    enabled=True,
                    background_color=None,
                    widget=_clock_widget(
                        profile.clock_color,
                        FontChoice.LEGACY_5X7,
                    ),
                ),
            ),
        )
    return ConfigurationDocumentV2(
        schema_version=SCHEMA_VERSION_V2,
        active_profile=configuration.active_profile,
        profiles=profiles,
    )


def parse_or_migrate_configuration_v2(
    raw: object,
) -> ConfigurationDocumentV2:
    """Strictly parse v2 or validate and deterministically migrate v0/v1."""
    detection = detect_configuration_schema(raw)
    if detection.kind is SchemaKind.UNSUPPORTED_FUTURE:
        assert detection.version is not None
        raise UnsupportedConfigurationVersionError(detection.version)
    if detection.kind is SchemaKind.MALFORMED:
        raise MalformedConfigurationError(
            "configuration schema could not be determined."
        )
    if detection.kind is SchemaKind.VERSION_2:
        return ConfigurationDocumentV2.from_dict(raw)
    if detection.kind in {SchemaKind.PRE_VERSIONED, SchemaKind.VERSION_1}:
        assert isinstance(raw, Mapping)
        normalized = (
            {**raw, "schema_version": SCHEMA_VERSION_V1}
            if detection.kind is SchemaKind.PRE_VERSIONED
            else raw
        )
        return migrate_configuration_v1_to_v2(
            ConfigurationDocument.from_dict(normalized)
        )
    raise AssertionError("Unhandled configuration schema classification.")
