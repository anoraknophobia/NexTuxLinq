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
from nextuxlinq.models_v2 import ForegroundRainbowEffect
from nextuxlinq.rendering_v2.effects import (
    EffectInvariantError,
    apply_display_effect,
)
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
from nextuxlinq.rendering_v2.update_policy import (
    UpdatePolicy,
    aggregate_update_policies,
    policy_has_animated_content,
)
from nextuxlinq.widget_models import WidgetType
from nextuxlinq.system_metrics import SystemMetricsSnapshot


class PixelFormat(StrEnum):
    RGB888 = "rgb888"


class CompositorFailure(StrEnum):
    INVALID_CONTEXT = "invalid_context"
    MISSING_WIDGET_DEFINITION = "missing_widget_definition"
    MISSING_WIDGET_RENDERER = "missing_widget_renderer"
    SETTINGS_MISMATCH = "settings_mismatch"
    INVALID_RENDERER_RETURN = "invalid_renderer_return"
    CONTROLLED_RENDERER_FAILURE = "controlled_renderer_failure"
    UNKNOWN_FONT = "unknown_font"
    UNSUPPORTED_GLYPH = "unsupported_glyph"
    CONTENT_TOO_TALL = "content_too_tall"
    CONTENT_TOO_WIDE = "content_too_wide"
    TIME_ZONE_UNAVAILABLE = "time_zone_unavailable"
    DATE_FORMAT_UNAVAILABLE = "date_format_unavailable"
    RENDERER_INVARIANT = "renderer_invariant"
    UNEXPECTED_RENDERER_FAILURE = "unexpected_renderer_failure"
    INVALID_DRAWING = "invalid_drawing"
    INVALID_LOCAL_BUFFER = "invalid_local_buffer"
    EFFECT_INVARIANT = "effect_invariant"


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
    CompositorFailure.UNKNOWN_FONT: "The requested font is unavailable.",
    CompositorFailure.UNSUPPORTED_GLYPH: (
        "The requested font lacks a required glyph."
    ),
    CompositorFailure.CONTENT_TOO_TALL: (
        "The widget content is too tall."
    ),
    CompositorFailure.CONTENT_TOO_WIDE: (
        "The widget content is too wide."
    ),
    CompositorFailure.TIME_ZONE_UNAVAILABLE: (
        "The requested time zone is unavailable."
    ),
    CompositorFailure.DATE_FORMAT_UNAVAILABLE: (
        "The requested date format is unavailable."
    ),
    CompositorFailure.RENDERER_INVARIANT: (
        "The widget renderer received invalid state."
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
    CompositorFailure.EFFECT_INVARIANT: (
        "The display effect could not be applied safely."
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
        field: str | None = None,
    ) -> None:
        self.category = category
        self.region_id = region_id
        self.widget_type = widget_type
        self.field = field
        context = ""
        if region_id is not None and widget_type is not None:
            context = (
                f" Region {region_id!r}, widget {widget_type.value!r}."
            )
        if field is not None:
            context += f" Field {field!r}."
        super().__init__(_FAILURE_MESSAGES[category] + context)


_CONTROLLED_FAILURES = {
    item.value: item
    for item in (
        CompositorFailure.UNKNOWN_FONT,
        CompositorFailure.UNSUPPORTED_GLYPH,
        CompositorFailure.CONTENT_TOO_TALL,
        CompositorFailure.CONTENT_TOO_WIDE,
        CompositorFailure.TIME_ZONE_UNAVAILABLE,
        CompositorFailure.DATE_FORMAT_UNAVAILABLE,
        CompositorFailure.RENDERER_INVARIANT,
        CompositorFailure.UNEXPECTED_RENDERER_FAILURE,
    )
}
_CONTROLLED_FIELDS = frozenset(
    {
        "animation_time",
        "color",
        "content",
        "context",
        "font",
        "format",
        "renderer",
        "settings",
        "surface",
        "time_zone",
    }
)


def _controlled_failure(
    error: WidgetRenderError,
) -> tuple[CompositorFailure, str | None]:
    attributes = vars(error)
    raw_category = attributes.get("category")
    raw_field = attributes.get("field")
    category = (
        _CONTROLLED_FAILURES.get(raw_category)
        if isinstance(raw_category, str)
        else None
    )
    field = (
        raw_field
        if isinstance(raw_field, str) and raw_field in _CONTROLLED_FIELDS
        else None
    )
    return (
        CompositorFailure.CONTROLLED_RENDERER_FAILURE
        if category is None
        else category,
        field,
    )


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
    update_policy: UpdatePolicy
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
        if not isinstance(self.update_policy, UpdatePolicy):
            raise TypeError("update_policy must be an UpdatePolicy.")
        if not isinstance(self.animated_content, bool):
            raise TypeError("animated_content must be a boolean.")
        if self.animated_content is not policy_has_animated_content(
            self.update_policy
        ):
            raise ValueError(
                "animated_content must match the update policy."
            )


def _copy_local_surface(
    destination: RegionSurface,
    region: RegionConfig,
    local_payload: bytes,
    local_mask: bytes,
) -> None:
    expected = region.width * region.height * RGB_CHANNELS
    if len(local_payload) != expected:
        raise SurfaceInvariantError(
            "Local surface export has an invalid byte length."
        )
    if len(local_mask) != region.width * region.height:
        raise SurfaceInvariantError(
            "Local surface mask has an invalid byte length."
        )
    row_bytes = region.width * RGB_CHANNELS
    for local_y in range(region.height):
        start = local_y * row_bytes
        destination._write_rgb_row(
            region.x,
            region.y + local_y,
            local_payload[start : start + row_bytes],
        )
        mask_start = local_y * region.width
        destination._write_mask_row(
            region.x,
            region.y + local_y,
            local_mask[mask_start : mask_start + region.width],
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
        metrics_snapshot: SystemMetricsSnapshot | None = None,
    ) -> RenderResult:
        if not isinstance(profile, ProfileConfigV2):
            raise TypeError("profile must be a ProfileConfigV2.")
        if metrics_snapshot is not None and type(
            metrics_snapshot
        ) is not SystemMetricsSnapshot:
            raise CompositorError(CompositorFailure.INVALID_CONTEXT)
        try:
            validated_generation = _validate_aware_datetime(
                generation_time
            )
            validated_animation = _validate_animation_time(
                animation_time
            )
        except (TypeError, ValueError):
            invalid_context = True
            validated_generation = generation_time
            validated_animation = animation_time
        else:
            invalid_context = False
        if invalid_context:
            raise CompositorError(
                CompositorFailure.INVALID_CONTEXT
            )
        assert isinstance(validated_generation, datetime)
        assert isinstance(validated_animation, float)

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
        update_policy = UpdatePolicy.STATIC

        for region in profile.regions:
            if not region.enabled:
                continue
            widget_type = region.widget.type
            try:
                definition = self._registry.resolve(widget_type)
            except UnknownWidgetDefinitionError:
                definition_missing = True
                definition = None
            else:
                definition_missing = False
            if definition_missing:
                raise CompositorError(
                    CompositorFailure.MISSING_WIDGET_DEFINITION,
                    region_id=region.id,
                    widget_type=widget_type,
                )
            assert definition is not None
            if definition.renderer is None:
                raise CompositorError(
                    CompositorFailure.MISSING_WIDGET_RENDERER,
                    region_id=region.id,
                    widget_type=widget_type,
                )
            if type(region.widget.settings) is not definition.settings_model:
                raise CompositorError(
                    CompositorFailure.SETTINGS_MISMATCH,
                    region_id=region.id,
                    widget_type=widget_type,
                )

            failure: tuple[CompositorFailure, str | None] | None = None
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
                    metrics_snapshot=metrics_snapshot,
                )
                returned = definition.renderer(
                    surface,
                    region.widget.settings,
                    context,
                )
                if returned is not None:
                    failure = (
                        CompositorFailure.INVALID_RENDERER_RETURN,
                        None,
                    )
                else:
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
                    region_policy = definition.resolve_update_policy(
                        region.widget.settings
                    )
                    _copy_local_surface(
                        logical, region, local_payload, surface.foreground_mask()
                    )
            except CompositorError:
                failure = (
                    CompositorFailure.UNEXPECTED_RENDERER_FAILURE,
                    None,
                )
            except RenderContextValidationError:
                failure = (CompositorFailure.INVALID_CONTEXT, None)
            except WidgetRenderError as error:
                failure = _controlled_failure(error)
            except SurfaceInvariantError:
                failure = (CompositorFailure.INVALID_LOCAL_BUFFER, None)
            except (
                SurfaceTypeError,
                SurfaceValidationError,
            ):
                failure = (CompositorFailure.INVALID_DRAWING, None)
            except Exception:
                failure = (
                    CompositorFailure.UNEXPECTED_RENDERER_FAILURE,
                    None,
                )

            if failure is not None:
                category, field = failure
                raise CompositorError(
                    category,
                    region_id=region.id,
                    widget_type=widget_type,
                    field=field,
                )
            update_policy = aggregate_update_policies(
                (update_policy, region_policy)
            )

        logical_framebuffer = logical.to_bytes()
        try:
            logical_framebuffer = apply_display_effect(
                logical_framebuffer,
                logical.foreground_mask(),
                profile.effect,
                animation_time=validated_animation,
                width=MATRIX_WIDTH,
                height=MATRIX_HEIGHT,
            )
        except EffectInvariantError:
            raise CompositorError(CompositorFailure.EFFECT_INVARIANT) from None
        effect_policy = (
            UpdatePolicy.CONTINUOUS
            if isinstance(profile.effect, ForegroundRainbowEffect)
            and profile.effect.speed > 0
            else UpdatePolicy.STATIC
        )
        update_policy = aggregate_update_policies(
            (update_policy, effect_policy)
        )
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
            update_policy=update_policy,
            animated_content=policy_has_animated_content(update_policy),
        )
