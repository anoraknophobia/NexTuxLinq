"""Immutable font-domain types for the inactive schema-v2 renderer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from nextuxlinq.widget_models import FontChoice


class FontTextError(ValueError):
    """A categorized, sanitized font or text-rendering failure."""

    _MESSAGES = {
        "invalid_glyph": "The font contains invalid glyph data.",
        "duplicate_font": "The font registry contains a duplicate font.",
        "unknown_font": "The requested font is not available.",
        "unsupported_glyph": "The text contains a character unsupported by the font.",
        "content_too_tall": "The selected text is too tall for the region.",
        "content_too_wide": "The selected text is too wide for the region.",
        "invalid_alignment": "The requested text alignment is invalid.",
        "renderer_invariant": "The text renderer could not produce a valid result.",
    }

    def __init__(
        self,
        category: str,
        *,
        field: str = "font",
        font_id: FontChoice | None = None,
    ) -> None:
        if category not in self._MESSAGES:
            category = "renderer_invariant"
        self.category = category
        self.field = field
        self.font_id = font_id
        message = self._MESSAGES[category]
        if font_id is not None:
            message = f"{message} Font: {font_id.value}."
        super().__init__(message)


def _strict_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FontTextError("invalid_glyph", field=field)
    return value


@dataclass(frozen=True, slots=True, init=False)
class GlyphBitmap:
    """One immutable, rectangular binary glyph bitmap."""

    rows: tuple[tuple[int, ...], ...]

    def __init__(self, rows: Sequence[Sequence[int]]) -> None:
        try:
            immutable_rows = tuple(tuple(row) for row in rows)
        except TypeError as error:
            raise FontTextError("invalid_glyph", field="glyph") from error
        if not immutable_rows or not immutable_rows[0]:
            raise FontTextError("invalid_glyph", field="glyph")
        width = len(immutable_rows[0])
        if any(len(row) != width for row in immutable_rows):
            raise FontTextError("invalid_glyph", field="glyph")
        for row in immutable_rows:
            for pixel in row:
                value = _strict_integer(pixel, field="glyph")
                if value not in (0, 1):
                    raise FontTextError("invalid_glyph", field="glyph")
        object.__setattr__(self, "rows", immutable_rows)

    @classmethod
    def from_strings(cls, rows: Sequence[str]) -> GlyphBitmap:
        """Build a glyph from explicit checked-in ``0``/``1`` rows."""
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise FontTextError("invalid_glyph", field="glyph")
        converted: list[tuple[int, ...]] = []
        for row in rows:
            if not isinstance(row, str) or not row or set(row) - {"0", "1"}:
                raise FontTextError("invalid_glyph", field="glyph")
            converted.append(tuple(int(pixel) for pixel in row))
        return cls(converted)

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)

    def to_strings(self) -> tuple[str, ...]:
        """Return a deterministic immutable fixture representation."""
        return tuple("".join(str(pixel) for pixel in row) for row in self.rows)


@dataclass(frozen=True, slots=True, init=False)
class FontDefinition:
    """One immutable concrete bitmap font."""

    font_id: FontChoice
    display_label: str
    glyphs: Mapping[str, GlyphBitmap]
    character_spacing: int
    glyph_height: int
    supported_characters: frozenset[str]

    def __init__(
        self,
        *,
        font_id: FontChoice,
        display_label: str,
        glyphs: Mapping[str, GlyphBitmap]
        | Iterable[tuple[str, GlyphBitmap]],
        character_spacing: int,
    ) -> None:
        if not isinstance(font_id, FontChoice) or font_id is FontChoice.AUTO:
            raise FontTextError("invalid_glyph", field="font_id")
        if not isinstance(display_label, str) or not display_label.strip():
            raise FontTextError("invalid_glyph", field="display_label")
        spacing = _strict_integer(
            character_spacing, field="character_spacing"
        )
        if spacing < 0:
            raise FontTextError("invalid_glyph", field="character_spacing")

        items = tuple(glyphs.items()) if isinstance(glyphs, Mapping) else tuple(glyphs)
        copied: dict[str, GlyphBitmap] = {}
        for key, glyph in items:
            if not isinstance(key, str) or len(key) != 1:
                raise FontTextError("invalid_glyph", field="glyph_key")
            if key in copied:
                raise FontTextError("invalid_glyph", field="glyph_key")
            if type(glyph) is not GlyphBitmap:
                raise FontTextError("invalid_glyph", field="glyph")
            copied[key] = glyph
        if not copied:
            raise FontTextError("invalid_glyph", field="glyphs")
        heights = {glyph.height for glyph in copied.values()}
        if len(heights) != 1:
            raise FontTextError("invalid_glyph", field="glyph_height")

        object.__setattr__(self, "font_id", font_id)
        object.__setattr__(self, "display_label", display_label)
        object.__setattr__(self, "glyphs", MappingProxyType(copied))
        object.__setattr__(self, "character_spacing", spacing)
        object.__setattr__(self, "glyph_height", next(iter(heights)))
        object.__setattr__(
            self, "supported_characters", frozenset(copied)
        )


@dataclass(frozen=True, slots=True, init=False)
class FontRegistry:
    """An effectively immutable deterministic collection of concrete fonts."""

    _definitions: tuple[FontDefinition, ...]
    _lookup: Mapping[FontChoice, FontDefinition]

    def __init__(self, definitions: Iterable[FontDefinition]) -> None:
        ordered = tuple(definitions)
        lookup: dict[FontChoice, FontDefinition] = {}
        for definition in ordered:
            if type(definition) is not FontDefinition:
                raise FontTextError("invalid_glyph", field="font")
            if definition.font_id is FontChoice.AUTO:
                raise FontTextError("invalid_glyph", field="font_id")
            if definition.font_id in lookup:
                raise FontTextError(
                    "duplicate_font", font_id=definition.font_id
                )
            lookup[definition.font_id] = definition
        object.__setattr__(self, "_definitions", ordered)
        object.__setattr__(self, "_lookup", MappingProxyType(lookup))

    @property
    def definitions(self) -> tuple[FontDefinition, ...]:
        return self._definitions

    def resolve(self, font_id: FontChoice) -> FontDefinition:
        if not isinstance(font_id, FontChoice) or font_id is FontChoice.AUTO:
            raise FontTextError(
                "unknown_font",
                font_id=font_id if isinstance(font_id, FontChoice) else None,
            )
        try:
            return self._lookup[font_id]
        except KeyError as error:
            raise FontTextError("unknown_font", font_id=font_id) from error


@dataclass(frozen=True, slots=True)
class MeasuredText:
    """Deterministic integer dimensions for one concrete-font string."""

    text: str
    font_id: FontChoice
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ResolvedFont:
    """A concrete font selected for normalized text and region geometry."""

    font: FontDefinition
    normalized_text: str
    measurement: MeasuredText


@dataclass(frozen=True, slots=True)
class DrawTextResult:
    """Immutable summary of one local text-drawing operation."""

    normalized_text: str
    rendered_text: str
    font_id: FontChoice
    measured_width: int
    measured_height: int
    origin_x: int
    origin_y: int
    truncated: bool
