"""Hardware-independent Phase 6 profile transfer, preset, and backup tests."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from nextuxlinq.config_store import (
    MANAGED_BACKUP_RETENTION,
    ConfigStore,
    ManagedBackupError,
)
from nextuxlinq.defaults import legacy_equivalent_configuration_v2
from nextuxlinq.models_v2 import ConfigurationDocumentV2
from nextuxlinq.profile_presets import get_builtin_presets
from nextuxlinq.profile_transfer import (
    MAX_IMPORT_BYTES,
    PROFILE_EXPORT_FORMAT,
    TransferValidationError,
    decode_import_content,
    deterministic_json,
    export_profile,
    import_configuration,
    import_profile,
    parse_json_bytes,
    safe_profile_filename,
)
from nextuxlinq.rendering_v2.compositor import Compositor
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.service import NexTuxLinqService
from nextuxlinq.system_metrics import SystemMetricsSnapshot
from nextuxlinq.web import create_app


class NoHardwareWorker:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def submit_frame(self, frame: bytes, revision: int = 0) -> None:
        self.frames.append(frame)

    def set_brightness(self, value: int, revision: int = 0) -> None:
        raise AssertionError("Phase 6 read-only operations must not set brightness")


class LocalClient:
    def __init__(self, app) -> None:
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://127.0.0.1",
                headers={"host": "127.0.0.1"},
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())


def phase6_client(tmp_path: Path) -> tuple[LocalClient, NexTuxLinqService, NoHardwareWorker]:
    configuration = legacy_equivalent_configuration_v2()
    store = ConfigStore(tmp_path / "nextuxlinq" / "config.json")
    worker = NoHardwareWorker()
    class Metrics:
        def sample(self) -> SystemMetricsSnapshot:
            return SystemMetricsSnapshot.unavailable(
                5.0, datetime(2026, 8, 1, tzinfo=timezone.utc)
            )
    service = NexTuxLinqService(
        config_store=store,
        device_worker=worker,  # type: ignore[arg-type]
        wall_clock=lambda: datetime(2026, 8, 1, tzinfo=timezone.utc),
        monotonic=lambda: 5.0,
        metrics_provider=Metrics(),
    )
    service._configuration = configuration
    service._saved_configuration = configuration
    service._saved_revision = 1
    service._saved_revision_counter = 1
    service.state.update(
        lifecycle_state="running",
        rendering_state="rendering",
        active_configuration_revision=1,
    )
    return LocalClient(create_app(service)), service, worker


def test_profile_export_round_trip_is_exact_and_runtime_free() -> None:
    configuration = legacy_equivalent_configuration_v2()
    profile = configuration.active
    envelope = export_profile(configuration.active_profile, profile)

    imported = import_profile(
        parse_json_bytes(deterministic_json(envelope)),
        (),
    )

    assert envelope["format"] == PROFILE_EXPORT_FORMAT
    assert imported.profile_name == configuration.active_profile
    assert imported.profile.to_dict() == profile.to_dict()
    assert [region.id for region in imported.profile.regions] == [
        region.id for region in profile.regions
    ]
    serialized = deterministic_json(envelope)
    assert serialized == deterministic_json(envelope)
    for forbidden in (
        b"hidraw", b"device_path", b"systemd", b"revision", b"viewer_x"
    ):
        assert forbidden not in serialized


def test_full_configuration_round_trip_preserves_order_and_active_profile() -> None:
    source = legacy_equivalent_configuration_v2()
    profiles = {
        "second": source.active,
        "first": source.active,
    }
    document = ConfigurationDocumentV2(
        schema_version=2,
        active_profile="first",
        profiles=profiles,
    )

    imported = import_configuration(parse_json_bytes(deterministic_json(document.to_dict())))

    assert list(imported.to_dict()["profiles"]) == ["first", "second"]
    assert imported.active_profile == "first"
    assert imported.to_dict() == document.to_dict()


@pytest.mark.parametrize("preset_id", ["system-monitor", "system-bars"])
def test_metric_widgets_round_trip_through_profile_transfer(preset_id: str) -> None:
    profile = next(
        preset.profile for preset in get_builtin_presets()
        if preset.id == preset_id
    )
    envelope = export_profile("metrics", profile)
    imported = import_profile(envelope, ())
    assert imported.profile.to_dict() == profile.to_dict()


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", "empty_file"),
        (b"\xff", "invalid_utf8"),
        (b"{", "malformed_json"),
        (b'{"a":1,"a":2}', "malformed_json"),
        (b"x" * (MAX_IMPORT_BYTES + 1), "file_too_large"),
    ],
)
def test_strict_import_parser_reports_sanitized_errors(raw: bytes, code: str) -> None:
    with pytest.raises(TransferValidationError) as caught:
        parse_json_bytes(raw)
    assert caught.value.code == code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    if raw:
        assert raw[:20] not in str(caught.value).encode()


def test_profile_import_name_collision_and_region_id_rejection() -> None:
    document = legacy_equivalent_configuration_v2()
    envelope = export_profile("default", document.active)
    imported = import_profile(envelope, ("default", "default_Imported"))
    assert imported.profile_name == "default_Imported_2"

    invalid = json.loads(json.dumps(envelope))
    invalid["profile"]["regions"][1]["id"] = invalid["profile"]["regions"][0]["id"]
    with pytest.raises(TransferValidationError) as caught:
        import_profile(invalid, ())
    assert caught.value.code == "duplicate_region_id"


def test_safe_filename_does_not_change_embedded_profile_name() -> None:
    assert safe_profile_filename("Default_01") == (
        "nextuxlinq-profile-default_01.json"
    )
    assert export_profile("Default_01", legacy_equivalent_configuration_v2().active)[
        "profile_name"
    ] == "Default_01"


def test_every_builtin_preset_validates_and_renders_without_mutation() -> None:
    compositor = Compositor(create_widget_registry())
    presets = get_builtin_presets()
    expected_counts = [0, 2, 3, 2, 2, 1, 3, 3]
    before = [preset.profile.to_dict() for preset in presets]

    for preset, expected in zip(presets, expected_counts, strict=True):
        assert len(preset.profile.regions) == expected
        result = compositor.render(
            preset.profile,
            generation_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
            animation_time=0.0,
        )
        assert len(result.logical_framebuffer) == 90 * 10 * 3
    assert presets[3].profile.regions[0].widget.settings.font.value == "3x5"
    assert presets[3].profile.regions[1].widget.settings.font.value == "3x5"
    assert [preset.profile.to_dict() for preset in presets] == before


def test_managed_backup_lifecycle_permissions_retention_and_migration_preservation(
    tmp_path: Path,
) -> None:
    counter = iter(f"{index:032x}" for index in range(20))
    tick = iter(
        datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(seconds=index)
        for index in range(20)
    )
    path = tmp_path / "nextuxlinq" / "config.json"
    store = ConfigStore(
        path,
        backup_id_factory=lambda: next(counter),
        backup_clock=lambda: next(tick),
    )
    migration = path.parent / ".config.json.schema-v1.original.bak"
    migration.parent.mkdir(mode=0o700, parents=True)
    migration.write_text("original", encoding="utf-8")
    migration.chmod(0o600)
    configuration = legacy_equivalent_configuration_v2()

    created = [store.create_backup(f"Backup {index}", configuration) for index in range(12)]
    backups = store.list_backups()

    assert len(backups) == MANAGED_BACKUP_RETENTION
    assert created[-1].id == backups[0].id
    assert migration.read_text(encoding="utf-8") == "original"
    backup_path = store.backup_directory / f"backup-{created[-1].id}.json"
    assert backup_path.stat().st_mode & 0o777 == 0o600
    assert store.backup_directory.stat().st_mode & 0o777 == 0o700
    restored = store.restore_backup(created[-1].id)
    assert restored.to_dict() == configuration.to_dict()
    store.delete_backup(created[-1].id)
    assert created[-1].id not in {item.id for item in store.list_backups()}
    with pytest.raises(ManagedBackupError) as caught:
        store.restore_backup("../config.json")
    assert caught.value.code == "backup_not_found"


def test_managed_backup_directory_symlink_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "configuration" / "config.json"
    outside = tmp_path / "outside"
    outside.mkdir()
    path.parent.mkdir()
    (path.parent / "backups").symlink_to(outside, target_is_directory=True)
    store = ConfigStore(path)

    with pytest.raises(ManagedBackupError) as caught:
        store.create_backup("Unsafe", legacy_equivalent_configuration_v2())

    assert caught.value.code == "invalid_backup"
    assert list(outside.iterdir()) == []


def test_phase6_api_operations_are_side_effect_free_until_explicit_mutation(
    tmp_path: Path,
) -> None:
    client, service, worker = phase6_client(tmp_path)
    before = service.get_configuration()

    profile_export = client.request(
        "GET", "/api/v1/free-layout/export/profile/default"
    )
    config_export = client.request(
        "GET", "/api/v1/free-layout/export/configuration"
    )
    presets = client.request("GET", "/api/v1/free-layout/presets")
    envelope = json.loads(profile_export.content)
    profile_import = client.request(
        "POST",
        "/api/v1/free-layout/import/profile/validate",
        json={
            "content_base64": base64.b64encode(
                deterministic_json(envelope)
            ).decode("ascii"),
            "existing_profile_names": ["default"],
            "existing_region_count": len(
                legacy_equivalent_configuration_v2().active.regions
            ),
        },
    )
    full_import = client.request(
        "POST",
        "/api/v1/free-layout/import/configuration/validate",
        json={"content_base64": base64.b64encode(config_export.content).decode("ascii")},
    )

    assert profile_export.status_code == 200
    assert "nextuxlinq-profile-default.json" in profile_export.headers["content-disposition"]
    assert config_export.status_code == 200
    assert list(json.loads(config_export.content)["profiles"]) == ["default"]
    assert len(presets.json()["presets"]) == 8
    assert profile_import.status_code == 200
    assert profile_import.json()["profile_name"] == "default_Imported"
    assert full_import.json()["summary"] == {
        "profile_count": 1,
        "active_profile": "default",
        "schema_version": 2,
        "replaces_existing_profiles": True,
    }
    after = service.get_configuration()
    assert after.configuration == before.configuration
    assert after.active_revision == before.active_revision
    assert after.saved_revision == before.saved_revision
    assert worker.frames == []


def test_backup_api_never_exposes_paths_and_restore_is_draft_only(tmp_path: Path) -> None:
    client, service, worker = phase6_client(tmp_path)
    created = client.request(
        "POST", "/api/v1/free-layout/backups", json={"name": "Before testing"}
    )
    listed = client.request("GET", "/api/v1/free-layout/backups")
    backup_id = created.json()["id"]
    restored = client.request(
        "POST", f"/api/v1/free-layout/backups/{backup_id}/restore", json={}
    )

    assert created.status_code == 200
    assert "path" not in json.dumps(listed.json()).lower()
    assert restored.status_code == 200
    assert restored.json()["configuration"] == (
        legacy_equivalent_configuration_v2().to_dict()
    )
    assert service.get_configuration().active_revision == 1
    assert worker.frames == []
    deleted = client.request(
        "DELETE", f"/api/v1/free-layout/backups/{backup_id}", json={}
    )
    assert deleted.json() == {"deleted": True}
