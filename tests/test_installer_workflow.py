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


# ---------------------------------------------------------------------------
# Temporary-directory handling
# ---------------------------------------------------------------------------


def source_common(sandbox: Path, snippet: str, extra: dict[str, str] | None = None):
    """Run a snippet against the real common.sh without touching the system."""
    home = sandbox / "home with spaces"
    home.mkdir(parents=True, exist_ok=True)
    (sandbox / ".nextuxlinq-installer-test-root").touch()
    environment = {
        **os.environ,
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "NEXTUXLINQ_INSTALLER_TEST_MODE": "1",
        "NEXTUXLINQ_INSTALLER_TEST_ROOT": str(sandbox),
        **(extra or {}),
    }
    script = (
        'source "$1/scripts/installer/common.sh"\ninit_environment\n' + snippet
    )
    return subprocess.run(
        ["bash", "-c", script, "bash", str(REPOSITORY)],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "tmpdir_value",
    ["/var/tmp", "/tmp/nextuxlinq installer spaces"],
)
def test_temporary_build_directory_ignores_a_foreign_tmpdir(
    tmp_path: Path, tmpdir_value: str
) -> None:
    result = source_common(
        tmp_path,
        'path=$(create_temporary_directory)\n'
        'printf "%s\\n" "$path"\n'
        'cleanup_temporary "$path"\n'
        '[[ -e $path ]] && exit 3\n'
        'exit 0\n',
        extra={"TMPDIR": tmpdir_value},
    )

    assert result.returncode == 0, result.stderr
    created = result.stdout.strip().splitlines()[-1]
    # The build directory lives under the approved base, never under TMPDIR.
    assert created.startswith(f"{tmp_path}/tmp/nextuxlinq-installer.")
    assert not created.startswith(tmpdir_value)
    assert not Path(created).exists()


def test_temporary_build_directory_is_private_and_unpredictable(
    tmp_path: Path,
) -> None:
    result = source_common(
        tmp_path,
        'first=$(create_temporary_directory)\n'
        'second=$(create_temporary_directory)\n'
        'printf "%s\\n%s\\n" "$first" "$second"\n'
        'stat -c "%a" "$first"\n',
    )

    assert result.returncode == 0, result.stderr
    first, second, mode = result.stdout.strip().splitlines()[-3:]
    assert first != second
    assert mode == "700"
    assert len(Path(first).name) > len("nextuxlinq-installer.")


def test_cleanup_warns_and_succeeds_for_an_unrecognized_path(
    tmp_path: Path,
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    result = source_common(
        tmp_path,
        f'cleanup_temporary "{foreign}"\n'
        'printf "exit=%s\\n" "$?"\n',
    )

    assert result.returncode == 0, result.stderr
    assert "exit=0" in result.stdout
    assert "[WARN]" in result.stderr
    # A foreign directory is preserved, never removed.
    assert foreign.exists()


def test_cleanup_refuses_to_follow_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep").write_text("keep", encoding="utf-8")
    result = source_common(
        tmp_path,
        'base="$TEMPORARY_BASE"\n'
        'mkdir -p -- "$base"\n'
        f'ln -s "{target}" "$base/nextuxlinq-installer.symlink"\n'
        'cleanup_temporary "$base/nextuxlinq-installer.symlink"\n'
        'printf "exit=%s\\n" "$?"\n',
    )

    assert result.returncode == 0, result.stderr
    assert "exit=0" in result.stdout
    assert (target / "keep").read_text() == "keep"


def test_cleanup_warning_does_not_fail_a_successful_installation(
    tmp_path: Path,
) -> None:
    installed = run_script(tmp_path, "install.sh", "--no-udev", "--no-start")

    assert installed.returncode == 0, installed.stderr
    assert "[OK] Installed NexTuxLinq" in installed.stdout
    assert "Refusing unsafe temporary cleanup" not in installed.stderr
    leftovers = list((tmp_path / "tmp").glob("nextuxlinq-installer.*"))
    assert leftovers == []


@pytest.mark.parametrize("tmpdir_value", ["/var/tmp", "/tmp/spaced dir"])
def test_installation_succeeds_with_a_foreign_tmpdir(
    tmp_path: Path, tmpdir_value: str
) -> None:
    installed = run_script(
        tmp_path,
        "install.sh",
        "--no-udev",
        "--no-start",
        extra={"TMPDIR": tmpdir_value},
    )

    assert installed.returncode == 0, installed.stderr
    assert (tmp_path / "home with spaces/data/nextuxlinq/current").is_symlink()


def test_staged_python_invocation_is_quoted() -> None:
    common = (REPOSITORY / "scripts/installer/common.sh").read_text()

    assert '"$stage/venv/bin/python" -c' in common
    assert "$stage/venv/bin/python -c" not in common.replace(
        '"$stage/venv/bin/python" -c', ""
    )


# ---------------------------------------------------------------------------
# Update rollback traps
# ---------------------------------------------------------------------------


def install_for_update(tmp_path: Path) -> Path:
    assert run_script(
        tmp_path, "install.sh", "--no-udev", "--no-start"
    ).returncode == 0
    return tmp_path / "home with spaces/data/nextuxlinq"


def test_failure_immediately_after_the_switch_rolls_back(
    tmp_path: Path,
) -> None:
    root = install_for_update(tmp_path)
    original = (root / "current").resolve()
    releases_before = {item.name for item in (root / "releases").iterdir()}

    result = run_script(
        tmp_path,
        "update.sh",
        extra={"NEXTUXLINQ_TEST_INSTALL_FAILURE": "1"},
    )

    assert result.returncode != 0
    assert (root / "current").resolve() == original
    # The known-good previous release is still present.
    assert original.exists()
    assert {
        item.name for item in (root / "releases").iterdir()
    } == releases_before
    assert "Update failed; restoring" in result.stderr


def test_rollback_runs_exactly_once_and_is_idempotent(tmp_path: Path) -> None:
    root = install_for_update(tmp_path)
    original = (root / "current").resolve()

    result = run_script(
        tmp_path,
        "update.sh",
        extra={
            "NEXTUXLINQ_TEST_SERVICE_ACTIVE": "1",
            "NEXTUXLINQ_TEST_SERVICE_FAILURE": "1",
        },
    )

    assert result.returncode != 0
    # The explicit failure path and the EXIT trap both request a rollback; the
    # guard must collapse them into a single restoration.
    assert result.stderr.count("Update failed; restoring") == 1
    assert (root / "current").resolve() == original


@pytest.mark.parametrize(
    ("signal_name", "expected_code"),
    [("INT", 130), ("TERM", 143)],
)
def test_signal_after_the_switch_rolls_back(
    tmp_path: Path, signal_name: str, expected_code: int
) -> None:
    import signal as signal_module
    import time

    root = install_for_update(tmp_path)
    original = (root / "current").resolve()
    pause_file = tmp_path / "paused"

    home = tmp_path / "home with spaces"
    environment = {
        **os.environ,
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "NEXTUXLINQ_INSTALLER_TEST_MODE": "1",
        "NEXTUXLINQ_INSTALLER_TEST_ROOT": str(tmp_path),
        "NEXTUXLINQ_TEST_PAUSE_FILE": str(pause_file),
    }
    process = subprocess.Popen(
        [str(REPOSITORY / "update.sh")],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30.0
        while not pause_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.02)
        assert pause_file.exists(), "update.sh never reached the switch point"
        # The switch already happened; current now points at the new release.
        assert (root / "current").resolve() != original
        process.send_signal(getattr(signal_module, f"SIG{signal_name}"))
        stdout, stderr = process.communicate(timeout=60)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()
            process.communicate(timeout=30)

    assert process.returncode == expected_code, stderr
    assert f"interrupted by SIG{signal_name}" in stderr
    assert "Update failed; restoring" in stderr
    assert (root / "current").resolve() == original
    assert original.exists()
    leftovers = list((tmp_path / "tmp").glob("nextuxlinq-installer.*"))
    assert leftovers == []


def test_successful_update_never_rolls_back(tmp_path: Path) -> None:
    root = install_for_update(tmp_path)
    original = (root / "current").resolve()

    result = run_script(tmp_path, "update.sh", "--no-start")

    assert result.returncode == 0, result.stderr
    assert "Updated NexTuxLinq to" in result.stdout
    assert "Update failed" not in result.stderr
    assert (root / "current").resolve() != original
    # The previous release is retained for a later manual rollback.
    assert (root / "previous").resolve() == original
    assert original.exists()


def test_update_restores_the_previous_release_pointer_on_rollback(
    tmp_path: Path,
) -> None:
    root = install_for_update(tmp_path)
    first = (root / "current").resolve()
    assert run_script(tmp_path, "update.sh", "--no-start").returncode == 0
    second = (root / "current").resolve()
    assert (root / "previous").resolve() == first

    result = run_script(
        tmp_path,
        "update.sh",
        extra={"NEXTUXLINQ_TEST_INSTALL_FAILURE": "1"},
    )

    assert result.returncode != 0
    assert (root / "current").resolve() == second
    assert (root / "previous").resolve() == first


def test_installer_scripts_never_touch_the_real_system(tmp_path: Path) -> None:
    """Sandbox runs must not reference real systemd, sudo, udev, or /etc."""
    installed = run_script(tmp_path, "install.sh", "--no-udev", "--no-start")
    updated = run_script(tmp_path, "update.sh", "--no-start")
    removed = run_script(tmp_path, "uninstall.sh", "--keep-udev")

    for result in (installed, updated, removed):
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        for forbidden in ("/etc/udev", "sudo ", "systemctl ", "udevadm "):
            assert forbidden not in combined
