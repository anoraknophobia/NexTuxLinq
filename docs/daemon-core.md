# Matrix900 service core

`nextuxlinqd` is the foreground local controller for the Phanteks Matrix900. It
owns discovery, rendering, brightness updates, reconnects, all
daemon-originated HID writes, and the local web application.

Run it from a development checkout:

```console
python -m nextuxlinq.daemon --foreground --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765> after the process starts. The process does not
daemonize itself, invoke systemd, or open a browser. Phase 2B does not install
or migrate systemd services.

The HTTP server accepts loopback addresses only. It has no account or
authentication because it is local-only, but other processes running as the
same machine user can still connect to loopback. Do not treat loopback as a
security boundary between local processes.

The GUI's **Apply** action changes only the running configuration. **Save
configuration** atomically writes the complete configuration and applies it.
Configuration is stored at:

```text
${XDG_CONFIG_HOME:-~/.config}/nextuxlinq/config.json
```

When the canonical file is absent, NexTuxLinq can load the pre-rename file at
`${XDG_CONFIG_HOME:-~/.config}/nexlinq/config.json`. The GUI surfaces a fixed
migration warning. The next successful **Save configuration** writes the
canonical file with the existing atomic-save and permission guarantees. The
legacy file is never deleted automatically, and it is never preferred over an
existing canonical file. Migration reads accept only a bounded, privately
owned regular file from a private legacy directory; symlinks, non-regular
files, unsafe permissions, and oversized input are rejected without exposing
filesystem details in the warning.

The versioned local API is available below `/api/v1`. It is intended for the
packaged GUI and future local clients, not remote access.

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

The future daemon systemd unit name is reserved as `nextuxlinqd.service`.
Daemon systemd integration is not included in this phase.
