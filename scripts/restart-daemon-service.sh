#!/usr/bin/env bash
# Restart a 31C workspace daemon's systemd user unit (Linux/WSL2).
#
# Usage:
#   scripts/restart-daemon-service.sh <name>
#
# Counterpart to scripts/restart-bridge-daemon.ps1 (Windows).

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

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found - cannot restart." >&2
    exit 3
fi

systemctl --user restart "$UNIT.service"
echo "  [ok] restarted $UNIT.service"
echo ""
systemctl --user status "$UNIT.service" --no-pager 2>&1 | head -10 || true
