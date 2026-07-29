"""Tests for Matrix900 generic layout presets."""

import unittest

from nextuxlinq.layout import SystemDataSource
from nextuxlinq.presets import (
    USER_LAYOUT_TO_INTERNAL_ID,
    build_layout_reports,
    build_two_slot_clock_system_reports,
)


class GenericLayoutPresetTests(unittest.TestCase):
    """Verify user-facing layout mapping and report generation."""

    def test_layout_mapping_matches_capture(self) -> None:
        self.assertEqual(
            USER_LAYOUT_TO_INTERNAL_ID,
            {
                1: 0x02,
                2: 0x03,
                3: 0x04,
                4: 0x05,
                5: 0x06,
                6: 0x07,
                7: 0x08,
                8: 0x09,
                9: 0x0A,
                10: 0x0B,
                11: 0x0C,
                12: 0x01,
            },
        )

    def test_builds_three_slot_layout(self) -> None:
        reports = build_layout_reports(
            2,
            ("digital", "system-cpu", "system-gpu"),
        )

        self.assertEqual(len(reports), 3)

        for slot_index, report in enumerate(reports):
            self.assertEqual(len(report), 64)
            self.assertEqual(report[0:2], bytes.fromhex("01 03"))
            self.assertEqual(report[2], 3)
            self.assertEqual(report[4], slot_index)
            self.assertEqual(report[6], 0x03)

        self.assertEqual(reports[0][7:9], bytes.fromhex("01 01"))
        self.assertEqual(reports[1][7:10], bytes.fromhex("02 00 01"))
        self.assertEqual(reports[2][7:10], bytes.fromhex("02 00 02"))

    def test_rejects_wrong_widget_count(self) -> None:
        with self.assertRaises(ValueError):
            build_layout_reports(2, ("digital", "system-cpu"))

    def test_rejects_unknown_widget(self) -> None:
        with self.assertRaises(ValueError):
            build_layout_reports(1, ("digital", "unknown"))

    def test_preserves_two_slot_preset(self) -> None:
        reports = build_two_slot_clock_system_reports(SystemDataSource.GPU)

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0][6], 0x02)
        self.assertEqual(reports[1][9], 0x02)

    def test_fan_uses_rpm_scale(self) -> None:
        reports = build_layout_reports(1, ("digital", "system-fan"))

        self.assertEqual(reports[1][9], 0x04)
        self.assertEqual(reports[1][18:20], bytes.fromhex("07 d0"))


if __name__ == "__main__":
    unittest.main()
