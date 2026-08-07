#!/usr/bin/env bash
# RETIRED AND DISABLED. Clock-driven auto-retire of auto-memory is switched off.
#
# The standing directive is that auto-memory is NEVER pruned: a memory that has
# gone unused sinks in recall ranking and stays retrievable, and removal happens
# only when the operator explicitly asks for it. /dream no longer stamps
# `expires:`, so this timer's only trigger no longer accrues -- but a timer left
# installable is a timer a fresh clone re-arms, and this one deletes memories.
#
# The mechanism is switched off, not removed: the script and its units stay on
# disk so reversing the decision is a one-line change rather than an
# archaeology exercise. This installer therefore REFUSES by default. A warning
# would have been the softer choice and the wrong one -- it changes nothing
# mechanically, so a clone that ignores it ends up in exactly the state the
# directive forbids, with deletion armed and nobody aware.
#
# Usage:
#   scripts/install-memory-auto-retire-timer.sh                  # refuses, exits 9
#   scripts/install-memory-auto-retire-timer.sh --i-am-reversing-the-no-prune-directive
#   MEMORY_AUTO_RETIRE_OVERRIDE=1 scripts/install-memory-auto-retire-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-memory-auto-retire-timer.sh   # pin a TZ
#   PYTHON=/path/to/python scripts/install-memory-auto-retire-timer.sh   # override interpreter
#
# Renders scripts/templates/systemd/memory-auto-retire.{service,timer}
# (substituting {{WORKSPACE}} and {{PYTHON}}) into ~/.config/systemd/user/, then
# enables a DAILY timer (07:20 host-local, Persistent) that runs
# `scripts/memory-auto-retire.py` -- the safe, deterministic slice of /dream that
# retires ONLY memories whose author stamped an explicit expires: date now in the
# past. Orphans, redundancy pairs, and rewording stay a human-gated /dream call.
#
# This is a STANDALONE installer mirroring scripts/install-memory-hygiene-timer.sh
# (same template+sed render convention). It defaults PYTHON to the workspace
# .venv interpreter: the script imports the workspace utils (pyyaml-backed
# frontmatter + routing), and a systemd unit does not inherit the interactive
# shell profile -- so the venv python must be named explicitly.
#
# For unattended boot:  loginctl enable-linger "$USER"  (done automatically below)

set -euo pipefail

# The retirement gate. First thing, before any path is resolved or unit rendered.
OVERRIDE="${MEMORY_AUTO_RETIRE_OVERRIDE:-}"
if [[ "${1:-}" == "--i-am-reversing-the-no-prune-directive" ]]; then
    OVERRIDE=1
    shift
fi
if [[ -z "$OVERRIDE" ]]; then
    echo "[refused] memory auto-retire is RETIRED and disabled." >&2
    echo "          Auto-memory is never pruned on a clock. A memory that has gone" >&2
    echo "          unused sinks in recall ranking and stays retrievable; removal is" >&2
    echo "          an explicit operator instruction, run through" >&2
    echo "          scripts/retire-memory.py by hand." >&2
    echo "          /dream no longer stamps expires:, so this timer's only trigger" >&2
    echo "          no longer accrues. See docs/memory-lifecycle.md." >&2
    echo "" >&2
    echo "          If the directive is being reversed deliberately, re-run with" >&2
    echo "          --i-am-reversing-the-no-prune-directive (or set" >&2
    echo "          MEMORY_AUTO_RETIRE_OVERRIDE=1)." >&2
    exit 9
fi
echo "[warn] installing a timer that DELETES memories. The no-prune directive is" >&2
echo "       being overridden deliberately (docs/memory-lifecycle.md)." >&2

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

# Default to the workspace venv; allow PYTHON override.
if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "$WORKSPACE/.venv/bin/python" ]]; then
        PYTHON="$WORKSPACE/.venv/bin/python"
    else
        PYTHON="$(command -v python3 || command -v python || true)"
    fi
fi

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

# Sanity-check the interpreter can import the workspace utils (pyyaml) the script
# relies on for frontmatter parsing, before wiring a timer that would otherwise
# fail silently into journald every day.
if ! "$PYTHON" -c "import yaml" >/dev/null 2>&1; then
    echo "[error] $PYTHON lacks pyyaml -- the script's frontmatter parse will degrade." >&2
    echo "        Point PYTHON at the workspace .venv (pyyaml present there)." >&2
    exit 7
fi

for unit in memory-auto-retire.service memory-auto-retire.timer; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed (pipe markers tolerate slashes in paths).
for unit in memory-auto-retire.service memory-auto-retire.timer; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the RENDERED calendar expression before enabling, so a too-old systemd
# that rejects a timezone suffix fails here with an explanation rather than
# opaquely at enable time.
RENDERED_CAL="$(grep -m1 '^OnCalendar=' "$DEST_DIR/memory-auto-retire.timer" | sed 's|^OnCalendar=||')"
if ! systemd-analyze calendar "$RENDERED_CAL" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects the calendar expression: $RENDERED_CAL" >&2
    echo "       Drop the trailing timezone from $DEST_DIR/memory-auto-retire.timer and set" >&2
    echo "       the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

systemctl --user daemon-reload
systemctl --user enable --now memory-auto-retire.timer

# Belt-and-braces for unattended firing (the bridge daemon already holds WSL up).
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: memory-auto-retire.timer"
echo "       interpreter: ${PYTHON}"
echo ""
echo "  Next fire:"
systemctl --user list-timers memory-auto-retire.timer --no-pager || true
echo ""
echo "  Status:  systemctl --user status memory-auto-retire.timer"
echo "  Logs:    journalctl --user -u memory-auto-retire.service -f"
echo "  Test:    systemctl --user start memory-auto-retire.service  # run a pass now"
