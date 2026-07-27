"""Command-line interface for nexlinq-linux."""

from __future__ import annotations

import sys

from nexlinq.device import find_matrix900


def main() -> int:
    """Detect connected Phanteks Matrix900 devices."""
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


if __name__ == "__main__":
    sys.exit(main())
