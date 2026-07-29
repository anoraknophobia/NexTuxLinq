"""Inactive bitmap-font foundation for future schema-v2 widgets.

No font registry is constructed at import time. Call
``create_builtin_font_registry`` explicitly when using this inactive layer.
"""

from nextuxlinq.rendering_v2.fonts.builtin import (
    COMPACT_3X5_ROWS,
    LEGACY_3X7_ROWS,
    LEGACY_5X7_ROWS,
    create_builtin_font_registry,
)
from nextuxlinq.rendering_v2.fonts.models import (
    DrawTextResult,
    FontDefinition,
    FontRegistry,
    FontTextError,
    GlyphBitmap,
    MeasuredText,
    ResolvedFont,
)
from nextuxlinq.rendering_v2.fonts.text import (
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
    "COMPACT_3X5_ROWS",
    "LEGACY_3X7_ROWS",
    "LEGACY_5X7_ROWS",
    "DrawTextResult",
    "FontDefinition",
    "FontRegistry",
    "FontTextError",
    "GlyphBitmap",
    "MeasuredText",
    "ResolvedFont",
    "create_builtin_font_registry",
    "draw_glyph",
    "draw_text",
    "horizontal_origin",
    "measure_normalized_text",
    "measure_text",
    "normalize_text",
    "resolve_font",
    "validate_text",
    "vertical_origin",
)
