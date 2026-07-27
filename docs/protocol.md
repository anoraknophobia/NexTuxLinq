# Matrix900 USB Protocol Notes

## Status

The Matrix900 USB protocol has not been reverse-engineered yet.
This document separates verified observations from assumptions and open questions.

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
- No protocol commands have been transmitted by this project yet.

## Unknown protocol details

- Command identifiers
- Packet headers and sequence numbers
- Checksums or integrity fields
- Device acknowledgement format
- Display power command
- Brightness command
- Pixel format and color depth
- Pixel ordering and display orientation
- Image transfer and frame segmentation
- Text and widget representation
- Animation storage and playback
- Persistent device storage behavior

## Reverse-engineering rules

- Capture traffic from the official NexLinq application before sending commands from Linux.
- Change only one setting per capture.
- Record the display state before and after every test.
- Do not send random or partially understood packets.
- Do not test firmware, bootloader, reset, or update commands.
- Keep original packet captures unchanged and document their provenance.
- Remove serial numbers and other machine-specific identifiers before publishing captures.

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
