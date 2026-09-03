#!/usr/bin/env bash
# Restart the 31C bridge daemon (Linux/WSL2 wrapper).
# Mirrors scripts/restart-bridge-daemon.ps1 (Windows).

set -euo pipefail

# Guarded here as well as in the delegate. The delegate does carry the guard,
# so this is not the only wall -- but "covered by the thing it execs into" is an
# argument, and an argument is what the previous version of this rule was made
# of. Daemons are HELM's alone (CLAUDE.md, HELM and YARD).
source "$(dirname "$0")/lib/require-main-clone.sh"
require_main_clone

DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/restart-daemon-service.sh" bridge "$@"
