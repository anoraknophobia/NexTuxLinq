"""Deterministic rendering for System Metric and Metric Bar widgets."""

from __future__ import annotations

import math
from dataclasses import dataclass

from nextuxlinq.rendering_v2.fonts import (
    FontDefinition,
    FontFitAnalysis,
    FontFitRequirement,
    FontRegistry,
    FontTextError,
    GlyphBitmap,
    analyze_font_fit,
    create_builtin_font_registry,
    draw_glyph,
    horizontal_origin,
    measure_normalized_text,
    resolve_analyzed_font,
    vertical_origin,
)
from nextuxlinq.rendering_v2.registry import RenderContext
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.system_metrics import (
    METRIC_DEFINITIONS,
    MetricValueType,
    format_metric_value,
    metric_value_format_envelope,
)
from nextuxlinq.widget_models import (
    BarDirection,
    BarOrientation,
    FontChoice,
    MetricBarSettings,
    MetricDisplayMode,
    SystemMetricSettings,
    TemperatureUnit,
    TextOverflow,
)


def _font_failure(error: FontTextError) -> tuple[str, str]:
    mapping = {
        "unknown_font": ("unknown_font", "font"),
        "unsupported_glyph": ("unsupported_glyph", "content"),
        "content_too_tall": ("content_too_tall", "font"),
        "content_too_wide": ("content_too_wide", "content"),
        "renderer_invariant": ("renderer_invariant", "renderer"),
    }
    return mapping.get(error.category, ("renderer_invariant", "renderer"))


_PERCENT_ROWS = {
    5: ("101", "001", "010", "100", "101"),
    7: ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
}


def _metric_font_registry() -> FontRegistry:
    """Extend fresh built-ins with the canonical ASCII percent suffix only."""
    definitions = []
    for definition in create_builtin_font_registry().definitions:
        glyphs = dict(definition.glyphs)
        if "%" not in glyphs:
            rows = _PERCENT_ROWS[definition.glyph_height]
            if definition.glyph_height == 7 and max(
                glyph.width for glyph in definition.glyphs.values()
            ) <= 3:
                rows = ("101", "001", "010", "010", "010", "100", "101")
                glyphs["-"] = GlyphBitmap.from_strings(
                    ("000", "000", "000", "111", "000", "000", "000")
                )
            glyphs["%"] = GlyphBitmap.from_strings(rows)
        definitions.append(FontDefinition(
            font_id=definition.font_id,
            display_label=definition.display_label,
            glyphs=glyphs,
            character_spacing=definition.character_spacing,
        ))
    return FontRegistry(definitions)


def metric_text(settings: SystemMetricSettings, context: RenderContext) -> str:
    """Return the exact safe display text for one snapshot metric."""
    snapshot = context.metrics_snapshot
    value = None if snapshot is None else snapshot.value(settings.metric)
    rendered = settings.unavailable_text if value is None else (
        format_temperature_value(settings, value)
        if _is_temperature(settings)
        else format_metric_value(settings.metric, value, settings.precision)
    )
    if settings.display_mode is MetricDisplayMode.LABEL_AND_VALUE:
        if _is_temperature(settings):
            return f"{settings.label}{rendered}"
        return f"{settings.label} {rendered}"
    return rendered


def _is_temperature(settings: SystemMetricSettings) -> bool:
    return (
        METRIC_DEFINITIONS[settings.metric].value_type
        is MetricValueType.TEMPERATURE
    )


def format_temperature_value(
    settings: SystemMetricSettings,
    celsius_value: float,
) -> str:
    """Format one canonical Celsius snapshot using the selected display unit."""
    value = float(celsius_value)
    suffix = "°"
    if settings.temperature_unit is TemperatureUnit.CELSIUS:
        suffix = "°C"
    elif settings.temperature_unit is TemperatureUnit.FAHRENHEIT:
        value = value * 9.0 / 5.0 + 32.0
        suffix = "°F"
    return f"{value:.{settings.precision}f}{suffix}"


def _temperature_format_envelope(
    settings: SystemMetricSettings,
) -> tuple[str, ...]:
    integer = (
        "999"
        if settings.temperature_unit is TemperatureUnit.FAHRENHEIT
        else "99"
    )
    fraction = f".{('0' * settings.precision)}" if settings.precision else ""
    suffix = {
        TemperatureUnit.DEGREE_ONLY: "°",
        TemperatureUnit.CELSIUS: "°C",
        TemperatureUnit.FAHRENHEIT: "°F",
    }[settings.temperature_unit]
    return (f"{integer}{fraction}{suffix}",)


@dataclass(frozen=True, slots=True)
class SystemMetricGlyphPlacement:
    """One deterministic visible or reserved glyph cell in local coordinates."""

    character: str
    origin_x: int
    origin_y: int
    advance: int
    visible: bool
    role: str


@dataclass(frozen=True, slots=True)
class SystemMetricTextLayout:
    """Immutable System Metric layout metadata used by rendering and tests."""

    font_id: FontChoice
    glyph_height: int
    measured_width: int
    origin_x: int
    origin_y: int
    placements: tuple[SystemMetricGlyphPlacement, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _GlyphSlot:
    character: str
    advance: int
    glyph_offset: int
    gap_before: int
    visible: bool
    role: str


_COMPACT_METRIC_GAP = 1


def _ordinary_slots(
    font: FontDefinition,
    content: str,
    *,
    role: str,
    initial_gap: int = 0,
) -> tuple[_GlyphSlot, ...]:
    return tuple(
        _GlyphSlot(
            character,
            font.glyphs[character].width,
            0,
            initial_gap if index == 0 else font.character_spacing,
            True,
            role,
        )
        for index, character in enumerate(content)
    )


def _digit_slots(
    font: FontDefinition,
    digits: str,
    *,
    minimum_cells: int,
    initial_gap: int,
) -> tuple[_GlyphSlot, ...]:
    cell_width = max(font.glyphs[str(digit)].width for digit in range(10))
    hidden_count = max(0, minimum_cells - len(digits))
    characters = f"{'0' * hidden_count}{digits}"
    return tuple(
        _GlyphSlot(
            character,
            cell_width,
            (cell_width - font.glyphs[character].width) // 2,
            initial_gap if index == 0 else _COMPACT_METRIC_GAP,
            index >= hidden_count,
            "digit",
        )
        for index, character in enumerate(characters)
    )


def _fixed_value_slots(
    font: FontDefinition,
    settings: SystemMetricSettings,
    value_text: str,
    *,
    initial_gap: int,
) -> tuple[_GlyphSlot, ...] | None:
    definition = METRIC_DEFINITIONS[settings.metric]
    if definition.value_type is MetricValueType.PERCENTAGE:
        if not value_text.endswith("%"):
            return None
        numeric = value_text[:-1]
        integer, separator, fraction = numeric.partition(".")
        if not integer.isdigit() or (separator and not fraction.isdigit()):
            return None
        slots = list(_digit_slots(
            font,
            integer,
            minimum_cells=2,
            initial_gap=initial_gap,
        ))
        if separator:
            slots.extend(_ordinary_slots(
                font,
                separator,
                role="decimal",
                initial_gap=_COMPACT_METRIC_GAP,
            ))
            slots.extend(_digit_slots(
                font,
                fraction,
                minimum_cells=0,
                initial_gap=_COMPACT_METRIC_GAP,
            ))
        slots.extend(_ordinary_slots(
            font,
            "%",
            role="suffix",
            initial_gap=_COMPACT_METRIC_GAP,
        ))
        return tuple(slots)
    if definition.value_type is MetricValueType.TEMPERATURE:
        suffix = {
            TemperatureUnit.DEGREE_ONLY: "°",
            TemperatureUnit.CELSIUS: "°C",
            TemperatureUnit.FAHRENHEIT: "°F",
        }[settings.temperature_unit]
        if not value_text.endswith(suffix):
            return None
        numeric = value_text[:-len(suffix)]
        sign = ""
        if numeric.startswith("-"):
            sign, numeric = "-", numeric[1:]
        integer, separator, fraction = numeric.partition(".")
        if not integer.isdigit() or (separator and not fraction.isdigit()):
            return None
        slots: list[_GlyphSlot] = []
        if sign:
            slots.extend(_ordinary_slots(
                font, sign, role="sign", initial_gap=initial_gap
            ))
            initial_gap = _COMPACT_METRIC_GAP
        slots.extend(_digit_slots(
            font,
            integer,
            minimum_cells=(
                3
                if settings.temperature_unit is TemperatureUnit.FAHRENHEIT
                else 2
            ),
            initial_gap=initial_gap,
        ))
        if separator:
            slots.extend(_ordinary_slots(
                font,
                separator,
                role="decimal",
                initial_gap=_COMPACT_METRIC_GAP,
            ))
            slots.extend(_digit_slots(
                font,
                fraction,
                minimum_cells=0,
                initial_gap=_COMPACT_METRIC_GAP,
            ))
        slots.extend(_ordinary_slots(
            font,
            "°",
            role="degree",
            initial_gap=0,
        ))
        if len(suffix) == 2:
            slots.extend(_ordinary_slots(
                font,
                suffix[1],
                role="suffix",
                initial_gap=0,
            ))
        return tuple(slots)
    return None


def _metric_slots(
    font: FontDefinition,
    settings: SystemMetricSettings,
    value_text: str,
    *,
    available: bool,
) -> tuple[_GlyphSlot, ...]:
    has_label = settings.display_mode is MetricDisplayMode.LABEL_AND_VALUE
    temperature = _is_temperature(settings)
    prefix = (
        settings.label
        if has_label and temperature
        else f"{settings.label} " if has_label else ""
    )
    content = f"{prefix}{value_text}"
    measure_normalized_text(font, content)
    value_slots = (
        _fixed_value_slots(
            font,
            settings,
            value_text,
            initial_gap=_COMPACT_METRIC_GAP if has_label else 0,
        )
        if available
        else None
    )
    if value_slots is not None:
        label_slots = (
            _ordinary_slots(font, settings.label, role="label")
            if has_label else ()
        )
        return (
            *label_slots,
            *value_slots,
        )
    if temperature:
        return (
            *(
                _ordinary_slots(font, settings.label, role="label")
                if has_label else ()
            ),
            *_ordinary_slots(
                font,
                value_text,
                role="value",
                initial_gap=_COMPACT_METRIC_GAP if has_label else 0,
            ),
        )
    return (
        *_ordinary_slots(font, prefix, role="label"),
        *_ordinary_slots(
            font,
            value_text,
            role="value",
            initial_gap=font.character_spacing if prefix else 0,
        ),
    )


def _slots_width(font: FontDefinition, slots: tuple[_GlyphSlot, ...]) -> int:
    if not slots:
        return 0
    return sum(slot.gap_before + slot.advance for slot in slots)


def _font_envelope_widths(
    font: FontDefinition,
    settings: SystemMetricSettings,
) -> tuple[int, ...]:
    widths = [
        _slots_width(
            font,
            _metric_slots(
                font, settings, value_text, available=True
            ),
        )
        for value_text in (
            _temperature_format_envelope(settings)
            if _is_temperature(settings)
            else metric_value_format_envelope(settings.metric, settings.precision)
        )
    ]
    widths.append(_slots_width(
        font,
        _metric_slots(
            font, settings, settings.unavailable_text, available=False
        ),
    ))
    return tuple(widths)


def metric_text_format_envelope(settings: SystemMetricSettings) -> tuple[str, ...]:
    """Return snapshot-independent text forms used to choose one stable font."""
    values = (*(
        _temperature_format_envelope(settings)
        if _is_temperature(settings)
        else metric_value_format_envelope(settings.metric, settings.precision)
    ), settings.unavailable_text)
    if settings.display_mode is MetricDisplayMode.LABEL_AND_VALUE:
        separator = "" if _is_temperature(settings) else " "
        values = tuple(f"{settings.label}{separator}{value}" for value in values)
    return tuple(dict.fromkeys(values))


def resolve_system_metric_font(
    settings: SystemMetricSettings,
    *,
    region_width: int,
    region_height: int,
) -> FontDefinition:
    """Resolve a concrete font from the widget's complete format envelope."""
    registry = _metric_font_registry()
    return resolve_analyzed_font(
        registry,
        analyze_system_metric_font_fit(
            settings,
            region_width=region_width,
            region_height=region_height,
            registry=registry,
        ),
    )


def analyze_system_metric_font_fit(
    settings: SystemMetricSettings,
    *,
    region_width: int,
    region_height: int,
    registry: FontRegistry | None = None,
) -> FontFitAnalysis:
    """Analyze the complete snapshot-independent compact metric envelope."""
    font_registry = registry or _metric_font_registry()

    def requirement(font: FontDefinition) -> FontFitRequirement:
        try:
            widths = _font_envelope_widths(font, settings)
        except FontTextError:
            return FontFitRequirement(
                compatible_with_widget=True,
                complete_glyph_coverage=False,
                minimum_width=None,
                minimum_height=font.glyph_height,
            )
        return FontFitRequirement(
            compatible_with_widget=True,
            complete_glyph_coverage=True,
            minimum_width=(
                max(widths) if settings.overflow is TextOverflow.ERROR else 1
            ),
            minimum_height=font.glyph_height,
        )

    return analyze_font_fit(
        font_registry,
        settings.font,
        region_width=region_width,
        region_height=region_height,
        requirement_for=requirement,
    )


def _layout_system_metric_text(
    font: FontDefinition,
    settings: SystemMetricSettings,
    context: RenderContext,
    *,
    region_width: int,
    region_height: int,
) -> SystemMetricTextLayout:
    """Lay out one current value using one already-resolved concrete font."""
    snapshot = context.metrics_snapshot
    value = None if snapshot is None else snapshot.value(settings.metric)
    value_text = settings.unavailable_text if value is None else (
        format_temperature_value(settings, value)
        if _is_temperature(settings)
        else format_metric_value(settings.metric, value, settings.precision)
    )
    slots = _metric_slots(
        font, settings, value_text, available=value is not None
    )
    measured_width = _slots_width(font, slots)
    truncated = False
    if settings.overflow is TextOverflow.ERROR and measured_width > region_width:
        raise FontTextError(
            "content_too_wide", field="content", font_id=font.font_id
        )
    if settings.overflow is TextOverflow.TRUNCATE:
        retained: list[_GlyphSlot] = []
        retained_width = 0
        for slot in slots:
            candidate_width = (
                retained_width
                + slot.gap_before
                + slot.advance
            )
            if candidate_width > region_width:
                break
            retained.append(slot)
            retained_width = candidate_width
        truncated = len(retained) != len(slots)
        slots = tuple(retained)
        measured_width = retained_width
    origin_x = horizontal_origin(
        region_width, measured_width, settings.horizontal_alignment
    )
    origin_y = vertical_origin(
        region_height, font.glyph_height, settings.vertical_alignment
    )
    placements: list[SystemMetricGlyphPlacement] = []
    cursor_x = origin_x
    for slot in slots:
        cursor_x += slot.gap_before
        placements.append(SystemMetricGlyphPlacement(
            character=slot.character,
            origin_x=cursor_x + slot.glyph_offset,
            origin_y=origin_y,
            advance=slot.advance,
            visible=slot.visible,
            role=slot.role,
        ))
        cursor_x += slot.advance
    return SystemMetricTextLayout(
        font_id=font.font_id,
        glyph_height=font.glyph_height,
        measured_width=measured_width,
        origin_x=origin_x,
        origin_y=origin_y,
        placements=tuple(placements),
        truncated=truncated,
    )


def layout_system_metric_text(
    settings: SystemMetricSettings,
    context: RenderContext,
    *,
    region_width: int,
    region_height: int,
) -> SystemMetricTextLayout:
    """Lay out one current value with fixed numeric cells where applicable."""
    font = resolve_system_metric_font(
        settings,
        region_width=region_width,
        region_height=region_height,
    )
    return _layout_system_metric_text(
        font,
        settings,
        context,
        region_width=region_width,
        region_height=region_height,
    )


def render_system_metric(
    surface: RegionSurface,
    settings: SystemMetricSettings,
    context: RenderContext,
) -> None:
    """Render one intrinsic ASCII metric label and value."""
    font_failure: tuple[str, str] | None = None
    unexpected = False
    try:
        if (
            not isinstance(surface, RegionSurface)
            or type(settings) is not SystemMetricSettings
            or type(context) is not RenderContext
            or surface.width != context.region_width
            or surface.height != context.region_height
        ):
            raise WidgetRenderingError("renderer_invariant", field="context")
        font = resolve_system_metric_font(
            settings,
            region_width=surface.width,
            region_height=surface.height,
        )
        layout = _layout_system_metric_text(
            font,
            settings,
            context,
            region_width=surface.width,
            region_height=surface.height,
        )
        color = RgbColor.from_schema_color(settings.color)
        for placement in layout.placements:
            if placement.visible:
                draw_glyph(
                    surface,
                    font.glyphs[placement.character],
                    origin_x=placement.origin_x,
                    origin_y=placement.origin_y,
                    color=color,
                )
        return None
    except WidgetRenderingError:
        raise
    except FontTextError as error:
        font_failure = _font_failure(error)
    except Exception:
        unexpected = True
    if font_failure is not None:
        category, field = font_failure
        raise WidgetRenderingError(category, field=field)
    if unexpected:
        raise WidgetRenderingError("unexpected_renderer_failure", field="renderer")
    raise WidgetRenderingError("renderer_invariant", field="renderer")


def metric_bar_fill_length(value: float | None, settings: MetricBarSettings, extent: int) -> int:
    """Return nearest-integer fill pixels with halves rounded upward."""
    if value is None:
        return 0
    ratio = (value - settings.minimum) / (settings.maximum - settings.minimum)
    clamped = min(1.0, max(0.0, ratio))
    return min(extent, max(0, int(math.floor(clamped * extent + 0.5))))


def render_metric_bar(
    surface: RegionSurface,
    settings: MetricBarSettings,
    context: RenderContext,
) -> None:
    """Draw one opaque region-local bar without fractional pixels."""
    unexpected = False
    try:
        if (
            not isinstance(surface, RegionSurface)
            or type(settings) is not MetricBarSettings
            or type(context) is not RenderContext
            or surface.width != context.region_width
            or surface.height != context.region_height
        ):
            raise WidgetRenderingError("renderer_invariant", field="context")
        empty = RgbColor.from_schema_color(settings.empty_color)
        fill = RgbColor.from_schema_color(settings.fill_color)
        border = RgbColor.from_schema_color(settings.border_color)
        surface.fill(empty)
        inset = 1 if settings.border_enabled and surface.width > 2 and surface.height > 2 else 0
        if inset:
            surface.fill(border)
            surface.fill_rect(1, 1, surface.width - 2, surface.height - 2, empty)
        inner_width = surface.width - inset * 2
        inner_height = surface.height - inset * 2
        snapshot = context.metrics_snapshot
        value = None if snapshot is None else snapshot.value(settings.metric)
        extent = inner_width if settings.orientation is BarOrientation.HORIZONTAL else inner_height
        length = metric_bar_fill_length(value, settings, extent)
        if settings.orientation is BarOrientation.HORIZONTAL:
            x = inset if settings.direction is BarDirection.START_TO_END else inset + inner_width - length
            surface.fill_rect(x, inset, length, inner_height, fill)
        else:
            # Vertical start-to-end is bottom-to-top; end-to-start is top-to-bottom.
            y = inset + inner_height - length if settings.direction is BarDirection.START_TO_END else inset
            surface.fill_rect(inset, y, inner_width, length, fill)
        return None
    except WidgetRenderingError:
        raise
    except Exception:
        unexpected = True
    if unexpected:
        raise WidgetRenderingError("unexpected_renderer_failure", field="renderer")
    raise WidgetRenderingError("renderer_invariant", field="renderer")
