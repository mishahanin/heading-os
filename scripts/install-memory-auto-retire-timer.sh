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
# (substituting {{WORKSPACE}} and {{PYTHON}}) into ~/.config/systemd/user/, and
# ENABLES NOTHING. The rendered timer would fire daily (07:20 in the configured
# zone, Persistent) and run
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
# This installer does NOT call `loginctl enable-linger`, and does not need to:
# it enables no timer (see the RETIRED note at the bottom). It claimed linger was
# "done automatically below" until 2026-09-02, when the enable it accompanied was
# removed. Reversing the directive means running BOTH commands the script prints,
# and linger is one of them.

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

# HELM only, and deliberately BELOW the retirement gate above rather than at the
# top like its eighteen siblings. That gate is asserted to be the first
# statement after `set -euo pipefail`, ahead of the workspace-root resolution
# and the interpreter probe, and its test pins PATH to `dirname` alone to prove
# nothing runs before it. A clone-type check placed first would answer with
# exit 2 instead of the refusal this script exists to give, and would need git
# on a PATH the caller deliberately emptied. Nothing is weakened: the gate
# already stops the default run outright, and this guard still runs before any
# unit is rendered or any directory created.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone
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

# RETIRED: the timer is RENDERED but never ENABLED, even past the override.
#
# The operator disabled memory-auto-retire.timer by hand on 2026-08-07 under the
# standing directive that auto-memory is never pruned or retired on a clock
# (`.claude/rules/memory-discipline.md`: "auto-memory is never pruned, and
# deletion happens only on an explicit instruction from the operator"). The
# MACHINE is right; an installer that re-armed it would silently overturn that
# decision on the next fresh clone or re-run.
#
# What fires here is not an annotation. `scripts/memory-auto-retire.py` calls
# `retire_memory()`, which `unlink()`s the record from EVERY store (the canonical
# DATA auto-memory plus every native harness store), then strips its pointer line
# out of MEMORY.md. The service's ExecStart passes no `--dry-run`. So the cost of
# getting this line wrong is deleted memories, not a noisy log.
#
# Same shape as scripts/install-odin-cadence-timer.sh: render the units so
# reversing the decision stays a one-line change, and leave arming the timer as a
# separate, explicit act by the operator. If a stale enabled instance exists from
# an install predating this change, disable it.
#
# Do NOT "fix" this back to `enable --now`. Reversing the directive is the
# operator's call, and it is the two commands echoed below, typed by hand.
systemctl --user disable --now memory-auto-retire.timer 2>/dev/null || true

echo "  [ok] memory-auto-retire units rendered; timer NOT enabled (never armed on a clock)."
echo "       interpreter: ${PYTHON}"
echo ""
echo "  The units are on disk and inert. Arming them DELETES memories that carry an"
echo "  expired 'expires:' date, from every store, and strips their MEMORY.md pointers."
echo "  If the no-prune directive is genuinely being reversed, arm it by hand:"
echo "      systemctl --user enable --now memory-auto-retire.timer"
echo "      loginctl enable-linger \"\$USER\"   # or it stays silent after a reboot"
echo ""
echo "  Dry run (mutates nothing):  ${PYTHON} ${WORKSPACE}/scripts/memory-auto-retire.py --dry-run"
echo "  Logs:    journalctl --user -u memory-auto-retire.service -f"
