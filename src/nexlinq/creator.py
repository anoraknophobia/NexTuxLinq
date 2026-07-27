from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Sequence

MATRIX_WIDTH = 90
MATRIX_HEIGHT = 10
RGB_CHANNELS = 3
FRAME_SIZE = MATRIX_WIDTH * MATRIX_HEIGHT * RGB_CHANNELS
HID_REPORT_SIZE = 64
CREATOR_DATA_BYTES_PER_REPORT = 56

RGB = tuple[int, int, int]


def _validate_color(color: RGB) -> RGB:
    if len(color) != RGB_CHANNELS:
        raise ValueError("RGB colors must contain exactly three components")

    normalized = tuple(int(component) for component in color)
    if any(component < 0 or component > 255 for component in normalized):
        raise ValueError("RGB components must be in the range 0..255")

    return normalized  # type: ignore[return-value]


@dataclass(slots=True)
class FrameBuffer:
    """A row-major 90x10 RGB888 frame for the Matrix900."""

    width: int = MATRIX_WIDTH
    height: int = MATRIX_HEIGHT
    _data: bytearray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.width != MATRIX_WIDTH or self.height != MATRIX_HEIGHT:
            raise ValueError(
                f"Matrix900 Creator frames must be {MATRIX_WIDTH}x{MATRIX_HEIGHT}"
            )
        self._data = bytearray(FRAME_SIZE)

    def to_bytes(self) -> bytes:
        return bytes(self._data)

    def fill(self, color: RGB) -> None:
        red, green, blue = _validate_color(color)
        self._data[:] = bytes((red, green, blue)) * (self.width * self.height)

    def set_pixel(self, x: int, y: int, color: RGB) -> None:
        self._validate_bounds(x, y)
        offset = (y * self.width + x) * RGB_CHANNELS
        self._data[offset : offset + RGB_CHANNELS] = bytes(_validate_color(color))

    def fill_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: RGB,
    ) -> None:
        if width < 0 or height < 0:
            raise ValueError("Rectangle dimensions must not be negative")
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise ValueError("Rectangle is outside the Matrix900 frame")

        pixel = bytes(_validate_color(color))
        row = pixel * width

        for row_index in range(y, y + height):
            start = (row_index * self.width + x) * RGB_CHANNELS
            self._data[start : start + len(row)] = row

    def _validate_bounds(self, x: int, y: int) -> None:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise ValueError(f"Pixel ({x}, {y}) is outside the Matrix900 frame")


def build_creator_init_report() -> bytes:
    report = bytearray(HID_REPORT_SIZE)
    report[:7] = bytes((0x01, 0x05, 0xFF, 0x00, 0x00, 0x01, 0x01))
    return bytes(report)


def build_creator_frame_reports(frame: bytes | bytearray | memoryview) -> tuple[bytes, ...]:
    raw_frame = bytes(frame)
    if len(raw_frame) != FRAME_SIZE:
        raise ValueError(
            f"Matrix900 Creator frame must contain exactly {FRAME_SIZE} bytes"
        )

    reports: list[bytes] = []

    for index, start in enumerate(
        range(0, FRAME_SIZE, CREATOR_DATA_BYTES_PER_REPORT)
    ):
        chunk = raw_frame[start : start + CREATOR_DATA_BYTES_PER_REPORT]
        report = bytearray(HID_REPORT_SIZE)
        report[0] = 0x01
        report[1] = 0x05
        report[2:5] = index.to_bytes(3, byteorder="big")
        report[5] = 2 + len(chunk)
        report[6] = MATRIX_WIDTH
        report[7] = MATRIX_HEIGHT
        report[8 : 8 + len(chunk)] = chunk
        reports.append(bytes(report))

    return tuple(reports)


def build_creator_widget_report() -> bytes:
    report = bytearray(HID_REPORT_SIZE)
    report[:21] = bytes.fromhex(
        "01 03 01 00 00 22 01 04 01 01 02 "
        "ff ff ff 00 00 00 ff 00 64 00"
    )
    return bytes(report)


def build_creator_upload_reports(
    frame: bytes | bytearray | memoryview,
) -> tuple[bytes, ...]:
    return (
        build_creator_init_report(),
        *build_creator_frame_reports(frame),
        build_creator_widget_report(),
    )


def resolve_percentage_widths(
    percentages: Sequence[float],
    *,
    total_width: int = MATRIX_WIDTH,
) -> tuple[int, ...]:
    """Convert percentage region sizes to exact pixel widths.

    Largest remainders receive the remaining pixels from left to right.
    """

    if not percentages:
        raise ValueError("At least one region is required")
    if total_width <= 0:
        raise ValueError("Total width must be positive")
    if any(value < 0 for value in percentages):
        raise ValueError("Region percentages must not be negative")

    percentage_sum = sum(percentages)
    if abs(percentage_sum - 100.0) > 1e-6:
        raise ValueError("Region percentages must add up to 100")

    exact_widths = [total_width * value / 100.0 for value in percentages]
    widths = [floor(value) for value in exact_widths]
    remaining = total_width - sum(widths)

    ranked_indices = sorted(
        range(len(exact_widths)),
        key=lambda index: (-(exact_widths[index] - widths[index]), index),
    )

    for index in ranked_indices[:remaining]:
        widths[index] += 1

    return tuple(widths)
