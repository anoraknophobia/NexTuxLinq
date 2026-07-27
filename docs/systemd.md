# Automatic Telemetry Service

The optional systemd user service continuously sends Linux hardware telemetry to the Phanteks Matrix900.

## Installation

```console
mkdir -p ~/.config/systemd/user
cp systemd/nexlinq-telemetry.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nexlinq-telemetry.service
```

## Status and logs

```console
systemctl --user status nexlinq-telemetry.service
journalctl --user -u nexlinq-telemetry.service
```

## Disable

```console
systemctl --user disable --now nexlinq-telemetry.service
```

The development service expects the repository at `~/nexlinq-linux` with its virtual environment at `.venv`.
