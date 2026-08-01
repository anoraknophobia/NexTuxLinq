"""Hardware-independent tests for the fixed-source Linux metric provider."""

from __future__ import annotations

import traceback
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nextuxlinq.system_metrics import (
    LinuxMetricReader,
    LinuxSystemMetricsProvider,
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
    format_uptime,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class FakeReader:
    def __init__(self) -> None:
        self.cpu = "cpu 100 0 100 800 0 0 0 0\n"
        self.memory = "MemTotal: 1000 kB\nMemAvailable: 400 kB\n"
        self.up = "3661.50 0.0\n"
        self.thermal = ()

    def cpu_stat(self):
        return self.cpu

    def meminfo(self):
        return self.memory

    def uptime(self):
        return self.up

    def temperatures(self):
        return self.thermal


def provider(reader: FakeReader) -> LinuxSystemMetricsProvider:
    return LinuxSystemMetricsProvider(
        reader=reader,  # type: ignore[arg-type]
        monotonic=lambda: 12.5,
        wall_clock=lambda: NOW,
    )


def test_cpu_first_sample_then_valid_delta_and_counter_failures() -> None:
    reader = FakeReader()
    metrics = provider(reader)
    first = metrics.sample()
    assert first.availability[MetricId.CPU_USAGE] is (
        MetricAvailability.AWAITING_INITIAL_CPU_DELTA
    )

    reader.cpu = "cpu 150 0 150 900 0 0 0 0\n"
    second = metrics.sample()
    assert second.value(MetricId.CPU_USAGE) == pytest.approx(50.0)

    assert metrics.sample().value(MetricId.CPU_USAGE) is None  # zero delta
    reader.cpu = "cpu 1 0 1 1 0 0 0 0\n"
    assert metrics.sample().value(MetricId.CPU_USAGE) is None  # reset
    reader.cpu = "cpu malformed\n"
    assert metrics.sample().value(MetricId.CPU_USAGE) is None


def test_memory_uptime_and_impossible_values_are_isolated() -> None:
    reader = FakeReader()
    metrics = provider(reader)
    snapshot = metrics.sample()
    assert snapshot.value(MetricId.MEMORY_USAGE) == pytest.approx(60.0)
    assert snapshot.value(MetricId.UPTIME) == pytest.approx(3661.5)
    assert format_uptime(59) == "00:59"
    assert format_uptime(3661) == "01:01"
    assert format_uptime(90061) == "1D 01H"

    reader.memory = "MemTotal: 1000 kB\n"
    reader.up = "not uptime"
    unavailable = metrics.sample()
    assert unavailable.value(MetricId.MEMORY_USAGE) is None
    assert unavailable.value(MetricId.UPTIME) is None
    reader.memory = "MemTotal: 100 kB\nMemAvailable: 101 kB\n"
    assert metrics.sample().value(MetricId.MEMORY_USAGE) is None


def test_fixed_temperature_discovery_is_deterministic(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    thermal = tmp_path / "thermal"
    hwmon = tmp_path / "hwmon"
    proc.mkdir()
    thermal.mkdir()
    hwmon.mkdir()
    (proc / "stat").write_text("cpu 1 0 1 8 0\n", encoding="ascii")
    (proc / "meminfo").write_text(
        "MemTotal: 10 kB\nMemAvailable: 5 kB\n", encoding="ascii"
    )
    (proc / "uptime").write_text("5 0\n", encoding="ascii")
    zone = thermal / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("gpu\n", encoding="ascii")
    (zone / "temp").write_text("70000\n", encoding="ascii")
    chip = hwmon / "hwmon0"
    chip.mkdir()
    (chip / "name").write_text("generic\n", encoding="ascii")
    (chip / "temp1_label").write_text("Package id 0\n", encoding="ascii")
    (chip / "temp1_input").write_text("54250\n", encoding="ascii")
    (chip / "temp2_label").write_text("Core 0\n", encoding="ascii")
    (chip / "temp2_input").write_text("51000\n", encoding="ascii")

    reader = LinuxMetricReader(
        proc_root=proc, thermal_root=thermal, hwmon_root=hwmon
    )
    snapshot = LinuxSystemMetricsProvider(
        reader=reader, monotonic=lambda: 1.0, wall_clock=lambda: NOW
    ).sample()
    assert snapshot.value(MetricId.CPU_TEMPERATURE) == pytest.approx(54.25)
    assert "path" not in snapshot.source_status


def test_thermal_zone_cpu_temperature_is_discovered(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    thermal = tmp_path / "thermal"
    hwmon = tmp_path / "hwmon"
    proc.mkdir()
    thermal.mkdir()
    hwmon.mkdir()
    zone = thermal / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("x86_pkg_temp\n", encoding="ascii")
    (zone / "temp").write_text("48000\n", encoding="ascii")
    reader = LinuxMetricReader(
        proc_root=proc, thermal_root=thermal, hwmon_root=hwmon
    )
    assert LinuxSystemMetricsProvider._temperature(reader.temperatures()) == 48.0


def test_malformed_and_unavailable_temperature_do_not_affect_other_metrics() -> None:
    reader = FakeReader()
    from nextuxlinq.system_metrics import TemperatureReading

    reader.thermal = (
        TemperatureReading("CPU", "malformed", 0),
        TemperatureReading("NVMe", "50000", 1),
    )
    snapshot = provider(reader).sample()
    assert snapshot.value(MetricId.CPU_TEMPERATURE) is None
    assert snapshot.value(MetricId.MEMORY_USAGE) == 60.0


def test_reader_exception_is_sanitized_and_snapshot_is_immutable() -> None:
    secret = "PRIVATE_PROC_PATH_7D19"

    class BrokenReader(FakeReader):
        def cpu_stat(self):
            raise RuntimeError(secret)

        def temperatures(self):
            raise RuntimeError(secret)

    snapshot = provider(BrokenReader()).sample()
    assert snapshot.value(MetricId.CPU_USAGE) is None
    assert snapshot.value(MetricId.MEMORY_USAGE) == 60.0
    assert secret not in repr(snapshot)
    assert secret not in "".join(traceback.format_stack())
    with pytest.raises(TypeError):
        snapshot.values[MetricId.UPTIME] = 1.0  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.availability[MetricId.UPTIME] = (  # type: ignore[index]
            MetricAvailability.UNAVAILABLE
        )


def test_invalid_injected_clock_returns_deterministic_unavailable_snapshot() -> None:
    metrics = LinuxSystemMetricsProvider(
        reader=FakeReader(),  # type: ignore[arg-type]
        monotonic=lambda: float("nan"),
        wall_clock=lambda: NOW,
    )
    snapshot = metrics.sample()
    assert snapshot.source_status == "unavailable"
    assert snapshot.sample_monotonic == 0.0
    assert snapshot.sample_time == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_snapshot_rejects_malformed_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        SystemMetricsSnapshot(
            1.0,
            NOW,
            {MetricId.CPU_USAGE: float("nan")},
            {metric: MetricAvailability.UNAVAILABLE for metric in MetricId},
            "partial",
        )


def test_metric_provider_uses_no_commands_network_or_dynamic_imports() -> None:
    import nextuxlinq.system_metrics as module

    source = inspect.getsource(module)
    for prohibited in (
        "subprocess", "os.system", "Popen", "requests", "urlopen",
        "importlib", "__import__", "eval(", "exec(",
    ):
        assert prohibited not in source
