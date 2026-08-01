"""XDG JSON persistence for Matrix900 display configuration."""

from __future__ import annotations

import json
import os
import stat
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from nextuxlinq.defaults import (
    default_configuration,
    legacy_equivalent_configuration_v2,
)
from nextuxlinq.migration import (
    SchemaKind,
    detect_configuration_schema,
    parse_or_migrate_configuration_v2,
)
from nextuxlinq.models import SCHEMA_VERSION, ConfigurationDocument
from nextuxlinq.models_v2 import ConfigurationDocumentV2

LEGACY_MIGRATION_WARNING = (
    "Schema-v1 configuration loaded in memory. An explicit save will write "
    "schema v2 and preserve the original."
)
LEGACY_LOAD_WARNING = (
    "Could not load legacy NexLinq configuration safely; using defaults."
)
MAX_CONFIGURATION_BYTES = 1024 * 1024
MANAGED_BACKUP_FORMAT = "nextuxlinq-managed-backup"
MANAGED_BACKUP_VERSION = 1
MANAGED_BACKUP_RETENTION = 10
MAX_BACKUP_NAME_LENGTH = 64
_BACKUP_ID = re.compile(r"^[0-9a-f]{32}$")


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    """Configuration loaded from disk or a safe built-in fallback."""

    configuration: ConfigurationDocumentV2
    warning: str | None = None
    persisted: bool = False
    migration_pending: bool = False


class ConfigSaveError(RuntimeError):
    """Configuration save failure with explicit commit-point state."""

    def __init__(self, message: str, *, committed: bool) -> None:
        super().__init__(message)
        self.committed = committed


class ManagedBackupError(ValueError):
    """A sanitized managed-backup failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("The managed backup operation could not be completed.")


@dataclass(frozen=True, slots=True)
class ManagedBackup:
    """Public backup metadata without a filesystem path."""

    id: str
    name: str
    created_at: str
    profile_count: int
    active_profile: str
    schema_version: int


def _resolve_config_root(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve the absolute XDG configuration root."""
    environment = os.environ if environ is None else environ
    configured = environment.get("XDG_CONFIG_HOME")
    configured_path = Path(configured) if configured else None
    if configured_path is not None and configured_path.is_absolute():
        root = configured_path.expanduser()
    else:
        home = environment.get("HOME")
        if not home:
            raise RuntimeError("HOME is not set and XDG_CONFIG_HOME is unavailable.")
        home_path = Path(home).expanduser()
        if not home_path.is_absolute():
            raise RuntimeError("HOME must be an absolute path.")
        root = home_path / ".config"
    return root


def resolve_config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve `${XDG_CONFIG_HOME:-~/.config}/nextuxlinq/config.json`."""
    return _resolve_config_root(environ) / "nextuxlinq" / "config.json"


def resolve_legacy_config_path(
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the pre-rename configuration path used only for migration."""
    return _resolve_config_root(environ) / "nexlinq" / "config.json"


def parse_configuration(raw: object) -> ConfigurationDocument:
    """Validate schema v1 or migrate the known pre-versioned draft shape."""
    if not isinstance(raw, Mapping):
        raise TypeError("configuration must be a JSON object.")
    version = raw.get("schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        raise TypeError("schema_version must be an integer.")
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"Configuration schema {version} is newer than supported "
            f"schema {SCHEMA_VERSION}."
        )
    if version == 0:
        raw = {**raw, "schema_version": SCHEMA_VERSION}
    return ConfigurationDocument.from_dict(raw)


class ConfigStore:
    """Load canonical or legacy configuration and save only canonically."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        legacy_path: Path | None = None,
        backup_clock: Callable[[], datetime] | None = None,
        backup_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if path is None:
            self.path = resolve_config_path()
            self.legacy_path = (
                resolve_legacy_config_path()
                if legacy_path is None
                else Path(legacy_path)
            )
        else:
            self.path = Path(path)
            self.legacy_path = (
                None if legacy_path is None else Path(legacy_path)
            )
        self._migration_pending = False
        self._schema_v1_backup: bytes | None = None
        self._backup_clock = backup_clock or (lambda: datetime.now(timezone.utc))
        self._backup_id_factory = backup_id_factory or (lambda: uuid.uuid4().hex)

    @property
    def backup_directory(self) -> Path:
        """Return the fixed managed-backup directory."""
        return self.path.parent / "backups"

    @staticmethod
    def _validate_backup_name(name: object) -> str:
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= MAX_BACKUP_NAME_LENGTH
            or name != name.strip()
            or not name.isprintable()
        ):
            raise ManagedBackupError("invalid_backup_name") from None
        return name

    @staticmethod
    def _validate_backup_id(backup_id: object) -> str:
        if not isinstance(backup_id, str) or not _BACKUP_ID.fullmatch(backup_id):
            raise ManagedBackupError("backup_not_found") from None
        return backup_id

    def _backup_path(self, backup_id: object) -> Path:
        identifier = self._validate_backup_id(backup_id)
        return self.backup_directory / f"backup-{identifier}.json"

    def _open_backup_directory(self, *, create: bool) -> int:
        """Open the fixed private directory without following a symlink."""
        failure = False
        if create:
            try:
                self.backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError:
                failure = True
        try:
            details = self.backup_directory.lstat()
        except OSError:
            failure = True
            details = None
        if failure or details is None:
            raise ManagedBackupError(
                "backup_creation_failed" if create else "backup_not_found"
            )
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or (not create and stat.S_IMODE(details.st_mode) & 0o077)
        ):
            raise ManagedBackupError("invalid_backup")
        if create:
            chmod_failed = False
            try:
                os.chmod(self.backup_directory, 0o700)
            except OSError:
                chmod_failed = True
            if chmod_failed:
                raise ManagedBackupError("backup_creation_failed")
        open_failed = False
        try:
            descriptor = os.open(
                self.backup_directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
        except OSError:
            open_failed = True
            descriptor = -1
        if open_failed:
            raise ManagedBackupError("invalid_backup")
        return descriptor

    @staticmethod
    def _backup_metadata(raw: object) -> tuple[ManagedBackup, ConfigurationDocumentV2]:
        if not isinstance(raw, Mapping):
            raise ManagedBackupError("invalid_backup") from None
        required = {
            "format", "format_version", "id", "name", "created_at",
            "schema_version", "configuration",
        }
        if set(raw) != required:
            raise ManagedBackupError("invalid_backup") from None
        if (
            raw.get("format") != MANAGED_BACKUP_FORMAT
            or raw.get("format_version") != MANAGED_BACKUP_VERSION
            or raw.get("schema_version") != 2
        ):
            raise ManagedBackupError("invalid_backup") from None
        backup_id = ConfigStore._validate_backup_id(raw.get("id"))
        name = ConfigStore._validate_backup_name(raw.get("name"))
        created_at = raw.get("created_at")
        if not isinstance(created_at, str) or len(created_at) > 40:
            raise ManagedBackupError("invalid_backup") from None
        invalid_time = False
        try:
            parsed_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            invalid_time = True
            parsed_time = None
        if invalid_time:
            raise ManagedBackupError("invalid_backup")
        assert parsed_time is not None
        if parsed_time.tzinfo is None:
            raise ManagedBackupError("invalid_backup") from None
        invalid_configuration = False
        try:
            configuration = ConfigurationDocumentV2.from_dict(raw["configuration"])
        except (TypeError, ValueError):
            invalid_configuration = True
            configuration = None
        if invalid_configuration:
            raise ManagedBackupError("invalid_backup")
        assert configuration is not None
        return (
            ManagedBackup(
                id=backup_id,
                name=name,
                created_at=created_at,
                profile_count=len(configuration.profiles),
                active_profile=configuration.active_profile,
                schema_version=configuration.schema_version,
            ),
            configuration,
        )

    def _read_managed_backup(self, path: Path) -> tuple[ManagedBackup, ConfigurationDocumentV2]:
        descriptor: int | None = None
        directory_descriptor: int | None = None
        failure_code: str | None = None
        result: tuple[ManagedBackup, ConfigurationDocumentV2] | None = None
        try:
            if path.parent != self.backup_directory:
                raise ManagedBackupError("invalid_backup")
            directory_descriptor = self._open_backup_directory(create=False)
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_descriptor,
            )
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_size > MAX_CONFIGURATION_BYTES
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) & 0o077
            ):
                raise ManagedBackupError("invalid_backup") from None
            chunks: list[bytes] = []
            remaining = MAX_CONFIGURATION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > MAX_CONFIGURATION_BYTES:
                raise ManagedBackupError("invalid_backup") from None
            invalid_json = False
            try:
                raw = json.loads(
                    content.decode("utf-8"),
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeError, json.JSONDecodeError, ValueError):
                invalid_json = True
                raw = None
            if invalid_json:
                raise ManagedBackupError("invalid_backup")
            metadata, configuration = self._backup_metadata(raw)
            if path.name != f"backup-{metadata.id}.json":
                raise ManagedBackupError("invalid_backup") from None
            result = (metadata, configuration)
        except FileNotFoundError:
            failure_code = "backup_not_found"
        except OSError:
            failure_code = "invalid_backup"
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if directory_descriptor is not None:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass
        if failure_code is not None:
            raise ManagedBackupError(failure_code)
        assert result is not None
        return result

    def list_backups(self) -> tuple[ManagedBackup, ...]:
        """List valid user-managed backups, newest first."""
        directory_descriptor: int | None = None
        try:
            directory_descriptor = self._open_backup_directory(create=False)
            names = tuple(os.listdir(directory_descriptor))
        except (OSError, ManagedBackupError):
            return ()
        finally:
            if directory_descriptor is not None:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass
        paths = tuple(
            self.backup_directory / name
            for name in names
            if name.startswith("backup-") and name.endswith(".json")
        )
        backups: list[ManagedBackup] = []
        for path in paths:
            try:
                metadata, _ = self._read_managed_backup(path)
            except ManagedBackupError:
                continue
            backups.append(metadata)
        return tuple(sorted(backups, key=lambda item: (item.created_at, item.id), reverse=True))

    def create_backup(
        self,
        name: str,
        configuration: ConfigurationDocumentV2,
    ) -> ManagedBackup:
        """Atomically create and retain a bounded user-managed backup."""
        name = self._validate_backup_name(name)
        if type(configuration) is not ConfigurationDocumentV2:
            raise ManagedBackupError("invalid_backup") from None
        backup_id = self._validate_backup_id(self._backup_id_factory())
        created_at = self._backup_clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "format": MANAGED_BACKUP_FORMAT,
            "format_version": MANAGED_BACKUP_VERSION,
            "id": backup_id,
            "name": name,
            "created_at": created_at,
            "schema_version": configuration.schema_version,
            "configuration": configuration.to_dict(),
        }
        temporary_name: str | None = None
        descriptor: int | None = None
        directory_descriptor: int | None = None
        failure_code: str | None = None
        try:
            directory_descriptor = self._open_backup_directory(create=True)
            temporary_name = f".backup-{uuid.uuid4().hex}.tmp"
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())
            target_name = self._backup_path(backup_id).name
            collision = False
            try:
                os.link(
                    temporary_name,
                    target_name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                collision = True
            if collision:
                raise ManagedBackupError("backup_id_collision")
            os.unlink(temporary_name, dir_fd=directory_descriptor)
            temporary_name = None
            os.fsync(directory_descriptor)
        except ManagedBackupError:
            raise
        except Exception:
            failure_code = "backup_creation_failed"
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_name is not None and directory_descriptor is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except OSError:
                    pass
            if directory_descriptor is not None:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass
        if failure_code is not None:
            raise ManagedBackupError(failure_code)
        backups = self.list_backups()
        for expired in backups[MANAGED_BACKUP_RETENTION:]:
            try:
                self.delete_backup(expired.id)
            except ManagedBackupError:
                try:
                    self.delete_backup(backup_id)
                except ManagedBackupError:
                    pass
                raise ManagedBackupError("backup_retention_failed")
        metadata, _ = self._read_managed_backup(self._backup_path(backup_id))
        return metadata

    def restore_backup(self, backup_id: str) -> ConfigurationDocumentV2:
        """Validate and return a managed backup without changing persistence."""
        _, configuration = self._read_managed_backup(self._backup_path(backup_id))
        return configuration

    def delete_backup(self, backup_id: str) -> None:
        """Delete exactly one managed backup, never configuration or migration data."""
        path = self._backup_path(backup_id)
        self._read_managed_backup(path)
        failure_code: str | None = None
        directory_descriptor: int | None = None
        try:
            directory_descriptor = self._open_backup_directory(create=False)
            os.unlink(path.name, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
        except FileNotFoundError:
            failure_code = "backup_not_found"
        except OSError:
            failure_code = "backup_deletion_failed"
        finally:
            if directory_descriptor is not None:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass
        if failure_code is not None:
            raise ManagedBackupError(failure_code)

    def load(self) -> ConfigLoadResult:
        """Load without modifying missing or invalid configuration files."""
        canonical = self._load_canonical_if_present()
        if canonical is not None:
            return canonical
        return self._load_legacy()

    def _load_canonical_if_present(self) -> ConfigLoadResult | None:
        """Load the canonical path, returning None only when it is absent."""
        try:
            raw_bytes = self.path.read_bytes()
            if len(raw_bytes) > MAX_CONFIGURATION_BYTES:
                raise ValueError("configuration is too large")
            raw = json.loads(raw_bytes.decode("utf-8"))
            detection = detect_configuration_schema(raw)
            configuration = parse_or_migrate_configuration_v2(raw)
        except FileNotFoundError:
            return None
        except (
            OSError,
            OverflowError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            self._migration_pending = False
            self._schema_v1_backup = None
            return ConfigLoadResult(
                legacy_equivalent_configuration_v2(),
                warning="Could not load configuration safely; using defaults.",
            )
        migrated = detection.kind in {
            SchemaKind.PRE_VERSIONED,
            SchemaKind.VERSION_1,
        }
        self._migration_pending = migrated
        self._schema_v1_backup = raw_bytes if migrated else None
        return ConfigLoadResult(
            configuration,
            warning=LEGACY_MIGRATION_WARNING if migrated else None,
            persisted=True,
            migration_pending=migrated,
        )

    @staticmethod
    def _validate_legacy_descriptor(
        details: os.stat_result,
        *,
        expect_directory: bool,
    ) -> None:
        """Validate ownership, type, and private permissions for migration."""
        expected_type = (
            stat.S_ISDIR(details.st_mode)
            if expect_directory
            else stat.S_ISREG(details.st_mode)
        )
        if not expected_type:
            raise ValueError("legacy configuration object has an unsafe type")
        if details.st_uid != os.getuid():
            raise ValueError("legacy configuration object has the wrong owner")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise ValueError("legacy configuration object has unsafe permissions")

    def _read_legacy_configuration(self) -> ConfigurationDocumentV2:
        """Read one bounded, symlink-resistant legacy configuration."""
        if self.legacy_path is None:
            raise FileNotFoundError
        directory_descriptor: int | None = None
        descriptor: int | None = None
        try:
            directory_descriptor = os.open(
                self.legacy_path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            self._validate_legacy_descriptor(
                os.fstat(directory_descriptor),
                expect_directory=True,
            )
            descriptor = os.open(
                self.legacy_path.name,
                os.O_RDONLY
                | os.O_CLOEXEC
                | os.O_NOFOLLOW
                | os.O_NONBLOCK,
                dir_fd=directory_descriptor,
            )
            details = os.fstat(descriptor)
            self._validate_legacy_descriptor(
                details,
                expect_directory=False,
            )
            if details.st_size > MAX_CONFIGURATION_BYTES:
                raise ValueError("legacy configuration is too large")
            chunks: list[bytes] = []
            remaining = MAX_CONFIGURATION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_CONFIGURATION_BYTES:
                raise ValueError("legacy configuration is too large")
            return parse_or_migrate_configuration_v2(
                json.loads(raw.decode("utf-8"))
            )
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if directory_descriptor is not None:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass

    def _load_legacy(self) -> ConfigLoadResult:
        """Load the legacy path only when the canonical path is absent."""
        if self.legacy_path is None:
            self._migration_pending = False
            self._schema_v1_backup = None
            return ConfigLoadResult(legacy_equivalent_configuration_v2())
        try:
            configuration = self._read_legacy_configuration()
        except FileNotFoundError:
            canonical = self._load_canonical_if_present()
            if canonical is not None:
                return canonical
            self._migration_pending = False
            self._schema_v1_backup = None
            return ConfigLoadResult(legacy_equivalent_configuration_v2())
        except (
            OSError,
            OverflowError,
            TypeError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            canonical = self._load_canonical_if_present()
            if canonical is not None:
                return canonical
            self._migration_pending = False
            self._schema_v1_backup = None
            return ConfigLoadResult(
                legacy_equivalent_configuration_v2(),
                warning=LEGACY_LOAD_WARNING,
            )
        canonical = self._load_canonical_if_present()
        if canonical is not None:
            return canonical
        self._migration_pending = True
        self._schema_v1_backup = None
        return ConfigLoadResult(
            configuration,
            warning=LEGACY_MIGRATION_WARNING,
            persisted=True,
            migration_pending=True,
        )

    def _write_schema_v1_backup(self, directory: Path) -> Path | None:
        payload = self._schema_v1_backup
        if payload is None:
            return None
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.schema-v1.",
            suffix=".bak",
            dir=directory,
        )
        backup = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                backup.unlink()
            except OSError:
                pass
            raise
        return backup

    def save(self, configuration: ConfigurationDocumentV2) -> None:
        """Atomically persist a validated configuration document."""
        if type(configuration) is not ConfigurationDocumentV2:
            raise TypeError(
                "configuration must be a ConfigurationDocumentV2."
            )
        directory = self.path.parent
        temporary_path: Path | None = None
        descriptor: int | None = None
        committed = False
        failed = False
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=directory
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                json.dump(
                    configuration.to_dict(), stream, indent=2, sort_keys=True
                )
                stream.write("\n")
                stream.flush()
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())
            if self._migration_pending:
                if self._schema_v1_backup is not None:
                    self._write_schema_v1_backup(directory)
                    backup_directory_fd = os.open(
                        directory,
                        os.O_RDONLY | os.O_DIRECTORY,
                    )
                    try:
                        os.fsync(backup_directory_fd)
                    finally:
                        os.close(backup_directory_fd)
                    os.replace(temporary_path, self.path)
                    committed = True
                    temporary_path = None
                else:
                    os.link(temporary_path, self.path)
                    committed = True
                    temporary_path.unlink()
                    temporary_path = None
            else:
                os.replace(temporary_path, self.path)
                committed = True
                temporary_path = None
            self._migration_pending = False
            self._schema_v1_backup = None
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            failed = True
        if failed:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
            state = "after" if committed else "before"
            raise ConfigSaveError(
                f"Configuration save failed {state} the commit point.",
                committed=committed,
            )
