#!/usr/bin/env bash
#
# yard-bootstrap.sh — provisions a YARD, and proves the engine's guards can fire
# in it before any agent starts.
#
# Runs automatically on Herdr's `worktree.created` event. Also runnable by hand:
#
#   cd <yard> && FORCE_BOOTSTRAP=1 HERDR_PLUGIN_EVENT_JSON='{"worktree":{"path":"'"$PWD"'"}}' \
#     bash scripts/herdr/heading-os-yard/yard-bootstrap.sh
#   bash scripts/herdr/heading-os-yard/yard-bootstrap.sh --doctor-only
#
# THE ORDER OF THE STEPS IS LOAD-BEARING. Each one is where it is because of a
# measurement, and moving it re-opens a defect that has already been paid for:
#
#   * `.venv` DOES NOT EXIST in a fresh worktree (gitignored). MEASURED
#     2026-09-03. So the status file is written with `printf` and nothing calls
#     `.venv/bin/python` before `uv sync` at step 4. A draft called it at step 1
#     and failed on the first line of every YARD, always.
#   * `jq` IS NOT INSTALLED on this machine. MEASURED 2026-09-03. The event JSON
#     is parsed with the system `python3` (3.12.3, standard library only). A
#     draft used `jq` under `command -v jq`, so on this machine the condition was
#     simply false and the script silently fell back to `$PWD` -- a directory it
#     did not choose.
#   * STATUS_FILE is made ABSOLUTE only after `cd` into the worktree. A relative
#     path resolves against the plugin runner's working directory, which is
#     unknown; a draft's own diagram showed the file landing in HELM. Worse, the
#     idempotency check reads the same relative path, so one stale `status: ok`
#     in HELM would make every future bootstrap exit 0 and provision nothing.
#   * ONE trap. A draft installed `trap cleanup EXIT INT TERM` at step 10 over
#     `trap write_status ERR INT TERM` from step 1, so an interrupt during the
#     probe removed the decoy and left the status at `in_progress` forever.
#   * Every refusal calls `fail`. An explicit `exit 1` inside `if ! ...` does not
#     run an ERR trap, so the two most likely failure points would have recorded
#     nothing at all.
#   * `$PWD` IS NEVER A FALLBACK. If the event does not say where the worktree
#     is, this stops. A directory nobody chose is the failure shape this whole
#     design exists to remove.
#
set -uo pipefail

HERDR="${HERDR_BIN_PATH:-herdr}"
AGENT_CMD="${HEADING_OS_AGENT_CMD:-claude}"
AUTOSTART="${HEADING_OS_AUTOSTART:-1}"
BOOTSTRAP_VERSION="5.0"

DOCTOR_ONLY=0
[ "${1:-}" = "--doctor-only" ] && DOCTOR_ONLY=1

log() { printf '[YARD] %s\n' "$*" >&2; }

# ─────────────────────────────────────────────────────────────
# 0. Where were we called for?
# ─────────────────────────────────────────────────────────────
CTX="${HERDR_PLUGIN_EVENT_JSON:-${HERDR_PLUGIN_CONTEXT_JSON:-}}"
WT_PATH=""; WS_ID="${HERDR_WORKSPACE_ID:-}"; PANE_ID="${HERDR_PANE_ID:-}"; BRANCH=""

if [ -n "$CTX" ] && command -v python3 >/dev/null 2>&1; then
  # Several candidate keys: Herdr documents the event NAME (`worktree.created`)
  # but not the payload's shape per field, so this reads the plausible spellings
  # and stops if none of them yields a directory.
  read -r WT_PATH WS_ID_J PANE_ID_J BRANCH <<EOF
$(printf '%s' "$CTX" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("- - - -"); raise SystemExit(0)
if not isinstance(d, dict):
    print("- - - -"); raise SystemExit(0)
def dig(*path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key, "")
    return cur if isinstance(cur, str) else ""
print(dig("worktree","path") or dig("worktree","cwd") or dig("workspace","cwd")
      or dig("cwd") or "-",
      dig("workspace","workspace_id") or dig("workspace_id") or "-",
      dig("workspace","root_pane","pane_id") or dig("pane","pane_id")
      or dig("pane_id") or "-",
      dig("worktree","branch") or dig("branch") or "-")
')
EOF
  [ "${WT_PATH:-}" = "-" ] && WT_PATH=""
  [ "${BRANCH:-}"  = "-" ] && BRANCH=""
  [ -z "$WS_ID" ]   && [ "${WS_ID_J:-}" != "-" ]   && WS_ID="${WS_ID_J:-}"
  [ -z "$PANE_ID" ] && [ "${PANE_ID_J:-}" != "-" ] && PANE_ID="${PANE_ID_J:-}"
fi

if [ -z "$WT_PATH" ] && [ -n "$WS_ID" ] && command -v python3 >/dev/null 2>&1; then
  WT_PATH="$("$HERDR" workspace get "$WS_ID" 2>/dev/null | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["result"]["workspace"]["cwd"])
except Exception:
    pass')"
fi

if [ -z "$WT_PATH" ] || [ ! -d "$WT_PATH" ]; then
  log "STOP: the event did not say which worktree to provision, and this"
  log "      script does not guess. Nothing was changed."
  "$HERDR" notification show "YARD: bootstrap could not find the worktree" \
    --body "HERDR_PLUGIN_EVENT_JSON carried no usable path" >/dev/null 2>&1 || true
  exit 1
fi

# Somebody else's repository: do nothing, quietly, and succeed.
if [ ! -f "$WT_PATH/scripts/utils/paths.py" ] || [ ! -f "$WT_PATH/pyproject.toml" ]; then
  log "not a HEADING OS engine checkout — nothing to do"
  exit 0
fi

cd "$WT_PATH" || { log "STOP: cannot enter $WT_PATH"; exit 1; }

# ─────────────────────────────────────────────────────────────
#  Now, and only now, the status file can have an absolute path.
# ─────────────────────────────────────────────────────────────
STATUS_FILE="$(pwd)/.claude/.yard-bootstrap-status"
mkdir -p "$(dirname "$STATUS_FILE")"
LAST_STEP=0
PROBE=""

write_status() {   # write_status <status> <step>
  printf '{"status":"%s","step":%s,"timestamp":"%s","version":"%s"}\n' \
    "$1" "$2" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BOOTSTRAP_VERSION" \
    > "${STATUS_FILE}.tmp" && mv "${STATUS_FILE}.tmp" "$STATUS_FILE"
}

badge() {
  [ -n "$PANE_ID" ] || return 0
  "$HERDR" pane report-metadata "$PANE_ID" --source heading-os.yard \
    --token "hos=$1" >/dev/null 2>&1 || true
}

cleanup_probe() {
  [ -n "$PROBE" ] && rm -f "$PROBE"
  PROBE=""
  return 0
}

fail() {           # fail <step> <reason>
  cleanup_probe
  write_status "failed" "$1"
  log "STOP at step $1: $2"
  badge "BROKEN"
  "$HERDR" notification show "YARD: the engine/data contour is broken" \
    --body "step $1: $2" --sound request >/dev/null 2>&1 || true
  exit 1
}

# ONE trap for the signals, and one for EXIT that only tidies. Every deliberate
# refusal goes through `fail` instead of relying on an ERR trap, because an
# explicit `exit` inside `if ! ...` does not fire one.
on_signal() { cleanup_probe; write_status "failed" "$LAST_STEP"; exit 1; }
trap on_signal INT TERM
trap cleanup_probe EXIT

# Idempotency: never touch a healthy YARD.
if [ "$DOCTOR_ONLY" -eq 0 ] && [ -f "$STATUS_FILE" ]; then
  CURRENT="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("status", "unknown"))
except Exception:
    print("unknown")' "$STATUS_FILE" 2>/dev/null || echo unknown)"
  if [ "$CURRENT" = "ok" ] && [ "${FORCE_BOOTSTRAP:-0}" != "1" ]; then
    log "bootstrap already complete; FORCE_BOOTSTRAP=1 to run it again"
    exit 0
  fi
fi

# ─────────────────────────────────────────────────────────────
# 1. HELM, from git. No path arithmetic.
# ─────────────────────────────────────────────────────────────
LAST_STEP=1; write_status "in_progress" 1
HELM_ROOT="$(git rev-parse --git-common-dir 2>/dev/null)" \
  || fail 1 "not a git repository"
case "$HELM_ROOT" in
  /*) : ;;
  *)  HELM_ROOT="$(cd "$HELM_ROOT" 2>/dev/null && pwd)" || fail 1 "cannot resolve the shared git directory" ;;
esac
HELM_ROOT="${HELM_ROOT%/.git}"
[ -d "$HELM_ROOT" ] || fail 1 "HELM does not exist at $HELM_ROOT"
log "HELM: $HELM_ROOT"

if [ "$DOCTOR_ONLY" -eq 0 ]; then
  # ───────────────────────────────────────────────────────────
  # 2. The environment: copied, then CORRECTED.
  #
  # `WORKSPACE_ROOT` is stripped, and this is the step nobody had. MEASURED
  # 2026-09-03: `get_workspace_root()` reads that variable FIRST, and every
  # guard downstream derives its inspected tree from that call. One line
  # carried over from HELM points tree-clean, the leak guard and
  # classification-health at the untouched main clone. Nothing errors. Each
  # reports clean. It is the 2026-06-22 failure shape written in a config file.
  # ───────────────────────────────────────────────────────────
  LAST_STEP=2
  DATA_ROOT="${HEADING_OS_DATA:-$(cd "$HELM_ROOT/.." && pwd)/.heading-os-data}"
  [ -d "$DATA_ROOT" ] || fail 2 "no data overlay at $DATA_ROOT"
  if [ -f "$HELM_ROOT/.env" ]; then cp "$HELM_ROOT/.env" .env; else : > .env; fi
  grep -v -E '^[[:space:]]*(WORKSPACE_ROOT|HEADING_OS_DATA)=' .env > .env.tmp \
    && mv .env.tmp .env
  printf '\nHEADING_OS_DATA=%s\n' "$DATA_ROOT" >> .env
  chmod 600 .env 2>/dev/null || true
  log "HEADING_OS_DATA=$DATA_ROOT"
  write_status "in_progress" 2

  # ───────────────────────────────────────────────────────────
  # 3. Close publishing from this copy.
  #
  # The engine repository is PUBLIC, so any branch pushed from a task is
  # visible immediately, not only `main`. `--worktree` binds the setting to
  # this checkout alone and needs `extensions.worktreeConfig`, which is
  # already true on this repository (MEASURED 2026-09-03).
  # ───────────────────────────────────────────────────────────
  LAST_STEP=3
  if [ "$(git -C "$HELM_ROOT" config --get extensions.worktreeConfig 2>/dev/null)" != "true" ]; then
    git -C "$HELM_ROOT" config extensions.worktreeConfig true \
      || fail 3 "cannot enable per-worktree config in HELM"
  fi
  git config --worktree remote.origin.pushurl "DISABLED://use-helm-to-publish" \
    || fail 3 "cannot disable the push url for this worktree"
  write_status "in_progress" 3

  # ───────────────────────────────────────────────────────────
  # 4. Dependencies. Nothing above here used .venv, because there was none.
  #
  # The flags are NOT optional, and they are the ones CLAUDE.md § Setup step 2
  # already prescribes for a fresh clone: `uv sync --all-extras --group dev`.
  # A YARD is a fresh clone in every sense that matters here, so it needs the
  # same environment; anything less builds a checkout that cannot run the suite
  # it exists to run.
  #
  # This read `uv sync --quiet` until 2026-09-03, which installs the core
  # dependencies only. MEASURED 2026-09-03 in the YARD at .yard/.heading-os/
  # test-123 against HELM: `import telethon` and `import cryptography` both
  # succeeded in HELM's .venv and both raised ModuleNotFoundError in the YARD's,
  # and the full suite reported 229 failures there against a HELM-shaped
  # environment. The failures were pure environment noise -- absent optional
  # extras, plus pytest-xdist and pre-commit from the dev group.
  #
  # Why that is worse than a slow first run. A YARD exists so engine work can be
  # judged in isolation, and a task cannot tell its own regression from 229
  # pre-existing failures it did not cause. The bare sync did not slow the YARD
  # down; it removed the YARD's reason to exist. The first run costs more
  # wall-clock, once, per worktree.
  #
  # `--all-extras` covers every name in [project.optional-dependencies]. The
  # `all` extra there is an aggregate of the other nine, so `--all-extras` and
  # `--extra all` resolve to the same set; the flag is used because it needs no
  # maintenance when a tenth extra lands.
  # ───────────────────────────────────────────────────────────
  LAST_STEP=4; badge "setup"
  command -v uv >/dev/null 2>&1 || fail 4 "uv is not on PATH"
  uv sync --all-extras --group dev --quiet \
    || fail 4 "uv sync --all-extras --group dev failed"
  write_status "in_progress" 4

  # ───────────────────────────────────────────────────────────
  # 5. Arm the PreToolUse walls IN THIS COPY.
  #
  # `.claude/settings.local.json` is gitignored, so a fresh worktree does not
  # have it (MEASURED 2026-09-03) and ELEVEN walls are unregistered, including
  # the release gate and the secret scanner. `setup-platform.sh` derives the
  # workspace from its own `${BASH_SOURCE[0]}`, so running THIS copy writes
  # into THIS copy and leaves HELM alone.
  # ───────────────────────────────────────────────────────────
  LAST_STEP=5
  ./scripts/setup-platform.sh >/dev/null 2>&1 || fail 5 "setup-platform.sh failed"

  # The overlay write guard, armed and PROVEN. It belongs to this step because
  # this is the arming step, and it comes after step 4 because `uv sync`
  # rebuilds site-packages and deletes the `.pth` that arms the guard outside
  # pytest. Nothing put it back, so MEASURED 2026-09-03 every YARD on this
  # machine ran with the guard off in every process except pytest.
  #
  # A report is not proof, and this is the case that shows why. The guard also
  # reported itself armed while wrapping NOTHING, because
  # `_structural_overlay_root()` looked for `.heading-os-data` beside the
  # checkout and a worktree does not sit beside it. Armed-over-nothing and
  # armed-and-working printed the same word. So the check below is a real
  # write into the real overlay that MUST be refused, in the same spirit as the
  # canary at step 10.
  ".venv/bin/python" scripts/overlay-guard-install.py --install >/dev/null 2>&1 \
    || fail 5 "the overlay write guard could not be installed into this venv"
  HEADING_OS_OVERLAY_GUARD=refuse ".venv/bin/python" - <<'PROBE' \
    || fail 5 "the overlay write guard did not refuse a write into the operator's overlay"
import os
import sys

sys.path.insert(0, os.getcwd())
from scripts.utils.overlay_write_guard import _structural_overlay_root

root = _structural_overlay_root()
if root is None:
    # A public clone with no overlay beside it. Nothing to guard, and saying so
    # is honest; claiming a passed proof would not be.
    print("no overlay beside this clone", file=sys.stderr)
    sys.exit(0)

probe = os.path.join(str(root), ".yard-overlay-guard-canary")
try:
    with open(probe, "w") as handle:
        handle.write("canary")
except Exception:
    sys.exit(0)          # refused: the guard is real

# It did NOT refuse. Remove what should never have been written, then fail.
try:
    os.unlink(probe)
except OSError:
    pass
sys.exit(1)
PROBE
  write_status "in_progress" 5

  LAST_STEP=6
  .venv/bin/python -c '
import json, sys
cfg = json.load(open(".claude/settings.local.json"))
pre = cfg.get("hooks", {}).get("PreToolUse", [])
if not any("_dispatch.py" in json.dumps(entry) for entry in pre):
    sys.exit(1)' \
    || fail 6 "the PreToolUse walls are not registered in this copy"
  write_status "in_progress" 6
fi

# ─────────────────────────────────────────────────────────────
# 7. The data root resolves OUTSIDE the engine.
#
# Sibling auto-discovery cannot work from here: a YARD lives under
# `.yard/<name>/`, so `../.heading-os-data` is `.yard/.heading-os-data`, which
# does not exist, and the seam falls through to demo mode. MEASURED
# 2026-09-03: `get_data_root()` answered `<worktree>/examples`.
# ─────────────────────────────────────────────────────────────
LAST_STEP=7
.venv/bin/python -c 'from scripts.utils.paths import assert_data_root_external; assert_data_root_external()' \
  || fail 7 "the data root did not resolve outside this checkout"
write_status "in_progress" 7

# 8. And the WORKSPACE root is THIS copy. Checks the result, not the intent.
LAST_STEP=8
.venv/bin/python -c '
import pathlib, sys
from scripts.utils.paths import get_workspace_root
here = pathlib.Path.cwd().resolve()
resolved = get_workspace_root().resolve()
if resolved != here:
    print(f"get_workspace_root()={resolved} but this checkout is {here}",
          file=sys.stderr)
    sys.exit(1)' \
  || fail 8 "the workspace root points somewhere other than this checkout (WORKSPACE_ROOT set?)"
write_status "in_progress" 8

# 9. The tree-clean wall passes on a clean tree.
LAST_STEP=9
.venv/bin/python -m pytest tests/test_engine_tree_clean.py -q >/dev/null 2>&1 \
  || fail 9 "tests/test_engine_tree_clean.py fails on a clean tree here"
write_status "in_progress" 9

# ─────────────────────────────────────────────────────────────
# 10. THE CANARY. The point of this whole script.
#
# Confirming a guard is switched on is not enough: a guard pointed at the wrong
# tree also reports clean, and the two are indistinguishable by their result.
# So a decoy goes into a path the engine itself classifies as private, and the
# wall is REQUIRED to fail on it.
#
# The probe path is chosen, not assumed. `repo_carried_paths()` asks git with
# `ls-files --others --exclude-standard`, so a file git IGNORES is invisible to
# the wall. MEASURED 2026-09-03 against the eight private directories the
# original specification told this script to try: SIX are gitignored, and only
# `docs/security/` and `auto-memory/` route non-engine AND are visible.
#
# The candidates now come from `config/routing-map.yaml` itself rather than from
# a list written here, which changes that measurement: over the map's 80 rules,
# 7 candidates survive the same filter, not 2. Two reasons for the swap, and the
# second is why the list was replaced rather than trimmed to the two that
# worked. A private directory added to the map next month becomes a candidate
# without anyone remembering this file. And a literal like the CRM contacts
# directory sitting in engine code is exactly the shape `scripts/leak-guard.py`
# exists to refuse; it refused two of the eight on 2026-09-03, and it was right
# to, because a reader cannot tell a path being TESTED from a path being USED.
#
# `.claude/` is excluded. Its subtrees are this agent's own runtime -- session
# transcripts, hook state -- and the first survivor in sort order is
# `.claude/projects/`. A decoy is a file something else may be writing to at the
# same moment, and the canary needs a directory that is inert.
#
# If no candidate survives the filter, this REFUSES. The earlier version logged
# "guards not confirmed" and carried on, which is a script behaving in the most
# dangerous case exactly as it behaves in the safe one.
# ─────────────────────────────────────────────────────────────
LAST_STEP=10
PROBE="$(.venv/bin/python -c '
import subprocess, sys
try:
    from scripts.utils.workspace import get_routing_destination, load_routing_map
except Exception:
    sys.exit(0)
try:
    keys = sorted(load_routing_map()["rules"])
except Exception:
    sys.exit(0)
for key in keys:
    if key.startswith(".claude/"):
        continue
    # A rule may name a FILE. Appending the probe name to one would ask git
    # about a path under a regular file and then mkdir a directory named after
    # it, so keep only keys whose last segment carries no extension.
    if "." in key.rstrip("/").rsplit("/", 1)[-1]:
        continue
    rel = key.rstrip("/") + "/.yard-canary-probe.md"
    try:
        if get_routing_destination(rel) == "engine":
            continue
    except Exception:
        continue
    if subprocess.run(["git", "check-ignore", "-q", rel]).returncode != 0:
        print(rel)
        break
' 2>/dev/null)"

[ -n "$PROBE" ] || fail 10 "no probe path survives the gitignore filter, so the guards cannot be proved here"

mkdir -p "$(dirname "$PROBE")"
printf 'Temporary decoy for the YARD guard probe. Removed automatically.\n' > "$PROBE"
log "canary: $PROBE"

if .venv/bin/python -m pytest tests/test_engine_tree_clean.py -q >/dev/null 2>&1; then
  fail 10 "the tree-clean wall did not see the decoy at $PROBE — it is inspecting a different tree"
fi
cleanup_probe
log "canary passed: the guards fire in this copy"
write_status "in_progress" 10

# ─────────────────────────────────────────────────────────────
# 11. Ready.
# ─────────────────────────────────────────────────────────────
LAST_STEP=11
write_status "ok" 11
badge "ok"
log "contour intact"

if [ "$DOCTOR_ONLY" -eq 0 ]; then
  if [ -z "$BRANCH" ]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  fi
  if [ -n "$WS_ID" ] && [ -n "$BRANCH" ] && [ "$BRANCH" != "HEAD" ]; then
    case "$BRANCH" in
      YARD/*) LABEL="$BRANCH" ;;
      *)      LABEL="YARD/$BRANCH" ;;
    esac
    "$HERDR" workspace rename "$WS_ID" "$LABEL" >/dev/null 2>&1 || true
  fi

  if [ "$AUTOSTART" = "1" ] && [ -n "$PANE_ID" ]; then
    # HEADING_OS_YARD is exported into the AGENT PROCESS, not written to .env.
    # The Bash tool does not load .env, so a marker written there is never set
    # for the commands the agent runs -- a draft called that a mechanical rule
    # and it was never once true. Process inheritance reaches the agent, its
    # shells, their children, and the git hooks those children run.
    "$HERDR" pane run "$PANE_ID" "HEADING_OS_YARD=1 exec $AGENT_CMD" \
      >/dev/null 2>&1 \
      || log "could not start the agent; start it by hand: HEADING_OS_YARD=1 $AGENT_CMD"
  fi
fi

exit 0
