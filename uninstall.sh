#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/scripts/installer/common.sh"
KEEP_UDEV=0; PURGE=0; YES=0; NON_INTERACTIVE=0
usage() { printf '%s\n' 'Usage: ./uninstall.sh [--dry-run] [--keep-udev] [--purge] [--yes] [--non-interactive] [--help]'; }
for argument in "$@"; do case $argument in --dry-run) DRY_RUN=1;; --keep-udev) KEEP_UDEV=1;; --purge) PURGE=1;; --yes) YES=1;; --non-interactive) NON_INTERACTIVE=1;; --help) usage; exit 0;; *) die "Unknown argument: $argument";; esac; done
init_environment
(( YES == 0 || PURGE == 1 )) || die "--yes is valid only with --purge."
if (( PURGE && NON_INTERACTIVE && ! YES )); then die "Non-interactive purge requires --yes."; fi
if (( PURGE && ! YES )); then read -r -p "Type PURGE to remove NexTuxLinq user data: " answer; [[ $answer == PURGE ]] || die "Purge cancelled."; fi
if (( PURGE )); then
    for path in "$CONFIG_HOME/nextuxlinq" "$STATE_HOME/nextuxlinq" "$CACHE_HOME/nextuxlinq"; do
        [[ ! -L $path ]] || die "Refusing to purge an unsafe symlink: $path"
    done
fi
if (( DRY_RUN )); then info "Would remove managed application files and preserve user data$([[ $PURGE == 1 ]] && printf ' only after purge confirmation')."; exit 0; fi
[[ ! -e $INSTALL_ROOT || -f $ROOT_METADATA ]] || die "Refusing to remove an unrecognized installation directory."
if (( ! TEST_MODE )); then
    "$CURRENT/venv/bin/nextuxlinqctl" service openlinkhub-restart disable >/dev/null 2>&1 || warn "No managed OpenLinkHub recovery drop-in was removed."
    "$CURRENT/venv/bin/nextuxlinqctl" service uninstall >/dev/null 2>&1 || warn "The managed user service could not be removed cleanly."
fi
(( TEST_MODE )) && rm -f -- "$UNIT_DIR/nextuxlinqd.service"
remove_managed_launchers
udev_sum=$(python3 "$METADATA_TOOL" get "$ROOT_METADATA" udev_rule.sha256 2>/dev/null || true)
if (( ! KEEP_UDEV )) && [[ -n $udev_sum && -f $UDEV_DEST ]]; then
    actual=$(sha256sum "$UDEV_DEST" | awk '{print $1}')
    if [[ $actual == "$udev_sum" ]]; then
        if (( TEST_MODE )); then
            rm -f "$UDEV_DEST"
        else
            sudo_command=(sudo)
            (( NON_INTERACTIVE == 0 )) || sudo_command+=("-n")
            "${sudo_command[@]}" rm -- "$UDEV_DEST"
            "${sudo_command[@]}" udevadm control --reload-rules
        fi
    else warn "Preserving modified udev rule: $UDEV_DEST"; fi
fi
[[ ! -e $INSTALL_ROOT ]] || safe_remove_tree "$INSTALL_ROOT" "$DATA_HOME"
if (( PURGE )); then
    for path in "$CONFIG_HOME/nextuxlinq" "$STATE_HOME/nextuxlinq" "$CACHE_HOME/nextuxlinq"; do
        [[ ! -e $path ]] || safe_remove_tree "$path" "$(dirname "$path")"
    done
    ok "NexTuxLinq and recognized user data removed."
else
    ok "NexTuxLinq removed. Configuration, profiles, backups, exports, state, and cache were preserved."
fi
