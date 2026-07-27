"""Verified and experimental Matrix900 layout presets."""

from __future__ import annotations

from nexlinq.clock import TimeAndDateWidget, build_time_and_date_report
from nexlinq.layout import (
    SystemDataSource,
    build_system_data_variant_one_report,
)

TWO_SLOT_LAYOUT_ID = 0x02


def build_two_slot_clock_system_reports(
    source: SystemDataSource | int,
) -> tuple[bytes, bytes]:
    """Build layout 0x02 with a digital clock and System Data widget."""
    return (
        build_time_and_date_report(
            TimeAndDateWidget.DIGITAL_CLOCK,
            layout_id=TWO_SLOT_LAYOUT_ID,
            slot_index=0,
        ),
        build_system_data_variant_one_report(
            source,
            layout_id=TWO_SLOT_LAYOUT_ID,
            slot_index=1,
        ),
    )
