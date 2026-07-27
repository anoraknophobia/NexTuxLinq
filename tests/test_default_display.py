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
    set_physical_pixel,
    text_width,
)


def physical_pixel(payload: bytes, x: int, y: int) -> bytes:
    raw_x = MATRIX_WIDTH - 1 - x
    offset = (y * MATRIX_WIDTH + raw_x) * 3
    return payload[offset : offset + 3]


def test_default_widths_fill_the_matrix() -> None:
    assert (FRONT_CLOCK_WIDTH, GAP_WIDTH, SCROLL_TEXT_WIDTH, REAR_CLOCK_WIDTH) == (
        24,
        4,
        31,
        31,
    )
    assert FRONT_CLOCK_WIDTH + GAP_WIDTH + SCROLL_TEXT_WIDTH + REAR_CLOCK_WIDTH == 90


def test_physical_coordinates_mirror_the_creator_x_axis() -> None:
    frame = FrameBuffer()
    set_physical_pixel(frame, 0, 0, (1, 2, 3))
    set_physical_pixel(frame, 89, 9, (4, 5, 6))

    payload = frame.to_bytes()

    assert payload[(89 * 3) : (89 * 3) + 3] == bytes((1, 2, 3))
    assert payload[-(90 * 3) : -(90 * 3) + 3] == bytes((4, 5, 6))


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

    for y in range(10):
        for x in range(90):
            pixel = physical_pixel(payload, x, y)
            if x < 10 or x >= 12:
                assert pixel == bytes((0, 0, 0))


def test_default_frame_has_black_four_pixel_gap() -> None:
    frame = render_default_frame(datetime(2026, 7, 27, 20, 36), scroll_step=20)
    payload = frame.to_bytes()

    assert len(payload) == FRAME_SIZE
    for y in range(10):
        for x in range(24, 28):
            assert physical_pixel(payload, x, y) == bytes((0, 0, 0))


def test_marquee_uses_turquoise_and_stays_inside_its_region() -> None:
    frame = render_default_frame(datetime(2026, 7, 27, 20, 36), scroll_step=35)
    payload = frame.to_bytes()
    turquoise = bytes(CACHYOS_TURQUOISE)

    turquoise_positions = [
        (x, y)
        for y in range(10)
        for x in range(90)
        if physical_pixel(payload, x, y) == turquoise
    ]

    assert turquoise_positions
    assert all(28 <= x < 59 for x, _ in turquoise_positions)


def test_cachyos_bitmap_is_wider_than_the_marquee_region() -> None:
    assert text_width("CACHYOS", FONT_5X7) > SCROLL_TEXT_WIDTH
