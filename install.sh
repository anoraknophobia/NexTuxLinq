#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/scripts/installer/common.sh"

NO_START=0; NO_ENABLE=0; NO_UDEV=0; NON_INTERACTIVE=0
usage() { cat <<'EOF'
Usage: ./install.sh [--dry-run] [--no-start] [--no-enable] [--no-udev] [--non-interactive] [--help]
Install NexTuxLinq into an isolated per-user managed release.
EOF
}
for argument in "$@"; do case $argument in
 --dry-run) DRY_RUN=1;; --no-start) NO_START=1;; --no-enable) NO_ENABLE=1;; --no-udev) NO_UDEV=1;; --non-interactive) NON_INTERACTIVE=1;; --help) usage; exit 0;; *) die "Unknown argument: $argument";; esac; done
init_environment; require_commands "$((1-NO_UDEV))"
[[ ! -e $ROOT_METADATA ]] || die "NexTuxLinq is already installed; run ./update.sh."
[[ ! -e $INSTALL_ROOT ]] || die "An unrecognized installation directory already exists at $INSTALL_ROOT."
if (( DRY_RUN )); then
    info "Would build a wheel, validate a new release under $INSTALL_ROOT, install launchers and the user service."
    (( NO_UDEV )) || info "Would request sudo only to install $UDEV_DEST and reload udev rules."
    exit 0
fi
temporary=$(create_temporary_directory); trap 'cleanup_temporary "$temporary"' EXIT
identifier=$(release_id); stage="$temporary/$identifier"; wheel_dir="$temporary/wheels"
build_release "$identifier" "$stage" "$wheel_dir"
mkdir -p -- "$RELEASES"; final="$RELEASES/$identifier"; mv -- "$stage" "$final"
installed_ok=0; udev_created=0; udev_sum=""
cleanup_failed_install() {
    cleanup_temporary "$temporary"
    if (( ! installed_ok )); then
        if (( TEST_MODE )); then
            rm -f -- "$UNIT_DIR/nextuxlinqd.service"
        elif [[ -x ${CURRENT:-}/venv/bin/nextuxlinqctl ]]; then
            "$CURRENT/venv/bin/nextuxlinqctl" service uninstall >/dev/null 2>&1 || warn "A partially installed user unit may require manual review."
        fi
        if (( udev_created )) && [[ -n $udev_sum && -f $UDEV_DEST ]] && [[ $(sha256sum "$UDEV_DEST" | awk '{print $1}') == "$udev_sum" ]]; then
            if (( TEST_MODE )); then
                rm -f -- "$UDEV_DEST"
            else
                sudo_command=(sudo); (( NON_INTERACTIVE == 0 )) || sudo_command+=("-n")
                "${sudo_command[@]}" rm -- "$UDEV_DEST" || warn "The newly installed udev rule requires manual cleanup."
                "${sudo_command[@]}" udevadm control --reload-rules || warn "udev rules could not be reloaded during cleanup."
            fi
        fi
        remove_managed_launchers
        rm -f -- "$CURRENT"
        [[ ! -d ${final:-} ]] || safe_remove_tree "$final" "$RELEASES"
        rmdir -- "$RELEASES" "$INSTALL_ROOT" 2>/dev/null || true
    fi
}
trap cleanup_failed_install EXIT
atomic_link "releases/$identifier" "$CURRENT"; install_launchers
if (( ! NO_UDEV )); then
    [[ -e $UDEV_DEST ]] || udev_created=1
    udev_sum=$(install_udev "$NON_INTERACTIVE")
fi
service_install "$((1-NO_ENABLE))" "$((1-NO_START))"
if (( ! NO_START && ! TEST_MODE )); then
    healthy=0
    for _ in {1..20}; do
        service_is_active && { healthy=1; break; }
        sleep 1
    done
    (( healthy )) || die "The installed service did not become healthy within 20 seconds."
fi
write_root_metadata "$final" "" "$udev_sum"; prune_releases
installed_ok=1
ok "Installed NexTuxLinq at $INSTALL_ROOT"
info "GUI: http://127.0.0.1:8765"
info "Status: nextuxlinqctl service status"
info "Update: ./update.sh   Uninstall: ./uninstall.sh"
if (( NO_UDEV )); then
    warn "The Matrix900 may require manual udev-rule installation."
fi
exit 0
