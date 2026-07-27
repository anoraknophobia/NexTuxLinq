"""Tests for Matrix900 multi-slot presets."""

import unittest

from nexlinq.layout import SystemDataSource
from nexlinq.presets import build_two_slot_clock_system_reports


class TwoSlotPresetTests(unittest.TestCase):
    """Verify the first two-slot Matrix900 preset."""

    def test_builds_two_complete_reports(self) -> None:
        reports = build_two_slot_clock_system_reports(SystemDataSource.GPU)

        self.assertEqual(len(reports), 2)
        self.assertTrue(all(len(report) == 64 for report in reports))

    def test_clock_slot_matches_captured_layout_report(self) -> None:
        clock_report, _ = build_two_slot_clock_system_reports(
            SystemDataSource.GPU
        )
        expected = bytes.fromhex(
            "01 03 02 00 00 22 02 01 01 "
            "02 01 16 1e 00 00 0d 01 ff ff ff 00 00 00 ff "
            "00 00 00 00 ff"
        ) + bytes(35)

        self.assertEqual(clock_report, expected)

    def test_system_widget_uses_second_slot(self) -> None:
        _, system_report = build_two_slot_clock_system_reports(
            SystemDataSource.GPU
        )

        self.assertEqual(system_report[2], 2)
        self.assertEqual(system_report[4], 1)
        self.assertEqual(system_report[6], 0x02)
        self.assertEqual(system_report[7], 0x02)
        self.assertEqual(system_report[8], 0x00)
        self.assertEqual(system_report[9], 0x02)

    def test_fan_source_uses_rpm_scale(self) -> None:
        _, system_report = build_two_slot_clock_system_reports(
            SystemDataSource.FAN
        )

        self.assertEqual(system_report[9], 0x04)
        self.assertEqual(system_report[18:20], bytes.fromhex("07 d0"))


if __name__ == "__main__":
    unittest.main()
