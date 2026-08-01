# Free-layout compositor

This document describes the NexTuxLinq Matrix900 free-layout compositor.
Phase 3D integrated all five renderers and update-policy metadata; Phase 4
activates that compositor in the service while preserving the existing
DeviceWorker and Creator output path.

## Regions and widgets

A region is an independent rectangular container with stable identity,
integer `x`, `y`, `width`, and `height` geometry, enabled state, an optional
background color, and exactly one widget assignment. A widget supplies the
content and content-specific settings. Geometry does not change when a user
selects another widget type.

The active visual editor shows one widget dropdown for each region. Choices
are:

- Nothing / Gap
- Clock
- Static Text
- Scrolling Text
- Date
- System Metric
- Metric Bar
- Built-in Icon

`Nothing / Gap` is the schema `blank` widget. It draws no foreground pixels.
When the region has a background color, that background remains visible.
When it has no background color, the canvas background remains visible.
Canvas pixels need no region at all, so uncovered pixels are also valid gaps.

The compositor does not stretch widget content to fill a region. A clock in a
90-pixel-wide region receives the complete 90-pixel local surface. The
Clock renderer chooses its intrinsic pixel size and applies
its own alignment settings. Font choice, fitting, alignment, and animation
behavior belong to widget renderers rather than the region compositor.

## Logical canvas and local surfaces

The logical Phanteks Matrix900 canvas is exactly 90 pixels wide and 10 pixels
high. It uses row-major RGB888 pixels:

- `x=0` is the physical front;
- `x=89` is the physical rear;
- `y=0` is the top row;
- `y=9` is the bottom row.

Composition starts by filling the logical canvas with its opaque background
color. Each enabled region receives a new isolated local surface with exactly
the region's dimensions. The surface starts with the region background when
present, or the canvas background otherwise.

Widget drawing uses local coordinates only. Pixel writes outside the local
rectangle are clipped and cannot affect the canvas or another region. Local
surfaces expose immutable byte copies, not their mutable backing storage.
After a widget succeeds, only its exact rectangle is copied to the logical
canvas.

Alongside RGB888 data, each local surface records semantic foreground
eligibility. Glyph and Metric Bar foreground writes mark this mask explicitly;
background fills clear it. The compositor copies immutable mask rows with the
same clipping and region isolation as color rows. It applies any profile effect
once to the complete logical framebuffer after all regions render and before
Creator orientation. See [Display effects](display-effects.md).

Disabled regions remain editor metadata but do not render. Schema v2 rejects
overlaps, and the compositor does not add overlap handling, layers, z-index,
alpha, transparency, or blending.

## Creator orientation

The logical framebuffer directly represents the front-to-rear preview. The
Matrix900 Creator framebuffer uses reversed region-block placement, not a
whole-frame horizontal mirror.

For a region at logical `x` with width `w`, its Creator origin is:

```text
90 - x - w
```

Each row slice is copied to that origin without reversing the pixels inside
the slice. Widget content therefore remains left-to-right, and `y` coordinates
and row order remain unchanged. A single 90-pixel-wide region maps to Creator
origin zero without any content reversal.

The device framebuffer is initialized with the canvas background before
enabled regions are copied. Uncovered pixels and disabled regions therefore
remain canvas background in device order. Both logical and device buffers are
exactly 2,700 bytes.

## Widget registry and failure behavior

Widget definitions bind one stable schema widget type to its exact settings
model, renderer, default settings, immutable update-policy resolver, safe
display label, and optional minimum editor guidance. The canonical
registry contains exactly `blank`, `clock`, `static_text`, `scrolling_text`,
and `date`, in that order. Each factory call creates a fresh registry; package
import creates no global registry.

Renderer failures are categorized and sanitized with the region ID and widget
type, plus a stable field when applicable. Raw exception objects, messages,
tracebacks, content, timezone values, paths, and implementation details are
not retained. Partial frames are not returned.

## Aggregate update policy

Only successfully rendered enabled regions contribute to the immutable
`RenderResult.update_policy`. The explicit highest-to-lowest precedence is:

1. `continuous`
2. `half_second`
3. `second_boundary`
4. `minute_boundary`
5. `day_boundary`
6. `static`

Empty layouts and layouts containing only disabled regions are `static`.
`animated_content` remains as compatibility metadata: it is true for
`continuous`, `half_second`, and `second_boundary`, and false for
`minute_boundary`, `day_boundary`, and `static`. Phase 4's single service
scheduler consumes this metadata without creating one task per region.
An animated Foreground Rainbow contributes `continuous`; Off and a zero-speed
rainbow add no periodic requirement.

Two separate five-pixel rows are supported. Each renderer receives only its
own five-pixel local surface, automatic font selection can choose `3x5`, and
neither row can draw into the other. No content is scaled or stretched.

Integrated migration tests compare complete framebuffer bytes for default and
customized schema-v1 profiles. The logical framebuffer exactly matches the
schema-v1 physical-preview order, while the device framebuffer exactly matches
Creator order across entry, scrolling,
clipping, trailing-gap, intermediate, and cycle-reset phases.

## Runtime activation

The service uses this compositor to validate candidates and to generate both
preview and DeviceWorker framebuffers. The canonical API exposes validation
and update-policy metadata. The GUI edits arbitrary regions and obtains draft
previews through the same validation path without persistence or upload.
Physical Matrix900 validation remains a separate operational step. See
[Phase 4 backend activation](phase4-backend.md) and
[Visual free-layout editor](free-layout-editor.md).
