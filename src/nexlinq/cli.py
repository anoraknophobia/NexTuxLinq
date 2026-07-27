"""Command-line interface for nexlinq-linux."""

from __future__ import annotations

import argparse
import sys
import time

from nexlinq.clock import (
    TimeAndDateWidget,
    build_fullscreen_time_and_date_report,
)
from nexlinq.configuration import (
    send_configuration_report,
    send_report_sequence,
)
from nexlinq.controller import set_brightness
from nexlinq.device import find_matrix900
from nexlinq.free_text import build_fullscreen_free_text_reports
from nexlinq.layout import (
    SystemDataSource,
    build_system_data_variant_one_report,
)
from nexlinq.presets import (
    build_layout_reports,
    build_two_slot_clock_system_reports,
)
from nexlinq.telemetry import collect_default_telemetry, send_telemetry


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="nexlinqctl",
        description="Control a Phanteks Matrix900 display on Linux.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("detect", help="Detect connected Matrix900 devices.")

    brightness_parser = subparsers.add_parser(
        "brightness",
        help="Set display brightness.",
    )
    brightness_parser.add_argument(
        "percentage",
        type=int,
        help="Brightness from 10 to 100 in ten-percent steps.",
    )

    telemetry_parser = subparsers.add_parser(
        "telemetry",
        help="Send Linux hardware telemetry to the display.",
    )
    telemetry_parser.add_argument(
        "--watch",
        action="store_true",
        help="Continue sending telemetry until interrupted.",
    )
    telemetry_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Update interval in seconds when using --watch.",
    )

    system_data_parser = subparsers.add_parser(
        "system-data",
        help="Configure the fullscreen System Data widget.",
    )
    system_data_parser.add_argument(
        "source",
        choices=("cpu", "gpu", "fan"),
        help="Select the value displayed by System Data variant one.",
    )

    text_parser = subparsers.add_parser(
        "text",
        help="Display fullscreen ASCII text.",
    )
    text_parser.add_argument(
        "value",
        help="ASCII text to show on the Matrix900.",
    )

    subparsers.add_parser(
        "clock",
        help="Display the captured fullscreen digital clock.",
    )

    time_widget_parser = subparsers.add_parser(
        "time-widget",
        help="Display a captured fullscreen Time and Date widget.",
    )
    time_widget_parser.add_argument(
        "widget",
        choices=("digital", "graphical", "date", "week"),
        help="Select the fullscreen Time and Date widget.",
    )

    two_slot_parser = subparsers.add_parser(
        "layout-clock-system",
        help="Display a two-slot clock and System Data layout.",
    )
    two_slot_parser.add_argument(
        "source",
        choices=("cpu", "gpu", "fan"),
        help="Select the System Data source for the second slot.",
    )

    layout_parser = subparsers.add_parser(
        "layout",
        help="Configure a known Matrix900 layout and all of its slots.",
    )
    layout_parser.add_argument(
        "number",
        type=int,
        choices=range(1, 13),
        help="User-facing NexLinq layout number from 1 to 12.",
    )
    layout_parser.add_argument(
        "widgets",
        nargs="+",
        choices=(
            "digital",
            "graphical",
            "date",
            "week",
            "system-cpu",
            "system-gpu",
            "system-fan",
        ),
        help="One widget name for every slot, in slot order.",
    )

    return parser


def detect_devices() -> int:
    """Print connected Matrix900 devices."""
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.")
        return 1

    print(f"Found {len(devices)} Phanteks Matrix900 device(s):")

    for index, device in enumerate(devices, start=1):
        print(f"  Device {index}:")
        print(f"    Device path: {device.device_path}")
        print(f"    Sysfs path: {device.sysfs_path}")
        print("    Vendor ID: 0x2e3c")
        print("    Product ID: 0x6500")

    return 0


def set_device_brightness(percentage: int) -> int:
    """Set brightness on the first connected Matrix900."""
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        result = set_brightness(devices[0].device_path, percentage)
    except (OSError, TypeError, ValueError) as error:
        print(f"Failed to set brightness: {error}", file=sys.stderr)
        return 1

    print(f"Brightness set to {percentage}%.")
    print(f"Device: {devices[0].device_path}")
    print("Sent:", result.report.hex(" "))
    print("Response:", result.response.hex(" ") if result.response else "none")
    return 0


def update_telemetry(watch: bool, interval: float) -> int:
    """Send one or more Linux telemetry updates."""
    if interval <= 0:
        print("Telemetry interval must be greater than zero.", file=sys.stderr)
        return 1

    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        while True:
            sample = collect_default_telemetry()
            result = send_telemetry(devices[0].device_path, sample)
            response = (
                "echo"
                if result.response == result.report
                else "none" if result.response is None else "different"
            )
            print(
                f"CPU {sample.cpu_temperature} C | "
                f"GPU {sample.gpu_temperature} C | "
                f"Fan/Pump {sample.fan_rpm} RPM | "
                f"Response {response}",
                flush=True,
            )

            if not watch:
                return 0

            time.sleep(interval)
    except KeyboardInterrupt:
        print("Telemetry stopped.")
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Failed to update telemetry: {error}", file=sys.stderr)
        return 1



def configure_system_data(source_name: str) -> int:
    """Configure the verified fullscreen System Data widget."""
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        source = SystemDataSource[source_name.upper()]
        report = build_system_data_variant_one_report(source)
        result = send_configuration_report(devices[0].device_path, report)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"Failed to configure System Data: {error}", file=sys.stderr)
        return 1

    response = (
        "echo"
        if result.response == result.report
        else "none" if result.response is None else "different"
    )

    print(f"System Data source set to {source_name.upper()}.")
    print(f"Device: {devices[0].device_path}")
    print(f"Response: {response}")
    print("Sent:", result.report.hex(" "))
    return 0


def configure_free_text(value: str) -> int:
    """Configure captured fullscreen free text."""
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        reports = build_fullscreen_free_text_reports(value)
        results = send_report_sequence(devices[0].device_path, reports)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Failed to configure free text: {error}", file=sys.stderr)
        return 1

    print(f"Fullscreen text set to: {value}")
    print(f"Device: {devices[0].device_path}")

    for index, result in enumerate(results, start=1):
        response = (
            "echo"
            if result.response == result.report
            else "none" if result.response is None else "different"
        )
        print(f"Report {index} response: {response}")
        print(f"Report {index} sent:", result.report.hex(" "))

    return 0


def configure_time_and_date_widget(widget_name: str) -> int:
    """Configure a captured fullscreen Time and Date widget."""
    widget_map = {
        "digital": TimeAndDateWidget.DIGITAL_CLOCK,
        "graphical": TimeAndDateWidget.GRAPHICAL_CLOCK,
        "date": TimeAndDateWidget.DATE,
        "week": TimeAndDateWidget.WEEK_NUMBER,
    }
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        widget = widget_map[widget_name]
        report = build_fullscreen_time_and_date_report(widget)
        result = send_configuration_report(devices[0].device_path, report)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Failed to configure Time and Date widget: {error}", file=sys.stderr)
        return 1

    response = (
        "echo"
        if result.response == result.report
        else "none" if result.response is None else "different"
    )

    print(f"Fullscreen Time and Date widget enabled: {widget_name}.")
    print(f"Device: {devices[0].device_path}")
    print(f"Response: {response}")
    print("Sent:", result.report.hex(" "))
    return 0


def configure_digital_clock() -> int:
    """Preserve the original digital-clock CLI command."""
    return configure_time_and_date_widget("digital")


def configure_clock_system_layout(source_name: str) -> int:
    """Configure a two-slot clock and System Data layout."""
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        source = SystemDataSource[source_name.upper()]
        reports = build_two_slot_clock_system_reports(source)
        results = send_report_sequence(devices[0].device_path, reports)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Failed to configure two-slot layout: {error}", file=sys.stderr)
        return 1

    print(f"Two-slot clock and {source_name.upper()} layout enabled.")
    print(f"Device: {devices[0].device_path}")

    for index, result in enumerate(results, start=1):
        response = (
            "echo"
            if result.response == result.report
            else "none" if result.response is None else "different"
        )
        print(f"Slot {index - 1} response: {response}")
        print(f"Slot {index - 1} sent:", result.report.hex(" "))

    return 0


def configure_generic_layout(
    layout_number: int,
    widget_names: list[str],
) -> int:
    """Configure all slots of a known Matrix900 layout."""
    devices = find_matrix900()

    if not devices:
        print("No Phanteks Matrix900 device found.", file=sys.stderr)
        return 1

    try:
        reports = build_layout_reports(layout_number, widget_names)
        results = send_report_sequence(devices[0].device_path, reports)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Failed to configure layout: {error}", file=sys.stderr)
        return 1

    print(f"User layout {layout_number} enabled.")
    print(f"Device: {devices[0].device_path}")

    for slot_index, result in enumerate(results):
        response = (
            "echo"
            if result.response == result.report
            else "none" if result.response is None else "different"
        )
        print(f"Slot {slot_index} response: {response}")
        print(f"Slot {slot_index} sent:", result.report.hex(" "))

    return 0

def main() -> int:
    """Run the nexlinqctl command-line interface."""
    arguments = build_parser().parse_args()

    if arguments.command in (None, "detect"):
        return detect_devices()
    if arguments.command == "brightness":
        return set_device_brightness(arguments.percentage)
    if arguments.command == "telemetry":
        return update_telemetry(arguments.watch, arguments.interval)
    if arguments.command == "system-data":
        return configure_system_data(arguments.source)
    if arguments.command == "text":
        return configure_free_text(arguments.value)
    if arguments.command == "clock":
        return configure_digital_clock()
    if arguments.command == "time-widget":
        return configure_time_and_date_widget(arguments.widget)
    if arguments.command == "layout-clock-system":
        return configure_clock_system_layout(arguments.source)
    if arguments.command == "layout":
        return configure_generic_layout(arguments.number, arguments.widgets)
    return 2


if __name__ == "__main__":
    sys.exit(main())
