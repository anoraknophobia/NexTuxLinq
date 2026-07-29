"""Tests for the Linux hidraw transport layer."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from nextuxlinq.transport import HidrawConnection, write_report


class WriteReportTests(unittest.TestCase):
    """Verify HID report validation and complete writes."""

    def test_writes_exactly_64_bytes(self) -> None:
        read_fd, write_fd = os.pipe()
        report = bytes(range(64))

        try:
            write_report(write_fd, report)
            self.assertEqual(os.read(read_fd, 64), report)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_rejects_invalid_report_length(self) -> None:
        read_fd, write_fd = os.pipe()

        try:
            with self.assertRaises(ValueError):
                write_report(write_fd, bytes(63))
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_rejects_non_bytes_reports(self) -> None:
        read_fd, write_fd = os.pipe()

        try:
            with self.assertRaises(TypeError):
                write_report(write_fd, bytearray(64))
        finally:
            os.close(read_fd)
            os.close(write_fd)


class HidrawConnectionTests(unittest.TestCase):
    """Verify persistent hidraw connection ownership and transactions."""

    def test_reuses_one_descriptor_until_closed(self) -> None:
        report = bytes(range(64))
        with (
            patch("nextuxlinq.transport.os.open", return_value=42) as open_mock,
            patch("nextuxlinq.transport.os.close") as close_mock,
            patch("nextuxlinq.transport.write_report") as write_mock,
            patch(
                "nextuxlinq.transport.select.select",
                return_value=([42], [], []),
            ),
            patch(
                "nextuxlinq.transport.read_report",
                return_value=report,
            ),
        ):
            connection = HidrawConnection(Path("/dev/fake"))
            first = connection.transact(report)
            second = connection.transact(report)
            connection.close()

        open_mock.assert_called_once()
        self.assertEqual(write_mock.call_count, 2)
        self.assertEqual(first, report)
        self.assertEqual(second, report)
        close_mock.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
