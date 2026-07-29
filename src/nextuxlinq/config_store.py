"""XDG JSON persistence for Matrix900 display configuration."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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
