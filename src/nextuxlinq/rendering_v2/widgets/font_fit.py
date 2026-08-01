"""Widget dispatch for authoritative native-font fit analysis."""

from __future__ import annotations

from nextuxlinq.rendering_v2.fonts import (
    FontFitAnalysis,
    analyze_text_font_fit,
    create_builtin_font_registry,
    normalize_text,
)
from nextuxlinq.rendering_v2.widgets.clock import analyze_clock_font_fit
from nextuxlinq.rendering_v2.widgets.date import analyze_date_font_fit
from nextuxlinq.rendering_v2.widgets.system_metric import (
    analyze_system_metric_font_fit,
)
from nextuxlinq.widget_models import (
    ClockSettings,
    DateSettings,
    ScrollingTextSettings,
    StaticTextSettings,
    SystemMetricSettings,
    TextOverflow,
    WidgetSettings,
)


def analyze_widget_font_fit(
    settings: WidgetSettings,
    *,
    region_width: int,
    region_height: int,
) -> FontFitAnalysis | None:
    """Return fit metadata for font-bearing widgets and ``None`` otherwise."""
    registry = create_builtin_font_registry()
    if type(settings) is ClockSettings:
        return analyze_clock_font_fit(
            registry,
            settings,
            region_width=region_width,
            region_height=region_height,
        )
    if type(settings) is DateSettings:
        return analyze_date_font_fit(
            registry,
            settings,
            region_width=region_width,
            region_height=region_height,
        )
    if type(settings) is StaticTextSettings:
        return analyze_text_font_fit(
            registry,
            settings.font,
            normalize_text(settings.content, uppercase=settings.uppercase),
            region_width=region_width,
            region_height=region_height,
            require_complete_width=settings.overflow is TextOverflow.ERROR,
        )
    if type(settings) is ScrollingTextSettings:
        return analyze_text_font_fit(
            registry,
            settings.font,
            normalize_text(settings.content, uppercase=settings.uppercase),
            region_width=region_width,
            region_height=region_height,
            require_complete_width=False,
        )
    if type(settings) is SystemMetricSettings:
        return analyze_system_metric_font_fit(
            settings,
            region_width=region_width,
            region_height=region_height,
        )
    return None
