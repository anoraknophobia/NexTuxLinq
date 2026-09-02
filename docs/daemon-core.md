# Matrix900 service core

`nextuxlinqd` is the foreground local controller for the Phanteks Matrix900. It
owns discovery, rendering, brightness updates, reconnects, all
daemon-originated HID writes, and the local web application.

Run it from a development checkout:

```console
python -m nextuxlinq.daemon --foreground --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765> after the process starts. The process does not
daemonize itself or open a browser. Install and control it as a systemd user
service with `nextuxlinqctl service`; see [`systemd.md`](systemd.md).

The HTTP server accepts loopback addresses only. It has no account or
authentication because it is local-only, but other processes running as the
same machine user can still connect to loopback. Do not treat loopback as a
security boundary between local processes.

Startup waits for one complete sanitized render before starting the HTTP
server lifespan. If that initial render fails, no partial framebuffer is
submitted, the renderer's generic status remains observable, worker cleanup is
awaited, and the HTTP lifespan is never started. A later renderer failure asks
the HTTP server to shut down and consumes expected background-task
cancellation so it cannot obscure the original sanitized rendering status.
The journal additionally records the complete Python traceback of a renderer
failure so the root cause is diagnosable; the HTTP and GUI status surfaces
stay sanitized.

The service keeps canonical schema-v2 configuration in memory. Valid schema-v1
files migrate deterministically in memory without an automatic rewrite. The
GUI's **Apply** action changes only the running configuration. **Save applied
configuration** atomically writes that already-applied schema-v2
configuration without applying a different browser draft.
Configuration is stored at:

```text
${XDG_CONFIG_HOME:-~/.config}/nextuxlinq/config.json
```

When the canonical file is absent, NexTuxLinq can load the pre-rename file at
`${XDG_CONFIG_HOME:-~/.config}/nexlinq/config.json`. The GUI surfaces a fixed
migration warning. The next successful **Save applied configuration** writes the
canonical file with the existing atomic-save and permission guarantees. The
legacy file is never deleted automatically, and it is never preferred over an
existing canonical file. Migration reads accept only a bounded, privately
owned regular file from a private legacy directory; symlinks, non-regular
files, unsafe permissions, and oversized input are rejected without exposing
filesystem details in the warning.

When an explicit save first replaces a canonical schema-v1 file, a private
unpredictably named rollback backup is synchronized beside it before the
replacement. A failed validation or pre-commit write leaves the original
intact.

The GUI tracks three related states explicitly:

- a local draft contains browser edits that have not been applied;
- an applied, unsaved runtime configuration differs from the persisted file;
- a synchronized configuration is both applied and saved.

The API `dirty` value is authoritative for the applied-versus-saved state.
**Discard draft** restores the current runtime configuration and discards only
local browser edits. It never claims to restore the persisted configuration.
Apply and Save follow immediate local checks and authoritative complete
candidate validation.

After a daemon reconnect, the GUI compares revisions, the active profile,
`dirty`, and a structural configuration fingerprint. An unchanged runtime
keeps any local draft without interruption. If the runtime changed, the draft
remains visible in a revision-conflict state until the user explicitly
discards it and reloads the newer applied state. The editor never fabricates a
merge or overwrites the newer revision.

Frontend transition and minimal DOM tests use Node.js 20 or newer through
Python's normal pytest suite. They use only built-in Node modules; Node.js is
not needed to run the daemon or packaged web GUI.

The versioned local API is available below `/api/v1`. Canonical schema-v2
state, validation, apply, save, and profile selection are under
`/api/v1/free-layout`; validation has no persistence or upload side effect.
The visual editor uses these canonical endpoints. Existing legacy-shaped
endpoints remain available as a compatibility surface and still refuse to
flatten profiles they cannot represent. See
[`free-layout-editor.md`](free-layout-editor.md).

Runtime and persisted configuration use independent monotonic revisions.
`POST /api/v1/apply` checks and advances the active revision only.
`PUT /api/v1/config` carries both `expected_active_revision` and
`expected_saved_revision`. Every save checks the saved revision. A save with
`apply=true` also checks and advances the active revision; a save with
`apply=false` ignores the expected active revision and never changes the
running profile. Every committed save advances the saved revision.

## Single-writer coordination

NexTuxLinq writers use an advisory lock at:

```text
${XDG_RUNTIME_DIR}/nextuxlinq/matrix900.lock
```

As temporary rename-migration compatibility, every NexTuxLinq writer also
acquires the pre-rename lock at
`${XDG_RUNTIME_DIR}/nexlinq/matrix900.lock`. Locks are always acquired in the
fixed order **legacy first, canonical second** and released in reverse order.
Failure to acquire either lock refuses the write and rolls back the other
lock. Existing legacy lock files are not rewritten or deleted.

If `XDG_RUNTIME_DIR` is missing or relative, a private directory named for the
current user ID under the system temporary directory is used for each
namespace. Direct `nextuxlinqctl` mutations and the dynamic display acquire the
same dual locks and refuse to run while `nextuxlinqd` owns them.

Before starting NexTuxLinq, stop and disable any installed
`nexlinq-display.service` and `nexlinq-telemetry.service`. The dual locks guard
the transition, but disabling the obsolete services avoids repeated restart
failures and is required migration hygiene.

This lock coordinates software from this project. It is not a kernel-level
exclusive claim on hidraw and cannot prevent unrelated third-party software
from opening the device.

The service remains running without a connected Matrix900 and retries
discovery with bounded backoff. When the device reconnects, the newest frame
and an explicitly selected brightness value are reapplied. A `null`
brightness leaves the device brightness unchanged.

Worker shutdown is event-based and takes priority over queued work; there is
no queued stop command. Pending frames and brightness values each use a
bounded latest-wins slot. When both classes are pending, the worker alternates
them so repeated brightness changes cannot indefinitely starve a frame.

Only the Phanteks Matrix900 with USB VID:PID `2e3c:6500` is supported.

The installed systemd user unit is `nextuxlinqd.service`. It conflicts with the
legacy and renamed display/telemetry helpers so the daemon remains the sole
NexTuxLinq Matrix900 owner.
