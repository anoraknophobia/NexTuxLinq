"""Behavioral tests for the inactive Phase 3A bitmap-font foundation."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

import pytest

from nextuxlinq.rendering import FONT_3X7, FONT_5X7
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
    FontDefinition,
    FontRegistry,
    FontTextError,
    GlyphBitmap,
)
from nextuxlinq.rendering_v2.fonts.text import (
    measure_normalized_text,
    measure_text,
    normalize_text,
    resolve_font,
)
from nextuxlinq.widget_models import FontChoice

REQUIRED_3X5 = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-_:!?/"
)

EXPECTED_3X5 = {
    " ": ("000", "000", "000", "000", "000"),
    "!": ("1", "1", "1", "0", "1"),
    "-": ("000", "000", "111", "000", "000"),
    ".": ("0", "0", "0", "0", "1"),
    "/": ("001", "001", "010", "100", "100"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "010", "100", "111"),
    "3": ("110", "001", "010", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "110"),
    ":": ("0", "1", "0", "1", "0"),
    "?": ("110", "001", "010", "000", "010"),
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "111", "011"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "_": ("000", "000", "000", "000", "111"),
}


def test_glyph_copies_rows_and_exposes_only_immutable_data() -> None:
    source = [[1, 0], [0, 1]]
    glyph = GlyphBitmap(source)
    source[0][0] = 0

    assert glyph.rows == ((1, 0), (0, 1))
    assert glyph.width == 2
    assert glyph.height == 2
    assert glyph.to_strings() == ("10", "01")
    with pytest.raises((AttributeError, TypeError)):
        glyph.rows[0][0] = 0  # type: ignore[index]


@pytest.mark.parametrize(
    "rows",
    [
        (),
        ((),),
        ((1, 0), (1,)),
        ((1, 2),),
        ((True, 0),),
        ((1.0, 0),),
        (("1", 0),),
    ],
)
def test_glyph_rejects_invalid_geometry_and_pixels(rows: object) -> None:
    with pytest.raises(FontTextError) as raised:
        GlyphBitmap(rows)  # type: ignore[arg-type]
    assert raised.value.category == "invalid_glyph"


@pytest.mark.parametrize("rows", [(), ("",), ("10x",), "10"])
def test_string_glyph_factory_rejects_invalid_rows(rows: object) -> None:
    with pytest.raises(FontTextError):
        GlyphBitmap.from_strings(rows)  # type: ignore[arg-type]


def test_font_definition_is_deeply_immutable_and_variable_width() -> None:
    source = {
        "A": GlyphBitmap.from_strings(("11", "10")),
        ".": GlyphBitmap.from_strings(("0", "1")),
    }
    definition = FontDefinition(
        font_id=FontChoice.COMPACT_3X5,
        display_label="Test",
        glyphs=source,
        character_spacing=1,
    )
    source.clear()

    assert tuple(definition.glyphs) == ("A", ".")
    assert definition.glyphs["A"].width == 2
    assert definition.glyphs["."].width == 1
    assert definition.supported_characters == frozenset({"A", "."})
    with pytest.raises(TypeError):
        definition.glyphs["B"] = GlyphBitmap(((1,),))  # type: ignore[index]


@pytest.mark.parametrize(
    "changes",
    [
        {"font_id": FontChoice.AUTO},
        {"display_label": ""},
        {"glyphs": {}},
        {"glyphs": {"AB": GlyphBitmap(((1,),))}},
        {
            "glyphs": (
                ("A", GlyphBitmap(((1,),))),
                ("A", GlyphBitmap(((1,),))),
            )
        },
        {
            "glyphs": {
                "A": GlyphBitmap(((1,),)),
                "B": GlyphBitmap(((1,), (1,))),
            }
        },
        {"character_spacing": -1},
        {"character_spacing": True},
        {"character_spacing": 1.0},
    ],
)
def test_font_definition_rejects_invalid_contract(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "font_id": FontChoice.COMPACT_3X5,
        "display_label": "Test",
        "glyphs": {"A": GlyphBitmap(((1,),))},
        "character_spacing": 1,
    }
    values.update(changes)
    with pytest.raises(FontTextError):
        FontDefinition(**values)  # type: ignore[arg-type]


def test_registry_rejects_duplicate_and_auto_fonts() -> None:
    definition = FontDefinition(
        font_id=FontChoice.COMPACT_3X5,
        display_label="Test",
        glyphs={"A": GlyphBitmap(((1,),))},
        character_spacing=0,
    )
    with pytest.raises(FontTextError) as duplicate:
        FontRegistry((definition, definition))
    assert duplicate.value.category == "duplicate_font"

    object.__setattr__(definition, "font_id", FontChoice.AUTO)
    with pytest.raises(FontTextError):
        FontRegistry((definition,))


def test_builtin_registry_is_fresh_deterministic_and_immutable() -> None:
    first = create_builtin_font_registry()
    second = create_builtin_font_registry()

    expected = (
        FontChoice.LEGACY_3X7,
        FontChoice.LEGACY_5X7,
        FontChoice.COMPACT_3X5,
        FontChoice.NATIVE_4X9,
        FontChoice.NATIVE_5X9,
        FontChoice.NATIVE_5X10,
    )
    assert tuple(item.font_id for item in first.definitions) == expected
    assert first.definitions == second.definitions
    assert first is not second
    assert first.definitions is not second.definitions
    with pytest.raises(AttributeError):
        first.definitions.append(first.definitions[0])  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        first._definitions = ()  # type: ignore[misc]


def test_registry_reports_unknown_font_without_mutable_views() -> None:
    registry = FontRegistry(())
    with pytest.raises(FontTextError) as raised:
        registry.resolve(FontChoice.LEGACY_5X7)
    assert raised.value.category == "unknown_font"
    assert "object at" not in str(raised.value)


def test_legacy_3x7_is_exactly_equivalent_to_active_renderer() -> None:
    definition = create_builtin_font_registry().resolve(
        FontChoice.LEGACY_3X7
    )

    assert LEGACY_3X7_ROWS == FONT_3X7
    assert {
        character: glyph.to_strings()
        for character, glyph in definition.glyphs.items()
    } == dict(FONT_3X7)
    assert definition.character_spacing == 1


def test_legacy_5x7_is_exactly_equivalent_to_active_renderer() -> None:
    definition = create_builtin_font_registry().resolve(
        FontChoice.LEGACY_5X7
    )

    assert LEGACY_5X7_ROWS == FONT_5X7
    assert {
        character: glyph.to_strings()
        for character, glyph in definition.glyphs.items()
    } == dict(FONT_5X7)
    assert {
        character: glyph.width
        for character, glyph in definition.glyphs.items()
    } == {
        character: len(rows[0])
        for character, rows in FONT_5X7.items()
    }
    assert definition.character_spacing == 1


def test_compact_3x5_has_complete_exact_checked_in_fixture() -> None:
    definition = create_builtin_font_registry().resolve(
        FontChoice.COMPACT_3X5
    )

    assert dict(COMPACT_3X5_ROWS) == EXPECTED_3X5
    assert definition.supported_characters == REQUIRED_3X5
    assert {
        character: glyph.to_strings()
        for character, glyph in definition.glyphs.items()
    } == EXPECTED_3X5
    assert all(glyph.height == 5 for glyph in definition.glyphs.values())
    assert all(
        len(row) == glyph.width
        for glyph in definition.glyphs.values()
        for row in glyph.rows
    )
    assert not (set("abcdefghijklmnopqrstuvwxyz") & set(definition.glyphs))
    assert definition.glyphs["!"].width == 1
    assert definition.glyphs["."].width == 1
    assert definition.glyphs[":"].width == 1
    assert definition.glyphs["A"].width == 3


@pytest.mark.parametrize(
    ("font_id", "rows", "width", "height", "fixture_hash"),
    [
        (
            FontChoice.NATIVE_4X9,
            NATIVE_4X9_ROWS,
            4,
            9,
            "6676524eed29938ce199d8165dd8ffb1f8fe6a5f96ad0d2e83738ae9b7455c1e",
        ),
        (
            FontChoice.NATIVE_5X9,
            NATIVE_5X9_ROWS,
            5,
            9,
            "70449262b1a2ee5202bbf96a0d1c451a64aa34f62073ac0e411856a6f0f57700",
        ),
        (
            FontChoice.NATIVE_5X10,
            NATIVE_5X10_ROWS,
            5,
            10,
            "15d9dddd2a6d849959c895f2ebb6880fadbf2f187a745dfead30fe32fa24631d",
        ),
    ],
)
def test_larger_native_fonts_are_exact_complete_immutable_fixtures(
    font_id: FontChoice,
    rows: MappingProxyType,
    width: int,
    height: int,
    fixture_hash: str,
) -> None:
    required = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-_:!?/%")
    definition = create_builtin_font_registry().resolve(font_id)
    encoded = json.dumps(
        dict(rows), sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    assert hashlib.sha256(encoded).hexdigest() == fixture_hash
    assert definition.supported_characters == required
    assert all(glyph.height == height for glyph in definition.glyphs.values())
    assert max(glyph.width for glyph in definition.glyphs.values()) == width
    assert {
        character: glyph.to_strings()
        for character, glyph in definition.glyphs.items()
    } == dict(rows)
    assert isinstance(rows, MappingProxyType)
    assert not (set("abcdefghijklmnopqrstuvwxyz") & set(rows))


def test_auto_font_priority_is_explicit_and_largest_first() -> None:
    assert AUTO_FONT_PRIORITY == (
        FontChoice.NATIVE_5X10,
        FontChoice.NATIVE_5X9,
        FontChoice.NATIVE_4X9,
        FontChoice.LEGACY_5X7,
        FontChoice.LEGACY_3X7,
        FontChoice.COMPACT_3X5,
    )


def test_ascii_only_normalization_does_not_expand_unicode() -> None:
    source = "abc ABC ß ﬁ ı"

    assert normalize_text(source, uppercase=True) == "ABC ABC ß ﬁ ı"
    assert normalize_text(source, uppercase=False) == source
    assert source == "abc ABC ß ﬁ ı"


@pytest.mark.parametrize("uppercase", [0, 1, "true", None])
def test_normalization_requires_strict_boolean(uppercase: object) -> None:
    with pytest.raises(FontTextError):
        normalize_text("abc", uppercase=uppercase)  # type: ignore[arg-type]


def test_unsupported_lowercase_and_unicode_are_not_replaced() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)
    for value in ("a", "Ä", "ß"):
        with pytest.raises(FontTextError) as raised:
            measure_text(font, value, uppercase=False)
        assert raised.value.category == "unsupported_glyph"


def test_measurement_uses_spacing_only_between_variable_width_glyphs() -> None:
    font = create_builtin_font_registry().resolve(FontChoice.COMPACT_3X5)

    assert measure_normalized_text(font, "").width == 0
    assert measure_normalized_text(font, "!").width == 1
    assert measure_normalized_text(font, "A").width == 3
    assert measure_normalized_text(font, "A!").width == 5
    assert measure_normalized_text(font, "!A.").width == 7
    assert measure_text(font, "a!", uppercase=True).text == "A!"
    assert measure_text(font, "A!", uppercase=True).height == 5
    assert measure_text(font, "A!", uppercase=True) == measure_text(
        font, "A!", uppercase=True
    )


@pytest.mark.parametrize(
    "choice,text,width,height,expected",
    [
        (FontChoice.LEGACY_5X7, "A", 5, 7, FontChoice.LEGACY_5X7),
        (FontChoice.LEGACY_3X7, "12:34", 19, 7, FontChoice.LEGACY_3X7),
        (FontChoice.COMPACT_3X5, "A", 3, 5, FontChoice.COMPACT_3X5),
        (FontChoice.AUTO, "A", 5, 7, FontChoice.LEGACY_5X7),
        (FontChoice.AUTO, "12:34", 19, 7, FontChoice.LEGACY_3X7),
        (FontChoice.AUTO, "A", 3, 5, FontChoice.COMPACT_3X5),
    ],
)
def test_explicit_and_automatic_font_resolution(
    choice: FontChoice,
    text: str,
    width: int,
    height: int,
    expected: FontChoice,
) -> None:
    resolved = resolve_font(
        create_builtin_font_registry(),
        choice,
        text,
        uppercase=True,
        region_width=width,
        region_height=height,
        require_complete_width=True,
    )
    assert resolved.font.font_id is expected


def test_auto_resolution_may_ignore_width_for_clipping_callers() -> None:
    resolved = resolve_font(
        create_builtin_font_registry(),
        FontChoice.AUTO,
        "WIDE",
        uppercase=True,
        region_width=1,
        region_height=7,
        require_complete_width=False,
    )
    assert resolved.font.font_id is FontChoice.LEGACY_5X7
    assert resolved.measurement.width > 1


@pytest.mark.parametrize(
    ("content", "width", "height", "expected"),
    [
        ("A", 5, 10, FontChoice.NATIVE_5X10),
        ("12:34", 25, 10, FontChoice.NATIVE_5X10),
        ("12:34", 24, 10, FontChoice.NATIVE_4X9),
        ("A", 5, 9, FontChoice.NATIVE_5X9),
        ("A", 4, 9, FontChoice.NATIVE_4X9),
    ],
)
def test_auto_resolution_selects_largest_fitting_native_font(
    content: str,
    width: int,
    height: int,
    expected: FontChoice,
) -> None:
    resolved = resolve_font(
        create_builtin_font_registry(),
        FontChoice.AUTO,
        content,
        uppercase=True,
        region_width=width,
        region_height=height,
        require_complete_width=True,
    )
    assert resolved.font.font_id is expected


@pytest.mark.parametrize(
    "choice,text,width,height,category",
    [
        (FontChoice.LEGACY_3X7, "A", 10, 7, "unsupported_glyph"),
        (FontChoice.LEGACY_5X7, "A", 5, 5, "content_too_tall"),
        (FontChoice.COMPACT_3X5, "AA", 3, 5, "content_too_wide"),
        (FontChoice.AUTO, "A", 3, 4, "content_too_tall"),
        (FontChoice.AUTO, "Ä", 20, 10, "unsupported_glyph"),
        (FontChoice.AUTO, "AA", 1, 10, "content_too_wide"),
    ],
)
def test_resolution_reports_controlled_no_fit_errors(
    choice: FontChoice,
    text: str,
    width: int,
    height: int,
    category: str,
) -> None:
    with pytest.raises(FontTextError) as raised:
        resolve_font(
            create_builtin_font_registry(),
            choice,
            text,
            uppercase=False,
            region_width=width,
            region_height=height,
            require_complete_width=True,
        )
    assert raised.value.category == category
    assert "Traceback" not in str(raised.value)


@pytest.mark.parametrize(
    "width,height",
    [(True, 5), (3, False), (3.0, 5), (3, "5"), (0, 5), (3, 0)],
)
def test_font_resolution_requires_strict_positive_region_dimensions(
    width: object, height: object
) -> None:
    with pytest.raises(FontTextError):
        resolve_font(
            create_builtin_font_registry(),
            FontChoice.AUTO,
            "A",
            uppercase=True,
            region_width=width,  # type: ignore[arg-type]
            region_height=height,  # type: ignore[arg-type]
            require_complete_width=True,
        )


def test_builtin_row_fixture_views_are_read_only() -> None:
    assert isinstance(LEGACY_3X7_ROWS, MappingProxyType)
    assert isinstance(LEGACY_5X7_ROWS, MappingProxyType)
    assert isinstance(COMPACT_3X5_ROWS, MappingProxyType)
    assert isinstance(NATIVE_4X9_ROWS, MappingProxyType)
    assert isinstance(NATIVE_5X9_ROWS, MappingProxyType)
    assert isinstance(NATIVE_5X10_ROWS, MappingProxyType)
    with pytest.raises(TypeError):
        COMPACT_3X5_ROWS["a"] = ("1",) * 5  # type: ignore[index]
