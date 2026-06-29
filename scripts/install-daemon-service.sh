#!/usr/bin/env bash
# Install a 31C workspace daemon as a systemd user unit on Linux/WSL2.
#
# Usage:
#   scripts/install-daemon-service.sh <name>
#
#   <name> is one of:
#     bridge | bridge-daemon
#     sentinel
#     fireside | fireside-bot | fireside-bot-daemon
#     sync-exchange | sync-exchange-daemon
#     eval-drift | eval-drift-daemon
#
# Substitutes {{WORKSPACE}} and {{PYTHON}} placeholders in the matching
# template at scripts/templates/systemd/<unit>.service, writes the unit
# to ~/.config/systemd/user/, then enables and starts it.
#
# For unattended boot:
#   loginctl enable-linger "$USER"
#
# Counterparts:
#   Windows: scripts/install-bridge-service.ps1
#   macOS:   scripts/install-bridge-service-mac.py (bridge only)
#            scripts/utils/schedule.py (sync + sentinel via launchd)

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <name>" >&2
    echo "  bridge | sentinel | fireside-bot | sync-exchange | eval-drift" >&2
    exit 1
fi

case "$1" in
    bridge|bridge-daemon)
        UNIT="bridge-daemon"
        ;;
    sentinel)
        UNIT="sentinel"
        ;;
    fireside|fireside-bot|fireside-bot-daemon)
        UNIT="fireside-bot-daemon"
        ;;
    sync-exchange|sync-exchange-daemon)
        UNIT="sync-exchange-daemon"
        ;;
    eval-drift|eval-drift-daemon)
        UNIT="eval-drift-daemon"
        ;;
    *)
        echo "Unknown daemon: $1" >&2
        echo "Expected one of: bridge sentinel fireside-bot sync-exchange eval-drift" >&2
        exit 2
        ;;
esac

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Resolve the interpreter the unit's ExecStart will use, in priority order:
#   1. An explicit PYTHON env override (caller points at a specific interpreter):
#        PYTHON=/path/to/.venv-linux/bin/python ./install-daemon-service.sh bridge
#   2. The workspace uv venv at <WORKSPACE>/.venv/bin/python, when it exists
#      (the standard install target -- `uv sync` builds it).
#   3. The first python3/python on PATH otherwise.
# Modern Linux (Ubuntu 24.04+, Fedora 38+) enforces PEP 668 against system
# Python, so the venv interpreter is preferred over a bare python3.
if [[ -n "${PYTHON:-}" ]]; then
    :  # explicit override wins
elif [[ -x "$WORKSPACE/.venv/bin/python" ]]; then
    PYTHON="$WORKSPACE/.venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python || true)"
fi

TEMPLATE="$WORKSPACE/scripts/templates/systemd/$UNIT.service"
DEST_DIR="$HOME/.config/systemd/user"
DEST="$DEST_DIR/$UNIT.service"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "Template not found: $TEMPLATE" >&2
    exit 3
fi
if [[ -z "$PYTHON" ]]; then
    echo "No python3 (or python) on PATH" >&2
    exit 4
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found - systemd user units require systemd >= 226." >&2
    echo "On WSL2 enable systemd via /etc/wsl.conf:" >&2
    echo "  [boot]" >&2
    echo "  systemd=true" >&2
    exit 5
fi

mkdir -p "$DEST_DIR"

# Render the template with portable sed. Pipe markers chosen because the paths
# may contain forward slashes; the workspace path on the CEO Windows machine
# also contains spaces and parens, which sed handles fine as long as we don't
# wrap the substitution in shell quoting that would expand them again.
sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
    -e "s|{{PYTHON}}|${PYTHON}|g" \
    "$TEMPLATE" > "$DEST"

# Ensure the daemon's runtime/log directory exists (systemd will not auto-
# create it; the daemon would crash trying to write a log file).
case "$UNIT" in
    bridge-daemon)         mkdir -p "$WORKSPACE/.daemon-state" ;;
    sentinel)              mkdir -p "$WORKSPACE/.sentinel" ;;
    fireside-bot-daemon)   mkdir -p "$WORKSPACE/.fireside" ;;
    sync-exchange-daemon)  mkdir -p "$WORKSPACE/.sync-exchange" ;;
    eval-drift-daemon)     mkdir -p "$WORKSPACE/.eval-drift" ;;
esac

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT.service"

echo "  [ok] systemd user unit installed and started: $UNIT.service"
echo ""
echo "  Status: systemctl --user status $UNIT.service"
echo "  Logs:   journalctl --user -u $UNIT.service -f"
echo ""
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    echo "  [hint] Run once for unattended boot:"
    echo "         loginctl enable-linger $USER"
fi
