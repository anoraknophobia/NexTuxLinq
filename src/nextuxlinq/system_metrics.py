"""Immutable system metrics and a fixed-source read-only Linux provider."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
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
    LOAD_AVERAGE_1M = "load_average_1m"
    STORAGE_USAGE = "storage_usage"
    NETWORK_RECEIVE_RATE = "network_receive_rate"
    NETWORK_TRANSMIT_RATE = "network_transmit_rate"
    GPU_USAGE = "gpu_usage"
    GPU_TEMPERATURE = "gpu_temperature"
    GPU_MEMORY_USAGE = "gpu_memory_usage"
    STORAGE_TEMPERATURE = "storage_temperature"


class MetricValueType(StrEnum):
    PERCENTAGE = "percentage"
    TEMPERATURE = "temperature"
    DURATION = "duration"
    NUMBER = "number"
    RATE = "rate"


class MetricAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AWAITING_INITIAL_CPU_DELTA = "awaiting_initial_cpu_delta"
    AWAITING_INITIAL_DELTA = "awaiting_initial_delta"


class MetricReason(StrEnum):
    SOURCE_NOT_PRESENT = "source_not_present"
    AWAITING_INITIAL_SAMPLE = "awaiting_initial_sample"
    MALFORMED_SOURCE = "malformed_source"
    AMBIGUOUS_SOURCE = "ambiguous_source"
    UNSUPPORTED_SOURCE = "unsupported_source"
    TEMPORARY_READ_FAILURE = "temporary_read_failure"


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
        MetricId.CPU_TEMPERATURE, "CPU", MetricValueType.TEMPERATURE,
        "C", 0, True, 0.0, 100.0,
    ),
    MetricId.UPTIME: MetricDefinition(
        MetricId.UPTIME, "UPTIME", MetricValueType.DURATION,
        "", 0, False, None, None,
    ),
    MetricId.LOAD_AVERAGE_1M: MetricDefinition(
        MetricId.LOAD_AVERAGE_1M, "LOAD", MetricValueType.NUMBER,
        "", 2, False, None, None,
    ),
    MetricId.STORAGE_USAGE: MetricDefinition(
        MetricId.STORAGE_USAGE, "DISK", MetricValueType.PERCENTAGE,
        "%", 0, True, 0.0, 100.0,
    ),
    MetricId.NETWORK_RECEIVE_RATE: MetricDefinition(
        MetricId.NETWORK_RECEIVE_RATE, "NET RX", MetricValueType.RATE,
        "B/S", 1, False, None, None,
    ),
    MetricId.NETWORK_TRANSMIT_RATE: MetricDefinition(
        MetricId.NETWORK_TRANSMIT_RATE, "NET TX", MetricValueType.RATE,
        "B/S", 1, False, None, None,
    ),
    MetricId.GPU_USAGE: MetricDefinition(
        MetricId.GPU_USAGE, "GPU", MetricValueType.PERCENTAGE,
        "%", 0, True, 0.0, 100.0,
    ),
    MetricId.GPU_TEMPERATURE: MetricDefinition(
        MetricId.GPU_TEMPERATURE, "GPU", MetricValueType.TEMPERATURE,
        "C", 0, True, 0.0, 100.0,
    ),
    MetricId.GPU_MEMORY_USAGE: MetricDefinition(
        MetricId.GPU_MEMORY_USAGE, "VRAM", MetricValueType.PERCENTAGE,
        "%", 0, True, 0.0, 100.0,
    ),
    MetricId.STORAGE_TEMPERATURE: MetricDefinition(
        MetricId.STORAGE_TEMPERATURE, "DISK", MetricValueType.TEMPERATURE,
        "C", 0, True, 0.0, 100.0,
    ),
})
LEGACY_TEMPERATURE_LABELS: Mapping[MetricId, str] = MappingProxyType({
    MetricId.CPU_TEMPERATURE: "CPU TEMP",
    MetricId.GPU_TEMPERATURE: "GPU TEMP",
    MetricId.STORAGE_TEMPERATURE: "DISK TEMP",
})


def canonical_metric_label(metric_id: MetricId, label: str) -> str:
    """Resolve exact legacy temperature defaults without changing custom labels."""
    metric = MetricId(metric_id)
    if label == LEGACY_TEMPERATURE_LABELS.get(metric):
        return METRIC_DEFINITIONS[metric].label
    return label


BAR_METRICS = frozenset(
    metric_id for metric_id, definition in METRIC_DEFINITIONS.items()
    if definition.supports_bar
)
_FALLBACK_SAMPLE_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)

# These display ceilings keep intrinsically unbounded metrics inside a stable,
# documented bitmap-font representation. Values above a ceiling retain the
# ceiling text instead of growing the display width without limit.
MAX_DISPLAY_LOAD_AVERAGE = 9999.0
MAX_DISPLAY_NETWORK_RATE = 9999.0 * 1024.0 ** 3
MAX_DISPLAY_UPTIME_SECONDS = float((999999 * 24 + 23) * 3600 + 3599)


@dataclass(frozen=True, slots=True)
class SystemMetricsSnapshot:
    """One immutable sanitized sample shared by a complete frame."""

    sample_monotonic: float
    sample_time: datetime
    values: Mapping[MetricId, float]
    availability: Mapping[MetricId, MetricAvailability]
    source_status: str
    reasons: Mapping[MetricId, MetricReason | None] = field(default_factory=dict)

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
            normalized_value = float(raw_value)
            value_type = METRIC_DEFINITIONS[metric_id].value_type
            if (
                value_type is MetricValueType.PERCENTAGE
                and not 0.0 <= normalized_value <= 100.0
            ) or (
                value_type in {
                    MetricValueType.DURATION,
                    MetricValueType.NUMBER,
                    MetricValueType.RATE,
                }
                and normalized_value < 0.0
            ) or (
                value_type is MetricValueType.TEMPERATURE
                and not -20.0 <= normalized_value <= 150.0
            ):
                raise ValueError("metric value is outside its canonical range.")
            values[metric_id] = normalized_value
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
        reasons: dict[MetricId, MetricReason | None] = {}
        for metric_id in MetricId:
            raw_reason = self.reasons.get(metric_id)
            reasons[metric_id] = (
                None if raw_reason is None else MetricReason(raw_reason)
            )
            if (
                availability[metric_id] is not MetricAvailability.AVAILABLE
                and reasons[metric_id] is None
            ):
                reasons[metric_id] = MetricReason.SOURCE_NOT_PRESENT
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "availability", MappingProxyType(availability))
        object.__setattr__(self, "reasons", MappingProxyType(reasons))

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
            reasons={
                metric_id: MetricReason.TEMPORARY_READ_FAILURE
                for metric_id in MetricId
            },
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
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError("metric value must be finite and numeric.")
    normalized = float(value)
    if metric is MetricId.UPTIME:
        return format_uptime(min(normalized, MAX_DISPLAY_UPTIME_SECONDS))
    if metric in {
        MetricId.NETWORK_RECEIVE_RATE,
        MetricId.NETWORK_TRANSMIT_RATE,
    }:
        normalized = min(normalized, MAX_DISPLAY_NETWORK_RATE)
        divisor, suffix = (1024.0, "KB/S")
        if normalized >= 1024.0 ** 3:
            divisor, suffix = (1024.0 ** 3, "GB/S")
        elif normalized >= 1024.0 ** 2:
            divisor, suffix = (1024.0 ** 2, "MB/S")
        return f"{normalized / divisor:.{precision}f}{suffix}"
    if metric is MetricId.LOAD_AVERAGE_1M:
        normalized = min(normalized, MAX_DISPLAY_LOAD_AVERAGE)
    if METRIC_DEFINITIONS[metric].value_type is MetricValueType.TEMPERATURE:
        return f"{normalized:.{precision}f}°C"
    suffix = METRIC_DEFINITIONS[metric].unit
    return f"{normalized:.{precision}f}{suffix}"


def metric_value_format_envelope(
    metric_id: MetricId,
    precision: int,
) -> tuple[str, ...]:
    """Return every width-relevant maximum form for stable font selection."""
    metric = MetricId(metric_id)
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 2:
        raise ValueError("precision must be between zero and two.")
    if METRIC_DEFINITIONS[metric].value_type is MetricValueType.PERCENTAGE:
        return (format_metric_value(metric, 99.0, precision),)
    if METRIC_DEFINITIONS[metric].value_type is MetricValueType.TEMPERATURE:
        return (format_metric_value(metric, 99.0, precision),)
    if metric is MetricId.UPTIME:
        return ("59:59", "23:59", "999999D 23H")
    if metric is MetricId.LOAD_AVERAGE_1M:
        return (format_metric_value(metric, MAX_DISPLAY_LOAD_AVERAGE, precision),)
    if metric in {
        MetricId.NETWORK_RECEIVE_RATE,
        MetricId.NETWORK_TRANSMIT_RATE,
    }:
        decimals = f".{('0' * precision)}" if precision else ""
        return (
            f"1024{decimals}KB/S",
            f"1024{decimals}MB/S",
            f"9999{decimals}GB/S",
        )
    raise ValueError("metric format envelope is unavailable.")


@dataclass(frozen=True, slots=True)
class TemperatureReading:
    """One internal safe temperature candidate without a public path."""

    label: str
    value: str
    source_order: int
    source_type: str = ""


@dataclass(frozen=True, slots=True)
class StorageUsageReading:
    """Fixed root-filesystem capacity values returned by the reader."""

    total_bytes: int
    available_bytes: int


@dataclass(frozen=True, slots=True)
class GpuTelemetryReading:
    """Sanitized values from one deterministically selected DRM device."""

    selection_reason: MetricReason | None
    usage: str | None = None
    memory_total: str | None = None
    memory_used: str | None = None
    temperatures: tuple[TemperatureReading, ...] = ()


class LinuxMetricReader:
    """Read only fixed procfs/sysfs metric interfaces."""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        thermal_root: Path = Path("/sys/class/thermal"),
        hwmon_root: Path = Path("/sys/class/hwmon"),
        drm_root: Path = Path("/sys/class/drm"),
        filesystem_stat: Callable[[], object] | None = None,
    ) -> None:
        self._proc_root = Path(proc_root)
        self._thermal_root = Path(thermal_root)
        self._hwmon_root = Path(hwmon_root)
        self._drm_root = Path(drm_root)
        self._filesystem_stat = (
            (lambda: os.statvfs("/"))
            if filesystem_stat is None
            else filesystem_stat
        )

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

    def loadavg(self) -> str | None:
        return self._safe_read(self._proc_root / "loadavg")

    def network_dev(self) -> str | None:
        return self._safe_read(self._proc_root / "net" / "dev")

    def storage_usage(self) -> StorageUsageReading | None:
        try:
            details = self._filesystem_stat()
            blocks = int(getattr(details, "f_blocks"))
            available = int(getattr(details, "f_bavail"))
            fragment_size = int(
                getattr(details, "f_frsize", 0) or getattr(details, "f_bsize")
            )
        except (AttributeError, OSError, TypeError, ValueError, OverflowError):
            return None
        if blocks <= 0 or fragment_size <= 0 or not 0 <= available <= blocks:
            return None
        return StorageUsageReading(
            total_bytes=blocks * fragment_size,
            available_bytes=available * fragment_size,
        )

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
                normalized = label.strip()
                readings.append(TemperatureReading(
                    normalized, value.strip(), order, normalized,
                ))
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
                    readings.append(TemperatureReading(
                        label, value.strip(), order, device_name,
                    ))
                    order += 1
        return tuple(readings)

    @staticmethod
    def _card_number(path: Path) -> int | None:
        matched = re.fullmatch(r"card([0-9]+)", path.name)
        return None if matched is None else int(matched.group(1))

    def _gpu_cards(self) -> tuple[Path, ...]:
        try:
            candidates = tuple(self._drm_root.glob("card*"))
        except OSError:
            return ()
        return tuple(sorted(
            (path for path in candidates if self._card_number(path) is not None),
            key=lambda path: self._card_number(path) or 0,
        ))

    def _select_gpu(self) -> tuple[Path | None, MetricReason | None]:
        cards = self._gpu_cards()
        if not cards:
            return None, MetricReason.SOURCE_NOT_PRESENT
        primary = [
            card for card in cards
            if (self._safe_read(card / "device" / "boot_vga") or "").strip() == "1"
        ]
        if len(primary) == 1:
            return primary[0], None
        if len(primary) > 1:
            return None, MetricReason.AMBIGUOUS_SOURCE
        active = [
            card for card in cards
            if (self._safe_read(card / "device" / "power" / "runtime_status") or "").strip()
            == "active"
        ]
        if len(active) == 1:
            return active[0], None
        if len(cards) == 1:
            return cards[0], None
        return None, MetricReason.AMBIGUOUS_SOURCE

    def gpu_telemetry(self) -> GpuTelemetryReading:
        card, reason = self._select_gpu()
        if card is None:
            return GpuTelemetryReading(reason)
        device = card / "device"
        readings: list[TemperatureReading] = []
        order = 0
        try:
            hwmon_entries = sorted((device / "hwmon").glob("hwmon*"))
        except OSError:
            hwmon_entries = []
        for entry in hwmon_entries:
            try:
                inputs = sorted(entry.glob("temp*_input"))
            except OSError:
                inputs = []
            for input_path in inputs:
                stem = input_path.name.removesuffix("_input")
                label = (self._safe_read(entry / f"{stem}_label") or "").strip()
                value = self._safe_read(input_path)
                if label and value is not None:
                    readings.append(TemperatureReading(
                        label, value.strip(), order, "gpu",
                    ))
                    order += 1
        return GpuTelemetryReading(
            selection_reason=None,
            usage=self._safe_read(device / "gpu_busy_percent"),
            memory_total=self._safe_read(device / "mem_info_vram_total"),
            memory_used=self._safe_read(device / "mem_info_vram_used"),
            temperatures=tuple(readings),
        )


_CPU_LABEL = re.compile(r"cpu|package|core|tctl|tdie|soc", re.IGNORECASE)
_UNRELATED_LABEL = re.compile(
    r"gpu|nvme|drive|storage|ssd|battery|ambient|wifi",
    re.IGNORECASE,
)
_STORAGE_LABEL = re.compile(r"nvme|drive|storage|ssd", re.IGNORECASE)
_GPU_TEMPERATURE_PRIORITY = {
    "edge": 0,
    "gpu": 1,
    "junction": 2,
    "hotspot": 3,
}


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
        self._previous_network: tuple[
            float, Mapping[str, tuple[int, int]]
        ] | None = None
        self._last_snapshot: SystemMetricsSnapshot | None = None
        self._read_failures: set[str] = set()

    @property
    def last_snapshot(self) -> SystemMetricsSnapshot | None:
        return self._last_snapshot

    def _reader_value(self, name: str) -> object | None:
        try:
            reader = getattr(self._reader, name)
        except AttributeError:
            return None
        except Exception:
            self._read_failures.add(name)
            return None
        try:
            return reader()
        except Exception:
            self._read_failures.add(name)
            return None

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
    def _load_average(raw: str | None) -> float | None:
        if raw is None:
            return None
        try:
            value = float(raw.split()[0])
        except (IndexError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    @staticmethod
    def _storage_percentage(reading: object | None) -> float | None:
        if type(reading) is not StorageUsageReading:
            return None
        total = reading.total_bytes
        available = reading.available_bytes
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or isinstance(available, bool)
            or not isinstance(available, int)
            or total <= 0
            or not 0 <= available <= total
        ):
            return None
        return min(100.0, max(0.0, (total - available) / total * 100.0))

    @staticmethod
    def _network_counters(raw: str | None) -> dict[str, tuple[int, int]] | None:
        if raw is None:
            return None
        counters: dict[str, tuple[int, int]] = {}
        malformed = False
        for line in raw.splitlines():
            if ":" not in line:
                continue
            name, payload = line.split(":", 1)
            interface = name.strip()
            if not interface or interface == "lo":
                continue
            fields = payload.split()
            try:
                received = int(fields[0])
                transmitted = int(fields[8])
            except (IndexError, ValueError):
                malformed = True
                continue
            if received < 0 or transmitted < 0:
                malformed = True
                continue
            counters[interface] = (received, transmitted)
        if malformed:
            return None
        return counters

    def _network_rates(
        self,
        raw: str | None,
        sample_monotonic: float,
    ) -> tuple[
        tuple[float, float] | None,
        MetricAvailability,
        MetricReason,
    ]:
        current = self._network_counters(raw)
        if current is None or not current:
            self._previous_network = None
            return (
                None,
                MetricAvailability.UNAVAILABLE,
                MetricReason.SOURCE_NOT_PRESENT
                if raw is None or current == {}
                else MetricReason.MALFORMED_SOURCE,
            )
        previous = self._previous_network
        self._previous_network = (sample_monotonic, MappingProxyType(dict(current)))
        if previous is None:
            return (
                None,
                MetricAvailability.AWAITING_INITIAL_DELTA,
                MetricReason.AWAITING_INITIAL_SAMPLE,
            )
        elapsed = sample_monotonic - previous[0]
        common = sorted(set(previous[1]).intersection(current))
        if not math.isfinite(elapsed) or elapsed <= 0 or not common:
            return (
                None,
                MetricAvailability.UNAVAILABLE,
                MetricReason.MALFORMED_SOURCE,
            )
        receive_delta = 0
        transmit_delta = 0
        for interface in common:
            receive = current[interface][0] - previous[1][interface][0]
            transmit = current[interface][1] - previous[1][interface][1]
            if receive < 0 or transmit < 0:
                return (
                    None,
                    MetricAvailability.UNAVAILABLE,
                    MetricReason.MALFORMED_SOURCE,
                )
            receive_delta += receive
            transmit_delta += transmit
        try:
            receive_rate = receive_delta / elapsed
            transmit_rate = transmit_delta / elapsed
        except OverflowError:
            return (
                None,
                MetricAvailability.UNAVAILABLE,
                MetricReason.MALFORMED_SOURCE,
            )
        if not math.isfinite(receive_rate) or not math.isfinite(transmit_rate):
            return (
                None,
                MetricAvailability.UNAVAILABLE,
                MetricReason.MALFORMED_SOURCE,
            )
        return (
            (receive_rate, transmit_rate),
            MetricAvailability.AVAILABLE,
            MetricReason.TEMPORARY_READ_FAILURE,
        )

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
            if (
                not isinstance(reading, TemperatureReading)
                or not isinstance(reading.label, str)
                or not isinstance(reading.value, str)
                or isinstance(reading.source_order, bool)
                or not isinstance(reading.source_order, int)
                or not isinstance(reading.source_type, str)
            ):
                continue
            label = reading.label[:128]
            identity = f"{reading.source_type} {label}"[:256]
            if _UNRELATED_LABEL.search(identity):
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

    @staticmethod
    def _temperature_value(raw: str) -> float | None:
        if not isinstance(raw, str):
            return None
        try:
            raw_value = float(raw)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(raw_value):
            return None
        value = raw_value / 1000.0 if abs(raw_value) > 200 else raw_value
        return value if -20.0 <= value <= 150.0 else None

    @classmethod
    def _gpu_temperature(
        cls,
        readings: tuple[TemperatureReading, ...],
    ) -> float | None:
        candidates: list[tuple[int, int, float]] = []
        for reading in readings:
            if (
                not isinstance(reading, TemperatureReading)
                or not isinstance(reading.label, str)
                or not isinstance(reading.value, str)
                or isinstance(reading.source_order, bool)
                or not isinstance(reading.source_order, int)
            ):
                continue
            normalized = reading.label.strip().casefold()
            priority = next(
                (
                    score for label, score in _GPU_TEMPERATURE_PRIORITY.items()
                    if label in normalized
                ),
                None,
            )
            value = cls._temperature_value(reading.value)
            if priority is not None and value is not None:
                candidates.append((priority, reading.source_order, value))
        return None if not candidates else min(candidates)[2]

    @classmethod
    def _storage_temperature(
        cls,
        readings: tuple[TemperatureReading, ...],
    ) -> float | None:
        candidates: list[tuple[int, int, float]] = []
        for reading in readings:
            if (
                not isinstance(reading, TemperatureReading)
                or not isinstance(reading.label, str)
                or not isinstance(reading.value, str)
                or isinstance(reading.source_order, bool)
                or not isinstance(reading.source_order, int)
                or not isinstance(reading.source_type, str)
            ):
                continue
            identity = f"{reading.source_type} {reading.label}"[:256]
            if not _STORAGE_LABEL.search(identity):
                continue
            value = cls._temperature_value(reading.value)
            if value is None:
                continue
            preferred = 0 if "composite" in reading.label.casefold() else 1
            candidates.append((preferred, reading.source_order, value))
        return None if not candidates else min(candidates)[2]

    @staticmethod
    def _percentage_value(raw: str | None) -> float | None:
        if not isinstance(raw, str):
            return None
        try:
            value = float(raw.strip())
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(value) or not 0 <= value <= 100:
            return None
        return value

    @staticmethod
    def _gpu_memory_percentage(reading: GpuTelemetryReading) -> float | None:
        if (
            not isinstance(reading.memory_total, (str, type(None)))
            or not isinstance(reading.memory_used, (str, type(None)))
        ):
            return None
        try:
            total = int((reading.memory_total or "").strip())
            used = int((reading.memory_used or "").strip())
        except (ValueError, OverflowError):
            return None
        if total <= 0 or not 0 <= used <= total:
            return None
        return min(100.0, max(0.0, used / total * 100.0))

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
        self._read_failures.clear()
        values: dict[MetricId, float] = {}
        availability = {metric_id: MetricAvailability.UNAVAILABLE for metric_id in MetricId}
        reasons: dict[MetricId, MetricReason | None] = {
            metric_id: MetricReason.SOURCE_NOT_PRESENT for metric_id in MetricId
        }

        def available(metric_id: MetricId, value: float) -> None:
            values[metric_id] = value
            availability[metric_id] = MetricAvailability.AVAILABLE
            reasons[metric_id] = None

        cpu_raw = self._reader_value("cpu_stat")
        cpu, cpu_state = self._cpu_usage(cpu_raw if isinstance(cpu_raw, str) else None)
        availability[MetricId.CPU_USAGE] = cpu_state
        reasons[MetricId.CPU_USAGE] = (
            None if cpu_state is MetricAvailability.AVAILABLE
            else MetricReason.AWAITING_INITIAL_SAMPLE
            if cpu_state is MetricAvailability.AWAITING_INITIAL_CPU_DELTA
            else MetricReason.SOURCE_NOT_PRESENT
            if cpu_raw is None
            else MetricReason.MALFORMED_SOURCE
        )
        if cpu is not None:
            available(MetricId.CPU_USAGE, cpu)

        memory_raw = self._reader_value("meminfo")
        memory = self._memory_usage(memory_raw if isinstance(memory_raw, str) else None)
        if memory is not None:
            available(MetricId.MEMORY_USAGE, memory)
        elif memory_raw is not None:
            reasons[MetricId.MEMORY_USAGE] = MetricReason.MALFORMED_SOURCE

        uptime_raw = self._reader_value("uptime")
        uptime = self._uptime_value(uptime_raw if isinstance(uptime_raw, str) else None)
        if uptime is not None:
            available(MetricId.UPTIME, uptime)
        elif uptime_raw is not None:
            reasons[MetricId.UPTIME] = MetricReason.MALFORMED_SOURCE

        load_raw = self._reader_value("loadavg")
        load = self._load_average(load_raw if isinstance(load_raw, str) else None)
        if load is not None:
            available(MetricId.LOAD_AVERAGE_1M, load)
        elif load_raw is not None:
            reasons[MetricId.LOAD_AVERAGE_1M] = MetricReason.MALFORMED_SOURCE

        storage_raw = self._reader_value("storage_usage")
        storage = self._storage_percentage(storage_raw)
        if storage is not None:
            available(MetricId.STORAGE_USAGE, storage)
        elif storage_raw is not None:
            reasons[MetricId.STORAGE_USAGE] = MetricReason.MALFORMED_SOURCE

        network_raw = self._reader_value("network_dev")
        network, network_state, network_reason = self._network_rates(
            network_raw if isinstance(network_raw, str) else None,
            monotonic_value,
        )
        for metric_id in (
            MetricId.NETWORK_RECEIVE_RATE,
            MetricId.NETWORK_TRANSMIT_RATE,
        ):
            availability[metric_id] = network_state
            reasons[metric_id] = network_reason
        if network is not None:
            available(MetricId.NETWORK_RECEIVE_RATE, network[0])
            available(MetricId.NETWORK_TRANSMIT_RATE, network[1])

        temperature_raw = self._reader_value("temperatures")
        temperature_readings = (
            temperature_raw
            if isinstance(temperature_raw, tuple)
            and all(isinstance(item, TemperatureReading) for item in temperature_raw)
            else ()
        )
        temperature = self._temperature(temperature_readings)
        if temperature is not None:
            available(MetricId.CPU_TEMPERATURE, temperature)
        elif temperature_raw is not None:
            reasons[MetricId.CPU_TEMPERATURE] = (
                MetricReason.MALFORMED_SOURCE
                if temperature_raw is not None
                and (
                    not isinstance(temperature_raw, tuple)
                    or any(
                        isinstance(item.label, str)
                        and _CPU_LABEL.search(item.label)
                        for item in temperature_readings
                    )
                )
                else MetricReason.UNSUPPORTED_SOURCE
            )

        storage_temperature = self._storage_temperature(
            temperature_readings
        )
        if storage_temperature is not None:
            available(MetricId.STORAGE_TEMPERATURE, storage_temperature)
        elif temperature_raw is not None:
            reasons[MetricId.STORAGE_TEMPERATURE] = (
                MetricReason.MALFORMED_SOURCE
                if temperature_raw is not None
                and (
                    not isinstance(temperature_raw, tuple)
                    or any(
                        isinstance(item.source_type, str)
                        and isinstance(item.label, str)
                        and _STORAGE_LABEL.search(
                            f"{item.source_type} {item.label}"
                        )
                        for item in temperature_readings
                    )
                )
                else MetricReason.UNSUPPORTED_SOURCE
            )

        gpu_raw = self._reader_value("gpu_telemetry")
        gpu = (
            gpu_raw
            if type(gpu_raw) is GpuTelemetryReading
            and (
                gpu_raw.selection_reason is None
                or isinstance(gpu_raw.selection_reason, MetricReason)
            )
            and isinstance(gpu_raw.temperatures, tuple)
            else GpuTelemetryReading(MetricReason.SOURCE_NOT_PRESENT)
        )
        gpu_metrics = (
            MetricId.GPU_USAGE,
            MetricId.GPU_TEMPERATURE,
            MetricId.GPU_MEMORY_USAGE,
        )
        if gpu.selection_reason is not None:
            for metric_id in gpu_metrics:
                reasons[metric_id] = gpu.selection_reason
        else:
            gpu_usage = self._percentage_value(gpu.usage)
            gpu_temperature = self._gpu_temperature(gpu.temperatures)
            gpu_memory = self._gpu_memory_percentage(gpu)
            if gpu_usage is not None:
                available(MetricId.GPU_USAGE, gpu_usage)
            else:
                reasons[MetricId.GPU_USAGE] = (
                    MetricReason.UNSUPPORTED_SOURCE
                    if gpu.usage is None else MetricReason.MALFORMED_SOURCE
                )
            if gpu_temperature is not None:
                available(MetricId.GPU_TEMPERATURE, gpu_temperature)
            else:
                reasons[MetricId.GPU_TEMPERATURE] = (
                    MetricReason.MALFORMED_SOURCE
                    if gpu.temperatures else MetricReason.UNSUPPORTED_SOURCE
                )
            if gpu_memory is not None:
                available(MetricId.GPU_MEMORY_USAGE, gpu_memory)
            else:
                reasons[MetricId.GPU_MEMORY_USAGE] = (
                    MetricReason.UNSUPPORTED_SOURCE
                    if gpu.memory_total is None or gpu.memory_used is None
                    else MetricReason.MALFORMED_SOURCE
                )

        failure_metrics = {
            "cpu_stat": (MetricId.CPU_USAGE,),
            "meminfo": (MetricId.MEMORY_USAGE,),
            "uptime": (MetricId.UPTIME,),
            "loadavg": (MetricId.LOAD_AVERAGE_1M,),
            "storage_usage": (MetricId.STORAGE_USAGE,),
            "network_dev": (
                MetricId.NETWORK_RECEIVE_RATE,
                MetricId.NETWORK_TRANSMIT_RATE,
            ),
            "temperatures": (
                MetricId.CPU_TEMPERATURE,
                MetricId.STORAGE_TEMPERATURE,
            ),
            "gpu_telemetry": (
                MetricId.GPU_USAGE,
                MetricId.GPU_TEMPERATURE,
                MetricId.GPU_MEMORY_USAGE,
            ),
        }
        for source in self._read_failures:
            for metric_id in failure_metrics.get(source, ()):
                if availability[metric_id] is not MetricAvailability.AVAILABLE:
                    reasons[metric_id] = MetricReason.TEMPORARY_READ_FAILURE

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
            reasons,
        )
        self._last_snapshot = snapshot
        return snapshot
