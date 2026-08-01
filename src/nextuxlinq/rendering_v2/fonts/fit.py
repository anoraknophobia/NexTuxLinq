"""Authoritative deterministic native-font fitting and safe diagnostics."""

from __future__ import annotations

from collections.abc import Callable

from nextuxlinq.rendering_v2.fonts.builtin import AUTO_FONT_PRIORITY
from nextuxlinq.rendering_v2.fonts.models import (
    FontCandidateFit,
    FontDefinition,
    FontFitAnalysis,
    FontFitRequirement,
    FontRegistry,
    FontTextError,
)
from nextuxlinq.widget_models import FontChoice

FontRequirementResolver = Callable[[FontDefinition], FontFitRequirement]


def analyze_font_fit(
    registry: FontRegistry,
    selected_font: FontChoice,
    *,
    region_width: int,
    region_height: int,
    requirement_for: FontRequirementResolver,
) -> FontFitAnalysis:
    """Analyze every canonical candidate and resolve Auto from that analysis."""
    if not isinstance(selected_font, FontChoice):
        raise FontTextError("unknown_font", field="font")
    if (
        isinstance(region_width, bool)
        or not isinstance(region_width, int)
        or region_width <= 0
        or isinstance(region_height, bool)
        or not isinstance(region_height, int)
        or region_height <= 0
    ):
        raise FontTextError("renderer_invariant", field="region")

    candidates: list[FontCandidateFit] = []
    for font_id in AUTO_FONT_PRIORITY:
        try:
            font = registry.resolve(font_id)
        except FontTextError:
            continue
        requirement = requirement_for(font)
        if type(requirement) is not FontFitRequirement:
            raise FontTextError("renderer_invariant", field="font_fit")
        coverage = (
            requirement.compatible_with_widget
            and requirement.complete_glyph_coverage
        )
        fits_height = coverage and requirement.minimum_height <= region_height
        fits_width = (
            coverage
            and requirement.minimum_width is not None
            and requirement.minimum_width <= region_width
        )
        overall = fits_height and fits_width
        if not requirement.compatible_with_widget:
            rejection = "unsupported_by_widget"
        elif not requirement.complete_glyph_coverage:
            rejection = "unsupported_characters"
        elif not fits_height:
            rejection = "too_tall"
        elif not fits_width:
            rejection = "too_wide"
        else:
            rejection = None
        candidates.append(FontCandidateFit(
            font_id=font_id,
            display_label=font_id.value.replace("x", "×"),
            glyph_width=max(glyph.width for glyph in font.glyphs.values()),
            glyph_height=font.glyph_height,
            compatible_with_widget=requirement.compatible_with_widget,
            complete_glyph_coverage=requirement.complete_glyph_coverage,
            fits_height=fits_height,
            fits_width=fits_width,
            overall_fit=overall,
            minimum_width=(requirement.minimum_width if coverage else None),
            minimum_height=(requirement.minimum_height if coverage else None),
            rejection_category=rejection,
        ))

    fitting = tuple(item for item in candidates if item.overall_fit)
    supported = tuple(
        item for item in candidates
        if item.compatible_with_widget and item.complete_glyph_coverage
    )
    selected_explicit = (
        None if selected_font is FontChoice.AUTO else selected_font
    )
    best_smaller = None
    if selected_explicit is not None:
        selected_index = AUTO_FONT_PRIORITY.index(selected_explicit)
        best_smaller = next(
            (
                item.font_id for item in candidates[selected_index + 1 :]
                if item.overall_fit
            ),
            None,
        )
    largest = fitting[0].font_id if fitting else None
    smallest = fitting[-1].font_id if fitting else None
    return FontFitAnalysis(
        actual_width=region_width,
        actual_height=region_height,
        selected_explicit_font=selected_explicit,
        resolved_auto_font=(largest if selected_font is FontChoice.AUTO else None),
        largest_fitting_font=largest,
        smallest_fitting_font=smallest,
        best_smaller_alternative=best_smaller,
        no_font_fits=not fitting,
        no_font_supports_content=not supported,
        candidates=tuple(candidates),
    )


def resolve_analyzed_font(
    registry: FontRegistry,
    analysis: FontFitAnalysis,
) -> FontDefinition:
    """Return the selected concrete font or a sanitized exact fit failure."""
    font_id = analysis.resolved_auto_font or analysis.selected_explicit_font
    if font_id is None:
        category = (
            "unsupported_glyph"
            if analysis.no_font_supports_content
            else "content_too_tall"
        )
        if not analysis.no_font_supports_content and any(
            item.complete_glyph_coverage and item.fits_height
            for item in analysis.candidates
        ):
            category = "content_too_wide"
        raise FontTextError(category, field="font")
    candidate = analysis.candidate(font_id)
    if not candidate.overall_fit:
        category = {
            "unsupported_by_widget": "unsupported_glyph",
            "unsupported_characters": "unsupported_glyph",
            "too_tall": "content_too_tall",
            "too_wide": "content_too_wide",
        }.get(candidate.rejection_category, "renderer_invariant")
        raise FontTextError(category, field="font", font_id=font_id)
    return registry.resolve(font_id)
