# Visual free-layout editor

The active NexTuxLinq web GUI includes a visual editor for the Phanteks
Matrix900's exact 90×10 RGB888 canvas. A profile contains an ordered list of
regions; each region can be added, duplicated, selected, moved, resized,
reordered, enabled, disabled, or removed. New profiles start with the default
canvas background and no regions.

Each region has exactly one widget selection: Nothing / Gap, Clock, Static
Text, Scrolling Text, or Date. The inspector displays only the canonical
settings for that widget, including font, alignment, color, timezone, overflow,
scroll direction, speed, and trailing gap where applicable. Geometry is always
stored as integer `x`, `y`, `width`, and `height` pixels. Full/half canvas
buttons are calculation helpers and do not persist percentages.

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

## Draft workflow

Edits create a local draft. Bounds and overlap checks run immediately, then a
debounced request validates the complete candidate through the real registry
and compositor. A valid response supplies the draft preview and aggregate
update policy without persistence or physical upload. Invalid drafts keep the
last valid preview and cannot be applied or saved.

**Apply** publishes the validated candidate to runtime state. **Save applied
configuration** persists only that applied state; it never saves a different
local draft. Revision conflicts preserve the local draft and require an
explicit reload rather than fabricating a merge. Reconnect polling accepts a
newer server state only when no local draft would be lost.

The editor uses no frontend framework or external JavaScript dependency. It
does not change Creator ordering, device framebuffer orientation, DeviceWorker
ownership, or Matrix900 protocol behavior. NexTuxLinq continues to support only
the Phanteks Matrix900.
