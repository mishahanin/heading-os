#!/usr/bin/env bash
#
# require-main-clone.sh — the shell half of the HELM/YARD entry guard.
#
# Usage, in the guarded script, above anything that touches the machine:
#
#     source "$(dirname "$0")/lib/require-main-clone.sh"
#     require_main_clone
#
# Refuses with exit status 2 when the calling script is a copy inside a git
# worktree (a "YARD") rather than the main clone ("HELM"). Every script that
# installs, restarts or removes a daemon belongs here: the systemd unit
# templates substitute the workspace path into `WorkingDirectory=` and
# `ExecStart=`, so an installer run from a worktree points a LIVE daemon at a
# checkout that is deleted two days later, and nothing says so until it stops.
#
# NO EXTERNAL COMMANDS ON THE COMMON PATH
#
# The verdict is reached with bash builtins alone: no `git`, no `python`, no
# `basename`, no `dirname`. That is a requirement rather than an optimisation.
# `tests/test_memory_expiry.py` runs an installer with PATH pinned to hold
# `dirname` and nothing else, precisely to prove nothing runs before that
# script's own refusal gate, and the first version of this helper died there
# with `basename: command not found` and status 127 instead of the status the
# caller was asserting. A guard that only works on a full PATH is a guard that
# turns someone else's careful test red for a reason unrelated to what it tests.
#
# The predicate is the same one `scripts/utils/clone_guard.py` uses, and it is
# the SHAPE OF `.git`. MEASURED 2026-09-03 on this repository: a directory in
# the main clone, a plain file (holding `gitdir: ...`) in a worktree. That is a
# property of git, not a convention of ours; it cannot be faked, forgotten, or
# lost by a new shell.
#
# Two copies of one rule, and that is deliberate. Python callers go through
# `clone_guard.py`; this file cannot, for the reason above. The duplication is
# held by `tests/test_guarded_shell_installers_refuse_from_a_worktree.py`, which
# asserts the two answer identically in HELM and in a real worktree, so a change
# to one that is not made to the other goes red.
#
# `git` is used for ONE thing only: the case where `.git` is neither a file nor
# a directory, which means the script is not at a checkout root and the walk
# above found nothing. There the answer is unknown, and unknown refuses.
#
# Tests: tests/test_guarded_shell_installers_refuse_from_a_worktree.py

require_main_clone() {
  local invoked dir root helm

  # The OUTERMOST script in the source chain: the program the operator ran.
  # `$0` is "bash" for a sourced script, and a guard that reads "bash" as a path
  # answers about the wrong thing.
  invoked="${BASH_SOURCE[-1]:-$0}"
  case "$invoked" in
    bash|-bash|sh|-sh|"")
      echo "require_main_clone: cannot determine which script is running." >&2
      echo "  Run the script as a program, not sourced into a shell." >&2
      exit 2
      ;;
  esac

  # THIS file is never the answer. Sourced on its own -- `bash -c 'source
  # .../require-main-clone.sh; require_main_clone'` -- the chain holds nothing
  # else, and without this check the guard would resolve scripts/lib, find
  # HELM's root, and report "main clone" while saying nothing about any caller.
  # That is the shell spelling of the `__file__`-instead-of-`$0` hole this
  # helper exists to avoid. Found by the test, not by reading.
  if [ "$invoked" -ef "${BASH_SOURCE[0]}" ]; then
    echo "require_main_clone: cannot determine which script is running." >&2
    echo "  This helper is sourced BY a script and called from it. On its own" >&2
    echo "  there is nothing for it to report on." >&2
    exit 2
  fi

  dir="${invoked%/*}"
  [ "$dir" = "$invoked" ] && dir="."
  dir="$(cd "$dir" 2>/dev/null && pwd)" || {
    echo "${invoked##*/}: cannot resolve its own directory." >&2
    exit 2
  }

  # Walk up to the checkout root: the nearest ancestor carrying a `.git`.
  root="$dir"
  while [ ! -e "$root/.git" ]; do
    [ "$root" = "/" ] && break
    root="${root%/*}"
    [ -z "$root" ] && root="/"
  done

  if [ -d "$root/.git" ]; then
    return 0                      # main clone. HELM. Carry on.
  fi

  if [ -f "$root/.git" ]; then
    # A worktree. Its `.git` file holds `gitdir: <HELM>/.git/worktrees/<name>`,
    # so HELM can be named in the refusal without running git.
    helm="$(<"$root/.git")"
    helm="${helm#gitdir: }"
    helm="${helm%%/.git/worktrees/*}"
    echo "${invoked##*/}: this script runs from HELM (the main clone) only," >&2
    echo "  not from a YARD worktree." >&2
    echo "  Detected checkout: $root" >&2
    echo "  HELM: ${helm:-<could not resolve>}" >&2
    echo "  Change to HELM and run it there." >&2
    exit 2
  fi

  # `.git` is neither, so the walk found nothing: not inside a repository, or a
  # layout this predicate does not understand. Ask git if it is available, and
  # refuse if it is not. Unknown never passes.
  if command -v git >/dev/null 2>&1; then
    if [ "$(git -C "$dir" rev-parse --git-dir 2>/dev/null)" = \
         "$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null)" ] \
       && git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
      return 0
    fi
  fi
  echo "${invoked##*/}: not inside a git repository ($dir)." >&2
  echo "  Refusing rather than guessing which clone this is." >&2
  exit 2
}
