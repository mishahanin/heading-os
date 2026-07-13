#!/usr/bin/env bash
# Install the durable reminders dispatcher as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-reminders-timer.sh
#   HEADING_OS_TZ=Asia/Dubai scripts/install-reminders-timer.sh   # pin a TZ
#
# Renders scripts/templates/systemd/reminders.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# daily timer. The timer fires 07:45 in the configured timezone (HEADING_OS_TZ,
# default UTC) independent of any Claude Code session and runs
# scripts/reminders-notify.py, which is notify-only -- it dispatches due one-off
# and recurring reminders to the operator's Telegram alert channel and never
# executes any action on their behalf. Persistent=true on the timer catches a
# fire missed while the host was off, so a reminder whose date passed while
# asleep still fires on next boot.
#
# This is a STANDALONE installer mirroring install-ops-radar-timer.sh's
# template+sed render convention. The timezone is read from the environment (no
# hardcoded locale), so the templates carry no geographic signal and ship in the
# public engine.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
TZ_VALUE="${HEADING_OS_TZ:-UTC}"
TEMPLATE_DIR="$WORKSPACE/scripts/templates/systemd"
DEST_DIR="$HOME/.config/systemd/user"
if [[ -z "$PYTHON" ]]; then echo "No python3 on PATH" >&2; exit 4; fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found - need systemd >= 226 (WSL2: enable systemd in /etc/wsl.conf)" >&2; exit 5; fi
for unit in reminders.service reminders.timer; do
  [[ -f "$TEMPLATE_DIR/$unit" ]] || { echo "Template not found: $TEMPLATE_DIR/$unit" >&2; exit 3; }
done
mkdir -p "$DEST_DIR"
for unit in reminders.service reminders.timer; do
  sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" -e "s|{{PYTHON}}|${PYTHON}|g" -e "s|{{TZ}}|${TZ_VALUE}|g" \
    "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done
if ! systemd-analyze calendar "*-*-* 07:45:00 ${TZ_VALUE}" >/dev/null 2>&1; then
  echo "[warn] this systemd rejects a timezone-suffixed OnCalendar; edit the timer to 'OnCalendar=*-*-* 07:45' and set host TZ to ${TZ_VALUE}." >&2; exit 6; fi
systemctl --user daemon-reload
systemctl --user enable --now reminders.timer
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
  loginctl enable-linger "$USER" 2>/dev/null || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"; fi
echo "  [ok] systemd user timer installed and enabled: reminders.timer"
echo ""
echo "  Next fire:"
systemctl --user list-timers reminders.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status reminders.timer"
echo "  Logs:    journalctl --user -u reminders.service -f"
echo "  Test:    python3 scripts/reminders-notify.py"
