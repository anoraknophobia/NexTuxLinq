"""Verified and experimental Matrix900 layout presets."""

from __future__ import annotations

from collections.abc import Sequence

from nexlinq.clock import TimeAndDateWidget, build_time_and_date_report
from nexlinq.layout import (
    LAYOUT_SLOT_COUNTS,
    SystemDataSource,
    SystemDataWidget,
    build_system_data_report,
)

USER_LAYOUT_TO_INTERNAL_ID: dict[int, int] = {
    1: 0x02,
    2: 0x03,
    3: 0x04,
    4: 0x05,
    5: 0x06,
    6: 0x07,
    7: 0x08,
    8: 0x09,
    9: 0x0A,
    10: 0x0B,
    11: 0x0C,
    12: 0x01,
}

_TIME_WIDGETS: dict[str, TimeAndDateWidget] = {
    "digital": TimeAndDateWidget.DIGITAL_CLOCK,
    "graphical": TimeAndDateWidget.GRAPHICAL_CLOCK,
    "date": TimeAndDateWidget.DATE,
    "week": TimeAndDateWidget.WEEK_NUMBER,
}

_SYSTEM_WIDGETS: dict[
    str,
    tuple[SystemDataWidget, SystemDataSource | None],
] = {
    "system-cpu": (SystemDataWidget.VARIANT_ONE, SystemDataSource.CPU),
    "system-gpu": (SystemDataWidget.VARIANT_ONE, SystemDataSource.GPU),
    "system-fan": (SystemDataWidget.VARIANT_ONE, SystemDataSource.FAN),
    "system-bar-cpu": (
        SystemDataWidget.VALUE_BAR,
        SystemDataSource.CPU,
    ),
    "system-bar-gpu": (
        SystemDataWidget.VALUE_BAR,
        SystemDataSource.GPU,
    ),
    "system-background-bar-cpu": (
        SystemDataWidget.VALUE_BACKGROUND_BAR,
        SystemDataSource.CPU,
    ),
    "system-background-bar-gpu": (
        SystemDataWidget.VALUE_BACKGROUND_BAR,
        SystemDataSource.GPU,
    ),
    "system-variant2": (
        SystemDataWidget.VALUE_BAR,
        SystemDataSource.CPU,
    ),
    "system-variant3": (
        SystemDataWidget.VALUE_BACKGROUND_BAR,
        SystemDataSource.CPU,
    ),
    "system-overview": (SystemDataWidget.OVERVIEW, None),
    "system-aio-pump": (SystemDataWidget.AIO_PUMP_SPEED, None),
    "system-graph-cpu": (
        SystemDataWidget.SINGLE_SOURCE_GRAPH,
        SystemDataSource.CPU,
    ),
    "system-graph-gpu": (
        SystemDataWidget.SINGLE_SOURCE_GRAPH,
        SystemDataSource.GPU,
    ),
}


def build_layout_reports(
    user_layout_number: int,
    widget_names: Sequence[str],
) -> tuple[bytes, ...]:
    """Build one command 0x03 report for every slot in a known layout."""
    if isinstance(user_layout_number, bool) or not isinstance(
        user_layout_number, int
    ):
        raise TypeError("layout number must be an integer.")

    try:
        internal_layout_id = USER_LAYOUT_TO_INTERNAL_ID[user_layout_number]
    except KeyError as error:
        raise ValueError(
            f"layout number must be between 1 and 12: {user_layout_number}."
        ) from error

    if isinstance(widget_names, (str, bytes)):
        raise TypeError("widget names must be a sequence of strings.")

    widgets = tuple(widget_names)
    expected_slots = LAYOUT_SLOT_COUNTS[internal_layout_id]

    if len(widgets) != expected_slots:
        raise ValueError(
            f"layout {user_layout_number} requires {expected_slots} widget(s), "
            f"but {len(widgets)} were provided."
        )

    reports: list[bytes] = []

    for slot_index, widget_name in enumerate(widgets):
        if widget_name in _TIME_WIDGETS:
            reports.append(
                build_time_and_date_report(
                    _TIME_WIDGETS[widget_name],
                    layout_id=internal_layout_id,
                    slot_index=slot_index,
                )
            )
            continue

        if widget_name in _SYSTEM_WIDGETS:
            system_widget, source = _SYSTEM_WIDGETS[widget_name]
            reports.append(
                build_system_data_report(
                    system_widget,
                    source=source,
                    layout_id=internal_layout_id,
                    slot_index=slot_index,
                )
            )
            continue

        raise ValueError(f"unsupported widget name: {widget_name}.")

    return tuple(reports)


def build_two_slot_clock_system_reports(
    source: SystemDataSource | int,
) -> tuple[bytes, bytes]:
    """Preserve the original two-slot clock and System Data preset."""
    if isinstance(source, bool):
        raise TypeError("System Data source must not be a boolean.")

    try:
        source = SystemDataSource(source)
    except ValueError as error:
        raise ValueError(f"Unsupported System Data source: {source}.") from error

    source_name = {
        SystemDataSource.CPU: "system-cpu",
        SystemDataSource.GPU: "system-gpu",
        SystemDataSource.FAN: "system-fan",
    }[source]

    reports = build_layout_reports(1, ("digital", source_name))
    return reports[0], reports[1]
