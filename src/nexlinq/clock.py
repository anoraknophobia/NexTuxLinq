"""Fullscreen digital-clock encoding for the Matrix900."""

from __future__ import annotations

from nexlinq.layout import WidgetCategory, build_widget_report

DIGITAL_CLOCK_WIDGET_ID = 0x01

_FULLSCREEN_DIGITAL_CLOCK_OPTIONS = bytes.fromhex(
    "02 01 16 1e 00 00 0d 01 ff ff ff 00 00 00 ff 00 00 00 00 ff"
)


def build_fullscreen_digital_clock_report() -> bytes:
    """Build the captured fullscreen digital-clock configuration."""
    return build_widget_report(
        layout_id=0x01,
        slot_index=0,
        category=WidgetCategory.TIME_AND_DATE,
        widget_id=DIGITAL_CLOCK_WIDGET_ID,
        options=_FULLSCREEN_DIGITAL_CLOCK_OPTIONS,
    )
