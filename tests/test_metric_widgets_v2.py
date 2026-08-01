"""Focused tests for schema-v2 system metric widgets."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nextuxlinq.models_v2 import ProfileConfigV2
from nextuxlinq.rendering_v2.compositor import Compositor
from nextuxlinq.rendering_v2.registry import RenderContext, create_widget_registry
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.rendering_v2.widgets.errors import WidgetRenderingError
from nextuxlinq.rendering_v2.widgets.system_metric import (
    analyze_system_metric_font_fit,
    format_temperature_value,
    metric_bar_fill_length,
    metric_text,
    metric_text_format_envelope,
    layout_system_metric_text,
    render_metric_bar,
    render_system_metric,
    resolve_system_metric_font,
)
from nextuxlinq.rendering_v2.update_policy import UpdatePolicy
from nextuxlinq.system_metrics import (
    MetricAvailability,
    MetricId,
    SystemMetricsSnapshot,
)
from nextuxlinq.widget_models import MetricBarSettings, SystemMetricSettings, WidgetType

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def snapshot(**values: float) -> SystemMetricsSnapshot:
    converted = {MetricId(key): value for key, value in values.items()}
    return SystemMetricsSnapshot(
        5.0,
        NOW,
        converted,
        {
            metric: (
                MetricAvailability.AVAILABLE
                if metric in converted
                else MetricAvailability.UNAVAILABLE
            )
            for metric in MetricId
        },
        "partial",
    )


def context(width: int, height: int, metrics=None) -> RenderContext:
    return RenderContext(
        wall_clock=NOW,
        animation_time=5.0,
        canvas_width=90,
        canvas_height=10,
        region_id="metric",
        region_width=width,
        region_height=height,
        metrics_snapshot=metrics,
    )


@pytest.mark.parametrize(
    ("metric", "legacy_label", "short_label"),
    [
        ("cpu_temperature", "CPU TEMP", "CPU"),
        ("gpu_temperature", "GPU TEMP", "GPU"),
        ("storage_temperature", "DISK TEMP", "DISK"),
    ],
)
def test_temperature_legacy_default_labels_resolve_without_rewriting_custom_labels(
    metric: str,
    legacy_label: str,
    short_label: str,
) -> None:
    legacy = SystemMetricSettings(
        metric=metric, label=legacy_label
    )  # type: ignore[arg-type]
    assert legacy.label == short_label
    custom = SystemMetricSettings(
        metric=metric, label=f"{legacy_label} CUSTOM"
    )  # type: ignore[arg-type]
    assert custom.label == f"{legacy_label} CUSTOM"


def test_exact_long_label_for_a_different_metric_remains_custom() -> None:
    settings = SystemMetricSettings(
        metric="gpu_temperature", label="CPU TEMP"
    )  # type: ignore[arg-type]
    assert settings.label == "CPU TEMP"


def test_old_system_metric_settings_default_to_celsius() -> None:
    serialized = SystemMetricSettings().to_dict()
    serialized.pop("temperature_unit")
    restored = SystemMetricSettings.from_dict(serialized)
    assert restored.temperature_unit.value == "celsius"
    assert restored.to_dict()["temperature_unit"] == "celsius"


@pytest.mark.parametrize(
    ("metric", "value", "expected"),
    [
        ("cpu_usage", 42.0, "CPU 42%"),
        ("memory_usage", 63.0, "RAM 63%"),
        ("cpu_temperature", 54.0, "CPU54°C"),
        ("uptime", 221.0, "UPTIME 03:41"),
    ],
)
def test_system_metric_formats_every_metric(metric: str, value: float, expected: str) -> None:
    labels = {
        "cpu_usage": "CPU",
        "memory_usage": "RAM",
        "cpu_temperature": "CPU",
        "uptime": "UPTIME",
    }
    settings = SystemMetricSettings(metric=metric, label=labels[metric])  # type: ignore[arg-type]
    assert metric_text(settings, context(90, 10, snapshot(**{metric: value}))) == expected
    value_only = SystemMetricSettings(
        **{**settings.to_dict(), "display_mode": "value_only", "precision": 1}
    )
    rendered = metric_text(value_only, context(90, 10, snapshot(**{metric: value})))
    assert rendered == ("03:41" if metric == "uptime" else {
        "cpu_usage": "42.0%",
        "memory_usage": "63.0%",
        "cpu_temperature": "54.0°C",
    }[metric])


@pytest.mark.parametrize(
    ("metric", "label", "value", "precision", "expected"),
    [
        ("load_average_1m", "LOAD", 1.24, 2, "LOAD 1.24"),
        ("storage_usage", "DISK", 75.0, 0, "DISK 75%"),
        ("network_receive_rate", "NET RX", 1536.0, 1, "NET RX 1.5KB/S"),
        ("network_transmit_rate", "NET TX", 2 * 1024 ** 2, 1, "NET TX 2.0MB/S"),
        ("gpu_usage", "GPU", 61.0, 0, "GPU 61%"),
        ("gpu_temperature", "GPU", 54.0, 0, "GPU54°C"),
        ("gpu_memory_usage", "VRAM", 40.0, 0, "VRAM 40%"),
        ("storage_temperature", "DISK", 38.0, 0, "DISK38°C"),
    ],
)
def test_system_metric_formats_every_extended_metric(
    metric: str,
    label: str,
    value: float,
    precision: int,
    expected: str,
) -> None:
    settings = SystemMetricSettings(
        metric=metric, label=label, precision=precision  # type: ignore[arg-type]
    )
    assert metric_text(
        settings,
        context(90, 10, snapshot(**{metric: value})),
    ) == expected


def test_network_metric_units_scale_deterministically() -> None:
    settings = SystemMetricSettings(
        metric="network_receive_rate",  # type: ignore[arg-type]
        display_mode="value_only",  # type: ignore[arg-type]
        precision=1,
    )
    for value, expected in (
        (512.0, "0.5KB/S"),
        (1024.0 ** 2, "1.0MB/S"),
        (3 * 1024.0 ** 3, "3.0GB/S"),
    ):
        assert metric_text(
            settings,
            context(90, 10, snapshot(network_receive_rate=value)),
        ) == expected


def test_system_metric_unavailable_custom_label_fonts_and_background() -> None:
    settings = SystemMetricSettings(
        label="LOAD", unavailable_text="NA", font="3x5",  # type: ignore[arg-type]
        horizontal_alignment="start", vertical_alignment="top",  # type: ignore[arg-type]
        overflow="clip",  # type: ignore[arg-type]
    )
    surface = RegionSurface(30, 5, RgbColor(7, 8, 9))
    render_system_metric(surface, settings, context(30, 5, snapshot()))
    payload = surface.to_bytes()
    assert bytes.fromhex("FFFFFF") in payload
    assert bytes((7, 8, 9)) in payload
    assert metric_text(settings, context(30, 5, snapshot())) == "LOAD NA"


def test_system_metric_rejects_unsupported_glyph_without_leaking_details() -> None:
    settings = SystemMetricSettings(label="CPU☃")
    surface = RegionSurface(90, 10)
    with pytest.raises(WidgetRenderingError) as raised:
        render_system_metric(
            surface,
            settings,
            context(90, 10, snapshot(cpu_usage=42)),
        )
    assert raised.value.category == "unsupported_glyph"
    assert "☃" not in str(raised.value)
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("font,height", [
    ("auto", 10), ("3x5", 5), ("3x7", 7), ("5x7", 7),
    ("4x9", 9), ("5x9", 9), ("5x10", 10),
])
def test_system_metric_supports_automatic_and_explicit_fonts(font: str, height: int) -> None:
    surface = RegionSurface(40, height, RgbColor(1, 2, 3))
    render_system_metric(
        surface,
        SystemMetricSettings(
            metric="cpu_usage", display_mode="value_only", font=font,  # type: ignore[arg-type]
        ),
        context(40, height, snapshot(cpu_usage=9)),
    )
    assert bytes.fromhex("FFFFFF") in surface.to_bytes()
    if font == "auto":
        assert resolve_system_metric_font(
            SystemMetricSettings(
                metric="cpu_usage",
                display_mode="value_only",  # type: ignore[arg-type]
            ),
            region_width=40,
            region_height=10,
        ).font_id.value == "5x10"


@pytest.mark.parametrize(
    ("metric", "label"),
    [
        ("cpu_usage", "CPU"),
        ("memory_usage", "RAM"),
        ("gpu_usage", "GPU"),
        ("gpu_memory_usage", "VRAM"),
        ("storage_usage", "DISK"),
    ],
)
def test_percentage_auto_font_is_stable_for_one_two_and_three_digits(
    metric: str,
    label: str,
) -> None:
    settings = SystemMetricSettings(metric=metric, label=label)  # type: ignore[arg-type]
    layouts = [
        layout_system_metric_text(
            settings,
            context(90, 10, snapshot(**{metric: value})),
            region_width=90,
            region_height=10,
        )
        for value in (5.0, 14.0, 99.0, 100.0)
    ]
    assert [layout.font_id.value for layout in layouts] == ["5x10"] * 4
    assert [layout.glyph_height for layout in layouts] == [10] * 4
    assert metric_text(
        settings, context(90, 10, snapshot(**{metric: 5.0}))
    ) == f"{label} 5%"

    label_origins = [
        next(
            placement.origin_x for placement in layout.placements
            if placement.role == "label" and placement.character == label[0]
        )
        for layout in layouts[:3]
    ]
    percent_origins = [
        next(
            placement.origin_x for placement in layout.placements
            if placement.role == "suffix" and placement.character == "%"
        )
        for layout in layouts[:3]
    ]
    assert len(set(label_origins)) == 1
    assert len(set(percent_origins)) == 1
    assert len({layout.origin_x for layout in layouts[:3]}) == 1
    assert len({layout.measured_width for layout in layouts[:3]}) == 1
    hidden = next(
        placement for placement in layouts[0].placements
        if placement.role == "digit" and not placement.visible
    )
    first_visible_14 = next(
        placement for placement in layouts[1].placements
        if placement.role == "digit"
    )
    assert hidden.character == "0"
    assert hidden.advance == first_visible_14.advance
    assert hidden.origin_x == first_visible_14.origin_x
    zero_layout = layout_system_metric_text(
        settings,
        context(90, 10, snapshot(**{metric: 0.0})),
        region_width=90,
        region_height=10,
    )
    zero_slots = [
        placement for placement in zero_layout.placements
        if placement.role == "digit"
    ]
    assert [placement.visible for placement in zero_slots] == [False, True]
    assert zero_slots[0].advance == zero_slots[1].advance

    background = RgbColor(7, 8, 9)
    surface = RegionSurface(90, 10, background)
    render_system_metric(
        surface,
        settings,
        context(90, 10, snapshot(**{metric: 5.0})),
    )
    payload = surface.to_bytes()
    effect_mask = surface.foreground_mask()
    assert all(
        payload[((y * 90 + x) * 3):][:3] == background.to_bytes()
        for y in range(
            hidden.origin_y,
            hidden.origin_y + layouts[0].glyph_height,
        )
        for x in range(hidden.origin_x, hidden.origin_x + hidden.advance)
    )
    assert all(
        effect_mask[y * 90 + x] == 0
        for y in range(
            hidden.origin_y,
            hidden.origin_y + layouts[0].glyph_height,
        )
        for x in range(hidden.origin_x, hidden.origin_x + hidden.advance)
    )


@pytest.mark.parametrize(
    ("metric", "label", "values"),
    [
        ("cpu_temperature", "CPU", (9.0, 54.0, 150.0)),
        ("gpu_temperature", "GPU", (5.0, 64.0, 150.0)),
        ("storage_temperature", "DISK", (5.0, 38.0, 100.0)),
    ],
)
def test_temperature_auto_font_is_snapshot_independent(
    metric: str,
    label: str,
    values: tuple[float, ...],
) -> None:
    settings = SystemMetricSettings(
        metric=metric, label=label, precision=0  # type: ignore[arg-type]
    )
    layouts = [
        layout_system_metric_text(
            settings,
            context(90, 10, snapshot(**{metric: value})),
            region_width=90,
            region_height=10,
        )
        for value in values
    ]
    assert {layout.font_id.value for layout in layouts} == {"5x10"}
    assert {layout.glyph_height for layout in layouts} == {10}
    if values[0] >= 0 and values[0] < 10:
        assert any(
            placement.role == "digit" and not placement.visible
            for placement in layouts[0].placements
        )
        assert layouts[0].origin_x == layouts[1].origin_x
        assert layouts[0].measured_width == layouts[1].measured_width
        assert next(
            placement.origin_x for placement in layouts[0].placements
            if placement.role == "suffix"
        ) == next(
            placement.origin_x for placement in layouts[1].placements
            if placement.role == "suffix"
        )


@pytest.mark.parametrize(
    ("unit", "value", "precision", "expected"),
    [
        ("degree_only", 43.0, 0, "43°"),
        ("celsius", 43.0, 0, "43°C"),
        ("celsius", 43.25, 1, "43.2°C"),
        ("fahrenheit", 43.0, 0, "109°F"),
        ("fahrenheit", 30.25, 1, "86.5°F"),
    ],
)
def test_temperature_units_format_from_canonical_celsius(
    unit: str,
    value: float,
    precision: int,
    expected: str,
) -> None:
    settings = SystemMetricSettings(
        metric="cpu_temperature",
        label="CPU",
        temperature_unit=unit,
        precision=precision,
    )  # type: ignore[arg-type]
    assert format_temperature_value(settings, value) == expected
    assert metric_text(
        settings,
        context(90, 10, snapshot(cpu_temperature=value)),
    ) == f"CPU{expected}"


def test_compact_celsius_cpu_temperature_fits_25_by_5_exactly() -> None:
    settings = SystemMetricSettings(
        metric="cpu_temperature", label="CPU", temperature_unit="celsius"
    )  # type: ignore[arg-type]
    analysis = analyze_system_metric_font_fit(
        settings, region_width=25, region_height=5
    )
    assert analysis.resolved_auto_font.value == "3x5"
    candidate = next(
        item for item in analysis.candidates if item.font_id.value == "3x5"
    )
    assert candidate.minimum_width == 25
    layout = layout_system_metric_text(
        settings,
        context(25, 5, snapshot(cpu_temperature=43)),
        region_width=25,
        region_height=5,
    )
    assert layout.font_id.value == "3x5"
    assert layout.measured_width == 25
    assert all(placement.character != " " for placement in layout.placements)
    degree = next(
        placement for placement in layout.placements
        if placement.role == "degree"
    )
    unit = next(
        placement for placement in layout.placements
        if placement.role == "suffix"
    )
    assert unit.origin_x == degree.origin_x + 3


def test_temperature_unit_changes_exact_minimum_width_guidance() -> None:
    expected = {"degree_only": 22, "celsius": 25, "fahrenheit": 29}
    for unit, width in expected.items():
        settings = SystemMetricSettings(
            metric="cpu_temperature", label="CPU", temperature_unit=unit
        )  # type: ignore[arg-type]
        analysis = analyze_system_metric_font_fit(
            settings, region_width=25, region_height=5
        )
        candidate = next(
            item for item in analysis.candidates
            if item.font_id.value == "3x5"
        )
        assert candidate.minimum_width == width
        assert candidate.overall_fit is (unit != "fahrenheit")


def test_explicit_temperature_font_keeps_selection_and_suggests_smaller_fit() -> None:
    settings = SystemMetricSettings(
        metric="cpu_temperature",
        label="CPU",
        temperature_unit="celsius",
        font="5x7",
    )  # type: ignore[arg-type]
    analysis = analyze_system_metric_font_fit(
        settings, region_width=36, region_height=7
    )
    selected = next(
        item for item in analysis.candidates if item.font_id.value == "5x7"
    )
    assert analysis.selected_explicit_font.value == "5x7"
    assert analysis.resolved_auto_font is None
    assert selected.minimum_width == 37
    assert selected.rejection_category == "too_wide"
    assert analysis.best_smaller_alternative.value == "3x5"
    with pytest.raises(WidgetRenderingError) as raised:
        render_system_metric(
            RegionSurface(36, 7),
            settings,
            context(36, 7, snapshot(cpu_temperature=43)),
        )
    assert raised.value.category == "content_too_wide"


def test_fahrenheit_digit_cells_and_anchors_are_stable() -> None:
    settings = SystemMetricSettings(
        metric="cpu_temperature",
        label="CPU",
        temperature_unit="fahrenheit",
        font="3x5",
    )  # type: ignore[arg-type]
    layouts = [
        layout_system_metric_text(
            settings,
            context(40, 5, snapshot(cpu_temperature=value)),
            region_width=40,
            region_height=5,
        )
        for value in (30.0, 42.8)
    ]
    assert [
        format_temperature_value(settings, value)
        for value in (30.0, 42.8)
    ] == ["86°F", "109°F"]
    assert {layout.font_id.value for layout in layouts} == {"3x5"}
    assert {layout.measured_width for layout in layouts} == {29}
    first_digits = [p for p in layouts[0].placements if p.role == "digit"]
    second_digits = [p for p in layouts[1].placements if p.role == "digit"]
    assert [p.visible for p in first_digits] == [False, True, True]
    assert [p.visible for p in second_digits] == [True, True, True]
    assert [p.origin_x for p in first_digits] == [p.origin_x for p in second_digits]
    degree_origins = [
        next(
            placement.origin_x for placement in layout.placements
            if placement.role == "degree"
        )
        for layout in layouts
    ]
    assert degree_origins[0] == degree_origins[1]


def test_temperature_unavailable_and_available_keep_the_same_auto_font() -> None:
    settings = SystemMetricSettings(
        metric="cpu_temperature", label="CPU", temperature_unit="celsius",
        overflow="clip",
    )  # type: ignore[arg-type]
    layouts = [
        layout_system_metric_text(
            settings,
            context(25, 5, current_snapshot),
            region_width=25,
            region_height=5,
        )
        for current_snapshot in (
            snapshot(),
            snapshot(cpu_temperature=5),
            snapshot(cpu_temperature=99),
            snapshot(cpu_temperature=100),
        )
    ]
    assert {layout.font_id.value for layout in layouts} == {"3x5"}
    assert metric_text(settings, context(25, 5, snapshot())) == "CPU--"


def test_temperature_hidden_digit_cell_draws_no_pixels() -> None:
    settings = SystemMetricSettings(
        metric="cpu_temperature",
        label="CPU",
        temperature_unit="celsius",
        font="3x5",
        horizontal_alignment="start",
        vertical_alignment="top",
    )  # type: ignore[arg-type]
    layout = layout_system_metric_text(
        settings,
        context(25, 5, snapshot(cpu_temperature=5)),
        region_width=25,
        region_height=5,
    )
    hidden = next(
        placement for placement in layout.placements
        if placement.role == "digit" and not placement.visible
    )
    background = RgbColor(7, 8, 9)
    surface = RegionSurface(25, 5, background)
    render_system_metric(
        surface, settings, context(25, 5, snapshot(cpu_temperature=5))
    )
    payload = surface.to_bytes()
    effect_mask = surface.foreground_mask()
    assert all(
        payload[((y * 25 + x) * 3):][:3] == background.to_bytes()
        for y in range(hidden.origin_y, hidden.origin_y + layout.glyph_height)
        for x in range(hidden.origin_x, hidden.origin_x + hidden.advance)
    )
    assert all(
        effect_mask[y * 25 + x] == 0
        for y in range(hidden.origin_y, hidden.origin_y + layout.glyph_height)
        for x in range(hidden.origin_x, hidden.origin_x + hidden.advance)
    )


def test_unavailable_and_available_snapshots_use_the_same_auto_font(
) -> None:
    settings = SystemMetricSettings(
        metric="gpu_usage", label="GPU", unavailable_text="NO DATA",  # type: ignore[arg-type]
    )
    assert metric_text(settings, context(55, 10, snapshot())) == "GPU NO DATA"
    unavailable = layout_system_metric_text(
        settings,
        context(90, 10, snapshot()),
        region_width=90,
        region_height=10,
    )
    assert metric_text(
        settings, context(55, 10, snapshot(gpu_usage=19))
    ) == "GPU 19%"
    available = layout_system_metric_text(
        settings,
        context(90, 10, snapshot(gpu_usage=19)),
        region_width=90,
        region_height=10,
    )
    assert unavailable.font_id is available.font_id
    assert unavailable.glyph_height == available.glyph_height == 10


@pytest.mark.parametrize("font", ["3x5", "5x7", "4x9", "5x9", "5x10"])
def test_explicit_system_metric_font_never_changes(
    font: str,
) -> None:
    settings = SystemMetricSettings(
        metric="memory_usage", label="RAM", font=font,  # type: ignore[arg-type]
        overflow="clip",  # type: ignore[arg-type]
    )
    layouts = [
        layout_system_metric_text(
            settings,
            context(20, 10, snapshot(memory_usage=value)),
            region_width=20,
            region_height=10,
        )
        for value in (9.0, 19.0, 100.0)
    ]
    assert {layout.font_id.value for layout in layouts} == {font}


def test_auto_font_and_alignment_are_stable_across_repeated_snapshots() -> None:
    settings = SystemMetricSettings(
        metric="cpu_usage", label="CPU", horizontal_alignment="center",  # type: ignore[arg-type]
    )
    font = resolve_system_metric_font(settings, region_width=90, region_height=10)
    surfaces = []
    for value in (9.0, 19.0, 9.0):
        surface = RegionSurface(90, 10)
        render_system_metric(
            surface, settings, context(90, 10, snapshot(cpu_usage=value))
        )
        surfaces.append(surface.to_bytes())
        assert resolve_system_metric_font(
            settings, region_width=90, region_height=10
        ).font_id is font.font_id
    assert surfaces[0] == surfaces[2]
    assert surfaces[0] != surfaces[1]


def test_normal_metric_width_ignores_hypothetical_100_but_actual_100_obeys_overflow() -> None:
    settings = SystemMetricSettings(metric="cpu_usage", label="CPU")  # type: ignore[arg-type]
    assert metric_text_format_envelope(settings) == ("CPU 99%", "CPU --")
    for value in (5.0, 14.0, 99.0):
        layout = layout_system_metric_text(
            settings,
            context(36, 10, snapshot(cpu_usage=value)),
            region_width=36,
            region_height=10,
        )
        assert layout.font_id.value == "5x10"
        assert layout.measured_width == 35
    with pytest.raises(WidgetRenderingError) as raised:
        render_system_metric(
            RegionSurface(36, 10),
            settings,
            context(36, 10, snapshot(cpu_usage=100)),
        )
    assert raised.value.category == "content_too_wide"
    for overflow in ("clip", "truncate"):
        overflow_settings = SystemMetricSettings(
            **{**settings.to_dict(), "overflow": overflow}
        )
        layout = layout_system_metric_text(
            overflow_settings,
            context(36, 10, snapshot(cpu_usage=100)),
            region_width=36,
            region_height=10,
        )
        assert layout.font_id.value == "5x10"
        render_system_metric(
            RegionSurface(36, 10),
            overflow_settings,
            context(36, 10, snapshot(cpu_usage=100)),
        )


def test_error_mode_still_rejects_a_genuinely_too_narrow_two_digit_layout() -> None:
    settings = SystemMetricSettings(
        metric="cpu_usage", label="CPU"  # type: ignore[arg-type]
    )
    with pytest.raises(WidgetRenderingError) as raised:
        render_system_metric(
            RegionSurface(22, 10),
            settings,
            context(22, 10, snapshot(cpu_usage=5)),
        )
    assert raised.value.category == "content_too_wide"


def test_value_only_envelope_excludes_label_and_includes_unavailable_text() -> None:
    settings = SystemMetricSettings(
        metric="cpu_usage", display_mode="value_only",  # type: ignore[arg-type]
        precision=2, unavailable_text="UNAVAILABLE",
    )
    assert metric_text_format_envelope(settings) == ("99.00%", "UNAVAILABLE")


def test_diagnosed_saved_profile_geometry_dry_renders_without_mutation() -> None:
    clock_settings = {
        "format": "24h", "show_seconds": False, "blink_separator": False,
        "time_zone": "local", "color": "#FFFFFF",
        "horizontal_alignment": "center", "vertical_alignment": "center",
        "font": "auto",
    }
    metric_settings = SystemMetricSettings().to_dict()
    profile = ProfileConfigV2.from_dict({
        "brightness": None,
        "canvas": {"width": 90, "height": 10, "background_color": "#000000"},
        "regions": [
            {
                "id": "legacy-front-clock", "name": "Front Clock",
                "x": 0, "y": 0, "width": 24, "height": 10,
                "enabled": True, "background_color": None,
                "widget": {"type": "clock", "settings": clock_settings},
            },
            {
                "id": "legacy-scrolling-text", "name": "System Metric",
                "x": 28, "y": 0, "width": 36, "height": 10,
                "enabled": True, "background_color": None,
                "widget": {"type": "system_metric", "settings": metric_settings},
            },
            {
                "id": "legacy-rear-clock", "name": "Rear Clock",
                "x": 64, "y": 0, "width": 26, "height": 10,
                "enabled": True, "background_color": None,
                "widget": {"type": "clock", "settings": clock_settings},
            },
        ],
    })
    before = profile.to_dict()
    result = Compositor(create_widget_registry()).render(
        profile,
        generation_time=NOW,
        animation_time=0.0,
        metrics_snapshot=snapshot(),
    )
    assert len(result.logical_framebuffer) == 90 * 10 * 3
    assert profile.to_dict() == before


@pytest.mark.parametrize("horizontal", ["start", "center", "end"])
@pytest.mark.parametrize("vertical", ["top", "center", "bottom"])
def test_system_metric_supports_all_alignments(horizontal: str, vertical: str) -> None:
    surface = RegionSurface(40, 10)
    render_system_metric(
        surface,
        SystemMetricSettings(
            display_mode="value_only",
            horizontal_alignment=horizontal,  # type: ignore[arg-type]
            vertical_alignment=vertical,  # type: ignore[arg-type]
        ),
        context(40, 10, snapshot(cpu_usage=42)),
    )
    assert bytes.fromhex("FFFFFF") in surface.to_bytes()


def test_system_metric_overflow_modes_use_existing_text_rules() -> None:
    metrics = snapshot(cpu_temperature=54)
    with pytest.raises(WidgetRenderingError) as raised:
        render_system_metric(
            RegionSurface(12, 5),
            SystemMetricSettings(font="3x5", overflow="error"),  # type: ignore[arg-type]
            context(12, 5, metrics),
        )
    assert raised.value.category == "content_too_wide"
    for overflow in ("truncate", "clip"):
        surface = RegionSurface(12, 5)
        render_system_metric(
            surface,
            SystemMetricSettings(font="3x5", overflow=overflow),  # type: ignore[arg-type]
            context(12, 5, metrics),
        )
        assert bytes.fromhex("FFFFFF") in surface.to_bytes()


@pytest.mark.parametrize(
    ("value", "extent", "expected"),
    [(None, 10, 0), (-1, 10, 0), (0, 10, 0), (24, 10, 2),
     (25, 10, 3), (50, 10, 5), (100, 10, 10), (101, 10, 10)],
)
def test_metric_bar_rounding_and_clamping(value, extent: int, expected: int) -> None:
    assert metric_bar_fill_length(value, MetricBarSettings(), extent) == expected


def test_temperature_metric_bar_keeps_canonical_celsius_value() -> None:
    settings = MetricBarSettings(
        metric="cpu_temperature", minimum=0, maximum=100
    )  # type: ignore[arg-type]
    surface = RegionSurface(10, 1)
    render_metric_bar(
        surface,
        settings,
        context(10, 1, snapshot(cpu_temperature=50)),
    )
    assert surface.to_bytes() == (
        bytes.fromhex("00D4C7") * 5 + bytes.fromhex("000000") * 5
    )


@pytest.mark.parametrize(
    ("orientation", "direction", "expected"),
    [
        ("horizontal", "start_to_end", {(0, 0), (1, 0)}),
        ("horizontal", "end_to_start", {(2, 0), (3, 0)}),
        ("vertical", "start_to_end", {(0, 2), (0, 3)}),
        ("vertical", "end_to_start", {(0, 0), (0, 1)}),
    ],
)
def test_metric_bar_directions_and_one_pixel_geometry(
    orientation: str, direction: str, expected: set[tuple[int, int]]
) -> None:
    width, height = (4, 1) if orientation == "horizontal" else (1, 4)
    settings = MetricBarSettings(
        orientation=orientation, direction=direction,  # type: ignore[arg-type]
        fill_color="#FFFFFF", empty_color="#000000",
    )
    surface = RegionSurface(width, height)
    render_metric_bar(
        surface, settings, context(width, height, snapshot(cpu_usage=50))
    )
    data = surface.to_bytes()
    actual = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if data[(y * width + x) * 3:][:3] == bytes.fromhex("FFFFFF")
    }
    assert actual == expected


def test_metric_bar_border_empty_and_registry_policy() -> None:
    surface = RegionSurface(5, 3)
    settings = MetricBarSettings(
        border_enabled=True, border_color="#FFFFFF",
        fill_color="#00FF00", empty_color="#010203",
    )
    render_metric_bar(surface, settings, context(5, 3, snapshot()))
    data = surface.to_bytes()
    assert data[:3] == bytes.fromhex("FFFFFF")
    assert data[(1 * 5 + 1) * 3:][:3] == bytes.fromhex("010203")
    registry = create_widget_registry()
    assert registry.resolve(WidgetType.SYSTEM_METRIC).resolve_update_policy(
        SystemMetricSettings()
    ) is UpdatePolicy.SECOND_BOUNDARY
    assert registry.resolve(WidgetType.METRIC_BAR).resolve_update_policy(
        MetricBarSettings()
    ) is UpdatePolicy.SECOND_BOUNDARY


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "uptime"},
        {"minimum": 10, "maximum": 10},
        {"orientation": "diagonal"},
        {"direction": "reverse"},
    ],
)
def test_metric_bar_rejects_invalid_settings(changes: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        MetricBarSettings(**changes)  # type: ignore[arg-type]


def test_metric_bar_eligibility_matches_canonical_metric_definitions() -> None:
    supported = {
        "cpu_usage", "memory_usage", "cpu_temperature", "storage_usage",
        "gpu_usage", "gpu_temperature", "gpu_memory_usage",
        "storage_temperature",
    }
    for metric in supported:
        assert MetricBarSettings(metric=metric).metric.value == metric  # type: ignore[arg-type]
    for metric in (
        "uptime", "load_average_1m", "network_receive_rate",
        "network_transmit_rate",
    ):
        with pytest.raises(ValueError, match="does not support"):
            MetricBarSettings(metric=metric)  # type: ignore[arg-type]
