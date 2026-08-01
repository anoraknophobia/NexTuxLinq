# Display effects

Display effects are immutable profile-level schema-v2 settings for the
Phanteks Matrix900. Existing profiles and schema-v1 migrations default to
`none`. The initial modes are **Off** (`none`) and **Foreground Rainbow**
(`foreground_rainbow`). Rainbow profiles store a direction and a speed from
0.00 through 2.00 complete color cycles per second; the default is
`rear_to_front` at 0.10 cycles/s. A speed of zero produces a static rainbow.

Foreground Rainbow is one continuous physical-width field across all 90
columns. It uses the compositor's shared `RenderContext.animation_time`, not
wall or system time and not per-region clocks. Logical geometry remains
unchanged. For effect calculation the viewer coordinate is
`viewer_x = 89 - logical_x`: Rear is viewer left and Front is viewer right.
`rear_to_front` visibly moves colors left to right, while `front_to_rear`
moves them right to left. Region boundaries and unused gaps never reset phase.

Region surfaces maintain a separate semantic foreground-eligibility mask.
Clock, text, Date, and System Metric glyph pixels are eligible. Metric Bar
filled pixels and an enabled border are eligible. Canvas and region
backgrounds, unused pixels, Blank regions, transparent glyph pixels, invisible
metric digit slots, and the empty part of Metric Bars are not. Eligibility is
independent of the RGB value, so equal foreground and background colors remain
unambiguous.
Built-in icons define eligibility in immutable icon metadata. The native
CachyOS logo is explicitly ineligible, so its fixed `#1DC7B5` pixels remain
byte-identical for every Rainbow direction, speed, and phase.

The compositor applies the deterministic integer RGB888 color wheel once,
after every region has rendered into the complete logical framebuffer and
before the established logical-to-Creator conversion. The logical preview and
Matrix900 output therefore derive from the same effected frame. There is no
CSS gradient, alpha blending, antialiasing, vector rendering, shader, or HID or
Creator protocol change.

The Profile section of the editor exposes Effect, Direction, and Speed using
backend metadata. These controls only change the local draft. Apply validates
and activates the draft; Save persists the already-applied configuration.
Widget foreground colors remain editable and stored, and return unchanged when
the effect is disabled. Both draft and applied previews use complete backend
framebuffers and retain the established Rear-left, Front-right perspective.

An animated rainbow contributes `continuous` to the existing aggregate update
policy, including for a Blank-only profile. Off and a zero-speed rainbow add no
periodic requirement. The existing single scheduler, bounded DeviceWorker
submission path, and preview animation loop remain authoritative.

Profile exports, complete configuration transfers, managed backups, restores,
and the built-in **Rainbow Showcase** preset preserve the canonical effect
settings. Old exports without an effect remain importable and resolve to Off.
Import, restore, preset creation, and preview remain side-effect-free until the
normal Apply and Save workflow.
