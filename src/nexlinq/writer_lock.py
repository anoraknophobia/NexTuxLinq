"""Advisory single-writer coordination for NexLinq processes."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from pathlib import Path
from typing import Mapping


class WriterLockError(RuntimeError):
    """Base error for NexLinq writer-lock operations."""


class WriterLockHeldError(WriterLockError):
    """Raised when another NexLinq process owns the writer lock."""


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


def _ensure_private_directory(path: Path, owner_uid: int) -> None:
    """Create or validate one private, non-symlink runtime directory."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode):
        raise WriterLockError(f"Unsafe NexLinq runtime path: {path}")
    if details.st_uid != owner_uid:
        raise WriterLockError(f"NexLinq runtime path has the wrong owner: {path}")
    os.chmod(path, 0o700, follow_symlinks=False)


def resolve_runtime_directory(
    environ: Mapping[str, str] | None = None,
    *,
    fallback_root: Path | None = None,
    user_id: int | None = None,
) -> Path:
    """Resolve a private runtime directory without requiring real user paths."""
    environment = os.environ if environ is None else environ
    configured = environment.get("XDG_RUNTIME_DIR")
    owner_uid = os.getuid()
    if configured and Path(configured).is_absolute():
        root = Path(configured)
        if root.is_symlink():
            raise WriterLockError("XDG_RUNTIME_DIR must not be a symbolic link.")
        _ensure_private_directory(root / "nexlinq", owner_uid)
        return root / "nexlinq"
    else:
        base = Path(tempfile.gettempdir()) if fallback_root is None else fallback_root
        base_details = base.lstat()
        if stat.S_ISLNK(base_details.st_mode) or not stat.S_ISDIR(
            base_details.st_mode
        ):
            raise WriterLockError("The runtime fallback root is unsafe.")
        uid = os.getuid() if user_id is None else user_id
        root = base / f"nexlinq-runtime-{uid}"
    _ensure_private_directory(root, owner_uid)
    directory = root / "nexlinq"
    _ensure_private_directory(directory, owner_uid)
    return directory


class WriterLock:
    """Process-wide advisory lock used by every NexLinq HID writer."""

    def __init__(self, path: Path, *, owner: str = "NexLinq writer") -> None:
        self.path = Path(path)
        self.owner = owner
        self._descriptor: int | None = None

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        fallback_root: Path | None = None,
        user_id: int | None = None,
        owner: str = "NexLinq writer",
    ) -> WriterLock:
        directory = resolve_runtime_directory(
            environ, fallback_root=fallback_root, user_id=user_id
        )
        return cls(directory / "matrix900.lock", owner=owner)

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def acquire(self) -> None:
        """Acquire the lock without waiting for another writer."""
        if self.acquired:
            return
        directory_descriptor: int | None = None
        descriptor: int | None = None
        lock_acquired = False
        primary_error: BaseException | None = None
        try:
            directory_descriptor = os.open(
                self.path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            directory_details = os.fstat(directory_descriptor)
            if (
                not stat.S_ISDIR(directory_details.st_mode)
                or directory_details.st_uid != os.getuid()
            ):
                raise WriterLockError("The NexLinq lock directory is unsafe.")
            os.fchmod(directory_descriptor, 0o700)
            descriptor = os.open(
                self.path.name,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
                raise WriterLockError("The NexLinq writer lock file is unsafe.")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True
            os.ftruncate(descriptor, 0)
            os.write(
                descriptor,
                f"pid={os.getpid()} owner={self.owner}\n".encode("utf-8"),
            )
        except BlockingIOError as error:
            primary_error = WriterLockHeldError(
                "Another NexLinq writer already owns the Matrix900 lock."
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
                "Could not finalize the NexLinq lock directory descriptor."
            )
            error.__cause__ = directory_close_error
            raise error
        if lock_cleanup_error is not None:
            error = WriterLockError(
                "Could not roll back the NexLinq writer lock descriptor."
            )
            error.__cause__ = lock_cleanup_error
            raise error
        if descriptor is None or not lock_acquired:
            raise WriterLockError("Could not acquire the NexLinq writer lock.")
        self._descriptor = descriptor

    def release(self) -> None:
        """Release a held lock; the stable lock file remains in place."""
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> WriterLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
