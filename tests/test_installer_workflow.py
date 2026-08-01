"""Hardware-independent tests for the managed shell installer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]


def run_script(
    sandbox: Path,
    script: str,
    *arguments: str,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    home = sandbox / "home with spaces"
    home.mkdir(parents=True, exist_ok=True)
    (sandbox / ".nextuxlinq-installer-test-root").touch()
    environment = {
        **os.environ,
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "NEXTUXLINQ_INSTALLER_TEST_MODE": "1",
        "NEXTUXLINQ_INSTALLER_TEST_ROOT": str(sandbox),
        **(extra or {}),
    }
    return subprocess.run(
        [str(REPOSITORY / script), *arguments],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("script", ["install.sh", "update.sh", "uninstall.sh"])
def test_help_is_side_effect_free(tmp_path: Path, script: str) -> None:
    result = run_script(tmp_path, script, "--help")
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert not (tmp_path / "home with spaces" / "data").exists()


def test_unknown_argument_fails(tmp_path: Path) -> None:
    result = run_script(tmp_path, "install.sh", "--unknown")
    assert result.returncode != 0
    assert "Unknown argument" in result.stderr


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    result = run_script(tmp_path, "install.sh", "--dry-run", "--no-udev")
    assert result.returncode == 0
    assert "[INFO] Would build" in result.stdout
    assert not (tmp_path / "home with spaces" / "data").exists()


def test_fresh_install_update_and_uninstall_preserve_data(tmp_path: Path) -> None:
    installed = run_script(tmp_path, "install.sh", "--no-udev", "--no-start")
    assert installed.returncode == 0, installed.stderr
    home = tmp_path / "home with spaces"
    root = home / "data" / "nextuxlinq"
    metadata = json.loads((root / "install-metadata.json").read_text())
    assert metadata["format_version"] == 1
    assert (root / "current").is_symlink()
    assert (home / ".local/bin/nextuxlinqctl").is_symlink()
    unit = home / "config/systemd/user/nextuxlinqd.service"
    assert str(root / "current/venv/bin/python") in unit.read_text()
    configuration = home / "config" / "nextuxlinq" / "config.json"
    configuration.parent.mkdir(parents=True)
    configuration.write_text("preserve", encoding="utf-8")

    repeated = run_script(tmp_path, "install.sh", "--no-udev")
    assert repeated.returncode != 0
    assert "already installed" in repeated.stderr

    updated = run_script(tmp_path, "update.sh", "--no-start")
    assert updated.returncode == 0, updated.stderr
    assert (root / "previous").is_symlink()
    assert configuration.read_text() == "preserve"

    removed = run_script(tmp_path, "uninstall.sh", "--keep-udev")
    assert removed.returncode == 0, removed.stderr
    assert not root.exists()
    assert not unit.exists()
    assert configuration.read_text() == "preserve"


def test_udev_checksum_and_modified_rule_preservation(tmp_path: Path) -> None:
    installed = run_script(
        tmp_path, "install.sh", "--no-start", "--non-interactive"
    )
    assert installed.returncode == 0, installed.stderr
    rule = tmp_path / "etc/udev/rules.d/70-nexlinq.rules"
    source = REPOSITORY / "udev/70-nextuxlinq.rules"
    assert rule.read_bytes() == source.read_bytes()
    rule.write_text("foreign", encoding="utf-8")
    removed = run_script(tmp_path, "uninstall.sh")
    assert removed.returncode == 0
    assert rule.read_text() == "foreign"
    assert "Preserving modified udev rule" in removed.stderr


def test_noninteractive_purge_requires_yes(tmp_path: Path) -> None:
    result = run_script(tmp_path, "uninstall.sh", "--purge", "--non-interactive")
    assert result.returncode != 0
    assert "requires --yes" in result.stderr


def test_failed_active_service_update_rolls_back(tmp_path: Path) -> None:
    assert run_script(tmp_path, "install.sh", "--no-udev", "--no-start").returncode == 0
    root = tmp_path / "home with spaces/data/nextuxlinq"
    original = (root / "current").resolve()
    result = run_script(
        tmp_path,
        "update.sh",
        extra={
            "NEXTUXLINQ_TEST_SERVICE_ACTIVE": "1",
            "NEXTUXLINQ_TEST_SERVICE_FAILURE": "1",
            "NEXTUXLINQ_TEST_RECOVERY_ENABLED": "1",
        },
    )
    assert result.returncode != 0
    assert (root / "current").resolve() == original
    assert "rollback completed" in result.stderr


def test_package_validation_failure_leaves_no_installation(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        "install.sh",
        "--no-udev",
        extra={"NEXTUXLINQ_TEST_BUILD_FAILURE": "1"},
    )
    assert result.returncode != 0
    assert "package validation failure" in result.stderr
    assert not (tmp_path / "home with spaces/data/nextuxlinq").exists()


def test_service_install_failure_cleans_release_and_udev(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        "install.sh",
        "--non-interactive",
        extra={"NEXTUXLINQ_TEST_INSTALL_FAILURE": "1"},
    )
    assert result.returncode != 0
    assert not (tmp_path / "home with spaces/data/nextuxlinq").exists()
    assert not (tmp_path / "etc/udev/rules.d/70-nexlinq.rules").exists()


def test_purge_removes_only_recognized_directories(tmp_path: Path) -> None:
    assert run_script(tmp_path, "install.sh", "--no-udev", "--no-start").returncode == 0
    home = tmp_path / "home with spaces"
    owned = home / "config/nextuxlinq/config.json"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_text("owned")
    foreign = home / "config/foreign"
    foreign.write_text("keep")
    result = run_script(
        tmp_path, "uninstall.sh", "--purge", "--yes", "--non-interactive",
    )
    assert result.returncode == 0
    assert not owned.parent.exists()
    assert foreign.read_text() == "keep"


def test_purge_refuses_owned_path_symlink_before_uninstall(tmp_path: Path) -> None:
    assert run_script(tmp_path, "install.sh", "--no-udev", "--no-start").returncode == 0
    home = tmp_path / "home with spaces"
    target = tmp_path / "foreign-data"
    target.mkdir()
    config = home / "config/nextuxlinq"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.symlink_to(target, target_is_directory=True)
    result = run_script(
        tmp_path, "uninstall.sh", "--purge", "--yes", "--non-interactive"
    )
    assert result.returncode != 0
    assert "unsafe symlink" in result.stderr
    assert (home / "data/nextuxlinq").exists()
    assert target.exists()


def test_source_udev_rule_is_narrow_and_assets_are_packaged() -> None:
    rule = (REPOSITORY / "udev/70-nextuxlinq.rules").read_text()
    assert 'idVendor}=="2e3c"' in rule
    assert 'idProduct}=="6500"' in rule
    assert rule.count("SUBSYSTEM") == 1
    project = (REPOSITORY / "pyproject.toml").read_text()
    assert "installer_assets/70-nextuxlinq.rules" in project
    assert "installer_assets/nextuxlinqd.service" in project


def test_foreign_launcher_is_never_overwritten_or_removed(tmp_path: Path) -> None:
    home = tmp_path / "home with spaces"
    launcher = home / ".local/bin/nextuxlinqctl"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("foreign", encoding="utf-8")
    installed = run_script(tmp_path, "install.sh", "--no-udev", "--no-start")
    assert installed.returncode != 0
    assert launcher.read_text() == "foreign"
    assert not (home / "data/nextuxlinq").exists()


def test_scripts_do_not_use_unsafe_shell_constructs() -> None:
    for name in ("install.sh", "update.sh", "uninstall.sh", "scripts/installer/common.sh"):
        source = (REPOSITORY / name).read_text()
        assert "eval " not in source
        assert "sudo pip" not in source
        assert "curl" not in source
        assert "git push" not in source


def test_no_showcase_image_is_changed_by_installer_tests(tmp_path: Path) -> None:
    before = {
        path: path.read_bytes()
        for path in (REPOSITORY / "docs/images").glob("*")
    } if (REPOSITORY / "docs/images").exists() else {}
    assert run_script(tmp_path, "install.sh", "--dry-run", "--no-udev").returncode == 0
    after = {path: path.read_bytes() for path in before}
    assert before == after
