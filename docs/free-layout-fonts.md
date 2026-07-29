# Free-layout font foundation

Phase 3A implements an inactive bitmap-font and local text-drawing foundation
for future NexTuxLinq Matrix900 free-layout widgets. It is not connected to the
daemon, API, GUI, configuration persistence, active schema-v1 renderer, or
physical output.

## Fonts

The inactive registry factory creates three concrete fonts:

- `3x5`: a compact five-row general font for regions such as either row of a
  two-row, five-pixel-high layout;
- `3x7`: the exact legacy-compatible compact clock font;
- `5x7`: the exact legacy-compatible general text font.

`auto` is a selection request, not a concrete font definition. Automatic
selection tries `5x7`, then `3x7`, then `3x5`, accepting the first font whose
height, character coverage, and any requested complete-width constraint fit.
Glyphs are never scaled or stretched.

The `3x7` font contains only digits and the colon, matching the active legacy
clock font. The `5x7` font preserves every character, variable glyph width,
bitmap row, and one-pixel spacing rule from the active renderer. The checked-in
`3x5` font contains uppercase `A` through `Z`, digits, space, period, hyphen,
underscore, colon, exclamation mark, question mark, and slash. It intentionally
contains no lowercase or replacement glyph.

## Normalization and measurement

Uppercase conversion is ASCII-only: `a` through `z` become `A` through `Z`.
Other Unicode characters are neither uppercased nor transliterated. When
uppercase conversion is disabled, input is preserved exactly. A character
without a font glyph produces a controlled error; it is not silently replaced.

Text width is the sum of declared glyph widths plus the font's one-pixel
spacing only between glyphs. There is no trailing character spacing. Empty
text has width zero. All dimensions and positions are integers.

## Alignment and overflow

Horizontal alignment is `start`, `center`, or `end`; vertical alignment is
`top`, `center`, or `bottom`. Centering uses:

```text
(available size - content size) // 2
```

Odd spare pixels therefore remain on the end or bottom. With centered
overflow, Python floor division clips one additional pixel from the start or
top. Drawing remains local to a `RegionSurface`, and its existing clipping
guarantees prevent text from changing another region.

The text primitive supports three non-animated overflow modes:

- `error` requires the complete measured width to fit;
- `truncate` draws the largest complete prefix that fits, never a partial
  glyph, and aligns that prefix;
- `clip` aligns the complete text and permits partial edge glyphs through
  region-local clipping.

Unset glyph pixels preserve the surface's existing background. The text layer
does not fill backgrounds, scale glyphs, rotate content, apply device
orientation, or implement animation.

## Widget use and deferred work

Clock, Static Text, Scrolling Text, and Date in the inactive
[widget registry](free-layout-widgets.md) use this foundation without scaling
or stretching. Blank does not use fonts. Two independent five-pixel rows can
each select `3x5` while remaining clipped to their own regions. Full
migration-equivalence tests confirm exact legacy 3×7 and 5×7 glyphs, spacing,
alignment, colors, and scrolling framebuffers.

Update scheduling, API and GUI exposure, persistence cutover, daemon
activation, and physical Matrix900 output remain future phases. The current
web GUI cannot configure free-layout widgets, and active schema-v1 framebuffer
behavior remains unchanged.
