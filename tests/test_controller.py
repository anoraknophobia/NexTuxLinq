"""Tests for high-level Matrix900 control operations."""

import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from nextuxlinq.controller import set_brightness
from nextuxlinq.protocol import build_brightness_report


class BrightnessControlTests(unittest.TestCase):
    """Verify high-level brightness control behavior."""

    def test_writes_report_and_returns_response(self) -> None:
        file_descriptor = 42
        expected_report = build_brightness_report(50)

        @contextmanager
        def fake_open_hidraw(device_path: Path):
            self.assertEqual(device_path, Path("/dev/hidraw-test"))
            yield file_descriptor

        write_mock = Mock()

        with (
            patch("nextuxlinq.controller.open_hidraw", fake_open_hidraw),
            patch("nextuxlinq.controller.write_report", write_mock),
            patch(
                "nextuxlinq.controller.select.select",
                return_value=([file_descriptor], [], []),
            ),
            patch(
                "nextuxlinq.controller.read_report",
                return_value=expected_report,
            ),
        ):
            result = set_brightness(Path("/dev/hidraw-test"), 50)

        write_mock.assert_called_once_with(file_descriptor, expected_report)
        self.assertEqual(result.report, expected_report)
        self.assertEqual(result.response, expected_report)

    def test_handles_missing_response(self) -> None:
        @contextmanager
        def fake_open_hidraw(device_path: Path):
            yield 42

        with (
            patch("nextuxlinq.controller.open_hidraw", fake_open_hidraw),
            patch("nextuxlinq.controller.write_report"),
            patch("nextuxlinq.controller.select.select", return_value=([], [], [])),
        ):
            result = set_brightness(Path("/dev/hidraw-test"), 50)

        self.assertIsNone(result.response)


if __name__ == "__main__":
    unittest.main()
