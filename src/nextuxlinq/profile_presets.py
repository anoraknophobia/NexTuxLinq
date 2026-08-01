"""Immutable built-in schema-v2 profile presets for Matrix900."""

from __future__ import annotations

from dataclasses import dataclass

from nextuxlinq.models_v2 import (
    CanvasConfig,
    DisplayEffect,
    ForegroundRainbowEffect,
    ProfileConfigV2,
    RainbowDirection,
    RegionConfig,
)
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.widget_models import FontChoice, WidgetConfig, WidgetType


@dataclass(frozen=True, slots=True)
class BuiltinPreset:
    id: str
    name: str
    description: str
    profile: ProfileConfigV2


def _settings(widget_type: WidgetType, **changes: object) -> WidgetConfig:
    registry = create_widget_registry()
    definition = registry.resolve(widget_type)
    raw = definition.default_settings.to_dict()
    raw.update(changes)
    return WidgetConfig.from_dict({"type": widget_type.value, "settings": raw})


def _region(
    region_id: str,
    x: int,
    y: int,
    width: int,
    height: int,
    widget: WidgetConfig,
    name: str,
) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=name,
        x=x,
        y=y,
        width=width,
        height=height,
        enabled=True,
        background_color=None,
        widget=widget,
    )


def _profile(
    *regions: RegionConfig,
    effect: DisplayEffect | None = None,
) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(width=90, height=10, background_color="#000000"),
        regions=regions,
        **({} if effect is None else {"effect": effect}),
    )


BUILTIN_PRESETS = (
    BuiltinPreset(
        "empty-layout",
        "Empty Layout",
        "A blank canvas ready for custom regions.",
        _profile(),
    ),
    BuiltinPreset(
        "dual-clock",
        "Dual Clock",
        "Rear clock on viewer left and front clock on viewer right.",
        _profile(
            _region("front-clock", 0, 0, 30, 10, _settings(WidgetType.CLOCK), "Front Clock"),
            _region("rear-clock", 60, 0, 30, 10, _settings(WidgetType.CLOCK), "Rear Clock"),
        ),
    ),
    BuiltinPreset(
        "clock-scroll-clock",
        "Clock + Scrolling Text + Clock",
        "Two clocks, scrolling text, and an unused center gap.",
        _profile(
            _region("front-clock", 0, 0, 24, 10, _settings(WidgetType.CLOCK), "Front Clock"),
            _region("scrolling-text", 28, 0, 34, 10, _settings(WidgetType.SCROLLING_TEXT, content="NEXTUXLINQ"), "Scrolling Text"),
            _region("rear-clock", 66, 0, 24, 10, _settings(WidgetType.CLOCK), "Rear Clock"),
        ),
    ),
    BuiltinPreset(
        "two-row-information",
        "Two-row Information",
        "Independent five-pixel clock and scrolling information rows.",
        _profile(
            _region("clock-row", 0, 0, 90, 5, _settings(WidgetType.CLOCK, font=FontChoice.COMPACT_3X5.value), "Clock Row"),
            _region("information-row", 0, 5, 90, 5, _settings(WidgetType.SCROLLING_TEXT, content="NEXTUXLINQ", font=FontChoice.COMPACT_3X5.value), "Information Row"),
        ),
    ),
    BuiltinPreset(
        "date-static-text",
        "Date + Static Text",
        "Centered German date and static text in separate rows.",
        _profile(
            _region("date-row", 0, 0, 90, 5, _settings(WidgetType.DATE, font=FontChoice.COMPACT_3X5.value), "Date Row"),
            _region("text-row", 0, 5, 90, 5, _settings(WidgetType.STATIC_TEXT, content="NEXTUXLINQ", font=FontChoice.COMPACT_3X5.value), "Text Row"),
        ),
    ),
    BuiltinPreset(
        "full-width-scrolling-text",
        "Full-width Scrolling Text",
        "One scrolling-text region across the complete display.",
        _profile(
            _region("scrolling-text", 0, 0, 90, 10, _settings(WidgetType.SCROLLING_TEXT, content="NEXTUXLINQ"), "Scrolling Text"),
        ),
    ),
    BuiltinPreset(
        "system-monitor",
        "System Monitor",
        "CPU, memory, and CPU temperature in a compact two-row layout.",
        _profile(
            _region(
                "cpu-usage", 0, 0, 45, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="cpu_usage",
                    label="CPU",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "CPU Usage",
            ),
            _region(
                "memory-usage", 45, 0, 45, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="memory_usage",
                    label="RAM",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "Memory Usage",
            ),
            _region(
                "cpu-temperature", 0, 5, 90, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="cpu_temperature",
                    label="CPU",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "CPU Temperature",
            ),
        ),
    ),
    BuiltinPreset(
        "system-bars",
        "System Bars",
        "Separate CPU, memory, and CPU-temperature metric bars.",
        _profile(
            _region(
                "cpu-bar", 0, 0, 90, 3,
                _settings(WidgetType.METRIC_BAR, metric="cpu_usage"),
                "CPU Bar",
            ),
            _region(
                "memory-bar", 0, 3, 90, 3,
                _settings(WidgetType.METRIC_BAR, metric="memory_usage"),
                "Memory Bar",
            ),
            _region(
                "temperature-bar", 0, 6, 90, 4,
                _settings(WidgetType.METRIC_BAR, metric="cpu_temperature"),
                "CPU Temperature Bar",
            ),
        ),
    ),
    BuiltinPreset(
        "extended-system-monitor",
        "Extended System Monitor",
        "CPU, memory, GPU temperature, and network receive rate.",
        _profile(
            _region(
                "extended-cpu", 0, 0, 45, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="cpu_usage",
                    label="CPU",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "CPU Usage",
            ),
            _region(
                "extended-memory", 45, 0, 45, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="memory_usage",
                    label="RAM",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "Memory Usage",
            ),
            _region(
                "extended-gpu-temperature", 0, 5, 35, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="gpu_temperature",
                    label="GPU",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "GPU Temperature",
            ),
            _region(
                "extended-network-receive", 35, 5, 55, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="network_receive_rate",
                    label="RX",
                    font=FontChoice.COMPACT_3X5.value,
                    precision=1,
                ),
                "Network Receive Rate",
            ),
        ),
    ),
    BuiltinPreset(
        "temperature-monitor",
        "Temperature Monitor",
        "CPU, GPU, and storage temperatures with safe unavailable text.",
        _profile(
            _region(
                "temperature-cpu", 0, 0, 45, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="cpu_temperature",
                    label="CPU",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "CPU Temperature",
            ),
            _region(
                "temperature-gpu", 45, 0, 45, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="gpu_temperature",
                    label="GPU",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "GPU Temperature",
            ),
            _region(
                "temperature-storage", 0, 5, 90, 5,
                _settings(
                    WidgetType.SYSTEM_METRIC,
                    metric="storage_temperature",
                    label="DISK",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "Storage Temperature",
            ),
        ),
    ),
    BuiltinPreset(
        "rainbow-showcase",
        "Rainbow Showcase",
        "A continuous Rear-to-Front rainbow across three foreground regions.",
        _profile(
            _region(
                "front-rainbow-clock", 0, 0, 24, 10,
                _settings(WidgetType.CLOCK), "Front Clock",
            ),
            _region(
                "rainbow-message", 28, 0, 34, 10,
                _settings(
                    WidgetType.SCROLLING_TEXT,
                    content="NEXTUXLINQ",
                    font=FontChoice.COMPACT_3X5.value,
                ),
                "Rainbow Message",
            ),
            _region(
                "rear-rainbow-clock", 66, 0, 24, 10,
                _settings(WidgetType.CLOCK), "Rear Clock",
            ),
            effect=ForegroundRainbowEffect(
                direction=RainbowDirection.REAR_TO_FRONT,
                speed=0.10,
            ),
        ),
    ),
)


def get_builtin_presets() -> tuple[BuiltinPreset, ...]:
    """Return the immutable preset library."""
    return BUILTIN_PRESETS
