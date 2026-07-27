# nexlinq-linux

Open-source Linux control software for the Phanteks Matrix900 display.

## Project scope

This project currently targets only the Phanteks Matrix900.

The following hardware is not currently supported or tested:

- Phanteks Matrix600
- Phanteks fans and fan controllers
- Other NexLinq hubs, displays, and accessories

Compatibility with untested devices must not be assumed. Support for additional hardware may be added in the future if contributors can provide hardware access, USB captures, and testing.

## Status

This project is in an early reverse-engineering stage. Device control is not implemented yet.

Currently identified hardware:

- Device: Phanteks Matrix900
- USB vendor ID: `2e3c`
- USB product ID: `6500`
- Interface: vendor-defined USB HID
- HID report ID: `0x01`
- HID report size: 64 bytes including the report ID
- USB OUT endpoint: `0x01`
- USB IN endpoint: `0x82`

## Goals

- Detect connected Matrix900 devices
- Control the display without root privileges
- Reverse-engineer and document the USB protocol
- Provide a command-line interface
- Support brightness, text, images, layouts, animations, and system telemetry
- Package the software for Arch Linux and CachyOS

## Safety

Unknown commands are not sent to the device.

Firmware updates, bootloader access, and other potentially destructive operations are out of scope until the protocol is fully understood.

## License

MIT

## Trademark notice

Phanteks and NexLinq are trademarks of their respective owners. This project is an independent community project and is not affiliated with or endorsed by Phanteks.

## Current Linux control support

The Linux implementation can currently:

- Detect connected Phanteks Matrix900 devices through `hidraw`.
- Set brightness from 10% through 100% in ten-percent steps.
- Read and display the Matrix900 response report.

Examples:

```console
nexlinqctl detect
nexlinqctl brightness 50
```

### Linux telemetry

Send one hardware telemetry update:

```console
nexlinqctl telemetry
```

Continuously update the display once per second:

```console
nexlinqctl telemetry --watch
```

The initial Linux sensor mapping uses:

- CPU temperature: `k10temp` / `Tctl`
- GPU temperature: `amdgpu` / `edge`
- Fan or pump speed: highest RPM reported by `nct6798`

This mapping is currently hardware-specific and will become configurable.

### System Data widget source

Configure the verified fullscreen System Data variant:

```console
nexlinqctl system-data cpu
nexlinqctl system-data gpu
nexlinqctl system-data fan
```

This command currently uses user-facing layout 12, internal layout ID `0x01`.
The CPU, GPU, and FAN source identifiers are verified from Windows captures.

### Fullscreen free text

Display ASCII text using the captured fullscreen Time and Date widget:

```console
nexlinqctl text "NEXLINQ"
```

The current encoder supports up to 49 ASCII bytes.
Unicode encoding and the exact maximum firmware length are not yet verified.

### Fullscreen digital clock

Display the captured fullscreen digital-clock configuration:

```console
nexlinqctl clock
```

The background telemetry service supplies the current Unix timestamp.
Clock option fields such as timezone, position, colors, and 12/24-hour mode
are currently preserved as an exact captured profile.

### Fullscreen Time and Date widgets

Display the verified captured profiles:

```console
nexlinqctl time-widget digital
nexlinqctl time-widget graphical
nexlinqctl time-widget date
nexlinqctl time-widget week
```

`nexlinqctl clock` remains available as an alias for the digital clock.
The captured digital profile displays local time using the 24-hour format.

### Two-slot clock and System Data layout

Display a digital clock beside a live System Data widget:

```console
nexlinqctl layout-clock-system cpu
nexlinqctl layout-clock-system gpu
nexlinqctl layout-clock-system fan
```

This preset uses user-facing layout 1, internal layout ID `0x02`.

### Generic multi-slot layouts

Configure a user-facing NexLinq layout by supplying exactly one widget for
every slot, in visual slot order:

```console
nexlinqctl layout 1 digital system-gpu
nexlinqctl layout 2 digital system-cpu system-gpu
nexlinqctl layout 3 digital date system-cpu system-gpu
```

Currently supported slot widgets:

- `digital`
- `graphical`
- `date`
- `week`
- `system-cpu`
- `system-gpu`
- `system-fan`

Layout geometry and slot counts are verified from the Windows layout capture.
Not every possible widget and layout combination has been validated on hardware.

### Fullscreen System Data profiles

Display the captured System Data widgets:

```console
nexlinqctl system-widget variant1-cpu
nexlinqctl system-widget variant1-gpu
nexlinqctl system-widget variant1-fan
nexlinqctl system-widget variant2
nexlinqctl system-widget variant3
nexlinqctl system-widget overview
nexlinqctl system-widget aio-pump
nexlinqctl system-widget graph-cpu
nexlinqctl system-widget graph-gpu
```

The fixed profiles reproduce exact Windows capture reports.
Source selection has only been verified for System Data variant one.


### Single-source System Data graph

Display an animated graph for one selected temperature source:

```console
nexlinqctl system-widget graph-cpu
nexlinqctl system-widget graph-gpu
```

The same widgets can be used in generic layouts as `system-graph-cpu` and
`system-graph-gpu`. CPU and GPU are hardware-verified. FAN is not yet verified
for this widget.


### Single-value System Data bars

Display one live CPU or GPU value using the verified bar profiles:

```console
nexlinqctl system-widget bar-cpu
nexlinqctl system-widget bar-gpu
nexlinqctl system-widget background-bar-cpu
nexlinqctl system-widget background-bar-gpu
```

The equivalent generic-layout names are:

- `system-bar-cpu`
- `system-bar-gpu`
- `system-background-bar-cpu`
- `system-background-bar-gpu`

The legacy names `variant2` and `variant3` remain CPU-profile aliases.
FAN source selection is not yet verified for these widgets.


### FAN source support

The following fullscreen FAN profiles are hardware-verified:

```console
nexlinqctl system-widget variant1-fan
nexlinqctl system-widget background-bar-fan
```

The generic-layout name for the second profile is
`system-background-bar-fan`.

FAN is not supported by the regular value bar or the animated single-source
graph. The Matrix900 falls back to an incomplete CPU display for those
combinations.

### RPM telemetry sources

The telemetry command supports two explicit RPM sources:

```console
nexlinqctl telemetry --rpm-source fan
nexlinqctl telemetry --rpm-source pump
```

- `fan` reads all active non-AIO fans from OpenLinkHub and sends their rounded
  average RPM. This is the default.
- `pump` reads the highest RPM value from the `nct6798` hwmon chip.

The Matrix900 protocol currently exposes one verified RPM telemetry field, so
fan and pump RPM cannot yet be transmitted simultaneously.
