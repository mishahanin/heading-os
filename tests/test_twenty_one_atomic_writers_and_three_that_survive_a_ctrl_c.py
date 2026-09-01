#!/usr/bin/env python3
"""`except Exception` does not catch Ctrl-C, so the scratch file stays behind.

Every atomic writer in this workspace has the same body: `tempfile.mkstemp`
beside the target, write, then `os.replace`. If anything interrupts between the
mkstemp and the replace, the cleanup handler must unlink the scratch file and
re-raise. `KeyboardInterrupt` and `SystemExit` do NOT derive from `Exception`,
so a handler written `except Exception` lets both walk past, and a `tmpXXXXXXXX`
file is left beside the target owned by nothing.

The target file is untouched either way: `os.replace` is what makes the write
visible, so this is about the ORPHAN, never about corruption. That is why it is
litter rather than data loss, and why it went unnoticed for so long.

## The count, which is the point of this file

A shard auditor fixed `scripts/utils/atomic.py` on 2026-09-01 and reported it as
"the last of four sibling copies still narrow". That number was wrong, and a
wrong number about the past is worse than no number: the next audit reads it and
stops looking.

DERIVED by AST on 2026-09-01, over every tracked file under `scripts/` and every
hook, selecting each function that calls both `mkstemp` and `replace`:

    21 atomic-write helpers
     3 catch BaseException
    18 catch only Exception

So the campaign had closed 3 of 21 and believed it had closed 4 of 4.

## What this file binds, and what it does not

It binds the SESSION-CRITICAL writers, listed in `MUST_SURVIVE_INTERRUPT` below.
Those are the ones that write the operator's handoff archive, the checkpoint
state, and the bridge session registry, on a Stop or a compaction, which is
precisely when an interrupt is most likely: the operator pressing Ctrl-C to stop
a long turn runs straight through this code.

It does NOT demand the widening across all 21. Thirteen of the remaining
helpers sit in modules being edited by concurrent audit agents, and a blanket
rule here would turn their runs red for a reason unrelated to their work. The
backlog is recorded at
`.tmp/bind/coordinator-findings/` and swept when the tree is quiet. This file
carries a FLOOR on the total instead, so the population cannot quietly shrink
and make a future sweep look complete.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

# Path -> the reason an interrupt is likely to land inside this particular one.
MUST_SURVIVE_INTERRUPT = {
    ".claude/hooks/checkpoint-save.py":
        "writes the handoff archive on Stop and on compaction",
    "scripts/utils/checkpoint_paths.py":
        "the canonical checkpoint state writers, called under locked_state",
    ".claude/hooks/bridge-hook.py":
        "writes the session registry at session start and session end",
}

# Measured 2026-09-01. A floor, never an equality: peers add helpers.
TOTAL_HELPERS_FLOOR = 20
WIDE_HELPERS_FLOOR = 3


def _helpers():
    """Every function that mkstemps beside a target and then replaces onto it.

    Derived, never listed. A hand-written list of atomic writers is exactly the
    thing that fell behind and produced the wrong count above.
    """
    seen: set[tuple[str, str, int]] = set()
    # BOTH globs through `tracked_paths`, never a bare `Path.glob` over
    # `.claude/`. A hand-written walk also picks up whatever an agent left under
    # `.claude/worktrees/`, which doubles the corpus and makes the floors below
    # meaningless. `tests/test_a_walker_that_never_asked_git.py` enforces this
    # and caught the first draft of this file doing exactly that; it is the
    # second time this author made the same mistake, which is the argument for
    # the rule being mechanical rather than remembered.
    paths = set(tracked_paths(["scripts/**/*.py", ".claude/hooks/*.py"]))

    for path in sorted(paths):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        try:
            rel = str(path.resolve().relative_to(ROOT))
        except ValueError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            called = {getattr(c.func, "attr", None)
                      for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if not {"mkstemp", "replace"} <= called:
                continue
            handlers: set[str] = set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Try):
                    continue
                for h in node.handlers:
                    if h.type is None:
                        handlers.add("<bare>")
                    elif isinstance(h.type, ast.Name):
                        handlers.add(h.type.id)
                    elif isinstance(h.type, ast.Tuple):
                        for e in h.type.elts:
                            handlers.add(getattr(e, "id", getattr(e, "attr", "?")))
                    else:
                        handlers.add(getattr(h.type, "attr", "?"))
            # `key` dedupes the same file reached by two path spellings, which
            # the first draft of this walk did, doubling every hook.
            key = (rel, fn.name, fn.lineno)
            if key in seen:
                continue
            seen.add(key)
            yield rel, fn.name, fn.lineno, handlers


def test_the_population_of_atomic_writers_has_not_shrunk():
    """A floor, so a future sweep cannot look complete over a smaller corpus."""
    found = list(_helpers())

    assert len(found) >= TOTAL_HELPERS_FLOOR, (
        f"only {len(found)} atomic-write helper(s) found, below the {TOTAL_HELPERS_FLOOR} "
        f"measured on 2026-09-01. Either the detector no longer matches the "
        f"shape, or helpers were removed; both make every other assertion in "
        f"this file weaker than it reads.")


def test_the_session_critical_writers_survive_an_interrupt():
    """The headline. These run on Stop, where Ctrl-C actually happens."""
    narrow = []
    covered = set()
    for rel, name, lineno, handlers in _helpers():
        if rel not in MUST_SURVIVE_INTERRUPT:
            continue
        covered.add(rel)
        if not (handlers & {"BaseException", "<bare>"}):
            narrow.append(
                f"{rel}:{lineno} {name} catches {sorted(handlers)} "
                f"({MUST_SURVIVE_INTERRUPT[rel]})")

    missing = set(MUST_SURVIVE_INTERRUPT) - covered
    assert not missing, (
        f"{sorted(missing)} no longer contains an atomic-write helper, so this "
        f"test silently stopped checking it. Remove it from "
        f"MUST_SURVIVE_INTERRUPT deliberately, or find where the writer went.")

    assert not narrow, (
        "a writer that runs on Stop cannot survive a Ctrl-C, and leaves a "
        "tmpXXXXXXXX file beside the operator's handoff archive:\n  "
        + "\n  ".join(narrow)
        + "\nKeyboardInterrupt and SystemExit do not derive from Exception.")


def test_at_least_the_known_wide_writers_stay_wide():
    """A ratchet on the three that were already correct.

    Named individually rather than counted, because a count is satisfied by any
    three and would not notice one of these regressing while an unrelated
    helper was widened.
    """
    wide = {rel for rel, _n, _l, h in _helpers() if h & {"BaseException", "<bare>"}}
    for expected in ("scripts/utils/atomic.py",
                     "scripts/bridge_daemon/_atomic.py",
                     "scripts/utils/crm_autolog.py"):
        assert expected in wide, (
            f"{expected} stopped catching BaseException in its atomic writer; "
            f"it was one of only three that ever did")
    assert len(wide) >= WIDE_HELPERS_FLOOR


def test_the_detector_can_actually_fire(tmp_path):
    """The negative case, against synthetic source.

    Without this, every green result above could mean the AST walk stopped
    matching the shape rather than that the code is correct.
    """
    def verdict(src: str):
        tree = ast.parse(src)
        out = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            called = {getattr(c.func, "attr", None)
                      for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if not {"mkstemp", "replace"} <= called:
                continue
            handlers = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Try):
                    for h in node.handlers:
                        if isinstance(h.type, ast.Name):
                            handlers.add(h.type.id)
            out.append((fn.name, bool(handlers & {"BaseException"})))
        return out

    narrow = ("import os, tempfile\n"
              "def w(p, c):\n"
              "    fd, t = tempfile.mkstemp()\n"
              "    try:\n"
              "        os.replace(t, p)\n"
              "    except Exception:\n"
              "        raise\n")
    wide = narrow.replace("except Exception:", "except BaseException:")
    unrelated = ("import os\n"
                 "def w(p, c):\n"
                 "    os.replace('a', p)\n")

    assert verdict(narrow) == [("w", False)], "a narrow writer was not flagged"
    assert verdict(wide) == [("w", True)], "a wide writer was flagged anyway"
    assert verdict(unrelated) == [], (
        "a function that replaces without mkstemping was matched; it has no "
        "scratch file to orphan and flagging it would be a false finding")


def test_the_exception_hierarchy_this_file_rests_on_is_real():
    """Bind the reasoning to the language rather than to a docstring."""
    assert not issubclass(KeyboardInterrupt, Exception)
    assert not issubclass(SystemExit, Exception)
    assert issubclass(KeyboardInterrupt, BaseException)
    assert issubclass(SystemExit, BaseException)


@pytest.mark.slow
def test_an_interrupted_write_leaves_no_scratch_file_behind(tmp_path):
    """The behavioural jaw, on the real canonical writer.

    The interrupt is delivered at `os.replace`, which is INSIDE the try block.
    The first draft raised it from inside `mkstemp`, which is one line ABOVE
    the try: the handler could never run, `tmp_name` was never even bound, and
    the orphan the test then found had been created by its own stub rather than
    by the code under test. It failed for a reason unrelated to the fix and
    would have kept failing after it. Same shape as the campaign's rule that a
    probe which does not reach the code measures nothing.

    Asked of a child process because a `KeyboardInterrupt` raised in-process
    would land in pytest's own machinery.
    """
    target = tmp_path / "state.json"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, sys, pathlib\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts.utils import checkpoint_paths as CP\n"
        "def boom(*a, **k):\n"
        "    raise KeyboardInterrupt('operator pressed Ctrl-C mid-write')\n"
        "# Patched on the MODULE the writer resolves, so the scratch file is\n"
        "# real and already written when the interrupt arrives.\n"
        "CP.os.replace = boom\n"
        "try:\n"
        f"    CP.write_json_atomic(pathlib.Path({str(target)!r}), {{'a': 1}})\n"
        "except BaseException:\n"
        "    pass\n", encoding="utf-8")

    subprocess.run([sys.executable, str(probe)], capture_output=True,
                   errors="replace", timeout=120,
                   env=dict(os.environ, HEADING_OS_DATA=str(tmp_path / "data")))

    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.startswith("state.json.") and p.name.endswith(".tmp")]
    assert not leftovers, (
        f"an interrupt during the write left {leftovers} beside the target. "
        f"The cleanup handler did not run, which is what `except Exception` "
        f"does when the interruption is a KeyboardInterrupt.")
