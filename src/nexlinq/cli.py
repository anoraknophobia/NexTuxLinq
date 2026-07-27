"""Command-line interface for nexlinq-linux."""

from __future__ import annotations

import argparse
import sys

from nexlinq.controller import set_brightness
from nexlinq.device import find_matrix900


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

    if result.response is None:
        print("Response: none")
        return 0

    print("Response:", result.response.hex(" "))

    if result.response != result.report:
        print("Warning: the response differs from the transmitted report.")

    return 0


def main() -> int:
    """Run the nexlinqctl command-line interface."""
    arguments = build_parser().parse_args()

    if arguments.command in (None, "detect"):
        return detect_devices()

    if arguments.command == "brightness":
        return set_device_brightness(arguments.percentage)

    return 2


if __name__ == "__main__":
    sys.exit(main())
