# nextuxlinqd systemd user service

NexTuxLinq can install its Matrix900 controller as a systemd user service. The
service starts after login, owns all NexTuxLinq Matrix900 writes, and serves the
loopback-only GUI at <http://127.0.0.1:8765>.

Only the Phanteks Matrix900 is supported. This is project-provided user-service
integration, not a distro package or public system installer, and it never
uses `sudo` or installs a system-wide unit.

## Install

Run the command from the Python environment that should run the daemon:

```console
nextuxlinqctl service install
```

The command writes
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/nextuxlinqd.service` atomically,
using the current Python interpreter in `ExecStart`. It refuses to replace a
foreign unit. Use `--force` only after reviewing the existing file:

```console
nextuxlinqctl service install --force
```

Install and uninstall operations are serialized with the private
`.nextuxlinq-service-manager.lock` file in the validated user-unit directory.
The lock is non-blocking: a concurrent NexTuxLinq service-management command
fails clearly and can be retried after the first command finishes. This lock
protects cooperating NexTuxLinq processes and is separate from the Matrix900
writer lock.

When replacing an existing managed unit, NexTuxLinq retains the previous unit
as a private mode-`0600` backup in the same directory. Its unpredictable
filename ends in `.bak`, not `.service`, so systemd does not recognize it as a
unit. Backups are not deleted automatically. Retention also avoids deleting a
file that another same-UID process substituted during an operation.

Installation reloads the user manager, disables and stops the renamed
`nextuxlinq-display.service` and `nextuxlinq-telemetry.service` helpers, and
also disables and stops their pre-rename `nexlinq-*` counterparts. Missing
helpers are harmless. The installer then enables and starts
`nextuxlinqd.service`.

The repository file `systemd/nextuxlinqd.service` is a generic example. The
CLI-generated unit is the installed form and records the actual interpreter;
the example does not depend on the local development checkout.

## Control and status

```console
nextuxlinqctl service start
nextuxlinqctl service stop
nextuxlinqctl service restart
nextuxlinqctl service status
```

View journal output with:

```console
journalctl --user -u nextuxlinqd.service
```

The GUI remains bound to `127.0.0.1:8765`; the service installer does not make
it remotely accessible.

## Optional OpenLinkHub startup recovery

Some systems exhibit a shared USB/controller hand-off problem after the
NexTuxLinq user service starts. Similar behavior has been observed with other
control software, so it is not necessarily specific to NexTuxLinq. An optional
managed drop-in can wait two seconds after a successful NexTuxLinq service
start and then run `systemctl --user try-restart OpenLinkHub.service`.
`try-restart` restarts OpenLinkHub only when it is already active; it never
starts an inactive service, and a failure is nonfatal.

Enable, inspect, or disable the workaround explicitly:

```console
nextuxlinqctl service openlinkhub-restart enable
nextuxlinqctl service openlinkhub-restart status
nextuxlinqctl service openlinkhub-restart disable
```

These commands manage only
`nextuxlinqd.service.d/openlinkhub-restart.conf` under the user's systemd unit
directory and preserve other drop-ins. Enable and disable perform a user
`daemon-reload`; status performs no systemd command. The default installed unit
does not contain this behavior, and manual `nextuxlinqd --host ...` starts are
unaffected.

This workaround does not add OpenLinkHub or Corsair-device control to
NexTuxLinq. The Phanteks Matrix900 remains the only hardware supported by this
project.

## Uninstall

```console
nextuxlinqctl service uninstall
```

Uninstall stops and disables `nextuxlinqd.service`, moves only a unit carrying
the NexTuxLinq-generated marker to a private mode-`0600` `.bak` file, and
reloads the user manager. The command reports the retained backup path. It
preserves all NexTuxLinq configuration and profiles and does not re-enable any
legacy helper.

The service normally starts automatically after the user logs in. Running a
user manager before login would require distribution-specific lingering
configuration, which NexTuxLinq does not change.
