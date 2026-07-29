"""Built-in Matrix900 dynamic display defaults."""

from nextuxlinq.models import (
    SCHEMA_VERSION,
    ConfigurationDocument,
    LayoutConfig,
    ProfileConfig,
)
from nextuxlinq.models_v2 import (
    SCHEMA_VERSION_V2,
    CanvasConfig,
    ConfigurationDocumentV2,
    ProfileConfigV2,
)

DEFAULT_PROFILE_NAME = "default"
DEFAULT_TEXT = "CACHYOS"
DEFAULT_TEXT_COLOR = "#00D4C7"
DEFAULT_CLOCK_COLOR = "#FFFFFF"
DEFAULT_SCROLL_SPEED = 5.0


def default_profile() -> ProfileConfig:
    """Return the current 24/4/31/31 dynamic display profile."""
    return ProfileConfig(
        scrolling_text=DEFAULT_TEXT,
        text_color=DEFAULT_TEXT_COLOR,
        clock_color=DEFAULT_CLOCK_COLOR,
        scroll_speed=DEFAULT_SCROLL_SPEED,
        brightness=None,
        layout=LayoutConfig(),
    )


def default_configuration() -> ConfigurationDocument:
    """Return a fresh built-in configuration document."""
    return ConfigurationDocument(
        schema_version=SCHEMA_VERSION,
        active_profile=DEFAULT_PROFILE_NAME,
        profiles={DEFAULT_PROFILE_NAME: default_profile()},
    )


def empty_profile_v2() -> ProfileConfigV2:
    """Return the future empty free-layout profile without activating it."""
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(
            width=90,
            height=10,
            background_color="#000000",
        ),
        regions=(),
    )


def legacy_equivalent_configuration_v2() -> ConfigurationDocumentV2:
    """Return a schema-v2 equivalent of the active schema-v1 default."""
    from nextuxlinq.migration import migrate_configuration_v1_to_v2

    configuration = migrate_configuration_v1_to_v2(default_configuration())
    assert configuration.schema_version == SCHEMA_VERSION_V2
    return configuration
