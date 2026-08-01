"""Inactive bitmap-font foundation for future schema-v2 widgets.

No font registry is constructed at import time. Call
``create_builtin_font_registry`` explicitly when using this inactive layer.
"""

from nextuxlinq.rendering_v2.fonts.builtin import (
    AUTO_FONT_PRIORITY,
    COMPACT_3X5_ROWS,
    LEGACY_3X7_ROWS,
    LEGACY_5X7_ROWS,
    NATIVE_4X9_ROWS,
    NATIVE_5X9_ROWS,
    NATIVE_5X10_ROWS,
    create_builtin_font_registry,
)
from nextuxlinq.rendering_v2.fonts.models import (
    DrawTextResult,
    FontCandidateFit,
    FontDefinition,
    FontFitAnalysis,
    FontFitRequirement,
    FontRegistry,
    FontTextError,
    GlyphBitmap,
    MeasuredText,
    ResolvedFont,
)
from nextuxlinq.rendering_v2.fonts.fit import (
    analyze_font_fit,
    resolve_analyzed_font,
)
from nextuxlinq.rendering_v2.fonts.text import (
    analyze_text_font_fit,
    draw_glyph,
    draw_text,
    horizontal_origin,
    measure_normalized_text,
    measure_text,
    normalize_text,
    resolve_font,
    validate_text,
    vertical_origin,
)

__all__ = (
    "AUTO_FONT_PRIORITY",
    "COMPACT_3X5_ROWS",
    "LEGACY_3X7_ROWS",
    "LEGACY_5X7_ROWS",
    "NATIVE_4X9_ROWS",
    "NATIVE_5X9_ROWS",
    "NATIVE_5X10_ROWS",
    "DrawTextResult",
    "FontCandidateFit",
    "FontDefinition",
    "FontFitAnalysis",
    "FontFitRequirement",
    "FontRegistry",
    "FontTextError",
    "GlyphBitmap",
    "MeasuredText",
    "ResolvedFont",
    "create_builtin_font_registry",
    "analyze_font_fit",
    "analyze_text_font_fit",
    "draw_glyph",
    "draw_text",
    "horizontal_origin",
    "measure_normalized_text",
    "measure_text",
    "normalize_text",
    "resolve_font",
    "resolve_analyzed_font",
    "validate_text",
    "vertical_origin",
)
