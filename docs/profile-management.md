# Profile management, transfer, presets, and backups

NexTuxLinq supports only the Phanteks Matrix900. Its canonical configuration is
schema v2 on a fixed 90×10 integer-pixel canvas. Logical coordinates are stored
unchanged; only browser previews transform them into the viewer perspective,
with Rear on the left and Front on the right. Text and glyphs are never
mirrored.

## Draft, Apply, and Save

Profile management, preset selection, import, and restore first change only the
browser's local draft. **Apply** revision-checks the complete draft and changes
the running configuration. **Save** separately revision-checks and atomically
persists the applied configuration. Conflicts preserve the local draft and are
never merged automatically. Navigation, refresh, and profile switching warn
when a meaningful local draft exists.

Import, export, preset preview or selection, and backup restore never persist a
configuration or upload a physical frame. Saving an imported full configuration
first creates a managed backup of the previously saved configuration.

## Built-in presets

The immutable preset library contains:

- Empty Layout
- Dual Clock
- Clock + Scrolling Text + Clock
- Two-row Information
- Date + Static Text
- Full-width Scrolling Text
- System Monitor
- System Bars
- Extended System Monitor
- Temperature Monitor

Every preset is a normal schema-v2 profile and is validated and rendered by the
same registry and compositor as user profiles. Selecting a preset previews it;
creating a profile from it makes an independent local draft copy.

## JSON export and import

Single-profile exports use UTF-8 JSON format `nextuxlinq-profile`, format
version `1`, and NexTuxLinq schema version `2`. The envelope contains the
canonical profile name and complete profile data, including canvas background,
profile-level display effect, ordered regions, exact logical geometry, widget
types, and widget settings. It
contains no revision, runtime, device, service, or filesystem data.

Complete exports are canonical schema-v2 documents named
`nextuxlinq-config-v2.json`. Canonical profile ordering, the active profile,
brightness, canvas settings, regions, and widget settings are preserved.
Profile-level display effects are preserved as well; old exports without the
field remain valid and resolve to Off.
System Metric and Metric Bar settings use the existing transfer format version
and round-trip exactly; unknown metric identifiers and invalid bar ranges fail
strict validation. Managed backups preserve and validate them identically.

Imports accept JSON files up to 256 KiB. The backend strictly decodes base64
transport data, UTF-8 and JSON, rejects duplicate JSON keys, validates the
transfer version and schema, then runs schema, geometry, limits, widget, and
full compositor validation. Client-provided paths are never accepted. Profile
imports receive collision-safe local names and cannot overwrite profiles.
Complete configuration imports require explicit replacement confirmation and
do not merge with newer server state.

## Managed backups

Named user backups contain the last successfully saved canonical configuration.
They are stored atomically with private permissions in the fixed NexTuxLinq
managed-backup directory. The UI and API expose identifiers and metadata, never
absolute paths. Restore validates the complete document and returns it only as
a local draft. Delete accepts only generated backup identifiers and cannot
address the active configuration or another file.

At most 10 user-managed backups are retained. This retention never includes or
automatically deletes the one-time original schema-v1 migration backup.

All transfer and backup content is JSON data only. NexTuxLinq does not use
Python object serialization, executable content, dynamic imports, commands, or
arbitrary filesystem paths for these operations.
