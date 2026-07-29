"""Inactive schema-v2 Matrix900 region compositor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nextuxlinq.creator import (
    FRAME_SIZE,
    MATRIX_HEIGHT,
    MATRIX_WIDTH,
    RGB_CHANNELS,
)
from nextuxlinq.models_v2 import ProfileConfigV2, RegionConfig
from nextuxlinq.rendering_v2.registry import (
    RenderContext,
    RenderContextValidationError,
    UnknownWidgetDefinitionError,
    WidgetRegistry,
    WidgetRenderError,
)
from nextuxlinq.rendering_v2.surface import (
    RegionSurface,
    RgbColor,
    SurfaceInvariantError,
    SurfaceTypeError,
    SurfaceValidationError,
)
from nextuxlinq.widget_models import WidgetType


class PixelFormat(StrEnum):
    RGB888 = "rgb888"


class CompositorFailure(StrEnum):
    INVALID_CONTEXT = "invalid_context"
    MISSING_WIDGET_DEFINITION = "missing_widget_definition"
    MISSING_WIDGET_RENDERER = "missing_widget_renderer"
    SETTINGS_MISMATCH = "settings_mismatch"
    INVALID_RENDERER_RETURN = "invalid_renderer_return"
    CONTROLLED_RENDERER_FAILURE = "controlled_renderer_failure"
    UNEXPECTED_RENDERER_FAILURE = "unexpected_renderer_failure"
    INVALID_DRAWING = "invalid_drawing"
    INVALID_LOCAL_BUFFER = "invalid_local_buffer"


_FAILURE_MESSAGES = {
    CompositorFailure.INVALID_CONTEXT: "Rendering context is invalid.",
    CompositorFailure.MISSING_WIDGET_DEFINITION: (
        "No widget definition is registered."
    ),
    CompositorFailure.MISSING_WIDGET_RENDERER: (
        "The widget renderer is not implemented."
    ),
    CompositorFailure.SETTINGS_MISMATCH: (
        "Widget settings do not match the registered renderer."
    ),
    CompositorFailure.INVALID_RENDERER_RETURN: (
        "The widget renderer returned an invalid result."
    ),
    CompositorFailure.CONTROLLED_RENDERER_FAILURE: (
        "The widget renderer reported a rendering failure."
    ),
    CompositorFailure.UNEXPECTED_RENDERER_FAILURE: (
        "The widget renderer failed unexpectedly."
    ),
    CompositorFailure.INVALID_DRAWING: (
        "The widget renderer attempted an invalid drawing operation."
    ),
    CompositorFailure.INVALID_LOCAL_BUFFER: (
        "The widget renderer produced an invalid local framebuffer."
    ),
}


class CompositorError(RuntimeError):
    """One sanitized, categorized complete-frame rendering failure."""

    def __init__(
        self,
        category: CompositorFailure,
        *,
        region_id: str | None = None,
        widget_type: WidgetType | None = None,
    ) -> None:
        self.category = category
        self.region_id = region_id
        self.widget_type = widget_type
        context = ""
        if region_id is not None and widget_type is not None:
            context = (
                f" Region {region_id!r}, widget {widget_type.value!r}."
            )
        super().__init__(_FAILURE_MESSAGES[category] + context)


def _strict_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    return value


def _validate_aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("generation_time must be a datetime.")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "generation_time must be timezone-aware."
            )
    except (OverflowError, ValueError) as error:
        raise ValueError(
            "generation_time must be timezone-aware."
        ) from error
    return value


def _validate_animation_time(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "animation_time must be a finite non-negative number."
        )
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(
            "animation_time must be a finite non-negative number."
        ) from error
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(
            "animation_time must be a finite non-negative number."
        )
    return normalized


@dataclass(frozen=True, slots=True)
class PreviewRegionV2:
    """Two-dimensional editor metadata for one persisted region."""

    id: str
    name: str | None
    x: int
    y: int
    width: int
    height: int
    enabled: bool
    widget_type: WidgetType

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise TypeError("region metadata id must be a string.")
        if self.name is not None and not isinstance(self.name, str):
            raise TypeError(
                "region metadata name must be a string or null."
            )
        for name, value in (
            ("x", self.x),
            ("y", self.y),
            ("width", self.width),
            ("height", self.height),
        ):
            _strict_integer(value, name)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean.")
        if not isinstance(self.widget_type, WidgetType):
            raise TypeError("widget_type must be a WidgetType.")

    @classmethod
    def from_region(cls, region: RegionConfig) -> PreviewRegionV2:
        return cls(
            id=region.id,
            name=region.name,
            x=region.x,
            y=region.y,
            width=region.width,
            height=region.height,
            enabled=region.enabled,
            widget_type=region.widget.type,
        )


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Immutable logical and Creator-order output from one composition."""

    logical_framebuffer: bytes
    device_framebuffer: bytes
    width: int
    height: int
    pixel_format: PixelFormat
    regions: tuple[PreviewRegionV2, ...]
    generation_time: datetime
    animation_time: float
    animated_content: bool

    def __post_init__(self) -> None:
        logical = bytes(self.logical_framebuffer)
        device = bytes(self.device_framebuffer)
        if len(logical) != FRAME_SIZE or len(device) != FRAME_SIZE:
            raise ValueError(
                f"framebuffers must contain exactly {FRAME_SIZE} bytes."
            )
        object.__setattr__(self, "logical_framebuffer", logical)
        object.__setattr__(self, "device_framebuffer", device)
        if (
            _strict_integer(self.width, "width") != MATRIX_WIDTH
            or _strict_integer(self.height, "height") != MATRIX_HEIGHT
        ):
            raise ValueError(
                f"render result dimensions must be "
                f"{MATRIX_WIDTH}x{MATRIX_HEIGHT}."
            )
        try:
            pixel_format = PixelFormat(self.pixel_format)
        except (TypeError, ValueError) as error:
            raise ValueError("pixel_format must be rgb888.") from error
        object.__setattr__(self, "pixel_format", pixel_format)
        regions = tuple(self.regions)
        if any(not isinstance(item, PreviewRegionV2) for item in regions):
            raise TypeError(
                "regions must contain PreviewRegionV2 values."
            )
        object.__setattr__(self, "regions", regions)
        object.__setattr__(
            self,
            "generation_time",
            _validate_aware_datetime(self.generation_time),
        )
        object.__setattr__(
            self,
            "animation_time",
            _validate_animation_time(self.animation_time),
        )
        if not isinstance(self.animated_content, bool):
            raise TypeError("animated_content must be a boolean.")


def _copy_local_surface(
    destination: RegionSurface,
    region: RegionConfig,
    local_payload: bytes,
) -> None:
    expected = region.width * region.height * RGB_CHANNELS
    if len(local_payload) != expected:
        raise SurfaceInvariantError(
            "Local surface export has an invalid byte length."
        )
    row_bytes = region.width * RGB_CHANNELS
    for local_y in range(region.height):
        start = local_y * row_bytes
        destination._write_rgb_row(
            region.x,
            region.y + local_y,
            local_payload[start : start + row_bytes],
        )


def _logical_to_device(
    logical_framebuffer: bytes,
    profile: ProfileConfigV2,
    background: RgbColor,
) -> bytes:
    if len(logical_framebuffer) != FRAME_SIZE:
        raise SurfaceInvariantError(
            "Logical framebuffer has an invalid byte length."
        )
    device = RegionSurface(MATRIX_WIDTH, MATRIX_HEIGHT, background)
    for region in profile.regions:
        if not region.enabled:
            continue
        raw_x = MATRIX_WIDTH - region.x - region.width
        row_bytes = region.width * RGB_CHANNELS
        for local_y in range(region.height):
            source = (
                (
                    (region.y + local_y) * MATRIX_WIDTH
                    + region.x
                )
                * RGB_CHANNELS
            )
            device._write_rgb_row(
                raw_x,
                region.y + local_y,
                logical_framebuffer[source : source + row_bytes],
            )
    return device.to_bytes()


class Compositor:
    """Compose one inactive schema-v2 profile without runtime side effects."""

    __slots__ = ("_registry",)

    def __init__(self, registry: WidgetRegistry) -> None:
        if not isinstance(registry, WidgetRegistry):
            raise TypeError("registry must be a WidgetRegistry.")
        self._registry = registry

    def render(
        self,
        profile: ProfileConfigV2,
        *,
        generation_time: datetime,
        animation_time: float,
    ) -> RenderResult:
        if not isinstance(profile, ProfileConfigV2):
            raise TypeError("profile must be a ProfileConfigV2.")
        try:
            validated_generation = _validate_aware_datetime(
                generation_time
            )
            validated_animation = _validate_animation_time(
                animation_time
            )
        except (TypeError, ValueError) as error:
            raise CompositorError(
                CompositorFailure.INVALID_CONTEXT
            ) from error

        canvas_color = RgbColor.from_schema_color(
            profile.canvas.background_color
        )
        logical = RegionSurface(
            MATRIX_WIDTH, MATRIX_HEIGHT, canvas_color
        )
        metadata = tuple(
            PreviewRegionV2.from_region(region)
            for region in profile.regions
        )
        animated_content = False

        for region in profile.regions:
            if not region.enabled:
                continue
            widget_type = region.widget.type
            try:
                definition = self._registry.resolve(widget_type)
            except UnknownWidgetDefinitionError as error:
                raise CompositorError(
                    CompositorFailure.MISSING_WIDGET_DEFINITION,
                    region_id=region.id,
                    widget_type=widget_type,
                ) from error
            if definition.renderer is None:
                raise CompositorError(
                    CompositorFailure.MISSING_WIDGET_RENDERER,
                    region_id=region.id,
                    widget_type=widget_type,
                )
            if not isinstance(
                region.widget.settings, definition.settings_model
            ):
                raise CompositorError(
                    CompositorFailure.SETTINGS_MISMATCH,
                    region_id=region.id,
                    widget_type=widget_type,
                )

            try:
                region_color = (
                    canvas_color
                    if region.background_color is None
                    else RgbColor.from_schema_color(
                        region.background_color
                    )
                )
                surface = RegionSurface(
                    region.width, region.height, region_color
                )
                context = RenderContext(
                    wall_clock=validated_generation,
                    animation_time=validated_animation,
                    canvas_width=MATRIX_WIDTH,
                    canvas_height=MATRIX_HEIGHT,
                    region_id=region.id,
                    region_width=region.width,
                    region_height=region.height,
                )
                returned = definition.renderer(
                    surface,
                    region.widget.settings,
                    context,
                )
                if returned is not None:
                    raise CompositorError(
                        CompositorFailure.INVALID_RENDERER_RETURN,
                        region_id=region.id,
                        widget_type=widget_type,
                    )
                local_payload = surface.to_bytes()
                expected_length = (
                    region.width
                    * region.height
                    * RGB_CHANNELS
                )
                if len(local_payload) != expected_length:
                    raise SurfaceInvariantError(
                        "Local surface export has an invalid byte length."
                    )
                _copy_local_surface(logical, region, local_payload)
            except CompositorError:
                raise
            except RenderContextValidationError as error:
                raise CompositorError(
                    CompositorFailure.INVALID_CONTEXT,
                    region_id=region.id,
                    widget_type=widget_type,
                ) from error
            except WidgetRenderError as error:
                raise CompositorError(
                    CompositorFailure.CONTROLLED_RENDERER_FAILURE,
                    region_id=region.id,
                    widget_type=widget_type,
                ) from error
            except SurfaceInvariantError as error:
                raise CompositorError(
                    CompositorFailure.INVALID_LOCAL_BUFFER,
                    region_id=region.id,
                    widget_type=widget_type,
                ) from error
            except (
                SurfaceTypeError,
                SurfaceValidationError,
            ) as error:
                raise CompositorError(
                    CompositorFailure.INVALID_DRAWING,
                    region_id=region.id,
                    widget_type=widget_type,
                ) from error
            except Exception as error:
                raise CompositorError(
                    CompositorFailure.UNEXPECTED_RENDERER_FAILURE,
                    region_id=region.id,
                    widget_type=widget_type,
                ) from error

            animated_content = animated_content or definition.animated

        logical_framebuffer = logical.to_bytes()
        device_framebuffer = _logical_to_device(
            logical_framebuffer, profile, canvas_color
        )
        return RenderResult(
            logical_framebuffer=logical_framebuffer,
            device_framebuffer=device_framebuffer,
            width=MATRIX_WIDTH,
            height=MATRIX_HEIGHT,
            pixel_format=PixelFormat.RGB888,
            regions=metadata,
            generation_time=validated_generation,
            animation_time=validated_animation,
            animated_content=animated_content,
        )
