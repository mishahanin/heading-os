#!/usr/bin/env bash
# Install the 31C bridge daemon as a systemd user unit (Linux/WSL2 wrapper).
#
# This is a thin wrapper around install-daemon-service.sh so the bridge
# install command mirrors the Windows / macOS pair:
#   install-bridge-service.ps1      (Windows: Startup-folder shortcut)
#   install-bridge-service-mac.py   (macOS: launchd agent)
#   install-bridge-service.sh       (Linux: systemd user unit) <-- this file

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/install-daemon-service.sh" bridge "$@"
