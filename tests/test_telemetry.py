"""Tests for Matrix900 telemetry support."""

import tempfile
import unittest
from pathlib import Path

from nexlinq.protocol import build_telemetry_report
from nexlinq.telemetry import collect_default_telemetry


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


class LinuxSensorTests(unittest.TestCase):
    """Verify Linux hwmon sensor discovery and conversion."""

    def test_collects_verified_default_sources(self) -> None:
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

            fan = root / "hwmon2"
            fan.mkdir()
            (fan / "name").write_text("nct6798\n", encoding="utf-8")
            (fan / "fan1_input").write_text("0\n", encoding="utf-8")
            (fan / "fan6_input").write_text("2459\n", encoding="utf-8")

            sample = collect_default_telemetry(root, timestamp=1234567890)

            self.assertEqual(sample.cpu_temperature, 44)
            self.assertEqual(sample.gpu_temperature, 52)
            self.assertEqual(sample.fan_rpm, 2459)
            self.assertEqual(sample.timestamp, 1234567890)


if __name__ == "__main__":
    unittest.main()
