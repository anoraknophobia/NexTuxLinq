from datetime import datetime

from nexlinq.creator import FRAME_SIZE, MATRIX_WIDTH, FrameBuffer
from nexlinq.default_display import (
    CACHYOS_TURQUOISE,
    FRONT_CLOCK_WIDTH,
    GAP_WIDTH,
    REAR_CLOCK_WIDTH,
    SCROLL_TEXT_WIDTH,
    FONT_5X7,
    draw_text_clipped,
    render_default_frame,
    set_region_pixel,
    text_width,
)


def raw_pixel(payload: bytes, x: int, y: int) -> bytes:
    offset = (y * MATRIX_WIDTH + x) * 3
    return payload[offset : offset + 3]


def region_pixel(
    payload: bytes, *, region_x: int, region_width: int, x: int, y: int
) -> bytes:
    if not region_x <= x < region_x + region_width:
        raise ValueError("Pixel is outside the requested region")
    raw_region_x = MATRIX_WIDTH - region_x - region_width
    raw_x = raw_region_x + (x - region_x)
    return raw_pixel(payload, raw_x, y)


def test_default_widths_fill_the_matrix() -> None:
    assert (FRONT_CLOCK_WIDTH, GAP_WIDTH, SCROLL_TEXT_WIDTH, REAR_CLOCK_WIDTH) == (
        24,
        4,
        31,
        31,
    )
    assert FRONT_CLOCK_WIDTH + GAP_WIDTH + SCROLL_TEXT_WIDTH + REAR_CLOCK_WIDTH == 90


def test_regions_reverse_position_without_mirroring_content() -> None:
    frame = FrameBuffer()
    set_region_pixel(
        frame,
        region_x=0,
        region_width=24,
        x=0,
        y=0,
        color=(1, 2, 3),
    )
    set_region_pixel(
        frame,
        region_x=0,
        region_width=24,
        x=23,
        y=0,
        color=(4, 5, 6),
    )

    payload = frame.to_bytes()
    first_raw_offset = 66 * 3
    last_raw_offset = 89 * 3

    assert payload[first_raw_offset : first_raw_offset + 3] == bytes((1, 2, 3))
    assert payload[last_raw_offset : last_raw_offset + 3] == bytes((4, 5, 6))


def test_clipping_prevents_text_from_crossing_region_boundaries() -> None:
    frame = FrameBuffer()
    draw_text_clipped(
        frame,
        "C",
        font=FONT_5X7,
        origin_x=8,
        origin_y=1,
        clip_x=10,
        clip_width=2,
        color=(10, 20, 30),
    )

    payload = frame.to_bytes()

    raw_region_x = MATRIX_WIDTH - 10 - 2
    for y in range(10):
        for raw_x in range(90):
            if raw_region_x <= raw_x < raw_region_x + 2:
                continue
            assert raw_pixel(payload, raw_x, y) == bytes((0, 0, 0))


def test_text_columns_remain_left_to_right_inside_reversed_region() -> None:
    frame = FrameBuffer()
    draw_text_clipped(
        frame,
        "C",
        font=FONT_5X7,
        origin_x=0,
        origin_y=0,
        clip_x=0,
        clip_width=24,
        color=(10, 20, 30),
    )

    payload = frame.to_bytes()
    raw_region_x = MATRIX_WIDTH - 24
    top_row = [
        raw_pixel(payload, raw_region_x + column, 0) != bytes((0, 0, 0))
        for column in range(5)
    ]

    assert top_row == [False, True, True, True, True]


def test_default_frame_has_black_four_pixel_gap() -> None:
    frame = render_default_frame(datetime(2026, 7, 27, 20, 36), scroll_step=20)
    payload = frame.to_bytes()

    assert len(payload) == FRAME_SIZE
    for y in range(10):
        for x in range(24, 28):
            assert region_pixel(
                payload,
                region_x=24,
                region_width=4,
                x=x,
                y=y,
            ) == bytes((0, 0, 0))


def test_marquee_uses_turquoise_and_stays_inside_its_region() -> None:
    frame = render_default_frame(datetime(2026, 7, 27, 20, 36), scroll_step=35)
    payload = frame.to_bytes()
    turquoise = bytes(CACHYOS_TURQUOISE)

    turquoise_positions = [
        (x, y)
        for y in range(10)
        for x in range(28, 59)
        if region_pixel(
            payload,
            region_x=28,
            region_width=31,
            x=x,
            y=y,
        ) == turquoise
    ]

    assert turquoise_positions
    assert all(28 <= x < 59 for x, _ in turquoise_positions)


def test_cachyos_bitmap_is_wider_than_the_marquee_region() -> None:
    assert text_width("CACHYOS", FONT_5X7) > SCROLL_TEXT_WIDTH
