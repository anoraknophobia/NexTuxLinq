# Matrix900 service core

`nexlinqd` is the foreground service core for the Phanteks Matrix900. In the
current Phase 2A implementation it owns discovery, rendering, brightness
updates, reconnects, and all daemon-originated HID writes.

Run it from a development checkout:

```console
python -m nexlinq.daemon --foreground
```

The process does not daemonize itself, invoke systemd, open a browser, or serve
an HTTP API yet. Web and local API support are intentionally deferred.

## Single-writer coordination

NexLinq writers use an advisory lock at:

```text
${XDG_RUNTIME_DIR}/nexlinq/matrix900.lock
```

If `XDG_RUNTIME_DIR` is missing or relative, a private directory named for the
current user ID under the system temporary directory is used. Direct
`nexlinqctl` mutations and the legacy dynamic display acquire the same lock and
refuse to run while `nexlinqd` owns it.

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
