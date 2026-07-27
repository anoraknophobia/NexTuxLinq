# Dynamic Matrix900 default display

The default display is rendered as a complete 90x10 RGB888 Creator frame. Its physical layout starts at the front of the case and uses these fixed regions:

- 24 pixels: compact 24-hour clock using the same seven-pixel height as the rear clock
- 4 pixels: black gap
- 31 pixels: turquoise `CACHYOS` marquee
- 31 pixels: 24-hour clock

The Matrix900 Creator regions are arranged in reverse order relative to the physical case, but the pixel order inside each region must remain left-to-right. `nexlinq.default_display` handles this mapping so text and clocks are not mirrored.

The renderer also accepts a validated display profile. Configuration fields
cover scrolling text, text and clock colors, scroll speed, optional brightness,
and the fixed first-version layout. Text is normalized to uppercase and supports
`A-Z`, `0-9`, spaces, and `. - _ : ! ? /`. Brightness defaults to `null`, which
means that rendering does not request a brightness change.

Configuration is stored as versioned JSON at:

```text
${XDG_CONFIG_HOME:-~/.config}/nexlinq/config.json
```

Missing or invalid files fall back to the built-in display without overwriting
the file. Saves use a temporary file and atomic replacement. The current
standalone display command continues to use the built-in `CACHYOS` profile;
loading saved profiles into a long-running daemon is outside this phase.

Run one frame manually:

```console
python -m nexlinq.default_display --once
```

Run the animated display manually at the default five frames per second:

```console
python -m nexlinq.default_display
```

The user service is installed from `systemd/nexlinq-display.service`. It must not run concurrently with another process that writes HID reports to the Matrix900.
