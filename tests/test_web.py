"""Hardware-independent tests for the local HTTP API and GUI."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from nextuxlinq.config_store import (
    LEGACY_MIGRATION_WARNING,
    ConfigLoadResult,
    ConfigSaveError,
    ConfigStore,
)
from nextuxlinq.defaults import (
    default_configuration,
    empty_profile_v2,
    legacy_equivalent_configuration_v2,
)
from nextuxlinq.models_v2 import ConfigurationDocumentV2
from nextuxlinq.profile_presets import get_builtin_presets
from nextuxlinq.service import NexTuxLinqService
from nextuxlinq.service import RevisionConflictError
from nextuxlinq.service_manager import UserServiceStatus
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    MetricReason,
    SystemMetricsSnapshot,
)
from nextuxlinq.web import MAX_REQUEST_BODY, create_app
from nextuxlinq.widget_models import SystemMetricSettings


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.saved = []
        self.error: ConfigSaveError | None = None

    def load(self) -> ConfigLoadResult:
        return ConfigLoadResult(default_configuration())

    def save(self, configuration) -> None:
        if self.error is not None:
            raise self.error
        self.saved.append(configuration)


class FakeWorker:
    def __init__(self) -> None:
        self.frames: list[tuple[bytes, int]] = []
        self.brightness: list[tuple[int, int]] = []

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append((frame, revision))

    def set_brightness(self, value: int, revision: int = 0) -> None:
        self.brightness.append((value, revision))


def service_for_api(tmp_path: Path) -> tuple[NexTuxLinqService, MemoryStore, FakeWorker]:
    store = MemoryStore(tmp_path / "config.json")
    worker = FakeWorker()
    service = NexTuxLinqService(
        config_store=store,  # type: ignore[arg-type]
        device_worker=worker,  # type: ignore[arg-type]
        monotonic=lambda: 10.0,
    )
    service._configuration = default_configuration()
    service._saved_configuration = default_configuration()
    service._saved_revision = 1
    service._saved_revision_counter = 1
    service._started_at = 0.0
    service.state.update(
        lifecycle_state="running",
        rendering_state="rendering",
        active_configuration_revision=1,
    )
    return service, store, worker


@pytest.fixture
def api_client(tmp_path: Path):
    service, store, worker = service_for_api(tmp_path)
    client = LocalClient(create_app(service))
    yield client, service, store, worker


class LocalClient:
    """Small synchronous facade over HTTPX's in-process ASGI transport."""

    def __init__(self, app) -> None:
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def send():
            headers = {"host": "127.0.0.1", **kwargs.pop("headers", {})}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://127.0.0.1",
                headers=headers,
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)


def profile_payload(**changes):
    profile = default_configuration().active.to_dict()
    profile.update(changes)
    return profile


def apply_payload(**profile_changes):
    return {
        "profile_name": "default",
        "profile": profile_payload(**profile_changes),
        "expected_revision": 1,
    }


def test_status_and_config_are_strict_read_only_responses(api_client) -> None:
    client, service, store, worker = api_client
    before = service.get_configuration()
    status = client.get("/api/v1/status")
    config = client.get("/api/v1/config")

    assert status.status_code == 200
    assert status.json()["usb_vid"] == "2e3c"
    assert status.json()["device_available"] is False
    assert config.status_code == 200
    assert config.json()["configuration"]["active_profile"] == "default"
    assert service.get_configuration() == before
    assert store.saved == []
    assert worker.frames == []


def test_web_status_serializes_only_sanitized_user_service_state(
    api_client,
) -> None:
    _, service, store, worker = api_client
    app = create_app(
        service,
        service_status_provider=lambda: UserServiceStatus(
            runtime_mode="systemd_user_service",
            installed=True,
            managed=True,
            enabled=True,
            active=True,
            service_state="active",
            openlinkhub_recovery_enabled=True,
            pid=4321,
            last_failure_summary=None,
            configuration_path=Path("/private/config.json"),
        ),
    )
    response = LocalClient(app).get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_mode"] == "systemd_user_service"
    assert body["service_installed"] is True
    assert body["service_enabled"] is True
    assert body["service_active"] is True
    assert body["service_state"] == "active"
    assert body["openlinkhub_recovery_enabled"] is True
    serialized = json.dumps(body)
    assert "4321" not in serialized
    assert "/private" not in serialized
    assert store.saved == []
    assert worker.frames == []


def test_web_status_preserves_manual_runtime_when_systemd_is_unavailable(
    api_client,
) -> None:
    _, service, _, _ = api_client
    response = LocalClient(create_app(
        service,
        runtime_mode="manual",
    )).get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "manual"
    assert response.json()["service_active"] is None
    assert response.json()["openlinkhub_recovery_enabled"] is None


def test_canonical_configuration_and_preview_are_schema_v2(api_client) -> None:
    client, _, store, worker = api_client

    config = client.get("/api/v1/free-layout/config")
    preview = client.get("/api/v1/preview")

    assert config.status_code == 200
    assert config.json()["configuration"]["schema_version"] == 2
    assert config.json()["migration_pending"] is False
    assert config.json()["configuration"] == (
        legacy_equivalent_configuration_v2().to_dict()
    )
    assert config.json()["legacy_editor_supported"] is True
    assert preview.status_code == 200
    assert preview.json()["update_policy"] == "continuous"
    assert preview.json()["animated_content"] is True
    assert store.saved == []
    assert worker.frames == []


def test_migration_pending_configuration_saves_without_apply(
    api_client,
) -> None:
    client, service, store, worker = api_client
    service._migration_pending = True
    service.state.update(configuration_warning=LEGACY_MIGRATION_WARNING)
    before = service.get_configuration()

    config = client.get("/api/v1/free-layout/config")
    saved = client.put(
        "/api/v1/free-layout/config",
        json={
            "configuration": config.json()["configuration"],
            "expected_active_revision": before.active_revision,
            "expected_saved_revision": before.saved_revision,
            "apply": False,
        },
    )

    assert config.status_code == 200
    assert config.json()["dirty"] is False
    assert config.json()["migration_pending"] is True
    assert saved.status_code == 200
    assert saved.json()["configuration"]["schema_version"] == 2
    assert saved.json()["active_revision"] == before.active_revision
    assert saved.json()["saved_revision"] == 2
    assert saved.json()["dirty"] is False
    assert saved.json()["migration_pending"] is False
    assert service.get_configuration().migration_pending is False
    assert service.snapshot().configuration_warning is None
    assert len(store.saved) == 1
    assert worker.frames == []


def test_api_migration_save_writes_schema_v2_and_backup_atomically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nextuxlinq" / "config.json"
    path.parent.mkdir()
    original = json.dumps(
        default_configuration().to_dict(),
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(original)
    store = ConfigStore(path)
    loaded = store.load()
    worker = FakeWorker()
    service = NexTuxLinqService(
        config_store=store,
        device_worker=worker,  # type: ignore[arg-type]
        monotonic=lambda: 10.0,
    )
    service._configuration = loaded.configuration
    service._saved_configuration = loaded.configuration
    service._saved_revision = 1
    service._saved_revision_counter = 1
    service._migration_pending = loaded.migration_pending
    service._started_at = 0.0
    service.state.update(
        lifecycle_state="running",
        rendering_state="rendering",
        active_configuration_revision=1,
        configuration_warning=loaded.warning,
    )
    client = LocalClient(create_app(service))
    before = client.get("/api/v1/free-layout/config").json()

    saved = client.put(
        "/api/v1/free-layout/config",
        json={
            "configuration": before["configuration"],
            "expected_active_revision": before["active_revision"],
            "expected_saved_revision": before["saved_revision"],
            "apply": False,
        },
    )

    assert before["migration_pending"] is True
    assert before["dirty"] is False
    assert saved.status_code == 200
    assert saved.json()["migration_pending"] is False
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == loaded.configuration.to_dict()
    assert persisted["schema_version"] == 2
    backups = list(path.parent.glob(".config.json.schema-v1.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert service.snapshot().configuration_warning is None
    assert service.get_configuration().configuration == loaded.configuration
    assert service.get_configuration().active_revision == 1
    assert worker.frames == []


def test_failed_migration_save_preserves_api_state(api_client) -> None:
    client, service, store, worker = api_client
    service._migration_pending = True
    service.state.update(configuration_warning=LEGACY_MIGRATION_WARNING)
    store.error = ConfigSaveError("test-only secret", committed=False)
    before = service.get_configuration()

    response = client.put(
        "/api/v1/free-layout/config",
        json={
            "configuration": before.configuration.to_dict(),
            "expected_active_revision": before.active_revision,
            "expected_saved_revision": before.saved_revision,
            "apply": False,
        },
    )

    assert response.status_code == 500
    assert "test-only secret" not in str(response.json())
    after = service.get_configuration()
    assert after.configuration == before.configuration
    assert after.active_revision == before.active_revision
    assert after.saved_revision == before.saved_revision
    assert after.migration_pending is True
    assert service.snapshot().configuration_warning == LEGACY_MIGRATION_WARNING
    assert store.saved == []
    assert worker.frames == []


def test_free_layout_metadata_exposes_canonical_widget_defaults(
    api_client,
) -> None:
    client, service, store, worker = api_client
    before = service.get_configuration()

    response = client.get("/api/v1/free-layout/metadata")

    assert response.status_code == 200
    body = response.json()
    assert body["canvas_width"] == 90
    assert body["canvas_height"] == 10
    assert body["max_profiles"] == 32
    assert body["max_regions_per_profile"] == 32
    assert body["max_regions_per_document"] == 128
    definitions = body["widget_definitions"]
    assert [definition["type"] for definition in definitions] == [
        "blank",
        "clock",
        "static_text",
        "scrolling_text",
        "date",
        "system_metric",
        "metric_bar",
        "built_in_icon",
    ]
    assert definitions[0]["default_settings"] == {}
    assert definitions[-1]["default_settings"] == {
        "icon": "cachyos",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
    }
    assert body["built_in_icon_definitions"] == [{
        "id": "cachyos",
        "label": "CachyOS Logo",
        "width": 15,
        "height": 10,
        "fixed_color": "#1DC7B5",
        "fixed_color_label": "CachyOS Turquoise",
        "effect_eligible": False,
    }]
    assert body["built_in_icon_horizontal_alignments"] == [
        {"id": "start", "label": "Start"},
        {"id": "center", "label": "Center"},
        {"id": "end", "label": "End"},
    ]
    assert body["built_in_icon_vertical_alignments"] == [
        {"id": "top", "label": "Top"},
        {"id": "center", "label": "Center"},
        {"id": "bottom", "label": "Bottom"},
    ]
    assert body["font_definitions"] == [
        {
            "id": "auto", "label": "Auto — largest fitting",
            "glyph_height": None, "maximum_glyph_width": None,
        },
        {"id": "5x10", "label": "5×10", "glyph_height": 10,
         "maximum_glyph_width": 5},
        {"id": "5x9", "label": "5×9", "glyph_height": 9,
         "maximum_glyph_width": 5},
        {"id": "4x9", "label": "4×9", "glyph_height": 9,
         "maximum_glyph_width": 4},
        {"id": "5x7", "label": "5×7", "glyph_height": 7,
         "maximum_glyph_width": 5},
        {"id": "3x7", "label": "3×7", "glyph_height": 7,
         "maximum_glyph_width": 3},
        {"id": "3x5", "label": "3×5", "glyph_height": 5,
         "maximum_glyph_width": 3},
    ]
    assert body["bar_metric_ids"] == [
        "cpu_usage", "memory_usage", "cpu_temperature", "storage_usage",
        "gpu_usage", "gpu_temperature", "gpu_memory_usage",
        "storage_temperature",
    ]
    assert body["temperature_unit_definitions"] == [
        {"id": "degree_only", "label": "Degree only (°)", "suffix": "°"},
        {"id": "celsius", "label": "Celsius (°C)", "suffix": "°C"},
        {"id": "fahrenheit", "label": "Fahrenheit (°F)", "suffix": "°F"},
    ]
    assert body["display_effect"] == {
        "modes": [
            {"id": "none", "label": "Off"},
            {"id": "foreground_rainbow", "label": "Foreground Rainbow"},
        ],
        "directions": [
            {"id": "rear_to_front", "label": "Rear → Front"},
            {"id": "front_to_rear", "label": "Front → Rear"},
        ],
        "speed_minimum": 0.0,
        "speed_maximum": 2.0,
        "speed_step": 0.01,
        "default_mode": "none",
        "default_direction": "rear_to_front",
        "default_speed": 0.1,
    }
    metric_labels = {
        metric["id"]: metric["label"] for metric in body["metric_definitions"]
    }
    assert metric_labels == {
        "cpu_usage": "CPU Usage",
        "memory_usage": "RAM Usage",
        "load_average_1m": "System Load",
        "uptime": "Uptime",
        "cpu_temperature": "CPU Temperature",
        "gpu_temperature": "GPU Temperature",
        "storage_temperature": "Storage Temperature",
        "storage_usage": "Storage Usage",
        "network_receive_rate": "Network Receive",
        "network_transmit_rate": "Network Transmit",
        "gpu_usage": "GPU Usage",
        "gpu_memory_usage": "VRAM Usage",
    }
    assert len(set(metric_labels.values())) == len(metric_labels)
    assert metric_labels["cpu_usage"] != metric_labels["cpu_temperature"]
    assert metric_labels["gpu_usage"] != metric_labels["gpu_temperature"]
    assert metric_labels["storage_usage"] != metric_labels["storage_temperature"]
    assert [metric["id"] for metric in body["metric_definitions"]] == [
        "cpu_usage", "memory_usage", "load_average_1m", "uptime",
        "cpu_temperature", "gpu_temperature", "storage_temperature",
        "storage_usage", "network_receive_rate", "network_transmit_rate",
        "gpu_usage", "gpu_memory_usage",
    ]
    assert [metric["group"] for metric in body["metric_definitions"]] == [
        "System", "System", "System", "System",
        "Temperatures", "Temperatures", "Temperatures",
        "Storage", "Network", "Network", "GPU", "GPU",
    ]
    assert {
        metric["id"]: metric["default_label"]
        for metric in body["metric_definitions"]
    } == {
        "cpu_usage": "CPU", "memory_usage": "RAM",
        "load_average_1m": "LOAD", "uptime": "UPTIME",
        "cpu_temperature": "CPU", "gpu_temperature": "GPU",
        "storage_temperature": "DISK", "storage_usage": "DISK",
        "network_receive_rate": "NET RX",
        "network_transmit_rate": "NET TX", "gpu_usage": "GPU",
        "gpu_memory_usage": "VRAM",
    }
    assert all("path" not in metric for metric in body["metric_definitions"])
    assert all("availability_reason" in metric for metric in body["metric_definitions"])
    assert {
        metric["availability_reason"] for metric in body["metric_definitions"]
    } <= {
        None,
        "source_not_present",
        "awaiting_initial_sample",
        "malformed_source",
        "ambiguous_source",
        "unsupported_source",
        "temporary_read_failure",
    }
    assert definitions[1]["default_settings"] == {
        "format": "24h",
        "show_seconds": False,
        "blink_separator": False,
        "time_zone": "local",
        "color": "#FFFFFF",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "font": "auto",
    }
    assert definitions[2]["default_settings"]["content"] == "TEXT"
    assert definitions[3]["default_settings"]["direction"] == "left"
    assert definitions[3]["default_settings"]["speed"] == 5
    assert definitions[3]["default_settings"]["trailing_gap"] == 6
    assert definitions[4]["default_settings"]["format"] == (
        "day_month_full_year"
    )
    assert service.get_configuration() == before
    assert store.saved == []
    assert worker.frames == []


def test_widget_content_preview_is_authoritative_and_side_effect_free(api_client) -> None:
    client, service, store, worker = api_client
    before = service.get_configuration()
    saved_before = list(store.saved)
    frames_before = list(worker.frames)

    response = client.post("/api/v1/free-layout/content-preview", json={
        "widget": {
            "type": "built_in_icon",
            "settings": {
                "icon": "cachyos",
                "horizontal_alignment": "center",
                "vertical_alignment": "center",
            },
        },
        "region_background": "#102030",
        "canvas_background": "#000000",
        "effect": {
            "mode": "foreground_rainbow",
            "direction": "rear_to_front",
            "speed": 0.1,
        },
        "current_width": 10,
        "current_height": 5,
    })

    assert response.status_code == 200
    body = response.json()
    assert (body["preview_width"], body["preview_height"]) == (15, 10)
    assert (body["minimum_width"], body["minimum_height"]) == (15, 10)
    assert body["fit_state"] == "current_region_too_small"
    assert body["current_output"] == "CachyOS Logo"
    assert body["resolved_font"] is None
    assert service.get_configuration() == before
    assert store.saved == saved_before
    assert worker.frames == frames_before


def test_widget_content_preview_rejects_unknown_icon_safely(api_client) -> None:
    client, service, store, worker = api_client
    response = client.post("/api/v1/free-layout/content-preview", json={
        "widget": {
            "type": "built_in_icon",
            "settings": {
                "icon": "PRIVATE_ICON_PATH",
                "horizontal_alignment": "center",
                "vertical_alignment": "center",
            },
        },
        "current_width": 15,
        "current_height": 10,
    })
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_icon"
    assert "PRIVATE_ICON_PATH" not in str(response.json())
    assert store.saved == []
    assert worker.frames == []


def test_metric_diagnostics_response_is_sanitized_and_read_only(api_client) -> None:
    client, service, store, worker = api_client
    availability = {
        metric: MetricAvailability.UNAVAILABLE for metric in MetricId
    }
    availability[MetricId.CPU_USAGE] = MetricAvailability.AVAILABLE
    availability[MetricId.NETWORK_RECEIVE_RATE] = (
        MetricAvailability.AWAITING_INITIAL_DELTA
    )
    availability[MetricId.NETWORK_TRANSMIT_RATE] = (
        MetricAvailability.AWAITING_INITIAL_DELTA
    )
    service._last_metrics_snapshot = SystemMetricsSnapshot(
        5.0,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        {MetricId.CPU_USAGE: 42.0},
        availability,
        "partial",
        {
            metric: (
                None if metric is MetricId.CPU_USAGE
                else MetricReason.AWAITING_INITIAL_SAMPLE
                if metric in {
                    MetricId.NETWORK_RECEIVE_RATE,
                    MetricId.NETWORK_TRANSMIT_RATE,
                }
                else MetricReason.SOURCE_NOT_PRESENT
            )
            for metric in MetricId
        },
    )

    response = client.get("/api/v1/free-layout/metrics")

    assert response.status_code == 200
    metrics = {item["id"]: item for item in response.json()["metrics"]}
    assert metrics["cpu_usage"]["state"] == "available"
    assert metrics["cpu_usage"]["reason"] is None
    assert metrics["network_receive_rate"] == {
        "id": "network_receive_rate",
        "label": "Network Receive",
        "state": "awaiting_initial_delta",
        "reason": "awaiting_initial_sample",
    }
    assert len({item["label"] for item in metrics.values()}) == len(metrics)
    serialized = response.text
    for forbidden in ("/proc", "/sys", "0000:", "serial", "traceback"):
        assert forbidden not in serialized
    assert store.saved == []
    assert worker.frames == []


@pytest.mark.parametrize(
    ("preset_id", "region_index", "setting", "value", "category", "field"),
    [
        ("system-monitor", 0, "metric", "fan_usage", "unknown_metric", "metric"),
        ("system-monitor", 0, "precision", 3, "invalid_precision", "precision"),
        ("system-monitor", 0, "label", "", "invalid_custom_label", "label"),
        ("system-bars", 0, "metric", "uptime", "unsupported_metric_for_bar", "metric"),
        ("system-bars", 0, "maximum", 0, "invalid_bar_range", "maximum"),
        ("system-bars", 0, "orientation", "diagonal", "invalid_bar_orientation", "orientation"),
        ("system-bars", 0, "direction", "reverse", "invalid_bar_direction", "direction"),
    ],
)
def test_metric_configuration_errors_are_sanitized_and_field_addressable(
    api_client,
    preset_id: str,
    region_index: int,
    setting: str,
    value: object,
    category: str,
    field: str,
) -> None:
    client, service, store, worker = api_client
    profile = next(
        preset.profile.to_dict() for preset in get_builtin_presets()
        if preset.id == preset_id
    )
    profile["regions"][region_index]["widget"]["settings"][setting] = value
    response = client.post(
        "/api/v1/free-layout/validate",
        json={
            "configuration": {
                "schema_version": 2,
                "active_profile": "metrics",
                "profiles": {"metrics": profile},
            }
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == category
    assert body["field_errors"][0]["field"].endswith(field)
    serialized = json.dumps(body)
    assert "gpu_usage" not in serialized
    assert "diagonal" not in serialized
    assert "reverse" not in serialized


@pytest.mark.parametrize(
    ("effect", "category", "field"),
    [
        (None, "unknown_display_effect", "effect.mode"),
        ({"mode": "future"}, "unknown_display_effect", "effect.mode"),
        (
            {"mode": "foreground_rainbow", "direction": "side", "speed": 0.1},
            "invalid_effect_direction",
            "effect.direction",
        ),
        (
            {
                "mode": "foreground_rainbow",
                "direction": "rear_to_front",
                "speed": 2.1,
            },
            "invalid_effect_speed",
            "effect.speed",
        ),
    ],
)
def test_display_effect_errors_are_sanitized_and_field_addressable(
    api_client, effect: object, category: str, field: str
) -> None:
    client, service, store, worker = api_client
    profile = legacy_equivalent_configuration_v2().active.to_dict()
    profile["effect"] = effect
    response = client.post(
        "/api/v1/free-layout/validate",
        json={
            "configuration": {
                "schema_version": 2,
                "active_profile": "effect",
                "profiles": {"effect": profile},
            }
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == category
    assert body["field_errors"][0]["field"].endswith(field)
    serialized = json.dumps(body)
    assert "future" not in serialized
    assert "side" not in serialized
    assert "traceback" not in serialized.lower()
    assert store.saved == []
    assert worker.frames == []


def test_canonical_validation_uses_real_compositor_without_side_effects(
    api_client,
) -> None:
    client, service, store, worker = api_client
    before = service.get_configuration()
    candidate = legacy_equivalent_configuration_v2().to_dict()

    response = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["update_policy"] == "continuous"
    assert len(base64.b64decode(response.json()["framebuffer_base64"])) == (
        90 * 10 * 3
    )
    assert service.get_configuration() == before
    assert store.saved == []
    assert worker.frames == []


def test_clock_validation_reports_largest_resolved_font_and_option_reasons(
    api_client,
) -> None:
    client, service, store, worker = api_client
    candidate = legacy_equivalent_configuration_v2().to_dict()
    clock = candidate["profiles"]["default"]["regions"][0]
    clock["x"] = 0
    clock["width"] = 25
    clock["widget"]["settings"]["font"] = "auto"
    candidate["profiles"]["default"]["regions"] = [clock]

    fitting = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )

    assert fitting.status_code == 200
    assert fitting.json()["resolved_fonts"] == {"legacy-front-clock": "5x10"}
    fit = fitting.json()["font_fit"]["legacy-front-clock"]
    assert fit["resolved_auto_font"] == "5x10"
    assert fit["largest_fitting_font"] == "5x10"
    assert fit["smallest_fitting_font"] == "3x5"
    assert fit["no_font_fits"] is False
    statuses = {
        item["id"]: item
        for item in fitting.json()["font_options"]["legacy-front-clock"]
    }
    assert statuses["5x10"]["available"] is True

    clock["width"] = 24
    narrower = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )

    assert narrower.status_code == 200
    assert narrower.json()["resolved_fonts"] == {"legacy-front-clock": "4x9"}
    assert narrower.json()["font_fit"]["legacy-front-clock"][
        "resolved_auto_font"
    ] == "4x9"
    statuses = {
        item["id"]: item
        for item in narrower.json()["font_options"]["legacy-front-clock"]
    }
    assert statuses["5x10"] == {
        "id": "5x10",
        "available": False,
        "category": "content_too_wide",
        "minimum_width": 25,
        "minimum_height": 10,
    }

    clock["widget"]["settings"]["font"] = "5x10"
    explicit_failure = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )
    assert explicit_failure.status_code == 422
    explicit_error = explicit_failure.json()["field_errors"][0]
    assert {key: value for key, value in explicit_error.items() if key != "font_fit"} == {
        "field": "regions.legacy-front-clock.content",
        "message": "The widget settings cannot be rendered.",
        "region_id": "legacy-front-clock",
        "widget_type": "clock",
        "category": "content_too_wide",
        "renderer_field": "content",
        "actual_width": 24,
        "actual_height": 10,
        "minimum_width": 25,
        "minimum_height": 10,
    }
    assert explicit_error["font_fit"]["selected_explicit_font"] == "5x10"
    assert explicit_error["font_fit"]["best_smaller_alternative"] == "4x9"
    assert explicit_error["font_fit"]["candidates"][0] == {
        "font_id": "5x10",
        "display_label": "5×10",
        "glyph_width": 5,
        "glyph_height": 10,
        "compatible_with_widget": True,
        "complete_glyph_coverage": True,
        "fits_height": True,
        "fits_width": False,
        "overall_fit": False,
        "minimum_width": 25,
        "minimum_height": 10,
        "rejection_category": "too_wide",
    }
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_validation_distinguishes_no_fitting_font_from_no_supported_font(
    api_client,
) -> None:
    client, service, store, worker = api_client
    candidate = legacy_equivalent_configuration_v2().to_dict()
    profile = candidate["profiles"]["default"]
    clock = profile["regions"][0]
    clock["x"] = 0
    clock["width"] = 16
    clock["widget"]["settings"]["font"] = "auto"
    profile["regions"] = [clock]

    no_fit = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )
    assert no_fit.status_code == 422
    fit_error = no_fit.json()["field_errors"][0]
    assert fit_error["font_fit"]["no_font_fits"] is True
    assert fit_error["font_fit"]["no_font_supports_content"] is False
    smallest = fit_error["font_fit"]["candidates"][-1]
    assert (smallest["font_id"], smallest["minimum_width"],
            smallest["minimum_height"]) == ("3x5", 17, 5)

    static_definition = next(
        item for item in service.get_widget_definitions()
        if item.widget_type.value == "static_text"
    )
    clock["widget"] = {
        "type": "static_text",
        "settings": {
            **static_definition.default_settings.to_dict(),
            "content": "PRIVATE Ä CONTENT",
            "font": "auto",
        },
    }
    clock["width"] = 90
    unsupported = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )
    assert unsupported.status_code == 422
    unsupported_error = unsupported.json()["field_errors"][0]
    assert unsupported_error["font_fit"]["no_font_supports_content"] is True
    assert "minimum_width" not in unsupported_error
    serialized = json.dumps(unsupported.json())
    assert "PRIVATE Ä CONTENT" not in serialized
    assert store.saved == []
    assert worker.frames == []


def test_canonical_validation_rejects_geometry_and_renderer_errors(
    api_client,
) -> None:
    client, service, store, worker = api_client
    overlap = legacy_equivalent_configuration_v2().to_dict()
    overlap["profiles"]["default"]["regions"][1]["x"] = 20
    invalid_geometry = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": overlap},
    )

    unsupported = legacy_equivalent_configuration_v2().to_dict()
    unsupported["profiles"]["default"]["regions"][1]["widget"]["settings"][
        "content"
    ] = "SECRET \u2603"
    invalid_renderer = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": unsupported},
    )

    assert invalid_geometry.status_code == 422
    assert invalid_geometry.json()["code"] == "validation_failed"
    assert invalid_renderer.status_code == 422
    body = invalid_renderer.json()
    assert body["code"] == "candidate_rendering_failed"
    assert body["field_errors"][0]["category"] == "unsupported_glyph"
    assert body["field_errors"][0]["region_id"] == "legacy-scrolling-text"
    assert "SECRET" not in str(body)
    assert "\u2603" not in str(body)
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_canonical_validation_rejects_strict_region_and_widget_shapes(
    api_client,
) -> None:
    client, service, store, worker = api_client
    candidates: list[dict[str, object]] = []

    duplicate = legacy_equivalent_configuration_v2().to_dict()
    duplicate["profiles"]["default"]["regions"][1]["id"] = (
        duplicate["profiles"]["default"]["regions"][0]["id"]
    )
    candidates.append(duplicate)

    out_of_bounds = legacy_equivalent_configuration_v2().to_dict()
    out_of_bounds["profiles"]["default"]["regions"][2]["x"] = 90
    candidates.append(out_of_bounds)

    invalid_size = legacy_equivalent_configuration_v2().to_dict()
    invalid_size["profiles"]["default"]["regions"][0]["width"] = 0
    candidates.append(invalid_size)

    unknown_widget = legacy_equivalent_configuration_v2().to_dict()
    unknown_widget["profiles"]["default"]["regions"][0]["widget"]["type"] = (
        "arbitrary"
    )
    candidates.append(unknown_widget)

    wrong_settings = legacy_equivalent_configuration_v2().to_dict()
    wrong_settings["profiles"]["default"]["regions"][0]["widget"][
        "settings"
    ] = {}
    candidates.append(wrong_settings)

    for candidate in candidates:
        response = client.post(
            "/api/v1/free-layout/validate",
            json={"configuration": candidate},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"

    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


@pytest.mark.parametrize(
    ("change", "category", "field", "prohibited"),
    [
        (
            {"time_zone": "Phase4/UnavailableSecret"},
            "time_zone_unavailable",
            "time_zone",
            "Phase4/UnavailableSecret",
        ),
        (
            {"font": "5x7"},
            "content_too_wide",
            "content",
            "FontTextError",
        ),
    ],
)
def test_canonical_renderer_errors_are_sanitized_and_addressable(
    api_client,
    change: dict[str, object],
    category: str,
    field: str,
    prohibited: str,
) -> None:
    client, service, store, worker = api_client
    candidate = legacy_equivalent_configuration_v2().to_dict()
    front = candidate["profiles"]["default"]["regions"][0]
    if category == "content_too_wide":
        front["width"] = 13
    front["widget"]["settings"].update(change)

    response = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "candidate_rendering_failed"
    assert body["field_errors"][0]["category"] == category
    assert body["field_errors"][0]["field"].endswith(f".{field}")
    assert prohibited not in str(body)
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_candidate_error_exposes_only_safe_authoritative_geometry_metadata(
    api_client,
) -> None:
    client, service, store, worker = api_client
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    class Provider:
        def sample(self) -> SystemMetricsSnapshot:
            return SystemMetricsSnapshot.unavailable(10.0, now)

    service._metrics_provider = Provider()
    candidate = legacy_equivalent_configuration_v2().to_dict()
    profile = candidate["profiles"]["default"]
    profile["regions"] = [{
        "id": "metric", "name": "Metric",
        "x": 0, "y": 0, "width": 34, "height": 10,
        "enabled": True, "background_color": None,
        "widget": {
            "type": "system_metric",
            "settings": SystemMetricSettings(
                font="5x10",  # type: ignore[arg-type]
            ).to_dict(),
        },
    }]

    response = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )

    assert response.status_code == 422
    error = response.json()["field_errors"][0]
    assert {key: value for key, value in error.items() if key != "font_fit"} == {
        "field": "regions.metric.content",
        "message": "The widget settings cannot be rendered.",
        "region_id": "metric",
        "widget_type": "system_metric",
        "category": "content_too_wide",
        "renderer_field": "content",
        "actual_width": 34,
        "actual_height": 10,
        "minimum_width": 35,
        "minimum_height": 10,
    }
    assert error["font_fit"]["selected_explicit_font"] == "5x10"
    assert error["font_fit"]["best_smaller_alternative"] == "4x9"
    serialized = json.dumps(response.json())
    assert "/proc" not in serialized
    assert "/sys" not in serialized
    assert "FontTextError" not in serialized
    assert store.saved == []
    assert worker.frames == []


def test_candidate_validation_reports_each_invalid_renderer_region(
    api_client,
) -> None:
    client, service, store, worker = api_client
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    class Provider:
        calls = 0

        def sample(self) -> SystemMetricsSnapshot:
            self.calls += 1
            return SystemMetricsSnapshot.unavailable(10.0, now)

    provider = Provider()
    service._metrics_provider = provider
    candidate = legacy_equivalent_configuration_v2().to_dict()
    settings = SystemMetricSettings(
        font="5x10",  # type: ignore[arg-type]
    ).to_dict()
    candidate["profiles"]["default"]["regions"] = [
        {
            "id": region_id, "name": None,
            "x": x, "y": 0, "width": 34, "height": 10,
            "enabled": True, "background_color": None,
            "widget": {"type": "system_metric", "settings": settings},
        }
        for region_id, x in (("metric-a", 0), ("metric-b", 50))
    ]

    response = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )

    assert response.status_code == 422
    errors = response.json()["field_errors"]
    assert [error["region_id"] for error in errors] == ["metric-a", "metric-b"]
    assert all(error["minimum_width"] == 35 for error in errors)
    assert provider.calls == 1
    assert store.saved == []
    assert worker.frames == []


def test_temperature_unit_updates_candidate_validation_width(api_client) -> None:
    client, service, store, worker = api_client
    candidate = legacy_equivalent_configuration_v2().to_dict()
    settings = SystemMetricSettings(
        metric="cpu_temperature",
        label="CPU",
        temperature_unit="celsius",
    ).to_dict()  # type: ignore[arg-type]
    candidate["profiles"]["default"]["regions"] = [{
        "id": "temperature", "name": "Temperature",
        "x": 0, "y": 0, "width": 25, "height": 5,
        "enabled": True, "background_color": None,
        "widget": {"type": "system_metric", "settings": settings},
    }]

    celsius = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )
    assert celsius.status_code == 200
    assert celsius.json()["resolved_fonts"] == {"temperature": "3x5"}

    settings["temperature_unit"] = "degree_only"
    degree_only = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )
    assert degree_only.status_code == 200
    compact = next(
        item for item in degree_only.json()["font_fit"]["temperature"]["candidates"]
        if item["font_id"] == "3x5"
    )
    assert compact["minimum_width"] == 22

    settings["temperature_unit"] = "fahrenheit"
    fahrenheit = client.post(
        "/api/v1/free-layout/validate",
        json={"configuration": candidate},
    )
    assert fahrenheit.status_code == 422
    error = fahrenheit.json()["field_errors"][0]
    assert error["category"] == "content_too_wide"
    assert error["minimum_width"] == 29
    assert error["font_fit"]["no_font_fits"] is True
    assert store.saved == []
    assert worker.frames == []


def test_canonical_apply_and_save_preserve_revision_workflow(api_client) -> None:
    client, service, store, worker = api_client
    candidate = legacy_equivalent_configuration_v2().to_dict()
    candidate["profiles"]["default"]["regions"][1]["widget"]["settings"][
        "content"
    ] = "PHASE FOUR"

    applied = client.post(
        "/api/v1/free-layout/apply",
        json={"configuration": candidate, "expected_revision": 1},
    )
    saved = client.put(
        "/api/v1/free-layout/config",
        json={
            "configuration": candidate,
            "expected_active_revision": 2,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )

    assert applied.status_code == 200
    assert applied.json()["active_revision"] == 2
    assert applied.json()["dirty"] is True
    assert saved.status_code == 200
    assert saved.json()["active_revision"] == 2
    assert saved.json()["saved_revision"] == 2
    assert saved.json()["dirty"] is False
    assert len(worker.frames) == 1
    assert len(store.saved) == 1
    assert service.configuration.to_dict() == candidate


def test_legacy_editor_is_read_only_for_arbitrary_free_layout(
    api_client,
) -> None:
    client, service, store, worker = api_client
    free_layout = ConfigurationDocumentV2(
        schema_version=2,
        active_profile="default",
        profiles={"default": empty_profile_v2()},
    )
    service._configuration = free_layout
    service._saved_configuration = free_layout

    config = client.get("/api/v1/config")
    mutation = client.post("/api/v1/apply", json=apply_payload())

    assert config.status_code == 200
    assert config.json()["configuration"] is None
    assert config.json()["legacy_editor_supported"] is False
    assert config.json()["canonical_configuration"] == free_layout.to_dict()
    assert mutation.status_code == 409
    assert mutation.json()["code"] == "legacy_editor_unsupported"
    assert service.configuration == free_layout
    assert store.saved == []
    assert worker.frames == []


def test_apply_normalizes_without_persisting_and_queues_brightness(api_client) -> None:
    client, service, store, worker = api_client
    response = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="matrix 900!", brightness=70),
    )

    assert response.status_code == 200
    assert response.json()["profile"]["scrolling_text"] == "MATRIX 900!"
    assert response.json()["active_revision"] == 2
    assert store.saved == []
    assert worker.frames[-1][1] == 2
    assert worker.brightness == [(70, 2)]
    assert service.configuration.active.scrolling_text == "MATRIX 900!"


def test_apply_with_null_brightness_does_not_write_brightness(api_client) -> None:
    client, _, _, worker = api_client
    response = client.post("/api/v1/apply", json=apply_payload(brightness=None))
    assert response.status_code == 200
    assert worker.brightness == []


def test_save_persists_and_applies(api_client) -> None:
    client, service, store, worker = api_client
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "saved"
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": document,
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["configuration"]["profiles"]["default"][
        "scrolling_text"
    ] == "SAVED"
    assert response.json()["dirty"] is False
    assert len(store.saved) == 1
    assert service.configuration.active.scrolling_text == "SAVED"
    assert worker.frames[-1][1] == 2


def test_apply_false_uses_independent_saved_revisions(api_client) -> None:
    client, service, store, _ = api_client
    first = default_configuration().to_dict()
    first["profiles"]["default"]["scrolling_text"] = "FIRST"
    response_one = client.put(
        "/api/v1/config",
        json={
            "configuration": first,
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )
    second = default_configuration().to_dict()
    second["profiles"]["default"]["scrolling_text"] = "SECOND"
    response_two = client.put(
        "/api/v1/config",
        json={
            "configuration": second,
            "expected_active_revision": 1,
            "expected_saved_revision": 2,
            "apply": False,
        },
    )

    assert response_one.status_code == response_two.status_code == 200
    assert response_one.json()["active_revision"] == 1
    assert response_one.json()["saved_revision"] == 2
    assert response_two.json()["active_revision"] == 1
    assert response_two.json()["saved_revision"] == 3
    assert response_two.json()["dirty"] is True
    assert service.configuration.active.scrolling_text == "CACHYOS"
    assert [item.active.scrolling_text for item in store.saved] == [
        "FIRST",
        "SECOND",
    ]

    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="SECOND"),
    )
    assert applied.status_code == 200
    assert applied.json()["active_revision"] == 2
    assert applied.json()["dirty"] is False
    assert client.get("/api/v1/config").json()["saved_revision"] == 3


def test_apply_then_save_without_apply_preserves_active_revision(api_client) -> None:
    client, service, _, _ = api_client
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="RUNTIME"),
    )
    assert applied.status_code == 200
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "DISK"
    saved = client.put(
        "/api/v1/config",
        json={
            "configuration": document,
            "expected_active_revision": 2,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["active_revision"] == 2
    assert saved.json()["saved_revision"] == 2
    assert saved.json()["dirty"] is True
    assert service.configuration.active.scrolling_text == "RUNTIME"


@pytest.mark.parametrize(
    ("active_revision", "saved_revision", "apply"),
    [
        (0, 1, True),
        (1, 0, True),
        (0, 0, True),
        (0, 0, False),
    ],
)
def test_save_rejects_stale_required_revisions(
    api_client,
    active_revision: int,
    saved_revision: int,
    apply: bool,
) -> None:
    client, service, store, worker = api_client
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": default_configuration().to_dict(),
            "expected_active_revision": active_revision,
            "expected_saved_revision": saved_revision,
            "apply": apply,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_apply_false_ignores_active_revision_but_checks_saved(api_client) -> None:
    client, service, store, _ = api_client
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="NEW RUNTIME"),
    )
    assert applied.status_code == 200
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "DISK ONLY"

    saved = client.put(
        "/api/v1/config",
        json={
            "configuration": document,
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": False,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["active_revision"] == 2
    assert saved.json()["saved_revision"] == 2
    assert saved.json()["dirty"] is True
    assert service.configuration.active.scrolling_text == "NEW RUNTIME"
    assert store.saved[-1].active.scrolling_text == "DISK ONLY"


def test_stale_browser_cannot_save_over_newer_runtime_apply(api_client) -> None:
    client, service, store, _ = api_client
    initial = client.get("/api/v1/config").json()
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(scrolling_text="NEWER"),
    )
    assert applied.status_code == 200

    stale_save = client.put(
        "/api/v1/config",
        json={
            "configuration": initial["configuration"],
            "expected_active_revision": initial["active_revision"],
            "expected_saved_revision": initial["saved_revision"],
            "apply": True,
        },
    )

    assert stale_save.status_code == 409
    assert service.configuration.active.scrolling_text == "NEWER"
    assert store.saved == []


def test_revision_conflict_preserves_state(api_client) -> None:
    client, service, store, worker = api_client
    response = client.post(
        "/api/v1/apply",
        json={**apply_payload(), "expected_revision": 0},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert service.snapshot().active_configuration_revision == 1
    assert store.saved == []
    assert worker.frames == []


def test_concurrent_apply_requests_are_revision_serialized(tmp_path: Path) -> None:
    service, _, _ = service_for_api(tmp_path)

    async def scenario():
        return await asyncio.gather(
            service.apply_profile(
                "default",
                default_configuration().active,
                expected_revision=1,
            ),
            service.apply_profile(
                "default",
                default_configuration().active,
                expected_revision=1,
            ),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, RevisionConflictError) for result in results) == 1
    assert service.snapshot().active_configuration_revision == 2


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"scrolling_text": ""}, "scrolling_text"),
        ({"scrolling_text": "SNOWMAN ☃"}, "unsupported"),
        ({"text_color": "turquoise"}, "text_color"),
        ({"scroll_speed": 100}, "scroll_speed"),
        ({"brightness": 55}, "brightness"),
        (
            {
                "layout": {
                    "front_clock_width": 23,
                    "gap_width": 4,
                    "scroll_text_width": 31,
                    "rear_clock_width": 31,
                }
            },
            "width",
        ),
    ],
)
def test_domain_validation_errors_are_422(api_client, changes, field) -> None:
    client, service, _, _ = api_client
    response = client.post("/api/v1/apply", json=apply_payload(**changes))
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert response.json()["message"] == (
        "The request contains invalid configuration data."
    )
    assert str(changes) not in response.json()["message"]
    assert service.snapshot().active_configuration_revision == 1


def test_request_parsing_and_security_failures(api_client) -> None:
    client, _, _, _ = api_client
    malformed = client.post(
        "/api/v1/apply",
        content="{",
        headers={"content-type": "application/json"},
    )
    wrong_type = client.post("/api/v1/apply", content="{}")
    unknown = client.post(
        "/api/v1/apply",
        json={**apply_payload(), "unknown": True},
    )
    oversized = client.post(
        "/api/v1/apply",
        content=b"x" * (MAX_REQUEST_BODY + 1),
        headers={"content-type": "application/json"},
    )

    assert malformed.status_code == 400
    assert wrong_type.status_code == 415
    assert unknown.status_code == 422
    assert oversized.status_code == 413
    for response in (malformed, wrong_type, unknown, oversized):
        assert response.json()["request_id"]


def test_host_origin_and_security_headers(api_client) -> None:
    client, _, _, _ = api_client
    invalid_host = client.get(
        "/api/v1/status",
        headers={"host": "matrix900.example"},
    )
    invalid_origin = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={"origin": "http://evil.example"},
    )
    same_origin = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={"origin": "http://127.0.0.1:8765"},
    )

    assert invalid_host.status_code == 403
    assert invalid_origin.status_code == 403
    assert same_origin.status_code == 200
    for response in (invalid_host, invalid_origin, same_origin):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        ("http://127.0.0.1:8765", 200),
        ("https://127.0.0.1:8765", 403),
        ("http://127.0.0.1:8765/path", 403),
        ("http://127.0.0.1:8765?query=yes", 403),
        ("http://127.0.0.1:8765#fragment", 403),
        ("http://user@127.0.0.1:8765", 403),
        ("null", 403),
        ("http://127.0.0.1:8765 http://127.0.0.1:8765", 403),
        ("http://127.0.0.1:9000", 403),
    ],
)
def test_origin_requires_exact_http_origin(api_client, origin: str, status: int) -> None:
    client, _, _, _ = api_client
    response = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={"origin": origin},
    )
    assert response.status_code == status


def test_bracketed_ipv6_origin_is_accepted(tmp_path: Path) -> None:
    service, _, _ = service_for_api(tmp_path)
    client = LocalClient(create_app(service))
    response = client.post(
        "/api/v1/apply",
        json=apply_payload(),
        headers={
            "host": "[::1]:8765",
            "origin": "http://[::1]:8765",
        },
    )
    assert response.status_code == 200


def test_multiple_origin_headers_are_rejected(api_client) -> None:
    client, _, _, _ = api_client

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://127.0.0.1",
        ) as async_client:
            return await async_client.post(
                "/api/v1/apply",
                json=apply_payload(),
                headers=[
                    ("host", "127.0.0.1"),
                    ("origin", "http://127.0.0.1:8765"),
                    ("origin", "http://127.0.0.1:8765"),
                ],
            )

    assert asyncio.run(send()).status_code == 403


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1", 200),
        ("127.0.0.1:8765", 200),
        ("127.0.0.1:invalid", 403),
        ("127.0.0.1:", 403),
        ("[::1]", 200),
        ("[::1]:8765", 200),
        ("[::1]:invalid", 403),
        ("[::1", 403),
        ("::1", 403),
        ("127.0.0.1:9000", 403),
        ("192.168.1.2:8765", 403),
        ("0.0.0.0:8765", 403),
        ("user@127.0.0.1:8765", 403),
    ],
)
def test_host_authority_is_strict(api_client, host: str, status: int) -> None:
    client, _, _, _ = api_client
    response = client.get("/api/v1/status", headers={"host": host})
    assert response.status_code == status


def test_api_404_405_and_internal_errors_use_stable_shape(api_client) -> None:
    client, service, _, _ = api_client
    not_found = client.get("/api/v1/missing")
    wrong_method = client.put(
        "/api/v1/status",
        json={},
    )
    original = service.get_configuration
    service.get_configuration = lambda: (_ for _ in ()).throw(
        RuntimeError("/private/internal/detail")
    )
    internal = client.get("/api/v1/status")
    service.get_configuration = original

    for response, code in (
        (not_found, "not_found"),
        (wrong_method, "method_not_allowed"),
        (internal, "internal_error"),
    ):
        assert response.json()["code"] == code
        assert response.json()["message"]
        assert response.json()["request_id"]
        assert response.json()["field_errors"] is None
        assert "/private/" not in response.text


@pytest.mark.parametrize(
    ("exception", "secret"),
    [
        (ValueError("/private/config.json"), "/private/config.json"),
        (TypeError("InternalWidget implementation"), "InternalWidget"),
    ],
)
def test_unexpected_value_and_type_errors_are_sanitized(
    api_client,
    exception: Exception,
    secret: str,
) -> None:
    client, service, _, _ = api_client
    original = service.get_configuration
    service.get_configuration = lambda: (_ for _ in ()).throw(exception)
    response = client.get("/api/v1/status")
    service.get_configuration = original

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.json()["message"] == "The request could not be completed."
    assert secret not in response.text


def test_preview_uses_physical_renderer_and_exact_regions(api_client) -> None:
    client, _, _, _ = api_client
    response = client.get("/api/v1/preview")
    payload = response.json()
    framebuffer = base64.b64decode(payload["framebuffer_base64"])

    assert response.status_code == 200
    assert (payload["width"], payload["height"]) == (90, 10)
    assert payload["pixel_format"] == "rgb888"
    assert payload["orientation"] == "physical-front-to-rear"
    assert len(framebuffer) == 2700
    assert [region["width"] for region in payload["regions"]] == [24, 4, 31, 31]
    assert [region["start_pixel"] for region in payload["regions"]] == [0, 24, 28, 59]
    assert payload["active_revision"] == 1


def test_preview_custom_colors_stay_in_physical_regions(api_client) -> None:
    client, _, _, _ = api_client
    applied = client.post(
        "/api/v1/apply",
        json=apply_payload(
            scrolling_text="L",
            text_color="#112233",
            clock_color="#445566",
            scroll_speed=0.5,
        ),
    )
    assert applied.status_code == 200
    payload = client.get("/api/v1/preview").json()
    frame = base64.b64decode(payload["framebuffer_base64"])

    def pixel(x: int, y: int) -> bytes:
        offset = (y * 90 + x) * 3
        return frame[offset : offset + 3]

    clock = bytes.fromhex("445566")
    text = bytes.fromhex("112233")
    assert any(pixel(x, y) == clock for x in range(24) for y in range(10))
    assert all(pixel(x, y) == b"\0\0\0" for x in range(24, 28) for y in range(10))
    assert any(pixel(x, y) == text for x in range(28, 59) for y in range(10))
    assert any(pixel(x, y) == clock for x in range(59, 90) for y in range(10))
    assert all(
        pixel(x, y) != text
        for x in list(range(28)) + list(range(59, 90))
        for y in range(10)
    )


def test_precommit_save_failure_changes_no_state(api_client) -> None:
    client, service, store, worker = api_client
    store.error = ConfigSaveError("private filename", committed=False)
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": default_configuration().to_dict(),
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": True,
        },
    )

    assert response.status_code == 500
    assert response.json()["message"] == "The configuration could not be saved."
    assert "private" not in response.text
    assert service.snapshot().active_configuration_revision == 1
    assert worker.frames == []


def test_postcommit_warning_is_truthful_and_publishes_state(api_client) -> None:
    client, service, store, _ = api_client
    store.error = ConfigSaveError("private directory", committed=True)
    response = client.put(
        "/api/v1/config",
        json={
            "configuration": default_configuration().to_dict(),
            "expected_active_revision": 1,
            "expected_saved_revision": 1,
            "apply": True,
        },
    )

    assert response.status_code == 200
    assert "durability" in response.json()["warning"]
    assert response.json()["saved_revision"] == 2
    assert service.snapshot().configuration_warning == response.json()["warning"]
    assert "private" not in response.text


def test_frontend_and_local_assets_are_self_contained(api_client) -> None:
    client, _, _, _ = api_client

    async def fetch_assets():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://127.0.0.1",
        ) as async_client:
            return (
                await async_client.get("/"),
                await async_client.get("/static/app.css"),
                await async_client.get("/static/app.js"),
                await async_client.get(
                    "/static/free_layout_editor.js"
                ),
            )

    page, css, javascript, editor = asyncio.run(fetch_assets())

    assert (
        page.status_code
        == css.status_code
        == javascript.status_code
        == editor.status_code
        == 200
    )
    assert "NexTuxLinq Matrix900 Controller" in page.text
    assert 'name="application-name"' in page.text
    assert 'aria-live="polite"' in page.text
    assert "A 90 by 10 pixel viewer-perspective preview" in page.text
    assert "Free-layout editor" in page.text
    assert "createEditor" in editor.text
    assert "https://" not in page.text
    assert "http://" not in javascript.text
    assert "https://" not in javascript.text
    assert "innerHTML" not in javascript.text
    assert "setInterval" not in javascript.text
    assert "setTimeoutFunction(pollStatus" in javascript.text
    assert "setTimeoutFunction(pollPreview" in javascript.text
    assert 'id="configuration-state"' in page.text
    assert 'id="multiple-device-warning"' in page.text
    assert 'id="region-labels"' in page.text
    assert "Discard draft" in page.text
    assert 'request("/config")' in javascript.text
    assert "reloadAppliedSettings" in javascript.text
    assert 'request("/config")' in editor.text
    assert "reconcileConfiguration" in editor.text
    assert 'windowObject.addEventListener("beforeunload"' in javascript.text
