"""Built-in Matrix900 dynamic display defaults."""

from nextuxlinq.models import (
    SCHEMA_VERSION,
    ConfigurationDocument,
    LayoutConfig,
    ProfileConfig,
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
