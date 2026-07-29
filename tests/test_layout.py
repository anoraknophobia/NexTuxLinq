"""Tests for Matrix900 layout and widget encoding."""

import unittest

from nextuxlinq.layout import (
    SystemDataSource,
    build_system_data_variant_one_report,
    build_widget_report,
)


class WidgetReportTests(unittest.TestCase):
    """Verify known Matrix900 widget configuration reports."""

    def test_cpu_report_matches_capture(self) -> None:
        expected = bytes.fromhex(
            "01 03 01 00 00 22 01 02 00 01 02 04 07 d0 07 d0 "
            "07 d0 00 50 00 01 ff ff ff 32 00 ff 00 00 00 ff "
            "ff ff ff 00 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
        )

        report = build_system_data_variant_one_report(SystemDataSource.CPU)
        self.assertEqual(report, expected)

    def test_gpu_source_field(self) -> None:
        report = build_system_data_variant_one_report(SystemDataSource.GPU)

        self.assertEqual(report[9], 0x02)
        self.assertEqual(report[18:20], bytes.fromhex("00 50"))

    def test_fan_source_and_scale(self) -> None:
        report = build_system_data_variant_one_report(SystemDataSource.FAN)

        self.assertEqual(report[9], 0x04)
        self.assertEqual(report[18:20], bytes.fromhex("07 d0"))

    def test_rejects_slot_outside_layout(self) -> None:
        with self.assertRaises(ValueError):
            build_widget_report(
                layout_id=0x01,
                slot_index=1,
                category=0x02,
                widget_id=0x00,
            )


if __name__ == "__main__":
    unittest.main()
