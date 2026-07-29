# Free-layout backend activation

Phase 4 makes schema v2 and `rendering_v2` the canonical NexTuxLinq backend
for the Phanteks Matrix900. It does not add the visual free-layout editor or
support another device.

## Configuration and persistence

Valid schema-v1 and pre-versioned documents are migrated deterministically to
schema v2 in memory. Loading never rewrites the source file. A valid schema-v2
document loads directly; malformed and unsupported versions are rejected
safely without partially replacing runtime state.

Explicit saves validate and render the complete schema-v2 document before an
atomic write. Before the first explicit save replaces a canonical schema-v1
file, NexTuxLinq writes a private, unpredictably named, one-time rollback copy
beside it and synchronizes that directory entry. Existing backups are never
overwritten. A pre-commit failure leaves the original configuration intact.
The older pre-rename configuration path remains untouched when it is the
migration source.

Applied and saved state retain independent monotonic revisions. Stale writes
return a conflict, validation precedes publication, and a failed candidate
does not replace the last known-good applied configuration or frame.

## Rendering and scheduling

The service renders the active canonical profile through
`create_widget_registry()` and the free-layout compositor. Preview uses its
logical RGB888 framebuffer; the existing single DeviceWorker receives its
Creator-order device framebuffer. The two buffers come from the same complete
render result. Creator reports, HID transport, transaction locking, and the
logical-to-device region mapping are unchanged.

One service scheduler aggregates the active profile's update policy:

- `continuous` follows the established animation cadence;
- `half_second` targets the next monotonic animation half-cycle;
- `second_boundary` and `minute_boundary` target the next wall-clock boundary;
- `day_boundary` targets the next midnight relevant to enabled Date widgets;
- `static` waits for a configuration or connection-state wake-up.

The initial frame and configuration/profile changes render immediately.
Cadence waits use monotonic durations, boundaries are recalculated after each
frame, and the DeviceWorker's existing bounded latest-wins frame slot prevents
an unbounded physical-upload backlog.

## API and current GUI

The existing `/api/v1/config`, `/api/v1/apply`, `/api/v1/preview`, and save
workflow remain available through a strict legacy-form adapter. The adapter
preserves the migrated `24 / 4 / 31 / 31` layout and refuses to flatten or
overwrite a profile it cannot represent.

Canonical state, validation, apply, save, and profile selection are available
under `/api/v1/free-layout`. Candidate validation uses the real registry and
compositor but does not persist or upload. Responses use stable schema
identifiers and sanitized region/widget error metadata.

The current GUI remains the existing form, not a free-layout editor. A
representable migrated profile remains editable. An arbitrary free-layout
profile is shown as read-only while status and preview remain available. The
preview remains viewer-oriented (Rear on the left, Front on the right), while
stored coordinates and renderer output remain canonical and glyphs are never
mirrored.

Physical Matrix900 validation of the activated path remains required. Phase 4
does not perform or authorize a live upload during development validation.
