#!/usr/bin/env bash
# Restart the 31C bridge daemon (Linux/WSL2 wrapper).
# Mirrors scripts/restart-bridge-daemon.ps1 (Windows).

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/restart-daemon-service.sh" bridge "$@"
