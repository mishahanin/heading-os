#!/usr/bin/env bash
# Uninstall the 31C bridge daemon systemd user unit (Linux/WSL2 wrapper).
# Mirrors scripts/uninstall-bridge-service.ps1 (Windows).

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/uninstall-daemon-service.sh" bridge "$@"
