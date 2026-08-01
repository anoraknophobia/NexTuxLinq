# Visual free-layout editor

Profile presets, JSON transfer, managed backups, and draft-loss behavior are
documented in [Profile management, transfer, presets, and backups](profile-management.md).

The active NexTuxLinq web GUI includes a visual editor for the Phanteks
Matrix900's exact 90×10 RGB888 canvas. A profile contains an ordered list of
regions; each region can be added, duplicated, selected, moved, resized,
reordered, enabled, disabled, or removed. New profiles start with the default
canvas background and no regions.

Each region has exactly one widget selection: Nothing / Gap, Clock, Static
Text, Scrolling Text, Date, System Metric, or Metric Bar. The inspector displays only the canonical
settings for that widget, including font, alignment, color, timezone, overflow,
scroll direction, speed, and trailing gap where applicable. Geometry is always
stored as integer `x`, `y`, `width`, and `height` pixels. Full/half canvas
buttons are calculation helpers and do not persist percentages.

Every editor color uses the same compact control: a visible swatch opens the
native picker and a hexadecimal field beside it shows the canonical
`#RRGGBB` value. Picker and field stay synchronized, and complete lowercase
values normalize to uppercase. An incomplete or malformed hexadecimal value
remains visible as an invalid local draft; it disables Apply and Save and
keeps the last valid preview instead of silently substituting black.

Font dropdowns are populated from canonical backend metadata. **Auto — largest
fitting** chooses among the native 5×10, 5×9, 4×9, 5×7, 3×7, and 3×5 fixtures
using the configured widget's real content, geometry, and overflow rules. The
inspector shows the currently resolved Auto font. Concrete fonts remain visible;
an unavailable choice is disabled where practical with a concise height, width,
or character-coverage reason. An explicit saved choice is retained while its
draft geometry is invalid rather than silently replaced.

The primary page order is Device status, Free-layout editor, Physical display
preview, Profile, then Validate, Apply, and Save. The full-width region
inspector remains below the canvas. Moving Profile below the preview changes no
draft, applied, saved, transfer, preset, or backup state behavior.

## Coordinates and preview

Canonical logical X coordinates start with the Front at `x=0`. The editor and
physical display preview instead use viewer perspective: Rear appears on the
left and Front on the right. A region is placed with
`viewer_x = 90 - logical_x - width`; dragging converts that position back to
logical X before validation. Width, height, Y, text, and glyph pixel order are
unchanged, so content is never mirrored.

Unused pixels display the canvas background. A Blank region is optional and
draws only its region background. Region backgrounds remain visible under
unset glyph pixels.

Each physical-preview refresh constructs a fresh 90×10 image from the current
authoritative RGB888 framebuffer. Canvas-background pixels are established in
the new buffer before enabled regions are moved once into viewer perspective,
then the browser replaces the displayed image atomically. Editor selection
outlines, labels, and resize handles remain separate DOM layers and never enter
the physical-preview pixels, so an older Clock, Metric, or scrolling frame
cannot leave fragments behind.

## Draft workflow

Edits create a local draft. Bounds and overlap checks run immediately, then a
debounced request validates the complete candidate through the real registry
and compositor. A valid response supplies the draft preview and aggregate
update policy without persistence or physical upload. Invalid drafts keep the
last valid preview and cannot be applied or saved.

Invalid regions retain their red outline and also show a concise textual
reason at the region. When a region is too small to hold that label, the label
appears immediately below the canvas without changing the 90×10 canvas size.
Selecting a message opens the affected region, where the inspector lists every
known sanitized error and marks the responsible geometry or widget control.
The canvas message prioritizes bounds, overlap, height, width, settings, and
content errors in that order.

For renderer geometry failures, the validation API returns only safe numeric
metadata: actual and authoritative minimum width and height when known. The
service obtains these limits by probing the configured canonical widget
renderer within the fixed canvas bounds; it does not parse exception text.
Consequently font choice, Clock seconds, Date format, System Metric fixed digit
slots, labels, and overflow behavior are reflected accurately. Clip,
Truncate, Scrolling Text, and Metric Bar do not inherit a generic full-content
width. Validation labels never include widget content, host paths, or raw
exceptions.

**Apply** publishes the validated candidate to runtime state. **Save applied
configuration** persists only that applied state; it never saves a different
local draft. Revision conflicts preserve the local draft and require an
explicit reload rather than fabricating a merge. Reconnect polling accepts a
newer server state only when no local draft would be lost.

When a schema-v1 file was migrated in memory, the editor shows **Migration
pending** and enables **Save applied configuration** even when there is no
ordinary profile change. That explicit save needs no Apply step: it validates
the already-applied canonical document, preserves the one-time schema-v1
backup, and atomically writes schema v2. A failed save leaves the migration
pending and the original file and applied state unchanged.

The editor uses no frontend framework or external JavaScript dependency. It
does not change Creator ordering, device framebuffer orientation, DeviceWorker
ownership, or Matrix900 protocol behavior. NexTuxLinq continues to support only
the Phanteks Matrix900.
