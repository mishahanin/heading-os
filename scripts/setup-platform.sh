#!/usr/bin/env bash
# ============================================================
# Platform Setup Script
# ============================================================
# Detects the operating system and copies the correct
# settings.local.json template for Claude Code hooks.
#
# Usage:  bash scripts/setup-platform.sh
#
# Safe to run multiple times (idempotent).
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
        # macOS — use Linux template (same Python3 paths)
        TEMPLATE="$SETTINGS_DIR/settings.local.linux.json"
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

# Copy template to active settings
cp "$TEMPLATE" "$TARGET"
echo "Platform detected: $PLATFORM"
echo "Settings copied:   $(basename "$TEMPLATE") -> settings.local.json"
echo "Done."
