"""Widget rendering contracts and inactive Phase 2 registry construction."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeVar

from nextuxlinq.creator import MATRIX_HEIGHT, MATRIX_WIDTH
from nextuxlinq.rendering_v2.surface import RegionSurface
from nextuxlinq.widget_models import (
    BlankSettings,
    CANONICAL_WIDGET_SETTINGS_MODELS,
    WidgetSettings,
    WidgetType,
)

_REGION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SettingsT = TypeVar("SettingsT", bound=WidgetSettings)


class RenderContextValidationError(ValueError):
    """Injected rendering context data is invalid."""


class WidgetRenderError(RuntimeError):
    """A renderer-reported controlled failure.

    The compositor deliberately does not expose the exception message.
    """


class DuplicateWidgetDefinitionError(ValueError):
    """More than one definition used the same stable widget type."""


class UnknownWidgetDefinitionError(LookupError):
    """No definition is registered for a schema widget type."""

    def __init__(self, widget_type: WidgetType) -> None:
        super().__init__(
            f"No widget definition is registered for {widget_type.value}."
        )
        self.widget_type = widget_type


def _strict_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RenderContextValidationError(f"{name} must be an integer.")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderContext:
    """All time and geometry inputs available to one widget renderer."""

    wall_clock: datetime
    animation_time: float
    canvas_width: int
    canvas_height: int
    region_id: str
    region_width: int
    region_height: int

    def __post_init__(self) -> None:
        if not isinstance(self.wall_clock, datetime):
            raise RenderContextValidationError(
                "wall_clock must be a datetime."
            )
        try:
            aware = (
                self.wall_clock.tzinfo is not None
                and self.wall_clock.utcoffset() is not None
            )
        except (OverflowError, ValueError) as error:
            raise RenderContextValidationError(
                "wall_clock must have a valid timezone."
            ) from error
        if not aware:
            raise RenderContextValidationError(
                "wall_clock must be timezone-aware."
            )

        if isinstance(self.animation_time, bool) or not isinstance(
            self.animation_time, (int, float)
        ):
            raise RenderContextValidationError(
                "animation_time must be a finite non-negative number."
            )
        try:
            animation_time = float(self.animation_time)
        except (OverflowError, TypeError, ValueError) as error:
            raise RenderContextValidationError(
                "animation_time must be a finite non-negative number."
            ) from error
        if not math.isfinite(animation_time) or animation_time < 0:
            raise RenderContextValidationError(
                "animation_time must be a finite non-negative number."
            )
        object.__setattr__(self, "animation_time", animation_time)

        canvas_width = _strict_integer(
            self.canvas_width, "canvas_width"
        )
        canvas_height = _strict_integer(
            self.canvas_height, "canvas_height"
        )
        if canvas_width != MATRIX_WIDTH or canvas_height != MATRIX_HEIGHT:
            raise RenderContextValidationError(
                f"canvas dimensions must be {MATRIX_WIDTH}x{MATRIX_HEIGHT}."
            )
        region_width = _strict_integer(
            self.region_width, "region_width"
        )
        region_height = _strict_integer(
            self.region_height, "region_height"
        )
        if not 1 <= region_width <= canvas_width:
            raise RenderContextValidationError(
                "region_width must fit inside the canvas."
            )
        if not 1 <= region_height <= canvas_height:
            raise RenderContextValidationError(
                "region_height must fit inside the canvas."
            )
        if not isinstance(self.region_id, str) or not _REGION_ID.fullmatch(
            self.region_id
        ):
            raise RenderContextValidationError(
                "region_id must be a validated region identifier."
            )


class WidgetRenderer(Protocol[SettingsT]):
    """Draw one widget into its isolated local surface."""

    def __call__(
        self,
        surface: RegionSurface,
        settings: SettingsT,
        context: RenderContext,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class WidgetDefinition(Generic[SettingsT]):
    """One immutable renderer definition and safe editor metadata."""

    widget_type: WidgetType
    display_label: str
    settings_model: type[SettingsT]
    default_settings: SettingsT
    renderer: WidgetRenderer[SettingsT] | None
    animated: bool
    minimum_width: int | None = None
    minimum_height: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.widget_type, WidgetType):
            raise TypeError("widget_type must be a WidgetType.")
        if (
            not isinstance(self.display_label, str)
            or not self.display_label
            or self.display_label != self.display_label.strip()
            or not self.display_label.isprintable()
        ):
            raise ValueError(
                "display_label must be non-empty printable text."
            )
        if not isinstance(self.settings_model, type):
            raise TypeError("settings_model must be a type.")
        canonical_model = CANONICAL_WIDGET_SETTINGS_MODELS[
            self.widget_type
        ]
        if self.settings_model is not canonical_model:
            raise TypeError(
                f"{self.widget_type.value} widget definition has an "
                "incompatible settings model."
            )
        if type(self.default_settings) is not canonical_model:
            raise TypeError(
                f"{self.widget_type.value} widget definition has "
                "incompatible default settings."
            )
        if self.renderer is not None and not callable(self.renderer):
            raise TypeError("renderer must be callable or null.")
        if not isinstance(self.animated, bool):
            raise TypeError("animated must be a boolean.")
        self._validate_minimum(
            self.minimum_width, "minimum_width", MATRIX_WIDTH
        )
        self._validate_minimum(
            self.minimum_height, "minimum_height", MATRIX_HEIGHT
        )

    @staticmethod
    def _validate_minimum(
        value: int | None, name: str, maximum: int
    ) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer or null.")
        if not 1 <= value <= maximum:
            raise ValueError(
                f"{name} must be between 1 and {maximum}."
            )


class WidgetRegistry:
    """An immutable mapping of widget types to renderer definitions."""

    __slots__ = ("_definitions",)

    def __init__(
        self, definitions: Iterable[WidgetDefinition[WidgetSettings]]
    ) -> None:
        if isinstance(definitions, (str, bytes)):
            raise TypeError("definitions must be an iterable.")
        collected: dict[
            WidgetType, WidgetDefinition[WidgetSettings]
        ] = {}
        for definition in definitions:
            if not isinstance(definition, WidgetDefinition):
                raise TypeError(
                    "definitions must contain WidgetDefinition values."
                )
            if definition.widget_type in collected:
                raise DuplicateWidgetDefinitionError(
                    "A widget type may be registered only once."
                )
            collected[definition.widget_type] = definition
        self._definitions: Mapping[
            WidgetType, WidgetDefinition[WidgetSettings]
        ] = MappingProxyType(collected)

    def resolve(
        self, widget_type: WidgetType
    ) -> WidgetDefinition[WidgetSettings]:
        if not isinstance(widget_type, WidgetType):
            raise TypeError("widget_type must be a WidgetType.")
        try:
            return self._definitions[widget_type]
        except KeyError as error:
            raise UnknownWidgetDefinitionError(widget_type) from error

    @property
    def definitions(
        self,
    ) -> tuple[WidgetDefinition[WidgetSettings], ...]:
        return tuple(self._definitions.values())


def render_blank(
    surface: RegionSurface,
    settings: BlankSettings,
    context: RenderContext,
) -> None:
    """Render no foreground content into an initialized region."""
    if not isinstance(surface, RegionSurface):
        raise TypeError("surface must be a RegionSurface.")
    if not isinstance(settings, BlankSettings):
        raise TypeError("settings must be BlankSettings.")
    if not isinstance(context, RenderContext):
        raise TypeError("context must be a RenderContext.")
    return None


def create_phase2_widget_registry() -> WidgetRegistry:
    """Create a new isolated registry containing only Nothing / Gap."""
    definition: WidgetDefinition[BlankSettings] = WidgetDefinition(
        widget_type=WidgetType.BLANK,
        display_label="Nothing / Gap",
        settings_model=BlankSettings,
        default_settings=BlankSettings(),
        renderer=render_blank,
        animated=False,
        minimum_width=1,
        minimum_height=1,
    )
    return WidgetRegistry((definition,))  # type: ignore[arg-type]
