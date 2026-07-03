#!/usr/bin/env bash
# Demo recipe for the README terminal recording.
# Rendered to docs/assets/demo.svg with:
#   termtosvg docs/assets/demo.svg -g 92x26 -M 120 -c "bash docs/assets/demo.sh"
# Runs only read-only, data-less commands against the bundled examples/ tree,
# so it is safe to re-record on any clone with no private data.
set -u

BLUE=$'\033[38;5;69m'; ORANGE=$'\033[38;5;215m'; GRAY=$'\033[38;5;245m'
BOLD=$'\033[1m'; RESET=$'\033[0m'; GREEN=$'\033[38;5;71m'

prompt() { printf "%s~/.heading-os%s $ " "$BLUE" "$RESET"; }
type_out() { # simulate typing a command
  prompt
  local s="$1"; for ((i=0; i<${#s}; i++)); do printf "%s" "${s:$i:1}"; sleep 0.018; done
  printf "\n"; sleep 0.35
}
say() { printf "%s# %s%s\n" "$GRAY" "$1" "$RESET"; sleep 0.5; }

clear
printf "%s%sHEADING OS%s %s: the OS an executive runs their company from%s\n\n" "$BOLD" "$ORANGE" "$RESET" "$GRAY" "$RESET"
sleep 0.8

say "engine and data are kept apart. no data repo? the engine falls back to demo data."
type_out 'export HEADING_OS_DATA="$(pwd)/examples"'
export HEADING_OS_DATA="$(pwd)/examples"
sleep 0.4

type_out 'python -c "from scripts.utils.paths import get_data_root, data_root_is_demo as d; print(get_data_root()); print(\"demo mode:\", d())"'
python -c "import os,sys; sys.path.insert(0,'.'); from scripts.utils.paths import get_data_root, data_root_is_demo as d; print(os.path.relpath(get_data_root())+'/'); print('demo mode:', d())" 2>/dev/null
sleep 0.9

say "run a real skill against it. /crm radar surfaces contacts you have drifted from."
type_out 'python scripts/crm-health.py     # the engine behind /crm radar'
python scripts/crm-health.py 2>/dev/null
sleep 1.2

printf "\n%s%s✓%s %sread-only, no API, no models. your real data stays in a private repo the engine never touches.%s\n" "$BOLD" "$GREEN" "$RESET" "$GRAY" "$RESET"
sleep 1.6
