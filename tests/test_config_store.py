"""Tests for XDG configuration loading and atomic persistence."""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from nexlinq.config_store import (
    ConfigSaveError,
    ConfigStore,
    parse_configuration,
    resolve_config_path,
)
from nexlinq.defaults import default_configuration


def test_xdg_path_resolution() -> None:
    assert resolve_config_path(
        {"XDG_CONFIG_HOME": "/tmp/example", "HOME": "/ignored"}
    ) == Path("/tmp/example/nexlinq/config.json")
    assert resolve_config_path(
        {"HOME": "/home/example"}
    ) == Path("/home/example/.config/nexlinq/config.json")
    for configured in ("~/custom", "./custom", "../custom", "relative/path"):
        assert resolve_config_path(
            {"XDG_CONFIG_HOME": configured, "HOME": "/home/example"}
        ) == Path("/home/example/.config/nexlinq/config.json")


def test_missing_file_uses_defaults_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "nexlinq" / "config.json"
    result = ConfigStore(path).load()
    assert result.configuration == default_configuration()
    assert result.persisted is False
    assert result.warning is None
    assert not path.exists()


def test_round_trip_is_normalized_and_private(tmp_path: Path) -> None:
    path = tmp_path / "nexlinq" / "config.json"
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
    directory = tmp_path / "nexlinq"
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
        tmp_path, "nexlinq.config_store.json.dump", TypeError("failed")
    )


def test_file_fsync_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nexlinq.config_store.os.fsync", OSError("failed")
    )


def test_file_chmod_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nexlinq.config_store.os.fchmod", OSError("failed")
    )


def test_replace_failure_is_pre_commit_and_cleans_up(tmp_path: Path) -> None:
    assert_pre_commit_failure(
        tmp_path, "nexlinq.config_store.os.replace", OSError("failed")
    )


def test_directory_chmod_failure_is_pre_commit(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    with patch("nexlinq.config_store.os.chmod", side_effect=OSError("failed")):
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
        patch("nexlinq.config_store.os.fdopen", side_effect=OSError("open failed")),
        patch("nexlinq.config_store.os.close", side_effect=close_then_fail),
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

    with patch("nexlinq.config_store.os.open", side_effect=fail_directory_open):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


def test_directory_fsync_failure_reports_committed_state(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    fsync = Mock(side_effect=[None, OSError("failed")])
    with patch("nexlinq.config_store.os.fsync", fsync):
        with pytest.raises(ConfigSaveError) as raised:
            ConfigStore(path).save(default_configuration())

    assert raised.value.committed is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not [entry for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]


def test_saved_json_has_expected_schema(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(default_configuration())
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
