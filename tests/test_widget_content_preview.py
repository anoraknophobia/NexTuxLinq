"""Tests for the side-effect-free authoritative widget-content preview."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nextuxlinq.models_v2 import (
    CanvasConfig,
    ForegroundRainbowEffect,
    NoDisplayEffect,
    RegionConfig,
)
from nextuxlinq.rendering_v2.registry import create_widget_registry
from nextuxlinq.service import NexTuxLinqService
from nextuxlinq.widget_models import (
    MetricDisplayMode,
    SystemMetricSettings,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class NoSideEffects:
    def __getattr__(self, name: str):
        raise AssertionError(f"content preview attempted side effect {name}")


@pytest.fixture
def service() -> NexTuxLinqService:
    return NexTuxLinqService(
        config_store=NoSideEffects(),  # type: ignore[arg-type]
        device_worker=NoSideEffects(),  # type: ignore[arg-type]
        monotonic=lambda: 0.0,
        wall_clock=lambda: NOW,
        metrics_provider=NoSideEffects(),  # type: ignore[arg-type]
    )


def region(widget: WidgetConfig, width: int, height: int) -> RegionConfig:
    return RegionConfig(
        id="preview-target",
        name="Preview",
        x=0,
        y=0,
        width=width,
        height=height,
        enabled=True,
        background_color="#102030",
        widget=widget,
    )


def canvas() -> CanvasConfig:
    return CanvasConfig(90, 10, "#000000")


def test_invalid_icon_region_gets_intrinsic_authoritative_preview_without_mutation(
    service: NexTuxLinqService,
) -> None:
    widget = WidgetConfig.from_dict({
        "type": "built_in_icon",
        "settings": {
            "icon": "cachyos",
            "horizontal_alignment": "center",
            "vertical_alignment": "center",
        },
    })
    source = region(widget, 10, 5)
    before = source.to_dict()
    result = service.get_widget_content_preview(
        source, canvas(), ForegroundRainbowEffect(speed=0.1)
    )
    assert source.to_dict() == before
    assert (result.current_width, result.current_height) == (10, 5)
    assert (result.preview_width, result.preview_height) == (15, 10)
    assert (result.minimum_width, result.minimum_height) == (15, 10)
    assert result.fit_state == "current_region_too_small"
    assert result.current_output == "CachyOS Logo"
    assert bytes.fromhex("1DC7B5") in result.framebuffer
    assert len(result.framebuffer) == 15 * 10 * 3


def test_value_only_preview_uses_example_and_excludes_hidden_label_width(
    service: NexTuxLinqService,
) -> None:
    settings = SystemMetricSettings(
        metric="load_average_1m",
        label="LOAD",
        display_mode=MetricDisplayMode.VALUE_ONLY,
        precision=2,
    )  # type: ignore[arg-type]
    source = region(WidgetConfig(WidgetType.SYSTEM_METRIC, settings), 25, 5)
    result = service.get_widget_content_preview(
        source, canvas(), NoDisplayEffect()
    )
    assert result.current_output == "1.24"
    assert result.output_is_example is True
    assert result.resolved_font == "3x5"
    assert result.fit_state == "fits_current_region"
    assert result.minimum_width is not None and result.minimum_width <= 25
    assert "LOAD" not in result.current_output


def test_label_and_value_preview_offers_value_only_only_when_width_reduces(
    service: NexTuxLinqService,
) -> None:
    settings = SystemMetricSettings(
        metric="network_receive_rate",
        label="NET RX",
        display_mode="label_and_value",
        precision=1,
    )  # type: ignore[arg-type]
    result = service.get_widget_content_preview(
        region(WidgetConfig(WidgetType.SYSTEM_METRIC, settings), 20, 5),
        canvas(),
        NoDisplayEffect(),
    )
    assert result.current_output == "NET RX 1.2MB/S"
    assert result.output_is_example is True
    assert result.suggest_value_only is True


@pytest.mark.parametrize("widget_type", list(WidgetType))
def test_content_preview_supports_every_registered_widget(
    service: NexTuxLinqService, widget_type: WidgetType
) -> None:
    definition = create_widget_registry().resolve(widget_type)
    source = region(
        WidgetConfig(widget_type, definition.default_settings), 1, 1
    )
    result = service.get_widget_content_preview(
        source, canvas(), NoDisplayEffect()
    )
    assert 1 <= result.preview_width <= 90
    assert 1 <= result.preview_height <= 10
    assert len(result.framebuffer) == (
        result.preview_width * result.preview_height * 3
    )
    assert result.fit_state in {
        "fits_current_region",
        "would_fit_with_smaller_font",
        "current_region_too_small",
        "no_available_font_supports_content",
    }


def test_repeated_preview_has_no_stale_pixels_and_is_deterministic(
    service: NexTuxLinqService,
) -> None:
    definition = create_widget_registry().resolve(WidgetType.BUILT_IN_ICON)
    source = region(
        WidgetConfig(WidgetType.BUILT_IN_ICON, definition.default_settings), 15, 10
    )
    first = service.get_widget_content_preview(source, canvas(), NoDisplayEffect())
    second = service.get_widget_content_preview(source, canvas(), NoDisplayEffect())
    assert first == second


def test_required_geometry_larger_than_canvas_remains_safely_bounded(
    service: NexTuxLinqService,
) -> None:
    definition = create_widget_registry().resolve(WidgetType.STATIC_TEXT)
    raw = definition.default_settings.to_dict()
    raw.update({"content": "W" * 64, "font": "5x10", "overflow": "error"})
    source = region(
        WidgetConfig.from_dict({"type": "static_text", "settings": raw}),
        90,
        10,
    )
    result = service.get_widget_content_preview(
        source, canvas(), NoDisplayEffect()
    )
    assert result.minimum_width is not None and result.minimum_width > 90
    assert (result.preview_width, result.preview_height) == (90, 10)
    assert len(result.framebuffer) == 90 * 10 * 3
    assert result.fit_state == "current_region_too_small"
