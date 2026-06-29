#!/usr/bin/env bash
# Install the Odin cadence reminder as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-odin-cadence-timer.sh
#   HEADING_OS_TZ=Asia/Dubai scripts/install-odin-cadence-timer.sh   # pin a TZ
#
# Renders scripts/templates/systemd/odin-cadence.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# weekly timer. The timer fires Monday 09:00 in the configured timezone
# (HEADING_OS_TZ, default UTC) independent of any Claude Code session and runs
# scripts/odin-cadence-notify.py, which pushes a counts-only nudge to the
# operator's Telegram Saved Messages ONLY on a genuine collect/reflect cadence
# signal -- it never writes to the brain.
#
# This is a STANDALONE installer. It deliberately does NOT extend the shared
# scripts/install-daemon-service.sh (which is .service-only) -- it mirrors that
# script's template+sed render convention so the pattern is honoured, not
# reinvented. The timezone is read from the environment (no hardcoded locale), so
# the templates carry no geographic signal and ship in the public engine.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Honor PYTHON env override so callers can point at a venv interpreter:
#   PYTHON=/path/to/.venv-linux/bin/python ./install-odin-cadence-timer.sh
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"

# Cadence timezone: externalized so no operating locale is baked into the engine.
# Defaults to UTC; pin via HEADING_OS_TZ (e.g. Asia/Dubai) for a local fire time.
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
for unit in odin-cadence.service odin-cadence.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes; the Windows-side workspace path also contains spaces
# and parens, which sed handles fine inside the substitution.
for unit in odin-cadence.service odin-cadence.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd
# that rejects the trailing timezone, rather than failing opaquely at enable).
if ! systemd-analyze calendar "Mon *-*-* 09:00:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/odin-cadence.timer to 'OnCalendar=Mon 09:00' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload

# RETIRED (ops-radar Decision 2): the standalone weekly Odin Telegram push is no
# longer enabled. ops-radar folds the Odin collect/reflect signal into its daily
# exception-driven push, so enabling this timer too would double-ping Telegram.
# The unit files are still rendered above (harmless, and odin-cadence.py compute +
# its /prime line stay intact); we just do not enable the timer. If a stale enabled
# instance exists from a prior install, disable it.
systemctl --user disable --now odin-cadence.timer 2>/dev/null || true

echo "  [ok] odin-cadence units rendered; weekly timer RETIRED (folded into ops-radar)."
echo "       Install the replacement: scripts/install-ops-radar-timer.sh"
echo ""
echo "  odin-cadence.py compute + its /prime line remain active (reused by ops-radar)."
echo "  Status:  systemctl --user list-timers ops-radar.timer --no-pager"
