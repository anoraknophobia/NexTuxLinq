"""Tests for verified Matrix900 protocol primitives."""

import unittest

from nextuxlinq.protocol import encode_brightness


class BrightnessEncodingTests(unittest.TestCase):
    """Verify brightness values captured from the official NexLinq app."""

    def test_confirmed_capture_values(self) -> None:
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
                self.assertEqual(encode_brightness(percentage), expected)

    def test_rejects_out_of_range_values(self) -> None:
        for percentage in (0, 110):
            with self.subTest(percentage=percentage):
                with self.assertRaises(ValueError):
                    encode_brightness(percentage)

    def test_rejects_unsupported_steps(self) -> None:
        with self.assertRaises(ValueError):
            encode_brightness(55)

    def test_rejects_non_integer_values(self) -> None:
        for value in (True, 50.0, "50"):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    encode_brightness(value)


if __name__ == "__main__":
    unittest.main()
