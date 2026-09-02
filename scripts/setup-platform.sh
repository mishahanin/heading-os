#!/usr/bin/env bash
# ============================================================
# Platform Setup Script
# ============================================================
# Detects the operating system and MERGES the correct
# settings.local.json template into the live settings for Claude Code hooks.
#
# Usage:  bash scripts/setup-platform.sh [--force] [--dry-run] [--check]
#
# `--check` writes nothing. It reports whether this clone's live settings file
# registers every session hook the template defines, and exits 1 when it does
# not. MEASURED 2026-09-02: a clone where this script has never run arms 1 hook
# of 17, and the 16 absent ones include the dispatcher behind eleven PreToolUse
# walls. Nothing reported that state before this flag existed.
#
# Safe to run multiple times. It did not used to be, and the header here said
# it was: the script ended in an unconditional `cp "$TEMPLATE" "$TARGET"`, and
# `scripts/vps-sync.sh` calls it from a cron whenever the template changes.
#
# MEASURED 2026-09-02 by comparing the two files on the live workspace: one run
# would have discarded 29 permission entries and three top-level keys that no
# template can carry because every one of them is per-instance. Among them
# `autoMemoryDirectory`, the pointer at the private data overlay, whose loss
# does not raise and silently redirects every memory write.
#
# It now merges: the template proposes, the live file disposes. A local value
# is kept, a template addition is added, permission lists are unioned, and the
# live file is backed up before any write. `--force` restores the old
# destructive behaviour on purpose, and says which keys it discards.
# ============================================================

set -euo pipefail

# Navigate to workspace root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"

SETTINGS_DIR="$WORKSPACE_ROOT/.claude"
TARGET="$SETTINGS_DIR/settings.local.json"

# Detect operating system
OS="$(uname -s)"

case "$OS" in
    Linux*)
        TEMPLATE="$SETTINGS_DIR/settings.local.linux.json"
        PLATFORM="Linux"
        ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT*)
        TEMPLATE="$SETTINGS_DIR/settings.local.windows.json"
        PLATFORM="Windows"
        ;;
    Darwin*)
        # macOS has its OWN template and it was reached by nothing. The comment
        # here used to read "use Linux template (same Python3 paths)", while
        # .claude/settings.local.macos.json sat beside its two siblings, was
        # maintained, was covered by tests, and was installed by no code path
        # on any platform. Prefer it; fall back to the Linux template only if
        # it is genuinely absent, so an older clone still sets up.
        if [ -f "$SETTINGS_DIR/settings.local.macos.json" ]; then
            TEMPLATE="$SETTINGS_DIR/settings.local.macos.json"
        else
            TEMPLATE="$SETTINGS_DIR/settings.local.linux.json"
        fi
        PLATFORM="macOS"
        ;;
    *)
        echo "ERROR: Unknown operating system: $OS"
        echo "Please manually copy the correct template to:"
        echo "  $TARGET"
        exit 1
        ;;
esac

# Check that the template exists
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Template not found: $TEMPLATE"
    echo "Make sure you have cloned the full repository."
    exit 1
fi

echo "Platform detected: $PLATFORM"
echo "Template:          $(basename "$TEMPLATE")"

# Is --check among the arguments? Answered before anything below, because both
# branches below WRITE and --check must never write.
CHECK_ONLY=0
for arg in "$@"; do
    if [ "$arg" = "--check" ]; then CHECK_ONLY=1; fi
done

# A clone with no live file is unarmed, and saying so needs no JSON and no
# interpreter. Answering it HERE, above the install branch, is what lets --check
# work on a fresh clone with no python3 at all, which is the one state it exists
# to detect and the one where an operator most needs a diagnosis.
#
# This is the ONLY gate between --check and the write below, deliberately. An
# earlier draft also guarded the install branch with `$CHECK_ONLY -eq 0`, and
# the mutation harness killed neither guard: each made the other unreachable,
# so breaking either one alone changed no behaviour, which is the shape of dead
# code rather than of defence in depth. One gate, and a mutation of it is fatal.
if [ "$CHECK_ONLY" -eq 1 ] && [ ! -e "$TARGET" ]; then
    echo "NOT ARMED: $TARGET does not exist, so no session hooks are registered."
    echo "  Fix: bash scripts/setup-platform.sh"
    exit 1
fi

# First install needs no interpreter: there is no live file to preserve, so a
# plain copy is both correct and the only thing that works on a clone whose
# .venv does not exist yet. This script is step 1 of setup.
if [ ! -e "$TARGET" ]; then
    cp "$TEMPLATE" "$TARGET"
    echo "Settings created:  settings.local.json"
    echo "Done."
    exit 0
fi

# A live file exists, so this is a MERGE and it needs JSON. Prefer the pinned
# interpreter, fall back to whatever python3 is on PATH.
PYTHON=""
for candidate in "$WORKSPACE_ROOT/.venv/bin/python" "$(command -v python3 || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
done

if [ -z "$PYTHON" ]; then
    if [ "$CHECK_ONLY" -eq 1 ]; then
        # Cannot tell must never read as armed.
        echo "ERROR: no python3 was found, so this clone cannot be checked." >&2
        echo "       Treat the answer as unknown, not as armed." >&2
        exit 2
    fi
    # REFUSE rather than fall back to the copy. Without an interpreter the only
    # available action is the destructive one, and the whole point of this
    # change is that the destructive one must never happen by default.
    echo "ERROR: settings.local.json already exists and no python3 was found to" >&2
    echo "       merge the template into it. Refusing to overwrite it: that would" >&2
    echo "       discard every per-instance key, including autoMemoryDirectory." >&2
    echo "       Install python3, or merge by hand." >&2
    exit 2
fi

"$PYTHON" "$SCRIPT_DIR/merge-platform-settings.py" "$TEMPLATE" "$TARGET" "$@"
echo "Done."
