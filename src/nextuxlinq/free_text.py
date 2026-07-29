"""Fullscreen free-text report encoding for the Matrix900."""

from __future__ import annotations

from nextuxlinq.layout import WidgetCategory, build_widget_report
from nextuxlinq.protocol import HID_REPORT_SIZE, REPORT_ID

COMMAND_TEXT_DATA = 0x02
FREE_TEXT_WIDGET_ID = 0x00
TEXT_FIELD_START = 7
TEXT_FIELD_END = 57
MAX_TEXT_BYTES = 49

_FULLSCREEN_FREE_TEXT_OPTIONS = bytes.fromhex(
    "01 01 16 1e 00 00 0d 01 ff ff ff ff 64 00 ff c8 "
    "00 00 00 ff"
)


def build_text_data_report(value: str) -> bytes:
    """Build a command 0x02 report containing ASCII text."""
    if not isinstance(value, str):
        raise TypeError("text value must be a string.")

    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("text currently supports ASCII characters only.") from error

    if not encoded:
        raise ValueError("text must not be empty.")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError(
            f"text must not exceed {MAX_TEXT_BYTES} ASCII bytes."
        )

    report = bytearray(HID_REPORT_SIZE)
    report[0] = REPORT_ID
    report[1] = COMMAND_TEXT_DATA
    report[5] = len(encoded) + 1
    report[6] = 0x00
    report[TEXT_FIELD_START:TEXT_FIELD_END] = bytes([0xFF]) * (
        TEXT_FIELD_END - TEXT_FIELD_START
    )
    report[TEXT_FIELD_START : TEXT_FIELD_START + len(encoded)] = encoded
    return bytes(report)


def build_fullscreen_free_text_widget_report() -> bytes:
    """Build the captured fullscreen free-text widget configuration."""
    return build_widget_report(
        layout_id=0x01,
        slot_index=0,
        category=WidgetCategory.TIME_AND_DATE,
        widget_id=FREE_TEXT_WIDGET_ID,
        options=_FULLSCREEN_FREE_TEXT_OPTIONS,
    )


def build_fullscreen_free_text_reports(value: str) -> tuple[bytes, bytes]:
    """Build the text-data and widget-configuration report sequence."""
    return (
        build_text_data_report(value),
        build_fullscreen_free_text_widget_report(),
    )
