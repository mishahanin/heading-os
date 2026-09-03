#!/usr/bin/env bash
# Install the council model freshness check as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-council-models-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-council-models-timer.sh   # pin a TZ
#
# Renders scripts/templates/systemd/council-models-check.{service,timer}
# (substituting {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/,
# then enables the DAILY timer. The timer fires 08:30 in the configured timezone
# (HEADING_OS_TZ, default UTC) independent of any Claude Code session and runs
# scripts/council-models-notify.py, which pushes a one-line nudge to the
# operator's Telegram alert channel ONLY when a /council model pin is broken or a
# newer flagship is available (deduped, so it never re-nudges an unchanged
# finding). It never edits the pins -- adoption is the operator's one-command
# `python scripts/council-models.py --set ...`.
#
# STANDALONE installer mirroring scripts/install-odin-cadence-timer.sh's
# template+sed render convention (no hardcoded locale; the templates ship in the
# public engine). For unattended boot: loginctl enable-linger (done below).

set -euo pipefail

# HELM only. The systemd unit templates substitute the workspace path into
# WorkingDirectory= and ExecStart=, so running this from a YARD worktree
# points a LIVE daemon at a checkout that is deleted two days later.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
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
UNITS=(council-models-check.service council-models-check.timer)

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
for unit in "${UNITS[@]}"; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

for unit in "${UNITS[@]}"; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd
# that rejects the trailing timezone rather than failing opaquely at enable).
if ! systemd-analyze calendar "*-*-* 08:30:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/council-models-check.timer to 'OnCalendar=*-*-* 08:30' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now council-models-check.timer
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

echo "  [ok] council-models-check daily timer installed and enabled (${TZ_VALUE} 08:30)."
echo "  Status:   systemctl --user list-timers council-models-check.timer --no-pager"
echo "  Dry-run:  python3 scripts/council-models-notify.py --force"
echo "  Manual:   python  scripts/council-models.py --check"
