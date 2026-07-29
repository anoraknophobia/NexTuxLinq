"""Strict Pydantic schemas for the local NexTuxLinq HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from nextuxlinq.models import ConfigurationDocument, LayoutConfig, ProfileConfig
from nextuxlinq.models_v2 import ConfigurationDocumentV2


class DomainValidationError(ValueError):
    """Field-safe validation failure raised at the HTTP/domain boundary."""

    def __init__(self, *, field: str) -> None:
        self.field = field
        super().__init__("The request contains invalid configuration data.")


def _convert_domain(factory, value, *, field: str = "configuration"):
    failed = False
    try:
        return factory(value)
    except (TypeError, ValueError):
        failed = True
    if failed:
        raise DomainValidationError(field=field)
    raise AssertionError("Unreachable domain conversion state.")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayoutModel(StrictModel):
    front_clock_width: StrictInt
    gap_width: StrictInt
    scroll_text_width: StrictInt
    rear_clock_width: StrictInt

    def to_domain(self) -> LayoutConfig:
        return _convert_domain(
            LayoutConfig.from_dict,
            self.model_dump(),
            field="profile.layout",
        )


class ProfileModel(StrictModel):
    scrolling_text: str
    text_color: str
    clock_color: str
    scroll_speed: StrictFloat | StrictInt
    brightness: StrictInt | None
    layout: LayoutModel

    def to_domain(self) -> ProfileConfig:
        return _convert_domain(
            ProfileConfig.from_dict,
            self.model_dump(),
            field="profile",
        )

    @classmethod
    def from_domain(cls, profile: ProfileConfig) -> ProfileModel:
        return cls.model_validate(profile.to_dict())


class ConfigurationModel(StrictModel):
    schema_version: StrictInt
    active_profile: str
    profiles: dict[str, ProfileModel]

    def to_domain(self) -> ConfigurationDocument:
        return _convert_domain(ConfigurationDocument.from_dict, self.model_dump())

    @classmethod
    def from_domain(
        cls,
        configuration: ConfigurationDocument,
    ) -> ConfigurationModel:
        return cls.model_validate(configuration.to_dict())


class ApplyRequest(StrictModel):
    profile_name: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    profile: ProfileModel
    expected_revision: StrictInt | None = None


class ApplyResponse(StrictModel):
    profile_name: str
    profile: ProfileModel
    active_revision: int
    dirty: bool


class SaveRequest(StrictModel):
    configuration: ConfigurationModel
    expected_active_revision: StrictInt | None
    expected_saved_revision: StrictInt | None
    apply: bool = True


class SaveResponse(StrictModel):
    configuration: ConfigurationModel
    active_revision: int
    saved_revision: int | None
    dirty: bool
    warning: str | None


class ConfigResponse(StrictModel):
    configuration: ConfigurationModel | None
    built_in_default: ConfigurationModel
    canonical_configuration: dict[str, Any]
    legacy_editor_supported: bool
    legacy_editor_message: str | None
    active_revision: int
    saved_revision: int | None
    dirty: bool


class StatusResponse(StrictModel):
    daemon_state: str
    device_available: bool
    model_name: str | None
    device_path: str | None
    usb_vid: str
    usb_pid: str
    multiple_device_warning: str | None
    active_profile: str
    active_revision: int
    saved_revision: int | None
    dirty: bool
    rendering_state: str
    update_policy: str
    frame_rate: float
    last_frame_generation_time: float | None
    last_successful_upload_time: float | None
    last_display_error: str | None
    configuration_warning: str | None


class PreviewRegion(StrictModel):
    name: str
    start_pixel: int
    width: int
    type: str
    id: str | None = None
    y: int = 0
    height: int = 10
    enabled: bool = True
    background_color: str | None = None
    widget_type: str | None = None


class PreviewResponse(StrictModel):
    width: int
    height: int
    pixel_format: str
    orientation: str
    framebuffer_base64: str
    active_revision: int
    generation_timestamp: float
    regions: list[PreviewRegion]
    update_policy: str
    animated_content: bool


class CanonicalConfigurationModel(StrictModel):
    schema_version: StrictInt
    active_profile: str
    profiles: dict[str, Any]

    def to_domain(self) -> ConfigurationDocumentV2:
        return _convert_domain(
            ConfigurationDocumentV2.from_dict,
            self.model_dump(),
        )

    @classmethod
    def from_domain(
        cls,
        configuration: ConfigurationDocumentV2,
    ) -> CanonicalConfigurationModel:
        return cls.model_validate(configuration.to_dict())


class CanonicalConfigResponse(StrictModel):
    configuration: CanonicalConfigurationModel
    built_in_default: CanonicalConfigurationModel
    active_revision: int
    saved_revision: int | None
    dirty: bool
    legacy_editor_supported: bool


class WidgetEditorDefinition(StrictModel):
    type: str
    label: str
    default_settings: dict[str, Any]
    minimum_width: int | None
    minimum_height: int | None


class FreeLayoutMetadataResponse(StrictModel):
    canvas_width: int
    canvas_height: int
    max_profiles: int
    max_regions_per_profile: int
    max_regions_per_document: int
    widget_definitions: list[WidgetEditorDefinition]


class CanonicalValidateRequest(StrictModel):
    configuration: CanonicalConfigurationModel


class CanonicalValidateResponse(StrictModel):
    valid: bool
    update_policy: str
    animated_content: bool
    framebuffer_base64: str


class CanonicalApplyRequest(StrictModel):
    configuration: CanonicalConfigurationModel
    expected_revision: StrictInt | None = None


class CanonicalApplyResponse(StrictModel):
    configuration: CanonicalConfigurationModel
    active_revision: int
    dirty: bool
    update_policy: str


class CanonicalSaveRequest(StrictModel):
    configuration: CanonicalConfigurationModel
    expected_active_revision: StrictInt | None
    expected_saved_revision: StrictInt | None
    apply: bool = True


class CanonicalSaveResponse(StrictModel):
    configuration: CanonicalConfigurationModel
    active_revision: int
    saved_revision: int | None
    dirty: bool
    warning: str | None


class ProfileSelectionRequest(StrictModel):
    profile_name: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    expected_revision: StrictInt | None = None


class ErrorResponse(StrictModel):
    code: str
    message: str
    field_errors: list[dict[str, Any]] | None = None
    request_id: str
