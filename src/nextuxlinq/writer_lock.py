"""Advisory single-writer coordination for NexTuxLinq processes."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from pathlib import Path
from typing import Mapping

CANONICAL_RUNTIME_NAMESPACE = "nextuxlinq"
LEGACY_RUNTIME_NAMESPACE = "nexlinq"


class WriterLockError(RuntimeError):
    """Base error for NexTuxLinq writer-lock operations."""


class WriterLockHeldError(WriterLockError):
    """Raised when another NexTuxLinq process owns the writer lock."""


def _close_descriptor(descriptor: int) -> OSError | None:
    """Close a descriptor, retrying once while retaining the first error."""
    first_error: OSError | None = None
    for _ in range(2):
        try:
            os.close(descriptor)
            return first_error
        except OSError as error:
            if first_error is None:
                first_error = error
    return first_error


def _ensure_private_directory(
    path: Path,
    owner_uid: int,
    *,
    label: str = "NexTuxLinq",
) -> None:
    """Create or validate one private, non-symlink runtime directory."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode):
        raise WriterLockError(f"Unsafe {label} runtime path: {path}")
    if details.st_uid != owner_uid:
        raise WriterLockError(f"{label} runtime path has the wrong owner: {path}")
    os.chmod(path, 0o700, follow_symlinks=False)


def _resolve_runtime_directory(
    namespace: str,
    label: str,
    environ: Mapping[str, str] | None = None,
    *,
    fallback_root: Path | None = None,
    user_id: int | None = None,
) -> Path:
    """Resolve one private runtime namespace without requiring real user paths."""
    environment = os.environ if environ is None else environ
    configured = environment.get("XDG_RUNTIME_DIR")
    owner_uid = os.getuid()
    if configured and Path(configured).is_absolute():
        root = Path(configured)
        if root.is_symlink():
            raise WriterLockError("XDG_RUNTIME_DIR must not be a symbolic link.")
        directory = root / namespace
        _ensure_private_directory(directory, owner_uid, label=label)
        return directory
    else:
        base = Path(tempfile.gettempdir()) if fallback_root is None else fallback_root
        base_details = base.lstat()
        if stat.S_ISLNK(base_details.st_mode) or not stat.S_ISDIR(
            base_details.st_mode
        ):
            raise WriterLockError("The runtime fallback root is unsafe.")
        uid = os.getuid() if user_id is None else user_id
        root = base / f"{namespace}-runtime-{uid}"
    _ensure_private_directory(root, owner_uid, label=label)
    directory = root / namespace
    _ensure_private_directory(directory, owner_uid, label=label)
    return directory


def resolve_runtime_directory(
    environ: Mapping[str, str] | None = None,
    *,
    fallback_root: Path | None = None,
    user_id: int | None = None,
) -> Path:
    """Resolve the canonical NexTuxLinq runtime directory."""
    return _resolve_runtime_directory(
        CANONICAL_RUNTIME_NAMESPACE,
        "NexTuxLinq",
        environ,
        fallback_root=fallback_root,
        user_id=user_id,
    )


def resolve_legacy_runtime_directory(
    environ: Mapping[str, str] | None = None,
    *,
    fallback_root: Path | None = None,
    user_id: int | None = None,
) -> Path:
    """Resolve the legacy runtime directory used for migration locking."""
    return _resolve_runtime_directory(
        LEGACY_RUNTIME_NAMESPACE,
        "legacy NexLinq",
        environ,
        fallback_root=fallback_root,
        user_id=user_id,
    )


def _release_descriptor(descriptor: int) -> BaseException | None:
    """Release and close one lock descriptor without skipping either step."""
    unlock_error: BaseException | None = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except BaseException as error:
        unlock_error = error
    close_error = _close_descriptor(descriptor)
    return unlock_error if unlock_error is not None else close_error


class WriterLock:
    """Advisory lock used by every NexTuxLinq HID writer.

    Environment-derived locks acquire the legacy namespace first and the
    canonical namespace second. The legacy lock is temporary rename-migration
    compatibility; direct path instances remain useful as single-lock test
    fixtures.
    """

    def __init__(
        self,
        path: Path,
        *,
        legacy_path: Path | None = None,
        owner: str = "NexTuxLinq writer",
    ) -> None:
        self.path = Path(path)
        self.legacy_path = (
            None if legacy_path is None else Path(legacy_path)
        )
        self.owner = owner
        self._descriptor: int | None = None
        self._legacy_descriptor: int | None = None

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        fallback_root: Path | None = None,
        user_id: int | None = None,
        owner: str = "NexTuxLinq writer",
    ) -> WriterLock:
        legacy_directory = resolve_legacy_runtime_directory(
            environ, fallback_root=fallback_root, user_id=user_id
        )
        directory = resolve_runtime_directory(
            environ, fallback_root=fallback_root, user_id=user_id
        )
        return cls(
            directory / "matrix900.lock",
            legacy_path=legacy_directory / "matrix900.lock",
            owner=owner,
        )

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def _acquire_path(
        self,
        path: Path,
        *,
        namespace_label: str,
        write_owner: bool,
    ) -> int:
        """Acquire one hardened lock path and return its open descriptor."""
        directory_descriptor: int | None = None
        descriptor: int | None = None
        lock_acquired = False
        primary_error: BaseException | None = None
        try:
            directory_descriptor = os.open(
                path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            directory_details = os.fstat(directory_descriptor)
            if (
                not stat.S_ISDIR(directory_details.st_mode)
                or directory_details.st_uid != os.getuid()
            ):
                raise WriterLockError("The NexTuxLinq lock directory is unsafe.")
            os.fchmod(directory_descriptor, 0o700)
            descriptor = os.open(
                path.name,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
                raise WriterLockError("The NexTuxLinq writer lock file is unsafe.")
            if write_owner:
                os.fchmod(descriptor, 0o600)
            elif stat.S_IMODE(details.st_mode) & 0o077:
                raise WriterLockError(
                    "The legacy NexLinq writer lock file has unsafe permissions."
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True
            if write_owner:
                os.ftruncate(descriptor, 0)
                os.write(
                    descriptor,
                    f"pid={os.getpid()} owner={self.owner}\n".encode("utf-8"),
                )
        except BlockingIOError as error:
            primary_error = WriterLockHeldError(
                "Another NexTuxLinq writer or legacy NexLinq writer already "
                f"owns the Matrix900 lock ({namespace_label} namespace)."
            )
            primary_error.__cause__ = error
        except BaseException as error:
            primary_error = error

        directory_close_error = (
            _close_descriptor(directory_descriptor)
            if directory_descriptor is not None
            else None
        )
        rollback = primary_error is not None or directory_close_error is not None
        lock_cleanup_error: BaseException | None = None
        if rollback and descriptor is not None:
            self._descriptor = None
            if lock_acquired:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except BaseException as error:
                    lock_cleanup_error = error
            close_error = _close_descriptor(descriptor)
            if lock_cleanup_error is None:
                lock_cleanup_error = close_error

        if primary_error is not None:
            raise primary_error
        if directory_close_error is not None:
            error = WriterLockError(
                "Could not finalize the NexTuxLinq lock directory descriptor."
            )
            error.__cause__ = directory_close_error
            raise error
        if lock_cleanup_error is not None:
            error = WriterLockError(
                "Could not roll back the NexTuxLinq writer lock descriptor."
            )
            error.__cause__ = lock_cleanup_error
            raise error
        if descriptor is None or not lock_acquired:
            raise WriterLockError("Could not acquire the NexTuxLinq writer lock.")
        return descriptor

    def acquire(self) -> None:
        """Acquire legacy then canonical locks without waiting."""
        if self.acquired:
            return
        legacy_descriptor: int | None = None
        try:
            if self.legacy_path is not None:
                legacy_descriptor = self._acquire_path(
                    self.legacy_path,
                    namespace_label="legacy",
                    write_owner=False,
                )
            descriptor = self._acquire_path(
                self.path,
                namespace_label="canonical",
                write_owner=True,
            )
        except BaseException as primary_error:
            if legacy_descriptor is not None:
                cleanup_error = _release_descriptor(legacy_descriptor)
                if cleanup_error is not None:
                    primary_error.add_note(
                        "The legacy writer lock reported an error during rollback."
                    )
            raise
        self._legacy_descriptor = legacy_descriptor
        self._descriptor = descriptor

    def release(self) -> None:
        """Release canonical then legacy locks; stable lock files remain."""
        descriptor = self._descriptor
        legacy_descriptor = self._legacy_descriptor
        self._descriptor = None
        self._legacy_descriptor = None
        first_error: BaseException | None = None
        if descriptor is not None:
            first_error = _release_descriptor(descriptor)
        if legacy_descriptor is not None:
            legacy_error = _release_descriptor(legacy_descriptor)
            if first_error is None:
                first_error = legacy_error
        if first_error is not None:
            raise first_error

    def __enter__(self) -> WriterLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
