#!/usr/bin/env bash
# Install the session-transcript archive as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-archive-transcripts-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-archive-transcripts-timer.sh
#   PYTHON=/path/to/python scripts/install-archive-transcripts-timer.sh
#
# Renders scripts/templates/systemd/archive-transcripts.{service,timer} into
# ~/.config/systemd/user/ and enables a DAILY timer at 02:50 that runs
# `scripts/archive-transcripts.py` -- copying each settled session transcript
# into the DATA overlay, gzipped, where the normal backup already runs.
#
# Why this needs a timer at all. Claude Code deletes transcripts under
# ~/.claude/projects/ on its own schedule. A Chronicle entry keeps the DECISION;
# the transcript is the only place the reasoning behind it survives. Measured
# 2026-08-22, before the retention window was raised: 177 of 258 Chronicle
# entries already pointed at a file that no longer existed. An archiver nobody
# runs is the same outcome one step later.
#
# Safe to automate: read-only against the harness's own files, writes gzip copies
# and nothing else, never sends, never deletes a source.
#
# This mirrors scripts/install-chronicle-timer.sh (same template+sed render
# convention). It defaults PYTHON to the workspace .venv interpreter: the script
# imports the workspace package chain and a systemd unit does not inherit the
# interactive shell profile, so the venv python must be named explicitly.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# HELM only. The systemd unit templates substitute the workspace path into
# WorkingDirectory= and ExecStart=, so running this from a YARD worktree
# points a LIVE daemon at a checkout that is deleted two days later.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "$WORKSPACE/.venv/bin/python" ]]; then
        PYTHON="$WORKSPACE/.venv/bin/python"
    else
        PYTHON="$(command -v python3 || command -v python || true)"
    fi
fi

# Resolved through the workspace resolver rather than the environment alone:
# HEADING_OS_TZ lives in the gitignored .env and is exported by nothing, so an
# environment-only read renders UTC on a correctly configured machine. Invoked as
# a MODULE from the workspace root -- running the file directly puts scripts/utils
# on sys.path[0], where operator.py shadows the stdlib operator module.
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

# Check the interpreter can reach the workspace package chain before wiring a
# timer that would otherwise fail silently into journald every night.
if ! "$PYTHON" -c "import sys; sys.path.insert(0, '$WORKSPACE'); import scripts.utils.workspace" >/dev/null 2>&1; then
    echo "[error] $PYTHON cannot import scripts.utils.workspace -- the archive run will fail." >&2
    echo "        Point PYTHON at the workspace .venv." >&2
    exit 7
fi

# The DATA overlay is where the archive lands. Warn rather than block if it is
# absent: a public clone legitimately has none, and the script degrades to
# archiving nothing rather than failing.
if ! "$PYTHON" -c "
import sys; sys.path.insert(0, '$WORKSPACE')
from scripts.utils.workspace import get_data_root
raise SystemExit(0 if get_data_root().is_dir() else 1)
" >/dev/null 2>&1; then
    echo "  [warn] the DATA overlay is not present -- the timer installs, but each" >&2
    echo "         run will have nowhere to file an archive." >&2
fi

for unit in archive-transcripts.service archive-transcripts.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Portable sed (pipe markers handle slashes in paths).
for unit in archive-transcripts.service archive-transcripts.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the RENDERED calendar expression before enabling, so a too-old systemd
# that rejects a timezone suffix fails here with an explanation rather than
# opaquely at enable time.
RENDERED_CAL="$(grep -m1 '^OnCalendar=' "$DEST_DIR/archive-transcripts.timer" | sed 's|^OnCalendar=||')"
if ! systemd-analyze calendar "$RENDERED_CAL" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects the calendar expression: $RENDERED_CAL" >&2
    echo "       Drop the trailing timezone from $DEST_DIR/archive-transcripts.timer and set" >&2
    echo "       the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now archive-transcripts.timer

# Belt-and-braces for unattended firing after a reboot with no interactive login.
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: archive-transcripts.timer"
echo "       interpreter: ${PYTHON}"
echo "       status:  systemctl --user list-timers archive-transcripts.timer"
echo "       logs:    journalctl --user -u archive-transcripts.service -f"
echo "       run now: systemctl --user start archive-transcripts.service"
