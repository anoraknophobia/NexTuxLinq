"""Safe systemd user-service management for nextuxlinqd."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

SERVICE_NAME = "nextuxlinqd.service"
GUI_ADDRESS = "http://127.0.0.1:8765"
GENERATED_MARKER = "# X-NexTuxLinq-Managed: nextuxlinqctl-service-v1"
MAX_UNIT_BYTES = 256 * 1024
MANAGER_LOCK_NAME = ".nextuxlinq-service-manager.lock"
HELPER_SERVICES = (
    "nextuxlinq-display.service",
    "nextuxlinq-telemetry.service",
    "nexlinq-display.service",
    "nexlinq-telemetry.service",
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_FILE_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = getattr(_LIBC, "renameat2", None)
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    _RENAMEAT2.restype = ctypes.c_int


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CompletedProcessLike]


@dataclass(frozen=True, slots=True)
class ServiceOperationResult:
    """Sanitized outcome suitable for direct CLI presentation."""

    success: bool
    output: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _UnitInspection:
    state: str
    identity: tuple[int, int] | None = None
    mode: int | None = None


class ServiceManagerError(RuntimeError):
    """Sanitized service-management failure."""


class UnitInstallError(ServiceManagerError):
    """Unit-file installation failure with explicit commit-point state."""

    def __init__(self, message: str, *, committed: bool) -> None:
        super().__init__(message)
        self.committed = committed


class RetainedArtifactError(ServiceManagerError):
    """Failure that intentionally leaves managed-directory artifacts behind."""

    def __init__(
        self,
        message: str,
        *names: str,
        retain_all: bool = False,
    ) -> None:
        super().__init__(message)
        self.names = tuple(names)
        self.retain_all = retain_all


def _default_runner(arguments: list[str]) -> CompletedProcessLike:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
    )


def _resolve_config_root(environ: Mapping[str, str]) -> Path:
    return _resolve_config_paths(environ)[0]


def _resolve_config_paths(environ: Mapping[str, str]) -> tuple[Path, Path]:
    """Return the configuration root and its required user-owned anchor."""
    configured = environ.get("XDG_CONFIG_HOME")
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path, path
    home = environ.get("HOME")
    if not home:
        raise ServiceManagerError(
            "HOME is not set and no absolute XDG_CONFIG_HOME is available."
        )
    home_path = Path(home)
    if not home_path.is_absolute():
        raise ServiceManagerError("HOME must be an absolute path.")
    return home_path / ".config", home_path


def _rename_noreplace(
    source_directory: int,
    source: str,
    destination_directory: int,
    destination: str,
) -> None:
    """Atomically rename without replacing an existing destination."""
    if _RENAMEAT2 is None:
        raise OSError(
            errno.ENOSYS,
            "renameat2 is required for safe user-unit management",
        )
    result = _RENAMEAT2(
        source_directory,
        os.fsencode(source),
        destination_directory,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def systemd_quote_argument(value: str) -> str:
    """Quote one literal systemd command argument."""
    if not value or any(character in value for character in ("\0", "\n", "\r")):
        raise ServiceManagerError("The Python executable path is not usable.")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def generate_unit(executable: str) -> str:
    """Generate the installed user unit for one absolute Python executable."""
    if not Path(executable).is_absolute():
        raise ServiceManagerError("The Python executable path must be absolute.")
    quoted_executable = systemd_quote_argument(executable)
    conflicts = " ".join(HELPER_SERVICES)
    return (
        f"{GENERATED_MARKER}\n"
        "# Generated by NexTuxLinq; install and remove with nextuxlinqctl.\n"
        "[Unit]\n"
        "Description=NexTuxLinq Matrix900 Controller\n"
        "After=default.target\n"
        f"Conflicts={conflicts}\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={quoted_executable} -m nextuxlinq.daemon "
        "--host 127.0.0.1 --port 8765\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "TimeoutStopSec=10\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


class ServiceManager:
    """Install and control the nextuxlinqd systemd user unit."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        executable: str | None = None,
        runner: Runner = _default_runner,
    ) -> None:
        self.environ = dict(os.environ if environ is None else environ)
        self.executable = sys.executable if executable is None else executable
        self.runner = runner
        self.config_root, self.user_anchor = _resolve_config_paths(self.environ)
        self.unit_directory = self.config_root / "systemd" / "user"
        self.unit_path = self.unit_directory / SERVICE_NAME

    @staticmethod
    def _close_once(descriptor: int | None) -> bool:
        if descriptor is None:
            return True
        try:
            os.close(descriptor)
        except OSError:
            return False
        return True

    @staticmethod
    def _primary_message(error: BaseException, fallback: str) -> str:
        if isinstance(error, ServiceManagerError):
            return str(error)
        return fallback

    @classmethod
    def _append_cleanup_warning(
        cls,
        error: BaseException,
        *,
        fallback: str,
        warning: str,
    ) -> ServiceManagerError:
        message = (
            f"Primary operation failed: {cls._primary_message(error, fallback)} "
            f"Cleanup warning: {warning}."
        )
        if isinstance(error, RetainedArtifactError):
            return RetainedArtifactError(
                message,
                *error.names,
                retain_all=error.retain_all,
            )
        return ServiceManagerError(message)

    @staticmethod
    def _validate_open_directory(
        details: os.stat_result,
        *,
        system_owner: int,
        user_tree: bool,
        require_user: bool,
    ) -> bool:
        if not stat.S_ISDIR(details.st_mode):
            raise ServiceManagerError(
                "The systemd user-unit path contains an unsafe component."
        )
        user_id = os.getuid()
        if details.st_uid not in {system_owner, user_id}:
            raise ServiceManagerError(
                "The systemd user-unit path has the wrong owner."
            )
        if (user_tree or require_user) and details.st_uid != user_id:
            raise ServiceManagerError(
                "The systemd user-unit path has the wrong owner."
            )
        permissions = stat.S_IMODE(details.st_mode)
        shared_sticky_directory = (
            not user_tree
            and details.st_uid == system_owner
            and bool(permissions & stat.S_ISVTX)
            and bool(permissions & 0o002)
        )
        if permissions & 0o022 and not shared_sticky_directory:
            raise ServiceManagerError(
                "The systemd user-unit path has unsafe permissions."
            )
        return user_tree or details.st_uid == user_id

    def _open_unit_directory(self, *, create: bool) -> int | None:
        components = (*self.config_root.parts[1:], "systemd", "user")
        anchor_components = self.user_anchor.parts[1:]
        if not self.config_root.is_absolute() or any(
            component in {"", ".", ".."} for component in components
        ) or (
            not self.user_anchor.is_absolute()
            or not anchor_components
            or tuple(components[: len(anchor_components)])
            != tuple(anchor_components)
        ):
            raise ServiceManagerError(
                "The systemd user-unit path is not a safe absolute path."
            )
        anchor_index = len(anchor_components) - 1
        current_descriptor: int | None = None
        try:
            current_descriptor = os.open("/", _DIRECTORY_FLAGS)
        except OSError as error:
            raise ServiceManagerError(
                "Could not safely open the systemd user-unit path."
            ) from error
        try:
            system_owner = os.fstat(current_descriptor).st_uid
            user_tree = False
            for index, component in enumerate(components):
                child_descriptor: int | None = None
                try:
                    child_descriptor = os.open(
                        component,
                        _DIRECTORY_FLAGS,
                        dir_fd=current_descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        descriptor_to_close = current_descriptor
                        current_descriptor = None
                        if not self._close_once(descriptor_to_close):
                            raise ServiceManagerError(
                                "The systemd user-unit path was not opened "
                                "because descriptor cleanup failed."
                            )
                        return None
                    parent_details = os.fstat(current_descriptor)
                    if parent_details.st_uid != os.getuid():
                        raise ServiceManagerError(
                            "Refusing to create a systemd user-unit directory "
                            "inside a foreign-owned path."
                        )
                    try:
                        os.mkdir(
                            component,
                            mode=0o755,
                            dir_fd=current_descriptor,
                        )
                    except FileExistsError:
                        pass
                    child_descriptor = os.open(
                        component,
                        _DIRECTORY_FLAGS,
                        dir_fd=current_descriptor,
                    )
                try:
                    details = os.fstat(child_descriptor)
                    user_tree = self._validate_open_directory(
                        details,
                        system_owner=system_owner,
                        user_tree=user_tree,
                        require_user=index >= anchor_index,
                    )
                except BaseException:
                    child_closed = self._close_once(child_descriptor)
                    if not child_closed:
                        raise ServiceManagerError(
                            "The systemd user-unit path is unsafe and descriptor "
                            "cleanup could not be confirmed."
                        )
                    raise
                descriptor_to_close = current_descriptor
                current_descriptor = None
                if not self._close_once(descriptor_to_close):
                    child_close_failed = not self._close_once(child_descriptor)
                    cleanup = (
                        " Child descriptor cleanup also could not be confirmed."
                        if child_close_failed
                        else ""
                    )
                    raise ServiceManagerError(
                        "The systemd user-unit path descriptor could not be "
                        f"closed.{cleanup}"
                    )
                current_descriptor = child_descriptor
            return current_descriptor
        except ServiceManagerError as primary_error:
            descriptor_to_close = current_descriptor
            current_descriptor = None
            if not self._close_once(descriptor_to_close):
                raise ServiceManagerError(
                    f"{primary_error} Cleanup warning: directory descriptor "
                    "cleanup could not be confirmed."
                ) from primary_error
            raise
        except OSError as error:
            descriptor_to_close = current_descriptor
            current_descriptor = None
            close_failed = not self._close_once(descriptor_to_close)
            cleanup = (
                " Descriptor cleanup could not be confirmed."
                if close_failed
                else ""
            )
            raise ServiceManagerError(
                "The systemd user-unit path contains an unsafe component."
                f"{cleanup}"
            ) from error

    @staticmethod
    def _inspect_named_unit(
        directory_descriptor: int,
        name: str,
        *,
        allow_absent: bool = False,
    ) -> _UnitInspection:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                name,
                _FILE_READ_FLAGS,
                dir_fd=directory_descriptor,
            )
        except FileNotFoundError:
            if allow_absent:
                return _UnitInspection("absent")
            raise ServiceManagerError(
                "The quarantined nextuxlinqd user unit disappeared."
            )
        except OSError as error:
            raise ServiceManagerError(
                "The nextuxlinqd user-unit path is unsafe."
            ) from error
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) & 0o022
            ):
                raise ServiceManagerError(
                    "The existing nextuxlinqd user unit is unsafe."
                )
            if details.st_size > MAX_UNIT_BYTES:
                raise ServiceManagerError(
                    "The existing nextuxlinqd user unit is too large."
                )
            remaining = MAX_UNIT_BYTES + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            contents = b"".join(chunks)
            if len(contents) > MAX_UNIT_BYTES:
                raise ServiceManagerError(
                    "The existing nextuxlinqd user unit is too large."
                )
            marker = GENERATED_MARKER.encode("utf-8")
            state = "managed" if marker in contents.splitlines() else "foreign"
            return _UnitInspection(
                state,
                (details.st_dev, details.st_ino),
                stat.S_IMODE(details.st_mode),
            )
        finally:
            if not ServiceManager._close_once(descriptor):
                primary_error = sys.exception()
                primary_message = (
                    str(primary_error)
                    if isinstance(primary_error, ServiceManagerError)
                    else "Could not safely inspect the nextuxlinqd user unit."
                )
                raise ServiceManagerError(
                    f"{primary_message} Descriptor cleanup could not be confirmed."
                ) from primary_error

    @staticmethod
    def _temporary_name(kind: str) -> str:
        return f".{SERVICE_NAME}.{secrets.token_hex(16)}.{kind}"

    @staticmethod
    def _backup_name() -> str:
        return f".nextuxlinq-service-backup-{secrets.token_hex(16)}.bak"

    @classmethod
    def _create_temporary_unit(cls, directory_descriptor: int, contents: str) -> str:
        temporary_name: str | None = None
        raw_descriptor: int | None = None
        try:
            for _attempt in range(128):
                candidate = cls._temporary_name("tmp")
                try:
                    raw_descriptor = os.open(
                        candidate,
                        _FILE_CREATE_FLAGS,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_name is None or raw_descriptor is None:
                raise OSError(
                    errno.EEXIST,
                    "could not allocate a unique unit temporary file",
                )
            try:
                stream = os.fdopen(raw_descriptor, "w", encoding="utf-8")
            except BaseException as primary_error:
                close_failed = not cls._close_once(raw_descriptor)
                raw_descriptor = None
                unlink_failed = False
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except OSError:
                    unlink_failed = True
                warnings: list[str] = []
                if close_failed:
                    warnings.append(
                        "temporary descriptor cleanup could not be confirmed"
                    )
                if unlink_failed:
                    warnings.append(
                        f"retained temporary file: {temporary_name}"
                    )
                message = "Could not prepare the nextuxlinqd unit temporary file."
                if warnings:
                    message = f"{message} Cleanup warning: {'; '.join(warnings)}."
                raise ServiceManagerError(message) from primary_error
            raw_descriptor = None
            with stream:
                stream.write(contents)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            return temporary_name
        except ServiceManagerError:
            raise
        except BaseException as primary_error:
            close_failed = not cls._close_once(raw_descriptor)
            raw_descriptor = None
            unlink_failed = False
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except OSError:
                    unlink_failed = True
            warnings = []
            if close_failed:
                warnings.append("temporary descriptor cleanup could not be confirmed")
            if unlink_failed and temporary_name is not None:
                warnings.append(f"retained temporary file: {temporary_name}")
            message = "Could not prepare the nextuxlinqd unit temporary file."
            if warnings:
                message = f"{message} Cleanup warning: {'; '.join(warnings)}."
            raise ServiceManagerError(message) from primary_error

    @classmethod
    def _acquire_manager_lock(cls, directory_descriptor: int) -> int:
        descriptor: int | None = None
        created = False
        flags = (
            os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            try:
                descriptor = os.open(
                    MANAGER_LOCK_NAME,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_descriptor,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(
                    MANAGER_LOCK_NAME,
                    flags,
                    dir_fd=directory_descriptor,
                )
            if created:
                os.fchmod(descriptor, 0o600)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise ServiceManagerError(
                    "The NexTuxLinq service-manager lock file is unsafe."
                )
            if created:
                os.fsync(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ServiceManagerError(
                    "Another NexTuxLinq service-management operation is in "
                    "progress; retry after it finishes."
                ) from error
            return descriptor
        except BaseException as primary_error:
            close_failed = not cls._close_once(descriptor)
            if close_failed:
                primary_message = (
                    str(primary_error)
                    if isinstance(primary_error, ServiceManagerError)
                    else "Could not acquire the NexTuxLinq service-manager lock."
                )
                raise ServiceManagerError(
                    f"{primary_message} Lock descriptor cleanup could not be "
                    "confirmed."
                ) from primary_error
            if isinstance(primary_error, ServiceManagerError):
                raise
            raise ServiceManagerError(
                "Could not safely acquire the NexTuxLinq service-manager lock."
            ) from primary_error

    @classmethod
    def _release_operation_descriptors(
        cls,
        lock_descriptor: int | None,
        directory_descriptor: int | None,
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if lock_descriptor is not None:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                warnings.append("manager lock release could not be confirmed")
            if not cls._close_once(lock_descriptor):
                warnings.append("manager lock descriptor cleanup could not be confirmed")
        if not cls._close_once(directory_descriptor):
            warnings.append("unit-directory descriptor cleanup could not be confirmed")
        return tuple(warnings)

    @classmethod
    def _quarantine_unit(cls, directory_descriptor: int) -> str | None:
        for _attempt in range(128):
            quarantine_name = cls._backup_name()
            try:
                _rename_noreplace(
                    directory_descriptor,
                    SERVICE_NAME,
                    directory_descriptor,
                    quarantine_name,
                )
                return quarantine_name
            except FileNotFoundError:
                return None
            except FileExistsError:
                continue
        raise OSError(
            errno.EEXIST,
            "could not allocate a unique unit quarantine name",
        )

    @staticmethod
    def _restore_quarantine(
        directory_descriptor: int,
        quarantine_name: str,
    ) -> None:
        _rename_noreplace(
            directory_descriptor,
            quarantine_name,
            directory_descriptor,
            SERVICE_NAME,
        )

    def _restore_after_failure(
        self,
        directory_descriptor: int,
        quarantine_name: str,
        primary_error: BaseException,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        if expected_identity is not None:
            try:
                details = os.stat(
                    quarantine_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                details = None
            if (
                details is None
                or (details.st_dev, details.st_ino) != expected_identity
            ):
                raise RetainedArtifactError(
                    f"Primary operation failed: "
                    f"{self._primary_message(primary_error, 'The unit operation failed.')} "
                    "Cleanup warning: the retained backup changed before "
                    f"restoration: {quarantine_name}.",
                    quarantine_name,
                    retain_all=True,
                ) from primary_error
        try:
            self._restore_quarantine(directory_descriptor, quarantine_name)
        except OSError:
            raise RetainedArtifactError(
                f"Primary operation failed: "
                f"{self._primary_message(primary_error, 'The unit operation failed.')} "
                "Cleanup warning: the previous unit could not be restored; "
                f"it remains as {quarantine_name}.",
                quarantine_name,
            ) from primary_error
        try:
            os.fsync(directory_descriptor)
        except OSError:
            raise self._append_cleanup_warning(
                primary_error,
                fallback="The unit operation failed.",
                warning="restored unit could not be durably synchronized",
            ) from primary_error

    @classmethod
    def _open_verified_backup(
        cls,
        directory_descriptor: int,
        backup_name: str,
        expected_identity: tuple[int, int],
        *,
        require_managed: bool,
    ) -> int:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                backup_name,
                _FILE_READ_FLAGS,
                dir_fd=directory_descriptor,
            )
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or (details.st_dev, details.st_ino) != expected_identity
                or stat.S_IMODE(details.st_mode) & 0o022
                or details.st_size > MAX_UNIT_BYTES
            ):
                raise RetainedArtifactError(
                    "The retained backup changed after validation: "
                    f"{backup_name}.",
                    backup_name,
                    retain_all=True,
                )
            remaining = MAX_UNIT_BYTES + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            contents = b"".join(chunks)
            marker = GENERATED_MARKER.encode("utf-8")
            if len(contents) > MAX_UNIT_BYTES or (
                require_managed and marker not in contents.splitlines()
            ):
                raise RetainedArtifactError(
                    "The retained backup changed after validation: "
                    f"{backup_name}.",
                    backup_name,
                    retain_all=True,
                )
            return descriptor
        except BaseException as primary_error:
            descriptor_to_close = descriptor
            descriptor = None
            if not cls._close_once(descriptor_to_close):
                raise cls._append_cleanup_warning(
                    primary_error,
                    fallback="The retained backup could not be validated.",
                    warning="backup descriptor could not be closed cleanly",
                ) from primary_error
            raise

    @classmethod
    def _revalidate_backup_identity(
        cls,
        directory_descriptor: int,
        backup_name: str,
        backup_descriptor: int,
    ) -> None:
        """Confirm the retained name still identifies the held backup inode."""
        try:
            path_details = os.stat(
                backup_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            descriptor_details = os.fstat(backup_descriptor)
        except OSError as error:
            raise RetainedArtifactError(
                "The retained backup identity could not be revalidated; "
                "retained artifacts require manual inspection.",
                backup_name,
                retain_all=True,
            ) from error
        if (
            not stat.S_ISREG(path_details.st_mode)
            or not stat.S_ISREG(descriptor_details.st_mode)
            or path_details.st_uid != os.getuid()
            or descriptor_details.st_uid != os.getuid()
            or (path_details.st_dev, path_details.st_ino)
            != (descriptor_details.st_dev, descriptor_details.st_ino)
        ):
            raise RetainedArtifactError(
                "The retained backup identity changed before finalization; "
                "retained artifacts require manual inspection.",
                backup_name,
                retain_all=True,
            )

    def _write_unit_locked(
        self,
        directory_descriptor: int,
        *,
        force: bool,
    ) -> str | None:
        temporary_name: str | None = None
        quarantine_name: str | None = None
        quarantine_inspection: _UnitInspection | None = None
        backup_descriptor: int | None = None
        committed = False
        try:
            temporary_name = self._create_temporary_unit(
                directory_descriptor,
                generate_unit(self.executable),
            )
            try:
                os.link(
                    temporary_name,
                    SERVICE_NAME,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                quarantine_name = self._quarantine_unit(directory_descriptor)
                if quarantine_name is None:
                    raise ServiceManagerError(
                        "The existing nextuxlinqd unit changed during replacement."
                    )
                quarantine_inspection = self._inspect_named_unit(
                    directory_descriptor,
                    quarantine_name,
                )
                if quarantine_inspection.state == "foreign" and not force:
                    raise ServiceManagerError(
                        "Refusing to overwrite a unit not managed by NexTuxLinq. "
                        "Use --force to replace it."
                    )
                backup_descriptor = self._open_verified_backup(
                    directory_descriptor,
                    quarantine_name,
                    quarantine_inspection.identity,
                    require_managed=not force,
                )
                self._revalidate_backup_identity(
                    directory_descriptor,
                    quarantine_name,
                    backup_descriptor,
                )
                # Replacement commit point: this no-replace link makes the
                # fully synchronized temporary inode canonical.
                os.link(
                    temporary_name,
                    SERVICE_NAME,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            # First-install commit point is the successful first link above.
            committed = True
            if backup_descriptor is not None:
                os.fchmod(backup_descriptor, 0o600)
                os.fsync(backup_descriptor)
                descriptor_to_close = backup_descriptor
                backup_descriptor = None
                if not self._close_once(descriptor_to_close):
                    raise ServiceManagerError(
                        "Cleanup warning: replacement backup descriptor could "
                        "not be closed cleanly."
                    )
            os.unlink(temporary_name, dir_fd=directory_descriptor)
            temporary_name = None
            os.fsync(directory_descriptor)
            return quarantine_name
        except BaseException as error:
            primary_error: BaseException = error
            if backup_descriptor is not None:
                descriptor_to_close = backup_descriptor
                backup_descriptor = None
                if not self._close_once(descriptor_to_close):
                    primary_error = self._append_cleanup_warning(
                        primary_error,
                        fallback="The unit operation failed.",
                        warning=(
                            "replacement backup descriptor could not be "
                            "closed cleanly"
                        ),
                    )
            if (
                not committed
                and quarantine_name is not None
                and not (
                    isinstance(primary_error, RetainedArtifactError)
                    and primary_error.retain_all
                )
            ):
                try:
                    self._restore_after_failure(
                        directory_descriptor,
                        quarantine_name,
                        primary_error,
                        (
                            quarantine_inspection.identity
                            if quarantine_inspection is not None
                            else None
                        ),
                    )
                    quarantine_name = None
                except BaseException as restoration_error:
                    primary_error = restoration_error
            retain_temporary = (
                isinstance(primary_error, RetainedArtifactError)
                and primary_error.retain_all
            )
            if temporary_name is not None and not retain_temporary:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                    temporary_name = None
                except OSError:
                    primary_message = (
                        str(primary_error)
                        if isinstance(primary_error, ServiceManagerError)
                        else (
                            "The nextuxlinqd unit operation failed before "
                            "completion."
                        )
                    )
                    primary_error = RetainedArtifactError(
                        f"{primary_message} Cleanup warning: retained temporary "
                        f"file: {temporary_name}.",
                        temporary_name,
                    )
            elif temporary_name is not None and retain_temporary:
                primary_error = RetainedArtifactError(
                    f"{primary_error} Retained temporary file: {temporary_name}.",
                    *primary_error.names,
                    temporary_name,
                    retain_all=True,
                )
            if committed:
                details = (
                    str(primary_error)
                    if isinstance(primary_error, ServiceManagerError)
                    else (
                        "Backup finalization or directory synchronization "
                        "failed."
                    )
                )
                retained = (
                    f" Retained backup: {quarantine_name}."
                    if quarantine_name is not None
                    else ""
                )
                raise UnitInstallError(
                    f"The nextuxlinqd unit was installed. {details}{retained}",
                    committed=True,
                ) from primary_error
            if isinstance(primary_error, ServiceManagerError):
                raise primary_error
            raise UnitInstallError(
                "Could not safely install the nextuxlinqd user unit.",
                committed=False,
            ) from primary_error

    def _write_unit_atomically(self, *, force: bool) -> str | None:
        directory_descriptor = self._open_unit_directory(create=True)
        assert directory_descriptor is not None
        lock_descriptor: int | None = None
        result: str | None = None
        primary_error: BaseException | None = None
        try:
            lock_descriptor = self._acquire_manager_lock(directory_descriptor)
            result = self._write_unit_locked(
                directory_descriptor,
                force=force,
            )
        except BaseException as error:
            primary_error = error
        warnings = self._release_operation_descriptors(
            lock_descriptor,
            directory_descriptor,
        )
        if warnings:
            message = (
                str(primary_error)
                if isinstance(primary_error, ServiceManagerError)
                else (
                    "The unit operation completed."
                    if primary_error is None
                    else "The unit operation failed."
                )
            )
            raise ServiceManagerError(
                f"{message} Cleanup warning: {'; '.join(warnings)}."
            ) from primary_error
        if primary_error is not None:
            raise primary_error
        return result

    def _run(self, *arguments: str) -> CompletedProcessLike | None:
        try:
            return self.runner(["systemctl", "--user", *arguments])
        except (OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _unit_is_missing(
        result: CompletedProcessLike,
        service_name: str,
    ) -> bool:
        lines = [
            line.strip().casefold()
            for output in (result.stdout, result.stderr)
            for line in output.splitlines()
            if line.strip()
        ]
        if not lines:
            return False
        escaped_name = re.escape(service_name.casefold())
        patterns = (
            rf"unit {escaped_name} not found[.!]?",
            rf"unit {escaped_name} could not be found[.!]?",
            (
                rf"failed to disable unit:\s*unit file "
                rf"{escaped_name} does not exist[.!]?"
            ),
        )
        return all(
            any(re.fullmatch(pattern, line) is not None for pattern in patterns)
            for line in lines
        )

    @staticmethod
    def _state(result: CompletedProcessLike | None) -> str:
        if result is None:
            return "unknown"
        candidate = result.stdout.strip().splitlines()
        if candidate and re.fullmatch(r"[a-z][a-z-]*", candidate[0]):
            return candidate[0]
        return "unknown"

    def _install_locked_operation(
        self,
        directory_descriptor: int,
        *,
        force: bool,
    ) -> ServiceOperationResult:
        """Install and activate while the manager-wide lock is held."""
        backup_name: str | None = None
        try:
            backup_name = self._write_unit_locked(
                directory_descriptor,
                force=force,
            )
        except UnitInstallError as error:
            return ServiceOperationResult(
                False,
                (
                    ("NexTuxLinq user-service unit may be installed.",)
                    if error.committed
                    else ()
                ),
                str(error),
            )
        except ServiceManagerError as error:
            return ServiceOperationResult(False, error=str(error))
        except OSError:
            return ServiceOperationResult(
                False,
                error=(
                    "The unit installation could not be finalized safely; "
                    "review the managed unit before retrying."
                ),
            )
        installed = ("NexTuxLinq user-service unit installed.",)
        if backup_name is not None:
            installed += (
                f"Previous unit retained as backup: "
                f"{self.unit_directory / backup_name}",
            )

        reload_result = self._run("daemon-reload")
        if reload_result is None or reload_result.returncode != 0:
            return ServiceOperationResult(
                False,
                installed,
                "The unit was installed, but systemctl daemon-reload failed; "
                "the service was not enabled.",
            )

        for helper in HELPER_SERVICES:
            result = self._run("disable", "--now", helper)
            if result is None:
                return ServiceOperationResult(
                    False,
                    installed,
                    "The unit was installed, but a competing helper could not "
                    "be disabled; nextuxlinqd was not enabled.",
                )
            if result.returncode != 0 and not self._unit_is_missing(result, helper):
                return ServiceOperationResult(
                    False,
                    installed,
                    "The unit was installed, but a competing helper could not "
                    "be disabled; nextuxlinqd was not enabled.",
                )

        enable_result = self._run("enable", "--now", SERVICE_NAME)
        if enable_result is None or enable_result.returncode != 0:
            return ServiceOperationResult(
                False,
                installed,
                "The unit was installed and legacy helpers were disabled, but "
                "systemctl could not complete enabling and starting "
                "nextuxlinqd; it may be partially active.",
            )

        state_result = self._run("is-active", SERVICE_NAME)
        state = self._state(state_result)
        output = (
            "NexTuxLinq user service installed successfully.",
            f"Service state: {state}",
            f"GUI: {GUI_ADDRESS}",
        )
        if backup_name is not None:
            output += (
                f"Previous unit retained as backup: "
                f"{self.unit_directory / backup_name}",
            )
        if state_result is None or state_result.returncode != 0:
            return ServiceOperationResult(
                False,
                output,
                "The service was enabled, but systemctl did not report it active.",
            )
        return ServiceOperationResult(True, output)

    @staticmethod
    def _add_cleanup_warnings(
        result: ServiceOperationResult,
        warnings: tuple[str, ...],
    ) -> ServiceOperationResult:
        if not warnings:
            return result
        warning_text = f"Cleanup warning: {'; '.join(warnings)}."
        error = (
            f"{result.error} {warning_text}"
            if result.error is not None
            else warning_text
        )
        return ServiceOperationResult(False, result.output, error)

    def install(self, *, force: bool = False) -> ServiceOperationResult:
        """Install under one manager-wide lock, then release every descriptor."""
        directory_descriptor: int | None = None
        lock_descriptor: int | None = None
        try:
            directory_descriptor = self._open_unit_directory(create=True)
            assert directory_descriptor is not None
            lock_descriptor = self._acquire_manager_lock(directory_descriptor)
            result = self._install_locked_operation(
                directory_descriptor,
                force=force,
            )
        except ServiceManagerError as error:
            result = ServiceOperationResult(False, error=str(error))
        except OSError:
            result = ServiceOperationResult(
                False,
                error="Could not safely begin NexTuxLinq service installation.",
            )
        warnings = self._release_operation_descriptors(
            lock_descriptor,
            directory_descriptor,
        )
        return self._add_cleanup_warnings(result, warnings)

    def _uninstall_locked_operation(
        self,
        directory_descriptor: int,
    ) -> ServiceOperationResult:
        """Disable and retain a managed unit while the manager lock is held."""
        removed = False
        backup_name: str | None = None
        backup_inspection: _UnitInspection | None = None
        backup_descriptor: int | None = None
        try:
            existing = self._inspect_named_unit(
                directory_descriptor,
                SERVICE_NAME,
                allow_absent=True,
            )
            if existing.state == "absent":
                return ServiceOperationResult(
                    True,
                    ("NexTuxLinq user service is not installed.",),
                )
            backup_name = self._quarantine_unit(directory_descriptor)
            if backup_name is None:
                raise ServiceManagerError(
                    "The nextuxlinqd user unit changed during uninstall."
                )
            backup_inspection = self._inspect_named_unit(
                directory_descriptor,
                backup_name,
            )
            if backup_inspection.state != "managed":
                raise ServiceManagerError(
                    "Refusing to remove a unit not managed by NexTuxLinq."
                )
            self._restore_after_failure(
                directory_descriptor,
                backup_name,
                ServiceManagerError("The unit was inspected before uninstall."),
                backup_inspection.identity,
            )
            backup_name = None

            disable_result = self._run("disable", "--now", SERVICE_NAME)
            if disable_result is None or (
                disable_result.returncode != 0
                and not self._unit_is_missing(disable_result, SERVICE_NAME)
            ):
                return ServiceOperationResult(
                    False,
                    error=(
                        "Could not stop and disable nextuxlinqd; the managed "
                        "unit was preserved."
                    ),
                )

            backup_name = self._quarantine_unit(directory_descriptor)
            if backup_name is None:
                raise ServiceManagerError(
                    "The nextuxlinqd user unit disappeared during uninstall."
                )
            backup_inspection = self._inspect_named_unit(
                directory_descriptor,
                backup_name,
            )
            if backup_inspection.state != "managed":
                raise ServiceManagerError(
                    "Refusing to remove a substituted foreign user unit."
                )
            backup_descriptor = self._open_verified_backup(
                directory_descriptor,
                backup_name,
                backup_inspection.identity,
                require_managed=True,
            )
            self._revalidate_backup_identity(
                directory_descriptor,
                backup_name,
                backup_descriptor,
            )
            removed = True
            os.fchmod(backup_descriptor, 0o600)
            os.fsync(backup_descriptor)
            descriptor_to_close = backup_descriptor
            backup_descriptor = None
            if not self._close_once(descriptor_to_close):
                raise ServiceManagerError(
                    "The managed unit was retained as a disabled backup. "
                    "Cleanup warning: uninstall backup descriptor could not "
                    f"be closed cleanly. Retained backup: {backup_name}."
                )
            os.fsync(directory_descriptor)
        except BaseException as error:
            primary_error: BaseException = error
            if backup_descriptor is not None:
                descriptor_to_close = backup_descriptor
                backup_descriptor = None
                if not self._close_once(descriptor_to_close):
                    primary_error = self._append_cleanup_warning(
                        primary_error,
                        fallback="The uninstall operation failed.",
                        warning=(
                            "uninstall backup descriptor could not be closed "
                            "cleanly"
                        ),
                    )
            if (
                not removed
                and backup_name is not None
                and not (
                    isinstance(primary_error, RetainedArtifactError)
                    and primary_error.retain_all
                )
            ):
                try:
                    self._restore_after_failure(
                        directory_descriptor,
                        backup_name,
                        primary_error,
                        (
                            backup_inspection.identity
                            if backup_inspection is not None
                            else None
                        ),
                    )
                    backup_name = None
                except BaseException as restoration_error:
                    primary_error = restoration_error
            if isinstance(primary_error, ServiceManagerError):
                return ServiceOperationResult(False, error=str(primary_error))
            if removed:
                retained = (
                    f" Retained backup: {backup_name}."
                    if backup_name is not None
                    else ""
                )
                return ServiceOperationResult(
                    False,
                    ("Managed nextuxlinqd unit removed.",),
                    "The unit was removed, but backup synchronization failed "
                    f"and systemctl daemon-reload was not run.{retained}",
                )
            return ServiceOperationResult(
                False,
                error="Could not safely retain the managed nextuxlinqd unit.",
            )

        assert backup_name is not None
        backup_path = self.unit_directory / backup_name
        reload_result = self._run("daemon-reload")
        if reload_result is None or reload_result.returncode != 0:
            return ServiceOperationResult(
                False,
                (
                    "Managed nextuxlinqd unit removed.",
                    f"Disabled unit retained as backup: {backup_path}",
                ),
                "The unit was retained as a disabled backup, but systemctl "
                "daemon-reload failed.",
            )
        return ServiceOperationResult(
            True,
            (
                "NexTuxLinq user service uninstalled successfully.",
                f"Disabled unit retained as backup: {backup_path}",
                "Configuration and profiles were preserved.",
            ),
        )

    def uninstall(self) -> ServiceOperationResult:
        """Uninstall under one manager-wide lock."""
        directory_descriptor: int | None = None
        lock_descriptor: int | None = None
        try:
            directory_descriptor = self._open_unit_directory(create=False)
            if directory_descriptor is None:
                result = ServiceOperationResult(
                    True,
                    ("NexTuxLinq user service is not installed.",),
                )
            else:
                lock_descriptor = self._acquire_manager_lock(directory_descriptor)
                result = self._uninstall_locked_operation(directory_descriptor)
        except ServiceManagerError as error:
            result = ServiceOperationResult(False, error=str(error))
        except OSError:
            result = ServiceOperationResult(
                False,
                error="Could not safely begin NexTuxLinq service uninstall.",
            )
        warnings = self._release_operation_descriptors(
            lock_descriptor,
            directory_descriptor,
        )
        return self._add_cleanup_warnings(result, warnings)

    def control(self, action: str) -> ServiceOperationResult:
        """Start, stop, restart, or report status for nextuxlinqd only."""
        if action not in {"start", "stop", "restart", "status"}:
            return ServiceOperationResult(False, error="Unknown service action.")
        arguments: Sequence[str]
        if action == "status":
            arguments = ("status", SERVICE_NAME, "--no-pager")
        else:
            arguments = (action, SERVICE_NAME)
        result = self._run(*arguments)
        if result is None:
            return ServiceOperationResult(
                False,
                error=f"Could not run systemctl to {action} nextuxlinqd.",
            )
        useful_output = tuple(
            line for line in result.stdout.rstrip().splitlines() if line
        )
        if result.returncode != 0:
            return ServiceOperationResult(
                False,
                useful_output,
                f"systemctl could not {action} nextuxlinqd.service "
                f"(exit {result.returncode}).",
            )
        if action == "status":
            return ServiceOperationResult(True, useful_output)
        return ServiceOperationResult(
            True,
            (f"nextuxlinqd.service {action} completed successfully.",),
        )
