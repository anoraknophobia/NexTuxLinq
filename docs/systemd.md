# Automatic Telemetry Service

The optional systemd user service continuously sends Linux hardware telemetry to the Phanteks Matrix900.

## Installation

Before installing either renamed helper, stop and disable both pre-rename
NexLinq helpers so an old writer cannot run alongside NexTuxLinq:

```console
systemctl --user disable --now nexlinq-display.service nexlinq-telemetry.service
```

The renamed helpers also declare conflicts with both legacy service names and
with each other. This is migration protection in addition to the temporary
dual runtime locks used by NexTuxLinq writers.

```console
mkdir -p ~/.config/systemd/user
cp systemd/nextuxlinq-telemetry.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nextuxlinq-telemetry.service
```

## Status and logs

```console
systemctl --user status nextuxlinq-telemetry.service
journalctl --user -u nextuxlinq-telemetry.service
```

## Disable

```console
systemctl --user disable --now nextuxlinq-telemetry.service
```

The development service expects the repository at `~/nexlinq-linux` with its virtual environment at `.venv`.

These are the renamed pre-existing development helpers. Integration of the
foreground daemon is intentionally deferred; its future unit name is
`nextuxlinqd.service`.
