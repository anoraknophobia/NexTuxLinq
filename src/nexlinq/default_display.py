from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Mapping, Sequence

from nexlinq.configuration import send_report_sequence
from nexlinq.creator import (
    MATRIX_HEIGHT,
    MATRIX_WIDTH,
    FrameBuffer,
    RGB,
    build_creator_upload_reports,
)
from nexlinq.device import find_matrix900

FRONT_CLOCK_WIDTH = 24
GAP_WIDTH = 4
SCROLL_TEXT_WIDTH = 31
REAR_CLOCK_WIDTH = 31

FRONT_CLOCK_X = 0
GAP_X = FRONT_CLOCK_X + FRONT_CLOCK_WIDTH
SCROLL_TEXT_X = GAP_X + GAP_WIDTH
REAR_CLOCK_X = SCROLL_TEXT_X + SCROLL_TEXT_WIDTH

BACKGROUND: RGB = (0x00, 0x00, 0x00)
CLOCK_COLOR: RGB = (0xFF, 0xFF, 0xFF)
CACHYOS_TURQUOISE: RGB = (0x00, 0xD4, 0xC7)
DEFAULT_TEXT = "CACHYOS"
DEFAULT_FPS = 5.0
MARQUEE_TRAILING_GAP = 6

Glyph = tuple[str, ...]

FONT_3X5: Mapping[str, Glyph] = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    ":": ("0", "1", "0", "1", "0"),
}

FONT_5X7: Mapping[str, Glyph] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ":": ("0", "1", "0", "0", "1", "0", "0"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
}


def _validate_layout() -> None:
    widths = (FRONT_CLOCK_WIDTH, GAP_WIDTH, SCROLL_TEXT_WIDTH, REAR_CLOCK_WIDTH)
    if sum(widths) != MATRIX_WIDTH:
        raise RuntimeError(f"Default layout widths must add up to {MATRIX_WIDTH}: {widths}")


def _glyph_width(glyph: Glyph) -> int:
    return len(glyph[0])


def text_width(text: str, font: Mapping[str, Glyph], spacing: int = 1) -> int:
    if not text:
        return 0
    widths = [_glyph_width(font[character]) for character in text]
    return sum(widths) + spacing * (len(widths) - 1)


def set_region_pixel(
    frame: FrameBuffer,
    *,
    region_x: int,
    region_width: int,
    x: int,
    y: int,
    color: RGB,
) -> None:
    """Map a case region to reversed Creator placement without mirroring its content."""

    if region_width <= 0:
        raise ValueError("Region width must be positive")
    if not 0 <= region_x < MATRIX_WIDTH or region_x + region_width > MATRIX_WIDTH:
        raise ValueError("Region must fit inside the Matrix900 width")
    if not region_x <= x < region_x + region_width or not 0 <= y < MATRIX_HEIGHT:
        return

    raw_region_x = MATRIX_WIDTH - region_x - region_width
    raw_x = raw_region_x + (x - region_x)
    frame.set_pixel(raw_x, y, color)


def draw_text_clipped(
    frame: FrameBuffer,
    text: str,
    *,
    font: Mapping[str, Glyph],
    origin_x: int,
    origin_y: int,
    clip_x: int,
    clip_width: int,
    color: RGB,
    spacing: int = 1,
) -> None:
    cursor_x = origin_x
    clip_end = clip_x + clip_width

    for character in text:
        try:
            glyph = font[character]
        except KeyError as error:
            raise ValueError(f"Unsupported display character: {character!r}") from error

        glyph_width = _glyph_width(glyph)
        for row, pixels in enumerate(glyph):
            for column, enabled in enumerate(pixels):
                physical_x = cursor_x + column
                physical_y = origin_y + row
                if enabled == "1" and clip_x <= physical_x < clip_end:
                    set_region_pixel(
                        frame,
                        region_x=clip_x,
                        region_width=clip_width,
                        x=physical_x,
                        y=physical_y,
                        color=color,
                    )
        cursor_x += glyph_width + spacing


def render_default_frame(now: datetime, scroll_step: int) -> FrameBuffer:
    _validate_layout()
    frame = FrameBuffer()
    frame.fill(BACKGROUND)

    clock_text = now.strftime("%H:%M")

    front_width = text_width(clock_text, FONT_3X5)
    draw_text_clipped(
        frame,
        clock_text,
        font=FONT_3X5,
        origin_x=FRONT_CLOCK_X + (FRONT_CLOCK_WIDTH - front_width) // 2,
        origin_y=(MATRIX_HEIGHT - 5) // 2,
        clip_x=FRONT_CLOCK_X,
        clip_width=FRONT_CLOCK_WIDTH,
        color=CLOCK_COLOR,
    )

    marquee_width = text_width(DEFAULT_TEXT, FONT_5X7)
    marquee_cycle = SCROLL_TEXT_WIDTH + marquee_width + MARQUEE_TRAILING_GAP
    marquee_origin = SCROLL_TEXT_X + SCROLL_TEXT_WIDTH - (scroll_step % marquee_cycle)
    draw_text_clipped(
        frame,
        DEFAULT_TEXT,
        font=FONT_5X7,
        origin_x=marquee_origin,
        origin_y=(MATRIX_HEIGHT - 7) // 2,
        clip_x=SCROLL_TEXT_X,
        clip_width=SCROLL_TEXT_WIDTH,
        color=CACHYOS_TURQUOISE,
    )

    rear_width = text_width(clock_text, FONT_5X7)
    draw_text_clipped(
        frame,
        clock_text,
        font=FONT_5X7,
        origin_x=REAR_CLOCK_X + (REAR_CLOCK_WIDTH - rear_width) // 2,
        origin_y=(MATRIX_HEIGHT - 7) // 2,
        clip_x=REAR_CLOCK_X,
        clip_width=REAR_CLOCK_WIDTH,
        color=CLOCK_COLOR,
    )

    return frame


def upload_frame(device_path: str, frame: FrameBuffer) -> None:
    reports = build_creator_upload_reports(frame.to_bytes())
    results = send_report_sequence(device_path, reports)
    echoed = sum(result.response == result.report for result in results)
    if echoed != len(reports):
        raise RuntimeError(f"Creator upload echoed {echoed}/{len(reports)} reports")


def run(*, once: bool = False, fps: float = DEFAULT_FPS) -> None:
    if fps <= 0:
        raise ValueError("FPS must be positive")

    devices = find_matrix900()
    if not devices:
        raise RuntimeError("No supported Matrix900 device found")

    device_path = devices[0].device_path
    frame_interval = 1.0 / fps
    scroll_step = 0
    next_frame_at = time.monotonic()

    print(
        "Starting Matrix900 default display: "
        "24px clock / 4px gap / 31px CACHYOS / 31px clock",
        flush=True,
    )

    while True:
        upload_frame(device_path, render_default_frame(datetime.now(), scroll_step))
        if once:
            return

        scroll_step += 1
        next_frame_at += frame_interval
        delay = next_frame_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_frame_at = time.monotonic()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the default dynamic Matrix900 Creator layout"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="upload one frame and exit",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help=f"animation frame rate (default: {DEFAULT_FPS:g})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run(once=args.once, fps=args.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
