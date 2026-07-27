from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Sequence

from nexlinq.configuration import send_report_sequence
from nexlinq.creator import (
    MATRIX_WIDTH,
    FrameBuffer,
    build_creator_upload_reports,
)
from nexlinq.defaults import (
    DEFAULT_CLOCK_COLOR,
    DEFAULT_TEXT,
    DEFAULT_TEXT_COLOR,
    default_profile,
)
from nexlinq.device import find_matrix900
from nexlinq.models import color_to_rgb
from nexlinq.rendering import (
    FONT_3X7,
    FONT_5X7,
    MARQUEE_TRAILING_GAP,
    draw_text_clipped,
    render_profile_frame,
    set_region_pixel,
    text_width,
)

_DEFAULT_LAYOUT = default_profile().layout
FRONT_CLOCK_WIDTH = _DEFAULT_LAYOUT.front_clock_width
GAP_WIDTH = _DEFAULT_LAYOUT.gap_width
SCROLL_TEXT_WIDTH = _DEFAULT_LAYOUT.scroll_text_width
REAR_CLOCK_WIDTH = _DEFAULT_LAYOUT.rear_clock_width

FRONT_CLOCK_X = 0
GAP_X = FRONT_CLOCK_X + FRONT_CLOCK_WIDTH
SCROLL_TEXT_X = GAP_X + GAP_WIDTH
REAR_CLOCK_X = SCROLL_TEXT_X + SCROLL_TEXT_WIDTH

CLOCK_COLOR = color_to_rgb(DEFAULT_CLOCK_COLOR)
CACHYOS_TURQUOISE = color_to_rgb(DEFAULT_TEXT_COLOR)
DEFAULT_FPS = 5.0


def _validate_layout() -> None:
    widths = (FRONT_CLOCK_WIDTH, GAP_WIDTH, SCROLL_TEXT_WIDTH, REAR_CLOCK_WIDTH)
    if sum(widths) != MATRIX_WIDTH:
        raise RuntimeError(f"Default layout widths must add up to {MATRIX_WIDTH}: {widths}")


def render_default_frame(now: datetime, scroll_step: int) -> FrameBuffer:
    """Render the existing default display with byte-compatible behavior."""
    _validate_layout()
    return render_profile_frame(default_profile(), now, scroll_step)


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
