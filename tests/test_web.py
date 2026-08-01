"""Hardware-independent tests for the local HTTP API and GUI."""

from __future__ import annotations

import asyncio
import base64
import json
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
from nextuxlinq.web import MAX_REQUEST_BODY, create_app


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
    ]
    assert definitions[0]["default_settings"] == {}
    assert body["bar_metric_ids"] == [
        "cpu_usage", "memory_usage", "cpu_temperature",
    ]
    assert [metric["id"] for metric in body["metric_definitions"]] == [
        "cpu_usage", "memory_usage", "cpu_temperature", "uptime",
    ]
    assert all("path" not in metric for metric in body["metric_definitions"])
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


@pytest.mark.parametrize(
    ("preset_id", "region_index", "setting", "value", "category", "field"),
    [
        ("system-monitor", 0, "metric", "gpu_usage", "unknown_metric", "metric"),
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
