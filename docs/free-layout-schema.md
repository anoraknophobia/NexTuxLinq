# Free-layout schema v2

Schema v2 is the canonical NexTuxLinq backend configuration for Matrix900
free-form layouts as of Phase 4. Schema-v1 input remains supported through
deterministic in-memory migration. The active
[visual free-layout editor](free-layout-editor.md) edits this schema directly.

## Canvas and geometry

The Phanteks Matrix900 physical canvas is exactly 90 pixels wide and 10 pixels
high using RGB888 pixels. Schema v2 records these dimensions explicitly and
rejects any other canvas size. The canvas has one opaque background color in
canonical `#RRGGBB` form.

Profiles contain an ordered list of absolute rectangles. Each rectangle stores
integer `x`, `y`, `width`, and `height` values. Width and height are positive,
coordinates are non-negative, and the complete rectangle must fit within the
90×10 canvas.

Rectangles may touch at an edge but may not intersect. Disabled rectangles
continue to reserve their geometry, so they also participate in overlap
validation. A profile may contain no rectangles. Pixels not covered by a
rectangle are intentional gaps and retain the canvas background.

Schema v2 permits at most 32 regions in one profile and 128 regions across one
configuration document. These limits bound validation, API, and persistence
costs.

## Stable region identity

Every region has an ID containing at most 64 ASCII letters, digits, hyphens, or
underscores. The first character is a letter or digit. IDs are unique within a
profile and are structural: moving, resizing, or renaming a region does not
change its ID.

The optional display name is separate from the ID. Names are bounded printable
text, and leading or trailing whitespace is rejected. Duplicate display names
are allowed. Control and non-printing characters are rejected. Stable region
IDs, not display names, identify regions structurally.

## Widget discriminator

Each region contains a strict widget object with a `type` discriminator and a
matching `settings` object. Initial types are:

- `blank`
- `clock`
- `static_text`
- `scrolling_text`
- `date`

Unknown widget types, settings for the wrong discriminator, missing required
settings, and unknown settings fields are rejected. Phase 4 activates the
Phase 3D registry and compositor in the service, as documented in
[free-layout widget renderers](free-layout-widgets.md).

The optional region `background_color` is common to all widgets. A null or
omitted value means that the canvas background remains visible. Widget settings
do not duplicate this property.

## Deterministic schema-v1 migration

Migration accepts an already validated schema-v1 document and constructs a new
schema-v2 object without modifying the source, reading files, using time, or
generating random IDs. Each v1 profile becomes:

- a black 90×10 canvas;
- a `24×10` front clock at `(0, 0)` using the legacy 3×7 font;
- no region for the four-pixel gap at `x=24..27`;
- a `31×10` left-scrolling text region at `(28, 0)` using the legacy 5×7
  font, end-edge initial alignment, and six-pixel trailing gap;
- a `31×10` rear clock at `(59, 0)` using the legacy 5×7 font.

Both clocks retain the v1 color, local 24-hour time, centered alignment, no
seconds, and no blinking separator. Scrolling content, text color, speed, and
brightness are preserved. Profile names and the active profile are preserved.
Fixed migration IDs are unique within every migrated profile.

Pre-versioned schema-0 data follows the existing normalization path:

```text
schema 0 -> validated schema 1 -> deterministic schema 2
```

Malformed data is never replaced with schema-v2 defaults by the strict parser.
Schema versions newer than 2 have a distinct unsupported-version result.

## Persistence cutover

Loading a schema-v1 file migrates it only in memory and does not rewrite it.
The first explicit schema-v2 save that replaces a canonical schema-v1 file
preserves the validated source as a rollback backup with these rules:

- the backup is in the canonical configuration directory;
- its unpredictable filename cannot overwrite an existing entry;
- it is not named or discovered as an active configuration file;
- it is mode `0600`;
- its file contents and directory entry are synchronized before canonical
  replacement;
- failure to create a safe backup prevents the v2 commit.

The save remains atomic: validation precedes the write, a pre-commit failure
leaves the original intact, and schema v2 is serialized only after an explicit
save. Automatic downgrade from schema v2 would lose free-layout information
and is not performed.

This design remains specific to the Phanteks Matrix900. It does not change HID
transport, protocol reports, physical orientation, or framebuffer generation.

The separate [free-layout compositor foundation](free-layout-compositor.md)
implements region-local drawing, all five canonical widgets,
settings-dependent update-policy aggregation, and logical-to-Creator
orientation. Complete byte comparisons prove that migrated schema-v1 logical
and device framebuffers remain equivalent to the compatibility renderer. The
service now uses this compositor for preview and DeviceWorker candidates.

The [free-layout font foundation](free-layout-fonts.md) adds immutable
3×5, legacy-compatible 3×7, and legacy-compatible 5×7 bitmap fonts plus local
measurement, alignment, clipping, and overflow primitives. All text-producing
widgets use those primitives in the canonical renderer. See
[Phase 4 backend activation](phase4-backend.md) for persistence, API,
scheduling, and compatibility behavior.
