#!/usr/bin/env bash
# ============================================================
# VPS Sync Script
# ============================================================
# Pulls the latest workspace from GitHub and restarts
# services if their config files changed.
#
# Usage:
#   bash scripts/vps-sync.sh          # manual sync
#
# For automatic sync, add to crontab:
#   crontab -e
#   # Replace the path below with the absolute path to your own clone.
#   */30 * * * * "$HOME/path/to/workspace/scripts/vps-sync.sh" >> "$HOME/vps-sync.log" 2>&1
# ============================================================

set -euo pipefail

# Navigate to workspace root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$WORKSPACE_ROOT"

# Timestamp for logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== VPS Sync Started ==="
log "Workspace: $WORKSPACE_ROOT"

# Record current HEAD before pull
OLD_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "none")

# Step 1: Stash any local changes (safety net)
if ! git diff --quiet HEAD 2>/dev/null; then
    log "Local changes detected -- stashing for safety..."
    git stash push -m "vps-auto-stash-$(date +%s)"
    log "Changes stashed. Use 'git stash list' to see them."
else
    log "No local changes to stash."
fi

# Step 2: Pull latest from GitHub
log "Pulling from origin/main..."
if git pull origin main 2>&1; then
    log "Git pull successful."
else
    log "ERROR: Git pull failed. Check network or credentials."
    exit 1
fi

# Step 3: Pull LFS objects
if command -v git-lfs &>/dev/null; then
    log "Pulling LFS objects..."
    git lfs pull 2>&1
    log "LFS pull complete."
else
    log "WARNING: git-lfs not installed. Skipping LFS pull."
fi

# Get new HEAD
NEW_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "none")

# If nothing changed, we're done
if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
    log "Already up to date. No changes to process."
    log "=== VPS Sync Complete (no changes) ==="
    exit 0
fi

# Step 4: Get list of changed files
CHANGED_FILES=$(git diff --name-only "$OLD_HEAD" "$NEW_HEAD" 2>/dev/null || echo "")
log "Changed files since last sync:"
echo "$CHANGED_FILES" | while read -r f; do
    [ -n "$f" ] && log "  - $f"
done

# Step 5: Re-run platform setup (in case templates changed)
if echo "$CHANGED_FILES" | grep -q "settings.local.linux.json\|setup-platform.sh"; then
    log "Platform settings changed -- re-running setup..."
    bash scripts/setup-platform.sh
fi

# Step 6: Update Python dependencies if requirements changed
if echo "$CHANGED_FILES" | grep -q "requirements.txt"; then
    log "requirements.txt changed -- updating Python packages..."
    if [ -f ".venv/bin/pip" ]; then
        .venv/bin/pip install -r requirements.txt 2>&1
        log "Python packages updated."
    else
        log "WARNING: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    fi
fi

# Step 7: Restart Sentinel if its files changed
if echo "$CHANGED_FILES" | grep -qE "(scripts/sentinel\.py|config/sentinel_config\.yaml|scripts/sentinel_config\.example\.yaml)"; then
    log "Sentinel files changed -- restarting service..."
    if systemctl is-active --quiet sentinel 2>/dev/null; then
        sudo systemctl restart sentinel
        log "Sentinel restarted."
    else
        log "Sentinel service not running (or not installed). Skipping restart."
    fi
fi

# Step 8: Restart Fireside bot if its files changed
if echo "$CHANGED_FILES" | grep -qE "(scripts/fireside-bot\.py|scripts/fireside-bot-daemon\.py|scripts/fireside_topics\.py|scripts/fireside_webhook\.py)"; then
    log "Fireside files changed -- restarting service..."
    if systemctl is-active --quiet steward-fireside 2>/dev/null; then
        sudo systemctl restart steward-fireside
        log "steward-fireside restarted."
    else
        log "steward-fireside not running (or not installed). Skipping restart."
    fi
fi

log "=== VPS Sync Complete ==="
