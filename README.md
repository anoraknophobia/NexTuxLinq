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
