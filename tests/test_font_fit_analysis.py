"""Authoritative native-font fit analysis and safe guidance metadata."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from nextuxlinq.rendering_v2.fonts import AUTO_FONT_PRIORITY
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.rendering_v2.widgets.font_fit import analyze_widget_font_fit
from nextuxlinq.service import font_fit_payload
from nextuxlinq.system_metrics import MetricId
from nextuxlinq.widget_models import (
    DateFormat,
    FontChoice,
    SystemMetricSettings,
    TextOverflow,
    WidgetType,
)


def default_settings(widget_type: WidgetType):
    return create_widget_registry().resolve(widget_type).default_settings


def analyze(settings, width: int, height: int):
    result = analyze_widget_font_fit(
        settings, region_width=width, region_height=height
    )
    assert result is not None
    return result


def test_clock_auto_uses_canonical_largest_fitting_order_and_exact_geometry() -> None:
    settings = default_settings(WidgetType.CLOCK)
    exact = analyze(settings, 25, 10)
    assert exact.resolved_auto_font is FontChoice.NATIVE_5X10
    assert tuple(item.font_id for item in exact.candidates) == AUTO_FONT_PRIORITY
    assert exact.candidate(FontChoice.NATIVE_5X10).minimum_width == 25
    assert exact.candidate(FontChoice.NATIVE_5X10).minimum_height == 10

    narrower = analyze(settings, 24, 10)
    assert narrower.resolved_auto_font is FontChoice.NATIVE_4X9
    assert narrower.candidate(FontChoice.NATIVE_5X10).rejection_category == "too_wide"
    assert narrower.candidate(FontChoice.NATIVE_5X9).rejection_category == "too_wide"
    assert narrower.candidate(FontChoice.NATIVE_4X9).overall_fit is True

    five_high = analyze(settings, 17, 5)
    assert five_high.resolved_auto_font is FontChoice.COMPACT_3X5
    assert five_high.candidate(FontChoice.LEGACY_3X7).rejection_category == "too_tall"


def test_explicit_font_never_falls_back_and_reports_a_smaller_alternative() -> None:
    settings = replace(
        default_settings(WidgetType.CLOCK),
        font=FontChoice.NATIVE_5X10,
    )
    result = analyze(settings, 24, 10)
    assert result.selected_explicit_font is FontChoice.NATIVE_5X10
    assert result.resolved_auto_font is None
    assert result.selected_candidate is not None
    assert result.selected_candidate.overall_fit is False
    assert result.selected_candidate.minimum_width == 25
    assert result.best_smaller_alternative is FontChoice.NATIVE_4X9


def test_no_fitting_font_and_unsupported_content_are_distinct_and_safe() -> None:
    no_fit = analyze(default_settings(WidgetType.CLOCK), 16, 10)
    assert no_fit.no_font_fits is True
    assert no_fit.no_font_supports_content is False
    smallest = no_fit.candidate(FontChoice.COMPACT_3X5)
    assert (smallest.minimum_width, smallest.minimum_height) == (17, 5)

    secret = "PRIVATE Ä CONTENT"
    unsupported = analyze(
        replace(
            default_settings(WidgetType.STATIC_TEXT),
            content=secret,
            overflow=TextOverflow.ERROR,
        ),
        90,
        10,
    )
    assert unsupported.no_font_fits is True
    assert unsupported.no_font_supports_content is True
    assert all(item.minimum_width is None for item in unsupported.candidates)
    serialized = json.dumps(font_fit_payload(unsupported))
    assert secret not in serialized
    assert "PRIVATE" not in serialized


@pytest.mark.parametrize("overflow", [TextOverflow.CLIP, TextOverflow.TRUNCATE])
def test_static_non_error_overflow_does_not_shrink_for_width(
    overflow: TextOverflow,
) -> None:
    settings = replace(
        default_settings(WidgetType.STATIC_TEXT),
        content="A VERY LONG STATIC VALUE",
        overflow=overflow,
    )
    result = analyze(settings, 1, 10)
    assert result.resolved_auto_font is FontChoice.NATIVE_5X10
    assert result.candidate(FontChoice.NATIVE_5X10).minimum_width == 1


def test_scrolling_text_width_never_restricts_font_selection() -> None:
    settings = replace(
        default_settings(WidgetType.SCROLLING_TEXT),
        content="A VERY LONG SCROLLING VALUE",
    )
    result = analyze(settings, 1, 10)
    assert result.resolved_auto_font is FontChoice.NATIVE_5X10
    assert all(
        item.fits_width
        for item in result.candidates
        if item.complete_glyph_coverage
    )


@pytest.mark.parametrize("date_format", tuple(DateFormat))
@pytest.mark.parametrize("overflow", tuple(TextOverflow))
def test_every_date_format_uses_stable_german_envelope_and_overflow(
    date_format: DateFormat,
    overflow: TextOverflow,
) -> None:
    settings = replace(
        default_settings(WidgetType.DATE),
        format=date_format,
        overflow=overflow,
    )
    result = analyze(settings, 90 if overflow is TextOverflow.ERROR else 1, 10)
    assert result.resolved_auto_font is FontChoice.NATIVE_5X10
    assert result.candidate(FontChoice.LEGACY_3X7).complete_glyph_coverage is False


@pytest.mark.parametrize(
    ("metric", "label"),
    [
        (MetricId.CPU_USAGE, "CPU"),
        (MetricId.MEMORY_USAGE, "RAM"),
        (MetricId.GPU_USAGE, "GPU"),
        (MetricId.GPU_MEMORY_USAGE, "VRAM"),
        (MetricId.STORAGE_USAGE, "DISK"),
        (MetricId.CPU_TEMPERATURE, "TEMP"),
        (MetricId.GPU_TEMPERATURE, "TEMP"),
        (MetricId.STORAGE_TEMPERATURE, "TEMP"),
    ],
)
def test_metric_analysis_is_snapshot_independent_and_tries_smaller_fonts(
    metric: MetricId,
    label: str,
) -> None:
    settings = SystemMetricSettings(metric=metric, label=label)
    wide = analyze(settings, 90, 10)
    repeated = analyze(settings, 90, 10)
    assert wide == repeated
    assert wide.resolved_auto_font is FontChoice.NATIVE_5X10

    minimum = wide.candidate(FontChoice.NATIVE_5X10).minimum_width
    assert minimum is not None
    if minimum > 1:
        narrow = analyze(settings, minimum - 1, 10)
        assert narrow.resolved_auto_font is not FontChoice.NATIVE_5X10
        if narrow.resolved_auto_font is not None:
            assert narrow.candidate(narrow.resolved_auto_font).overall_fit is True


def test_font_fit_response_is_deeply_immutable_at_the_domain_boundary() -> None:
    result = analyze(default_settings(WidgetType.CLOCK), 25, 10)
    with pytest.raises(FrozenInstanceError):
        result.actual_width = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.candidates[0].overall_fit = False  # type: ignore[misc]


def test_non_text_widgets_have_no_font_analysis_or_recommendations() -> None:
    for widget_type in (WidgetType.BLANK, WidgetType.METRIC_BAR):
        assert analyze_widget_font_fit(
            default_settings(widget_type), region_width=10, region_height=5
        ) is None
