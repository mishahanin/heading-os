#!/usr/bin/env python3
"""PreToolUse path-redirect hook: data-relative tool paths -> the data root.

HEADING OS engine/data separation. When the session runs from the engine clone
(`.heading-os`), the DATA directories (`context/`, `crm/`, `outputs/`,
`knowledge/`, `threads/`, `plans/`, `datastore/`, `_archive/`, `corporate/`) are
NOT physically present at cwd -- they live in the `.heading-os-data` sibling,
resolved by `get_data_root()`. Python code reaches them via the `get_*_dir()`
helpers; this hook is the *tool-layer twin*: it rewrites Claude's own
Read/Write/Edit/Grep/Glob paths that target a data dir so the tool operates on
the real file in the data root. The engine working tree therefore stays
byte-clean -- zero data dirs, zero symlinks, nothing to leak -- while the agent
still reads and writes data transparently using ordinary cwd-relative paths
(exactly what every SKILL.md already does).

That "zero data dirs" premise is load-bearing and was FALSE on the operator's
clone until 2026-08-25: `outputs/` and `plans/` survived the cutover with 27
files (2 outputs, 25 archived plans), gitignored but populated. Classification
runs on the first path segment alone and never asks whether the file exists at
cwd, so every relative reference to one of those engine-local files was rewritten
to a data-root path where it was not -- a spurious "file does not exist" on a
Read, and on a Write a second copy created in the overlay while the engine file
sat untouched and unsaid. The 27 files were moved into the data overlay rather
than the redirect being softened, because the redirect IS the seam;
`tests/test_data_path_redirect.py` pins `outputs` and `plans` as always
redirected, and a test now fails if any DATA_DIRS name reappears here.

No-op when `get_data_root() == get_workspace_root()` (ceo-main pre-cutover, data
still in-tree): the relative path already resolves correctly, so nothing is
rewritten and the hook exits silently.

Only RELATIVE paths whose first path segment is a data dir are rewritten;
absolute paths and engine paths (`scripts/`, `.claude/`, `reference/`,
`config/`, `docs/`, `tests/`, ...) are left untouched. This is what makes the
`/prime` script-path slip impossible: `scripts/prime-health-parallel.py` is not
a data path, so it is never redirected and resolves to the engine copy.

Mechanism: PreToolUse `hookSpecificOutput.updatedInput` (Claude Code hooks spec,
code.claude.com/docs/en/hooks.md). The emitted `updatedInput` carries the FULL
original tool input with only the path field(s) replaced, so it is correct under
both merge and full-replace harness semantics.

Performance: the cheap data-prefix test runs BEFORE importing workspace utils, so
the common case (engine paths, absolute paths, non-path tools) exits without the
import cost.

Classification: engine (workspace logic, shipped in the engine clone).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Data dirs that live under the DATA root (mirror of the engine .gitignore data
# block + the get_*_dir() seam). A relative path whose FIRST segment is one of
# these is redirected; everything else (engine code, absolute paths) is not.
DATA_DIRS = frozenset(
    {"context", "crm", "outputs", "knowledge", "threads", "plans", "datastore",
     "_archive", "corporate"}
)


# The lexical collapse lives in `scripts/utils/pathnorm.py`, imported here.
#
# It was written in this file on 2026-08-23, after `..` segments were found
# reaching the rewrite unnormalized: classification ran on the raw first segment
# and the rewrite concatenated the raw path onto the data root, so
# `outputs/../scripts/foo.py` became `<data-root>/scripts/foo.py` and
# `outputs/../../../etc/passwd` left the data root altogether.
#
# The personal-threads wall in `_dispatch.py` needed the same collapse and never
# got it, so for six days it refused one spelling of a CEO-only path and allowed
# three others that open the same file. Measured 2026-08-29: 4 of 9. A private
# copy in one hook is what let the second hook stay broken, so there is now one
# copy and both import it.
#
# The import costs ~0.9 ms of a ~55 ms hook, which is inside the run-to-run
# noise; measured 12 runs each way before moving it out of the lazy section.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.pathnorm import normalize_rel as _normalize_rel  # noqa: E402


def _first_segment(path: str) -> str:
    """First segment of a NORMALIZED relative path ('' if absolute/empty/escaping)."""
    norm = _normalize_rel(path)
    return norm.split("/", 1)[0] if norm else ""


def _is_data_rel(path: str) -> bool:
    return _first_segment(path) in DATA_DIRS


# Per-tool: which input fields can carry a data-relative path we should rewrite.
# Grep's `pattern` is a regex (never a path) so only its `path` is considered.
# Glob's `pattern` IS a path-glob, handled specially (see _candidate_paths).
_PATH_FIELDS = {
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Grep": ("path",),
    "Glob": ("path",),
}


def _path_value(tool_input: dict, field: str) -> str:
    """The string value of a path field, or '' for anything that is not a string.

    The TYPE of the path INSIDE tool_input. `main` guards the container and
    nothing guarded the field, so `{"file_path": 3}` reached `_is_data_rel` and
    raised an uncaught AttributeError on `.replace`. Measured 2026-08-31 driving
    the real hook: an int, a bool, a list, a dict and a float each exited 1 with
    a traceback.

    This hook is PreToolUse, which is why the field matters more here than in
    its PostToolUse neighbours. A traceback exit means the redirect does not
    happen and the tool then proceeds against the ENGINE path, so a write lands
    in the wrong repository. A field that is not a string names no path, so it
    is treated as no path and announced, never silently swallowed.
    """
    value = tool_input.get(field) or ""
    if not isinstance(value, str):
        print(f"[data-path-redirect] {field} was {type(value).__name__}, "
              "not a string; treated as no path", file=sys.stderr)
        return ""
    return value


def _candidate_paths(tool_name: str, tool_input: dict) -> bool:
    """Cheap pre-check: is there any data-relative path worth rewriting?
    Runs before the workspace import so the common case stays import-free."""
    for field in _PATH_FIELDS.get(tool_name, ()):
        if _is_data_rel(_path_value(tool_input, field)):
            return True
    # Glob with no explicit path but a data-prefixed pattern (e.g. "outputs/**").
    if tool_name == "Glob" and not _path_value(tool_input, "path"):
        if _is_data_rel(_path_value(tool_input, "pattern")):
            return True
    return False


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as e:
        # Fail-open on unparseable stdin only: the harness contract is broken,
        # not an exfil attempt; blocking would wedge every tool call.
        print(f"[data-path-redirect] failed to parse input: {e}", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    # The same guard as the line above, for the field beside it. `tool_name` is
    # used as a DICT KEY (`not in _PATH_FIELDS`, `_PATH_FIELDS[tool_name]`), so
    # an unhashable value does not merely mismatch, it raises. Measured
    # 2026-09-01 driving the real hook: `[]` and `{}` each exited 1 with
    # `TypeError: unhashable type`, while `3`, `True`, `null` and `""` matched
    # nothing and returned 0 correctly.
    #
    # This is the defect `_path_value` documents one layer down, in the file
    # that had just fixed it there: the container was type-checked, the field
    # next to it was not. The consequence is the same one that docstring names.
    # PreToolUse dying means the redirect never runs and the tool proceeds
    # against the ENGINE path, so a write lands in the wrong repository.
    if not isinstance(tool_name, str):
        print(f"[data-path-redirect] tool_name was {type(tool_name).__name__}, "
              "not a string; no tool matched, nothing redirected", file=sys.stderr)
        return 0
    if tool_name not in _PATH_FIELDS:
        return 0

    # Cheap path-prefix gate BEFORE importing workspace utils.
    if not _candidate_paths(tool_name, tool_input):
        return 0

    # A data-relative path is present. Resolve the data root; no-op if data is
    # in-tree (data_root == workspace_root), i.e. ceo-main pre-cutover. The
    # workspace root reached sys.path at import time, for pathnorm.
    try:
        from scripts.utils.workspace import get_data_root, get_workspace_root
        data_root = get_data_root().resolve()
        ws_root = get_workspace_root().resolve()
    except Exception as e:
        # Cannot resolve roots -> do not rewrite (leave the call as-is). Logged.
        print(f"[data-path-redirect] root resolution failed: {e}", file=sys.stderr)
        return 0
    if data_root == ws_root:
        return 0  # data in-tree; relative path already correct

    def _redirect(p: str) -> str | None:
        # Join the NORMALIZED path, never the raw one: `outputs/../../x` used to
        # be classified data-relative on its raw first segment and then
        # concatenated verbatim, landing outside the data root.
        norm = _normalize_rel(p)
        if norm and _first_segment(p) in DATA_DIRS:
            return str(data_root / norm)
        return None

    updated = dict(tool_input)
    changed = False
    for field in _PATH_FIELDS[tool_name]:
        new = _redirect(_path_value(updated, field))
        if new is not None:
            updated[field] = new
            changed = True
    # Glob with a data-prefixed pattern and no path: anchor the search at the
    # data root so the (still-relative) pattern resolves under it.
    if tool_name == "Glob" and not _path_value(tool_input, "path"):
        if _is_data_rel(_path_value(tool_input, "pattern")):
            updated["path"] = str(data_root)
            changed = True

    if not changed:
        return 0

    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated,
        }},
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
