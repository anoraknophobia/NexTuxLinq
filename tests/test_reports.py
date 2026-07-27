"""Tests for verified Matrix900 HID reports."""

import unittest

from nexlinq.protocol import build_brightness_report


class BrightnessReportTests(unittest.TestCase):
    """Verify reports captured from the official NexLinq application."""

    def test_builds_complete_64_byte_report(self) -> None:
        report = build_brightness_report(10)

        self.assertEqual(len(report), 64)
        self.assertEqual(report[:7], bytes.fromhex("01 04 00 00 00 01 47"))
        self.assertEqual(report[7:], bytes(57))

    def test_encodes_confirmed_brightness_values(self) -> None:
        expected_values = {
            10: 0x47,
            30: 0x5F,
            50: 0x77,
            60: 0x84,
            80: 0x9C,
            100: 0xB4,
        }

        for percentage, expected in expected_values.items():
            with self.subTest(percentage=percentage):
                report = build_brightness_report(percentage)
                self.assertEqual(report[6], expected)


if __name__ == "__main__":
    unittest.main()
