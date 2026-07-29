"""Tests for deterministic schema detection and v1-to-v2 migration."""

from __future__ import annotations

import copy
import json

import pytest

from nextuxlinq.config_store import ConfigStore, parse_configuration
from nextuxlinq.defaults import (
    default_configuration,
    empty_profile_v2,
    legacy_equivalent_configuration_v2,
)
from nextuxlinq.migration import (
    LEGACY_FRONT_CLOCK_REGION_ID,
    LEGACY_REAR_CLOCK_REGION_ID,
    LEGACY_SCROLLING_TEXT_REGION_ID,
    MalformedConfigurationError,
    SchemaDetection,
    SchemaKind,
    UnsupportedConfigurationVersionError,
    detect_configuration_schema,
    migrate_configuration_v1_to_v2,
    parse_or_migrate_configuration_v2,
)
from nextuxlinq.models import ConfigurationDocument, ProfileConfig
from nextuxlinq.models_v2 import ConfigurationDocumentV2
from nextuxlinq.widget_models import (
    ClockSettings,
    FontChoice,
    ScrollingTextSettings,
    WidgetType,
)


def v1_profile(**changes: object) -> ProfileConfig:
    raw = default_configuration().active.to_dict()
    raw.update(changes)
    return ProfileConfig.from_dict(raw)


def expected_migrated_profile(
    *,
    scrolling_text: str,
    text_color: str,
    clock_color: str,
    scroll_speed: float,
    brightness: int | None,
) -> dict[str, object]:
    clock_settings = {
        "format": "24h",
        "show_seconds": False,
        "blink_separator": False,
        "time_zone": "local",
        "color": clock_color,
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
    }
    return {
        "brightness": brightness,
        "canvas": {
            "width": 90,
            "height": 10,
            "background_color": "#000000",
        },
        "regions": [
            {
                "id": LEGACY_FRONT_CLOCK_REGION_ID,
                "name": "Front clock",
                "x": 0,
                "y": 0,
                "width": 24,
                "height": 10,
                "enabled": True,
                "background_color": None,
                "widget": {
                    "type": "clock",
                    "settings": {
                        **clock_settings,
                        "font": "3x7",
                    },
                },
            },
            {
                "id": LEGACY_SCROLLING_TEXT_REGION_ID,
                "name": "Scrolling text",
                "x": 28,
                "y": 0,
                "width": 31,
                "height": 10,
                "enabled": True,
                "background_color": None,
                "widget": {
                    "type": "scrolling_text",
                    "settings": {
                        "content": scrolling_text,
                        "direction": "left",
                        "speed": scroll_speed,
                        "color": text_color,
                        "initial_alignment": "end",
                        "vertical_alignment": "center",
                        "font": "5x7",
                        "uppercase": True,
                        "trailing_gap": 6,
                    },
                },
            },
            {
                "id": LEGACY_REAR_CLOCK_REGION_ID,
                "name": "Rear clock",
                "x": 59,
                "y": 0,
                "width": 31,
                "height": 10,
                "enabled": True,
                "background_color": None,
                "widget": {
                    "type": "clock",
                    "settings": {
                        **clock_settings,
                        "font": "5x7",
                    },
                },
            },
        ],
    }


def canonical_bytes(configuration: ConfigurationDocumentV2) -> bytes:
    return json.dumps(
        configuration.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_default_migration_matches_complete_schema_v2_fixture() -> None:
    migrated = migrate_configuration_v1_to_v2(default_configuration())
    expected = {
        "schema_version": 2,
        "active_profile": "default",
        "profiles": {
            "default": expected_migrated_profile(
                scrolling_text="CACHYOS",
                text_color="#00D4C7",
                clock_color="#FFFFFF",
                scroll_speed=5.0,
                brightness=None,
            )
        },
    }

    assert migrated.to_dict() == expected
    assert migrated.active.brightness is None
    covered_columns = {
        x
        for region in migrated.active.regions
        for x in range(region.x, region.x + region.width)
    }
    assert covered_columns.isdisjoint(range(24, 28))


def test_multiple_profile_migration_matches_complete_schema_v2_fixture() -> None:
    source = ConfigurationDocument(
        schema_version=1,
        active_profile="zeta",
        profiles={
            "zeta": v1_profile(
                scrolling_text="SECOND",
                text_color="#112233",
                clock_color="#445566",
                scroll_speed=0.6,
                brightness=80,
            ),
            "alpha": v1_profile(
                scrolling_text="FIRST",
                text_color="#ABCDEF",
                clock_color="#102030",
                scroll_speed=20.0,
                brightness=None,
            ),
        },
    )
    expected = {
        "schema_version": 2,
        "active_profile": "zeta",
        "profiles": {
            "alpha": expected_migrated_profile(
                scrolling_text="FIRST",
                text_color="#ABCDEF",
                clock_color="#102030",
                scroll_speed=20.0,
                brightness=None,
            ),
            "zeta": expected_migrated_profile(
                scrolling_text="SECOND",
                text_color="#112233",
                clock_color="#445566",
                scroll_speed=0.6,
                brightness=80,
            ),
        },
    }

    assert migrate_configuration_v1_to_v2(source).to_dict() == expected


def test_default_v1_profile_migrates_to_exact_legacy_geometry() -> None:
    migrated = migrate_configuration_v1_to_v2(default_configuration())
    profile = migrated.active

    assert migrated.schema_version == 2
    assert profile.canvas.to_dict() == {
        "width": 90,
        "height": 10,
        "background_color": "#000000",
    }
    geometry = [
        (item.id, item.x, item.y, item.width, item.height)
        for item in profile.regions
    ]
    assert geometry == [
        (LEGACY_FRONT_CLOCK_REGION_ID, 0, 0, 24, 10),
        (LEGACY_SCROLLING_TEXT_REGION_ID, 28, 0, 31, 10),
        (LEGACY_REAR_CLOCK_REGION_ID, 59, 0, 31, 10),
    ]
    assert not any(
        region.x <= x < region.x + region.width
        for region in profile.regions
        for x in range(24, 28)
    )


def test_migration_preserves_all_v1_profile_settings() -> None:
    source_profile = v1_profile(
        scrolling_text="MATRIX 900",
        text_color="#123456",
        clock_color="#ABCDEF",
        scroll_speed=0.6,
        brightness=70,
    )
    source = ConfigurationDocument(
        schema_version=1,
        active_profile="custom",
        profiles={"custom": source_profile},
    )
    migrated = migrate_configuration_v1_to_v2(source)
    front, scrolling, rear = migrated.active.regions

    assert migrated.active_profile == "custom"
    assert migrated.active.brightness == 70
    assert front.name == "Front clock"
    assert rear.name == "Rear clock"
    assert front.widget.type is rear.widget.type is WidgetType.CLOCK
    assert isinstance(front.widget.settings, ClockSettings)
    assert isinstance(rear.widget.settings, ClockSettings)
    assert front.widget.settings.color == rear.widget.settings.color == "#ABCDEF"
    assert front.widget.settings.format.value == "24h"
    assert front.widget.settings.show_seconds is False
    assert front.widget.settings.blink_separator is False
    assert front.widget.settings.time_zone == "local"
    assert front.widget.settings.font is FontChoice.LEGACY_3X7
    assert rear.widget.settings.font is FontChoice.LEGACY_5X7
    assert isinstance(scrolling.widget.settings, ScrollingTextSettings)
    assert scrolling.widget.settings.content == "MATRIX 900"
    assert scrolling.widget.settings.color == "#123456"
    assert scrolling.widget.settings.speed == 0.6
    assert scrolling.widget.settings.direction.value == "left"
    assert scrolling.widget.settings.initial_alignment.value == "end"
    assert scrolling.widget.settings.trailing_gap == 6
    assert scrolling.widget.settings.font is FontChoice.LEGACY_5X7


def test_multiple_profiles_and_active_profile_are_preserved() -> None:
    source = ConfigurationDocument(
        schema_version=1,
        active_profile="second",
        profiles={
            "first": v1_profile(scrolling_text="FIRST"),
            "second": v1_profile(scrolling_text="SECOND", brightness=40),
        },
    )
    migrated = migrate_configuration_v1_to_v2(source)

    assert list(migrated.profiles) == ["first", "second"]
    assert migrated.active_profile == "second"
    assert migrated.profiles["second"].brightness == 40
    scrolling = migrated.profiles["first"].regions[1].widget.settings
    assert isinstance(scrolling, ScrollingTextSettings)
    assert scrolling.content == "FIRST"


def test_migration_is_deterministic_and_does_not_modify_source() -> None:
    source = default_configuration()
    before = copy.deepcopy(source.to_dict())

    first = migrate_configuration_v1_to_v2(source)
    second = migrate_configuration_v1_to_v2(source)

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert source.to_dict() == before
    assert [item.id for item in first.active.regions] == [
        LEGACY_FRONT_CLOCK_REGION_ID,
        LEGACY_SCROLLING_TEXT_REGION_ID,
        LEGACY_REAR_CLOCK_REGION_ID,
    ]
    assert canonical_bytes(first) == canonical_bytes(second)


def test_equivalent_source_documents_produce_byte_identical_serialization() -> None:
    raw = default_configuration().to_dict()
    first_source = ConfigurationDocument.from_dict(copy.deepcopy(raw))
    second_source = ConfigurationDocument.from_dict(copy.deepcopy(raw))
    first_before = first_source.to_dict()
    second_before = second_source.to_dict()

    first = migrate_configuration_v1_to_v2(first_source)
    second = migrate_configuration_v1_to_v2(second_source)

    assert canonical_bytes(first) == canonical_bytes(second)
    assert first_source.to_dict() == first_before
    assert second_source.to_dict() == second_before


def test_schema_zero_is_validated_as_v1_before_v2_migration() -> None:
    raw = default_configuration().to_dict()
    del raw["schema_version"]

    migrated = parse_or_migrate_configuration_v2(raw)

    assert migrated == migrate_configuration_v1_to_v2(
        parse_configuration(raw)
    )


def test_invalid_v1_is_rejected_before_migration() -> None:
    raw = default_configuration().to_dict()
    raw["profiles"]["default"]["layout"]["gap_width"] = 5

    with pytest.raises(ValueError, match="add up"):
        parse_or_migrate_configuration_v2(raw)


@pytest.mark.parametrize(
    ("raw", "kind", "version"),
    [
        ({}, SchemaKind.PRE_VERSIONED, 0),
        ({"schema_version": 0}, SchemaKind.PRE_VERSIONED, 0),
        ({"schema_version": 1}, SchemaKind.VERSION_1, 1),
        ({"schema_version": 2}, SchemaKind.VERSION_2, 2),
        ({"schema_version": 3}, SchemaKind.UNSUPPORTED_FUTURE, 3),
        ({"schema_version": 99}, SchemaKind.UNSUPPORTED_FUTURE, 99),
        ({"schema_version": True}, SchemaKind.MALFORMED, None),
        ({"schema_version": "2"}, SchemaKind.MALFORMED, None),
        ({"schema_version": -1}, SchemaKind.MALFORMED, None),
        ([], SchemaKind.MALFORMED, None),
    ],
)
def test_schema_detection_is_explicit(
    raw: object,
    kind: SchemaKind,
    version: int | None,
) -> None:
    assert detect_configuration_schema(raw) == SchemaDetection(kind, version)


def test_v2_is_parsed_strictly_without_default_fallback() -> None:
    raw = legacy_equivalent_configuration_v2().to_dict()
    parsed = parse_or_migrate_configuration_v2(raw)

    assert isinstance(parsed, ConfigurationDocumentV2)
    assert parsed.to_dict() == raw

    broken = copy.deepcopy(raw)
    del broken["profiles"]["default"]["canvas"]
    with pytest.raises(ValueError, match="missing fields"):
        parse_or_migrate_configuration_v2(broken)


def test_unsupported_future_schema_has_distinct_error() -> None:
    with pytest.raises(UnsupportedConfigurationVersionError) as raised:
        parse_or_migrate_configuration_v2({"schema_version": 3})

    assert raised.value.version == 3
    assert "newer than supported schema 2" in str(raised.value)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"schema_version": True},
        {"schema_version": "2"},
        {"schema_version": -1},
    ],
)
def test_malformed_schema_does_not_fall_back_to_defaults(raw: object) -> None:
    with pytest.raises(MalformedConfigurationError):
        parse_or_migrate_configuration_v2(raw)


def test_active_defaults_remain_v1_and_future_empty_profile_is_separate() -> None:
    assert default_configuration().schema_version == 1
    assert empty_profile_v2().regions == ()
    assert empty_profile_v2().canvas.background_color == "#000000"
    assert legacy_equivalent_configuration_v2().schema_version == 2


def test_legacy_parser_remains_available_but_persistence_is_schema_v2(
    tmp_path,
) -> None:
    path = tmp_path / "config.json"
    current = parse_configuration(default_configuration().to_dict())
    canonical = migrate_configuration_v1_to_v2(current)
    ConfigStore(path).save(canonical)

    assert isinstance(current, ConfigurationDocument)
    assert current.schema_version == 1
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    with pytest.raises(ValueError, match="newer than supported schema 1"):
        parse_configuration(legacy_equivalent_configuration_v2().to_dict())
