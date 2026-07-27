"""Tests for the Matrix900 fullscreen digital clock."""

import unittest

from nexlinq.clock import build_fullscreen_digital_clock_report


class DigitalClockReportTests(unittest.TestCase):
    """Verify the digital-clock report captured under Windows."""

    def test_fullscreen_clock_report_matches_capture(self) -> None:
        expected = bytes.fromhex(
            "01 03 01 00 00 22 01 01 01 "
            "02 01 16 1e 00 00 0d 01 ff ff ff 00 00 00 ff "
            "00 00 00 00 ff"
        ) + bytes(35)

        self.assertEqual(len(expected), 64)
        self.assertEqual(build_fullscreen_digital_clock_report(), expected)

    def test_uses_time_and_date_category(self) -> None:
        report = build_fullscreen_digital_clock_report()

        self.assertEqual(report[6], 0x01)
        self.assertEqual(report[7], 0x01)
        self.assertEqual(report[8], 0x01)


if __name__ == "__main__":
    unittest.main()
