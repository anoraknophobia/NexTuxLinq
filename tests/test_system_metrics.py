"""Hardware-independent tests for the fixed-source Linux metric provider."""

from __future__ import annotations

import traceback
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from nextuxlinq.system_metrics import (
    LinuxMetricReader,
    LinuxSystemMetricsProvider,
    GpuTelemetryReading,
    MetricAvailability,
    MetricId,
    MetricReason,
    StorageUsageReading,
    SystemMetricsSnapshot,
    TemperatureReading,
    format_metric_value,
    format_uptime,
    metric_value_format_envelope,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class FakeReader:
    def __init__(self) -> None:
        self.cpu = "cpu 100 0 100 800 0 0 0 0\n"
        self.memory = "MemTotal: 1000 kB\nMemAvailable: 400 kB\n"
        self.up = "3661.50 0.0\n"
        self.thermal = ()
        self.load = None
        self.network = None
        self.storage = None
        self.gpu = GpuTelemetryReading(MetricReason.SOURCE_NOT_PRESENT)

    def cpu_stat(self):
        return self.cpu

    def meminfo(self):
        return self.memory

    def uptime(self):
        return self.up

    def temperatures(self):
        return self.thermal

    def loadavg(self):
        return self.load

    def network_dev(self):
        return self.network

    def storage_usage(self):
        return self.storage

    def gpu_telemetry(self):
        return self.gpu


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


def test_metric_value_format_envelopes_are_deterministic_and_include_precision() -> None:
    assert metric_value_format_envelope(MetricId.CPU_USAGE, 0) == ("100%",)
    assert metric_value_format_envelope(MetricId.GPU_USAGE, 2) == ("100.00%",)
    assert metric_value_format_envelope(MetricId.CPU_TEMPERATURE, 1) == (
        "-20.0C", "150.0C",
    )
    assert metric_value_format_envelope(MetricId.CPU_TEMPERATURE, 0) == (
        "-20C", "150C", "5C",
    )
    assert metric_value_format_envelope(MetricId.LOAD_AVERAGE_1M, 2) == (
        "9999.00",
    )
    assert metric_value_format_envelope(MetricId.NETWORK_RECEIVE_RATE, 1) == (
        "1024.0KB/S", "1024.0MB/S", "9999.0GB/S",
    )
    assert metric_value_format_envelope(MetricId.UPTIME, 0) == (
        "59:59", "23:59", "999999D 23H",
    )


def test_unbounded_metric_formatters_observe_the_documented_display_ceiling() -> None:
    assert format_metric_value(MetricId.LOAD_AVERAGE_1M, 1e100, 2) == "9999.00"
    assert format_metric_value(
        MetricId.NETWORK_RECEIVE_RATE, 1e100, 1
    ) == "9999.0GB/S"
    assert format_metric_value(MetricId.UPTIME, 1e100, 0) == "999999D 23H"


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
    assert snapshot.reasons[MetricId.CPU_USAGE] is (
        MetricReason.TEMPORARY_READ_FAILURE
    )
    assert snapshot.reasons[MetricId.CPU_TEMPERATURE] is (
        MetricReason.TEMPORARY_READ_FAILURE
    )
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
    for metric, value in (
        (MetricId.GPU_USAGE, 101.0),
        (MetricId.NETWORK_RECEIVE_RATE, -1.0),
        (MetricId.LOAD_AVERAGE_1M, -0.1),
        (MetricId.STORAGE_TEMPERATURE, 1000.0),
    ):
        with pytest.raises(ValueError, match="canonical range"):
            SystemMetricsSnapshot(
                1.0,
                NOW,
                {metric: value},
                {item: MetricAvailability.UNAVAILABLE for item in MetricId},
                "partial",
            )


def test_load_storage_and_network_rate_sources_are_parsed_without_sleeping() -> None:
    reader = FakeReader()
    reader.load = "1.24 0.50 0.25 1/100 42\n"
    reader.storage = StorageUsageReading(1_000, 250)
    reader.network = (
        "Inter-| Receive | Transmit\n"
        " lo: 9999 0 0 0 0 0 0 0 9999 0 0 0 0 0 0 0\n"
        " eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n"
    )
    times = iter((10.0, 12.0, 14.0, 16.0))
    metrics = LinuxSystemMetricsProvider(
        reader=reader,  # type: ignore[arg-type]
        monotonic=lambda: next(times),
        wall_clock=lambda: NOW,
    )

    first = metrics.sample()
    assert first.value(MetricId.LOAD_AVERAGE_1M) == pytest.approx(1.24)
    assert first.value(MetricId.STORAGE_USAGE) == pytest.approx(75.0)
    assert first.availability[MetricId.NETWORK_RECEIVE_RATE] is (
        MetricAvailability.AWAITING_INITIAL_DELTA
    )
    assert first.reasons[MetricId.NETWORK_RECEIVE_RATE] is (
        MetricReason.AWAITING_INITIAL_SAMPLE
    )

    reader.network = (
        "eth0: 5096 0 0 0 0 0 0 0 6096 0 0 0 0 0 0 0\n"
        "eth1: 900000 0 0 0 0 0 0 0 900000 0 0 0 0 0 0 0\n"
    )
    second = metrics.sample()
    assert second.value(MetricId.NETWORK_RECEIVE_RATE) == pytest.approx(2048.0)
    assert second.value(MetricId.NETWORK_TRANSMIT_RATE) == pytest.approx(2048.0)
    assert format_metric_value(
        MetricId.NETWORK_RECEIVE_RATE, 2048.0, 1
    ) == "2.0KB/S"

    reader.network = (
        "eth1: 902048 0 0 0 0 0 0 0 904096 0 0 0 0 0 0 0\n"
    )
    third = metrics.sample()
    assert third.value(MetricId.NETWORK_RECEIVE_RATE) == pytest.approx(1024.0)
    assert third.value(MetricId.NETWORK_TRANSMIT_RATE) == pytest.approx(2048.0)

    reader.network = (
        "eth1: 1 0 0 0 0 0 0 0 1 0 0 0 0 0 0 0\n"
    )
    reset = metrics.sample()
    assert reset.value(MetricId.NETWORK_RECEIVE_RATE) is None
    assert reset.reasons[MetricId.NETWORK_RECEIVE_RATE] is MetricReason.MALFORMED_SOURCE


def test_root_filesystem_stat_and_storage_hwmon_are_generic_and_deterministic(
    tmp_path: Path,
) -> None:
    hwmon_root = tmp_path / "hwmon"
    hwmon_root.mkdir()
    nvme = hwmon_root / "hwmon2"
    nvme.mkdir()
    (nvme / "name").write_text("nvme\n", encoding="ascii")
    (nvme / "temp1_label").write_text("Composite\n", encoding="ascii")
    (nvme / "temp1_input").write_text("41000\n", encoding="ascii")
    (nvme / "temp2_label").write_text("Sensor 1\n", encoding="ascii")
    (nvme / "temp2_input").write_text("45000\n", encoding="ascii")
    reader = LinuxMetricReader(
        proc_root=tmp_path / "proc",
        thermal_root=tmp_path / "thermal",
        hwmon_root=hwmon_root,
        drm_root=tmp_path / "drm",
        filesystem_stat=lambda: SimpleNamespace(
            f_blocks=200,
            f_bavail=50,
            f_frsize=4096,
            f_bsize=4096,
        ),
    )

    assert reader.storage_usage() == StorageUsageReading(819_200, 204_800)
    assert LinuxSystemMetricsProvider._storage_temperature(
        reader.temperatures()
    ) == 41.0


@pytest.mark.parametrize("raw", ["", "nan 0 0", "-0.1 0 0", "broken"])
def test_malformed_load_average_is_unavailable(raw: str) -> None:
    reader = FakeReader()
    reader.load = raw
    snapshot = provider(reader).sample()
    assert snapshot.value(MetricId.LOAD_AVERAGE_1M) is None
    assert snapshot.reasons[MetricId.LOAD_AVERAGE_1M] is MetricReason.MALFORMED_SOURCE


def test_loopback_only_and_zero_network_time_are_unavailable() -> None:
    reader = FakeReader()
    reader.network = "lo: 10 0 0 0 0 0 0 0 20 0 0 0 0 0 0 0\n"
    metrics = provider(reader)
    first = metrics.sample()
    assert first.value(MetricId.NETWORK_RECEIVE_RATE) is None
    assert first.reasons[MetricId.NETWORK_RECEIVE_RATE] is MetricReason.SOURCE_NOT_PRESENT

    reader.network = "eth0: 10 0 0 0 0 0 0 0 20 0 0 0 0 0 0 0\n"
    awaiting = metrics.sample()
    assert awaiting.availability[MetricId.NETWORK_RECEIVE_RATE] is (
        MetricAvailability.AWAITING_INITIAL_DELTA
    )
    reader.network = "eth0: 20 0 0 0 0 0 0 0 30 0 0 0 0 0 0 0\n"
    assert metrics.sample().value(MetricId.NETWORK_RECEIVE_RATE) is None


def _write_gpu_card(
    drm: Path,
    number: int,
    *,
    primary: bool = False,
    active: bool = False,
) -> Path:
    card = drm / f"card{number}"
    device = card / "device"
    (device / "power").mkdir(parents=True)
    (device / "boot_vga").write_text(
        "1\n" if primary else "0\n", encoding="ascii"
    )
    (device / "power" / "runtime_status").write_text(
        "active\n" if active else "suspended\n", encoding="ascii"
    )
    return device


def test_drm_gpu_selection_usage_memory_and_temperature_are_deterministic(
    tmp_path: Path,
) -> None:
    drm = tmp_path / "drm"
    drm.mkdir()
    _write_gpu_card(drm, 0, active=True)
    device = _write_gpu_card(drm, 1, primary=True)
    (device / "gpu_busy_percent").write_text("73\n", encoding="ascii")
    (device / "mem_info_vram_total").write_text("1000\n", encoding="ascii")
    (device / "mem_info_vram_used").write_text("250\n", encoding="ascii")
    hwmon = device / "hwmon" / "hwmon7"
    hwmon.mkdir(parents=True)
    (hwmon / "temp1_label").write_text("junction\n", encoding="ascii")
    (hwmon / "temp1_input").write_text("81000\n", encoding="ascii")
    (hwmon / "temp2_label").write_text("edge\n", encoding="ascii")
    (hwmon / "temp2_input").write_text("62000\n", encoding="ascii")
    reader = LinuxMetricReader(
        proc_root=tmp_path / "proc",
        thermal_root=tmp_path / "thermal",
        hwmon_root=tmp_path / "hwmon",
        drm_root=drm,
        filesystem_stat=lambda: object(),
    )
    selected = reader.gpu_telemetry()
    assert selected.selection_reason is None
    assert selected.usage == "73\n"
    assert LinuxSystemMetricsProvider._gpu_temperature(
        selected.temperatures
    ) == pytest.approx(62.0)
    assert LinuxSystemMetricsProvider._gpu_memory_percentage(selected) == 25.0

    class Reader(FakeReader):
        def gpu_telemetry(self):
            return selected

    snapshot = provider(Reader()).sample()
    assert snapshot.value(MetricId.GPU_USAGE) == 73.0
    assert snapshot.value(MetricId.GPU_TEMPERATURE) == 62.0
    assert snapshot.value(MetricId.GPU_MEMORY_USAGE) == 25.0


def test_drm_gpu_absence_and_ambiguity_are_sanitized(tmp_path: Path) -> None:
    drm = tmp_path / "drm"
    drm.mkdir()
    reader = LinuxMetricReader(
        proc_root=tmp_path / "proc",
        thermal_root=tmp_path / "thermal",
        hwmon_root=tmp_path / "hwmon",
        drm_root=drm,
        filesystem_stat=lambda: object(),
    )
    assert reader.gpu_telemetry().selection_reason is MetricReason.SOURCE_NOT_PRESENT
    active = _write_gpu_card(drm, 0, active=True)
    _write_gpu_card(drm, 1)
    assert reader.gpu_telemetry().selection_reason is None
    (active / "power" / "runtime_status").write_text(
        "suspended\n", encoding="ascii"
    )
    assert reader.gpu_telemetry().selection_reason is MetricReason.AMBIGUOUS_SOURCE

    fake = FakeReader()
    fake.gpu = GpuTelemetryReading(MetricReason.AMBIGUOUS_SOURCE)
    snapshot = provider(fake).sample()
    for metric in (
        MetricId.GPU_USAGE,
        MetricId.GPU_TEMPERATURE,
        MetricId.GPU_MEMORY_USAGE,
    ):
        assert snapshot.value(metric) is None
        assert snapshot.reasons[metric] is MetricReason.AMBIGUOUS_SOURCE


def test_malformed_gpu_fields_are_independently_unavailable() -> None:
    reader = FakeReader()
    reader.gpu = GpuTelemetryReading(
        selection_reason=None,
        usage="101",
        memory_total="100",
        memory_used="200",
        temperatures=(TemperatureReading("edge", "not-a-temp", 0, "gpu"),),
    )

    snapshot = provider(reader).sample()

    for metric in (
        MetricId.GPU_USAGE,
        MetricId.GPU_MEMORY_USAGE,
    ):
        assert snapshot.value(metric) is None
        assert snapshot.reasons[metric] is MetricReason.MALFORMED_SOURCE
    assert snapshot.value(MetricId.GPU_TEMPERATURE) is None
    assert snapshot.reasons[MetricId.GPU_TEMPERATURE] is (
        MetricReason.MALFORMED_SOURCE
    )
    assert snapshot.value(MetricId.MEMORY_USAGE) == 60.0


def test_malformed_injected_reader_objects_never_escape_provider() -> None:
    reader = FakeReader()
    reader.storage = StorageUsageReading("bad", 0)  # type: ignore[arg-type]
    reader.thermal = (
        TemperatureReading(1, object(), 0, None),  # type: ignore[arg-type]
    )
    reader.gpu = GpuTelemetryReading(
        selection_reason=None,
        usage=object(),  # type: ignore[arg-type]
        memory_total=object(),  # type: ignore[arg-type]
        memory_used="1",
        temperatures=("bad",),  # type: ignore[arg-type]
    )

    snapshot = provider(reader).sample()

    assert snapshot.value(MetricId.STORAGE_USAGE) is None
    assert snapshot.value(MetricId.CPU_TEMPERATURE) is None
    assert snapshot.value(MetricId.GPU_USAGE) is None
    assert snapshot.value(MetricId.GPU_MEMORY_USAGE) is None
    assert snapshot.value(MetricId.MEMORY_USAGE) == 60.0


def test_storage_temperature_prefers_composite_and_rejects_malformed() -> None:
    readings = (
        TemperatureReading("Sensor 1", "50000", 0, "drivetemp"),
        TemperatureReading("Composite", "42000", 1, "nvme"),
        TemperatureReading("Composite", "malformed", 2, "nvme"),
    )
    assert LinuxSystemMetricsProvider._storage_temperature(readings) == 42.0
    reader = FakeReader()
    reader.thermal = readings
    snapshot = provider(reader).sample()
    assert snapshot.value(MetricId.STORAGE_TEMPERATURE) == 42.0


def test_every_fixed_source_is_read_once_and_snapshot_reasons_are_immutable() -> None:
    class CountingReader(FakeReader):
        def __init__(self) -> None:
            super().__init__()
            self.calls: dict[str, int] = {}

        def __getattribute__(self, name):
            value = super().__getattribute__(name)
            if name in {
                "cpu_stat", "meminfo", "uptime", "loadavg", "network_dev",
                "storage_usage", "temperatures", "gpu_telemetry",
            } and callable(value):
                def counted():
                    self.calls[name] = self.calls.get(name, 0) + 1
                    return value()
                return counted
            return value

    reader = CountingReader()
    snapshot = provider(reader).sample()
    assert reader.calls == {
        "cpu_stat": 1,
        "meminfo": 1,
        "uptime": 1,
        "loadavg": 1,
        "network_dev": 1,
        "storage_usage": 1,
        "temperatures": 1,
        "gpu_telemetry": 1,
    }
    with pytest.raises(TypeError):
        snapshot.reasons[MetricId.GPU_USAGE] = (  # type: ignore[index]
            MetricReason.MALFORMED_SOURCE
        )


def test_metric_provider_uses_no_commands_network_or_dynamic_imports() -> None:
    import nextuxlinq.system_metrics as module

    source = inspect.getsource(module)
    for prohibited in (
        "subprocess", "os.system", "Popen", "requests", "urlopen",
        "importlib", "__import__", "eval(", "exec(",
    ):
        assert prohibited not in source
