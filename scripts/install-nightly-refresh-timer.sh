#!/usr/bin/env bash
# Install the nightly refresh (full suite -> day-mode green marker, verdict store,
# warm caches) as a systemd-user timer on Linux/WSL2.
#
# Usage:
#   scripts/install-nightly-refresh-timer.sh
#   HEADING_OS_TZ=America/New_York scripts/install-nightly-refresh-timer.sh  # pin a TZ
#   scripts/install-nightly-refresh-timer.sh --check       # report state, write nothing
#   scripts/install-nightly-refresh-timer.sh --uninstall   # remove the timer
#
# Renders scripts/templates/systemd/nightly-refresh.{service,timer} (substituting
# {{WORKSPACE}}, {{PYTHON}}, {{TZ}}) into ~/.config/systemd/user/, then enables the
# nightly timer. The timer fires 01:30 in the configured timezone (HEADING_OS_TZ,
# default UTC) and runs scripts/nightly-refresh.py, which runs the FULL suite and,
# only on success, moves the day-mode green marker, records the verdict store, and
# warms the day-mode fact cache. The night side of that contract is printed by
# `python scripts/day-mode.py nightly`.
#
# This mirrors install-router-accuracy-timer.sh's template+sed render convention.
# The timezone is read through the workspace resolver (no hardcoded locale), so
# the templates carry no geographic signal and ship in the public engine.
#
# WHY --check EXISTS. An installer that was merged is not a timer that is armed,
# and the two are indistinguishable from a directory listing. Two live examples in
# this tree on 2026-09-04: `scripts/night-repair.py` line 8 says `--run` is what
# "the timer calls" and no such timer has ever existed, and `bridge-daemon.service`
# sat installed-but-DISABLED while its snapshot reader kept serving a frozen file
# under a "computed X ago" label. Both are the same failure: the artifact that says
# a thing is running is not the thing running. `--check` verifies each of the three
# reboot-survival mechanisms SEPARATELY and names which one is missing, because
# they fail independently and two of them fail silently:
#
#   1. Persistent=true in the RENDERED unit on disk
#   2. systemctl --user is-enabled nightly-refresh.timer == enabled
#   3. loginctl show-user "$USER" reports Linger=yes
#
# Mechanism 3 was already true on the operator's machine when this was written.
# It is checked anyway: it is machine-level state that a `loginctl disable-linger`,
# a new user or a fresh machine removes silently, and a check that skips a
# condition because it happened to be true once is the shape this workspace keeps
# finding. --check also reports INSTALLED-BUT-DISABLED as a failure DISTINCT from
# "not installed"; those look identical in a directory listing and are not the
# same state.
#
# Tests: tests/test_a_check_that_only_asked_whether_the_unit_file_existed.py

set -euo pipefail

# HELM only. The systemd unit templates substitute the workspace path into
# WorkingDirectory= and ExecStart=, so running this from a YARD worktree points a
# LIVE unit at a checkout that is deleted two days later. The guard sits above
# every line that reaches machine state, including --check's: a guard placed after
# one is a guard that reports on work already done.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

UNIT_NAME="nightly-refresh"

# Workspace root = directory containing this script's parent (i.e. scripts/../).
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"

TEMPLATE_DIR="$WORKSPACE/scripts/templates/systemd"
DEST_DIR="$HOME/.config/systemd/user"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found - systemd user units require systemd >= 226." >&2
    echo "On WSL2 enable systemd via /etc/wsl.conf:" >&2
    echo "  [boot]" >&2
    echo "  systemd=true" >&2
    exit 5
fi

# ------------------------------------------------------------------
# --check: report the installed state. Writes nothing, enables nothing.
# ------------------------------------------------------------------
if [[ "${1:-}" == "--check" || "${1:-}" == "check" ]]; then
    failed=0
    timer_unit="$DEST_DIR/$UNIT_NAME.timer"
    service_unit="$DEST_DIR/$UNIT_NAME.service"

    if [[ ! -f "$timer_unit" ]]; then
        echo "  [MISSING] not installed: $timer_unit does not exist." >&2
        echo "            Install it:  bash scripts/install-$UNIT_NAME-timer.sh" >&2
        failed=1
    else
        echo "  [ok] unit rendered: $timer_unit"
        if [[ ! -f "$service_unit" ]]; then
            echo "  [FAIL] the timer is installed but $service_unit is not." >&2
            echo "         A timer with no service fires into nothing." >&2
            failed=1
        fi
        # Mechanism 1: a fire missed while the machine was off must be replayed.
        if grep -qx 'Persistent=true' "$timer_unit"; then
            echo "  [ok] mechanism 1/3  Persistent=true is in the rendered timer"
        else
            echo "  [FAIL] mechanism 1/3  Persistent=true is NOT in $timer_unit." >&2
            echo "         A fire missed while the machine was off will never run." >&2
            failed=1
        fi
        if grep -qx 'WantedBy=timers.target' "$timer_unit"; then
            echo "  [ok] WantedBy=timers.target is in the rendered timer"
        else
            echo "  [FAIL] WantedBy=timers.target is NOT in $timer_unit, so" >&2
            echo "         'systemctl --user enable' has nothing to hook onto." >&2
            failed=1
        fi
        # An unsubstituted token renders a unit systemd cannot act on, and it
        # looks installed. Checked here rather than trusted from the installer.
        if grep -q '{{' "$timer_unit" "$service_unit" 2>/dev/null; then
            echo "  [FAIL] an unsubstituted {{TOKEN}} survives in the rendered units." >&2
            failed=1
        fi
    fi

    # Mechanism 2: enabled, and INSTALLED-BUT-DISABLED named as its own state.
    #
    # `not-found` belongs HERE, with "systemd does not know it", and not with
    # the disabled branch below, which is the one state it cannot mean. Routing
    # it there made `--check` print "The unit file is on disk" one line under
    # "[MISSING] ... does not exist". MEASURED 2026-09-05 in HELM, in the only
    # window the branch is reachable: before the unit was installed.
    enabled_state="$(systemctl --user is-enabled "$UNIT_NAME.timer" 2>/dev/null || true)"
    if [[ "$enabled_state" == "enabled" ]]; then
        echo "  [ok] mechanism 2/3  systemctl --user is-enabled: enabled"
    elif [[ -z "$enabled_state" || "$enabled_state" == "not-found" ]]; then
        echo "  [FAIL] mechanism 2/3  systemd does not know $UNIT_NAME.timer at all" >&2
        echo "         (is-enabled said '${enabled_state:-nothing}')." >&2
        echo "         Install it:  bash scripts/install-$UNIT_NAME-timer.sh" >&2
        failed=1
    else
        echo "  [FAIL] mechanism 2/3  INSTALLED BUT NOT ENABLED: systemctl --user" >&2
        echo "         is-enabled reports '$enabled_state'. The unit file is on disk" >&2
        echo "         and the timer will NOT fire. This is not the same state as" >&2
        echo "         'not installed', and a directory listing cannot tell them apart." >&2
        echo "         Arm it:  systemctl --user enable --now $UNIT_NAME.timer" >&2
        failed=1
    fi

    # Mechanism 3: user units need linger to run without an interactive login.
    if loginctl show-user "$USER" 2>/dev/null | grep -qx 'Linger=yes'; then
        echo "  [ok] mechanism 3/3  loginctl: Linger=yes"
    else
        echo "  [FAIL] mechanism 3/3  Linger is NOT enabled for $USER. The timer" >&2
        echo "         stays silent after an unattended reboot while looking installed." >&2
        echo "         Fix:  loginctl enable-linger $USER" >&2
        failed=1
    fi

    if [[ "$failed" -eq 0 ]]; then
        echo "  [ok] $UNIT_NAME.timer is installed, enabled and reboot-survivable."
        echo ""
        echo "  Next fire:"
        systemctl --user list-timers "$UNIT_NAME.timer" --no-pager || true
        exit 0
    fi
    echo "" >&2
    echo "  $UNIT_NAME.timer is NOT fully armed. See the [FAIL] lines above." >&2
    exit 1
fi

# Honor PYTHON env override so callers can point at a venv interpreter. The
# nightly runs pytest, which lives only in .venv; nightly-refresh.py calls
# ensure_venv() and re-execs itself if handed a bare system interpreter, so a
# plain python3 here is correct but the venv one saves a re-exec.
PYTHON="${PYTHON:-$WORKSPACE/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3 || command -v python || true)"
fi

# Unit timezone: resolved through the workspace resolver rather than read from the
# environment alone. HEADING_OS_TZ lives in the gitignored .env and is exported by
# nothing, so an environment-only read renders UTC on a machine whose timezone is
# correctly configured. An explicit HEADING_OS_TZ=X still wins. Invoked as a
# MODULE, from the workspace root: running the file directly puts scripts/utils/
# on sys.path[0], where operator.py shadows the stdlib operator that collections
# imports.
TZ_VALUE="${HEADING_OS_TZ:-$(cd "$WORKSPACE" && "$PYTHON" -m scripts.utils.paths tz || echo UTC)}"

# The PATH the night runs under, taken from the shell that installs it.
#
# A systemd user service does NOT inherit the installing shell's environment; it
# gets the user manager's, which on this machine is
# /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin
# and carries neither ~/.local/bin, nor ~/bin, nor the nvm node bin. MEASURED
# 2026-09-05: with that PATH, gh, git-lfs, node, npx, marp, uv, pre-commit,
# claude and herdr are all absent, 240 tests skipped instead of 2, and the run
# still exited 0. The night marked green over 238 checks that never executed.
#
# Taking the installer's own PATH is the honest derivation rather than a clever
# one: this script runs from the operator's interactive shell in HELM, so its
# PATH is by construction the one under which `python scripts/run-tests.py`
# passes with the baseline skip count. Nothing else on the machine knows that
# set, and there is no .env key to read it from, so the "second, staler source
# of truth" objection this directory's README raises against pinning
# HEADING_OS_TZ into a unit does not have an alternative to point at here.
#
# It CAN go stale, and that is handled rather than denied: bump the nvm node
# version and this PATH stops resolving node until the installer is re-run. The
# skip ceiling in config/nightly-skip-baseline.json is what makes that loud
# instead of silent, which is why the ceiling is the control and this line is
# the convenience. Override with TOOL_PATH=... for a deliberate value.
TOOL_PATH="${TOOL_PATH:-$PATH}"

# Uninstall path: disable + remove the units, then reload.
if [[ "${1:-}" == "--uninstall" || "${1:-}" == "uninstall" ]]; then
    systemctl --user disable --now "$UNIT_NAME.timer" 2>/dev/null || true
    rm -f "$DEST_DIR/$UNIT_NAME.service" "$DEST_DIR/$UNIT_NAME.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "  [ok] $UNIT_NAME.timer uninstalled."
    exit 0
fi

if [[ -z "$PYTHON" ]]; then
    echo "No python3 (or python) on PATH" >&2
    exit 4
fi
for unit in "$UNIT_NAME.service" "$UNIT_NAME.timer"; do
    if [[ ! -f "$TEMPLATE_DIR/$unit" ]]; then
        echo "Template not found: $TEMPLATE_DIR/$unit" >&2
        exit 3
    fi
done

mkdir -p "$DEST_DIR"

# Render both units with portable sed. Pipe markers chosen because the paths may
# contain forward slashes.
for unit in "$UNIT_NAME.service" "$UNIT_NAME.timer"; do
    sed -e "s|{{WORKSPACE}}|${WORKSPACE}|g" \
        -e "s|{{PYTHON}}|${PYTHON}|g" \
        -e "s|{{TZ}}|${TZ_VALUE}|g" \
        -e "s|{{TOOLPATH}}|${TOOL_PATH}|g" \
        "$TEMPLATE_DIR/$unit" > "$DEST_DIR/$unit"
done

# Validate the calendar expression before enabling (catches a too-old systemd that
# rejects the trailing timezone, rather than failing opaquely at enable).
if ! systemd-analyze calendar "*-*-* 01:30:00 ${TZ_VALUE}" >/dev/null 2>&1; then
    echo "[warn] this systemd rejects a timezone-suffixed OnCalendar." >&2
    echo "       Edit $DEST_DIR/$UNIT_NAME.timer to 'OnCalendar=*-*-* 01:30' and" >&2
    echo "       set the host timezone to ${TZ_VALUE}, then re-run." >&2
    exit 6
fi

# Mechanism 2 of 3: enable, so WantedBy=timers.target starts the timer at boot.
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME.timer"

# Mechanism 3 of 3, and the one that is always forgotten: without linger a user
# timer stays silent after an unattended reboot while looking installed.
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  [hint] run once for unattended boot: loginctl enable-linger $USER"
fi

echo "  [ok] systemd user timer installed and enabled: $UNIT_NAME.timer"
echo ""
echo "  Verify all three reboot-survival mechanisms in one command:"
echo "      bash scripts/install-$UNIT_NAME-timer.sh --check"
echo "  Or one at a time:"
echo "      grep Persistent $DEST_DIR/$UNIT_NAME.timer     # mechanism 1"
echo "      systemctl --user is-enabled $UNIT_NAME.timer   # mechanism 2"
echo "      loginctl show-user $USER | grep Linger         # mechanism 3"
echo ""
echo "  Next fire:"
systemctl --user list-timers "$UNIT_NAME.timer" --no-pager || true
echo ""
echo "  Status:  systemctl --user status $UNIT_NAME.timer"
echo "  Logs:    journalctl --user -u $UNIT_NAME.service -f"
echo "  Test:    python scripts/nightly-refresh.py --dry-run"
echo "  Last:    python scripts/nightly-refresh.py --status"
echo "  Remove:  scripts/install-$UNIT_NAME-timer.sh --uninstall"
