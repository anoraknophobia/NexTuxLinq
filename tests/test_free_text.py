"""Tests for Matrix900 fullscreen free text."""

import unittest

from nextuxlinq.free_text import (
    build_fullscreen_free_text_reports,
    build_fullscreen_free_text_widget_report,
    build_text_data_report,
)


class FreeTextReportTests(unittest.TestCase):
    """Verify reports observed in the Windows capture."""

    def test_official_text_report_matches_capture(self) -> None:
        expected = bytes.fromhex(
            "01 02 00 00 00 08 00 4e 45 58 4c 49 4e 51 ff ff "
            "ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff "
            "ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff "
            "ff ff ff ff ff ff ff ff ff 00 00 00 00 00 00 00"
        )

        self.assertEqual(build_text_data_report("NEXLINQ"), expected)

    def test_fullscreen_widget_report_matches_capture(self) -> None:
        expected = bytes.fromhex(
            "01 03 01 00 00 22 01 01 00 01 01 16 1e 00 00 0d "
            "01 ff ff ff ff 64 00 ff c8 00 00 00 ff"
        ) + bytes(35)
        self.assertEqual(len(expected), 64)

        self.assertEqual(build_fullscreen_free_text_widget_report(), expected)

    def test_builds_two_report_sequence(self) -> None:
        reports = build_fullscreen_free_text_reports("NEXLINQ")

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0][1], 0x02)
        self.assertEqual(reports[1][1], 0x03)

    def test_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            build_text_data_report("")

    def test_rejects_non_ascii_text(self) -> None:
        with self.assertRaises(ValueError):
            build_text_data_report("Grüße")

    def test_rejects_text_larger_than_current_field(self) -> None:
        with self.assertRaises(ValueError):
            build_text_data_report("A" * 50)


if __name__ == "__main__":
    unittest.main()
