"""Three fail-toward-the-wrong-side defects in the two memory hooks.

All three found by the 2026-08-23 engine audit, all three cases where the code
did the opposite of what its own comment or docstring said.

1. `memory-inject.py` — the air-gap fallback was fail-OPEN.

   The module docstring promises the hook "defensively skips any air-gapped
   path". When `scripts.utils.air_gap` failed to import, the fallback returned
   `False` for everything, meaning nothing is denied, under an inline comment
   calling itself "fail-closed-ish". Closed is the cheap direction: denying
   every path means the hook injects nothing, which costs one turn of context
   and breaks no workflow, while injecting an air-gapped path costs the air gap.

2. `memory-reconcile.py` — one bad entry aborted the whole reconcile.

   The sync loop had no per-entry guard. A directory named `*.md`, an unreadable
   file, or a file that vanished between `exists()` and `read_bytes()` raised
   out of the loop; `main()` caught it once and returned, so every REMAINING
   memory went unsynced because of one entry. Skipping the entry syncs the other
   N-1.

3. `memory-reconcile.py` — the cwd-slug fallback was wrong on Windows.

   The docstring says the slug is derived "the way Claude Code does (each '/'
   and '.' becomes '-')". On Windows `Path(cwd).resolve()` gives `C:\\Users\\...`,
   whose backslashes and drive colon neither replacement touches, so the hook
   reconciled against an invented directory and created it. It now returns None
   there, which the caller already handles, rather than guessing a store format
   this file cannot verify.
"""
from __future__ import annotations

import importlib.util
import os  # noqa: F401  # kept: used by the POSIX skipif below
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"


def _load(name: str):
    path = HOOKS / name
    spec = importlib.util.spec_from_file_location(f"hook_{name.replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- 1. the air-gap fallback --------------------------------------------------

def test_the_air_gap_fallback_denies_rather_than_allows():
    """Read the fallback out of the source: it is only reachable when an import
    fails, which cannot be provoked from inside a test without breaking the
    import for everything else."""
    src = (HOOKS / "memory-inject.py").read_text(encoding="utf-8")
    block = src[src.index("from scripts.utils.air_gap import is_denied"):]
    block = block[:block.index("\n    try:", 1)]
    assert "def is_denied" in block, "the fallback definition moved"
    assert "return True" in block, (
        "the air-gap fallback returns False again, meaning nothing is denied, "
        "while the module docstring promises air-gapped paths are skipped"
    )
    assert "return False" not in block


def test_the_docstring_promise_is_still_made():
    """If the promise is deleted, the fallback direction stops being anchored to
    anything and this test guards a preference rather than a contract."""
    src = (HOOKS / "memory-inject.py").read_text(encoding="utf-8")
    assert "skips any air-gapped path" in src


# --- 2. one bad entry must not abort the reconcile ----------------------------

@pytest.fixture
def reconcile():
    return _load("memory-reconcile.py")


def test_an_unreadable_entry_does_not_stop_the_other_files(reconcile, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    # A DIRECTORY named *.md: exists() is true, read_bytes() raises IsADirectory.
    (a / "bad.md").mkdir()
    (b / "bad.md").mkdir()
    (a / "good.md").write_text("fresh\n", encoding="utf-8")
    (a / "second.md").write_text("also fresh\n", encoding="utf-8")

    a_upd, b_upd = reconcile.reconcile(a, b)

    assert (b / "good.md").is_file(), (
        "the sync stopped at the bad entry; good.md never reached the other side"
    )
    assert (b / "second.md").is_file(), (
        "only the entry before the bad one was synced"
    )
    assert b_upd == 2, f"reported {b_upd} updates, expected 2"


def test_a_clean_pair_still_syncs_both_ways(reconcile, tmp_path):
    """The guard must not swallow the normal path."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "from-a.md").write_text("x\n", encoding="utf-8")
    (b / "from-b.md").write_text("y\n", encoding="utf-8")
    a_upd, b_upd = reconcile.reconcile(a, b)
    assert (b / "from-a.md").is_file() and (a / "from-b.md").is_file()
    assert (a_upd, b_upd) == (1, 1)


# --- 3. the POSIX-only slug fallback ------------------------------------------

def test_transcript_path_wins_and_is_platform_independent(reconcile):
    got = reconcile._native_from_hook(
        {"transcript_path": "/home/x/.claude/projects/p/s.jsonl"})
    assert got == Path("/home/x/.claude/projects/p/memory")


@pytest.mark.skipif(os.name != "posix", reason="the fallback is POSIX-only by design")
def test_the_cwd_fallback_still_works_on_posix(reconcile):
    got = reconcile._native_from_hook({"cwd": "/home/x/work.dir"})
    assert got is not None
    assert got.name == "memory"
    assert "-home-x-work-dir" in str(got)


def test_the_fallback_refuses_rather_than_guessing_off_posix():
    """Run in a SUBPROCESS, because `os.name` cannot be monkeypatched in place.

    The first version did `monkeypatch.setattr(os, "name", "nt")`. `pathlib`
    picks WindowsPath vs PosixPath off that same attribute, so the patch made
    every later `Path()` in the worker raise
    `NotImplementedError: cannot instantiate 'WindowsPath' on your system`, and
    pytest aborted the whole run with an INTERNALERROR rather than a failure.
    Caught 2026-08-23 by running this file alongside twelve others.

    A child process carries the pollution and dies with it. The patch lands
    AFTER the import, not before: `shutil` does `if os.name == 'nt': import nt`
    at ITS import time, so patching first makes the hook's own
    `import shutil` raise `ModuleNotFoundError: No module named 'nt'`. Caught
    2026-08-23, the second time this probe was defeated by patching a global
    the stdlib reads. Patching after import is safe because the guard under
    test returns before any `Path()` is constructed on that branch, so
    `pathlib` never has to pick WindowsPath.
    """
    probe = (
        "import os, sys, importlib.util, shutil, pathlib;"
        f"spec = importlib.util.spec_from_file_location('h', {str(HOOKS / 'memory-reconcile.py')!r});"
        "m = importlib.util.module_from_spec(spec);"
        "sys.modules['h'] = m;"
        "spec.loader.exec_module(m);"
        "os.name = 'nt';"
        "print('RESULT=' + repr(m._native_from_hook({'cwd': 'C:/Users/x/work'})))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, f"probe failed: {out.stderr[-800:]}"
    assert "RESULT=None" in out.stdout, (
        "the cwd-slug fallback produced a path on a non-POSIX platform; the "
        "backslashes and drive colon are not handled and the result names no "
        f"real store. Got: {out.stdout.strip()!r}"
    )


def test_the_caller_treats_none_as_nothing_to_do(reconcile):
    """The refusal above is only safe because main() already handles None."""
    src = (HOOKS / "memory-reconcile.py").read_text(encoding="utf-8")
    assert "if native is None:\n            return 0" in src
