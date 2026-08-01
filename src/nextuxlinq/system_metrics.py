"""Immutable system metrics and a fixed-source read-only Linux provider."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping


class MetricId(StrEnum):
    CPU_USAGE = "cpu_usage"
    MEMORY_USAGE = "memory_usage"
    CPU_TEMPERATURE = "cpu_temperature"
    UPTIME = "uptime"


class MetricValueType(StrEnum):
    PERCENTAGE = "percentage"
    TEMPERATURE = "temperature"
    DURATION = "duration"


class MetricAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AWAITING_INITIAL_CPU_DELTA = "awaiting_initial_cpu_delta"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    id: MetricId
    label: str
    value_type: MetricValueType
    unit: str
    default_precision: int
    supports_bar: bool
    bar_minimum: float | None
    bar_maximum: float | None


METRIC_DEFINITIONS: Mapping[MetricId, MetricDefinition] = MappingProxyType({
    MetricId.CPU_USAGE: MetricDefinition(
        MetricId.CPU_USAGE, "CPU", MetricValueType.PERCENTAGE,
        "%", 0, True, 0.0, 100.0,
    ),
    MetricId.MEMORY_USAGE: MetricDefinition(
        MetricId.MEMORY_USAGE, "RAM", MetricValueType.PERCENTAGE,
        "%", 0, True, 0.0, 100.0,
    ),
    MetricId.CPU_TEMPERATURE: MetricDefinition(
        MetricId.CPU_TEMPERATURE, "CPU TEMP", MetricValueType.TEMPERATURE,
        "C", 0, True, 0.0, 100.0,
    ),
    MetricId.UPTIME: MetricDefinition(
        MetricId.UPTIME, "UPTIME", MetricValueType.DURATION,
        "", 0, False, None, None,
    ),
})
BAR_METRICS = frozenset(
    metric_id for metric_id, definition in METRIC_DEFINITIONS.items()
    if definition.supports_bar
)
_FALLBACK_SAMPLE_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class SystemMetricsSnapshot:
    """One immutable sanitized sample shared by a complete frame."""

    sample_monotonic: float
    sample_time: datetime
    values: Mapping[MetricId, float]
    availability: Mapping[MetricId, MetricAvailability]
    source_status: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.sample_monotonic, bool)
            or not isinstance(self.sample_monotonic, (int, float))
            or not math.isfinite(float(self.sample_monotonic))
            or float(self.sample_monotonic) < 0
        ):
            raise ValueError("sample_monotonic must be finite and non-negative.")
        object.__setattr__(self, "sample_monotonic", float(self.sample_monotonic))
        if (
            not isinstance(self.sample_time, datetime)
            or self.sample_time.tzinfo is None
            or self.sample_time.utcoffset() is None
        ):
            raise ValueError("sample_time must be timezone-aware.")
        values: dict[MetricId, float] = {}
        for raw_id, raw_value in self.values.items():
            metric_id = MetricId(raw_id)
            if (
                isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not math.isfinite(float(raw_value))
            ):
                raise ValueError("metric values must be finite numbers.")
            values[metric_id] = float(raw_value)
        availability = {
            MetricId(raw_id): MetricAvailability(raw_state)
            for raw_id, raw_state in self.availability.items()
        }
        if set(availability) != set(MetricId):
            raise ValueError("availability must describe every metric.")
        if any(
            availability[metric_id] is MetricAvailability.AVAILABLE
            and metric_id not in values
            for metric_id in MetricId
        ):
            raise ValueError("available metrics must contain a value.")
        if self.source_status not in {"ok", "partial", "unavailable"}:
            raise ValueError("source_status is invalid.")
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "availability", MappingProxyType(availability))

    def value(self, metric_id: MetricId) -> float | None:
        metric = MetricId(metric_id)
        if self.availability[metric] is not MetricAvailability.AVAILABLE:
            return None
        return self.values.get(metric)

    @classmethod
    def unavailable(
        cls,
        sample_monotonic: float,
        sample_time: datetime,
    ) -> SystemMetricsSnapshot:
        return cls(
            sample_monotonic=sample_monotonic,
            sample_time=sample_time,
            values={},
            availability={metric_id: MetricAvailability.UNAVAILABLE for metric_id in MetricId},
            source_status="unavailable",
        )


def format_uptime(seconds: float) -> str:
    """Format uptime as MM:SS, HH:MM, or Nd HHh using ASCII."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("uptime must be numeric.")
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("uptime must be finite and non-negative.")
    whole = int(value)
    if whole < 3600:
        return f"{whole // 60:02d}:{whole % 60:02d}"
    if whole < 86400:
        return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}"
    return f"{whole // 86400}D {(whole % 86400) // 3600:02d}H"


def format_metric_value(metric_id: MetricId, value: float, precision: int) -> str:
    """Format one available metric deterministically for bitmap fonts."""
    metric = MetricId(metric_id)
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 2:
        raise ValueError("precision must be between zero and two.")
    if metric is MetricId.UPTIME:
        return format_uptime(value)
    suffix = METRIC_DEFINITIONS[metric].unit
    return f"{value:.{precision}f}{suffix}"


@dataclass(frozen=True, slots=True)
class TemperatureReading:
    """One internal safe temperature candidate without a public path."""

    label: str
    value: str
    source_order: int


class LinuxMetricReader:
    """Read only fixed procfs/sysfs metric interfaces."""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        thermal_root: Path = Path("/sys/class/thermal"),
        hwmon_root: Path = Path("/sys/class/hwmon"),
    ) -> None:
        self._proc_root = Path(proc_root)
        self._thermal_root = Path(thermal_root)
        self._hwmon_root = Path(hwmon_root)

    @staticmethod
    def _safe_read(path: Path) -> str | None:
        failed = False
        try:
            value = path.read_text(encoding="ascii", errors="strict")
        except (OSError, UnicodeError):
            failed = True
            value = None
        return None if failed else value

    def cpu_stat(self) -> str | None:
        return self._safe_read(self._proc_root / "stat")

    def meminfo(self) -> str | None:
        return self._safe_read(self._proc_root / "meminfo")

    def uptime(self) -> str | None:
        return self._safe_read(self._proc_root / "uptime")

    def temperatures(self) -> tuple[TemperatureReading, ...]:
        readings: list[TemperatureReading] = []
        order = 0
        try:
            thermal_entries = sorted(self._thermal_root.glob("thermal_zone*"))
        except OSError:
            thermal_entries = []
        for entry in thermal_entries:
            label = self._safe_read(entry / "type")
            value = self._safe_read(entry / "temp")
            if label is not None and value is not None:
                readings.append(TemperatureReading(label.strip(), value.strip(), order))
                order += 1
        try:
            hwmon_entries = sorted(self._hwmon_root.glob("hwmon*"))
        except OSError:
            hwmon_entries = []
        for entry in hwmon_entries:
            device_name = (self._safe_read(entry / "name") or "").strip()
            try:
                inputs = sorted(entry.glob("temp*_input"))
            except OSError:
                inputs = []
            for input_path in inputs:
                stem = input_path.name.removesuffix("_input")
                label = (self._safe_read(entry / f"{stem}_label") or device_name).strip()
                value = self._safe_read(input_path)
                if label and value is not None:
                    readings.append(TemperatureReading(label, value.strip(), order))
                    order += 1
        return tuple(readings)


_CPU_LABEL = re.compile(r"cpu|package|core|tctl|tdie|soc", re.IGNORECASE)
_UNRELATED_LABEL = re.compile(r"gpu|nvme|battery|ambient|wifi", re.IGNORECASE)


class LinuxSystemMetricsProvider:
    """Stateful Linux sampler with no sleeps, commands, or network access."""

    def __init__(
        self,
        *,
        reader: LinuxMetricReader | None = None,
        monotonic: Callable[[], float],
        wall_clock: Callable[[], datetime],
    ) -> None:
        self._reader = LinuxMetricReader() if reader is None else reader
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._previous_cpu: tuple[int, int] | None = None
        self._last_snapshot: SystemMetricsSnapshot | None = None

    @property
    def last_snapshot(self) -> SystemMetricsSnapshot | None:
        return self._last_snapshot

    @staticmethod
    def _read(reader: Callable[[], object]) -> object | None:
        """Call one fixed reader while discarding all source exception data."""
        failed = False
        try:
            value = reader()
        except Exception:
            failed = True
            value = None
        return None if failed else value

    def _sample_clock(self) -> tuple[float, datetime] | None:
        failed = False
        try:
            monotonic_value = float(self._monotonic())
            wall_time = self._wall_clock()
            if (
                not math.isfinite(monotonic_value)
                or monotonic_value < 0
                or not isinstance(wall_time, datetime)
                or wall_time.tzinfo is None
                or wall_time.utcoffset() is None
            ):
                failed = True
        except Exception:
            failed = True
            monotonic_value = 0.0
            wall_time = _FALLBACK_SAMPLE_TIME
        return None if failed else (monotonic_value, wall_time)

    @staticmethod
    def _cpu_counters(raw: str | None) -> tuple[int, int] | None:
        if raw is None:
            return None
        first = raw.splitlines()[0].split() if raw.splitlines() else []
        if len(first) < 5 or first[0] != "cpu":
            return None
        failed = False
        try:
            counters = [int(value) for value in first[1:]]
        except ValueError:
            failed = True
            counters = []
        if failed or any(value < 0 for value in counters) or len(counters) < 4:
            return None
        idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
        return sum(counters), idle

    def _cpu_usage(self, raw: str | None) -> tuple[float | None, MetricAvailability]:
        current = self._cpu_counters(raw)
        if current is None:
            return None, MetricAvailability.UNAVAILABLE
        previous = self._previous_cpu
        self._previous_cpu = current
        if previous is None:
            return None, MetricAvailability.AWAITING_INITIAL_CPU_DELTA
        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
            return None, MetricAvailability.UNAVAILABLE
        value = (total_delta - idle_delta) / total_delta * 100.0
        return min(100.0, max(0.0, value)), MetricAvailability.AVAILABLE

    @staticmethod
    def _memory_usage(raw: str | None) -> float | None:
        if raw is None:
            return None
        fields: dict[str, int] = {}
        failed = False
        for line in raw.splitlines():
            if ":" not in line:
                continue
            name, remainder = line.split(":", 1)
            if name not in {"MemTotal", "MemAvailable"}:
                continue
            pieces = remainder.split()
            try:
                fields[name] = int(pieces[0])
            except (IndexError, ValueError):
                failed = True
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if failed or total is None or available is None or total <= 0 or not 0 <= available <= total:
            return None
        return min(100.0, max(0.0, (total - available) / total * 100.0))

    @staticmethod
    def _uptime_value(raw: str | None) -> float | None:
        if raw is None:
            return None
        failed = False
        try:
            value = float(raw.split()[0])
        except (IndexError, ValueError, OverflowError):
            failed = True
            value = -1.0
        if failed or not math.isfinite(value) or value < 0:
            return None
        return value

    @staticmethod
    def _temperature(readings: tuple[TemperatureReading, ...]) -> float | None:
        candidates: list[tuple[int, int, float]] = []
        for reading in readings:
            label = reading.label[:128]
            if _UNRELATED_LABEL.search(label):
                continue
            failed = False
            try:
                raw_value = float(reading.value)
            except (ValueError, OverflowError):
                failed = True
                raw_value = 0.0
            if failed or not math.isfinite(raw_value):
                continue
            value = raw_value / 1000.0 if abs(raw_value) > 200 else raw_value
            if not -20.0 <= value <= 150.0:
                continue
            score = 10 if _CPU_LABEL.search(label) else 0
            candidates.append((-score, reading.source_order, value))
        if not candidates:
            return None
        return min(candidates)[2]

    def sample(self) -> SystemMetricsSnapshot:
        """Return one complete sanitized snapshot; source failures never escape."""
        clock = self._sample_clock()
        if clock is None:
            snapshot = SystemMetricsSnapshot.unavailable(
                0.0, _FALLBACK_SAMPLE_TIME
            )
            self._last_snapshot = snapshot
            return snapshot
        monotonic_value, wall_time = clock
        values: dict[MetricId, float] = {}
        availability = {metric_id: MetricAvailability.UNAVAILABLE for metric_id in MetricId}

        cpu_raw = self._read(self._reader.cpu_stat)
        cpu, cpu_state = self._cpu_usage(cpu_raw if isinstance(cpu_raw, str) else None)
        availability[MetricId.CPU_USAGE] = cpu_state
        if cpu is not None:
            values[MetricId.CPU_USAGE] = cpu

        memory_raw = self._read(self._reader.meminfo)
        memory = self._memory_usage(memory_raw if isinstance(memory_raw, str) else None)
        if memory is not None:
            values[MetricId.MEMORY_USAGE] = memory
            availability[MetricId.MEMORY_USAGE] = MetricAvailability.AVAILABLE

        uptime_raw = self._read(self._reader.uptime)
        uptime = self._uptime_value(uptime_raw if isinstance(uptime_raw, str) else None)
        if uptime is not None:
            values[MetricId.UPTIME] = uptime
            availability[MetricId.UPTIME] = MetricAvailability.AVAILABLE

        temperature_raw = self._read(self._reader.temperatures)
        temperature = self._temperature(
            temperature_raw
            if isinstance(temperature_raw, tuple)
            and all(isinstance(item, TemperatureReading) for item in temperature_raw)
            else ()
        )
        if temperature is not None:
            values[MetricId.CPU_TEMPERATURE] = temperature
            availability[MetricId.CPU_TEMPERATURE] = MetricAvailability.AVAILABLE

        available_count = sum(
            state is MetricAvailability.AVAILABLE for state in availability.values()
        )
        status = "ok" if available_count == len(MetricId) else (
            "unavailable" if available_count == 0 else "partial"
        )
        snapshot = SystemMetricsSnapshot(
            monotonic_value,
            wall_time,
            values,
            availability,
            status,
        )
        self._last_snapshot = snapshot
        return snapshot
