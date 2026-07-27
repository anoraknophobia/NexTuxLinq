"""Fullscreen Time and Date widget encoding for the Matrix900."""

from __future__ import annotations

from enum import IntEnum

from nexlinq.layout import WidgetCategory, build_widget_report


class TimeAndDateWidget(IntEnum):
    """Verified Time and Date widget identifiers."""

    DIGITAL_CLOCK = 0x01
    GRAPHICAL_CLOCK = 0x02
    DATE = 0x03
    WEEK_NUMBER = 0x04


_FULLSCREEN_PROFILES: dict[TimeAndDateWidget, bytes] = {
    TimeAndDateWidget.DIGITAL_CLOCK: bytes.fromhex(
        "02 01 16 1e 00 00 0d 01 ff ff ff 00 00 00 ff 00 00 00 00 ff"
    ),
    TimeAndDateWidget.GRAPHICAL_CLOCK: bytes.fromhex(
        "02 01 16 1e 00 00 0d 01 ff 00 32 00 96 ff ff c8 00 00 00 ff"
    ),
    TimeAndDateWidget.DATE: bytes.fromhex(
        "02 01 16 1e 00 00 0d 01 ff ff ff 00 ff 96 00 00 00 00 00 ff"
    ),
    TimeAndDateWidget.WEEK_NUMBER: bytes.fromhex(
        "02 01 16 1e 00 01 0d 01 ff ff ff 00 ff 64 ff 00 00 ff ff ff"
    ),
}


def build_fullscreen_time_and_date_report(
    widget: TimeAndDateWidget | int,
) -> bytes:
    """Build a captured fullscreen Time and Date widget report."""
    if isinstance(widget, bool):
        raise TypeError("Time and Date widget must not be a boolean.")

    try:
        widget = TimeAndDateWidget(widget)
    except ValueError as error:
        raise ValueError(f"Unsupported Time and Date widget: {widget}.") from error

    return build_widget_report(
        layout_id=0x01,
        slot_index=0,
        category=WidgetCategory.TIME_AND_DATE,
        widget_id=widget,
        options=_FULLSCREEN_PROFILES[widget],
    )


def build_fullscreen_digital_clock_report() -> bytes:
    """Build the captured fullscreen digital-clock report."""
    return build_fullscreen_time_and_date_report(
        TimeAndDateWidget.DIGITAL_CLOCK
    )


def build_fullscreen_graphical_clock_report() -> bytes:
    """Build the captured fullscreen graphical-clock report."""
    return build_fullscreen_time_and_date_report(
        TimeAndDateWidget.GRAPHICAL_CLOCK
    )


def build_fullscreen_date_report() -> bytes:
    """Build the captured fullscreen date report."""
    return build_fullscreen_time_and_date_report(TimeAndDateWidget.DATE)


def build_fullscreen_week_number_report() -> bytes:
    """Build the captured fullscreen week-number report."""
    return build_fullscreen_time_and_date_report(
        TimeAndDateWidget.WEEK_NUMBER
    )
