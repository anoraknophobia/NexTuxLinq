# Changelog

All notable changes to NexTuxLinq will be documented in this file.

The format is based on Keep a Changelog.

NexTuxLinq has not published a tagged release yet. The package version in
`pyproject.toml` is `0.1.0` and no version numbers are assigned to the entries
below, so this file does not claim adherence to Semantic Versioning. Entries
are grouped as unreleased work and as dated development milestones taken from
the Git history.

Scope note: every entry below concerns the Phanteks Matrix900 only. No other
NexLinq display, hub, or accessory is supported or implied, and NexTuxLinq is
an independent community project with no Phanteks affiliation.

## [Unreleased]

Work committed after the repository was prepared for public publication
(`bcfa3b9`, 2026-08-01). Nothing here has been released under a version number.

### Added

- Automated per-user installation, update, and removal tooling: `install.sh`,
  `update.sh`, `uninstall.sh`, and the shared `scripts/installer` helpers.
  Releases are built as a wheel into an isolated per-user virtual environment
  under `~/.local/share/nextuxlinq/releases/` and selected through atomic
  `current` and `previous` symlinks. The installer must not be run with `sudo`;
  it requests narrowly scoped sudo access only to install the checked-in
  Matrix900 udev rule and reload udev, and `--no-udev` skips every sudo
  operation. Uninstalling removes managed releases, launchers, and an unchanged
  managed udev rule while preserving configuration, profiles, backups, exports,
  state, and cache (`5cbfa40`).
- Duplicate framebuffer suppression in `DeviceWorker`. The worker compares the
  final 2700-byte device framebuffer against the last frame it transmitted
  completely and successfully; an identical frame sends zero HID reports and a
  changed frame sends one complete, unmodified 51-report Creator transaction.
  The baseline is worker-private and is invalidated on start, stop, device open
  and open failure, discovery failure, disconnect, transaction failure or
  timeout, and every brightness command, so a reconnect always replays the
  latest frame in full. Diagnostics gained `frame_submissions_received`,
  `duplicate_frames_skipped`, `frame_transactions_failed`, and
  `last_frame_transmission_monotonic`; no framebuffer content is exposed
  (`f2e9c3f`).
- Full Python traceback logging for daemon rendering failures. The renderer
  previously reported a fixed message only, so the originating exception never
  reached the journal. The traceback is now logged at the rendering-failure
  boundary in both the exception path and the cancellation path, where the
  renderer's own exception is read back from the finished render task, so a
  runtime rendering failure is diagnosable from the journal. Failure handling,
  exit codes, daemon shutdown and restart semantics, and the sanitized HTTP and
  GUI status surfaces are unchanged (`ba0abbc`).
- A `degraded` rendering state, published with a sanitized `last_display_error`
  while the last accepted frame is being retained, and cleared automatically
  when rendering resumes (`e352994`).

### Changed

- Bounded the time one Matrix900 Creator transaction may occupy the device
  worker. A complete transaction is 51 reports, and each echo previously used a
  fixed one-second transport timeout with no overall limit, so a device that
  stopped echoing could hold the worker for roughly 51 seconds while no frame,
  brightness command, or stop request was processed — which ended in daemon
  exit and a systemd restart loop. One monotonic deadline is now established
  before the first report (`CREATOR_REPORT_TIMEOUT_SECONDS = 0.5`,
  `CREATOR_TRANSACTION_DEADLINE_SECONDS = 5.0`), the stop event is checked
  before every report, and any timeout or malformed echo aborts the remaining
  reports so the activation report is never sent after an earlier failure.
  Report bytes, ordering, count, and activation behavior are unchanged. Both
  constants are conservative engineering defaults derived from the report count
  and the one-millisecond endpoint interval; they were not measured against
  physical hardware (`b847f19`).
- Stabilized live system metrics behind a single shared snapshot. CPU and
  network metrics are deltas against the previous sample, and rendering,
  preview, and validation each sampled independently, so an open editor
  validating eight profiles every 500 ms compressed the measured interval to a
  few milliseconds and turned the displayed CPU value into noise. The new
  `MetricsSnapshotCoordinator` owns the stateful provider: `sample_if_due()` is
  the only entry point that may advance provider state and samples at most once
  per 0.5 s, `current_snapshot()` never advances counters, and one lock
  prevents interleaved partial samples. Apply, save, validate, and preview each
  resolve one shared snapshot per operation. Structural validation of inactive
  profiles is unchanged (`048b925`).
- Cached the immutable rendering font registries with
  `functools.lru_cache(maxsize=1)`. Every text-bearing widget previously
  rebuilt all six native bitmap fonts from their checked-in fixtures on each
  render; profiling one render of the default profile showed 720 `GlyphBitmap`
  constructions at roughly 78 percent of the render cost. Measured on the
  default profile, one render went from 11.49 ms to 2.56 ms. Glyph contents,
  font-selection priority, Auto fitting, and rendered framebuffer bytes are
  unchanged (`0faa940`).
- `CandidateValidationError` now carries its sanitized category, region,
  widget, and field in the message so the journal identifies the cause directly
  (`e352994`).
- Routed the `DeviceWorker` submission counter through the guarded
  `_replace_snapshot_locked` helper so every snapshot mutation uses one path
  (`52e6fec`).

### Fixed

- Content-dependent render failures of an already-accepted profile no longer
  terminate the daemon. System Metric font selection is snapshot-independent
  and deliberately sizes regions for the stable two-digit format envelope, so a
  documented full-scale value such as `100%` or a three-digit temperature can
  measure wider than its region and report `content_too_wide`. The render loop
  let that per-frame `CandidateValidationError` escape, which stopped the
  renderer, exited the daemon, and produced a systemd restart loop as soon as
  CPU or GPU usage reached 100%. Candidate validation and steady-state
  rendering are now separate: once a profile has rendered one complete frame, a
  later content-dependent failure of that accepted profile submits nothing for
  that cadence step, leaves the previously accepted last known-good framebuffer
  on the display, publishes `rendering_state = degraded` with a sanitized
  error, and returns to normal on the next value that composes. The cadence is
  unchanged, so there is no retry loop, and each category is reported to the
  journal once per run. Content-dependent means `content_too_wide`,
  `content_too_tall`, `unsupported_glyph`, `time_zone_unavailable`, and
  `date_format_unavailable` only. Every other category means the profile or the
  renderer is broken and still stops the renderer and the process; a profile
  that cannot render its first frame is still rejected at startup, and applying
  or saving an invalid candidate is still refused (`e352994`).
- Installer temporary paths and update rollback. Build directories honoured
  `TMPDIR` through a bare `mktemp -d` while cleanup required the result to live
  under `/tmp`, so on a system with `TMPDIR` set elsewhere a completely
  successful installation ended in the exit trap with an unsafe-cleanup refusal
  and a non-zero status. Build directories are now created below a fixed
  project-owned base with an explicit absolute `mktemp` template, cleanup
  validates that base, refuses symlinks and foreign paths, and always returns
  success with a warning so a cleanup problem cannot fail a successful install.
  The one remaining unquoted path expansion in `build_release` broke on paths
  containing a space. `update.sh` had no rollback for an interruption after the
  `current` symlink was switched; an explicit update-state flag now drives
  idempotent rollback that restores the current release, the previous release
  pointer, the prior service enabled and active state, and the prior
  NexTuxLinq-owned OpenLinkHub option, removes only the new release, and never
  touches OpenLinkHub itself (`a049563`).
- Added the explicit `ERR` trap the `update.sh` rollback design calls for, so a
  failing command rolls back at the point of failure rather than only when the
  shell exits, and bounded the sandbox-only pause loop (`52e6fec`).

### Documentation

- Corrected `docs/protocol.md`, which still claimed the protocol had not been
  reverse engineered and that no commands had been transmitted, while listing
  pixel format, ordering, orientation, and frame transfer as unknown. The
  Creator framebuffer path is now documented: 90x10 RGB888, 2700 bytes, HID
  report ID `0x01`, 64-byte reports, one initialization report, 49 data chunks
  with their offsets and length markers, the activation report that commits the
  frame, echo behavior, the 51-report complete-transaction rule, and logical
  versus device orientation. Every section is labelled **verified**,
  **inferred**, or **unknown**; the byte layouts are transcribed from the
  shipping implementation rather than re-verified against hardware, and what
  remains unknown — including whether partial or changed-chunk uploads would be
  accepted — says so. Reverse engineering remains partial (`c15e6de`,
  `52e6fec`).
- Corrected the README, which claimed the implementation could only detect a
  device, set brightness, and print a response report, omitting the entire
  renderer, and which contradicted itself on OpenLinkHub. The daemon does not
  query OpenLinkHub; only the legacy developer command `nextuxlinqctl
  telemetry` does, and both places now say so. The claim that no udev rules are
  installed, which `install.sh` contradicts, was removed. OpenLinkHub wording
  across the README, installation, and systemd docs now states plainly that the
  post-start restart option is a manually enabled workaround, that it briefly
  interrupts OpenLinkHub, that the two programs use different USB device IDs
  and `hidraw` paths, and that OpenLinkHub failures were also observed while
  `nextuxlinqd` was inactive, so causality is not established (`c15e6de`).
- Documented that installation requires PyPI access while no shell code is
  downloaded, that the Git checkout is not needed to run NexTuxLinq but is
  still needed for `./update.sh`, and that a real VM or fresh-user installation
  test remains recommended (`c15e6de`).
- Added the AI-assisted development disclosure to the README (`ba9443d`) and an
  author identity `.mailmap` (`06ce640`).
- Added the Rainbow showcase image and settled its README embedding across a
  series of small documentation commits (`153c6cf` through `a3fe613`).

## Development history

Dated milestones reconstructed from the Git history, from the first commit
(`e6bc7b5`, 2026-07-27) through the public repository preparation (`bcfa3b9`,
2026-08-01). These are development milestones, not releases: no tag or version
number was ever assigned to them.

### 2026-07-27 — Matrix900 discovery, captured profiles, and the first daemon

- Established Matrix900 USB/HID discovery and a `hidraw` transport. The
  identified hardware is USB vendor ID `2e3c`, product ID `6500`, a
  vendor-defined USB HID interface, HID report ID `0x01`, 64-byte reports
  including the report ID, interrupt OUT endpoint `0x01`, and interrupt IN
  endpoint `0x82`. The initial commit also added the checked-in udev rule that
  grants the Matrix900 `uaccess` so the display can be controlled without root
  privileges (`e6bc7b5`).
- Documented the verified Matrix900 USB interface in `docs/protocol.md`,
  including the descriptor details, observed idle behavior, and explicit
  reverse-engineering rules: capture the official application first, change one
  setting per capture, and never send random, firmware, or bootloader commands
  (`c188e7a`).
- Implemented verified brightness support end to end — encoding, the report
  builder, a validated `hidraw` report writer, and the `nextuxlinqctl
  brightness` command covering 10% through 100% in ten-percent steps
  (`a211bb1`, `b1c0ee7`, `5148971`, `ebf01c8`).
- Added the Linux hardware telemetry command and its user service. This path
  predates the software renderer, writes to `hidraw` directly instead of going
  through the daemon, and its sensor mapping is hardwired to the original
  development machine (`k10temp`/`Tctl`, `amdgpu`/`edge`, and the highest RPM
  from `nct6798`). It is retained only as a legacy developer command and is not
  used by the daemon or the web GUI (`78a0884`, `ca328d6`).
- Added an optional OpenLinkHub RPM source to that legacy telemetry command,
  which reads active non-AIO case fans from OpenLinkHub's local HTTP API and
  sends their rounded average. It reads values only. This is the only place in
  the project that contacts OpenLinkHub; the daemon has no OpenLinkHub
  dependency (`428155f`).
- Added the widget configuration encoder and reproduced the captured NexLinq
  widget and layout profiles: fullscreen free text, the fullscreen digital
  clock, the remaining Time and Date widgets, a two-slot clock-and-System-Data
  preset, a generic multi-slot layout builder, and the remaining System Data
  widgets with graph and bar source handling (`d050265`, `2da685f`, `c3f46c7`,
  `3d80388`, `5bdb218`, `6dcf1a4`, `8cebe21`, `22cf091`, `1fed320`). Layout
  geometry and slot counts are verified from the Windows layout capture; source
  selection is verified for System Data variant one, and not every widget and
  layout combination has been validated on hardware.
- Added the hardware-verified FAN background bar profile. FAN remains
  unverified for the regular value bar and the animated single-source graph
  (`fed452a`).
- Introduced the Creator framebuffer protocol and the first software-rendered
  dynamic display: a complete 90x10 RGB888 frame composed locally and uploaded
  through the Creator path, with the Creator-versus-physical coordinate
  reversal hidden behind a physical-coordinate API. Two follow-ups fixed
  mirrored content and matched the front clock height (`d174402`, `c564ee5`,
  `5faf974`).
- Added the configurable rendering foundation: validated display profiles,
  versioned JSON configuration written atomically under the then-current
  `$XDG_CONFIG_HOME/nexlinq/config.json`, and a fallback to the built-in
  display that never overwrites an invalid file (`37a1aca`).
- Added the central daemon core `nextuxlinqd`, the `DeviceWorker` sole-writer
  model for all daemon-originated HID writes, the application writer lock that
  rejects a manual and a managed daemon running together, and the published
  daemon state (`3cc1ca4`).
- Added the local loopback-only web interface served by the daemon (`fe43776`).

### 2026-07-29 — Project rename, free-layout schema, and the visual editor

- Improved web GUI resilience and layout, then simplified status presentation
  and hardened GUI state correctness, adding frontend state tests that run on
  the built-in `node:test` runner. Node.js is a development test dependency
  only, not a runtime dependency (`eb1c936`, `8929d85`, `a0fa7c4`).
- Renamed the project from `nexlinq` to **NexTuxLinq**, moving the Python
  package, the udev rule, the systemd units, and all documentation (`f5e3e29`).
- Added `nextuxlinqctl service` user-service management and the
  `nextuxlinqd.service` user unit. Installation writes the unit atomically with
  the current interpreter in `ExecStart`, refuses to replace a foreign unit,
  stops and disables the older display and telemetry helpers so `nextuxlinqd`
  is the sole project writer, serializes install and uninstall behind a private
  non-blocking manager lock that is separate from the Matrix900 writer lock,
  and retains a replaced managed unit as a private `.bak` file that systemd
  does not recognize and NexTuxLinq never deletes automatically (`d6c90ec`).
- Introduced the canonical free-layout schema v2 on the fixed 90×10 canvas:
  ordered absolute rectangles with stable structural region IDs,
  non-intersecting geometry, bounded region counts, and deterministic
  in-memory migration of valid schema-v1 files without an automatic rewrite
  (`972cc17`).
- Added the free-layout region compositor, region surfaces, and the widget
  registry that composes regions into the complete logical framebuffer
  (`bc349cc`).
- Added the native bitmap font foundation with the checked-in 3×5, 3×7, and 5×7
  pixel fixtures and an `auto` selection request. Glyphs are never scaled or
  stretched (`e1d1d20`).
- Added the free-layout Clock and Date widgets, then the Static Text and
  Scrolling Text widgets, and integrated them through the registry with the
  aggregate update policy (`7ed396b`, `7b883f6`, `bd189ee`).
- Switched the web preview to the viewer perspective (Rear left, Front right)
  while stored coordinates and glyph pixels remain logical and unmirrored
  (`db68489`).
- Activated the free-layout backend as the live rendering path, adding the
  single continuous scheduler and the schema-v1 compatibility layer (`a6a4219`).
- Added the visual free-layout editor: regions can be added, moved, resized,
  reordered, and assigned a widget, with **Apply** validating and updating the
  running profile and **Save applied configuration** persisting only the
  already-applied state. Browser drafts and revision conflicts stay explicit
  (`5a587f8`). Follow-ups fixed schema-migration saving and region selection
  and made selected regions toggleable (`2cf581f`, `4aaf20b`).

### 2026-08-01 — Profiles, live metrics, fonts, effects, and public preparation

- Added profile presets, JSON import and export, and managed backups. Import,
  export, preset preview, and backup restore never persist a configuration or
  upload a physical frame until the normal Apply and Save workflow, and saving
  an imported full configuration first backs up the previously saved one
  (`06bc064`).
- Added the live **System Metric** and **Metric Bar** widgets together with
  the read-only Linux metric provider.
  Metrics are read from fixed procfs interfaces, root-filesystem statistics,
  and generic DRM, thermal, and hwmon sysfs interfaces; the provider runs no
  shell command, subprocess, vendor utility, network request, D-Bus call, or
  dynamic import, and configuration cannot supply paths (`598c985`).
- Expanded live telemetry to load average, fixed-root storage usage, network
  receive and transmit rates, and kernel-exposed GPU, VRAM, and temperature
  values. Sensor availability depends on the running kernel driver; no
  vendor-specific GPU support is claimed (`20fff88`).
- Stabilized System Metric font sizing against the stable format envelope,
  fixed metric number layout and preview artifacts, and fixed a metric startup
  rendering regression (`7d02f11`, `6da4420`, `d88ee21`).
- Improved editor color controls and added the optional, explicitly disabled
  post-start drop-in that `try-restart`s an already-active `OpenLinkHub.service`
  after login. It does not affect manual daemon starts, failure is nonfatal,
  and it adds no Corsair fan or RGB control (`1879f96`).
- Explained invalid region geometry in the editor instead of failing opaquely
  (`0a22ac4`).
- Added the larger native pixel fonts 4×9, 5×9, and 5×10 and redefined `auto`
  as *largest fitting native font* with the explicit order 5×10, 5×9, 4×9, 5×7,
  3×7, 3×5. The existing 3×7 and 5×7 fixtures remain byte-identical, and a
  25×10 `HH:MM` Clock now resolves to the true 5×10 font without scaling
  (`0cc4f6f`). Automatic font fitting guidance and backend font diagnostics
  followed (`789147c`).
- Finalized user-service autostart integration: the generated unit binds only
  to `127.0.0.1:8765`, uses the canonical
  `${XDG_CONFIG_HOME:-~/.config}/nextuxlinq/config.json` and the absolute
  interpreter of the installing environment, and uses `Restart=on-failure` with
  a three-second delay and a five-starts-per-minute limit so a transient
  failure recovers without a restart storm. `service enable`, `disable`,
  `status`, and `logs` were added, status reports a sanitized last-failure
  summary, and the web API exposes no service-control or arbitrary `systemctl`
  operation (`debd424`).
- Added compact temperature unit formatting such as `CPU43°C` (`114f0c0`) and
  clarified system metric labels (`be16e69`).
- Added the global **Foreground Rainbow** display effect: one continuous
  integer RGB888 color field across all 90 physical columns, applied after
  every region has rendered and before the logical-to-Creator conversion, so
  the preview and the Matrix900 derive from the same frame. It recolors only
  semantic widget foreground pixels and preserves canvas and region
  backgrounds and empty Metric Bar pixels. Direction is Rear → Front or
  Front → Rear, speed is in cycles per second, and speed zero is static. Widget
  colors remain stored and return when the effect is disabled (`09cc6e4`).
- Added the built-in icon widget with a checked-in, unscaled 15×10 CachyOS logo
  whose active pixels always use CachyOS Turquoise (`#1DC7B5`) and which
  display effects never recolor, plus the backend-rendered Content preview for
  every widget in the selected-region inspector. Previewing never applies,
  saves, persists, or uploads a frame. The pixel adaptation claims no CachyOS
  affiliation or endorsement (`127ea6d`).
- Prepared the repository for public GitHub publication: expanded `.gitignore`
  to exclude local tool, editor, runtime-state, and user-configuration
  artifacts, and narrowed the package description to the Phanteks Matrix900
  display (`bcfa3b9`).
