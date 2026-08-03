#!/usr/bin/env bash
# Install the Conversation Chronicle build as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-chronicle-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-chronicle-timer.sh   # pin a TZ
#   PYTHON=/path/to/python scripts/install-chronicle-timer.sh   # override interpreter
#
# Renders scripts/templates/systemd/chronicle.{service,timer} (substituting
# {{WORKSPACE}} and {{PYTHON}}) into ~/.config/systemd/user/, then enables a
# DAILY timer (03:00 host-local, Persistent) that runs an INCREMENTAL
# `scripts/chronicle.py build` -- summarizing only new sessions into the dated
# chronicle. It fires BEFORE the 03:30 memory-index refresh so the day's new
# business entries are embedded into recall the same night.
#
# Safe to automate: the chronicle NEVER writes to the brain and NEVER sends
# anything (unlike `/odin collect`, which stays manual). Personal tagging fails
# toward "personal", so an unattended run can only over-wall, never over-expose.
#
# This mirrors scripts/install-memory-index-timer.sh (same template+sed render
# convention). It defaults PYTHON to the workspace .venv interpreter: the build
# imports the workspace package chain and a systemd unit does not inherit the
# interactive shell profile, so the venv python must be named explicitly.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

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
TZ_VALUE="${HEADING_OS_TZ:-$("$PYTHON" "$WORKSPACE/scripts/utils/paths.py" tz || echo UTC)}"

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

# Sanity-check the interpreter can import the build's workspace package chain
# before wiring a timer that would otherwise fail silently into journald nightly.
if ! "$PYTHON" -c "import sys; sys.path.insert(0, '$WORKSPACE'); import scripts.calibrate" >/dev/null 2>&1; then
    echo "[error] $PYTHON cannot import scripts.calibrate -- the chronicle build will fail." >&2
    echo "        Point PYTHON at the workspace .venv." >&2
    exit 7
fi

# ollama (gemma3:4b) is the summarizer. Warn (do not block) if it is unreachable
# now -- the timer still installs; a nightly fire simply skips what it cannot
# summarize and the next fire resumes.
if ! "$PYTHON" -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3)" >/dev/null 2>&1; then
    echo "  [warn] ollama not reachable at localhost:11434 right now -- install proceeds;" >&2
    echo "         ensure ollama + gemma3:4b are up before the nightly fire." >&2
fi

for unit in chronicle.service chronicle.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed (pipe markers handle slashes in paths).
for unit in chronicle.service chronicle.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the RENDERED calendar expression before enabling, so a too-old systemd
# that rejects a timezone suffix fails here with an explanation rather than
# opaquely at enable time.
RENDERED_CAL="$(grep -m1 '^OnCalendar=' "$DEST_DIR/chronicle.timer" | sed 's|^OnCalendar=||')"
if ! systemd-analyze calendar "$RENDERED_CAL" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects the calendar expression: $RENDERED_CAL" >&2
    echo "       Drop the trailing timezone from $DEST_DIR/chronicle.timer and set" >&2
    echo "       the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now chronicle.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: chronicle.timer"
echo "       interpreter: ${PYTHON}"
echo ""
echo "  Next fire:"
systemctl --user list-timers chronicle.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status chronicle.timer"
echo "  Logs:    journalctl --user -u chronicle.service -f"
echo "  Test:    systemctl --user start chronicle.service   # run a build now"
echo "  Disable: systemctl --user disable --now chronicle.timer"
