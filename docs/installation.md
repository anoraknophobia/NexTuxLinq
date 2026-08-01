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

The managed installation lives in
`~/.local/share/nextuxlinq/releases/`. Atomic `current` and `previous` symlinks
select validated releases. Commands are linked from `~/.local/bin`, and the
user unit is installed below `~/.config/systemd/user`. Configuration, profiles,
backups, state, and cache continue to use their existing XDG locations. The Git
checkout is not needed at runtime and can be removed after installation.

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
`previous`; a startup-health failure restores it. At most three validated
release directories are retained. Configuration and user data are never copied
into or overwritten by a release.

The service is restarted only when it was active before the update. Use
`./update.sh --no-start` to suppress startup and `--dry-run` to inspect the
plan.

The optional OpenLinkHub startup-recovery setting remains an explicit
NexTuxLinq feature. Installation does not install, enable, stop, or otherwise
manage OpenLinkHub. Enable the nonfatal workaround only when needed:

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
or makes application directories world-writable.
