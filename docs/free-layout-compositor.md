# Free-layout compositor foundation

This document describes the implemented but inactive Phase 2 software
compositor for future NexTuxLinq Matrix900 free-form layouts. The daemon,
HTTP API, GUI, persistence layer, DeviceWorker, and physical output continue
to use the schema-v1 renderer.

## Regions and widgets

A region is an independent rectangular container with stable identity,
integer `x`, `y`, `width`, and `height` geometry, enabled state, an optional
background color, and exactly one widget assignment. A widget supplies the
content and content-specific settings. Geometry does not change when a user
selects another widget type.

The future editor will show one widget dropdown for each region. Initial
choices will include:

- Nothing / Gap
- Clock
- Static Text
- Scrolling Text
- Date

`Nothing / Gap` is the schema `blank` widget. It draws no foreground pixels.
When the region has a background color, that background remains visible.
When it has no background color, the canvas background remains visible.
Canvas pixels need no region at all, so uncovered pixels are also valid gaps.

The compositor does not stretch widget content to fill a region. A clock in a
90-pixel-wide region receives the complete 90-pixel local surface, but its
future clock renderer will choose its intrinsic pixel size and apply its own
alignment settings. Font choice, fitting, alignment, and animation behavior
belong to widget renderers rather than the region compositor.

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
model, renderer, default settings, animation capability, safe display label,
and optional minimum geometry.

Phase 2's production registry contains only the `blank` renderer, labeled
`Nothing / Gap`. Clock, static-text, scrolling-text, and date configurations
remain valid schema-v2 values but do not yet have production renderers.
Rendering an enabled unsupported widget fails the complete candidate frame;
it never silently becomes blank. Disabled unsupported widgets remain in
metadata without being resolved or invoked.

Renderer failures are categorized and sanitized with the region ID and widget
type. Partial frames are not returned.

## Deferred activation

This compositor is imported only by its hardware-independent tests. It is not
connected to the active renderer or exposed through the API or GUI. Clock,
text, date, editor dropdowns, configuration persistence cutover, and daemon
activation remain future phases. The separate
[free-layout font foundation](free-layout-fonts.md) now provides inactive
immutable bitmap fonts and local text primitives, but no widget renderer uses
them yet. No physical hardware test is required for either foundation.
