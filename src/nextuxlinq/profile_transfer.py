"""Safe JSON transfer formats for canonical schema-v2 profiles."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
from nextuxlinq.models_v2 import (
    SCHEMA_VERSION_V2,
    ConfigurationDocumentV2,
    ProfileConfigV2,
)

PROFILE_EXPORT_FORMAT = "nextuxlinq-profile"
PROFILE_EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 256 * 1024
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_-]+")


class TransferValidationError(ValueError):
    """A sanitized transfer failure safe to expose at the API boundary."""

    def __init__(self, code: str, *, field: str = "content") -> None:
        self.code = code
        self.field = field
        super().__init__("The imported JSON file is not valid.")


@dataclass(frozen=True, slots=True)
class ProfileImportResult:
    """A validated profile plus its collision-safe proposed local identity."""

    profile_name: str
    profile: ProfileConfigV2


def safe_profile_filename(profile_name: str) -> str:
    """Return a portable filename without changing the embedded profile name."""
    safe = _SAFE_FILENAME.sub("-", profile_name).strip("-_").lower()
    if not safe:
        safe = "profile"
    return f"nextuxlinq-profile-{safe[:48]}.json"


def export_profile(profile_name: str, profile: ProfileConfigV2) -> dict[str, object]:
    """Build one deterministic, runtime-free profile export envelope."""
    if not isinstance(profile_name, str) or not _PROFILE_NAME.fullmatch(profile_name):
        raise ValueError("profile_name must be a canonical profile name.")
    if type(profile) is not ProfileConfigV2:
        raise TypeError("profile must be a ProfileConfigV2.")
    return {
        "format": PROFILE_EXPORT_FORMAT,
        "format_version": PROFILE_EXPORT_VERSION,
        "schema_version": SCHEMA_VERSION_V2,
        "profile_name": profile_name,
        "profile": profile.to_dict(),
    }


def deterministic_json(value: object) -> bytes:
    """Encode one JSON value as deterministic UTF-8 without Python objects."""
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def decode_import_content(content_base64: str) -> bytes:
    """Decode one bounded browser file payload without accepting paths."""
    if not isinstance(content_base64, str):
        raise TransferValidationError("malformed_upload") from None
    failed = False
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        failed = True
        raw = b""
    if failed:
        raise TransferValidationError("malformed_upload")
    if not raw:
        raise TransferValidationError("empty_file") from None
    if len(raw) > MAX_IMPORT_BYTES:
        raise TransferValidationError("file_too_large") from None
    return raw


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def parse_json_bytes(raw: bytes) -> object:
    """Strictly parse bounded UTF-8 JSON with duplicate-key rejection."""
    if not raw:
        raise TransferValidationError("empty_file") from None
    if len(raw) > MAX_IMPORT_BYTES:
        raise TransferValidationError("file_too_large") from None
    invalid_utf8 = False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        invalid_utf8 = True
        text = ""
    if invalid_utf8:
        raise TransferValidationError("invalid_utf8")
    if not text.strip():
        raise TransferValidationError("empty_file") from None
    malformed = False
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        malformed = True
        parsed = None
    if malformed:
        raise TransferValidationError("malformed_json")
    return parsed


def _mapping(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise TransferValidationError("unsupported_export_format") from None
    return raw


def _profile_error_code(error: BaseException) -> str:
    """Classify a domain error while discarding all underlying details."""
    message = str(error).lower()
    if "duplicated" in message and "region id" in message:
        return "duplicate_region_id"
    if "region id" in message:
        return "invalid_region_id"
    if "overlap" in message:
        return "overlap"
    if any(item in message for item in (
        "fit inside", "must not be negative", "canvas width", "canvas height"
    )):
        return "out_of_bounds"
    if "at most" in message and "region" in message:
        return "region_limit_reached"
    if any(item in message for item in ("widget", "settings", "font", "content")):
        return "wrong_widget_settings"
    return "invalid_profile_data"


def _imported_name(name: object, existing_names: Sequence[str]) -> str:
    if not isinstance(name, str) or not _PROFILE_NAME.fullmatch(name):
        raise TransferValidationError("invalid_profile_name", field="profile_name") from None
    occupied = set(existing_names)
    if name not in occupied:
        return name
    suffix = "_Imported"
    for index in range(1, 1000):
        ending = suffix if index == 1 else f"{suffix}_{index}"
        candidate = f"{name[: 32 - len(ending)]}{ending}"
        if candidate not in occupied:
            return candidate
    raise TransferValidationError("duplicate_profile_name", field="profile_name") from None


def import_profile(raw: object, existing_names: Sequence[str]) -> ProfileImportResult:
    """Validate a single-profile envelope without mutating local state."""
    values = _mapping(raw)
    allowed = {
        "format", "format_version", "schema_version", "profile_name", "profile"
    }
    if values.get("format") != PROFILE_EXPORT_FORMAT:
        raise TransferValidationError("unsupported_export_format", field="format") from None
    if values.get("format_version") != PROFILE_EXPORT_VERSION:
        raise TransferValidationError("unsupported_export_version", field="format_version") from None
    if values.get("schema_version") != SCHEMA_VERSION_V2:
        raise TransferValidationError("unsupported_schema_version", field="schema_version") from None
    if set(values) != allowed:
        field = "profile" if "profile" not in values else "content"
        code = "missing_profile_data" if "profile" not in values else "unsupported_export_format"
        raise TransferValidationError(code, field=field) from None
    code: str | None = None
    try:
        profile = ProfileConfigV2.from_dict(values["profile"])
    except (TypeError, ValueError) as error:
        code = _profile_error_code(error)
        profile = None
    if code is not None:
        raise TransferValidationError(code, field="profile")
    assert profile is not None
    if profile.canvas.width != MATRIX_WIDTH or profile.canvas.height != MATRIX_HEIGHT:
        raise TransferValidationError("invalid_geometry", field="profile.canvas") from None
    return ProfileImportResult(
        profile_name=_imported_name(values["profile_name"], existing_names),
        profile=profile,
    )


def import_configuration(raw: object) -> ConfigurationDocumentV2:
    """Validate one complete canonical schema-v2 document."""
    values = _mapping(raw)
    version = values.get("schema_version")
    if version != SCHEMA_VERSION_V2:
        raise TransferValidationError("unsupported_schema_version", field="schema_version") from None
    failed = False
    try:
        configuration = ConfigurationDocumentV2.from_dict(values)
    except (TypeError, ValueError) as error:
        failed = True
        message = str(error).lower()
        if "profiles must contain" in message:
            error_code = "profile_limit_reached"
        elif "at most" in message and "region" in message:
            error_code = "region_limit_reached"
        else:
            error_code = _profile_error_code(error)
            if error_code == "invalid_profile_data":
                error_code = "invalid_configuration"
        configuration = None
    if failed:
        raise TransferValidationError(error_code, field="configuration")
    assert configuration is not None
    return configuration
