"""Deterministic text measurement and RegionSurface drawing primitives."""

from __future__ import annotations

from nextuxlinq.rendering_v2.fonts.builtin import AUTO_FONT_PRIORITY
from nextuxlinq.rendering_v2.fonts.models import (
    DrawTextResult,
    FontDefinition,
    FontRegistry,
    FontTextError,
    MeasuredText,
    ResolvedFont,
)
from nextuxlinq.rendering_v2.surface import RegionSurface, RgbColor
from nextuxlinq.widget_models import (
    FontChoice,
    HorizontalAlignment,
    TextOverflow,
    VerticalAlignment,
)

_ASCII_UPPERCASE = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


def _strict_dimension(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FontTextError("renderer_invariant", field=field)
    return value


def normalize_text(content: str, *, uppercase: bool) -> str:
    """Apply ASCII-only uppercase normalization without transliteration."""
    if not isinstance(content, str):
        raise FontTextError("renderer_invariant", field="content")
    if not isinstance(uppercase, bool):
        raise FontTextError("renderer_invariant", field="uppercase")
    return content.translate(_ASCII_UPPERCASE) if uppercase else content


def validate_text(font: FontDefinition, text: str) -> str:
    """Require complete glyph coverage for already-normalized text."""
    if type(font) is not FontDefinition or not isinstance(text, str):
        raise FontTextError("renderer_invariant", field="text")
    if any(character not in font.glyphs for character in text):
        raise FontTextError(
            "unsupported_glyph", field="content", font_id=font.font_id
        )
    return text


def measure_normalized_text(
    font: FontDefinition, text: str
) -> MeasuredText:
    """Measure validated text without trailing character spacing."""
    validate_text(font, text)
    width = sum(font.glyphs[character].width for character in text)
    if text:
        width += font.character_spacing * (len(text) - 1)
    return MeasuredText(
        text=text,
        font_id=font.font_id,
        width=width,
        height=font.glyph_height,
    )


def measure_text(
    font: FontDefinition, content: str, *, uppercase: bool
) -> MeasuredText:
    """Normalize, validate, and measure text with one concrete font."""
    return measure_normalized_text(
        font, normalize_text(content, uppercase=uppercase)
    )


def _validate_region_size(width: object, height: object) -> tuple[int, int]:
    normalized_width = _strict_dimension(width, field="region_width")
    normalized_height = _strict_dimension(height, field="region_height")
    if normalized_width <= 0 or normalized_height <= 0:
        raise FontTextError("renderer_invariant", field="region")
    return normalized_width, normalized_height


def resolve_font(
    registry: FontRegistry,
    choice: FontChoice,
    content: str,
    *,
    uppercase: bool,
    region_width: int,
    region_height: int,
    require_complete_width: bool,
) -> ResolvedFont:
    """Resolve explicit or automatic font selection deterministically."""
    if not isinstance(registry, FontRegistry):
        raise FontTextError("renderer_invariant", field="font_registry")
    if not isinstance(choice, FontChoice):
        raise FontTextError("unknown_font", field="font")
    if not isinstance(require_complete_width, bool):
        raise FontTextError("renderer_invariant", field="require_complete_width")
    width, height = _validate_region_size(region_width, region_height)
    normalized = normalize_text(content, uppercase=uppercase)

    if choice is not FontChoice.AUTO:
        font = registry.resolve(choice)
        measurement = measure_normalized_text(font, normalized)
        if font.glyph_height > height:
            raise FontTextError(
                "content_too_tall", field="font", font_id=font.font_id
            )
        if require_complete_width and measurement.width > width:
            raise FontTextError(
                "content_too_wide", field="content", font_id=font.font_id
            )
        return ResolvedFont(font, normalized, measurement)

    supported_but_tall = False
    supported_and_vertical = False
    for font_id in AUTO_FONT_PRIORITY:
        try:
            font = registry.resolve(font_id)
        except FontTextError:
            continue
        if any(character not in font.glyphs for character in normalized):
            continue
        supported_but_tall = True
        if font.glyph_height > height:
            continue
        supported_and_vertical = True
        measurement = measure_normalized_text(font, normalized)
        if require_complete_width and measurement.width > width:
            continue
        return ResolvedFont(font, normalized, measurement)

    if supported_and_vertical and require_complete_width:
        category = "content_too_wide"
    elif supported_but_tall:
        category = "content_too_tall"
    else:
        category = "unsupported_glyph"
    raise FontTextError(category, field="font")


def horizontal_origin(
    available_size: int,
    content_size: int,
    alignment: HorizontalAlignment,
) -> int:
    """Return a deterministic local x origin for one alignment."""
    available = _strict_dimension(available_size, field="available_width")
    content = _strict_dimension(content_size, field="content_width")
    if not isinstance(alignment, HorizontalAlignment):
        raise FontTextError("invalid_alignment", field="horizontal_alignment")
    if alignment is HorizontalAlignment.START:
        return 0
    if alignment is HorizontalAlignment.CENTER:
        return (available - content) // 2
    return available - content


def vertical_origin(
    available_size: int,
    content_size: int,
    alignment: VerticalAlignment,
) -> int:
    """Return a deterministic local y origin for one alignment."""
    available = _strict_dimension(available_size, field="available_height")
    content = _strict_dimension(content_size, field="content_height")
    if not isinstance(alignment, VerticalAlignment):
        raise FontTextError("invalid_alignment", field="vertical_alignment")
    if alignment is VerticalAlignment.TOP:
        return 0
    if alignment is VerticalAlignment.CENTER:
        return (available - content) // 2
    return available - content


def draw_glyph(
    surface: RegionSurface,
    glyph,
    *,
    origin_x: int,
    origin_y: int,
    color: RgbColor,
) -> int:
    """Draw enabled glyph pixels through RegionSurface clipping."""
    from nextuxlinq.rendering_v2.fonts.models import GlyphBitmap

    if not isinstance(surface, RegionSurface) or type(glyph) is not GlyphBitmap:
        raise FontTextError("renderer_invariant", field="glyph")
    if isinstance(origin_x, bool) or not isinstance(origin_x, int):
        raise FontTextError("renderer_invariant", field="origin_x")
    if isinstance(origin_y, bool) or not isinstance(origin_y, int):
        raise FontTextError("renderer_invariant", field="origin_y")
    if not isinstance(color, RgbColor):
        raise FontTextError("renderer_invariant", field="color")
    written = 0
    for row_index, row in enumerate(glyph.rows):
        for column_index, pixel in enumerate(row):
            if pixel and surface.set_pixel(
                origin_x + column_index, origin_y + row_index, color
            ):
                written += 1
    return written


def _truncate_to_width(
    font: FontDefinition, text: str, available_width: int
) -> tuple[str, MeasuredText]:
    rendered: list[str] = []
    used_width = 0
    for character in text:
        glyph_width = font.glyphs[character].width
        candidate_width = (
            used_width
            + (font.character_spacing if rendered else 0)
            + glyph_width
        )
        if candidate_width > available_width:
            break
        rendered.append(character)
        used_width = candidate_width
    prefix = "".join(rendered)
    return prefix, MeasuredText(
        prefix, font.font_id, used_width, font.glyph_height
    )


def draw_text(
    surface: RegionSurface,
    content: str,
    *,
    font: FontDefinition,
    color: RgbColor,
    horizontal_alignment: HorizontalAlignment,
    vertical_alignment: VerticalAlignment,
    overflow: TextOverflow,
    uppercase: bool = False,
) -> DrawTextResult:
    """Normalize and draw non-animated text inside one local region surface."""
    if not isinstance(surface, RegionSurface):
        raise FontTextError("renderer_invariant", field="surface")
    if type(font) is not FontDefinition:
        raise FontTextError("renderer_invariant", field="font")
    if not isinstance(color, RgbColor):
        raise FontTextError("renderer_invariant", field="color")
    if not isinstance(overflow, TextOverflow):
        raise FontTextError("renderer_invariant", field="overflow")
    normalized = normalize_text(content, uppercase=uppercase)
    full_measurement = measure_normalized_text(font, normalized)
    if font.glyph_height > surface.height:
        raise FontTextError(
            "content_too_tall", field="font", font_id=font.font_id
        )

    rendered_text = normalized
    measurement = full_measurement
    truncated = False
    if overflow is TextOverflow.ERROR:
        if measurement.width > surface.width:
            raise FontTextError(
                "content_too_wide", field="content", font_id=font.font_id
            )
    elif overflow is TextOverflow.TRUNCATE:
        rendered_text, measurement = _truncate_to_width(
            font, normalized, surface.width
        )
        truncated = rendered_text != normalized

    origin_x = horizontal_origin(
        surface.width, measurement.width, horizontal_alignment
    )
    origin_y = vertical_origin(
        surface.height, measurement.height, vertical_alignment
    )
    cursor_x = origin_x
    for index, character in enumerate(rendered_text):
        draw_glyph(
            surface,
            font.glyphs[character],
            origin_x=cursor_x,
            origin_y=origin_y,
            color=color,
        )
        cursor_x += font.glyphs[character].width
        if index + 1 < len(rendered_text):
            cursor_x += font.character_spacing

    return DrawTextResult(
        normalized_text=normalized,
        rendered_text=rendered_text,
        font_id=font.font_id,
        measured_width=measurement.width,
        measured_height=measurement.height,
        origin_x=origin_x,
        origin_y=origin_y,
        truncated=truncated,
    )
