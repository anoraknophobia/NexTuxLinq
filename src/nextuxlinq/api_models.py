"""Strict Pydantic schemas for the local NexTuxLinq HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from nextuxlinq.models import ConfigurationDocument, LayoutConfig, ProfileConfig


class DomainValidationError(ValueError):
    """Field-safe validation failure raised at the HTTP/domain boundary."""


def _convert_domain(factory, value):
    try:
        return factory(value)
    except (TypeError, ValueError) as error:
        raise DomainValidationError(str(error)) from error


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayoutModel(StrictModel):
    front_clock_width: StrictInt
    gap_width: StrictInt
    scroll_text_width: StrictInt
    rear_clock_width: StrictInt

    def to_domain(self) -> LayoutConfig:
        return _convert_domain(LayoutConfig.from_dict, self.model_dump())


class ProfileModel(StrictModel):
    scrolling_text: str
    text_color: str
    clock_color: str
    scroll_speed: StrictFloat | StrictInt
    brightness: StrictInt | None
    layout: LayoutModel

    def to_domain(self) -> ProfileConfig:
        return _convert_domain(ProfileConfig.from_dict, self.model_dump())

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
    configuration: ConfigurationModel
    built_in_default: ConfigurationModel
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


class PreviewResponse(StrictModel):
    width: int
    height: int
    pixel_format: str
    orientation: str
    framebuffer_base64: str
    active_revision: int
    generation_timestamp: float
    regions: list[PreviewRegion]


class ErrorResponse(StrictModel):
    code: str
    message: str
    field_errors: list[dict[str, Any]] | None = None
    request_id: str
