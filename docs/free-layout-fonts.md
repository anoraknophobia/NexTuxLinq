# Free-layout font foundation

NexTuxLinq uses checked-in native bitmap fonts for Matrix900 free-layout
widgets. The editor exposes Auto, 3×5, 3×7, 5×7, 4×9, 5×9, and 5×10. These
are pixel fixtures, not resized versions of another font.

## Fonts

The font registry factory creates six concrete fonts:

- `3x5`: a compact five-row general font for regions such as either row of a
  two-row, five-pixel-high layout;
- `3x7`: the exact legacy-compatible compact clock font;
- `5x7`: the exact legacy-compatible general text font;
- `4x9`: a narrow nine-row general font;
- `5x9`: a full-width nine-row general font;
- `5x10`: a full-height ten-row general font.

`auto` is a selection request, not a concrete font definition. Automatic
selection means **largest fitting native font**. Its explicit order is 5×10,
5×9, 4×9, 5×7, 3×7, then 3×5. A candidate must cover every required glyph,
fit the region height, and satisfy the widget's real width and overflow rules.
Equal-height choices therefore prefer 5×9 over 4×9 only when 5×9 fits. Glyphs
are never scaled, stretched, antialiased, or simulated by the browser.
Auto tries every compatible candidate in that order before failing; a rejected
larger font does not make the region invalid when a smaller native font fits.
An explicit font is evaluated by the same rules but is never replaced or
persisted as a different font automatically.

The `3x7` font contains only digits and the colon, matching the active legacy
clock font. The `5x7` font preserves every character, variable glyph width,
bitmap row, and one-pixel spacing rule from the active renderer. The checked-in
general fonts cover uppercase `A` through `Z`, digits, space, period, hyphen,
underscore, colon, exclamation mark, question mark, slash, and percent sign
where applicable. They intentionally contain no lowercase or replacement
glyph. The old 3×7 and 5×7 fixtures remain byte-identical.

A 24-hour Clock without seconds measures 25 pixels in 5×10, including the
font's declared colon and one-pixel inter-character spacing. It therefore
resolves to 5×10 in a 25×10 region; a 24×10 region resolves to the largest
smaller font that actually fits. Seconds require 39 pixels in 5×10. These
dimensions come from glyph metrics rather than special-case Clock constants.

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

Static Text with Clip or Truncate chooses the largest height-fitting font and
does not shrink just to avoid horizontal overflow. Scrolling Text likewise
ignores region width during font selection. Clock and Date use their configured
required representation. System Metric uses its snapshot-independent format
envelope and fixed numeric slots, so changing a sampled value never changes the
resolved font. This includes one-, two-, and three-digit percentages and
available/unavailable transitions; an actual `100%` is displayed as `99%`
because percentages are capped, and other three-digit values retain the chosen
font and use the configured Error, Truncate, or Clip behavior.

Unset glyph pixels preserve the surface's existing background. The text layer
does not fill backgrounds, scale glyphs, rotate content, apply device
orientation, or implement animation.

## Widget use and deferred work

Clock, Static Text, Scrolling Text, Date, and System Metric in the
[widget registry](free-layout-widgets.md) use this foundation without scaling
or stretching. Blank does not use fonts. Two independent five-pixel rows can
each select `3x5` while remaining clipped to their own regions. Full
migration-equivalence tests confirm exact legacy 3×7 and 5×7 glyphs, spacing,
alignment, colors, and scrolling framebuffers.

The service exposes the canonical choices, each concrete option's current
availability, and the resolved Auto font through the local metadata and
validation APIs. The visual editor shows the resolved font beside each Font
dropdown and retains explicit saved choices while a draft is invalid. When an
explicit font fails, the inspector can recommend a smaller fitting native font
or a safe required size. When Auto has no fitting candidate, the editor says
that no available font fits and reports the smallest compatible font's required
geometry where known. Unsupported characters are reported separately because
resizing cannot add missing glyphs.
Migration-equivalence tests preserve schema-v1 framebuffer behavior, and
physical Matrix900 validation remains a separate operational step.
