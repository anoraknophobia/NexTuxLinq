"""Hardware-independent systemd user-service management tests."""

from __future__ import annotations

import ctypes
import errno
import multiprocessing
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nextuxlinq import cli
from nextuxlinq import service_manager
from nextuxlinq.service_manager import (
    CachedUserServiceStatusProvider,
    GENERATED_MARKER,
    GUI_ADDRESS,
    HELPER_SERVICES,
    MANAGER_LOCK_NAME,
    OPENLINKHUB_DROPIN_DIRECTORY,
    OPENLINKHUB_DROPIN_MARKER,
    OPENLINKHUB_DROPIN_NAME,
    OPENLINKHUB_SERVICE_NAME,
    SERVICE_NAME,
    ServiceManager,
    ServiceManagerError,
    ServiceOperationResult,
    _default_runner,
    _resolve_config_root,
    detect_runtime_mode,
    generate_unit,
    generate_openlinkhub_restart_dropin,
    systemd_quote_argument,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def completed(
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
):
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class RecordingRunner:
    def __init__(self, callback=None) -> None:
        self.calls: list[list[str]] = []
        self.callback = callback

    def __call__(self, arguments: list[str]):
        assert isinstance(arguments, list)
        assert "sudo" not in arguments
        self.calls.append(arguments)
        if self.callback is not None:
            return self.callback(arguments)
        if arguments[2:3] == ["disable"]:
            return completed(
                1,
                stderr=(
                    "Failed to disable unit: Unit file "
                    f"{arguments[-1]} does not exist."
                ),
            )
        if arguments[2:3] == ["is-active"]:
            return completed(stdout="active\n")
        return completed()


def manager_for(
    tmp_path: Path,
    *,
    runner: RecordingRunner | None = None,
    executable: str = "/opt/NexTuxLinq Python/bin/python3",
) -> tuple[ServiceManager, RecordingRunner]:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    xdg = tmp_path / "xdg config"
    selected_runner = RecordingRunner() if runner is None else runner
    manager = ServiceManager(
        environ={
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
        },
        executable=executable,
        runner=selected_runner,
    )
    return manager, selected_runner


def managed_unit_state(
    manager: ServiceManager,
) -> tuple[bytes, int]:
    manager._write_unit_atomically(force=False)
    return (
        manager.unit_path.read_bytes(),
        stat.S_IMODE(manager.unit_path.stat().st_mode),
    )


def assert_managed_unit_preserved(
    manager: ServiceManager,
    runner: RecordingRunner,
    original: bytes,
    original_mode: int,
) -> None:
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert not list(manager.unit_directory.glob(f".{SERVICE_NAME}.*"))
    assert runner.calls == []


def try_manager_lock_in_child(
    environment: dict[str, str],
    results,
) -> None:
    manager = ServiceManager(environ=environment, runner=RecordingRunner())
    directory_descriptor = manager._open_unit_directory(create=False)
    assert directory_descriptor is not None
    lock_descriptor: int | None = None
    try:
        lock_descriptor = manager._acquire_manager_lock(directory_descriptor)
    except ServiceManagerError as error:
        results.put(str(error))
    else:
        results.put("acquired")
    finally:
        manager._release_operation_descriptors(
            lock_descriptor,
            directory_descriptor,
        )


def test_generated_unit_contains_required_directives_and_marker() -> None:
    unit = generate_unit("/opt/NexTuxLinq Python/bin/py%thon")

    assert unit.startswith(f"{GENERATED_MARKER}\n")
    assert "Type=simple" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=3" in unit
    assert "TimeoutStopSec=10" in unit
    assert "After=default.target" not in unit
    assert "StartLimitIntervalSec=60" in unit
    assert "StartLimitBurst=5" in unit
    assert "WantedBy=default.target" in unit
    assert "StandardOutput=journal" in unit
    assert "StandardError=journal" in unit
    legacy_name = "nex" + "linq"
    assert (
        "Conflicts=nextuxlinq-display.service nextuxlinq-telemetry.service "
        f"{legacy_name}-display.service {legacy_name}-telemetry.service"
    ) in unit
    assert (
        'ExecStart="/opt/NexTuxLinq Python/bin/py%%thon" '
        "-m nextuxlinq.daemon --host 127.0.0.1 --port 8765"
    ) in unit


def test_openlinkhub_recovery_dropin_is_fixed_nonfatal_and_delayed() -> None:
    dropin = generate_openlinkhub_restart_dropin("/opt/NexTuxLinq/bin/python")

    assert dropin.startswith(f"{OPENLINKHUB_DROPIN_MARKER}\n")
    assert "[Service]" in dropin
    assert "ExecStartPost=/usr/bin/sleep 2" in dropin
    assert (
        "ExecStartPost=-/usr/bin/systemctl --user try-restart "
        f"{OPENLINKHUB_SERVICE_NAME}"
    ) in dropin
    assert (
        'ExecStartPost=-"/opt/NexTuxLinq/bin/python" '
        "-m nextuxlinq.openlinkhub_recovery"
    ) in dropin
    assert " restart " not in dropin
    assert "Requires=" not in dropin
    assert "BindsTo=" not in dropin
    assert "After=" not in dropin


def test_systemd_quoting_handles_spaces_percent_dollar_quote_and_backslash() -> None:
    quoted = systemd_quote_argument('/tmp/a b/py%$"\\thon')

    assert quoted == '"/tmp/a b/py%%$$\\"\\\\thon"'


def test_absolute_xdg_config_home_is_used() -> None:
    assert _resolve_config_root(
        {
            "XDG_CONFIG_HOME": "/absolute/config",
            "HOME": "/ignored",
        }
    ) == Path("/absolute/config")


def test_missing_xdg_config_home_uses_home() -> None:
    assert _resolve_config_root({"HOME": "/home/example"}) == Path(
        "/home/example/.config"
    )


@pytest.mark.parametrize(
    "configured",
    ["relative/path", "./relative", "../relative", "~/custom"],
)
def test_relative_xdg_config_home_uses_home(configured: str) -> None:
    assert _resolve_config_root(
        {
            "XDG_CONFIG_HOME": configured,
            "HOME": "/home/example",
        }
    ) == Path("/home/example/.config")


@pytest.mark.parametrize(
    "environment",
    [
        {"XDG_CONFIG_HOME": "relative/path"},
        {"XDG_CONFIG_HOME": "relative/path", "HOME": "relative/home"},
        {},
    ],
)
def test_invalid_home_fallback_is_rejected(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ServiceManagerError, match="HOME"):
        _resolve_config_root(environment)


def test_root_owned_trusted_ancestors_before_home_are_accepted(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    runner = RecordingRunner()
    manager = ServiceManager(
        environ={"HOME": str(home)},
        executable="/opt/nextuxlinq/python",
        runner=runner,
    )

    result = manager.install()

    assert result.success
    assert manager.unit_path.is_file()


def test_user_owned_home_anchor_and_descendants_are_required(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    manager = ServiceManager(
        environ={"HOME": str(home)},
        executable="/opt/nextuxlinq/python",
        runner=RecordingRunner(),
    )

    assert manager.install().success
    for path in (
        home,
        home / ".config",
        home / ".config" / "systemd",
        home / ".config" / "systemd" / "user",
    ):
        assert path.stat().st_uid == os.getuid()


def _owner_injection_for(
    target: Path,
    injected_owner: int,
):
    original_fstat = os.fstat
    target_identity = (target.stat().st_dev, target.stat().st_ino)

    def injected_fstat(descriptor: int):
        details = original_fstat(descriptor)
        if (details.st_dev, details.st_ino) == target_identity:
            values = list(details)
            values[4] = injected_owner
            return os.stat_result(values)
        return details

    return injected_fstat


def test_root_owned_home_anchor_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    manager = ServiceManager(
        environ={"HOME": str(home)},
        runner=RecordingRunner(),
    )
    system_owner = Path("/").stat().st_uid
    assert system_owner != os.getuid()

    with patch(
        "nextuxlinq.service_manager.os.fstat",
        side_effect=_owner_injection_for(home, system_owner),
    ):
        result = manager.install()

    assert not result.success
    assert "wrong owner" in (result.error or "")


def test_root_owned_xdg_config_home_anchor_is_rejected(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    system_owner = Path("/").stat().st_uid
    assert system_owner != os.getuid()

    with patch(
        "nextuxlinq.service_manager.os.fstat",
        side_effect=_owner_injection_for(manager.config_root, system_owner),
    ):
        result = manager.install()

    assert not result.success
    assert "wrong owner" in (result.error or "")
    assert runner.calls == []


def test_foreign_owned_configured_anchor_is_rejected(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)

    with patch(
        "nextuxlinq.service_manager.os.fstat",
        side_effect=_owner_injection_for(
            manager.config_root,
            os.getuid() + 1,
        ),
    ):
        result = manager.install()

    assert not result.success
    assert "wrong owner" in (result.error or "")
    assert runner.calls == []


def test_user_owned_final_directory_does_not_override_invalid_anchor(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    assert manager.unit_directory.stat().st_uid == os.getuid()
    system_owner = Path("/").stat().st_uid
    assert system_owner != os.getuid()

    with patch(
        "nextuxlinq.service_manager.os.fstat",
        side_effect=_owner_injection_for(manager.config_root, system_owner),
    ):
        result = manager.install()

    assert not result.success
    assert "wrong owner" in (result.error or "")
    assert runner.calls == []


def test_production_runner_uses_safe_subprocess_arguments() -> None:
    expected = completed(stdout="active\n")
    arguments = ["systemctl", "--user", "status", SERVICE_NAME, "--no-pager"]

    with patch(
        "nextuxlinq.service_manager.subprocess.run",
        return_value=expected,
    ) as run:
        result = _default_runner(arguments)

    assert result is expected
    positional, keywords = run.call_args
    assert positional == (arguments,)
    assert isinstance(positional[0], list)
    assert positional[0][0] == "systemctl"
    assert "--user" in positional[0]
    assert "sudo" not in positional[0]
    assert keywords == {
        "check": False,
        "capture_output": True,
        "text": True,
    }
    assert "shell" not in keywords
    assert "env" not in keywords


def test_production_runner_exception_is_sanitized_by_manager(
    tmp_path: Path,
) -> None:
    with patch(
        "nextuxlinq.service_manager.subprocess.run",
        side_effect=subprocess.SubprocessError("/private/transport"),
    ):
        manager = ServiceManager(
            environ={
                "HOME": str(tmp_path),
                "XDG_CONFIG_HOME": str(tmp_path / "config"),
            }
        )
        result = manager.control("restart")

    assert not result.success
    assert result.error == "Could not run systemctl to restart nextuxlinqd."
    assert "/private/" not in (result.error or "")


def test_install_uses_current_interpreter_and_creates_directories(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    runner = RecordingRunner()
    manager = ServiceManager(
        environ={"HOME": str(home)},
        runner=runner,
    )

    result = manager.install()

    assert result.success
    unit = manager.unit_path.read_text(encoding="utf-8")
    assert f"ExecStart={systemd_quote_argument(sys.executable)} " in unit
    assert manager.unit_directory.is_dir()
    assert manager.unit_path.stat().st_mode & 0o777 == 0o644


def test_install_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)

    result = manager.install()

    assert result.success
    assert manager.unit_path.read_text(encoding="utf-8") == generate_unit(
        manager.executable
    )
    assert not [
        path
        for path in manager.unit_directory.iterdir()
        if path.name.startswith(f".{SERVICE_NAME}.")
    ]


def test_atomic_write_failure_is_sanitized_and_cleans_temporary_file(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)

    with patch(
        "nextuxlinq.service_manager.os.fsync",
        side_effect=OSError("/private/fsync detail"),
    ):
        result = manager.install()

    assert not result.success
    assert "/private/" not in (result.error or "")
    assert not manager.unit_path.exists()
    assert not list(manager.unit_directory.glob(f".{SERVICE_NAME}.*.tmp"))
    assert runner.calls == []


class _FailingTextStream:
    def __init__(self, stream, phase: str) -> None:
        self.stream = stream
        self.phase = phase

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, exception_type, exception, traceback):
        return self.stream.__exit__(exception_type, exception, traceback)

    def write(self, value: str):
        if self.phase == "write":
            raise OSError("injected write failure")
        return self.stream.write(value)

    def flush(self) -> None:
        if self.phase == "flush":
            raise OSError("injected flush failure")
        self.stream.flush()

    def fileno(self) -> int:
        return self.stream.fileno()


@pytest.mark.parametrize(
    "phase",
    [
        "temporary-create",
        "fdopen",
        "write",
        "flush",
        "file-fsync",
        "chmod",
        "quarantine",
        "replacement",
    ],
)
def test_precommit_failures_preserve_existing_managed_unit(
    tmp_path: Path,
    phase: str,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    real_open = os.open
    real_fdopen = os.fdopen
    real_fchmod = os.fchmod
    real_fsync = os.fsync
    unit_descriptor: int | None = None

    def failing_open(path, flags, mode=0o777, *, dir_fd=None):
        if (
            phase == "temporary-create"
            and flags & os.O_CREAT
            and str(path).endswith(".tmp")
        ):
            raise OSError("injected temporary creation failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def failing_fdopen(descriptor, *arguments, **keywords):
        nonlocal unit_descriptor
        unit_descriptor = descriptor
        if phase == "fdopen":
            raise OSError("injected fdopen failure")
        stream = real_fdopen(descriptor, *arguments, **keywords)
        if phase in {"write", "flush"}:
            return _FailingTextStream(stream, phase)
        return stream

    patches = [
        patch("nextuxlinq.service_manager.os.open", side_effect=failing_open),
        patch("nextuxlinq.service_manager.os.fdopen", side_effect=failing_fdopen),
    ]
    if phase == "file-fsync":
        patches.append(
            patch(
                "nextuxlinq.service_manager.os.fsync",
                side_effect=lambda descriptor: (
                    (_ for _ in ()).throw(OSError("injected file fsync failure"))
                    if descriptor == unit_descriptor
                    else real_fsync(descriptor)
                ),
            )
        )
    if phase == "chmod":
        patches.append(
            patch(
                "nextuxlinq.service_manager.os.fchmod",
                side_effect=lambda descriptor, mode: (
                    (_ for _ in ()).throw(OSError("injected chmod failure"))
                    if descriptor == unit_descriptor
                    else real_fchmod(descriptor, mode)
                ),
            )
        )
    if phase == "quarantine":
        patches.append(
            patch.object(
                manager,
                "_quarantine_unit",
                side_effect=OSError("injected quarantine failure"),
            )
        )
    if phase == "replacement":
        patches.append(
            patch(
                "nextuxlinq.service_manager.os.link",
                side_effect=OSError("injected replacement failure"),
            )
        )

    entered = []
    try:
        for selected_patch in patches:
            entered.append(selected_patch)
            selected_patch.start()
        result = manager.install()
    finally:
        for selected_patch in reversed(entered):
            selected_patch.stop()

    assert not result.success
    assert "Could not" in (result.error or "")
    assert_managed_unit_preserved(
        manager,
        runner,
        original,
        original_mode,
    )


def test_fdopen_failure_closes_raw_descriptor_and_preserves_unit(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    descriptor_directory = Path("/proc/self/fd")
    before = len(list(descriptor_directory.iterdir()))

    with patch(
        "nextuxlinq.service_manager.os.fdopen",
        side_effect=OSError("injected fdopen failure"),
    ):
        result = manager.install()

    after = len(list(descriptor_directory.iterdir()))
    assert not result.success
    assert after == before
    assert_managed_unit_preserved(
        manager,
        runner,
        original,
        original_mode,
    )


def test_fdopen_primary_failure_survives_raw_close_failure(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    raw_descriptor: int | None = None
    original_close = os.close
    raw_close_attempts = 0

    def failing_fdopen(descriptor: int, *arguments, **keywords):
        nonlocal raw_descriptor
        raw_descriptor = descriptor
        raise OSError("primary fdopen failure")

    def close_then_fail(descriptor: int) -> None:
        nonlocal raw_close_attempts
        original_close(descriptor)
        if descriptor == raw_descriptor:
            raw_close_attempts += 1
            raise OSError("secondary close failure")

    with (
        patch(
            "nextuxlinq.service_manager.os.fdopen",
            side_effect=failing_fdopen,
        ),
        patch(
            "nextuxlinq.service_manager.os.close",
            side_effect=close_then_fail,
        ),
    ):
        result = manager.install()

    assert not result.success
    assert "Could not prepare" in (result.error or "")
    assert "secondary" not in (result.error or "")
    assert raw_close_attempts == 1
    assert_managed_unit_preserved(
        manager,
        runner,
        original,
        original_mode,
    )


@pytest.mark.parametrize("also_fail_close", [False, True])
def test_fdopen_primary_failure_survives_temporary_unlink_cleanup_failure(
    tmp_path: Path,
    also_fail_close: bool,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    raw_descriptor: int | None = None
    original_close = os.close
    original_unlink = os.unlink

    def failing_fdopen(descriptor: int, *arguments, **keywords):
        nonlocal raw_descriptor
        raw_descriptor = descriptor
        raise OSError("primary fdopen failure")

    def maybe_failing_close(descriptor: int) -> None:
        original_close(descriptor)
        if also_fail_close and descriptor == raw_descriptor:
            raise OSError("secondary close failure")

    def failing_temporary_unlink(path, *, dir_fd=None):
        if str(path).endswith(".tmp"):
            raise OSError("secondary unlink failure")
        return original_unlink(path, dir_fd=dir_fd)

    with (
        patch(
            "nextuxlinq.service_manager.os.fdopen",
            side_effect=failing_fdopen,
        ),
        patch(
            "nextuxlinq.service_manager.os.close",
            side_effect=maybe_failing_close,
        ),
        patch(
            "nextuxlinq.service_manager.os.unlink",
            side_effect=failing_temporary_unlink,
        ),
    ):
        result = manager.install()

    assert not result.success
    assert "Could not prepare" in (result.error or "")
    assert "retained temporary file" in (result.error or "")
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert len(list(manager.unit_directory.glob(f".{SERVICE_NAME}.*.tmp"))) == 1
    assert runner.calls == []


def test_postcommit_directory_sync_failure_reports_installed_state(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)

    original_fsync = os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("/private/directory detail")
        original_fsync(descriptor)

    with patch(
        "nextuxlinq.service_manager.os.fsync",
        side_effect=fail_directory_sync,
    ):
        result = manager.install()

    assert not result.success
    assert manager.unit_path.is_file()
    assert "may be installed" in result.output[0]
    assert "was installed" in (result.error or "")
    assert "/private/" not in (result.error or "")
    assert runner.calls == []


def test_replacement_directory_sync_failure_reports_new_unit_committed(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, _original_mode = managed_unit_state(manager)
    manager.executable = "/opt/NexTuxLinq Python/bin/new-python"
    original_fsync = os.fsync
    directory_calls = 0

    def fail_directory_sync(descriptor: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
        if directory_calls == 1:
            raise OSError("injected directory fsync failure")
        original_fsync(descriptor)

    with patch(
        "nextuxlinq.service_manager.os.fsync",
        side_effect=fail_directory_sync,
    ):
        result = manager.install()

    assert not result.success
    assert "may be installed" in result.output[0]
    assert "was installed" in (result.error or "")
    assert manager.unit_path.read_bytes() != original
    assert "new-python" in manager.unit_path.read_text(encoding="utf-8")
    assert not list(manager.unit_directory.glob(f".{SERVICE_NAME}.*"))
    assert runner.calls == []


def test_managed_unit_is_atomically_replaced(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)
    manager._write_unit_atomically(force=False)
    original = manager.unit_path.read_bytes()
    manager.executable = "/opt/NexTuxLinq Python/bin/replacement-python"

    manager._write_unit_atomically(force=False)

    assert manager.unit_path.read_bytes() != original
    assert "replacement-python" in manager.unit_path.read_text(encoding="utf-8")
    assert manager.unit_path.stat().st_mode & 0o777 == 0o644
    assert not list(manager.unit_directory.glob(f".{SERVICE_NAME}.*"))


def test_foreign_unit_requires_force_and_is_preserved(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    manager.unit_path.write_text("[Unit]\nDescription=Foreign\n", encoding="utf-8")
    original = manager.unit_path.read_bytes()

    result = manager.install()

    assert not result.success
    assert "--force" in (result.error or "")
    assert manager.unit_path.read_bytes() == original
    assert runner.calls == []


def test_force_replaces_safe_foreign_unit(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    manager.unit_path.write_text("[Unit]\nDescription=Foreign\n", encoding="utf-8")

    result = manager.install(force=True)

    assert result.success
    assert GENERATED_MARKER in manager.unit_path.read_text(encoding="utf-8")
    assert manager.unit_path.stat().st_mode & 0o777 == 0o644


def test_first_install_does_not_require_libc_renameat2(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)

    with patch.object(service_manager, "_RENAMEAT2", None):
        result = manager.install()

    assert result.success
    assert manager.unit_path.is_file()


def test_replacement_fails_safely_without_libc_renameat2(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)

    with patch.object(service_manager, "_RENAMEAT2", None):
        result = manager.install()

    assert not result.success
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert not any(call[2:3] == ["enable"] for call in runner.calls)


def test_uninstall_fails_safely_without_libc_renameat2(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)

    with patch.object(service_manager, "_RENAMEAT2", None):
        result = manager.uninstall()

    assert not result.success
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert runner.calls == []


@pytest.mark.parametrize("error_number", [errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP])
def test_renameat2_runtime_failures_are_sanitized_and_preserve_unit(
    tmp_path: Path,
    error_number: int,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)

    def unsupported_renameat2(*arguments) -> int:
        ctypes.set_errno(error_number)
        return -1

    with patch.object(
        service_manager,
        "_RENAMEAT2",
        unsupported_renameat2,
    ):
        result = manager.install()

    assert not result.success
    assert os.strerror(error_number) not in (result.error or "")
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert runner.calls == []


def test_symlinked_target_is_rejected_even_with_force(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    target = tmp_path / "foreign.service"
    target.write_text("[Unit]\n", encoding="utf-8")
    manager.unit_path.symlink_to(target)

    result = manager.install(force=True)

    assert not result.success
    assert "unsafe" in (result.error or "")
    assert target.read_text(encoding="utf-8") == "[Unit]\n"
    assert runner.calls == []


@pytest.mark.parametrize("unsafe_kind", ["symlink", "permissions"])
def test_unsafe_parent_path_is_rejected(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.config_root.mkdir(mode=0o755)
    symlink_target: Path | None = None
    if unsafe_kind == "symlink":
        symlink_target = tmp_path / "elsewhere"
        symlink_target.mkdir()
        (manager.config_root / "systemd").symlink_to(
            symlink_target,
            target_is_directory=True,
        )
    else:
        (manager.config_root / "systemd").mkdir(mode=0o777)
        (manager.config_root / "systemd").chmod(0o777)

    result = manager.install()

    assert not result.success
    assert "unsafe" in (result.error or "")
    assert runner.calls == []
    if symlink_target is not None:
        assert not (symlink_target / "user").exists()


def test_symlinked_ancestor_before_config_root_is_rejected(tmp_path: Path) -> None:
    owned_parent = tmp_path / "owned"
    owned_parent.mkdir(mode=0o700)
    redirected = tmp_path / "redirected"
    redirected.mkdir(mode=0o700)
    (owned_parent / "jump").symlink_to(redirected, target_is_directory=True)
    runner = RecordingRunner()
    manager = ServiceManager(
        environ={
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(owned_parent / "jump" / "config"),
        },
        runner=runner,
    )

    result = manager.install()

    assert not result.success
    assert "unsafe" in (result.error or "")
    assert not (redirected / "config").exists()
    assert runner.calls == []


def test_non_directory_ancestor_is_rejected(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.config_root.mkdir(mode=0o755)
    (manager.config_root / "systemd").write_text("not a directory", encoding="utf-8")

    result = manager.install()

    assert not result.success
    assert "unsafe" in (result.error or "")
    assert runner.calls == []


def test_foreign_owned_ancestor_metadata_is_rejected(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    target = (manager.config_root / "systemd").stat()
    original_fstat = os.fstat
    before = len(list(Path("/proc/self/fd").iterdir()))

    def injected_fstat(descriptor: int):
        details = original_fstat(descriptor)
        if (details.st_dev, details.st_ino) == (target.st_dev, target.st_ino):
            values = list(details)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return details

    with patch("nextuxlinq.service_manager.os.fstat", injected_fstat):
        result = manager.install()

    after = len(list(Path("/proc/self/fd").iterdir()))
    assert not result.success
    assert "wrong owner" in (result.error or "")
    assert after == before
    assert runner.calls == []


def test_foreign_owned_system_ancestor_is_rejected(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    original_fstat = os.fstat
    target = Path("/tmp").stat()

    def injected_fstat(descriptor: int):
        details = original_fstat(descriptor)
        if (details.st_dev, details.st_ino) == (target.st_dev, target.st_ino):
            values = list(details)
            values[4] = os.getuid() + 99
            return os.stat_result(values)
        return details

    with patch("nextuxlinq.service_manager.os.fstat", injected_fstat):
        result = manager.install()

    assert not result.success
    assert "wrong owner" in (result.error or "")
    assert runner.calls == []


def test_writable_nonsticky_system_ancestor_is_rejected(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    original_fstat = os.fstat
    target = Path("/tmp").stat()

    def injected_fstat(descriptor: int):
        details = original_fstat(descriptor)
        if (details.st_dev, details.st_ino) == (target.st_dev, target.st_ino):
            values = list(details)
            values[0] = stat.S_IFDIR | 0o777
            return os.stat_result(values)
        return details

    with patch("nextuxlinq.service_manager.os.fstat", injected_fstat):
        result = manager.install()

    assert not result.success
    assert "unsafe permissions" in (result.error or "")
    assert runner.calls == []


def test_open_directory_descriptor_survives_path_replacement(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)
    original_open = manager._open_unit_directory
    detached_directory = tmp_path / "original-open-directory"

    def open_then_replace(*, create: bool):
        descriptor = original_open(create=create)
        assert descriptor is not None
        manager.unit_directory.rename(detached_directory)
        manager.unit_directory.mkdir(mode=0o755, parents=True)
        return descriptor

    with patch.object(
        manager,
        "_open_unit_directory",
        side_effect=open_then_replace,
    ):
        manager._write_unit_atomically(force=False)

    assert (detached_directory / SERVICE_NAME).is_file()
    assert not manager.unit_path.exists()
    assert not list(detached_directory.glob(f".{SERVICE_NAME}.*"))
    assert not list(manager.unit_directory.iterdir())


def test_manager_lock_excludes_same_process_operation(tmp_path: Path) -> None:
    first, _ = manager_for(tmp_path)
    second = ServiceManager(
        environ=first.environ,
        executable=first.executable,
        runner=RecordingRunner(),
    )
    first_directory = first._open_unit_directory(create=True)
    second_directory = second._open_unit_directory(create=False)
    assert first_directory is not None
    assert second_directory is not None
    first_lock = first._acquire_manager_lock(first_directory)

    try:
        with pytest.raises(ServiceManagerError, match="in progress"):
            second._acquire_manager_lock(second_directory)
    finally:
        first._release_operation_descriptors(first_lock, first_directory)
        second._release_operation_descriptors(None, second_directory)

    lock = first.unit_directory / MANAGER_LOCK_NAME
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_manager_lock_excludes_cross_process_operation(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)
    directory_descriptor = manager._open_unit_directory(create=True)
    assert directory_descriptor is not None
    lock_descriptor = manager._acquire_manager_lock(directory_descriptor)
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    process = context.Process(
        target=try_manager_lock_in_child,
        args=(manager.environ, results),
    )

    try:
        process.start()
        process.join(timeout=5)
        assert process.exitcode == 0
        assert "in progress" in results.get(timeout=1)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        manager._release_operation_descriptors(
            lock_descriptor,
            directory_descriptor,
        )


def test_manager_lock_remains_held_through_systemctl_sequence(
    tmp_path: Path,
) -> None:
    manager, _ = manager_for(tmp_path)
    competing = ServiceManager(
        environ=manager.environ,
        executable=manager.executable,
        runner=RecordingRunner(),
    )
    exclusion_confirmed = False

    def callback(arguments: list[str]):
        nonlocal exclusion_confirmed
        if arguments[2:] == ["daemon-reload"]:
            directory = competing._open_unit_directory(create=False)
            assert directory is not None
            try:
                with pytest.raises(ServiceManagerError, match="in progress"):
                    competing._acquire_manager_lock(directory)
                exclusion_confirmed = True
            finally:
                competing._release_operation_descriptors(None, directory)
        if arguments[2:3] == ["disable"]:
            return completed(
                1,
                stderr=(
                    "Failed to disable unit: Unit file "
                    f"{arguments[-1]} does not exist."
                ),
            )
        if arguments[2:3] == ["is-active"]:
            return completed(stdout="active\n")
        return completed()

    manager.runner = RecordingRunner(callback)

    result = manager.install()

    assert result.success
    assert exclusion_confirmed


def test_symlinked_manager_lock_is_rejected(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    target = tmp_path / "foreign-lock"
    target.write_text("foreign", encoding="utf-8")
    (manager.unit_directory / MANAGER_LOCK_NAME).symlink_to(target)

    result = manager.install(force=True)

    assert not result.success
    assert "lock" in (result.error or "").casefold()
    assert target.read_text(encoding="utf-8") == "foreign"
    assert runner.calls == []


def test_replacement_retains_private_non_unit_backup(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    assert original_mode == 0o644
    manager.executable = "/opt/NexTuxLinq Python/bin/replacement"

    result = manager.install()

    assert result.success
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert not backups[0].name.endswith(".service")
    assert str(backups[0]) in "\n".join(result.output)
    assert "replacement" in manager.unit_path.read_text(encoding="utf-8")


def test_uninstall_retains_private_disabled_backup(tmp_path: Path) -> None:
    manager, _ = manager_for(tmp_path)
    original, _original_mode = managed_unit_state(manager)

    result = manager.uninstall()

    assert result.success
    assert not manager.unit_path.exists()
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert not backups[0].name.endswith(".service")
    assert str(backups[0]) in "\n".join(result.output)


def test_matching_backup_identity_succeeds_at_final_revalidation(
    tmp_path: Path,
) -> None:
    manager, _ = manager_for(tmp_path)
    managed_unit_state(manager)
    original_revalidate = manager._revalidate_backup_identity
    revalidations = 0

    def record_revalidation(
        directory_descriptor: int,
        backup_name: str,
        backup_descriptor: int,
    ) -> None:
        nonlocal revalidations
        revalidations += 1
        original_revalidate(
            directory_descriptor,
            backup_name,
            backup_descriptor,
        )

    with patch.object(
        manager,
        "_revalidate_backup_identity",
        side_effect=record_revalidation,
    ):
        result = manager.install()

    assert result.success
    assert revalidations == 1


def test_backup_substitution_after_open_prevents_replacement_commit(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, _original_mode = managed_unit_state(manager)
    original_revalidate = manager._revalidate_backup_identity
    foreign = b"[Unit]\nDescription=Foreign retained pathname\n"
    displaced_original: Path | None = None

    def substitute_then_revalidate(
        directory_descriptor: int,
        backup_name: str,
        backup_descriptor: int,
    ) -> None:
        nonlocal displaced_original
        backup = manager.unit_directory / backup_name
        displaced_original = manager.unit_directory / f"{backup_name}.original"
        backup.rename(displaced_original)
        backup.write_bytes(foreign)
        original_revalidate(
            directory_descriptor,
            backup_name,
            backup_descriptor,
        )

    with patch.object(
        manager,
        "_revalidate_backup_identity",
        side_effect=substitute_then_revalidate,
    ):
        result = manager.install()

    assert not result.success
    assert "manual inspection" in (result.error or "")
    assert not manager.unit_path.exists()
    assert displaced_original is not None
    assert displaced_original.read_bytes() == original
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == foreign
    assert list(manager.unit_directory.glob(f".{SERVICE_NAME}.*.tmp"))
    assert runner.calls == []


def test_backup_substitution_after_open_prevents_uninstall_finalization(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, _original_mode = managed_unit_state(manager)
    original_revalidate = manager._revalidate_backup_identity
    foreign = b"[Unit]\nDescription=Foreign retained pathname\n"
    displaced_original: Path | None = None

    def substitute_then_revalidate(
        directory_descriptor: int,
        backup_name: str,
        backup_descriptor: int,
    ) -> None:
        nonlocal displaced_original
        backup = manager.unit_directory / backup_name
        displaced_original = manager.unit_directory / f"{backup_name}.original"
        backup.rename(displaced_original)
        backup.write_bytes(foreign)
        original_revalidate(
            directory_descriptor,
            backup_name,
            backup_descriptor,
        )

    with patch.object(
        manager,
        "_revalidate_backup_identity",
        side_effect=substitute_then_revalidate,
    ):
        result = manager.uninstall()

    assert not result.success
    assert "manual inspection" in (result.error or "")
    assert not manager.unit_path.exists()
    assert displaced_original is not None
    assert displaced_original.read_bytes() == original
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == foreign
    assert runner.calls == [
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
    ]


def test_backup_substitution_after_validation_prevents_replacement(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, _original_mode = managed_unit_state(manager)
    original_open_verified = manager._open_verified_backup
    foreign = b"[Unit]\nDescription=Substituted foreign backup\n"
    displaced_original: Path | None = None

    def substitute_then_verify(
        directory_descriptor: int,
        backup_name: str,
        expected_identity: tuple[int, int],
        *,
        require_managed: bool,
    ):
        nonlocal displaced_original
        backup = manager.unit_directory / backup_name
        displaced_original = manager.unit_directory / f"{backup_name}.original"
        backup.rename(displaced_original)
        backup.write_bytes(foreign)
        return original_open_verified(
            directory_descriptor,
            backup_name,
            expected_identity,
            require_managed=require_managed,
        )

    with patch.object(
        manager,
        "_open_verified_backup",
        side_effect=substitute_then_verify,
    ):
        result = manager.install()

    assert not result.success
    assert "changed after validation" in (result.error or "")
    assert not manager.unit_path.exists()
    assert displaced_original is not None
    assert displaced_original.read_bytes() == original
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == foreign
    assert list(manager.unit_directory.glob(f".{SERVICE_NAME}.*.tmp"))
    assert runner.calls == []


def test_in_place_backup_mutation_after_validation_prevents_replacement(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    managed_unit_state(manager)
    original_open_verified = manager._open_verified_backup
    foreign = b"[Unit]\nDescription=In-place foreign content\n"

    def mutate_then_verify(
        directory_descriptor: int,
        backup_name: str,
        expected_identity: tuple[int, int],
        *,
        require_managed: bool,
    ):
        (manager.unit_directory / backup_name).write_bytes(foreign)
        return original_open_verified(
            directory_descriptor,
            backup_name,
            expected_identity,
            require_managed=require_managed,
        )

    with patch.object(
        manager,
        "_open_verified_backup",
        side_effect=mutate_then_verify,
    ):
        result = manager.install()

    assert not result.success
    assert "changed after validation" in (result.error or "")
    assert not manager.unit_path.exists()
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == foreign
    assert list(manager.unit_directory.glob(f".{SERVICE_NAME}.*.tmp"))
    assert runner.calls == []


def test_backup_substitution_after_validation_prevents_uninstall_finalization(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, _original_mode = managed_unit_state(manager)
    original_open_verified = manager._open_verified_backup
    foreign = b"[Unit]\nDescription=Substituted foreign backup\n"
    displaced_original: Path | None = None

    def substitute_then_verify(
        directory_descriptor: int,
        backup_name: str,
        expected_identity: tuple[int, int],
        *,
        require_managed: bool,
    ):
        nonlocal displaced_original
        backup = manager.unit_directory / backup_name
        displaced_original = manager.unit_directory / f"{backup_name}.original"
        backup.rename(displaced_original)
        backup.write_bytes(foreign)
        return original_open_verified(
            directory_descriptor,
            backup_name,
            expected_identity,
            require_managed=require_managed,
        )

    with patch.object(
        manager,
        "_open_verified_backup",
        side_effect=substitute_then_verify,
    ):
        result = manager.uninstall()

    assert not result.success
    assert "changed after validation" in (result.error or "")
    assert not manager.unit_path.exists()
    assert displaced_original is not None
    assert displaced_original.read_bytes() == original
    backups = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == foreign
    assert runner.calls == [
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
    ]


def test_foreign_substitution_before_replacement_is_not_deleted(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    managed_unit_state(manager)
    original_quarantine = manager._quarantine_unit
    foreign = b"[Unit]\nDescription=Concurrent foreign unit\n"

    def substitute_then_quarantine(directory_descriptor: int):
        manager.unit_path.unlink()
        manager.unit_path.write_bytes(foreign)
        return original_quarantine(directory_descriptor)

    with patch.object(
        manager,
        "_quarantine_unit",
        side_effect=substitute_then_quarantine,
    ):
        result = manager.install()

    assert not result.success
    assert "--force" in (result.error or "")
    assert manager.unit_path.read_bytes() == foreign
    assert not list(manager.unit_directory.glob(f".{SERVICE_NAME}.*"))
    assert runner.calls == []


def test_quarantine_validation_failure_restores_original(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)

    with patch.object(
        manager,
        "_inspect_named_unit",
        side_effect=ServiceManagerError("Injected quarantine validation failure."),
    ):
        result = manager.install()

    assert not result.success
    assert "validation failure" in (result.error or "")
    assert_managed_unit_preserved(
        manager,
        runner,
        original,
        original_mode,
    )


def test_successful_restoration_synchronizes_unit_directory(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    foreign = b"[Unit]\nDescription=Foreign\n"
    manager.unit_path.write_bytes(foreign)
    original_fsync = os.fsync
    synchronized_directories = 0

    def recording_fsync(descriptor: int) -> None:
        nonlocal synchronized_directories
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            synchronized_directories += 1
        original_fsync(descriptor)

    with patch(
        "nextuxlinq.service_manager.os.fsync",
        side_effect=recording_fsync,
    ):
        result = manager.install()

    assert not result.success
    assert "--force" in (result.error or "")
    assert manager.unit_path.read_bytes() == foreign
    assert synchronized_directories == 1
    assert runner.calls == []


def test_restored_unit_directory_sync_failure_is_distinguished(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    foreign = b"[Unit]\nDescription=Foreign\n"
    manager.unit_path.write_bytes(foreign)
    original_fsync = os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected restoration sync failure")
        original_fsync(descriptor)

    with patch(
        "nextuxlinq.service_manager.os.fsync",
        side_effect=fail_directory_fsync,
    ):
        result = manager.install()

    assert not result.success
    assert (
        "Refusing to overwrite a unit not managed by NexTuxLinq"
        in (result.error or "")
    )
    assert "Cleanup warning" in (result.error or "")
    assert (
        "restored unit could not be durably synchronized"
        in (result.error or "")
    )
    assert "injected restoration sync failure" not in (result.error or "")
    assert manager.unit_path.read_bytes() == foreign
    assert runner.calls == []


def test_replacement_primary_failure_survives_backup_close_failure(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    original_open_verified = manager._open_verified_backup
    original_close = os.close
    backup_descriptor: int | None = None
    backup_close_attempts = 0

    def capture_backup_descriptor(*arguments, **keywords):
        nonlocal backup_descriptor
        backup_descriptor = original_open_verified(*arguments, **keywords)
        return backup_descriptor

    def close_backup_once(descriptor: int) -> None:
        nonlocal backup_close_attempts
        original_close(descriptor)
        if descriptor == backup_descriptor and backup_close_attempts == 0:
            backup_close_attempts += 1
            raise OSError("/private/replacement-close-detail")

    with (
        patch.object(
            manager,
            "_open_verified_backup",
            side_effect=capture_backup_descriptor,
        ),
        patch.object(
            manager,
            "_revalidate_backup_identity",
            side_effect=ServiceManagerError(
                "Replacement identity validation failed."
            ),
        ),
        patch(
            "nextuxlinq.service_manager.os.close",
            side_effect=close_backup_once,
        ),
    ):
        result = manager.install()

    assert not result.success
    assert "Replacement identity validation failed" in (result.error or "")
    assert "Cleanup warning" in (result.error or "")
    assert (
        "replacement backup descriptor could not be closed cleanly"
        in (result.error or "")
    )
    assert "/private/" not in (result.error or "")
    assert backup_close_attempts == 1
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert runner.calls == []


def test_uninstall_primary_failure_survives_backup_close_failure(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    original, original_mode = managed_unit_state(manager)
    original_open_verified = manager._open_verified_backup
    original_close = os.close
    backup_descriptor: int | None = None
    backup_close_attempts = 0

    def capture_backup_descriptor(*arguments, **keywords):
        nonlocal backup_descriptor
        backup_descriptor = original_open_verified(*arguments, **keywords)
        return backup_descriptor

    def close_backup_once(descriptor: int) -> None:
        nonlocal backup_close_attempts
        original_close(descriptor)
        if descriptor == backup_descriptor and backup_close_attempts == 0:
            backup_close_attempts += 1
            raise OSError("/private/uninstall-close-detail")

    with (
        patch.object(
            manager,
            "_open_verified_backup",
            side_effect=capture_backup_descriptor,
        ),
        patch.object(
            manager,
            "_revalidate_backup_identity",
            side_effect=ServiceManagerError(
                "Uninstall identity validation failed."
            ),
        ),
        patch(
            "nextuxlinq.service_manager.os.close",
            side_effect=close_backup_once,
        ),
    ):
        result = manager.uninstall()

    assert not result.success
    assert "Uninstall identity validation failed" in (result.error or "")
    assert "Cleanup warning" in (result.error or "")
    assert (
        "uninstall backup descriptor could not be closed cleanly"
        in (result.error or "")
    )
    assert "/private/" not in (result.error or "")
    assert backup_close_attempts == 1
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode
    assert runner.calls == [
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
    ]


def test_failed_quarantine_restoration_is_reported_truthfully(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    foreign = b"[Unit]\nDescription=Foreign\n"
    manager.unit_path.write_bytes(foreign)

    with patch.object(
        manager,
        "_restore_quarantine",
        side_effect=OSError("injected restoration failure"),
    ):
        result = manager.install()

    assert not result.success
    assert "remains as" in (result.error or "")
    assert not manager.unit_path.exists()
    quarantines = list(
        manager.unit_directory.glob(".nextuxlinq-service-backup-*.bak")
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == foreign
    assert runner.calls == []


def test_uninstall_foreign_substitution_is_restored_not_deleted(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    managed_unit_state(manager)
    original_quarantine = manager._quarantine_unit
    quarantine_calls = 0
    foreign = b"[Unit]\nDescription=Concurrent foreign unit\n"

    def substitute_before_final_quarantine(directory_descriptor: int):
        nonlocal quarantine_calls
        quarantine_calls += 1
        if quarantine_calls == 2:
            manager.unit_path.unlink()
            manager.unit_path.write_bytes(foreign)
        return original_quarantine(directory_descriptor)

    with patch.object(
        manager,
        "_quarantine_unit",
        side_effect=substitute_before_final_quarantine,
    ):
        result = manager.uninstall()

    assert not result.success
    assert "substituted foreign" in (result.error or "")
    assert manager.unit_path.read_bytes() == foreign
    assert not list(manager.unit_directory.glob(f".{SERVICE_NAME}.*"))
    assert runner.calls == [
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
    ]


@pytest.mark.parametrize(
    "message",
    [
        f"Unit {SERVICE_NAME} not found.",
        f"Unit {SERVICE_NAME} could not be found.",
        (
            "Failed to disable unit: Unit file "
            f"{SERVICE_NAME} does not exist."
        ),
    ],
)
def test_missing_unit_detection_accepts_only_known_exact_messages(
    message: str,
) -> None:
    assert ServiceManager._unit_is_missing(
        completed(1, stderr=message),
        SERVICE_NAME,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Failed to connect to bus: No such file or directory",
        "systemctl executable not found",
        "not found",
        "Permission denied",
        "Transport endpoint is not connected",
        "malformed output",
        "Unit another.service not found.",
    ],
)
def test_missing_unit_detection_rejects_unrelated_errors(message: str) -> None:
    assert not ServiceManager._unit_is_missing(
        completed(1, stderr=message),
        SERVICE_NAME,
    )


@pytest.mark.parametrize(
    "additional_error",
    [
        "Failed to connect to bus: No such file or directory",
        "Permission denied",
        "Transport endpoint is not connected",
        "systemctl executable not found",
        "Unit another.service not found.",
    ],
)
def test_missing_unit_detection_rejects_mixed_diagnostics(
    additional_error: str,
) -> None:
    result = completed(
        1,
        stdout=f"Unit {SERVICE_NAME} not found.\n",
        stderr=f"{additional_error}\n",
    )

    assert not ServiceManager._unit_is_missing(result, SERVICE_NAME)


def test_uninstall_preserves_unit_when_user_bus_is_unavailable(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        lambda arguments: completed(
            1,
            stderr="Failed to connect to bus: No such file or directory",
        )
    )
    manager, _ = manager_for(tmp_path, runner=runner)
    original, original_mode = managed_unit_state(manager)

    result = manager.uninstall()

    assert not result.success
    assert "preserved" in (result.error or "")
    assert manager.unit_path.read_bytes() == original
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == original_mode


def test_systemctl_executable_missing_is_sanitized(tmp_path: Path) -> None:
    def missing_executable(arguments: list[str]):
        raise FileNotFoundError("/private/systemctl")

    manager, _ = manager_for(
        tmp_path,
        runner=RecordingRunner(missing_executable),
    )
    manager._write_unit_atomically(force=False)

    result = manager.control("status")

    assert not result.success
    assert result.error == "User-service status could not be inspected."
    assert "/private/" not in (result.error or "")


def test_install_systemctl_order_and_success_output(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)

    result = manager.install()

    assert result.success
    assert runner.calls == [
        ["systemctl", "--user", "daemon-reload"],
        *[
            ["systemctl", "--user", "disable", "--now", helper]
            for helper in HELPER_SERVICES
        ],
        ["systemctl", "--user", "enable", "--now", SERVICE_NAME],
        ["systemctl", "--user", "is-active", SERVICE_NAME],
    ]
    assert "installed successfully" in result.output[0]
    assert "Service state: active" in result.output
    assert f"GUI: {GUI_ADDRESS}" in result.output


def test_missing_legacy_and_renamed_helpers_are_tolerated(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)

    result = manager.install()

    assert result.success
    disabled = [
        call[-1] for call in runner.calls if call[2:4] == ["disable", "--now"]
    ]
    assert disabled == list(HELPER_SERVICES)


@pytest.mark.parametrize(
    ("failed_step", "expected_fragment"),
    [
        ("reload", "daemon-reload failed"),
        ("helper", "competing helper"),
        ("enable", "could not complete"),
        ("state", "did not report it active"),
    ],
)
def test_partial_systemctl_failures_are_reported_truthfully(
    tmp_path: Path,
    failed_step: str,
    expected_fragment: str,
) -> None:
    def callback(arguments: list[str]):
        command = arguments[2:]
        if failed_step == "reload" and command == ["daemon-reload"]:
            return completed(1, stderr="/private/manager detail")
        if (
            failed_step == "helper"
            and command == ["disable", "--now", HELPER_SERVICES[0]]
        ):
            return completed(1, stderr="/private/policy detail")
        if command[:2] == ["disable", "--now"]:
            return completed(
                1,
                stderr=(
                    "Failed to disable unit: Unit file "
                    f"{command[-1]} does not exist."
                ),
            )
        if failed_step == "enable" and command == [
            "enable",
            "--now",
            SERVICE_NAME,
        ]:
            return completed(1, stderr="/private/start detail")
        if command == ["is-active", SERVICE_NAME]:
            if failed_step == "state":
                return completed(3, stdout="failed\n")
            return completed(stdout="active\n")
        return completed()

    runner = RecordingRunner(callback)
    manager, _ = manager_for(tmp_path, runner=runner)

    result = manager.install()

    assert not result.success
    assert expected_fragment in (result.error or "")
    assert "/private/" not in (result.error or "")
    assert manager.unit_path.is_file()
    if failed_step in {"reload", "helper"}:
        assert not any(call[2:3] == ["enable"] for call in runner.calls)


def test_managed_uninstall_preserves_configuration_and_does_not_enable_legacy(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager._write_unit_atomically(force=False)
    profile = manager.config_root / "nextuxlinq" / "config.json"
    profile.parent.mkdir(mode=0o700)
    profile.write_text('{"profile": "preserve"}\n', encoding="utf-8")
    original = profile.read_bytes()

    result = manager.uninstall()

    assert result.success
    assert not manager.unit_path.exists()
    assert profile.read_bytes() == original
    assert runner.calls == [
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert not any(helper in call for call in runner.calls for helper in HELPER_SERVICES)
    assert not any("enable" in call for call in runner.calls)


def test_uninstall_refuses_foreign_unit_without_stopping_it(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(mode=0o755, parents=True)
    manager.unit_path.write_text("[Unit]\nDescription=Foreign\n", encoding="utf-8")

    result = manager.uninstall()

    assert not result.success
    assert "not managed" in (result.error or "")
    assert manager.unit_path.exists()
    assert runner.calls == []


def test_uninstall_preserves_managed_unit_when_stop_fails(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        lambda arguments: completed(1, stderr="/private/bus failure")
    )
    manager, _ = manager_for(tmp_path, runner=runner)
    manager._write_unit_atomically(force=False)

    result = manager.uninstall()

    assert not result.success
    assert "preserved" in (result.error or "")
    assert "/private/" not in (result.error or "")
    assert manager.unit_path.exists()


def test_uninstall_reports_reload_failure_after_safe_removal(
    tmp_path: Path,
) -> None:
    def callback(arguments: list[str]):
        if arguments[2:] == ["daemon-reload"]:
            return completed(1, stderr="/private/reload")
        return completed()

    manager, _ = manager_for(tmp_path, runner=RecordingRunner(callback))
    manager._write_unit_atomically(force=False)

    result = manager.uninstall()

    assert not result.success
    assert not manager.unit_path.exists()
    assert "disabled backup" in (result.error or "")
    assert "/private/" not in (result.error or "")


def test_uninstall_directory_sync_failure_reports_removed_state(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager._write_unit_atomically(force=False)

    original_fsync = os.fsync
    directory_calls = 0

    def fail_final_directory_sync(descriptor: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls == 2:
                raise OSError("/private/uninstall sync")
        original_fsync(descriptor)

    with patch(
        "nextuxlinq.service_manager.os.fsync",
        side_effect=fail_final_directory_sync,
    ):
        result = manager.uninstall()

    assert not result.success
    assert not manager.unit_path.exists()
    assert "unit was removed" in (result.error or "")
    assert "daemon-reload was not run" in (result.error or "")
    assert "/private/" not in (result.error or "")
    assert runner.calls == [
        ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
    ]


@pytest.mark.parametrize(
    "action", ["enable", "disable", "start", "stop", "restart"],
)
def test_service_control_actions_target_only_daemon(
    tmp_path: Path,
    action: str,
) -> None:
    runner = RecordingRunner(lambda arguments: completed())
    manager, runner = manager_for(tmp_path, runner=runner)

    result = manager.control(action)

    assert result.success
    assert runner.calls == [
        ["systemctl", "--user", action, SERVICE_NAME],
    ]


def test_status_is_read_only_and_preserves_useful_output(tmp_path: Path) -> None:
    def status_result(arguments: list[str]):
        if arguments[2] == "is-enabled":
            return completed(stdout="enabled\n")
        if arguments[2] == "is-active":
            return completed(stdout="active\n")
        if arguments[2] == "show":
            return completed(stdout="MainPID=1234\nResult=success\n")
        raise AssertionError(arguments)

    runner = RecordingRunner(status_result)
    manager, _ = manager_for(tmp_path, runner=runner)
    manager._write_unit_atomically(force=False)

    result = manager.control("status")

    assert result.success
    assert "Service installed: yes" in result.output
    assert "Managed unit: yes" in result.output
    assert "Autostart enabled: yes" in result.output
    assert "Service active: yes" in result.output
    assert "Service state: active" in result.output
    assert "Current PID: 1234" in result.output
    assert "Last failure: none" in result.output
    assert runner.calls == [
        ["systemctl", "--user", "is-enabled", SERVICE_NAME],
        ["systemctl", "--user", "is-active", SERVICE_NAME],
        [
            "systemctl", "--user", "show", SERVICE_NAME,
            "--property=MainPID", "--property=Result",
        ],
    ]
    assert manager.unit_path.exists()


@pytest.mark.parametrize(
    ("enabled_state", "active_state", "result_state", "enabled", "active", "failure"),
    [
        ("enabled", "inactive", "success", True, False, None),
        (
            "disabled", "failed", "exit-code", False, False,
            "The service process exited with an error.",
        ),
    ],
)
def test_structured_status_handles_inactive_and_failed_units(
    tmp_path: Path,
    enabled_state: str,
    active_state: str,
    result_state: str,
    enabled: bool,
    active: bool,
    failure: str | None,
) -> None:
    def status_result(arguments: list[str]):
        return {
            "is-enabled": completed(
                0 if enabled else 1, stdout=f"{enabled_state}\n"
            ),
            "is-active": completed(
                0 if active else 3, stdout=f"{active_state}\n"
            ),
            "show": completed(stdout=f"MainPID=0\nResult={result_state}\n"),
        }[arguments[2]]

    manager, _ = manager_for(tmp_path, runner=RecordingRunner(status_result))
    manager._write_unit_atomically(force=False)

    status = manager.inspect_status(runtime_mode="manual")

    assert status.enabled is enabled
    assert status.active is active
    assert status.service_state == active_state
    assert status.pid is None
    assert status.last_failure_summary == failure
    assert status.runtime_mode == "manual"


def test_status_cache_bounds_systemctl_inspection(tmp_path: Path) -> None:
    ticks = iter((10.0, 11.0, 16.0))
    manager, runner = manager_for(tmp_path)
    manager._write_unit_atomically(force=False)
    provider = CachedUserServiceStatusProvider(
        manager,
        runtime_mode="systemd_user_service",
        ttl=5,
        monotonic=lambda: next(ticks),
    )

    first = provider()
    second = provider()
    third = provider()

    assert first is second
    assert third is not second
    assert first.runtime_mode == "systemd_user_service"
    assert len(runner.calls) == 6


def test_cached_status_sanitizes_systemctl_failure(tmp_path: Path) -> None:
    def fail(arguments: list[str]):
        raise OSError("/private/systemctl failure")

    manager, _ = manager_for(
        tmp_path,
        runner=RecordingRunner(fail),
    )
    manager._write_unit_atomically(force=False)
    provider = CachedUserServiceStatusProvider(
        manager,
        runtime_mode="systemd_user_service",
    )

    status = provider()

    assert status.runtime_mode == "systemd_user_service"
    assert status.installed is None
    assert status.enabled is None
    assert status.active is None
    assert status.openlinkhub_recovery_enabled is None
    assert status.last_failure_summary == (
        "User-service status is temporarily unavailable."
    )
    assert "/private" not in (status.last_failure_summary or "")


def test_runtime_mode_requires_a_systemd_invocation_identifier() -> None:
    assert detect_runtime_mode({}) == "manual"
    assert detect_runtime_mode({"INVOCATION_ID": "not-systemd"}) == "manual"
    assert detect_runtime_mode({"INVOCATION_ID": "a" * 32}) == (
        "systemd_user_service"
    )


def test_logs_action_only_identifies_read_only_journal_commands(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    result = manager.control("logs")
    assert result.success
    assert result.output == (
        f"journalctl --user -u {SERVICE_NAME} -n 100 --no-pager",
        f"journalctl --user -u {SERVICE_NAME} -f",
    )
    assert runner.calls == []


def test_control_failure_is_sanitized_and_nonzero(tmp_path: Path) -> None:
    runner = RecordingRunner(
        lambda arguments: completed(5, stderr="/private/systemctl detail")
    )
    manager, _ = manager_for(tmp_path, runner=runner)

    result = manager.control("restart")

    assert not result.success
    assert "exit 5" in (result.error or "")
    assert "/private/" not in (result.error or "")


def test_openlinkhub_recovery_enable_status_disable_is_idempotent(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)

    enabled = manager.openlinkhub_restart("enable")
    enabled_again = manager.openlinkhub_restart("enable")
    status = manager.openlinkhub_restart("status")

    assert enabled.success and enabled_again.success and status.success
    assert status.output == ("OpenLinkHub startup recovery: enabled.",)
    assert manager.openlinkhub_dropin_path.read_text(encoding="utf-8") == (
        generate_openlinkhub_restart_dropin(manager.executable)
    )
    assert stat.S_IMODE(manager.openlinkhub_dropin_path.stat().st_mode) == 0o644
    assert stat.S_IMODE(
        manager.openlinkhub_dropin_path.parent.stat().st_mode
    ) == 0o700
    assert runner.calls == [["systemctl", "--user", "daemon-reload"]]

    unrelated = manager.openlinkhub_dropin_path.parent / "unrelated.conf"
    unrelated.write_text("[Service]\nEnvironment=EXAMPLE=1\n", encoding="utf-8")
    disabled = manager.openlinkhub_restart("disable")
    disabled_again = manager.openlinkhub_restart("disable")
    status = manager.openlinkhub_restart("status")

    assert disabled.success and disabled_again.success and status.success
    assert status.output == ("OpenLinkHub startup recovery: disabled.",)
    assert not manager.openlinkhub_dropin_path.exists()
    assert unrelated.read_text(encoding="utf-8") == (
        "[Service]\nEnvironment=EXAMPLE=1\n"
    )
    assert runner.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_openlinkhub_recovery_status_never_invokes_systemctl(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)

    result = manager.openlinkhub_restart("status")

    assert result.success
    assert result.output == ("OpenLinkHub startup recovery: disabled.",)
    assert runner.calls == []
    assert not manager.unit_directory.exists()


def test_openlinkhub_recovery_rejects_symlinked_dropin_path(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(parents=True)
    dropin_directory = manager.unit_directory / OPENLINKHUB_DROPIN_DIRECTORY
    dropin_directory.mkdir()
    outside = tmp_path / "outside.conf"
    outside.write_text("outside", encoding="utf-8")
    (dropin_directory / OPENLINKHUB_DROPIN_NAME).symlink_to(outside)

    result = manager.openlinkhub_restart("enable")

    assert not result.success
    assert "unsafe" in (result.error or "")
    assert outside.read_text(encoding="utf-8") == "outside"
    assert runner.calls == []


def test_openlinkhub_recovery_rejects_symlinked_dropin_directory(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)
    manager.unit_directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (manager.unit_directory / OPENLINKHUB_DROPIN_DIRECTORY).symlink_to(outside)

    result = manager.openlinkhub_restart("enable")

    assert not result.success
    assert "unsafe" in (result.error or "")
    assert list(outside.iterdir()) == []
    assert runner.calls == []


def test_openlinkhub_recovery_atomic_write_failure_leaves_no_target(
    tmp_path: Path,
) -> None:
    manager, runner = manager_for(tmp_path)

    with patch.object(
        manager,
        "_create_temporary_unit",
        side_effect=OSError("private atomic detail"),
    ):
        result = manager.openlinkhub_restart("enable")

    assert not result.success
    assert "private atomic detail" not in (result.error or "")
    assert not manager.openlinkhub_dropin_path.exists()
    assert runner.calls == []


def test_openlinkhub_recovery_refuses_unmanaged_file(tmp_path: Path) -> None:
    manager, runner = manager_for(tmp_path)
    manager.openlinkhub_dropin_path.parent.mkdir(parents=True)
    manager.openlinkhub_dropin_path.write_text(
        "[Service]\nExecStartPost=/bin/false\n",
        encoding="utf-8",
    )

    enable = manager.openlinkhub_restart("enable")
    disable = manager.openlinkhub_restart("disable")

    assert not enable.success and not disable.success
    assert manager.openlinkhub_dropin_path.read_text(encoding="utf-8") == (
        "[Service]\nExecStartPost=/bin/false\n"
    )
    assert runner.calls == []


def test_openlinkhub_recovery_reload_failure_is_reported_after_commit(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(lambda arguments: completed(3))
    manager, _ = manager_for(tmp_path, runner=runner)

    result = manager.openlinkhub_restart("enable")

    assert not result.success
    assert result.output == (
        "OpenLinkHub startup recovery file was installed.",
    )
    assert result.error == "systemctl daemon-reload failed."
    assert manager.openlinkhub_dropin_path.is_file()
    assert runner.calls == [["systemctl", "--user", "daemon-reload"]]


def test_default_unit_has_no_openlinkhub_hook() -> None:
    unit = generate_unit("/usr/bin/python3")

    assert "OpenLinkHub" not in unit
    assert "ExecStartPost" not in unit


@pytest.mark.parametrize(
    ("arguments", "operation", "success", "expected_code"),
    [
        (["service", "install"], "install", True, 0),
        (["service", "install", "--force"], "install", False, 1),
        (["service", "uninstall"], "uninstall", True, 0),
        (["service", "start"], "control", True, 0),
        (["service", "stop"], "control", True, 0),
        (["service", "restart"], "control", True, 0),
        (["service", "enable"], "control", True, 0),
        (["service", "disable"], "control", True, 0),
        (["service", "logs"], "control", True, 0),
        (["service", "status"], "control", False, 1),
        (
            ["service", "openlinkhub-restart", "enable"],
            "openlinkhub_restart",
            True,
            0,
        ),
        (
            ["service", "openlinkhub-restart", "disable"],
            "openlinkhub_restart",
            True,
            0,
        ),
        (
            ["service", "openlinkhub-restart", "status"],
            "openlinkhub_restart",
            False,
            1,
        ),
    ],
)
def test_service_cli_exit_codes_without_hid_lock(
    arguments: list[str],
    operation: str,
    success: bool,
    expected_code: int,
) -> None:
    manager = SimpleNamespace()
    result = ServiceOperationResult(
        success,
        ("useful output",),
        None if success else "sanitized failure",
    )
    manager.install = lambda *, force: result
    manager.uninstall = lambda: result
    manager.control = lambda action: result
    manager.openlinkhub_restart = lambda action: result

    with (
        patch("nextuxlinq.cli.ServiceManager", return_value=manager),
        patch("nextuxlinq.cli.WriterLock.from_environment") as writer_lock,
        patch("sys.argv", ["nextuxlinqctl", *arguments]),
    ):
        code = cli.main()

    assert code == expected_code
    writer_lock.assert_not_called()
    if operation == "install":
        expected_force = "--force" in arguments
        # Re-run through a mock only to verify argparse passed the force flag.
        with (
            patch(
                "nextuxlinq.cli.ServiceManager",
                return_value=SimpleNamespace(
                    install=lambda *, force: (
                        ServiceOperationResult(force == expected_force)
                    )
                ),
            ),
            patch("sys.argv", ["nextuxlinqctl", *arguments]),
        ):
            assert cli.main() == 0


def test_service_template_has_no_repository_specific_paths() -> None:
    source = (
        REPOSITORY / "src" / "nextuxlinq" / "service_manager.py"
    ).read_text(encoding="utf-8")
    template = (
        REPOSITORY / "systemd" / "nextuxlinqd.service"
    ).read_text(encoding="utf-8")

    checkout = "~/nex" + "linq-linux"
    assert checkout not in source
    assert checkout not in template
    assert "/home/" not in template
    assert GENERATED_MARKER in template
    assert "nextuxlinq.daemon" in template
    assert "Type=simple" in template
    assert "Restart=on-failure" in template
    assert "TimeoutStopSec=10" in template
    assert all(service in template for service in HELPER_SERVICES)


def test_service_operations_leave_codex_directory_untouched(
    tmp_path: Path,
) -> None:
    codex = REPOSITORY / ".codex"
    before = sorted(
        (path.relative_to(codex), path.stat().st_size)
        for path in codex.rglob("*")
        if path.is_file()
    )
    manager, _ = manager_for(tmp_path)

    assert manager.install().success
    assert manager.uninstall().success

    after = sorted(
        (path.relative_to(codex), path.stat().st_size)
        for path in codex.rglob("*")
        if path.is_file()
    )
    assert after == before
