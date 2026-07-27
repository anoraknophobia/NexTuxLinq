"""Tests for advisory NexLinq single-writer coordination."""

import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nexlinq.writer_lock import (
    WriterLock,
    WriterLockHeldError,
    WriterLockError,
    resolve_runtime_directory,
)


def test_first_writer_acquires_second_is_refused_and_release_works(
    tmp_path: Path,
) -> None:
    path = tmp_path / "matrix900.lock"
    first = WriterLock(path, owner="first")
    second = WriterLock(path, owner="second")

    first.acquire()
    assert first.acquired
    with pytest.raises(WriterLockHeldError):
        second.acquire()

    first.release()
    second.acquire()
    assert second.acquired
    second.release()


def test_missing_runtime_directory_uses_private_fallback(tmp_path: Path) -> None:
    directory = resolve_runtime_directory(
        {},
        fallback_root=tmp_path,
        user_id=1234,
    )

    assert directory == tmp_path / "nexlinq-runtime-1234" / "nexlinq"
    assert directory.stat().st_mode & 0o777 == 0o700


def test_relative_runtime_directory_uses_fallback(tmp_path: Path) -> None:
    lock = WriterLock.from_environment(
        {"XDG_RUNTIME_DIR": "relative"},
        fallback_root=tmp_path,
        user_id=7,
    )
    assert lock.path == (
        tmp_path / "nexlinq-runtime-7" / "nexlinq" / "matrix900.lock"
    )


def test_writer_lock_excludes_another_process(tmp_path: Path) -> None:
    path = tmp_path / "matrix900.lock"
    first = WriterLock(path)
    first.acquire()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "from nexlinq.writer_lock import WriterLock, WriterLockHeldError; "
                    f"lock=WriterLock(Path({str(path)!r})); "
                    "\ntry: lock.acquire()"
                    "\nexcept WriterLockHeldError: raise SystemExit(23)"
                ),
            ],
            check=False,
        )
    finally:
        first.release()

    assert result.returncode == 23


def test_fallback_rejects_symlinked_component(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "nexlinq-runtime-1").symlink_to(target, target_is_directory=True)

    with pytest.raises(WriterLockError, match="Unsafe NexLinq runtime path"):
        resolve_runtime_directory({}, fallback_root=tmp_path, user_id=1)


def test_fallback_rejects_wrong_owner_without_chmod(tmp_path: Path) -> None:
    runtime = tmp_path / "nexlinq-runtime-1"
    runtime.mkdir()
    original_lstat = Path.lstat

    def fake_lstat(path: Path):
        details = original_lstat(path)
        if path == runtime:
            return SimpleNamespace(st_mode=details.st_mode, st_uid=os.getuid() + 1)
        return details

    with (
        patch.object(Path, "lstat", fake_lstat),
        patch("os.chmod") as chmod,
        pytest.raises(WriterLockError, match="wrong owner"),
    ):
        resolve_runtime_directory({}, fallback_root=tmp_path, user_id=1)

    chmod.assert_not_called()


def test_existing_fallback_permissions_are_restricted(tmp_path: Path) -> None:
    runtime = tmp_path / "nexlinq-runtime-1"
    directory = runtime / "nexlinq"
    directory.mkdir(parents=True)
    runtime.chmod(0o777)
    directory.chmod(0o777)

    resolved = resolve_runtime_directory({}, fallback_root=tmp_path, user_id=1)

    assert resolved == directory
    assert runtime.stat().st_mode & 0o777 == 0o700
    assert directory.stat().st_mode & 0o777 == 0o700


def test_lock_open_remains_relative_to_validated_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "runtime"
    moved = tmp_path / "validated-runtime"
    attacker = tmp_path / "attacker"
    directory.mkdir()
    attacker.mkdir()
    original_open = os.open
    replaced = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == directory and dir_fd is None and not replaced:
            replaced = True
            directory.rename(moved)
            directory.symlink_to(attacker, target_is_directory=True)
        return descriptor

    lock = WriterLock(directory / "matrix900.lock")
    with patch("os.open", racing_open):
        lock.acquire()
        lock.release()

    assert (moved / "matrix900.lock").is_file()
    assert not (attacker / "matrix900.lock").exists()


def test_lock_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    parent = tmp_path / "runtime"
    parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError):
        WriterLock(parent / "matrix900.lock").acquire()


def test_directory_descriptor_is_closed_when_lock_open_fails(
    tmp_path: Path,
) -> None:
    lock_target = tmp_path / "target"
    lock_target.touch()
    (tmp_path / "matrix900.lock").symlink_to(lock_target)
    descriptors_before = len(os.listdir("/proc/self/fd"))

    with pytest.raises(OSError):
        WriterLock(tmp_path / "matrix900.lock").acquire()

    assert len(os.listdir("/proc/self/fd")) == descriptors_before


@pytest.mark.parametrize("unsafe_field", ["directory-owner", "lock-type"])
def test_opened_lock_resources_reject_unsafe_metadata_and_close_descriptors(
    tmp_path: Path,
    unsafe_field: str,
) -> None:
    original_fstat = os.fstat
    calls = 0

    def unsafe_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        details = original_fstat(descriptor)
        if unsafe_field == "directory-owner" and calls == 1:
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_uid=os.getuid() + 1,
            )
        if unsafe_field == "lock-type" and calls == 2:
            return SimpleNamespace(
                st_mode=0o040700,
                st_uid=os.getuid(),
            )
        return details

    descriptors_before = len(os.listdir("/proc/self/fd"))
    with (
        patch("os.fstat", unsafe_fstat),
        pytest.raises(WriterLockError, match="unsafe"),
    ):
        WriterLock(tmp_path / "matrix900.lock").acquire()

    assert len(os.listdir("/proc/self/fd")) == descriptors_before


def _exercise_close_failure(
    tmp_path: Path,
    *,
    fail_directory_close: bool,
    fail_lock_close: bool,
    operation_failure: bool,
) -> BaseException:
    original_open = os.open
    original_close = os.close
    descriptors: dict[str, int] = {}
    failed_once: set[int] = set()

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_DIRECTORY:
            descriptors["directory"] = descriptor
        elif dir_fd is not None:
            descriptors["lock"] = descriptor
        return descriptor

    def failing_close(descriptor: int) -> None:
        should_fail = (
            fail_directory_close
            and descriptor == descriptors.get("directory")
        ) or (fail_lock_close and descriptor == descriptors.get("lock"))
        if should_fail and descriptor not in failed_once:
            failed_once.add(descriptor)
            raise OSError("injected descriptor close failure")
        original_close(descriptor)

    lock = WriterLock(tmp_path / "matrix900.lock")
    descriptors_before = len(os.listdir("/proc/self/fd"))
    patches = [
        patch("os.open", tracking_open),
        patch("os.close", failing_close),
    ]
    if operation_failure:
        patches.append(
            patch("os.ftruncate", side_effect=RuntimeError("primary failure"))
        )

    with patches[0], patches[1], patches[2] if operation_failure else nullcontext():
        with pytest.raises(BaseException) as captured:
            lock.acquire()

    assert not lock.acquired
    assert descriptors.keys() == {"directory", "lock"}
    assert descriptors["directory"] in failed_once or not fail_directory_close
    assert descriptors["lock"] in failed_once or not fail_lock_close
    assert len(os.listdir("/proc/self/fd")) == descriptors_before

    replacement = WriterLock(tmp_path / "matrix900.lock")
    replacement.acquire()
    replacement.release()
    return captured.value


def test_directory_close_failure_does_not_skip_lock_cleanup(
    tmp_path: Path,
) -> None:
    error = _exercise_close_failure(
        tmp_path,
        fail_directory_close=True,
        fail_lock_close=False,
        operation_failure=True,
    )
    assert isinstance(error, RuntimeError)
    assert str(error) == "primary failure"


def test_lock_close_failure_preserves_primary_error_and_releases_flock(
    tmp_path: Path,
) -> None:
    error = _exercise_close_failure(
        tmp_path,
        fail_directory_close=False,
        fail_lock_close=True,
        operation_failure=True,
    )
    assert isinstance(error, RuntimeError)
    assert str(error) == "primary failure"


def test_both_close_failures_preserve_primary_error_and_release_flock(
    tmp_path: Path,
) -> None:
    error = _exercise_close_failure(
        tmp_path,
        fail_directory_close=True,
        fail_lock_close=True,
        operation_failure=True,
    )
    assert isinstance(error, RuntimeError)
    assert str(error) == "primary failure"


def test_successful_lock_is_rolled_back_when_directory_cleanup_fails(
    tmp_path: Path,
) -> None:
    error = _exercise_close_failure(
        tmp_path,
        fail_directory_close=True,
        fail_lock_close=False,
        operation_failure=False,
    )
    assert isinstance(error, WriterLockError)
    assert "finalize" in str(error)
