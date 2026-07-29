"""Tests for inactive schema-v2 widget rendering contracts."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from nextuxlinq.models_v2 import CanvasConfig, ProfileConfigV2, RegionConfig
from nextuxlinq.rendering_v2.compositor import (
    Compositor,
    CompositorError,
    CompositorFailure,
)
from nextuxlinq.rendering_v2.registry import (
    DuplicateWidgetDefinitionError,
    RenderContext,
    RenderContextValidationError,
    UnknownWidgetDefinitionError,
    WidgetDefinition,
    WidgetRegistry,
    WidgetRenderError,
    create_phase2_widget_registry,
)
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.widget_models import (
    BlankSettings,
    CANONICAL_WIDGET_SETTINGS_MODELS,
    ClockSettings,
    DateSettings,
    ScrollingTextSettings,
    StaticTextSettings,
    WidgetConfig,
    WidgetType,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def clock_settings() -> ClockSettings:
    return ClockSettings(
        format="24h",  # type: ignore[arg-type]
        show_seconds=False,
        blink_separator=False,
        time_zone="local",
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
    )


def static_text_settings() -> StaticTextSettings:
    return StaticTextSettings(
        content="TEST",
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
        uppercase=True,
        overflow="clip",  # type: ignore[arg-type]
    )


def scrolling_text_settings() -> ScrollingTextSettings:
    return ScrollingTextSettings(
        content="TEST",
        direction="left",  # type: ignore[arg-type]
        speed=5.0,
        color="#FFFFFF",
        initial_alignment="end",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
        uppercase=True,
        trailing_gap=6,
    )


def date_settings() -> DateSettings:
    return DateSettings(
        format="iso",  # type: ignore[arg-type]
        time_zone="local",
        color="#FFFFFF",
        horizontal_alignment="center",  # type: ignore[arg-type]
        vertical_alignment="center",  # type: ignore[arg-type]
        font="auto",  # type: ignore[arg-type]
        overflow="clip",  # type: ignore[arg-type]
    )


def canonical_defaults() -> dict[WidgetType, object]:
    return {
        WidgetType.BLANK: BlankSettings(),
        WidgetType.CLOCK: clock_settings(),
        WidgetType.STATIC_TEXT: static_text_settings(),
        WidgetType.SCROLLING_TEXT: scrolling_text_settings(),
        WidgetType.DATE: date_settings(),
    }


def profile_with_widget(
    widget: WidgetConfig,
    *,
    width: int = 10,
    height: int = 5,
    enabled: bool = True,
) -> ProfileConfigV2:
    return ProfileConfigV2(
        brightness=None,
        canvas=CanvasConfig(
            width=90,
            height=10,
            background_color="#000000",
        ),
        regions=(
            RegionConfig(
                id="target",
                name="Target",
                x=0,
                y=0,
                width=width,
                height=height,
                enabled=enabled,
                background_color=None,
                widget=widget,
            ),
        ),
    )


def blank_definition(**changes: object) -> WidgetDefinition:
    values = {
        "widget_type": WidgetType.BLANK,
        "display_label": "Nothing / Gap",
        "settings_model": BlankSettings,
        "default_settings": BlankSettings(),
        "renderer": lambda surface, settings, context: None,
        "animated": False,
        "minimum_width": 1,
        "minimum_height": 1,
    }
    values.update(changes)
    return WidgetDefinition(**values)  # type: ignore[arg-type]


def test_phase2_registry_contains_only_blank_definition() -> None:
    registry = create_phase2_widget_registry()

    assert len(registry.definitions) == 1
    definition = registry.resolve(WidgetType.BLANK)
    assert definition.widget_type is WidgetType.BLANK
    assert definition.display_label == "Nothing / Gap"
    assert definition.settings_model is BlankSettings
    assert definition.default_settings == BlankSettings()
    assert definition.animated is False
    assert definition.minimum_width == 1
    assert definition.minimum_height == 1
    assert definition.renderer is not None


def test_blank_renderer_draws_no_foreground_content() -> None:
    registry = create_phase2_widget_registry()
    definition = registry.resolve(WidgetType.BLANK)
    assert definition.renderer is not None
    surface = RegionSurface(3, 2, RgbColor(10, 20, 30))
    before = surface.to_bytes()
    context = RenderContext(
        wall_clock=NOW,
        animation_time=4.5,
        canvas_width=90,
        canvas_height=10,
        region_id="blank",
        region_width=3,
        region_height=2,
    )

    result = definition.renderer(
        surface, definition.default_settings, context
    )

    assert result is None
    assert surface.to_bytes() == before


def test_registry_rejects_duplicate_widget_types() -> None:
    with pytest.raises(DuplicateWidgetDefinitionError):
        WidgetRegistry((blank_definition(), blank_definition()))


def test_authoritative_mapping_covers_every_widget_type_exactly_once() -> None:
    assert tuple(CANONICAL_WIDGET_SETTINGS_MODELS) == tuple(WidgetType)
    assert len(CANONICAL_WIDGET_SETTINGS_MODELS) == len(WidgetType)
    assert len(set(CANONICAL_WIDGET_SETTINGS_MODELS.values())) == len(
        WidgetType
    )

    with pytest.raises(TypeError):
        CANONICAL_WIDGET_SETTINGS_MODELS[WidgetType.BLANK] = (  # type: ignore[index]
            ClockSettings
        )


@pytest.mark.parametrize(
    "widget_type",
    list(WidgetType),
)
def test_every_widget_type_accepts_its_canonical_settings_model(
    widget_type: WidgetType,
) -> None:
    settings_model = CANONICAL_WIDGET_SETTINGS_MODELS[widget_type]
    default_settings = canonical_defaults()[widget_type]

    definition = WidgetDefinition(
        widget_type=widget_type,
        display_label="Test widget",
        settings_model=settings_model,
        default_settings=default_settings,
        renderer=lambda surface, settings, context: None,
        animated=False,
    )

    assert definition.settings_model is settings_model
    assert definition.default_settings is default_settings
    assert WidgetRegistry((definition,)).resolve(widget_type) is definition


@pytest.mark.parametrize(
    "widget_type,wrong_model,wrong_default",
    [
        (WidgetType.BLANK, ClockSettings, clock_settings()),
        (
            WidgetType.CLOCK,
            StaticTextSettings,
            static_text_settings(),
        ),
        (
            WidgetType.STATIC_TEXT,
            ClockSettings,
            clock_settings(),
        ),
        (
            WidgetType.SCROLLING_TEXT,
            DateSettings,
            date_settings(),
        ),
        (WidgetType.DATE, BlankSettings, BlankSettings()),
    ],
)
def test_every_widget_type_rejects_an_incompatible_settings_model(
    widget_type: WidgetType,
    wrong_model: type,
    wrong_default: object,
) -> None:
    with pytest.raises(TypeError) as captured:
        WidgetDefinition(
            widget_type=widget_type,
            display_label="Invalid widget",
            settings_model=wrong_model,
            default_settings=wrong_default,
            renderer=lambda surface, settings, context: None,
            animated=False,
        )

    assert str(captured.value) == (
        f"{widget_type.value} widget definition has an incompatible "
        "settings model."
    )
    assert "class" not in str(captured.value)
    assert "0x" not in str(captured.value)
    assert "/" not in str(captured.value)


def test_incompatible_definition_fails_before_it_can_enter_registry() -> None:
    definitions: list[WidgetDefinition] = []

    with pytest.raises(TypeError):
        definitions.append(
            WidgetDefinition(
                widget_type=WidgetType.CLOCK,
                display_label="Invalid clock",
                settings_model=StaticTextSettings,
                default_settings=static_text_settings(),
                renderer=lambda surface, settings, context: None,
                animated=False,
            )
        )

    registry = WidgetRegistry(definitions)
    assert registry.definitions == ()
    with pytest.raises(UnknownWidgetDefinitionError):
        registry.resolve(WidgetType.CLOCK)


@pytest.mark.parametrize(
    "widget_type,wrong_default",
    [
        (WidgetType.BLANK, clock_settings()),
        (WidgetType.CLOCK, static_text_settings()),
        (WidgetType.STATIC_TEXT, scrolling_text_settings()),
        (WidgetType.SCROLLING_TEXT, date_settings()),
        (WidgetType.DATE, BlankSettings()),
    ],
)
def test_canonical_model_rejects_incorrect_default_settings(
    widget_type: WidgetType,
    wrong_default: object,
) -> None:
    with pytest.raises(TypeError) as captured:
        WidgetDefinition(
            widget_type=widget_type,
            display_label="Invalid default",
            settings_model=CANONICAL_WIDGET_SETTINGS_MODELS[widget_type],
            default_settings=wrong_default,
            renderer=lambda surface, settings, context: None,
            animated=False,
        )

    assert str(captured.value) == (
        f"{widget_type.value} widget definition has incompatible "
        "default settings."
    )


def test_settings_model_and_default_subclasses_are_rejected() -> None:
    class DerivedBlankSettings(BlankSettings):
        pass

    with pytest.raises(TypeError, match="incompatible settings model"):
        WidgetDefinition(
            widget_type=WidgetType.BLANK,
            display_label="Derived model",
            settings_model=DerivedBlankSettings,
            default_settings=DerivedBlankSettings(),
            renderer=lambda surface, settings, context: None,
            animated=False,
        )

    with pytest.raises(TypeError, match="incompatible default settings"):
        WidgetDefinition(
            widget_type=WidgetType.BLANK,
            display_label="Derived default",
            settings_model=BlankSettings,
            default_settings=DerivedBlankSettings(),
            renderer=lambda surface, settings, context: None,
            animated=False,
        )


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"minimum_width": True}, TypeError),
        ({"minimum_height": False}, TypeError),
        ({"minimum_width": 0}, ValueError),
        ({"minimum_height": 0}, ValueError),
        ({"minimum_width": 91}, ValueError),
        ({"minimum_height": 11}, ValueError),
        ({"minimum_width": 1.5}, TypeError),
        ({"minimum_height": "1"}, TypeError),
    ],
)
def test_definition_rejects_invalid_minimum_geometry(
    changes: dict[str, object], error: type[Exception]
) -> None:
    with pytest.raises(error):
        blank_definition(**changes)


def test_registry_resolves_registered_and_rejects_unknown_type() -> None:
    registry = WidgetRegistry((blank_definition(),))

    assert registry.resolve(WidgetType.BLANK).display_label == (
        "Nothing / Gap"
    )
    with pytest.raises(UnknownWidgetDefinitionError) as captured:
        registry.resolve(WidgetType.CLOCK)
    assert captured.value.widget_type is WidgetType.CLOCK


def test_known_definition_without_renderer_is_distinct() -> None:
    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=clock_settings(),
        renderer=None,
        animated=False,
    )
    registry = WidgetRegistry((definition,))

    assert registry.resolve(WidgetType.CLOCK).renderer is None
    profile = profile_with_widget(
        WidgetConfig(
            type=WidgetType.CLOCK,
            settings=clock_settings(),
        )
    )
    with pytest.raises(CompositorError) as captured:
        Compositor(registry).render(
            profile, generation_time=NOW, animation_time=1.0
        )
    assert captured.value.category is (
        CompositorFailure.MISSING_WIDGET_RENDERER
    )


def test_injected_renderer_runs_once_with_exact_geometry_and_times() -> None:
    calls: list[tuple[int, int, datetime, float, str]] = []

    def renderer(
        surface: RegionSurface,
        settings: ClockSettings,
        context: RenderContext,
    ) -> None:
        calls.append(
            (
                surface.width,
                surface.height,
                context.wall_clock,
                context.animation_time,
                context.region_id,
            )
        )
        surface.set_pixel(0, 0, RgbColor(1, 2, 3))

    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=clock_settings(),
        renderer=renderer,
        animated=True,
    )
    profile = profile_with_widget(
        WidgetConfig(
            type=WidgetType.CLOCK,
            settings=clock_settings(),
        ),
        width=90,
        height=10,
    )

    result = Compositor(WidgetRegistry((definition,))).render(
        profile,
        generation_time=NOW,
        animation_time=12.25,
    )

    assert calls == [(90, 10, NOW, 12.25, "target")]
    assert result.animated_content is True


def test_compositor_defensively_rejects_corrupt_settings_instance() -> None:
    called = False

    def renderer(
        surface: RegionSurface,
        settings: ClockSettings,
        context: RenderContext,
    ) -> None:
        nonlocal called
        called = True

    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=clock_settings(),
        renderer=renderer,
        animated=False,
    )
    corrupted_widget = WidgetConfig(
        type=WidgetType.CLOCK,
        settings=clock_settings(),
    )
    object.__setattr__(
        corrupted_widget, "settings", static_text_settings()
    )
    profile = profile_with_widget(corrupted_widget)

    with pytest.raises(CompositorError) as captured:
        Compositor(WidgetRegistry((definition,))).render(
            profile, generation_time=NOW, animation_time=1.0
        )

    assert captured.value.category is CompositorFailure.SETTINGS_MISMATCH
    assert called is False


def test_non_none_renderer_result_is_rejected() -> None:
    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=clock_settings(),
        renderer=lambda surface, settings, context: "invalid",  # type: ignore[arg-type,return-value]
        animated=False,
    )
    profile = profile_with_widget(
        WidgetConfig(
            type=WidgetType.CLOCK,
            settings=clock_settings(),
        )
    )

    with pytest.raises(CompositorError) as captured:
        Compositor(WidgetRegistry((definition,))).render(
            profile, generation_time=NOW, animation_time=1.0
        )

    assert captured.value.category is (
        CompositorFailure.INVALID_RENDERER_RETURN
    )


@pytest.mark.parametrize(
    "controlled,category",
    [
        (True, CompositorFailure.CONTROLLED_RENDERER_FAILURE),
        (False, CompositorFailure.UNEXPECTED_RENDERER_FAILURE),
    ],
)
def test_renderer_failures_are_sanitized(
    controlled: bool, category: CompositorFailure
) -> None:
    private_detail = "/home/user/private/config.json at 0xDEADBEEF"

    def renderer(surface, settings, context):
        if controlled:
            raise WidgetRenderError(private_detail)
        raise RuntimeError(private_detail)

    definition = WidgetDefinition(
        widget_type=WidgetType.CLOCK,
        display_label="Clock",
        settings_model=ClockSettings,
        default_settings=clock_settings(),
        renderer=renderer,
        animated=False,
    )
    profile = profile_with_widget(
        WidgetConfig(
            type=WidgetType.CLOCK,
            settings=clock_settings(),
        )
    )

    with pytest.raises(CompositorError) as captured:
        Compositor(WidgetRegistry((definition,))).render(
            profile, generation_time=NOW, animation_time=1.0
        )

    assert captured.value.category is category
    assert captured.value.region_id == "target"
    assert captured.value.widget_type is WidgetType.CLOCK
    assert private_detail not in str(captured.value)
    assert "/home/" not in str(captured.value)
    assert "0xDEADBEEF" not in str(captured.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"wall_clock": datetime(2026, 7, 29, 12, 0)},
        {"wall_clock": "2026-07-29"},
        {"animation_time": True},
        {"animation_time": math.nan},
        {"animation_time": math.inf},
        {"animation_time": -0.1},
        {"canvas_width": 89},
        {"canvas_height": 9},
        {"canvas_width": True},
        {"region_width": 0},
        {"region_height": 11},
        {"region_id": "../unsafe"},
    ],
)
def test_render_context_rejects_invalid_time_and_geometry(
    changes: dict[str, object],
) -> None:
    values = {
        "wall_clock": NOW,
        "animation_time": 1.0,
        "canvas_width": 90,
        "canvas_height": 10,
        "region_id": "safe-region",
        "region_width": 10,
        "region_height": 5,
    }
    values.update(changes)

    with pytest.raises(RenderContextValidationError):
        RenderContext(**values)  # type: ignore[arg-type]
