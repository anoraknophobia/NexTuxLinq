"""XDG JSON persistence for Matrix900 display configuration."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from nextuxlinq.defaults import default_configuration
from nextuxlinq.models import SCHEMA_VERSION, ConfigurationDocument

LEGACY_MIGRATION_WARNING = (
    "Legacy NexLinq configuration loaded. Save to migrate it to NexTuxLinq; "
    "the legacy file will be preserved."
)
LEGACY_LOAD_WARNING = (
    "Could not load legacy NexLinq configuration safely; using defaults."
)
MAX_CONFIGURATION_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    """Configuration loaded from disk or a safe built-in fallback."""

    configuration: ConfigurationDocument
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

    def load(self) -> ConfigLoadResult:
        """Load without modifying missing or invalid configuration files."""
        canonical = self._load_canonical_if_present()
        if canonical is not None:
            return canonical
        return self._load_legacy()

    def _load_canonical_if_present(self) -> ConfigLoadResult | None:
        """Load the canonical path, returning None only when it is absent."""
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                configuration = parse_configuration(json.load(stream))
        except FileNotFoundError:
            return None
        except (
            OSError,
            OverflowError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            self._migration_pending = False
            return ConfigLoadResult(
                default_configuration(),
                warning=f"Could not load configuration: {error}",
            )
        self._migration_pending = False
        return ConfigLoadResult(configuration, persisted=True)

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

    def _read_legacy_configuration(self) -> ConfigurationDocument:
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
            return parse_configuration(json.loads(raw.decode("utf-8")))
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
            return ConfigLoadResult(default_configuration())
        try:
            configuration = self._read_legacy_configuration()
        except FileNotFoundError:
            canonical = self._load_canonical_if_present()
            if canonical is not None:
                return canonical
            self._migration_pending = False
            return ConfigLoadResult(default_configuration())
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
            return ConfigLoadResult(
                default_configuration(),
                warning=LEGACY_LOAD_WARNING,
            )
        canonical = self._load_canonical_if_present()
        if canonical is not None:
            return canonical
        self._migration_pending = True
        return ConfigLoadResult(
            configuration,
            warning=LEGACY_MIGRATION_WARNING,
            persisted=True,
            migration_pending=True,
        )

    def save(self, configuration: ConfigurationDocument) -> None:
        """Atomically persist a validated configuration document."""
        if not isinstance(configuration, ConfigurationDocument):
            raise TypeError("configuration must be a ConfigurationDocument.")
        directory = self.path.parent
        temporary_path: Path | None = None
        descriptor: int | None = None
        committed = False
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
                os.link(temporary_path, self.path)
                committed = True
                self._migration_pending = False
                temporary_path.unlink()
                temporary_path = None
            else:
                os.replace(temporary_path, self.path)
                committed = True
                self._migration_pending = False
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as error:
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
            ) from error
