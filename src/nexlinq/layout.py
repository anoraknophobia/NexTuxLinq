"""Matrix900 layout and widget report encoding."""

from __future__ import annotations

from enum import IntEnum

from nexlinq.protocol import HID_REPORT_SIZE, REPORT_ID

COMMAND_WIDGET_CONFIGURATION = 0x03
WIDGET_CONFIGURATION_MARKER = 0x22


class WidgetCategory(IntEnum):
    """Verified Matrix900 widget categories."""

    TIME_AND_DATE = 0x01
    SYSTEM_DATA = 0x02


class SystemDataWidget(IntEnum):
    """Verified System Data widget identifiers."""

    VARIANT_ONE = 0x00
    VARIANT_TWO = 0x01
    VARIANT_THREE = 0x03
    OVERVIEW = 0x04
    AIO_PUMP_SPEED = 0x05
    CPU_GPU_TEMPERATURES = 0x06


class SystemDataSource(IntEnum):
    """Verified System Data source identifiers."""

    CPU = 0x01
    GPU = 0x02
    FAN = 0x04


LAYOUT_SLOT_COUNTS: dict[int, int] = {
    0x01: 1,
    0x02: 2,
    0x03: 3,
    0x04: 4,
    0x05: 4,
    0x06: 5,
    0x07: 5,
    0x08: 5,
    0x09: 6,
    0x0A: 3,
    0x0B: 4,
    0x0C: 3,
}

_SYSTEM_DATA_PROFILES: dict[SystemDataWidget, bytes] = {
    SystemDataWidget.VARIANT_ONE: bytes.fromhex(
        "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
        "ff ff ff 32 00 ff 00 00 00 ff ff ff ff"
    ),
    SystemDataWidget.VARIANT_TWO: bytes.fromhex(
        "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
        "ff ff ff ff 00 00 00 00 00 ff ff ff ff"
    ),
    SystemDataWidget.VARIANT_THREE: bytes.fromhex(
        "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
        "ff ff ff 00 96 ff 00 00 00 ff ff ff ff"
    ),
    SystemDataWidget.OVERVIEW: bytes.fromhex(
        "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
        "32 00 ff 64 00 ff c8 00 ff ff ff ff ff"
    ),
    SystemDataWidget.AIO_PUMP_SPEED: bytes.fromhex(
        "04 02 04 07 d0 07 d0 07 d0 07 d0 00 01 "
        "00 00 00 00 ff c8 c8 00 ff ff ff ff ff"
    ),
    SystemDataWidget.CPU_GPU_TEMPERATURES: bytes.fromhex(
        "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
        "ff 00 00 ff ff ff c8 00 ff ff ff ff ff"
    ),
}


def _validate_byte(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be between 0 and 255.")
    return value


def build_widget_report(
    *,
    layout_id: int,
    slot_index: int,
    category: int,
    widget_id: int,
    options: bytes = b"",
) -> bytes:
    """Build a generic verified Matrix900 command 0x03 report."""
    layout_id = _validate_byte("layout ID", layout_id)
    slot_index = _validate_byte("slot index", slot_index)
    category = _validate_byte("widget category", category)
    widget_id = _validate_byte("widget ID", widget_id)

    if layout_id not in LAYOUT_SLOT_COUNTS:
        raise ValueError(f"Unsupported layout ID: 0x{layout_id:02x}.")

    slot_count = LAYOUT_SLOT_COUNTS[layout_id]
    if slot_index >= slot_count:
        raise ValueError(
            f"Slot {slot_index} is outside layout 0x{layout_id:02x} "
            f"with {slot_count} slot(s)."
        )

    if not isinstance(options, bytes):
        raise TypeError("widget options must be bytes.")
    if len(options) > HID_REPORT_SIZE - 9:
        raise ValueError("widget options do not fit in one HID report.")

    report = bytearray(HID_REPORT_SIZE)
    report[0] = REPORT_ID
    report[1] = COMMAND_WIDGET_CONFIGURATION
    report[2] = slot_count
    report[4] = slot_index
    report[5] = WIDGET_CONFIGURATION_MARKER
    report[6] = layout_id
    report[7] = category
    report[8] = widget_id
    report[9 : 9 + len(options)] = options
    return bytes(report)


def build_system_data_report(
    widget: SystemDataWidget | int,
    *,
    source: SystemDataSource | int | None = None,
    layout_id: int = 0x01,
    slot_index: int = 0,
) -> bytes:
    """Build a captured System Data widget report."""
    if isinstance(widget, bool):
        raise TypeError("System Data widget must not be a boolean.")

    try:
        widget = SystemDataWidget(widget)
    except ValueError as error:
        raise ValueError(f"Unsupported System Data widget: {widget}.") from error

    options = bytearray(_SYSTEM_DATA_PROFILES[widget])

    if widget is SystemDataWidget.VARIANT_ONE:
        if source is None:
            source = SystemDataSource.CPU
        if isinstance(source, bool):
            raise TypeError("System Data source must not be a boolean.")

        try:
            source = SystemDataSource(source)
        except ValueError as error:
            raise ValueError(f"Unsupported System Data source: {source}.") from error

        options[0] = source
        scale = 2000 if source is SystemDataSource.FAN else 80
        options[9:11] = scale.to_bytes(2, "big")
    elif source is not None:
        raise ValueError(
            "source selection is currently verified only for variant one."
        )

    return build_widget_report(
        layout_id=layout_id,
        slot_index=slot_index,
        category=WidgetCategory.SYSTEM_DATA,
        widget_id=widget,
        options=bytes(options),
    )


def build_system_data_variant_one_report(
    source: SystemDataSource | int,
    *,
    layout_id: int = 0x01,
    slot_index: int = 0,
) -> bytes:
    """Build the verified System Data variant-one report."""
    return build_system_data_report(
        SystemDataWidget.VARIANT_ONE,
        source=source,
        layout_id=layout_id,
        slot_index=slot_index,
    )
