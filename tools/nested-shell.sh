#!/bin/bash
# Run a command inside a private, headless GNOME Shell with the Launcher Helper
# extension enabled. Nothing touches the real session: settings (dconf), data, cache
# and the session bus are all temporary.
#
#   tools/nested-shell.sh                         # run the extension tests
#   tools/nested-shell.sh <command> [args...]     # run anything inside the nested shell
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UUID=launcher-helper@jinwei.github.io
TMP=$(mktemp -d /tmp/launcher-nested.XXXXXX)
DISPLAY_NAME=launcher-nested-$$
trap 'rm -rf "$TMP"' EXIT

export XDG_CONFIG_HOME=$TMP/config XDG_DATA_HOME=$TMP/data XDG_CACHE_HOME=$TMP/cache \
    XDG_STATE_HOME=$TMP/state
mkdir -p "$XDG_DATA_HOME/gnome-shell/extensions" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
cp -r "$ROOT/extension/$UUID" "$XDG_DATA_HOME/gnome-shell/extensions/"

if [ $# -eq 0 ]; then
    set -- "$ROOT/.venv/bin/python" "$ROOT/tools/nested_extension_test.py"
fi

export NESTED_DISPLAY=$DISPLAY_NAME NESTED_UUID=$UUID NESTED_TMP=$TMP
exec dbus-run-session -- bash -c '
    set -e
    gsettings set org.gnome.shell enabled-extensions "[\"$NESTED_UUID\"]"
    gsettings set org.gnome.shell welcome-dialog-last-shown-version "999"
    unset DISPLAY
    gnome-shell --headless --wayland --no-x11 --virtual-monitor 1280x800 \
        --wayland-display "$NESTED_DISPLAY" > "$NESTED_TMP/shell.log" 2>&1 &
    SHELL_PID=$!
    for _ in $(seq 100); do
        gdbus introspect --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
            >/dev/null 2>&1 && break
        sleep 0.1
    done
    export WAYLAND_DISPLAY="$NESTED_DISPLAY" GDK_BACKEND=wayland
    status=0
    "$@" || status=$?
    if [ $status -ne 0 ]; then
        echo "--- nested gnome-shell log (errors) ---"
        grep -iE "error|warning|exception|launcher" "$NESTED_TMP/shell.log" | tail -30 || true
    fi
    kill $SHELL_PID 2>/dev/null || true
    wait $SHELL_PID 2>/dev/null || true
    exit $status
' nested-shell "$@"
