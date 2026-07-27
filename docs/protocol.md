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
