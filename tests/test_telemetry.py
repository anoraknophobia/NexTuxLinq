"""Tests for Matrix900 telemetry support."""

import tempfile
import unittest
from pathlib import Path

from nextuxlinq.protocol import build_telemetry_report
from nextuxlinq.telemetry import (
    case_fan_rpm_from_openlinkhub_payload,
    collect_default_telemetry,
    extract_openlinkhub_case_fan_rpms,
)


class TelemetryReportTests(unittest.TestCase):
    """Verify the captured Matrix900 telemetry report format."""

    def test_builds_verified_report(self) -> None:
        report = build_telemetry_report(44, 52, 2459, 0x6A675524)

        self.assertEqual(len(report), 64)
        self.assertEqual(report[0:2], bytes.fromhex("01 01"))
        self.assertEqual(report[5], 0x14)
        self.assertEqual(report[6], 44)
        self.assertEqual(report[7], 52)
        self.assertEqual(report[9:11], bytes.fromhex("09 9b"))
        self.assertEqual(report[21:25], bytes.fromhex("6a 67 55 24"))
        self.assertEqual(report[25], 0x07)


class OpenLinkHubTelemetryTests(unittest.TestCase):
    """Verify extraction of physical case-fan RPM values."""

    def build_payload(self) -> dict[str, object]:
        return {
            "devices": {
                "controller": {
                    "GetDevice": {
                        "devices": {
                            "fan1": {
                                "HasSpeed": True,
                                "description": "Fan",
                                "rpm": 953,
                                "AIO": False,
                                "IsCpuBlock": False,
                            },
                            "fan2": {
                                "HasSpeed": True,
                                "description": "Fan",
                                "rpm": 958,
                                "AIO": False,
                                "IsCpuBlock": False,
                            },
                            "pump": {
                                "HasSpeed": True,
                                "description": "Pump",
                                "rpm": 2468,
                                "AIO": True,
                                "IsCpuBlock": True,
                            },
                        }
                    }
                }
            }
        }

    def test_extracts_only_case_fans(self) -> None:
        values = extract_openlinkhub_case_fan_rpms(self.build_payload())
        self.assertEqual(values, (953, 958))

    def test_returns_rounded_average(self) -> None:
        rpm = case_fan_rpm_from_openlinkhub_payload(self.build_payload())
        self.assertEqual(rpm, 956)

    def test_rejects_payload_without_case_fans(self) -> None:
        with self.assertRaises(RuntimeError):
            extract_openlinkhub_case_fan_rpms({"devices": {}})


class LinuxSensorTests(unittest.TestCase):
    """Verify Linux hwmon sensor discovery and conversion."""

    def test_collects_verified_pump_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            cpu = root / "hwmon0"
            cpu.mkdir()
            (cpu / "name").write_text("k10temp\n", encoding="utf-8")
            (cpu / "temp1_label").write_text("Tctl\n", encoding="utf-8")
            (cpu / "temp1_input").write_text("44123\n", encoding="utf-8")

            gpu = root / "hwmon1"
            gpu.mkdir()
            (gpu / "name").write_text("amdgpu\n", encoding="utf-8")
            (gpu / "temp1_label").write_text("edge\n", encoding="utf-8")
            (gpu / "temp1_input").write_text("52123\n", encoding="utf-8")

            pump = root / "hwmon2"
            pump.mkdir()
            (pump / "name").write_text("nct6798\n", encoding="utf-8")
            (pump / "fan1_input").write_text("0\n", encoding="utf-8")
            (pump / "fan6_input").write_text("2459\n", encoding="utf-8")

            sample = collect_default_telemetry(
                root,
                timestamp=1234567890,
                rpm_source="pump",
            )

            self.assertEqual(sample.cpu_temperature, 44)
            self.assertEqual(sample.gpu_temperature, 52)
            self.assertEqual(sample.fan_rpm, 2459)
            self.assertEqual(sample.timestamp, 1234567890)
            self.assertEqual(sample.rpm_source, "nct6798 highest RPM")


if __name__ == "__main__":
    unittest.main()
