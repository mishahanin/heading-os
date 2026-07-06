#!/usr/bin/env bash
# Install the memory auto-retire pass as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-memory-auto-retire-timer.sh
#   PYTHON=/path/to/python scripts/install-memory-auto-retire-timer.sh   # override interpreter
#
# Renders scripts/templates/systemd/memory-auto-retire.{service,timer}
# (substituting {{WORKSPACE}} and {{PYTHON}}) into ~/.config/systemd/user/, then
# enables a DAILY timer (07:20 host-local, Persistent) that runs
# `scripts/memory-auto-retire.py` -- the safe, deterministic slice of /dream that
# retires ONLY memories whose author stamped an explicit expires: date now in the
# past. Orphans, redundancy pairs, and rewording stay a human-gated /dream call.
#
# This is a STANDALONE installer mirroring scripts/install-memory-hygiene-timer.sh
# (same template+sed render convention). It defaults PYTHON to the workspace
# .venv interpreter: the script imports the workspace utils (pyyaml-backed
# frontmatter + routing), and a systemd unit does not inherit the interactive
# shell profile -- so the venv python must be named explicitly.
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

# Sanity-check the interpreter can import the workspace utils (pyyaml) the script
# relies on for frontmatter parsing, before wiring a timer that would otherwise
# fail silently into journald every day.
if ! "$PYTHON" -c "import yaml" >/dev/null 2>&1; then
    echo "[error] $PYTHON lacks pyyaml -- the script's frontmatter parse will degrade." >&2
    echo "        Point PYTHON at the workspace .venv (pyyaml present there)." >&2
    exit 7
fi

for unit in memory-auto-retire.service memory-auto-retire.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed (pipe markers tolerate slashes in paths).
for unit in memory-auto-retire.service memory-auto-retire.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now memory-auto-retire.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: memory-auto-retire.timer"
echo "       interpreter: ${PYTHON}"
echo ""
echo "  Next fire:"
systemctl --user list-timers memory-auto-retire.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status memory-auto-retire.timer"
echo "  Logs:    journalctl --user -u memory-auto-retire.service -f"
echo "  Test:    systemctl --user start memory-auto-retire.service  # run a pass now"
