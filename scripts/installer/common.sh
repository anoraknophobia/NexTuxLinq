#!/usr/bin/env bash
# Shared managed-install primitives. This file is sourced, not executed.

set -Eeuo pipefail

PROJECT_NAME="NexTuxLinq"
INSTALLER_FORMAT_VERSION=1
MAX_RELEASES=3
SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
METADATA_TOOL="$SOURCE_ROOT/scripts/installer/metadata.py"
UDEV_SOURCE="$SOURCE_ROOT/udev/70-nextuxlinq.rules"
UDEV_DEST="/etc/udev/rules.d/70-nexlinq.rules"
DRY_RUN=0
TEST_MODE=0
# Build directories are created below a fixed, project-owned base instead of an
# arbitrary caller-provided TMPDIR, so the cleanup safety check always matches
# the directory that was actually created.
TEMPORARY_BASE="/tmp"
TEMPORARY_PREFIX="nextuxlinq-installer"

info() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
run() { if (( DRY_RUN )); then printf '[DRY-RUN]'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }

init_environment() {
    [[ ${EUID:-$(id -u)} -ne 0 ]] || die "Do not run this installer with sudo. It requests sudo only for the Matrix900 udev rule."
    [[ -n ${HOME:-} && $HOME = /* ]] || die "HOME must be an absolute path."
    if [[ ${NEXTUXLINQ_INSTALLER_TEST_MODE:-0} == 1 ]]; then
        local root=${NEXTUXLINQ_INSTALLER_TEST_ROOT:-}
        [[ $root == /tmp/* && -f $root/.nextuxlinq-installer-test-root ]] || die "Invalid installer test sandbox."
        TEST_MODE=1
        UDEV_DEST="$root/etc/udev/rules.d/70-nexlinq.rules"
        # Keep sandbox build directories inside the approved test root.
        TEMPORARY_BASE="$root/tmp"
    elif env | grep -q '^NEXTUXLINQ_INSTALLER_TEST_'; then
        die "Installer test overrides are unavailable outside test mode."
    fi
    DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
    CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
    STATE_HOME=${XDG_STATE_HOME:-$HOME/.local/state}
    CACHE_HOME=${XDG_CACHE_HOME:-$HOME/.cache}
    INSTALL_ROOT="$DATA_HOME/nextuxlinq"
    RELEASES="$INSTALL_ROOT/releases"
    CURRENT="$INSTALL_ROOT/current"
    PREVIOUS="$INSTALL_ROOT/previous"
    ROOT_METADATA="$INSTALL_ROOT/install-metadata.json"
    BIN_DIR="$HOME/.local/bin"
    UNIT_DIR="$CONFIG_HOME/systemd/user"
}

require_commands() {
    local need_udev=${1:-0}
    local missing=() command
    for command in bash python3 install cp mv ln mkdir mktemp sha256sum systemctl; do
        command -v "$command" >/dev/null 2>&1 || missing+=("$command")
    done
    if ((${#missing[@]})); then
        if command -v pacman >/dev/null 2>&1; then
            die "Missing prerequisites: ${missing[*]}. On Arch/CachyOS: sudo pacman -S --needed bash coreutils python python-pip systemd sudo"
        fi
        die "Missing prerequisites: ${missing[*]}. See docs/installation.md."
    fi
    python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is required."
    python3 -m venv --help >/dev/null 2>&1 || die "Python venv support is required."
    python3 -m pip --version >/dev/null 2>&1 || die "Python pip support is required."
    if (( need_udev && ! TEST_MODE )); then
        command -v sudo >/dev/null 2>&1 || die "sudo is required unless --no-udev is used."
        command -v udevadm >/dev/null 2>&1 || die "udevadm is required unless --no-udev is used."
    fi
}

release_id() { printf '%s-%s-%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" "$(git -C "$SOURCE_ROOT" rev-parse --short=12 HEAD 2>/dev/null || printf source)" "$$"; }
source_commit() { git -C "$SOURCE_ROOT" rev-parse HEAD 2>/dev/null || printf unknown; }
timestamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

build_release() {
    local identifier=$1 stage=$2 wheel_dir=$3 wheel version checksum commit now
    mkdir -p -- "$stage" "$wheel_dir"
    if (( TEST_MODE )); then
        [[ ${NEXTUXLINQ_TEST_BUILD_FAILURE:-0} != 1 ]] || die "Simulated package validation failure."
        mkdir -p "$stage/venv/bin" "$stage/venv/lib/python3.11/site-packages/nextuxlinq/installer_assets"
        printf '#!/usr/bin/env bash\nexit 0\n' > "$stage/venv/bin/nextuxlinqctl"
        cp "$stage/venv/bin/nextuxlinqctl" "$stage/venv/bin/nextuxlinqd"
        cp "$stage/venv/bin/nextuxlinqctl" "$stage/venv/bin/python"
        chmod 0755 "$stage/venv/bin/nextuxlinqctl" "$stage/venv/bin/nextuxlinqd" "$stage/venv/bin/python"
        cp "$UDEV_SOURCE" "$stage/venv/lib/python3.11/site-packages/nextuxlinq/installer_assets/70-nextuxlinq.rules"
        cp "$SOURCE_ROOT/systemd/nextuxlinqd.service" "$stage/venv/lib/python3.11/site-packages/nextuxlinq/installer_assets/nextuxlinqd.service"
        version=0.1.0; checksum=test-wheel
    else
        local builder="$wheel_dir/builder"
        python3 -m venv "$builder"
        "$builder/bin/python" -m pip wheel --no-deps --wheel-dir "$wheel_dir" "$SOURCE_ROOT"
        wheel=$(find "$wheel_dir" -maxdepth 1 -type f -name 'nextuxlinq-*.whl' -print -quit)
        [[ -n $wheel ]] || die "Wheel build did not produce NexTuxLinq."
        python3 -m venv "$stage/venv"
        "$stage/venv/bin/python" -m pip install "$wheel"
        "$stage/venv/bin/python" -c 'import importlib.resources as r, nextuxlinq; assert r.files("nextuxlinq").joinpath("installer_assets/70-nextuxlinq.rules").is_file()'
        "$stage/venv/bin/nextuxlinqctl" --help >/dev/null
        "$stage/venv/bin/nextuxlinqd" --help >/dev/null
        version=$("$stage/venv/bin/python" -c 'import importlib.metadata as m; print(m.version("nextuxlinq"))')
        checksum=$(sha256sum "$wheel" | awk '{print $1}')
    fi
    commit=$(source_commit); now=$(timestamp)
    python3 "$METADATA_TOOL" release "$stage/install-metadata.json" "$identifier" "$version" "$commit" "$now" "$checksum"
}

atomic_link() {
    local target=$1 link=$2 temporary
    temporary="$link.new.$$"
    ln -s -- "$target" "$temporary"
    mv -Tf -- "$temporary" "$link"
}

install_launchers() {
    mkdir -p -- "$BIN_DIR"
    local name expected existing
    for name in nextuxlinqctl nextuxlinqd; do
        expected="$CURRENT/venv/bin/$name"
        if [[ -e $BIN_DIR/$name || -L $BIN_DIR/$name ]]; then
            [[ -L $BIN_DIR/$name ]] || die "Refusing to replace foreign launcher: $BIN_DIR/$name"
            existing=$(readlink "$BIN_DIR/$name")
            [[ $existing == "$expected" ]] || die "Refusing to replace foreign launcher: $BIN_DIR/$name"
        fi
        ln -sfn -- "$expected" "$BIN_DIR/$name"
    done
}

remove_managed_launchers() {
    local name expected
    for name in nextuxlinqctl nextuxlinqd; do
        expected="$CURRENT/venv/bin/$name"
        if [[ -L $BIN_DIR/$name ]] && [[ $(readlink "$BIN_DIR/$name") == "$expected" ]]; then
            rm -f -- "$BIN_DIR/$name"
        elif [[ -e $BIN_DIR/$name || -L $BIN_DIR/$name ]]; then
            warn "Preserving foreign launcher: $BIN_DIR/$name"
        fi
    done
}

service_install() {
    local enable=$1 start=$2 arguments=(service install --if-changed)
    (( enable )) || arguments+=(--no-enable)
    (( start )) || arguments+=(--no-start)
    if (( TEST_MODE )); then
        [[ ${NEXTUXLINQ_TEST_INSTALL_FAILURE:-0} != 1 ]] || return 1
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT_DIR/nextuxlinqd.service" <<EOF
# X-NexTuxLinq-Managed: nextuxlinqctl-service-v1
[Service]
ExecStart="$CURRENT/venv/bin/python" -m nextuxlinq.daemon --host 127.0.0.1 --port 8765
EOF
        return 0
    fi
    "$CURRENT/venv/bin/nextuxlinqctl" "${arguments[@]}"
}

service_control() {
    local action=$1
    if (( TEST_MODE )); then
        [[ ${NEXTUXLINQ_TEST_SERVICE_FAILURE:-0} != 1 ]]
        return
    fi
    "$CURRENT/venv/bin/nextuxlinqctl" service "$action"
}

recovery_is_enabled() {
    if (( TEST_MODE )); then [[ ${NEXTUXLINQ_TEST_RECOVERY_ENABLED:-0} == 1 ]]; return; fi
    "$CURRENT/venv/bin/nextuxlinqctl" service openlinkhub-restart status 2>/dev/null | grep -q 'enabled'
}

recovery_control() {
    local action=$1
    (( TEST_MODE )) && return 0
    "$CURRENT/venv/bin/nextuxlinqctl" service openlinkhub-restart "$action"
}

service_is_active() {
    if (( TEST_MODE )); then [[ ${NEXTUXLINQ_TEST_SERVICE_ACTIVE:-0} == 1 ]]; return; fi
    systemctl --user is-active --quiet nextuxlinqd.service
}
service_is_enabled() {
    if (( TEST_MODE )); then [[ ${NEXTUXLINQ_TEST_SERVICE_ENABLED:-0} == 1 ]]; return; fi
    systemctl --user is-enabled --quiet nextuxlinqd.service
}

install_udev() {
    local non_interactive=$1 source_sum existing_sum
    local sudo_command=(sudo)
    (( non_interactive == 0 )) || sudo_command+=("-n")
    source_sum=$(sha256sum "$UDEV_SOURCE" | awk '{print $1}')
    if [[ -e $UDEV_DEST ]]; then
        existing_sum=$(sha256sum "$UDEV_DEST" | awk '{print $1}')
        [[ $existing_sum == "$source_sum" ]] && { printf '%s' "$source_sum"; return; }
        (( non_interactive == 0 )) || die "A different udev rule exists at $UDEV_DEST; refusing replacement in non-interactive mode."
        read -r -p "Replace the different Matrix900 udev rule at $UDEV_DEST? [y/N] " answer
        [[ $answer == y || $answer == Y ]] || die "Udev rule replacement declined."
    fi
    if (( TEST_MODE )); then mkdir -p "$(dirname "$UDEV_DEST")"; install -m 0644 "$UDEV_SOURCE" "$UDEV_DEST"
    else
        command -v sudo >/dev/null && command -v udevadm >/dev/null || die "sudo and udevadm are required unless --no-udev is used."
        run "${sudo_command[@]}" install -o root -g root -m 0644 "$UDEV_SOURCE" "$UDEV_DEST"
        run "${sudo_command[@]}" udevadm control --reload-rules
        run "${sudo_command[@]}" udevadm trigger --subsystem-match=hidraw --attr-match=idVendor=2e3c --attr-match=idProduct=6500 || warn "No connected Matrix900 was retriggered."
    fi
    printf '%s' "$source_sum"
}

write_root_metadata() {
    local active=$1 previous=$2 udev_sum=$3 version commit now
    version=$(python3 "$METADATA_TOOL" get "$active/install-metadata.json" package_version)
    commit=$(source_commit); now=$(timestamp)
    python3 "$METADATA_TOOL" root "$ROOT_METADATA" "$version" "$commit" "$now" "$(basename "$active")" "${previous:+$(basename "$previous")}" "$UDEV_DEST" "$udev_sum"
}

prune_releases() {
    local keep_current keep_previous count=0 directory
    keep_current=$(readlink -f "$CURRENT" 2>/dev/null || true); keep_previous=$(readlink -f "$PREVIOUS" 2>/dev/null || true)
    while IFS= read -r directory; do
        ((++count <= MAX_RELEASES)) && continue
        [[ $directory == "$keep_current" || $directory == "$keep_previous" ]] && continue
        [[ -f $directory/install-metadata.json && ! -L $directory ]] || continue
        rm -rf -- "$directory"
    done < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
}

safe_remove_tree() {
    local path=$1 expected_parent=$2
    [[ -n $path && $path == "$expected_parent"/* && $path != "$expected_parent" && ! -L $path ]] || die "Refusing unsafe removal: $path"
    rm -rf -- "$path"
}

create_temporary_directory() {
    # An explicit absolute template makes mktemp ignore TMPDIR, so the created
    # path always matches what cleanup_temporary accepts.
    local path
    mkdir -p -- "$TEMPORARY_BASE" || die "Could not prepare $TEMPORARY_BASE."
    path=$(mktemp -d -- "$TEMPORARY_BASE/$TEMPORARY_PREFIX.XXXXXXXXXX") \
        || die "Could not create a private temporary build directory."
    chmod 0700 -- "$path" || die "Could not secure the temporary build directory."
    printf '%s' "$path"
}

cleanup_temporary() {
    # Never fails: this runs from EXIT traps, where turning a cleanup problem
    # into a non-zero exit status would misreport a successful installation.
    local path=${1:-}
    if [[ -z $path || $path != "$TEMPORARY_BASE/$TEMPORARY_PREFIX."* ]]; then
        warn "Skipping cleanup of an unrecognized temporary path: ${path:-<unset>}"
        return 0
    fi
    if [[ -L $path || ! -d $path ]]; then
        warn "Skipping cleanup of an unsafe temporary path: $path"
        return 0
    fi
    rm -rf -- "$path" \
        || warn "The temporary build directory requires manual cleanup: $path"
    return 0
}
