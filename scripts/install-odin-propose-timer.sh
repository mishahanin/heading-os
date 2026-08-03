#!/usr/bin/env bash
# Install the weekly Odin reflect-propose runner as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-odin-propose-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-odin-propose-timer.sh   # pin a TZ
#   scripts/install-odin-propose-timer.sh --uninstall                # remove the timer
#
# Renders scripts/templates/systemd/odin-propose.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# weekly timer. The timer fires Monday 05:31 in the configured timezone
# (HEADING_OS_TZ, default UTC) -- an odd minute clear of the 03:00-03:30 nightly
# batch -- and runs scripts/odin-cadence-notify.py --propose-only, which delivers a
# proposal-path message to the CEO's Telegram DM only when the headless propose flow
# produces one (armed by ODIN_REFLECT_PROPOSE_ENABLED; silent otherwise).
#
# Reboot survival is guaranteed by three mechanisms (see development-standards.md):
# Persistent=true in the .timer (missed-fire catch), `systemctl --user enable`
# (boot start), and `loginctl enable-linger` below (user units run without an
# interactive login). This is a STANDALONE installer mirroring
# install-router-accuracy-timer.sh's template+sed render convention. The timezone
# is read from the environment (no hardcoded locale), so the templates carry no
# geographic signal and ship in the public engine.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Honor PYTHON env override so callers can point at a venv interpreter.
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

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found - systemd user units require systemd >= 226." >&2
    echo "On WSL2 enable systemd via /etc/wsl.conf:" >&2
    echo "  [boot]" >&2
    echo "  systemd=true" >&2
    exit 5
fi

# Uninstall path: disable + remove the units, then reload.
if [[ "${1:-}" == "--uninstall" || "${1:-}" == "uninstall" ]]; then
    systemctl --user disable --now odin-propose.timer 2>/dev/null || true
    rm -f "$DEST_DIR/odin-propose.service" "$DEST_DIR/odin-propose.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "  [ok] odin-propose.timer uninstalled."
    exit 0
fi

if [[ -z "$PYTHON" ]]; then
    echo "No python3 (or python) on PATH" >&2
    exit 4
fi
for unit in odin-propose.service odin-propose.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes.
for unit in odin-propose.service odin-propose.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd that
# rejects the trailing timezone, rather than failing opaquely at enable).
if ! systemd-analyze calendar "Mon *-*-* 05:31:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/odin-propose.timer to 'OnCalendar=Mon *-*-* 05:31' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now odin-propose.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: odin-propose.timer"
echo ""
echo "  Next fire:"
systemctl --user list-timers odin-propose.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status odin-propose.timer"
echo "  Logs:    journalctl --user -u odin-propose.service -f"
echo "  Test:    python3 scripts/odin-cadence-notify.py --propose-only"
echo "  Remove:  scripts/install-odin-propose-timer.sh --uninstall"
