# Matrix900 USB Protocol Notes

## Status

Reverse engineering is partial but no longer preliminary. The Creator
framebuffer path, brightness, live telemetry, and the captured widget and
layout profiles are implemented and exercised against hardware. Several
option bytes inside the captured profiles remain unassigned.

Every section below is labelled:

- **Verified** — confirmed by USB capture and reproduced on Linux hardware.
- **Inferred** — consistent with all observations, but not proven by a
  controlled one-variable experiment.
- **Unknown** — not established.

## Verified device information

- Device: Phanteks Matrix900
- USB vendor ID: `0x2e3c`
- USB product ID: `0x6500`
- USB version: `2.00`
- Device release: `2.00`
- Connection speed: USB High Speed, 480 Mbit/s
- Number of interfaces: `1`
- Interface class: USB HID
- Interface subclass: `0`
- Interface protocol: `0`
- HID version: `1.10`
- HID report descriptor size: `32` bytes
- Vendor-defined usage page: `0xff1b`
- Usage: `0x91`
- Report ID: `0x01`
- Input payload size: `63` bytes
- Output payload size: `63` bytes
- Total numbered HID report size: `64` bytes
- Interrupt OUT endpoint: `0x01`
- Interrupt IN endpoint: `0x82`
- Endpoint packet size: `64` bytes
- Endpoint polling interval: `1`

## Observed behavior

- The device is detected by the Linux `usbhid` driver.
- The device is exposed through `/dev/hidraw*`.
- The exact hidraw number may change between boots.
- The device does not emit input reports while idle.
- Opening the hidraw device for reading and writing does not change the display.
- The device echoes each complete 64-byte report it accepts through its
  interrupt IN endpoint. NexTuxLinq treats a missing or mismatching echo as a
  transaction failure.

## Remaining unknown protocol details

- Checksums or integrity fields, if any exist
- Display power command
- Whether the framebuffer survives a brightness command
- Whether partial or changed-chunk framebuffer updates are accepted
- Animation storage and playback
- Persistent device storage behavior
- The meaning of individual option bytes inside the captured widget profiles

## Reverse-engineering rules

- Capture traffic from the official NexLinq application before sending commands from Linux.
- Change only one setting per capture.
- Record the display state before and after every test.
- Do not send random or partially understood packets.
- Do not test firmware, bootloader, reset, or update commands.
- Keep original packet captures unchanged and document their provenance.
- Remove serial numbers and other machine-specific identifiers before publishing captures.

## Creator framebuffer protocol

**Verified.** This is the path NexTuxLinq uses for all normal display output.
The report layouts below were reproduced from USB captures of the official
application, and the implementation has been exercised against a physical
Matrix900, which renders the uploaded frame and echoes every report. The byte
layouts are transcribed here from the shipping implementation in
`src/nextuxlinq/creator.py`.

### Framebuffer geometry

| Property | Value |
|---|---|
| Width | 90 pixels |
| Height | 10 pixels |
| Pixel format | RGB888, three bytes per pixel |
| Byte order | Row-major, left to right, top to bottom |
| Complete frame | 2700 bytes |

### Transaction shape

One complete display update is exactly **51 HID reports** of 64 bytes each,
always in this order:

1. one initialization report,
2. **49** framebuffer data chunks,
3. one activation report that commits the frame.

Every report uses HID report ID `0x01` in byte 0, so 63 payload bytes remain.
The device echoes each report; NexTuxLinq requires all 51 echoes before it
treats a frame as transmitted.

### Initialization report

Command `0x05`, sent once before the data chunks:

```text
01 05 ff 00 00 01 01 00 ... 00
```

### Framebuffer data chunks

Command `0x05`, 49 reports carrying up to 56 payload bytes each:

| Offset | Size | Meaning |
|------:|-----:|---------|
| 0 | 1 | HID report ID `0x01` |
| 1 | 1 | Command `0x05` |
| 2 | 3 | Zero-based chunk index, big-endian |
| 5 | 1 | Payload length plus two |
| 6 | 1 | Frame width, `0x5a` (90) |
| 7 | 1 | Frame height, `0x0a` (10) |
| 8 | up to 56 | Framebuffer bytes for this chunk |

2700 bytes divided into 56-byte chunks yields 48 full chunks plus a final
12-byte chunk, so chunk 48 carries length marker `0x0e` while chunks 0 to 47
carry `0x3a`. Unused trailing bytes of the final report stay zero.

### Activation report

Command `0x03`, sent last. It commits the uploaded framebuffer and is
reproduced byte for byte from the capture:

```text
01 03 01 00 00 22 01 04 01 01 02 ff ff ff 00 00 00 ff 00 64 00
```

**Inferred.** Byte 5 (`0x22`) and bytes 8 onward are the same widget-slot
option block used by command `0x03` elsewhere. Their individual meanings are
not assigned.

### Logical versus device orientation

**Verified.** NexTuxLinq composes into a *logical* framebuffer whose `x=0` is
the physical front of the case, which is also the order the web preview uses.
The Creator regions are arranged in reverse order relative to the physical
case, so before upload each enabled region rectangle is moved to its mirrored
horizontal origin:

```text
device_x = 90 - logical_x - region_width
```

Pixel order *inside* a region is not reversed, so glyphs and icons are never
mirrored. The 2700-byte result of this conversion is the *device
framebuffer*, and it is exactly what the data chunks carry.

### Complete transactions only

NexTuxLinq only ever sends complete 51-report transactions. It does not
implement partial framebuffer updates or changed-chunk-only uploads, and it
makes no claim that the firmware would accept them. Whether the device
tolerates an incomplete chunk sequence is **unknown** and untested.

A transaction is bounded in time: each report echo has a per-report timeout
and the complete transaction has a single monotonic deadline. Any timeout,
missing echo, or transport error aborts the remaining reports, so the
activation report is never sent after an earlier report failed.

### Duplicate frame suppression

**Verified behavior of this project, not of the device.** NexTuxLinq compares
the final 2700-byte device framebuffer against the last framebuffer it
transmitted completely and successfully. When the bytes are identical it
sends zero HID reports, because the device already retains that image. A
changed metric value that renders to identical pixels therefore produces no
USB traffic at all.

The comparison happens only inside the device worker, immediately before the
reports are built. The baseline is discarded whenever the retained device
image can no longer be trusted: worker start and stop, device open, open or
discovery failure, disconnect, transaction failure or timeout, and every
brightness command. Brightness is treated conservatively because whether the
firmware retains the framebuffer across a brightness change is **unknown**.

## Brightness encoding

Brightness is configured by report `0x04` and applied together with reports `0x05` and `0x03` when the user selects **Send to Screen**.

Verified report prefix:

```text
01 04 00 00 00 01 VV 00 ...
```

`VV` is the encoded brightness value.

Verified values:

| Percentage | Device value |
|-----------:|-------------:|
| 10% | `0x47` |
| 30% | `0x5f` |
| 50% | `0x77` |
| 60% | `0x84` |
| 80% | `0x9c` |
| 100% | `0xb4` |

The values match the following integer mapping:

```python
round(60 + (percentage - 1) * 120 / 99)
```

The official NexLinq application exposes brightness in ten-percent steps from 10% through 100%.

Changing the slider alone does not update the display. The setting is transmitted only after selecting **Send to Screen**.

### Linux verification

Direct Linux tests confirmed that report `0x04` is applied immediately without transmitting reports `0x05` or `0x03`.

- Sending the known 10% report while the display was already at 10% left the visible brightness unchanged.
- Sending the known 100% report immediately increased the visible brightness.
- The Matrix900 echoed each complete 64-byte report through its interrupt IN endpoint.

Reports `0x05` and `0x03` are therefore not required for brightness-only changes. They remain part of the official applications broader **Send to Screen** workflow.

## Live telemetry report

Linux hardware testing confirmed the following fields in command `0x01`:

| Offset | Size | Meaning |
|------:|-----:|---------|
| 6 | 1 byte | CPU temperature in whole degrees Celsius |
| 7 | 1 byte | GPU temperature in whole degrees Celsius |
| 9 | 2 bytes | Fan or pump speed in RPM, big-endian |
| 21 | 4 bytes | Unix timestamp, big-endian |

The report uses payload-length marker `0x14` at offset 5 and `0x07` at offset 25.
The Matrix900 echoes the complete telemetry report after receiving it.

## Widget configuration report

Command `0x03` configures one widget slot per report.

| Offset | Size | Meaning |
|------:|-----:|---------|
| 0 | 1 byte | HID report ID `0x01` |
| 1 | 1 byte | Command `0x03` |
| 2 | 1 byte | Total slot count for the selected layout |
| 4 | 1 byte | Zero-based slot index |
| 5 | 1 byte | Observed constant `0x22` |
| 6 | 1 byte | Internal layout ID |
| 7 | 1 byte | Widget category |
| 8 | 1 byte | Widget ID |
| 9+ | variable | Widget-specific options |

The meaning of `0x22` is not yet known.

## Free-text data report

Command `0x02` transfers text used by the Time and Date free-text widget.
The captured `NEXLINQ` report uses:

| Offset | Size | Meaning |
|------:|-----:|---------|
| 0 | 1 byte | HID report ID `0x01` |
| 1 | 1 byte | Command `0x02` |
| 5 | 1 byte | Observed text length plus one |
| 6 | 1 byte | Observed value `0x00` |
| 7 | variable | ASCII text |
| remaining text field | variable | Filled with `0xff` |

Linux hardware testing confirmed that command `0x03` immediately applies a
System Data source change. Command `0x05` was not required for that path.
Free text still requires a separate live validation of the `0x02`, `0x03` sequence.

## Digital-clock widget

The fullscreen digital clock uses:

- Internal layout ID `0x01`
- Time and Date category `0x01`
- Widget ID `0x01`

The current implementation reproduces the complete option sequence observed
in the Windows capture. Individual option meanings are not yet assigned until
they are validated through controlled one-setting-at-a-time captures.

### Verified fullscreen Time and Date profiles

| Widget | ID | Linux command |
|--------|---:|---------------|
| Digital clock | `0x01` | `nextuxlinqctl time-widget digital` |
| Graphical clock | `0x02` | `nextuxlinqctl time-widget graphical` |
| Date | `0x03` | `nextuxlinqctl time-widget date` |
| Week number | `0x04` | `nextuxlinqctl time-widget week` |

The complete option bytes are reproduced from the sequential Windows capture.
Their individual meanings remain intentionally undocumented until controlled
one-option differential captures confirm them.

## Multi-slot configuration

The layout capture confirmed that each slot is transferred in a separate
command `0x03` report. All reports carry the same layout ID and total slot
count, while offset 4 selects the zero-based destination slot.

The captured Time and Date profile bytes remain unchanged when the same widget
is assigned to slot 0 of different layouts.

### Week-number behavior

During Linux validation on 2026-07-27, the date widget displayed `27` while
the week-number widget displayed `30`. The ISO week for that date is `31`.
The firmware therefore appears to use a non-ISO, zero-based week counter.

## User-facing layout mapping

| NexLinq layout | Internal ID | Slots |
|---------------:|------------:|------:|
| 1 | `0x02` | 2 |
| 2 | `0x03` | 3 |
| 3 | `0x04` | 4 |
| 4 | `0x05` | 4 |
| 5 | `0x06` | 5 |
| 6 | `0x07` | 5 |
| 7 | `0x08` | 5 |
| 8 | `0x09` | 6 |
| 9 | `0x0a` | 3 |
| 10 | `0x0b` | 4 |
| 11 | `0x0c` | 3 |
| 12 | `0x01` | 1 |

Linux testing confirmed layout 1 with a digital clock in slot 0 and the GPU
System Data widget in slot 1. The display rendered the GPU source as `G51`,
confirming both the source selector and live telemetry path.

## Verified System Data profiles

| Profile | Widget ID | Source behavior |
|---------|----------:|-----------------|
| Variant one | `0x00` | CPU, GPU, or FAN verified |
| Single-value bar | `0x01` | CPU and GPU sources verified |
| Single-value background bar | `0x03` | CPU and GPU sources verified |
| Combined overview | `0x04` | Fixed captured profile |
| AIO pump speed | `0x05` | Uses the live RPM telemetry field |
| Single-source graph | `0x06` | CPU or GPU source verified |

Widget ID `0x02` was not emitted by the official application and remains
reserved, hidden, or otherwise unknown.


### Single-source graph validation

Linux hardware testing confirmed that widget ID `0x06` displays one selected
source as an animated graph. Source `0x01` renders CPU with a `C` marker and
source `0x02` renders GPU with a `G` marker. FAN source `0x04` has not yet
been validated for this widget.


### Source-selectable bar validation

Linux hardware testing confirmed that widget ID `0x01` displays one selected
value with a bar and widget ID `0x03` displays one selected value with a
background bar. Offset 9 selects CPU with `0x01` or GPU with `0x02`.
Both widgets changed their visible marker from `C` to `G` when only this source
byte was changed. FAN source `0x04` remains unverified for these profiles.


### Profile-dependent FAN source behavior

Linux hardware testing showed that source `0x04` is not universally supported.
Widget `0x03` displayed the FAN source correctly with its background bar.
Widget `0x01` fell back to CPU, while widget `0x06` displayed a `C` marker
without its normal graph. FAN support is therefore enabled only for the
compact variant-one profile and the background-bar profile.

### Linux RPM source mapping

The verified telemetry report contains one generic two-byte RPM field at
offsets 9-10. On the development system:

- OpenLinkHub reports six case fans at approximately 952-958 RPM.
- `nct6798/fan6` reports approximately 2450-2470 RPM and is treated as the
  AIO pump source.

The Linux telemetry command therefore allows either the average case-fan RPM
or the pump RPM to populate this single protocol field.
