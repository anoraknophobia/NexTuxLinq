"""Linux hidraw discovery for the Phanteks Matrix900."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MATRIX900_VENDOR_ID = 0x2E3C
MATRIX900_PRODUCT_ID = 0x6500
MATRIX900_HID_ID = "0003:00002E3C:00006500"
SYSFS_HIDRAW_PATH = Path("/sys/class/hidraw")


@dataclass(frozen=True)
class Matrix900Device:
    """A detected Matrix900 hidraw device."""

    device_path: Path
    sysfs_path: Path


def find_matrix900() -> list[Matrix900Device]:
    """Return all connected Matrix900 hidraw devices."""
    devices: list[Matrix900Device] = []

    for hidraw_path in sorted(SYSFS_HIDRAW_PATH.glob("hidraw*")):
        uevent_path = hidraw_path / "device" / "uevent"

        try:
            properties = dict(
                line.split("=", 1)
                for line in uevent_path.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
        except OSError:
            continue

        if properties.get("HID_ID", "").upper() != MATRIX900_HID_ID:
            continue

        devices.append(
            Matrix900Device(
                device_path=Path("/dev") / hidraw_path.name,
                sysfs_path=hidraw_path.resolve(),
            )
        )

    return devices
