#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/scripts/installer/common.sh"
NO_START=0
usage() { printf '%s\n' 'Usage: ./update.sh [--dry-run] [--no-start] [--help]'; }
for argument in "$@"; do case $argument in --dry-run) DRY_RUN=1;; --no-start) NO_START=1;; --help) usage; exit 0;; *) die "Unknown argument: $argument";; esac; done
init_environment; require_commands 0
[[ -f $ROOT_METADATA && -L $CURRENT ]] || die "NexTuxLinq is not installed; run ./install.sh."
if (( DRY_RUN )); then info "Would build and validate a new release, atomically switch current, and roll back on failed health validation."; exit 0; fi
was_active=0; was_enabled=0; was_recovery=0
service_is_active && was_active=1 || true; service_is_enabled && was_enabled=1 || true
recovery_is_enabled && was_recovery=1 || true
old=$(readlink -f "$CURRENT"); temporary=$(mktemp -d); trap 'cleanup_temporary "$temporary"' EXIT
identifier=$(release_id); stage="$temporary/$identifier"; build_release "$identifier" "$stage" "$temporary/wheels"
mkdir -p "$RELEASES"; final="$RELEASES/$identifier"; mv "$stage" "$final"
(( was_recovery )) && recovery_control disable
atomic_link "$old" "$PREVIOUS"; atomic_link "releases/$identifier" "$CURRENT"; install_launchers
rollback() {
    warn "Update failed; restoring $(basename "$old")."
    recovery_control disable || true
    atomic_link "$old" "$CURRENT"; install_launchers
    service_install "$was_enabled" 0 || true
    (( was_active && ! NO_START )) && service_control restart || true
    (( was_recovery )) && recovery_control enable || true
    safe_remove_tree "$final" "$RELEASES"
}
if ! service_install "$was_enabled" 0; then rollback; exit 1; fi
if (( was_recovery )); then recovery_control enable || { rollback; die "Recovery-setting migration failed; rollback completed."; }; fi
if (( was_active && ! NO_START )); then service_control restart || { rollback; die "Service restart failed; rollback completed."; }; fi
if (( was_active && ! NO_START && ! TEST_MODE )); then
    healthy=0; for _ in {1..20}; do service_is_active && { healthy=1; break; }; sleep 1; done
    (( healthy )) || { rollback; die "Updated service did not become healthy; rollback completed."; }
fi
udev_sum=$(python3 "$METADATA_TOOL" get "$ROOT_METADATA" udev_rule.sha256 2>/dev/null || true)
write_root_metadata "$final" "$old" "$udev_sum"; prune_releases
ok "Updated NexTuxLinq to $identifier"
