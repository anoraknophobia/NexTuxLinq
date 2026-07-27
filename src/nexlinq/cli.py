"""Command-line interface for nexlinq-linux."""

from __future__ import annotations

import argparse
import sys
import time

from nexlinq.configuration import send_configuration_report
from nexlinq.controller import set_brightness
from nexlinq.device import find_matrix900
from nexlinq.layout import (
    SystemDataSource,
    build_system_data_variant_one_report,
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
    return 2


if __name__ == "__main__":
    sys.exit(main())
