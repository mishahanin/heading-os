#!/usr/bin/env bash
# Install the dream-shadow scan as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-dream-shadow-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-dream-shadow-timer.sh   # pin a TZ
#   PYTHON=/path/to/python scripts/install-dream-shadow-timer.sh   # override interpreter
#
# Renders scripts/templates/systemd/dream-shadow.{service,timer}
# (substituting {{WORKSPACE}} and {{PYTHON}}) into ~/.config/systemd/user/, then
# enables a DAILY timer (03:10 host-local, Persistent, before the 03:30
# memory-index-refresh) that runs `scripts/dream-shadow.py` -- the nightly
# salience-ranked consolidation worklist detector over auto-memory. The scan
# only DETECTS and reports; it never mutates memory. Resolution stays with
# /dream.
#
# This is a STANDALONE installer, a direct adaptation of
# scripts/install-memory-hygiene-timer.sh (same template+sed render
# convention). Defaults PYTHON to the workspace .venv interpreter: the scan
# imports the workspace utils (pyyaml-backed routing), and a systemd unit does
# not inherit the interactive shell profile -- so the venv python must be
# named explicitly.
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

# Default to the workspace venv; allow PYTHON override.
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

# Sanity-check the interpreter can import the workspace utils (pyyaml) the scan
# relies on, before wiring a timer that would otherwise fail silently into
# journald every night.
if ! "$PYTHON" -c "import yaml" >/dev/null 2>&1; then
    echo "[error] $PYTHON lacks pyyaml -- the scan's workspace utils will fail." >&2
    echo "        Point PYTHON at the workspace .venv (pyyaml present there)." >&2
    exit 7
fi

for unit in dream-shadow.service dream-shadow.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed (pipe markers tolerate slashes in paths).
for unit in dream-shadow.service dream-shadow.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the RENDERED calendar expression before enabling, so a too-old systemd
# that rejects a timezone suffix fails here with an explanation rather than
# opaquely at enable time.
RENDERED_CAL="$(grep -m1 '^OnCalendar=' "$DEST_DIR/dream-shadow.timer" | sed 's|^OnCalendar=||')"
if ! systemd-analyze calendar "$RENDERED_CAL" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects the calendar expression: $RENDERED_CAL" >&2
    echo "       Drop the trailing timezone from $DEST_DIR/dream-shadow.timer and set" >&2
    echo "       the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now dream-shadow.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: dream-shadow.timer"
echo "       interpreter: ${PYTHON}"
echo ""
echo "  Next fire:"
systemctl --user list-timers dream-shadow.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status dream-shadow.timer"
echo "  Logs:    journalctl --user -u dream-shadow.service -f"
echo "  Test:    systemctl --user start dream-shadow.service  # run a scan now"
