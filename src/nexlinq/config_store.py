"""XDG JSON persistence for Matrix900 display configuration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from nexlinq.defaults import default_configuration
from nexlinq.models import SCHEMA_VERSION, ConfigurationDocument


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    """Configuration loaded from disk or a safe built-in fallback."""

    configuration: ConfigurationDocument
    warning: str | None = None
    persisted: bool = False


class ConfigSaveError(RuntimeError):
    """Configuration save failure with explicit commit-point state."""

    def __init__(self, message: str, *, committed: bool) -> None:
        super().__init__(message)
        self.committed = committed


def resolve_config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve `${XDG_CONFIG_HOME:-~/.config}/nexlinq/config.json`."""
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
    return root / "nexlinq" / "config.json"


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
    """Load and atomically save one fixed configuration path."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = resolve_config_path() if path is None else Path(path)

    def load(self) -> ConfigLoadResult:
        """Load without modifying missing or invalid configuration files."""
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                configuration = parse_configuration(json.load(stream))
        except FileNotFoundError:
            return ConfigLoadResult(default_configuration())
        except (
            OSError,
            OverflowError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            return ConfigLoadResult(
                default_configuration(),
                warning=f"Could not load configuration: {error}",
            )
        return ConfigLoadResult(configuration, persisted=True)

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
            os.replace(temporary_path, self.path)
            committed = True
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
