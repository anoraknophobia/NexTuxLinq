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
old=$(readlink -f "$CURRENT")
previous_before=$(readlink -f "$PREVIOUS" 2>/dev/null || true)
temporary=$(create_temporary_directory)
final=""

# UPDATE_STATE tracks how far the update progressed:
#   pending   - nothing user-visible changed yet, no rollback needed
#   switched  - the current symlink now points at the new release
#   committed - the update succeeded and must never be rolled back
UPDATE_STATE=pending
ROLLBACK_DONE=0

rollback() {
    (( ROLLBACK_DONE == 0 )) || return 0
    ROLLBACK_DONE=1
    warn "Update failed; restoring $(basename "$old")."
    recovery_control disable || true
    atomic_link "$old" "$CURRENT" || true
    [[ -z $previous_before ]] || atomic_link "$previous_before" "$PREVIOUS" || true
    install_launchers || true
    service_install "$was_enabled" 0 || true
    if (( was_active && ! NO_START )); then service_control restart || true; fi
    if (( was_recovery )); then recovery_control enable || true; fi
    # Remove only the new release; the known-good previous release stays.
    if [[ -n $final && -d $final && $final != "$old" ]]; then
        safe_remove_tree "$final" "$RELEASES" || true
    fi
    return 0
}

finish_update() {
    local status=$?
    trap - EXIT ERR INT TERM HUP
    if [[ $UPDATE_STATE == switched ]]; then rollback; fi
    cleanup_temporary "$temporary"
    exit "$status"
}

handle_signal() {
    local signal=$1
    trap - EXIT ERR INT TERM HUP
    warn "Update interrupted by SIG$signal."
    if [[ $UPDATE_STATE == switched ]]; then rollback; fi
    cleanup_temporary "$temporary"
    case $signal in INT) exit 130;; TERM) exit 143;; HUP) exit 129;; *) exit 1;; esac
}

trap finish_update EXIT
trap 'handle_signal INT' INT
trap 'handle_signal TERM' TERM
trap 'handle_signal HUP' HUP

identifier=$(release_id); stage="$temporary/$identifier"; build_release "$identifier" "$stage" "$temporary/wheels"
mkdir -p -- "$RELEASES"; final="$RELEASES/$identifier"; mv -- "$stage" "$final"
if (( was_recovery )); then recovery_control disable; fi
atomic_link "$old" "$PREVIOUS"; atomic_link "releases/$identifier" "$CURRENT"
UPDATE_STATE=switched
# Sandbox-only synchronization point used to exercise the signal traps.
if (( TEST_MODE )) && [[ -n ${NEXTUXLINQ_TEST_PAUSE_FILE:-} ]]; then
    : > "$NEXTUXLINQ_TEST_PAUSE_FILE"
    while [[ -e $NEXTUXLINQ_TEST_PAUSE_FILE ]]; do sleep 0.05; done
fi
install_launchers
if ! service_install "$was_enabled" 0; then rollback; exit 1; fi
if (( was_recovery )); then recovery_control enable || { rollback; die "Recovery-setting migration failed; rollback completed."; }; fi
if (( was_active && ! NO_START )); then service_control restart || { rollback; die "Service restart failed; rollback completed."; }; fi
if (( was_active && ! NO_START && ! TEST_MODE )); then
    healthy=0; for _ in {1..20}; do service_is_active && { healthy=1; break; }; sleep 1; done
    (( healthy )) || { rollback; die "Updated service did not become healthy; rollback completed."; }
fi
udev_sum=$(python3 "$METADATA_TOOL" get "$ROOT_METADATA" udev_rule.sha256 2>/dev/null || true)
write_root_metadata "$final" "$old" "$udev_sum"; prune_releases
UPDATE_STATE=committed
ok "Updated NexTuxLinq to $identifier"
