#!/usr/bin/env bash
# Uninstall a 31C workspace daemon's systemd user unit (Linux/WSL2).
#
# Usage:
#   scripts/uninstall-daemon-service.sh <name>
#
# Counterpart to scripts/install-daemon-service.sh. Idempotent: silent
# no-op if the unit is not installed.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <name>" >&2
    echo "  bridge | sentinel | fireside-bot | sync-exchange | eval-drift" >&2
    exit 1
fi

case "$1" in
    bridge|bridge-daemon)              UNIT="bridge-daemon" ;;
    sentinel)                          UNIT="sentinel" ;;
    fireside|fireside-bot|fireside-bot-daemon) UNIT="fireside-bot-daemon" ;;
    sync-exchange|sync-exchange-daemon) UNIT="sync-exchange-daemon" ;;
    eval-drift|eval-drift-daemon)      UNIT="eval-drift-daemon" ;;
    *)
        echo "Unknown daemon: $1" >&2
        exit 2
        ;;
esac

UNIT_FILE="$HOME/.config/systemd/user/$UNIT.service"

if [[ ! -f "$UNIT_FILE" ]]; then
    echo "  [info] $UNIT.service not installed - nothing to do."
    exit 0
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "$UNIT.service" 2>/dev/null || true
fi

rm -f "$UNIT_FILE"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
fi

echo "  [ok] systemd user unit removed: $UNIT.service"
