"""Tests for the Linux hidraw transport layer."""

import os
import unittest

from nexlinq.transport import write_report


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


if __name__ == "__main__":
    unittest.main()
