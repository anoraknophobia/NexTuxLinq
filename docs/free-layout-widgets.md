# Free-layout Clock and Date widgets

Phase 3B implements Clock and Date renderers for the inactive NexTuxLinq
schema-v2 and rendering-v2 foundation. They are available for direct tests and
explicitly constructed local test registries, but they are not registered in
the production widget registry. The active daemon, API, GUI, persistence,
schema-v1 renderer, DeviceWorker, Creator output, and physical Matrix900 upload
path remain unchanged.

The future editor will provide one widget dropdown per region. Production
registry integration and editor exposure remain later work.

## Clock

Clock content uses the injected, timezone-aware render-context wall clock. It
does not read the system clock. The formats are:

- 24-hour `HH:MM`, optionally `HH:MM:SS`;
- 12-hour `h:MM`, optionally `h:MM:SS`.

The 24-hour hour is always zero-padded. The 12-hour hour has no leading zero
and has no AM or PM suffix; both midnight and noon use `12`.

Separators are normally steady. When blinking is enabled, every colon is
visible for the first half of each one-second animation cycle and hidden for
the second half. Hidden separators retain their glyph width and advance, so
digits, measurement, and alignment never move.

`local` preserves the injected wall-clock value and its timezone unchanged. A
named IANA timezone converts that value with the standard library timezone
database before formatting. Invalid or unavailable names produce a controlled,
sanitized failure. Timezone conversion is deterministic for the injected
datetime and performs no network or locale lookup.

## German Date

Date output uses fixed numeric formatting and checked-in German weekday
abbreviations. No process locale or arbitrary `strftime` format participates.
The stable formats are:

| Identifier | Rendered form |
| --- | --- |
| `day_month_full_year` | `DD.MM.YYYY` |
| `day_month_short_year` | `DD.MM.YY` |
| `day_month` | `DD.MM.` |
| `weekday_day_month` | `MO. DD.MM.` |

`day_month_full_year` (`DD.MM.YYYY`) is the default. The fixed weekday labels,
Monday through Sunday, are `MO.`, `DI.`, `MI.`, `DO.`, `FR.`, `SA.`, and
`SO.`. Timezone conversion occurs before the calendar date is formatted.
ISO-style, US-style, compatibility aliases, and user-supplied format strings
are not accepted.

Dates support `error`, `truncate`, and `clip` overflow. A five-pixel-high row
can render at least `DD.MM.` with the compact `3x5` font when its width is
sufficient.

## Fonts, fitting, and placement

Both widgets use the Phase 3A `3x5`, legacy-compatible `3x7`, and
legacy-compatible `5x7` font system. An explicit font must exist, cover every
character the configured format can produce, and fit the region height. Clock
selection and Date `error` overflow also preflight the widest possible output,
so a later time, date, or weekday cannot invalidate an otherwise accepted
font.

Automatic selection follows the Phase 3A deterministic priority. Date
`truncate` and `clip` select by height and complete character coverage without
shrinking merely to avoid the selected overflow behavior.

Glyphs always render at their intrinsic bitmap size. They are never scaled,
stretched, or rotated. Horizontal and vertical placement default to centered,
while start/end and top/bottom alignment remain available. Unset glyph pixels
preserve the region's existing background.

## Deferred work

Static Text and Scrolling Text renderers remain deferred. Production registry
integration, schema-v2 activation, API and GUI editing, configuration
persistence cutover, daemon scheduling, and physical upload remain deferred to
later phases.
