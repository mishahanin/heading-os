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


def _first_segment(path: str) -> str:
    """First path segment of a relative POSIX/Windows path ('' if absolute/empty)."""
    if not path:
        return ""
    norm = path.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        return ""  # absolute (POSIX or Windows drive) -- never rewrite
    return norm.split("/", 1)[0]


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


def _candidate_paths(tool_name: str, tool_input: dict) -> bool:
    """Cheap pre-check: is there any data-relative path worth rewriting?
    Runs before the workspace import so the common case stays import-free."""
    for field in _PATH_FIELDS.get(tool_name, ()):
        if _is_data_rel(tool_input.get(field) or ""):
            return True
    # Glob with no explicit path but a data-prefixed pattern (e.g. "outputs/**").
    if tool_name == "Glob" and not (tool_input.get("path") or ""):
        if _is_data_rel(tool_input.get("pattern") or ""):
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
    if tool_name not in _PATH_FIELDS:
        return 0

    # Cheap path-prefix gate BEFORE importing workspace utils.
    if not _candidate_paths(tool_name, tool_input):
        return 0

    # A data-relative path is present. Resolve the data root; no-op if data is
    # in-tree (data_root == workspace_root), i.e. ceo-main pre-cutover.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
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
        if _is_data_rel(p):
            return str(data_root / p.replace("\\", "/"))
        return None

    updated = dict(tool_input)
    changed = False
    for field in _PATH_FIELDS[tool_name]:
        new = _redirect(updated.get(field) or "")
        if new is not None:
            updated[field] = new
            changed = True
    # Glob with a data-prefixed pattern and no path: anchor the search at the
    # data root so the (still-relative) pattern resolves under it.
    if tool_name == "Glob" and not (tool_input.get("path") or ""):
        if _is_data_rel(tool_input.get("pattern") or ""):
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
