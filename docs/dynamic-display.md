# Dynamic Matrix900 default display

The default display is rendered as a complete 90x10 RGB888 Creator frame. Its physical layout starts at the front of the case and uses these fixed regions:

- 24 pixels: compact 24-hour clock
- 4 pixels: black gap
- 31 pixels: turquoise `CACHYOS` marquee
- 31 pixels: 24-hour clock

The Matrix900 Creator regions are arranged in reverse order relative to the physical case, but the pixel order inside each region must remain left-to-right. `nexlinq.default_display` handles this mapping so text and clocks are not mirrored.

Run one frame manually:

```console
python -m nexlinq.default_display --once
```

Run the animated display manually at the default five frames per second:

```console
python -m nexlinq.default_display
```

The user service is installed from `systemd/nexlinq-display.service`. It must not run concurrently with another process that writes HID reports to the Matrix900.
