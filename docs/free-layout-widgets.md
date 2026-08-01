# Free-layout widget renderers

Phase 3B implemented Clock and Date, Phase 3C implemented Static Text and
Scrolling Text, and Phase 3D integrated the original five widget types into one
canonical registry and compositor. Phase 4 activates that registry through
the canonical schema-v2 service. DeviceWorker, Creator reports, and physical
orientation remain unchanged.

The active visual editor provides exactly one widget dropdown per region and
shows only settings belonging to the selected widget.

## System Metric

System Metric formats one immutable render-cycle snapshot as value-only or
label-and-value text. It supports CPU, memory, load, fixed-root storage,
network receive/transmit, generic GPU/VRAM, uptime, and available CPU, GPU, and
storage temperatures. Percentage values use `%`; temperatures use the
font-safe ASCII suffix `C`; network rates automatically use `KB/S`, `MB/S`, or
`GB/S`. Unavailable values use configurable safe text, defaulting to `--`.
Uptime is `MM:SS` below one hour, `HH:MM` below one day, and
`<days>D <hours>H` from one day onward, with a two-digit hour. Precision is
zero to two decimals for numeric values. Existing intrinsic fonts, alignments,
and overflow modes apply, and five-pixel rows can use `3x5`.

Font selection never depends on the current sample. In a ten-pixel-high region,
Auto keeps `5x7` whenever its glyph coverage is valid and never shrinks merely
to fit a wider value. Percentages below ten and non-negative integer
temperatures below ten reserve an invisible leading digit cell with no drawn
zero pixels. Thus one- and two-digit values retain the same label and suffix
positions. Explicit fonts remain authoritative. An Error-overflow candidate
whose stable envelope does not fit fails with `content_too_wide`; Clip and
Truncate retain their configured behavior.

## Metric Bar

Metric Bar supports CPU, memory, fixed-root storage, GPU, and VRAM usage plus
CPU, GPU, and storage temperatures. Load average and network rates remain text
metrics because they have no canonical percentage range. The bar clamps a
numeric value to the configured minimum and maximum and rounds the resulting
fill extent to the nearest integer pixel, with exact halves rounded upward.
Horizontal start-to-end is left to right. Vertical start-to-end is bottom to
top; the reverse direction is top to bottom. The opaque fill and empty colors
are region-local, with no antialiasing, alpha, or fractional pixels. An
unavailable metric is empty. A border is drawn only when both dimensions can
retain a nonempty one-pixel inset.

## Clock

Clock content uses the injected, timezone-aware render-context wall clock. It
does not read the system clock. The formats are:

- 24-hour `HH:MM`, optionally `HH:MM:SS`;
- 12-hour `h:MM`, optionally `h:MM:SS`.

The 24-hour hour is always zero-padded. The 12-hour hour has no leading zero
and has no AM or PM suffix; both midnight and noon use `12`.

Separators are normally steady. When blinking is enabled, every colon is
visible for the first half of each one-second animation cycle and hidden for
the second half. Hidden separators retain their glyph width and advance, so
digits, measurement, and alignment never move.

`local` preserves the injected wall-clock value and its timezone unchanged. A
named IANA timezone converts that value with the standard library timezone
database before formatting. Invalid or unavailable names produce a controlled,
sanitized failure. Timezone conversion is deterministic for the injected
datetime and performs no network or locale lookup.

## German Date

Date output uses fixed numeric formatting and checked-in German weekday
abbreviations. No process locale or arbitrary `strftime` format participates.
The stable formats are:

| Identifier | Rendered form |
| --- | --- |
| `day_month_full_year` | `DD.MM.YYYY` |
| `day_month_short_year` | `DD.MM.YY` |
| `day_month` | `DD.MM.` |
| `weekday_day_month` | `MO. DD.MM.` |

`day_month_full_year` (`DD.MM.YYYY`) is the default. The fixed weekday labels,
Monday through Sunday, are `MO.`, `DI.`, `MI.`, `DO.`, `FR.`, `SA.`, and
`SO.`. Timezone conversion occurs before the calendar date is formatted.
ISO-style, US-style, compatibility aliases, and user-supplied format strings
are not accepted.

Dates support `error`, `truncate`, and `clip` overflow. A five-pixel-high row
can render at least `DD.MM.` with the compact `3x5` font when its width is
sufficient.

## Static Text

Static Text renders configured content at intrinsic glyph size. When
`uppercase` is enabled, only ASCII `a` through `z` are converted to `A` through
`Z`; other characters are neither transliterated nor locale-normalized. When
it is disabled, content is preserved exactly. Any character absent from the
selected font produces a controlled, sanitized `unsupported_glyph` failure.

Horizontal start, center, and end alignment and vertical top, center, and
bottom alignment are supported. Overflow behavior is explicit:

- `error` requires the complete normalized text to fit before drawing;
- `truncate` draws the largest complete glyph prefix that fits;
- `clip` aligns the complete text and clips pixels at the region boundary,
  including partial edge glyphs.

Truncation never draws a partial glyph. Clipping never writes outside the
supplied region, and unset glyph pixels preserve its existing background.

## Scrolling Text

Scrolling Text uses only the injected render-context animation time. Its
integer pixel phase is:

```text
floor(animation_time * speed)
```

The cycle length is the region width plus the measured intrinsic text width
plus `trailing_gap`. The trailing gap is an exact number of physical pixels.
Fractional speeds are supported without fractional drawing coordinates.

Initial horizontal alignment defines the phase-zero base position: `start`
places the text just outside the start edge, `center` uses the centered base
position, and `end` places it just outside the end edge. `left` and `right`
change movement direction only. Rightward motion is the symmetric counterpart
of leftward motion; glyph pixels retain their original order and are never
mirrored.

The migrated schema-v1 settings use end-edge initial alignment, leftward
movement, the legacy-compatible `5x7` font and one-pixel spacing, ASCII
uppercase conversion, vertical centering, the configured text color and speed,
and a six-pixel trailing gap. Phase 3C tests verify byte-for-byte local-region
equivalence with the active schema-v1 renderer across entry, movement,
clipping, trailing-gap, and cycle-reset phases.

## Fonts, fitting, and placement

All four widgets use the Phase 3A `3x5`, legacy-compatible `3x7`, and
legacy-compatible `5x7` font system. An explicit font must exist, cover every
required character, and fit the region height. Clock selection and Date
`error` overflow also preflight the widest possible output, so a later time,
date, or weekday cannot invalidate an otherwise accepted font.

Automatic selection follows the Phase 3A deterministic priority. Date
and Static Text `truncate` and `clip` select by height and complete character
coverage without shrinking merely to avoid the selected overflow behavior.
Scrolling Text selection is constrained by height and complete glyph coverage,
not region width. Five-pixel-high regions use `3x5` when it supports the
content.

Glyphs always render at their intrinsic bitmap size. They are never scaled,
stretched, or rotated. Clock and Date default to centered placement. Static
Text supports start, center, and end plus top, center, and bottom alignment;
Scrolling Text uses its configured initial and vertical alignments. Unset glyph
pixels preserve the region's existing background.

## Registry and update policies

Each call to the canonical registry factory returns a fresh immutable
registry in stable order: Nothing / Gap, Clock, Static Text, Scrolling Text,
Date, System Metric, and Metric Bar. Every definition binds exactly one canonical settings model, one
renderer, validated defaults, broad minimum-size editor guidance, and an
immutable settings-aware update-policy resolver. No registry instance is
created during package import.

Update policies describe service scheduling needs:

- `continuous` for Scrolling Text;
- `half_second` for a Clock with blinking separators;
- `second_boundary` for a non-blinking Clock with seconds;
- `second_boundary` for System Metric and Metric Bar;
- `minute_boundary` for a plain Clock;
- `day_boundary` for Date;
- `static` for Blank and Static Text.

Aggregation uses the explicit precedence shown above, from highest to lowest
frequency. Disabled regions do not participate and an empty layout is
`static`. The compatibility `animated_content` flag is true only for
`continuous`, `half_second`, and `second_boundary`.

Two independent five-pixel rows can use automatic `3x5` fonts without vertical
bleed. The compositor does not use editor minimum guidance to reject narrower
valid scrolling, clipping, truncation, or blank regions.

## Backend and editor activation

Schema v2, compositor rendering, update-policy scheduling, persistence, and
canonical local API operations are active in the backend. The web GUI edits
arbitrary 90×10 profiles through the canonical API and keeps draft validation
separate from Apply and Save. See
[Visual free-layout editor](free-layout-editor.md) and
[Phase 4 backend activation](phase4-backend.md).
