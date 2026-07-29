# Dynamic Matrix900 default display

The default display is rendered as a complete 90x10 RGB888 Creator frame. Its physical layout starts at the front of the case and uses these fixed regions:

- 24 pixels: compact 24-hour clock using the same seven-pixel height as the rear clock
- 4 pixels: black gap
- 31 pixels: turquoise `CACHYOS` marquee
- 31 pixels: 24-hour clock

The Matrix900 Creator regions are arranged in reverse order relative to the physical case, but the pixel order inside each region must remain left-to-right. `nextuxlinq.default_display` handles this mapping so text and clocks are not mirrored.

The renderer also accepts a validated display profile. Configuration fields
cover scrolling text, text and clock colors, scroll speed, optional brightness,
and the fixed first-version layout. Text is normalized to uppercase and supports
`A-Z`, `0-9`, spaces, and `. - _ : ! ? /`. Brightness defaults to `null`, which
means that rendering does not request a brightness change.

Configuration is stored as versioned JSON at:

```text
${XDG_CONFIG_HOME:-~/.config}/nextuxlinq/config.json
```

If only the pre-rename `~/.config/nexlinq/config.json` exists, it is loaded
without modification and remains in place. A later explicit save writes the
new canonical path; an existing canonical file always takes precedence.

Missing or invalid files fall back to the built-in display without overwriting
the file. Saves use a temporary file and atomic replacement. The current
standalone display command continues to use the built-in `CACHYOS` profile;
`nextuxlinqd` loads saved profiles and owns long-running rendering.

Run one frame manually:

```console
python -m nextuxlinq.default_display --once
```

Run the animated display manually at the default five frames per second:

```console
python -m nextuxlinq.default_display
```

`systemd/nextuxlinq-display.service` is retained as a development helper.
Installing `nextuxlinqd.service` disables this helper and its telemetry
counterpart so they do not compete for the Matrix900.

The standalone command now participates in the same advisory writer lock as
`nextuxlinqd` and direct `nextuxlinqctl` mutations. See
[`daemon-core.md`](daemon-core.md) for the current single-writer architecture.
