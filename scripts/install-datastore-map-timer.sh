#!/usr/bin/env bash
# Install the daily datastore-map refresh as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-datastore-map-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-datastore-map-timer.sh   # pin a TZ
#   scripts/install-datastore-map-timer.sh --uninstall                # remove the timer
#
# Renders scripts/templates/systemd/datastore-map.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# timer. It fires 03:20 in the configured timezone (HEADING_OS_TZ, default UTC)
# and runs scripts/datastore-map.py, which regenerates reference/datastore-map.md
# inside the PRIVATE data overlay.
#
# WHY a timer at all: the map used to be a hand-written section inside
# .claude/rules/datastore.md. It was written on 2026-04-20, never regenerated,
# and by 2026-09-02 it had drifted far enough to omit three whole top-level
# directories. A generated inventory only stays honest if something regenerates
# it without being asked.
#
# WHY 03:20: it sits in the gap between dream-shadow at 03:10 and
# memory-index-refresh at 03:30, so a freshly written map is picked up by the
# index the same night rather than the next one.
#
# DO NOT install this on a pull-only mirror such as the Steward VM. That host
# runs `git pull --ff-only` and never commits, so one local write to a tracked
# file aborts every later pull. The generator already refuses when
# HEADING_OS_DATA_READONLY is set, which is the belt; not installing the timer
# there is the braces.
#
# This is a STANDALONE installer mirroring install-router-accuracy-timer.sh's
# template+sed render convention. The timezone is read from the environment (no
# hardcoded locale), so the templates carry no geographic signal and ship in the
# public engine.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# HELM only. The systemd unit templates substitute the workspace path into
# WorkingDirectory= and ExecStart=, so running this from a YARD worktree
# points a LIVE daemon at a checkout that is deleted two days later.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

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
    systemctl --user disable --now datastore-map.timer 2>/dev/null || true
    rm -f "$DEST_DIR/datastore-map.service" "$DEST_DIR/datastore-map.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "  [ok] datastore-map.timer uninstalled."
    exit 0
fi

if [[ -z "$PYTHON" ]]; then
    echo "No python3 (or python) on PATH" >&2
    exit 4
fi
for unit in datastore-map.service datastore-map.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes.
for unit in datastore-map.service datastore-map.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd that
# rejects the trailing timezone, rather than failing opaquely at enable).
if ! systemd-analyze calendar "*-*-* 03:20:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/datastore-map.timer to 'OnCalendar=*-*-* 03:20' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now datastore-map.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: datastore-map.timer"
echo ""
echo "  Next fire:"
systemctl --user list-timers datastore-map.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status datastore-map.timer"
echo "  Logs:    journalctl --user -u datastore-map.service -f"
echo "  Test:    python3 scripts/datastore-map.py --stdout"
echo "  Verify:  python3 scripts/datastore-map.py --check"
echo "  Remove:  scripts/install-datastore-map-timer.sh --uninstall"
