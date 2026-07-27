"""Tests for captured Matrix900 System Data widgets."""

import unittest

from nexlinq.layout import (
    SystemDataSource,
    SystemDataWidget,
    build_system_data_report,
)


class SystemDataProfileTests(unittest.TestCase):
    """Verify complete System Data profiles captured under Windows."""

    def assert_profile(self, widget: SystemDataWidget, options: str) -> None:
        expected = bytes.fromhex(
            f"01 03 01 00 00 22 01 02 {int(widget):02x} {options}"
        ) + bytes(29)

        self.assertEqual(len(expected), 64)
        self.assertEqual(build_system_data_report(widget), expected)

    def test_variant_two_matches_capture(self) -> None:
        self.assert_profile(
            SystemDataWidget.VARIANT_TWO,
            "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
            "ff ff ff ff 00 00 00 00 00 ff ff ff ff",
        )

    def test_variant_three_matches_capture(self) -> None:
        self.assert_profile(
            SystemDataWidget.VARIANT_THREE,
            "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
            "ff ff ff 00 96 ff 00 00 00 ff ff ff ff",
        )

    def test_overview_matches_capture(self) -> None:
        self.assert_profile(
            SystemDataWidget.OVERVIEW,
            "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
            "32 00 ff 64 00 ff c8 00 ff ff ff ff ff",
        )

    def test_aio_pump_matches_capture(self) -> None:
        self.assert_profile(
            SystemDataWidget.AIO_PUMP_SPEED,
            "04 02 04 07 d0 07 d0 07 d0 07 d0 00 01 "
            "00 00 00 00 ff c8 c8 00 ff ff ff ff ff",
        )

    def test_single_source_graph_cpu_matches_capture(self) -> None:
        self.assert_profile(
            SystemDataWidget.SINGLE_SOURCE_GRAPH,
            "01 02 04 07 d0 07 d0 07 d0 00 50 00 01 "
            "ff 00 00 ff ff ff c8 00 ff ff ff ff ff",
        )

    def test_single_source_graph_accepts_gpu(self) -> None:
        report = build_system_data_report(
            SystemDataWidget.SINGLE_SOURCE_GRAPH,
            source=SystemDataSource.GPU,
        )

        self.assertEqual(report[9], 0x02)
        self.assertEqual(report[18:20], bytes.fromhex("00 50"))

    def test_single_source_graph_rejects_unverified_fan(self) -> None:
        with self.assertRaises(ValueError):
            build_system_data_report(
                SystemDataWidget.SINGLE_SOURCE_GRAPH,
                source=SystemDataSource.FAN,
            )

    def test_variant_one_gpu_source(self) -> None:
        report = build_system_data_report(
            SystemDataWidget.VARIANT_ONE,
            source=SystemDataSource.GPU,
        )

        self.assertEqual(report[9], 0x02)
        self.assertEqual(report[18:20], bytes.fromhex("00 50"))

    def test_rejects_source_for_fixed_profile(self) -> None:
        with self.assertRaises(ValueError):
            build_system_data_report(
                SystemDataWidget.OVERVIEW,
                source=SystemDataSource.CPU,
            )


if __name__ == "__main__":
    unittest.main()
