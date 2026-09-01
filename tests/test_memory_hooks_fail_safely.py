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

import ast
import importlib.util
import json
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

def _fallback_is_denied():
    """The `def is_denied` defined inside an ImportError handler, as an AST node.

    Located structurally rather than by slicing the source between a literal
    import line and the next `\\n    try:`, which is how this was done until
    2026-09-01. Two things were wrong with the slice. Its start and end are
    prose, so a re-indent or a moved `try:` silently changes which region is
    read (cross-shard finding 22), and its `"return True" in block` question was
    asked of a text window that also contains the comments explaining the fix
    (finding 19) - a comment mentioning the words would answer it.

    Structure instead: the handler that catches the failed import, the function
    it defines, and what that function's `return` statements are. A behavioural
    probe is not available here, because reaching the fallback means breaking
    the import for the whole interpreter, and the enclosing hook is registered
    in no settings file so there is no end-to-end run to drive it from either.
    That limit is why the assertion is on the AST rather than on an outcome.
    """
    tree = ast.parse((HOOKS / "memory-inject.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        imports_air_gap = any(
            isinstance(stmt, ast.ImportFrom) and stmt.module == "scripts.utils.air_gap"
            for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])))
        if not imports_air_gap:
            continue
        for handler in node.handlers:
            for stmt in handler.body:
                if isinstance(stmt, ast.FunctionDef) and stmt.name == "is_denied":
                    return stmt
    return None


def test_the_air_gap_fallback_denies_rather_than_allows():
    fallback = _fallback_is_denied()
    assert fallback is not None, (
        "no `def is_denied` inside the handler for a failed "
        "`from scripts.utils.air_gap import ...`; the fallback moved or is gone")
    returns = [n for n in ast.walk(fallback) if isinstance(n, ast.Return)]
    assert returns, "the fallback returns nothing, which is None, which is falsy"
    for ret in returns:
        assert isinstance(ret.value, ast.Constant) and ret.value.value is True, (
            "the air-gap fallback returns something other than True, meaning "
            "not everything is denied, while the module docstring promises "
            f"air-gapped paths are skipped (line {ret.lineno})")


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


def test_the_caller_treats_none_as_nothing_to_do(reconcile, tmp_path):
    """The refusal above is only safe because main() treats None as "nothing".

    Asserted by RUNNING the hook, not by matching
    `"if native is None:\\n            return 0"` against the source, which is
    what this did until 2026-09-01. That literal carries an exact indent and an
    exact line break: re-wrapping the branch, or moving it inside a helper,
    breaks the assertion without changing the behaviour, and wrapping the return
    in something that swallows it changes the behaviour without breaking the
    assertion. Neither direction is what the test is for.

    `_CP` is forced to None in the child so `_native_from_hook` refuses on an
    empty payload rather than deriving a slug, which is the state this branch
    exists to absorb.
    """
    probe = (
        "import sys, importlib.util, io;"
        f"spec = importlib.util.spec_from_file_location('h', {str(HOOKS / 'memory-reconcile.py')!r});"
        "m = importlib.util.module_from_spec(spec);"
        "sys.modules['h'] = m;"
        "spec.loader.exec_module(m);"
        "m._CP = None;"
        "sys.argv = ['memory-reconcile.py'];"
        "sys.stdin = io.StringIO('{}');"
        "print('RC=' + repr(m.main()))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, timeout=60, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr[-800:]
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    assert "Traceback" not in out.stderr


# --- 4. the hook runs as a hook: the session still starts ---------------------
#
# Everything above calls `reconcile()` and `_native_from_hook()` in process. A
# SessionStart hook's actual promise is narrower and is not any of those: on
# every input it can be handed, it EXITS 0 and the session starts. Asserting
# that a warning was printed does not establish it, and a hook of this tree has
# already shipped a refusal that printed and exited 0 in the other direction
# (`.githooks/pre-push-data`). So these run the file as the harness runs it and
# assert the exit status plus the side effect.
#
# Isolation, load-bearing. Unfaked, this hook does a bidirectional newest-wins
# sync that writes into `<data-root>/auto-memory/` and into the harness's native
# store, and the operator's standing rule is that memory is never pruned or
# rewritten without him saying so. Every child below gets HEADING_OS_DATA and
# HOME pinned inside `tmp_path`: the first bounds the canonical side, the second
# bounds both the slug-derived native store (`Path.home()`) and the lock sidecar
# under `~/.claude/state/`.


def _run_hook(tmp_path, payload: str, *, data_root: Path | None = None):
    """Run `.claude/hooks/memory-reconcile.py` in hook mode, fully sandboxed."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("USERPROFILE", None)          # `_lock_path` prefers it over HOME
    env["HEADING_OS_DATA"] = str(
        (tmp_path / "data") if data_root is None else data_root)
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "memory-reconcile.py")],
        input=payload, capture_output=True, text=True, timeout=120,
        cwd=str(tmp_path), env=env,
    )
    return proc, home


def _seed(tmp_path):
    """A canonical store and a native store, each holding a file the other lacks."""
    canonical = tmp_path / "data" / "auto-memory"
    native = tmp_path / "native" / "proj" / "memory"
    canonical.mkdir(parents=True)
    native.mkdir(parents=True)
    (canonical / "from-canonical.md").write_text("c\n", encoding="utf-8")
    (native / "from-native.md").write_text("n\n", encoding="utf-8")
    payload = json.dumps({
        "hook_event_name": "SessionStart",
        "transcript_path": str(tmp_path / "native" / "proj" / "session.jsonl"),
    })
    return canonical, native, payload


def test_the_hook_exits_zero_and_actually_syncs_both_ways(tmp_path):
    """The positive control. A guard that only ever no-ops measures nothing."""
    canonical, native, payload = _seed(tmp_path)
    proc, home = _run_hook(tmp_path, payload)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert (native / "from-canonical.md").is_file(), proc.stderr
    assert (canonical / "from-native.md").is_file(), proc.stderr
    # The pin held: the lock sidecar landed under the sandboxed HOME, so no
    # child of this test reached `~/.claude/state/` on the real machine.
    assert list((home / ".claude" / "state").glob("memory-reconcile-*.lock"))


@pytest.mark.parametrize("payload, why", [
    ("", "empty stdin"),
    ("not json at all", "unparseable"),
    ("[]", "valid JSON, not an object"),
    ("3", "valid JSON scalar"),
    ("null", "JSON null"),
    ('{"transcript_path": 3}', "right key, wrong type"),
    ('{"transcript_path": []}', "right key, list"),
    ('{"transcript_path": ""}', "right key, empty string"),
])
def test_a_malformed_payload_still_lets_the_session_start(tmp_path, payload, why):
    """Exit status, and no traceback. Not the text of a warning.

    `""` on `transcript_path` falls through to the slug resolver, which under
    the pinned HOME derives a store inside `tmp_path`; the run is still bounded.
    """
    (tmp_path / "data" / "auto-memory").mkdir(parents=True)
    proc, _home = _run_hook(tmp_path, payload)
    assert proc.returncode == 0, f"{why}: rc={proc.returncode}\n{proc.stderr[-800:]}"
    assert "Traceback" not in proc.stderr, f"{why}: {proc.stderr[-800:]}"


def test_an_unresolvable_data_root_still_lets_the_session_start(tmp_path):
    """`env_data_root()` RAISES on a HEADING_OS_DATA naming nothing.

    The resolve sits inside main()'s try for exactly this reason, and the branch
    is measured on the exit status rather than on the "store resolve failed"
    line, which a future reword would silently retire.
    """
    _canonical, _native, payload = _seed(tmp_path)
    proc, _home = _run_hook(tmp_path, payload,
                            data_root=tmp_path / "no-such-overlay")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "no-such-overlay").exists(), (
        "the hook created the overlay it could not resolve")


def test_half_a_cli_invocation_refuses_instead_of_touching_the_live_stores(tmp_path):
    """`--canonical` without `--native` used to fall through to HOOK mode.

    A refusal here has to be a STOP, not a message: the fall-through reconciled
    the two LIVE stores while discarding the directory the operator named. So
    the exit status is asserted non-zero AND the named directory is asserted
    untouched.
    """
    canonical = tmp_path / "named-by-the-operator"
    canonical.mkdir()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("USERPROFILE", None)
    env["HEADING_OS_DATA"] = str(tmp_path / "data")
    (tmp_path / "data" / "auto-memory").mkdir(parents=True)
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "memory-reconcile.py"),
         "--canonical", str(canonical)],
        input="{}", capture_output=True, text=True, timeout=120,
        cwd=str(tmp_path), env=env,
    )
    assert proc.returncode != 0, (proc.stdout, proc.stderr)
    assert list(canonical.iterdir()) == []
    assert not (tmp_path / "data" / "auto-memory" / "MEMORY.md").exists()
