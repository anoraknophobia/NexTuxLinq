"""Strict compatibility between schema-v1 forms and canonical schema v2."""

from __future__ import annotations

from nextuxlinq.migration import (
    LEGACY_FRONT_CLOCK_REGION_ID,
    LEGACY_REAR_CLOCK_REGION_ID,
    LEGACY_SCROLLING_TEXT_REGION_ID,
    migrate_configuration_v1_to_v2,
)
from nextuxlinq.models import (
    SCHEMA_VERSION,
    ConfigurationDocument,
    LayoutConfig,
    ProfileConfig,
)
from nextuxlinq.models_v2 import (
    SCHEMA_VERSION_V2,
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


class LegacyEditorUnsupportedError(RuntimeError):
    """The current free-layout state cannot be edited by the legacy form."""


def _unsupported() -> LegacyEditorUnsupportedError:
    return LegacyEditorUnsupportedError(
        "The active free-layout profile is read-only in the current editor."
    )


def _clock(
    region: RegionConfig,
    *,
    font: FontChoice,
) -> ClockSettings:
    if (
        not region.enabled
        or region.background_color is not None
        or region.widget.type is not WidgetType.CLOCK
        or type(region.widget.settings) is not ClockSettings
    ):
        raise _unsupported()
    settings = region.widget.settings
    assert type(settings) is ClockSettings
    if (
        settings.format is not ClockFormat.HOUR_24
        or settings.show_seconds
        or settings.blink_separator
        or settings.time_zone != "local"
        or settings.horizontal_alignment is not HorizontalAlignment.CENTER
        or settings.vertical_alignment is not VerticalAlignment.CENTER
        or settings.font is not font
    ):
        raise _unsupported()
    return settings


def legacy_profile_from_v2(profile: ProfileConfigV2) -> ProfileConfig:
    """Return the exact legacy-form projection or reject without flattening."""
    if (
        type(profile) is not ProfileConfigV2
        or profile.canvas.background_color != "#000000"
        or len(profile.regions) != 3
    ):
        raise _unsupported()
    front, scrolling, rear = profile.regions
    if (
        front.id != LEGACY_FRONT_CLOCK_REGION_ID
        or scrolling.id != LEGACY_SCROLLING_TEXT_REGION_ID
        or rear.id != LEGACY_REAR_CLOCK_REGION_ID
        or (front.x, front.y, front.width, front.height) != (0, 0, 24, 10)
        or (scrolling.x, scrolling.y, scrolling.width, scrolling.height)
        != (28, 0, 31, 10)
        or (rear.x, rear.y, rear.width, rear.height) != (59, 0, 31, 10)
    ):
        raise _unsupported()
    front_clock = _clock(front, font=FontChoice.LEGACY_3X7)
    rear_clock = _clock(rear, font=FontChoice.LEGACY_5X7)
    if front_clock.color != rear_clock.color:
        raise _unsupported()
    if (
        not scrolling.enabled
        or scrolling.background_color is not None
        or scrolling.widget.type is not WidgetType.SCROLLING_TEXT
        or type(scrolling.widget.settings) is not ScrollingTextSettings
    ):
        raise _unsupported()
    text = scrolling.widget.settings
    assert type(text) is ScrollingTextSettings
    if (
        text.direction is not ScrollDirection.LEFT
        or text.initial_alignment is not HorizontalAlignment.END
        or text.vertical_alignment is not VerticalAlignment.CENTER
        or text.font is not FontChoice.LEGACY_5X7
        or not text.uppercase
        or text.trailing_gap != MARQUEE_TRAILING_GAP
    ):
        raise _unsupported()
    return ProfileConfig(
        scrolling_text=text.content,
        text_color=text.color,
        clock_color=front_clock.color,
        scroll_speed=text.speed,
        brightness=profile.brightness,
        layout=LayoutConfig(),
    )


def legacy_configuration_from_v2(
    configuration: ConfigurationDocumentV2,
) -> ConfigurationDocument:
    """Project a complete representable document for the current GUI."""
    if type(configuration) is not ConfigurationDocumentV2:
        raise TypeError(
            "configuration must be a ConfigurationDocumentV2."
        )
    return ConfigurationDocument(
        schema_version=SCHEMA_VERSION,
        active_profile=configuration.active_profile,
        profiles={
            name: legacy_profile_from_v2(profile)
            for name, profile in configuration.profiles.items()
        },
    )


def _migrate_profile(name: str, profile: ProfileConfig) -> ProfileConfigV2:
    migrated = migrate_configuration_v1_to_v2(
        ConfigurationDocument(
            schema_version=SCHEMA_VERSION,
            active_profile=name,
            profiles={name: profile},
        )
    )
    return migrated.profiles[name]


def apply_legacy_profile(
    configuration: ConfigurationDocumentV2,
    profile_name: str,
    profile: ProfileConfig,
) -> ConfigurationDocumentV2:
    """Apply one legacy-form profile without altering other free-layout data."""
    if type(configuration) is not ConfigurationDocumentV2:
        raise TypeError(
            "configuration must be a ConfigurationDocumentV2."
        )
    if type(profile) is not ProfileConfig:
        raise TypeError("profile must be a ProfileConfig.")
    if profile_name != configuration.active_profile:
        raise LegacyEditorUnsupportedError(
            "The current editor may update only the active profile."
        )
    current = configuration.profiles.get(profile_name)
    if current is None:
        raise _unsupported()
    legacy_profile_from_v2(current)
    profiles = dict(configuration.profiles)
    profiles[profile_name] = _migrate_profile(profile_name, profile)
    return ConfigurationDocumentV2(
        schema_version=SCHEMA_VERSION_V2,
        active_profile=profile_name,
        profiles=profiles,
    )


def apply_legacy_configuration(
    current: ConfigurationDocumentV2,
    requested: ConfigurationDocument,
) -> ConfigurationDocumentV2:
    """Translate a legacy save only when it cannot discard free-layout data."""
    if type(current) is not ConfigurationDocumentV2:
        raise TypeError("current must be a ConfigurationDocumentV2.")
    if type(requested) is not ConfigurationDocument:
        raise TypeError("requested must be a ConfigurationDocument.")
    legacy_configuration_from_v2(current)
    if set(requested.profiles) != set(current.profiles):
        raise LegacyEditorUnsupportedError(
            "The current editor cannot add or remove free-layout profiles."
        )
    migrated = migrate_configuration_v1_to_v2(requested)
    return ConfigurationDocumentV2(
        schema_version=SCHEMA_VERSION_V2,
        active_profile=migrated.active_profile,
        profiles={
            name: migrated.profiles[name]
            for name in current.profiles
        },
    )
