#!/usr/bin/env bash
# Install the nightly router-accuracy trend runner as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-router-accuracy-timer.sh
#   HEADING_OS_TZ=Asia/Dubai scripts/install-router-accuracy-timer.sh   # pin a TZ
#   scripts/install-router-accuracy-timer.sh --uninstall                # remove the timer
#
# Renders scripts/templates/systemd/router-accuracy.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# nightly timer. The timer fires 03:00 in the configured timezone (HEADING_OS_TZ,
# default UTC) -- deliberately after the eval-drift 02:00 run so the two judge-heavy
# jobs do not overlap -- and runs scripts/router-accuracy-nightly.py, which writes a
# dated artifact + a trend.jsonl line into the DATA overlay. The runner skips under
# SENSITIVE_MODE; a > 10-point single-skill drop is surfaced separately by the
# ops-radar router_accuracy signal on the ops-radar timer, not here.
#
# This is a STANDALONE installer mirroring install-ops-radar-timer.sh's template+sed
# render convention. The timezone is read from the environment (no hardcoded locale),
# so the templates carry no geographic signal and ship in the public engine.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Honor PYTHON env override so callers can point at a venv interpreter.
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"

# Runner timezone: externalized so no operating locale is baked into the engine.
TZ_VALUE="${HEADING_OS_TZ:-UTC}"

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
    systemctl --user disable --now router-accuracy.timer 2>/dev/null || true
    rm -f "$DEST_DIR/router-accuracy.service" "$DEST_DIR/router-accuracy.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "  [ok] router-accuracy.timer uninstalled."
    exit 0
fi

if [[ -z "$PYTHON" ]]; then
    echo "No python3 (or python) on PATH" >&2
    exit 4
fi
for unit in router-accuracy.service router-accuracy.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes.
for unit in router-accuracy.service router-accuracy.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd that
# rejects the trailing timezone, rather than failing opaquely at enable).
if ! systemd-analyze calendar "*-*-* 03:00:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/router-accuracy.timer to 'OnCalendar=*-*-* 03:00' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now router-accuracy.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: router-accuracy.timer"
echo ""
echo "  Next fire:"
systemctl --user list-timers router-accuracy.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status router-accuracy.timer"
echo "  Logs:    journalctl --user -u router-accuracy.service -f"
echo "  Test:    python3 scripts/router-accuracy-nightly.py --dry-run"
echo "  Remove:  scripts/install-router-accuracy-timer.sh --uninstall"
