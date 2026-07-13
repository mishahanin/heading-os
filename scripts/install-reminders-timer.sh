#!/usr/bin/env bash
# Install the durable reminders dispatcher as a systemd-user timer on Linux/WSL2.
#   scripts/install-reminders-timer.sh
#   HEADING_OS_TZ=Asia/Dubai scripts/install-reminders-timer.sh
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
systemctl --user list-timers reminders.timer --no-pager || true
echo "  Test:  python3 scripts/reminders-notify.py"
