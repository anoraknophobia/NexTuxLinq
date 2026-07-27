"""Tests for Matrix900 fullscreen Time and Date widgets."""

import unittest

from nexlinq.clock import (
    TimeAndDateWidget,
    build_fullscreen_date_report,
    build_fullscreen_digital_clock_report,
    build_fullscreen_graphical_clock_report,
    build_fullscreen_time_and_date_report,
    build_fullscreen_week_number_report,
)


class TimeAndDateReportTests(unittest.TestCase):
    """Verify Time and Date profiles captured under Windows."""

    def assert_report_matches(
        self,
        report: bytes,
        widget_id: int,
        options: str,
    ) -> None:
        expected = bytes.fromhex(
            f"01 03 01 00 00 22 01 01 {widget_id:02x} {options}"
        ) + bytes(35)

        self.assertEqual(len(expected), 64)
        self.assertEqual(report, expected)

    def test_digital_clock_matches_capture(self) -> None:
        self.assert_report_matches(
            build_fullscreen_digital_clock_report(),
            0x01,
            "02 01 16 1e 00 00 0d 01 ff ff ff 00 00 00 ff "
            "00 00 00 00 ff",
        )

    def test_graphical_clock_matches_capture(self) -> None:
        self.assert_report_matches(
            build_fullscreen_graphical_clock_report(),
            0x02,
            "02 01 16 1e 00 00 0d 01 ff 00 32 00 96 ff ff c8 "
            "00 00 00 ff",
        )

    def test_date_matches_capture(self) -> None:
        self.assert_report_matches(
            build_fullscreen_date_report(),
            0x03,
            "02 01 16 1e 00 00 0d 01 ff ff ff 00 ff 96 00 00 "
            "00 00 00 ff",
        )

    def test_week_number_matches_capture(self) -> None:
        self.assert_report_matches(
            build_fullscreen_week_number_report(),
            0x04,
            "02 01 16 1e 00 01 0d 01 ff ff ff 00 ff 64 ff 00 "
            "00 ff ff ff",
        )

    def test_generic_builder_accepts_verified_enum(self) -> None:
        report = build_fullscreen_time_and_date_report(
            TimeAndDateWidget.DATE
        )

        self.assertEqual(report[7], 0x01)
        self.assertEqual(report[8], 0x03)

    def test_rejects_unknown_widget(self) -> None:
        with self.assertRaises(ValueError):
            build_fullscreen_time_and_date_report(0x05)


if __name__ == "__main__":
    unittest.main()
