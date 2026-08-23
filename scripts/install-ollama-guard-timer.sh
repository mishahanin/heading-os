#!/usr/bin/env bash
# Install the ollama guard as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-ollama-guard-timer.sh
#   PYTHON=/path/to/python scripts/install-ollama-guard-timer.sh
#
# Renders scripts/templates/systemd/ollama-guard.{service,timer} into
# ~/.config/systemd/user/ and enables a timer that fires every five minutes.
# Each fire probes the addresses config/ollama-hosts.yaml names and, only when
# none of them answers, starts the Windows-side ollama through WSL interop.
#
# Why this exists. Since 2026-08-23 every model this workspace uses lives on one
# machine and nothing falls back: embedding refuses when its host is down, and
# the nightly chronicle stops. Autostart covers a reboot (Startup\Ollama.lnk);
# nothing covered a crash, and on 2026-08-20 the Windows daemon crash-looped for
# sixteen hours with no one the wiser.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "$WORKSPACE/.venv/bin/python" ]]; then
        PYTHON="$WORKSPACE/.venv/bin/python"
    else
        PYTHON="$(command -v python3 || command -v python || true)"
    fi
fi

# Unit timezone. This timer has no calendar expression, so the zone changes
# nothing about WHEN it fires - it is here so the guard's journal lines carry
# the operator's local time like every other unit's, and so this installer
# stays uniform with its siblings (a guard test enforces that uniformity).
# Invoked as a MODULE from the workspace root: running the file directly puts
# scripts/utils/ on sys.path[0], where operator.py shadows the stdlib operator.
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

# The guard starts a WINDOWS application from inside WSL. Without interop it can
# probe but never heal, which is a watchdog that only watches -- say so at
# install time rather than leaving it to be discovered during an outage.
if ! command -v cmd.exe >/dev/null 2>&1; then
    echo "  [warn] cmd.exe not on PATH: WSL interop looks disabled." >&2
    echo "         The guard will still REPORT an outage but cannot fix one." >&2
fi

for unit in ollama-guard.service ollama-guard.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

for unit in ollama-guard.service ollama-guard.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now ollama-guard.timer

if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: ollama-guard.timer"
echo "       interpreter: ${PYTHON}"
echo ""
systemctl --user list-timers ollama-guard.timer --no-pager || true
echo ""
echo "  Check now: $PYTHON $WORKSPACE/scripts/ollama-guard.py check"
echo "  Heal now:  systemctl --user start ollama-guard.service"
echo "  Logs:      journalctl --user -u ollama-guard.service -f"
echo "  Disable:   systemctl --user disable --now ollama-guard.timer"
