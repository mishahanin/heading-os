#!/usr/bin/env bash
# Install the Odin cadence reminder as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-odin-cadence-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-odin-cadence-timer.sh   # pin a TZ
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
# This installer does NOT call `loginctl enable-linger`, and does not need to:
# it enables no timer (see the RETIRED note at the bottom). The header claimed
# linger was "done automatically below" until 2026-09-02, which was never true
# here, because no `loginctl` line has ever existed in this file. The sibling
# installers that DO enable a timer each call it themselves.

set -euo pipefail

# HELM only. The systemd unit templates substitute the workspace path into
# WorkingDirectory= and ExecStart=, so running this from a YARD worktree
# points a LIVE daemon at a checkout that is deleted two days later.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Honor PYTHON env override so callers can point at a venv interpreter:
#   PYTHON=/path/to/.venv-linux/bin/python ./install-odin-cadence-timer.sh
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"

# Cadence timezone: externalized so no operating locale is baked into the engine.
# Defaults to UTC; pin via HEADING_OS_TZ (e.g. America/New_York) for a local fire time.
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
