#!/usr/bin/env bash
# Uninstall the 31C bridge daemon systemd user unit (Linux/WSL2 wrapper).
# Mirrors scripts/uninstall-bridge-service.ps1 (Windows).

set -euo pipefail

# Guarded here as well as in the delegate; see restart-bridge-daemon.sh for why
# "the delegate covers it" is not enough on its own.
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/uninstall-daemon-service.sh" bridge "$@"
