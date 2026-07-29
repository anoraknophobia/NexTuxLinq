"""Phase 3D inactive registry, compositor, and migration integration tests."""

from __future__ import annotations

import math
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nextuxlinq.defaults import default_configuration
from nextuxlinq.migration import migrate_configuration_v1_to_v2
from nextuxlinq.models import ConfigurationDocument, ProfileConfig
from nextuxlinq.models_v2 import CanvasConfig, ProfileConfigV2, RegionConfig
from nextuxlinq.rendering import (
    FONT_5X7,
    MARQUEE_TRAILING_GAP,
    physical_preview_bytes,
    render_profile_frame,
    text_width,
)
from nextuxlinq.rendering_v2.compositor import (
    Compositor,
    CompositorError,
    CompositorFailure,
    RenderResult,
)
from nextuxlinq.rendering_v2.registry import (
    WidgetDefinition,
    WidgetRegistry,
    create_widget_registry,
)
from nextuxlinq.rendering_v2.update_policy import (
    UpdatePolicy,
    UpdatePolicyResolver,
)
from nextuxlinq.widget_models import (
    ClockSettings,
    FontChoice,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 7, 29, 16, 26, 37, tzinfo=timezone.utc)
CANVAS = bytes.fromhex("010203")


def widget(widget_type: WidgetType, **changes: object) -> WidgetConfig:
    definition = create_widget_registry().resolve(widget_type)
    raw = definition.default_settings.to_dict()
    raw.update(changes)
    return WidgetConfig(
        type=widget_type,
        settings=definition.settings_model.from_dict(raw),
    )


def region(
    region_id: str,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    widget_config: WidgetConfig,
    enabled: bool = True,
    background_color: str | None = None,
) -> RegionConfig:
    return RegionConfig(
        id=region_id,
        name=region_id.replace("-", " ").title(),
        x=x,
        y=y,
        width=width,
        height=height,
        enabled=enabled,
        background_color=background_color,
        widget=widget_config,
    )


def profile(
    regions: tuple[RegionConfig, ...],
    *,
    background_color: str = "#010203",
) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(
            width=90,
            height=10,
            background_color=background_color,
        ),
        regions=regions,
    )


def render(
    candidate: ProfileConfigV2,
    *,
    animation_time: float = 2.5,
    registry: WidgetRegistry | None = None,
) -> RenderResult:
    return Compositor(
        create_widget_registry() if registry is None else registry
    ).render(
        candidate,
        generation_time=NOW,
        animation_time=animation_time,
    )


def pixel(payload: bytes, x: int, y: int) -> bytes:
    offset = (y * 90 + x) * 3
    return payload[offset : offset + 3]


def assert_region_orientation(
    result: RenderResult, item: RegionConfig
) -> None:
    device_x = 90 - item.x - item.width
    for local_y in range(item.height):
        for local_x in range(item.width):
            assert pixel(
                result.logical_framebuffer,
                item.x + local_x,
                item.y + local_y,
            ) == pixel(
                result.device_framebuffer,
                device_x + local_x,
                item.y + local_y,
            )


@pytest.mark.parametrize(
    ("widget_type", "expected_policy", "animated", "has_foreground"),
    [
        (WidgetType.BLANK, UpdatePolicy.STATIC, False, False),
        (WidgetType.CLOCK, UpdatePolicy.MINUTE_BOUNDARY, False, True),
        (WidgetType.STATIC_TEXT, UpdatePolicy.STATIC, False, True),
        (WidgetType.SCROLLING_TEXT, UpdatePolicy.CONTINUOUS, True, True),
        (WidgetType.DATE, UpdatePolicy.DAY_BOUNDARY, False, True),
    ],
)
def test_each_canonical_widget_composes_through_integrated_registry(
    widget_type: WidgetType,
    expected_policy: UpdatePolicy,
    animated: bool,
    has_foreground: bool,
) -> None:
    item = region(
        widget_type.value,
        x=0,
        y=0,
        width=90,
        height=10,
        widget_config=widget(widget_type),
    )
    first = render(profile((item,)))
    second = render(profile((item,)))

    assert first == second
    assert first.update_policy is expected_policy
    assert first.animated_content is animated
    assert (first.logical_framebuffer != CANVAS * 900) is has_foreground
    assert_region_orientation(first, item)


@pytest.mark.parametrize(
    ("widgets", "expected_policy", "animated"),
    [
        ((WidgetType.BLANK,), UpdatePolicy.STATIC, False),
        ((WidgetType.DATE,), UpdatePolicy.DAY_BOUNDARY, False),
        (
            (WidgetType.DATE, WidgetType.CLOCK),
            UpdatePolicy.MINUTE_BOUNDARY,
            False,
        ),
        (
            (WidgetType.DATE, "seconds"),
            UpdatePolicy.SECOND_BOUNDARY,
            True,
        ),
        (
            (WidgetType.DATE, "blink"),
            UpdatePolicy.HALF_SECOND,
            True,
        ),
        (
            (WidgetType.DATE, WidgetType.SCROLLING_TEXT),
            UpdatePolicy.CONTINUOUS,
            True,
        ),
    ],
)
def test_aggregate_update_policy_precedence(
    widgets: tuple[WidgetType | str, ...],
    expected_policy: UpdatePolicy,
    animated: bool,
) -> None:
    items: list[RegionConfig] = []
    width = 90 // len(widgets)
    for index, selected in enumerate(widgets):
        if selected == "seconds":
            configured = widget(WidgetType.CLOCK, show_seconds=True)
        elif selected == "blink":
            configured = widget(WidgetType.CLOCK, blink_separator=True)
        elif selected is WidgetType.DATE and len(widgets) > 1:
            configured = widget(
                WidgetType.DATE,
                format="day_month",
                font="3x5",
            )
        else:
            assert isinstance(selected, WidgetType)
            configured = widget(selected)
        items.append(
            region(
                f"region-{index}",
                x=index * width,
                y=0,
                width=width if index + 1 < len(widgets) else 90 - index * width,
                height=10,
                widget_config=configured,
            )
        )

    result = render(profile(tuple(items)))

    assert result.update_policy is expected_policy
    assert result.animated_content is animated


def test_empty_and_disabled_layouts_are_static() -> None:
    empty = render(profile(()))
    disabled = render(
        profile(
            (
                region(
                    "disabled-scroll",
                    x=0,
                    y=0,
                    width=90,
                    height=10,
                    widget_config=widget(WidgetType.SCROLLING_TEXT),
                    enabled=False,
                    background_color="#FF0000",
                ),
            )
        )
    )

    for result in (empty, disabled):
        assert result.update_policy is UpdatePolicy.STATIC
        assert result.animated_content is False
        assert result.logical_framebuffer == CANVAS * 900
        assert result.device_framebuffer == CANVAS * 900
    with pytest.raises((AttributeError, TypeError)):
        empty.update_policy = UpdatePolicy.CONTINUOUS  # type: ignore[misc]


@pytest.mark.parametrize(
    "configured",
    [
        widget(WidgetType.BLANK),
        widget(WidgetType.STATIC_TEXT, overflow="clip"),
        widget(WidgetType.SCROLLING_TEXT),
        widget(
            WidgetType.DATE,
            overflow="clip",
            font=FontChoice.COMPACT_3X5.value,
        ),
    ],
    ids=("blank", "static-clip", "scroll", "date-clip"),
)
def test_editor_minimum_guidance_does_not_reject_valid_width_one_regions(
    configured: WidgetConfig,
) -> None:
    height = 1 if configured.type is WidgetType.BLANK else 5
    result = render(
        profile(
            (
                region(
                    "narrow",
                    x=0,
                    y=0,
                    width=1,
                    height=height,
                    widget_config=configured,
                ),
            )
        ),
        animation_time=1.0,
    )

    assert len(result.logical_framebuffer) == 2700


def test_all_five_widgets_compose_in_order_with_local_backgrounds() -> None:
    items = (
        region(
            "clock",
            x=0,
            y=0,
            width=17,
            height=5,
            widget_config=widget(WidgetType.CLOCK),
            background_color="#100000",
        ),
        region(
            "date",
            x=17,
            y=0,
            width=19,
            height=5,
            widget_config=widget(
                WidgetType.DATE,
                format="day_month",
                font="3x5",
            ),
            background_color="#001000",
        ),
        region(
            "blank",
            x=36,
            y=0,
            width=54,
            height=5,
            widget_config=widget(WidgetType.BLANK),
            background_color="#000010",
        ),
        region(
            "static",
            x=0,
            y=5,
            width=20,
            height=5,
            widget_config=widget(WidgetType.STATIC_TEXT),
            background_color="#101000",
        ),
        region(
            "scroll",
            x=20,
            y=5,
            width=70,
            height=5,
            widget_config=widget(WidgetType.SCROLLING_TEXT),
            background_color="#001010",
        ),
    )

    result = render(profile(items), animation_time=4.0)

    assert tuple(item.id for item in result.regions) == tuple(
        item.id for item in items
    )
    assert result.update_policy is UpdatePolicy.CONTINUOUS
    assert result.animated_content is True
    assert pixel(result.logical_framebuffer, 89, 0) == bytes.fromhex(
        "000010"
    )
    assert pixel(result.logical_framebuffer, 19, 5) == bytes.fromhex(
        "101000"
    )
    for item in items:
        assert_region_orientation(result, item)


def test_two_five_pixel_rows_use_3x5_without_bleed_or_scaling() -> None:
    automatic = profile(
        (
            region(
                "top-clock",
                x=10,
                y=0,
                width=30,
                height=5,
                widget_config=widget(
                    WidgetType.CLOCK,
                    color="#FF0000",
                ),
            ),
            region(
                "bottom-static",
                x=10,
                y=5,
                width=30,
                height=5,
                widget_config=widget(
                    WidgetType.STATIC_TEXT,
                    color="#00FF00",
                ),
            ),
        ),
        background_color="#112233",
    )
    explicit = profile(
        (
            region(
                "top-clock",
                x=10,
                y=0,
                width=30,
                height=5,
                widget_config=widget(
                    WidgetType.CLOCK,
                    color="#FF0000",
                    font=FontChoice.COMPACT_3X5.value,
                ),
            ),
            region(
                "bottom-static",
                x=10,
                y=5,
                width=30,
                height=5,
                widget_config=widget(
                    WidgetType.STATIC_TEXT,
                    color="#00FF00",
                    font=FontChoice.COMPACT_3X5.value,
                ),
            ),
        ),
        background_color="#112233",
    )

    result = render(automatic)
    explicit_result = render(explicit)

    assert result.logical_framebuffer == explicit_result.logical_framebuffer
    assert result.device_framebuffer == explicit_result.device_framebuffer
    red = bytes.fromhex("FF0000")
    green = bytes.fromhex("00FF00")
    assert all(
        pixel(result.logical_framebuffer, x, y) != red
        for y in range(5, 10)
        for x in range(90)
    )
    assert all(
        pixel(result.logical_framebuffer, x, y) != green
        for y in range(5)
        for x in range(90)
    )
    for item in automatic.regions:
        assert_region_orientation(result, item)
    assert pixel(result.logical_framebuffer, 0, 0) == bytes.fromhex(
        "112233"
    )
    assert pixel(result.device_framebuffer, 0, 0) == bytes.fromhex(
        "112233"
    )


def assert_sanitized(
    error: CompositorError,
    *,
    category: CompositorFailure,
    field: str | None,
    original: BaseException | None = None,
    prohibited: tuple[str, ...] = (),
) -> None:
    assert error.category is category
    assert error.field == field
    assert error.__context__ is None
    assert error.__cause__ is None
    assert not any(
        isinstance(value, BaseException)
        for value in (*vars(error).values(), *error.args)
    )
    if original is not None:
        assert all(value is not original for value in vars(error).values())
        assert all(value is not original for value in error.args)
    formatted = "".join(traceback.format_exception(error))
    for value in prohibited:
        assert value not in str(error)
        assert value not in repr(error)
        assert value not in formatted


def test_controlled_compositor_failure_preserves_only_stable_metadata() -> None:
    secret_content = "phase3d_secret_lowercase"
    candidate = profile(
        (
            region(
                "safe-region",
                x=0,
                y=0,
                width=90,
                height=10,
                widget_config=widget(
                    WidgetType.STATIC_TEXT,
                    content=secret_content,
                    uppercase=False,
                ),
            ),
        )
    )

    with pytest.raises(CompositorError) as captured:
        render(candidate)

    assert captured.value.region_id == "safe-region"
    assert captured.value.widget_type is WidgetType.STATIC_TEXT
    assert_sanitized(
        captured.value,
        category=CompositorFailure.UNSUPPORTED_GLYPH,
        field="content",
        prohibited=(secret_content, "FontTextError", "WidgetRenderingError"),
    )
    assert not hasattr(captured.value, "partial_result")


def test_unexpected_compositor_failure_drops_original_exception() -> None:
    secret = "PHASE3D_UNEXPECTED_SECRET_6C91"
    original = RuntimeError(secret)

    def fail(surface, settings, context):
        raise original

    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=widget(WidgetType.CLOCK).settings,
        renderer=fail,
        update_policy_resolver=UpdatePolicyResolver.fixed(
            ClockSettings, UpdatePolicy.MINUTE_BOUNDARY
        ),
    )
    candidate = profile(
        (
            region(
                "clock",
                x=0,
                y=0,
                width=90,
                height=10,
                widget_config=widget(WidgetType.CLOCK),
            ),
        )
    )

    with pytest.raises(CompositorError) as captured:
        render(candidate, registry=WidgetRegistry((definition,)))

    assert_sanitized(
        captured.value,
        category=CompositorFailure.UNEXPECTED_RENDERER_FAILURE,
        field=None,
        original=original,
        prohibited=(secret, "RuntimeError"),
    )
    assert not hasattr(captured.value, "partial_result")


def test_renderer_cannot_inject_compositor_error_metadata() -> None:
    secret = "PHASE3D_COMPOSITOR_SECRET_2A74"
    original = CompositorError(
        CompositorFailure.CONTENT_TOO_WIDE,
        region_id=secret,
        widget_type=WidgetType.CLOCK,
        field=secret,
    )

    def fail(surface, settings, context):
        raise original

    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=widget(WidgetType.CLOCK).settings,
        renderer=fail,
        update_policy_resolver=UpdatePolicyResolver.fixed(
            ClockSettings, UpdatePolicy.MINUTE_BOUNDARY
        ),
    )
    candidate = profile(
        (
            region(
                "clock",
                x=0,
                y=0,
                width=90,
                height=10,
                widget_config=widget(WidgetType.CLOCK),
            ),
        )
    )

    with pytest.raises(CompositorError) as captured:
        render(candidate, registry=WidgetRegistry((definition,)))

    assert_sanitized(
        captured.value,
        category=CompositorFailure.UNEXPECTED_RENDERER_FAILURE,
        field=None,
        original=original,
        prohibited=(secret, "content_too_wide"),
    )


def custom_configuration() -> ConfigurationDocument:
    raw = default_configuration().active.to_dict()
    raw.update(
        {
            "scrolling_text": "MATRIX 900",
            "text_color": "#123456",
            "clock_color": "#ABCDEF",
            "scroll_speed": 2.0,
            "brightness": 70,
        }
    )
    return ConfigurationDocument(
        schema_version=1,
        active_profile="custom",
        profiles={"custom": ProfileConfig.from_dict(raw)},
    )


@pytest.mark.parametrize(
    "source",
    [default_configuration(), custom_configuration()],
    ids=("default", "custom"),
)
def test_full_migrated_profile_matches_complete_active_framebuffers(
    source: ConfigurationDocument,
) -> None:
    migrated = migrate_configuration_v1_to_v2(source)
    legacy_profile = source.active
    cycle = (
        legacy_profile.layout.scroll_text_width
        + text_width(legacy_profile.scrolling_text, FONT_5X7)
        + MARQUEE_TRAILING_GAP
    )
    offsets = (0, 1, legacy_profile.layout.scroll_text_width, cycle - 1, cycle)

    for offset in offsets:
        animation_time = offset / legacy_profile.scroll_speed
        assert math.floor(
            animation_time * legacy_profile.scroll_speed
        ) == offset
        integrated = Compositor(create_widget_registry()).render(
            migrated.active,
            generation_time=NOW,
            animation_time=animation_time,
        )
        active = render_profile_frame(
            legacy_profile,
            NOW,
            scroll_offset=offset,
        )

        assert integrated.logical_framebuffer == physical_preview_bytes(
            active, legacy_profile
        )
        assert integrated.device_framebuffer == active.to_bytes()


def test_active_runtime_modules_do_not_import_inactive_registry() -> None:
    root = Path(__file__).parents[1] / "src" / "nextuxlinq"
    active_modules = (
        "rendering.py",
        "migration.py",
        "daemon.py",
        "web.py",
        "config_store.py",
        "device_worker.py",
        "creator.py",
        "protocol.py",
        "transport.py",
        "service.py",
        "service_manager.py",
    )

    for name in active_modules:
        source = (root / name).read_text(encoding="utf-8")
        assert "rendering_v2.registry" not in source
        assert "create_widget_registry" not in source
