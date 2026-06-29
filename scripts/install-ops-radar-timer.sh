#!/usr/bin/env bash
# Install the ops-radar detector as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-ops-radar-timer.sh
#   HEADING_OS_TZ=Asia/Dubai scripts/install-ops-radar-timer.sh   # pin a TZ
#
# Renders scripts/templates/systemd/ops-radar.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# daily timer. The timer fires 08:00 in the configured timezone (HEADING_OS_TZ,
# default UTC) independent of any Claude Code session and runs
# scripts/ops-radar-notify.py, which auto-heals Tier-A (ollama/index) and pushes a
# counts-only nudge to the operator's Telegram alert channel ONLY on a genuine due
# signal -- never autonomously executing a Tier-B manual action.
#
# This is a STANDALONE installer mirroring install-odin-cadence-timer.sh's
# template+sed render convention. The timezone is read from the environment (no
# hardcoded locale), so the templates carry no geographic signal and ship in the
# public engine.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Honor PYTHON env override so callers can point at a venv interpreter.
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"

# Radar timezone: externalized so no operating locale is baked into the engine.
TZ_VALUE="${HEADING_OS_TZ:-UTC}"

TEMPLATE_DIR="$WORKSPACE/scripts/templates/systemd"
DEST_DIR="$HOME/.config/systemd/user"

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
for unit in ops-radar.service ops-radar.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes.
for unit in ops-radar.service ops-radar.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd
# that rejects the trailing timezone, rather than failing opaquely at enable).
if ! systemd-analyze calendar "*-*-* 08:00:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/ops-radar.timer to 'OnCalendar=*-*-* 08:00' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now ops-radar.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: ops-radar.timer"
echo ""
echo "  Next fire:"
systemctl --user list-timers ops-radar.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status ops-radar.timer"
echo "  Logs:    journalctl --user -u ops-radar.service -f"
echo "  Test:    python3 scripts/ops-radar-notify.py"
