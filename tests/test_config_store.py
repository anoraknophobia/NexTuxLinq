"""Tests for XDG configuration loading and atomic persistence."""

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from nextuxlinq.config_store import (
    LEGACY_LOAD_WARNING,
    LEGACY_MIGRATION_WARNING,
    MAX_CONFIGURATION_BYTES,
    ConfigSaveError,
    ConfigStore,
    parse_configuration,
    resolve_config_path,
    resolve_legacy_config_path,
)
from nextuxlinq.defaults import default_configuration


def _write_private_legacy(path: Path, contents: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o600)


def test_xdg_path_resolution() -> None:
    assert resolve_config_path(
        {"XDG_CONFIG_HOME": "/tmp/example", "HOME": "/ignored"}
    ) == Path("/tmp/example/nextuxlinq/config.json")
    assert resolve_config_path(
        {"HOME": "/home/example"}
    ) == Path("/home/example/.config/nextuxlinq/config.json")
    for configured in ("~/custom", "./custom", "../custom", "relative/path"):
        assert resolve_config_path(
            {"XDG_CONFIG_HOME": configured, "HOME": "/home/example"}
        ) == Path("/home/example/.config/nextuxlinq/config.json")
    assert resolve_legacy_config_path(
        {"XDG_CONFIG_HOME": "/tmp/example", "HOME": "/ignored"}
    ) == Path("/tmp/example/nexlinq/config.json")


def test_only_legacy_configuration_loads_without_writing(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    document = default_configuration().to_dict()
    document["profiles"]["default"]["scrolling_text"] = "LEGACY"
    original = json.dumps(document)
    _write_private_legacy(legacy, original)

    result = ConfigStore(canonical, legacy_path=legacy).load()

    assert result.configuration.active.scrolling_text == "LEGACY"
    assert result.persisted is True
    assert result.migration_pending is True
    assert result.warning == LEGACY_MIGRATION_WARNING
    assert str(tmp_path) not in result.warning
    assert not canonical.exists()
    assert legacy.read_text(encoding="utf-8") == original


def test_save_after_legacy_load_migrates_without_deleting_legacy(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    original = json.dumps(default_configuration().to_dict())
    _write_private_legacy(legacy, original)
    store = ConfigStore(canonical, legacy_path=legacy)
    loaded = store.load()

    store.save(loaded.configuration)

    assert json.loads(canonical.read_text(encoding="utf-8")) == (
        default_configuration().to_dict()
    )
    assert legacy.read_text(encoding="utf-8") == original
    assert canonical.stat().st_mode & 0o777 == 0o600
    assert canonical.parent.stat().st_mode & 0o777 == 0o700


def test_canonical_configuration_always_wins_over_legacy(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    canonical.parent.mkdir()
    canonical_document = default_configuration().to_dict()
    canonical_document["profiles"]["default"]["scrolling_text"] = "CANONICAL"
    legacy_document = default_configuration().to_dict()
    legacy_document["profiles"]["default"]["scrolling_text"] = "LEGACY"
    canonical.write_text(json.dumps(canonical_document), encoding="utf-8")
    _write_private_legacy(legacy, json.dumps(legacy_document))

    result = ConfigStore(canonical, legacy_path=legacy).load()

    assert result.configuration.active.scrolling_text == "CANONICAL"
    assert result.persisted is True
    assert result.migration_pending is False
    assert result.warning is None


def test_migration_never_overwrites_canonical_file_created_after_load(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    _write_private_legacy(
        legacy,
        json.dumps(default_configuration().to_dict()),
    )
    store = ConfigStore(canonical, legacy_path=legacy)
    loaded = store.load()
    canonical.parent.mkdir()
    canonical_contents = '{"created": "concurrently"}\n'
    canonical.write_text(canonical_contents, encoding="utf-8")

    with pytest.raises(ConfigSaveError) as raised:
        store.save(loaded.configuration)

    assert raised.value.committed is False
    assert canonical.read_text(encoding="utf-8") == canonical_contents
    assert legacy.exists()


def test_invalid_canonical_configuration_does_not_fall_back_to_legacy(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    canonical.parent.mkdir()
    canonical.write_text("{broken", encoding="utf-8")
    _write_private_legacy(
        legacy,
        json.dumps(default_configuration().to_dict()),
    )

    result = ConfigStore(canonical, legacy_path=legacy).load()

    assert result.configuration == default_configuration()
    assert result.persisted is False
    assert result.migration_pending is False
    assert result.warning is not None
    assert canonical.read_text(encoding="utf-8") == "{broken"


def _assert_legacy_rejected(
    canonical: Path,
    legacy: Path,
) -> None:
    result = ConfigStore(canonical, legacy_path=legacy).load()

    assert result.configuration == default_configuration()
    assert result.persisted is False
    assert result.migration_pending is False
    assert result.warning == LEGACY_LOAD_WARNING
    assert str(legacy) not in result.warning
    assert not canonical.exists()


def test_symlinked_legacy_configuration_is_rejected(tmp_path: Path) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    target = tmp_path / "target.json"
    target.write_text(json.dumps(default_configuration().to_dict()), encoding="utf-8")
    legacy.parent.mkdir(mode=0o700)
    legacy.symlink_to(target)

    _assert_legacy_rejected(canonical, legacy)


def test_legacy_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    legacy.parent.mkdir(mode=0o700)
    os.mkfifo(legacy, mode=0o600)

    _assert_legacy_rejected(canonical, legacy)


def test_legacy_directory_is_rejected(tmp_path: Path) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    legacy.mkdir(mode=0o700, parents=True)

    _assert_legacy_rejected(canonical, legacy)


def test_legacy_socket_metadata_is_rejected(tmp_path: Path) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    _write_private_legacy(
        legacy,
        json.dumps(default_configuration().to_dict()),
    )
    original_fstat = os.fstat

    def socket_file_metadata(descriptor: int):
        details = original_fstat(descriptor)
        if stat.S_ISREG(details.st_mode):
            return SimpleNamespace(
                st_mode=stat.S_IFSOCK | 0o600,
                st_uid=details.st_uid,
                st_size=details.st_size,
            )
        return details

    with patch(
        "nextuxlinq.config_store.os.fstat",
        side_effect=socket_file_metadata,
    ):
        _assert_legacy_rejected(canonical, legacy)


def test_foreign_owned_legacy_configuration_is_rejected(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    _write_private_legacy(
        legacy,
        json.dumps(default_configuration().to_dict()),
    )
    original_fstat = os.fstat

    def foreign_file_metadata(descriptor: int):
        details = original_fstat(descriptor)
        if stat.S_ISREG(details.st_mode):
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_uid=os.getuid() + 1,
                st_size=details.st_size,
            )
        return details

    with patch(
        "nextuxlinq.config_store.os.fstat",
        side_effect=foreign_file_metadata,
    ):
        _assert_legacy_rejected(canonical, legacy)


def test_legacy_configuration_with_unsafe_permissions_is_rejected(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    _write_private_legacy(
        legacy,
        json.dumps(default_configuration().to_dict()),
    )
    legacy.chmod(0o640)

    _assert_legacy_rejected(canonical, legacy)


def test_oversized_legacy_configuration_is_rejected(tmp_path: Path) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    _write_private_legacy(legacy, " " * (MAX_CONFIGURATION_BYTES + 1))

    _assert_legacy_rejected(canonical, legacy)


@pytest.mark.parametrize(
    "contents",
    [
        "{broken",
        '{"schema_version": 99}',
    ],
    ids=["invalid-json", "newer-schema"],
)
def test_invalid_or_newer_legacy_configuration_falls_back_safely(
    tmp_path: Path,
    contents: str,
) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    _write_private_legacy(legacy, contents)
    original = legacy.read_bytes()

    _assert_legacy_rejected(canonical, legacy)

    assert legacy.read_bytes() == original


def test_canonical_appearing_during_legacy_load_wins(tmp_path: Path) -> None:
    canonical = tmp_path / "nextuxlinq" / "config.json"
    legacy = tmp_path / "nexlinq" / "config.json"
    legacy_document = default_configuration().to_dict()
    legacy_document["profiles"]["default"]["scrolling_text"] = "LEGACY"
    canonical_document = default_configuration().to_dict()
    canonical_document["profiles"]["default"]["scrolling_text"] = "CANONICAL"
    _write_private_legacy(legacy, json.dumps(legacy_document))
    store = ConfigStore(canonical, legacy_path=legacy)
    original_read = store._read_legacy_configuration

    def read_then_create_canonical():
        configuration = original_read()
        canonical.parent.mkdir(mode=0o700)
        canonical.write_text(json.dumps(canonical_document), encoding="utf-8")
        return configuration

    with patch.object(
        store,
        "_read_legacy_configuration",
        side_effect=read_then_create_canonical,
    ):
        result = store.load()

    assert result.configuration.active.scrolling_text == "CANONICAL"
    assert result.persisted is True
    assert result.migration_pending is False
    assert result.warning is None


def test_missing_file_uses_defaults_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "nextuxlinq" / "config.json"
    result = ConfigStore(path).load()
    assert result.configuration == default_configuration()
    assert result.persisted is False
    assert result.warning is None
    assert not path.exists()


def test_round_trip_is_normalized_and_private(tmp_path: Path) -> None:
    path = tmp_path / "nextuxlinq" / "config.json"
    store = ConfigStore(path)
    store.save(default_configuration())
    result = store.load()

    assert result.configuration == default_configuration()
    assert result.persisted is True
    assert result.warning is None
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not [entry for entry in path.parent.iterdir() if entry.suffix == ".tmp"]


def test_invalid_file_is_preserved_and_defaults_are_safe(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    result = ConfigStore(path).load()

    assert result.configuration == default_configuration()
    assert result.warning is not None
    assert path.read_text(encoding="utf-8") == "{broken"


def test_newer_schema_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    contents = '{"schema_version": 99}'
    path.write_text(contents, encoding="utf-8")
    result = ConfigStore(path).load()

    assert result.persisted is False
    assert "newer" in (result.warning or "")
    assert path.read_text(encoding="utf-8") == contents


def test_pre_versioned_document_migrates_in_memory() -> None:
    raw = default_configuration().to_dict()
    del raw["schema_version"]
    migrated = parse_configuration(raw)
    assert migrated == default_configuration()


def test_huge_numeric_value_falls_back_safely(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = default_configuration().to_dict()
    raw["profiles"]["default"]["scroll_speed"] = 10**1000
    path.write_text(json.dumps(raw), encoding="utf-8")

    result = ConfigStore(path).load()

    assert result.configuration == default_configuration()
    assert result.persisted is False
    assert result.warning is not None


def test_existing_configuration_directory_is_restricted(tmp_path: Path) -> None:
    directory = tmp_path / "nextuxlinq"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    path = directory / "config.json"

    ConfigStore(path).save(default_configuration())

    assert directory.stat().st_mode & 0o777 == 0o700


def assert_pre_commit_failure(
    tmp_path: Path,
    target: str,
    failure: object,
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    with patch(target, side_effect=failure):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is False
    assert path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


def test_serialization_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nextuxlinq.config_store.json.dump", TypeError("failed")
    )


def test_file_fsync_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nextuxlinq.config_store.os.fsync", OSError("failed")
    )


def test_file_chmod_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nextuxlinq.config_store.os.fchmod", OSError("failed")
    )


def test_replace_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nextuxlinq.config_store.os.replace", OSError("failed")
    )


def test_directory_chmod_failure_is_pre_commit(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    with patch("nextuxlinq.config_store.os.chmod", side_effect=OSError("failed")):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is False
    assert path.read_text(encoding="utf-8") == '{"old": true}\n'


def test_descriptor_close_failure_does_not_prevent_temp_cleanup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    actual_close = os.close

    def close_then_fail(descriptor: int) -> None:
        actual_close(descriptor)
        raise OSError("close failed")

    with (
        patch("nextuxlinq.config_store.os.fdopen", side_effect=OSError("open failed")),
        patch("nextuxlinq.config_store.os.close", side_effect=close_then_fail),
    ):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is False
    assert raised.value.__cause__ is not None
    assert str(raised.value.__cause__) == "open failed"
    assert path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


def test_directory_open_failure_reports_committed_state(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    actual_open = os.open

    def fail_directory_open(
        target: object, flags: int, mode: int = 0o777
    ) -> int:
        if flags & os.O_DIRECTORY:
            raise OSError("failed")
        return actual_open(target, flags, mode)  # type: ignore[arg-type]

    with patch("nextuxlinq.config_store.os.open", side_effect=fail_directory_open):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


def test_directory_fsync_failure_reports_committed_state(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    fsync = Mock(side_effect=[None, OSError("failed")])
    with patch("nextuxlinq.config_store.os.fsync", fsync):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


def test_saved_json_has_expected_schema(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(default_configuration())
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
