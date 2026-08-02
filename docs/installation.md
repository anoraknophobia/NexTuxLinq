# Installation, updates, and removal

NexTuxLinq provides an automated installer for Arch Linux, CachyOS, and other
pacman-based Arch derivatives. The application remains Linux software, but the
automated scripts are not claimed as tested on other distribution families.

## Arch Linux and CachyOS

Install the prerequisites if they are missing:

```console
sudo pacman -S --needed bash coreutils git python python-pip sudo systemd
```

Clone and install:

```console
git clone https://github.com/anoraknophobia/NexTuxLinq.git
cd NexTuxLinq
./install.sh
```

Do **not** run the installer with `sudo`. Application code and dependencies are
installed per-user; the installer requests narrowly scoped sudo access only to
install the checked-in Matrix900 udev rule and reload udev. Use `--no-udev` to
skip all sudo operations. `--no-enable`, `--no-start`, `--non-interactive`, and
`--dry-run` are also available; see `./install.sh --help`.

The installer needs **network access**: it builds a wheel from this checkout
and then installs the project's Python dependencies (FastAPI, Uvicorn,
Pydantic, and Jinja2) from PyPI into a private virtual environment. No shell
code is downloaded or executed at any point, and no system package is
installed.

Build directories are created under a fixed `/tmp/nextuxlinq-installer.*`
base with private permissions, independent of `TMPDIR`.

The managed installation lives in
`~/.local/share/nextuxlinq/releases/`. Atomic `current` and `previous` symlinks
select validated releases. Commands are linked from `~/.local/bin`, and the
user unit is installed below `~/.config/systemd/user`. Configuration, profiles,
backups, state, and cache continue to use their existing XDG locations.

The Git checkout is not required to *run* NexTuxLinq after installation.
It is still required to *update*: `./update.sh` builds the new release from
the checkout, and there is currently no installed self-update command. Keep
the checkout if you intend to update in place.

The service listens on loopback and the GUI is available at
<http://127.0.0.1:8765>. Inspect it with:

```console
nextuxlinqctl service status
journalctl --user -u nextuxlinqd.service -n 100 --no-pager
```

The installer does not enable systemd lingering. Users who require startup
before login may configure lingering separately after understanding its system
policy implications.

The udev rule is installed as `/etc/udev/rules.d/70-nexlinq.rules` and is
strictly limited to the Matrix900 USB identity `2e3c:6500`. A checksum is
recorded. Uninstall preserves a subsequently modified or foreign rule.

## Updating and rollback

```console
cd NexTuxLinq
git pull --ff-only
./update.sh
```

Updates build and validate a separate wheel and virtual environment, then
atomically switch `current`. The previous release remains available through
`previous`. Once `current` has been switched, any failure or interruption
rolls the update back: a failed unit installation, a failed service restart, a
startup-health failure, and `SIGINT`, `SIGTERM`, or `SIGHUP` all restore the
previous release, the previous release pointer, the earlier service enabled
and active state, and the earlier NexTuxLinq-owned OpenLinkHub option.
Rollback runs at most once and never removes the known-good previous release.
At most three validated release directories are retained. Configuration and
user data are never copied into or overwritten by a release.

The service is restarted only when it was active before the update. Use
`./update.sh --no-start` to suppress startup and `--dry-run` to inspect the
plan.

The optional OpenLinkHub startup action remains an explicit NexTuxLinq
feature and stays disabled unless you enable it. Installation does not
install, enable, stop, or otherwise manage OpenLinkHub. NexTuxLinq and
OpenLinkHub use different USB device IDs and different `hidraw` paths, and
NexTuxLinq has not been shown to cause OpenLinkHub failures; the action is a
workaround, not a diagnosis. Enable the nonfatal workaround only when needed:

```console
nextuxlinqctl service openlinkhub-restart enable
```

## Uninstalling

```console
./uninstall.sh
```

Default removal stops/disables the NexTuxLinq user service and removes managed
releases, launchers, and an unchanged managed udev rule. It preserves
configuration, profiles, backups, exports, state, and cache. Keep the rule with
`--keep-udev`.

Complete removal is deliberately explicit:

```console
./uninstall.sh --purge
```

Interactive purge requires typing `PURGE`; non-interactive purge requires both
`--non-interactive --yes`. Only recognized NexTuxLinq XDG subdirectories are
removed, never their parent directories, and unsafe symlinks are refused.

## Manual installation on other Linux distributions

Install Python 3.11 or newer with venv and pip support, Git, systemd user tools,
sudo, and udev using your distribution's documentation. From a checkout:

```console
python3 -m venv /path/to/nextuxlinq-venv
/path/to/nextuxlinq-venv/bin/python -m pip wheel --wheel-dir /tmp/nextuxlinq-wheel .
/path/to/nextuxlinq-venv/bin/python -m pip install /tmp/nextuxlinq-wheel/nextuxlinq-*.whl
```

Install `udev/70-nextuxlinq.rules` as root-owned mode `0644` at
`/etc/udev/rules.d/70-nexlinq.rules`, reload udev rules, and install the supplied
systemd user service using `nextuxlinqctl service install`. Do not run pip as
root. Confirm `nextuxlinqctl service status` and the loopback GUI before
removing the checkout.

Troubleshooting should begin with the service status and journal commands
above. `--no-udev` is useful when an administrator manages device permissions.
No installer operation downloads shell code, performs a distribution upgrade,
or makes application directories world-writable. Python package dependencies
are fetched from PyPI, so the installer is not usable fully offline.

The automated scripts are developed and exercised on Arch Linux, CachyOS, and
other pacman-based Arch derivatives. They are covered by a sandboxed test
suite that stubs out systemd, sudo, and udev, so a real installation test in a
virtual machine or under a fresh user account is still recommended before
relying on them on a new system.
