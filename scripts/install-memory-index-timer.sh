#!/usr/bin/env bash
# Install the associative-memory index refresh as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-memory-index-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-memory-index-timer.sh   # pin a TZ
#   PYTHON=/path/to/python scripts/install-memory-index-timer.sh   # override interpreter
#
# Renders scripts/templates/systemd/memory-index-refresh.{service,timer}
# (substituting {{WORKSPACE}} and {{PYTHON}}) into ~/.config/systemd/user/, then
# enables a DAILY timer (03:30 host-local, Persistent) that runs an INCREMENTAL
# `scripts/memory-index.py build` -- re-embedding only changed notes so recall
# never silently goes stale. The "hippocampus" (.memory-index/index.db) is kept
# in parity with the "neocortex" (git-tracked note layers).
#
# This is a STANDALONE installer mirroring scripts/install-odin-cadence-timer.sh
# (same template+sed render convention). It deliberately defaults PYTHON to the
# workspace .venv interpreter: memory-index.py needs numpy + pyyaml and does NOT
# self-re-exec into the venv, and a systemd unit does not inherit the interactive
# shell profile -- so the venv python must be named explicitly or the build fails
# at import time under bare system python.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# HELM only. The systemd unit templates substitute the workspace path into
# WorkingDirectory= and ExecStart=, so running this from a YARD worktree
# points a LIVE daemon at a checkout that is deleted two days later.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Default to the workspace venv (heavy deps live there); allow PYTHON override.
if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "$WORKSPACE/.venv/bin/python" ]]; then
        PYTHON="$WORKSPACE/.venv/bin/python"
    else
        PYTHON="$(command -v python3 || command -v python || true)"
    fi
fi

# Unit timezone: resolved through the workspace resolver rather than read from
# the environment alone. HEADING_OS_TZ lives in the gitignored .env and is
# exported by nothing, so an environment-only read renders UTC on a machine
# whose timezone is correctly configured. An explicit HEADING_OS_TZ=X still wins.
# Invoked as a MODULE, from the workspace root. Running the file directly puts
# scripts/utils/ on sys.path[0], where operator.py shadows the stdlib operator
# that collections imports -- measured fatal on Python 3.12 (the service host)
# and silently fine on 3.11 (the laptop), so the || echo UTC fallback below was
# swallowing it as a plain "no timezone configured".
TZ_VALUE="${HEADING_OS_TZ:-$(cd "$WORKSPACE" && "$PYTHON" -m scripts.utils.paths tz || echo UTC)}"

TEMPLATE_DIR="$WORKSPACE/scripts/templates/systemd"
DEST_DIR="$HOME/.config/systemd/user"

if [[ -z "$PYTHON" ]]; then
    echo "No python interpreter found (set PYTHON=...)." >&2
    exit 4
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found - systemd user units require systemd >= 226." >&2
    echo "On WSL2 enable systemd via /etc/wsl.conf:" >&2
    echo "  [boot]" >&2
    echo "  systemd=true" >&2
    exit 5
fi

# Sanity-check the interpreter has the build's hard dependencies before wiring a
# timer that would otherwise fail silently into journald every night.
if ! "$PYTHON" -c "import numpy, yaml" >/dev/null 2>&1; then
    echo "[error] $PYTHON lacks numpy and/or pyyaml -- the index build will fail." >&2
    echo "        Point PYTHON at the workspace .venv (numpy + pyyaml present there)." >&2
    exit 7
fi

for unit in memory-index-refresh.service memory-index-refresh.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes (the substitution handles them fine).
for unit in memory-index-refresh.service memory-index-refresh.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the RENDERED calendar expression before enabling, so a too-old systemd
# that rejects a timezone suffix fails here with an explanation rather than
# opaquely at enable time.
RENDERED_CAL="$(grep -m1 '^OnCalendar=' "$DEST_DIR/memory-index-refresh.timer" | sed 's|^OnCalendar=||')"
if ! systemd-analyze calendar "$RENDERED_CAL" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects the calendar expression: $RENDERED_CAL" >&2
    echo "       Drop the trailing timezone from $DEST_DIR/memory-index-refresh.timer and set" >&2
    echo "       the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now memory-index-refresh.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: memory-index-refresh.timer"
echo "       interpreter: ${PYTHON}"
echo ""
echo "  Next fire:"
systemctl --user list-timers memory-index-refresh.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status memory-index-refresh.timer"
echo "  Logs:    journalctl --user -u memory-index-refresh.service -f"
echo "  Test:    systemctl --user start memory-index-refresh.service  # run a refresh now"
