"""Tests for configurable hardware-independent Matrix900 rendering."""

import math
from datetime import datetime
from hashlib import sha256

import pytest

from nexlinq.defaults import default_profile
from nexlinq.models import ProfileConfig
from nexlinq.rendering import (
    FONT_3X7,
    FONT_5X7,
    calculate_scroll_offset,
    profile_scroll_offset,
    render_profile_at_elapsed,
    render_profile_frame,
    validate_font,
)


def configured_profile(**changes: object) -> ProfileConfig:
    raw = default_profile().to_dict()
    raw.update(changes)
    return ProfileConfig.from_dict(raw)


def test_font_covers_the_approved_character_set() -> None:
    for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-_:!?/":
        assert character in FONT_5X7


def test_every_font_glyph_has_valid_geometry_and_pixels() -> None:
    validate_font(FONT_3X7)
    validate_font(FONT_5X7)
    for font in (FONT_3X7, FONT_5X7):
        for glyph in font.values():
            assert len(glyph) == 7
            assert len({len(row) for row in glyph}) == 1
            assert all(set(row) <= {"0", "1"} for row in glyph)


@pytest.mark.parametrize(
    "font",
    [
        {"X": ("1",) * 6},
        {"X": ("1", "11", "1", "1", "1", "1", "1")},
        {"X": ("1", "1", "1", "X", "1", "1", "1")},
    ],
)
def test_font_validation_rejects_malformed_glyphs(
    font: dict[str, tuple[str, ...]],
) -> None:
    with pytest.raises(ValueError):
        validate_font(font)


def test_production_font_mappings_are_immutable() -> None:
    with pytest.raises(TypeError):
        FONT_3X7["X"] = ("1",) * 7  # type: ignore[index]
    with pytest.raises(TypeError):
        FONT_5X7["X"] = ("1",) * 7  # type: ignore[index]


def physical_region_pixel(
    payload: bytes,
    *,
    region_x: int,
    region_width: int,
    x: int,
    y: int,
) -> bytes:
    raw_x = 90 - region_x - region_width + (x - region_x)
    offset = (y * 90 + raw_x) * 3
    return payload[offset : offset + 3]


def test_configurable_content_is_clipped_and_not_mirrored() -> None:
    profile = configured_profile(
        scrolling_text="l",
        text_color="#112233",
        clock_color="#445566",
    )
    frame = render_profile_frame(
        profile, datetime(2026, 7, 27, 20, 36), scroll_offset=31
    ).to_bytes()

    text_color = bytes.fromhex("112233")
    clock_color = bytes.fromhex("445566")
    top_row = [
        physical_region_pixel(
            frame, region_x=28, region_width=31, x=x, y=1
        ) == text_color
        for x in range(28, 33)
    ]
    assert top_row == [True, False, False, False, False]

    raw_scroll_start = 90 - 28 - 31
    for y in range(10):
        for raw_x in range(90):
            offset = (y * 90 + raw_x) * 3
            if frame[offset : offset + 3] == text_color:
                assert raw_scroll_start <= raw_x < raw_scroll_start + 31

    for y in range(10):
        for x in range(28, 59):
            pixel = physical_region_pixel(
                frame, region_x=28, region_width=31, x=x, y=y
            )
            assert pixel != clock_color
    assert any(
        physical_region_pixel(
            frame, region_x=0, region_width=24, x=x, y=y
        ) == clock_color
        for y in range(10)
        for x in range(24)
    )
    assert any(
        physical_region_pixel(
            frame, region_x=59, region_width=31, x=x, y=y
        ) == clock_color
        for y in range(10)
        for x in range(59, 90)
    )
    front_clock_top = [
        physical_region_pixel(
            frame, region_x=0, region_width=24, x=x, y=1
        ) == clock_color
        for x in range(3, 6)
    ]
    rear_clock_top = [
        physical_region_pixel(
            frame, region_x=59, region_width=31, x=x, y=1
        ) == clock_color
        for x in range(62, 67)
    ]
    assert front_clock_top == [True, True, True]
    assert rear_clock_top == [False, True, True, True, False]
    assert profile.scrolling_text == "L"


def test_scroll_speed_can_drive_deterministic_offsets() -> None:
    assert calculate_scroll_offset(0.0, 5.0) == 0
    assert calculate_scroll_offset(0.2, 5.0) == 1
    assert calculate_scroll_offset(3.9, 2.0) == 7
    profile = configured_profile(scroll_speed=2.5)
    assert profile_scroll_offset(profile, 2.0) == 5
    assert (
        render_profile_at_elapsed(
            profile, datetime(2026, 7, 27, 20, 36), 2.0
        ).to_bytes()
        == render_profile_frame(
            profile, datetime(2026, 7, 27, 20, 36), 5
        ).to_bytes()
    )


@pytest.mark.parametrize(
    "elapsed,speed",
    [
        (True, 1.0),
        (1.0, False),
        ("1", 1.0),
        (10**1000, 1.0),
        (1.0, 10**1000),
        (1e308, 1e308),
        (math.nan, 1.0),
        (1.0, math.inf),
    ],
)
def test_scroll_offset_rejects_invalid_numbers(
    elapsed: object, speed: object
) -> None:
    with pytest.raises(ValueError):
        calculate_scroll_offset(elapsed, speed)  # type: ignore[arg-type]


def test_default_renderer_hashes_preserve_previous_bytes() -> None:
    expected = {
        0: "c40da64b2c31449dc66f9dba49d38a31bd4fe465db4deb2452a02389a0b4b3af",
        20: "1403ca29b8748c22a91f9ae84280a64a1c27c86658e4a46ae4d05b8db00e75e0",
        35: "3bfb134dda185d007e919b7a1f067c6fadd1155db22ae023b5833861760b946e",
    }
    now = datetime(2026, 7, 27, 20, 36)
    for offset, digest in expected.items():
        frame = render_profile_frame(default_profile(), now, offset)
        assert sha256(frame.to_bytes()).hexdigest() == digest
