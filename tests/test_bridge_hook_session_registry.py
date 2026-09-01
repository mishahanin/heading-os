"""Two sessions in one directory must not deregister each other.

`.claude/hooks/bridge-hook.py` kept its session registry keyed by cwd:

    reg[cwd] = {...}      # session-start
    del reg[cwd]          # session-end

This workspace runs several sessions on one tree by design, which
`.claude/hooks/checkpoint-statusline.py` states in its own docstring. Two
sessions launched from the same directory therefore produced ONE entry: the
second overwrote the first, and whichever ended first deleted it, deregistering
a session that was still running. Found by the 2026-08-23 engine audit and
reproduced end to end.

`session_id` is unique per session and was already required by the same
function, so it is the key. `cwd` stays as a field.

The second defect here is in `_read_user_choice`. After `select.select` reported
the tty readable, the code called `tty.readline()`, which blocks until a
newline. Claude Code leaves the terminal in raw/cbreak mode, so `select` fires
on a single keypress — a stray arrow key is enough — and `readline()` then waits
for a `\\n` that may never come. That hangs the Stop hook, and with it the
session exit, under a docstring promising a 5 s timeout. It now reads byte by
byte against a deadline, so every wait is bounded.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "bridge-hook.py"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME, so the test never touches the operator's registry."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    return tmp_path


def _run(sub: str, payload: dict, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    env.pop("USERPROFILE", None)
    return subprocess.run([sys.executable, str(HOOK), sub],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=60)


def _registry(home: Path) -> dict:
    path = home / ".claude" / "state" / "active-sessions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def test_two_sessions_in_one_directory_both_register(home):
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    _run("session-start", {"session_id": "sess-B", "cwd": "/work/tree"}, home)
    reg = _registry(home)
    assert sorted(reg) == ["sess-A", "sess-B"], (
        f"the second session overwrote the first: {reg}"
    )


def test_one_session_ending_leaves_the_other_registered(home):
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    _run("session-start", {"session_id": "sess-B", "cwd": "/work/tree"}, home)
    _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    reg = _registry(home)
    assert sorted(reg) == ["sess-B"], (
        f"ending A deregistered a session that is still running: {reg}"
    )


def test_the_entry_still_records_the_directory(home):
    """Rekeying must not lose the cwd; anything grouping by directory needs it."""
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert _registry(home)["sess-A"]["cwd"] == "/work/tree"


def test_session_end_is_idempotent(home):
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    proc = _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert proc.returncode == 0
    assert _registry(home) == {}


def test_a_legacy_cwd_keyed_entry_of_ours_is_swept(home):
    """A registry written before the rekey would otherwise never drain."""
    path = home / ".claude" / "state" / "active-sessions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"/work/tree": {"session_id": "sess-A"}}),
                    encoding="utf-8")
    _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert _registry(home) == {}


def test_a_legacy_entry_belonging_to_another_session_is_left_alone(home):
    """The sweep must not delete a live session's row just because it shares a
    directory. That is the original bug wearing the compatibility hat."""
    path = home / ".claude" / "state" / "active-sessions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"/work/tree": {"session_id": "sess-OTHER"}}),
                    encoding="utf-8")
    _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert "/work/tree" in _registry(home)


# --- an undecodable registry must recover, not crash --------------------------
#
# `_load_registry` promises "Returns empty dict on missing file or corrupt JSON
# (auto-recover - the next session-start will rewrite a clean file)" and caught
# `(json.JSONDecodeError, OSError)`. Neither reaches a byte that is not UTF-8:
# `Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError` INSIDE the read,
# before `json.loads` is called at all, and `UnicodeDecodeError` is a sibling of
# `JSONDecodeError` under `ValueError`, not a subclass of it.
#
# MEASURED 2026-09-01 against the unfixed hook: a registry holding one 0xff byte
# made `session-start` exit 1 with a `UnicodeDecodeError` traceback. The promise
# in the docstring is the part that matters - the file is never rewritten, so it
# never heals, and every session on this machine stays unregistered until a human
# deletes it by hand. The daemon-side reader of the same file
# (`scripts/bridge_daemon/sessions.read_registry`) was fixed for exactly this on
# an earlier pass; the hook that WRITES the file was not.

BAD_BYTE = b"\xff"


def _corrupt_registry(home: Path, payload: bytes) -> Path:
    path = home / ".claude" / "state" / "active-sessions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_an_undecodable_registry_does_not_crash_session_start(home):
    _corrupt_registry(home, b'{"s1": {"cwd": "' + BAD_BYTE + b'"}}')
    proc = _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert "UnicodeDecodeError" not in proc.stderr, (
        "a byte that is not UTF-8 walked past `except (JSONDecodeError, OSError)` "
        f"and crashed the hook: {proc.stderr}"
    )
    assert proc.returncode == 0, proc.stderr


def test_an_undecodable_registry_is_rewritten_clean(home):
    """The docstring's actual promise: the next session-start heals the file."""
    _corrupt_registry(home, b'{"s1": {"cwd": "' + BAD_BYTE + b'"}}')
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert sorted(_registry(home)) == ["sess-A"], (
        "the corrupt registry was not replaced, so it never heals and every "
        "later session stays unregistered"
    )


def test_an_undecodable_registry_does_not_crash_session_end(home):
    _corrupt_registry(home, b'{"s1": {"cwd": "' + BAD_BYTE + b'"}}')
    proc = _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert "UnicodeDecodeError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_a_decodable_but_corrupt_registry_still_recovers(home):
    """The neighbouring branch, so a fix cannot trade one failure for the other."""
    _corrupt_registry(home, b"{not json at all")
    proc = _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert proc.returncode == 0, proc.stderr
    assert sorted(_registry(home)) == ["sess-A"]


def test_a_registry_holding_a_list_does_not_crash_session_start(home):
    """`_load_registry` returns {} for a non-object registry, and nothing
    exercised that branch. MEASURED 2026-09-01: replacing the `isinstance(loaded,
    dict)` check with `if False` left all 196 tests across the five files that
    name this hook green, so the guard was standing unmeasured. A list reaches
    `reg[sid] = {...}` as `TypeError: list indices must be integers`."""
    _corrupt_registry(home, b'["s1", "s2"]')
    proc = _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert proc.returncode == 0, proc.stderr
    assert "TypeError" not in proc.stderr, proc.stderr
    assert sorted(_registry(home)) == ["sess-A"]


def test_session_start_refuses_a_payload_with_no_cwd(home):
    """The other half of "Returns 1 on missing fields". Only the session_id half
    was covered: MEASURED 2026-09-01, narrowing the check to `if not sid` left
    the same 196 tests green while an entry was written carrying `"cwd": null`,
    which every consumer that groups by directory then has to defend against."""
    proc = _run("session-start", {"session_id": "sess-A"}, home)
    assert proc.returncode == 1, proc.stderr
    assert _registry(home) == {}, "a cwd-less entry was written anyway"


def test_session_start_refuses_a_payload_with_no_session_id(home):
    proc = _run("session-start", {"cwd": "/work/tree"}, home)
    assert proc.returncode == 1, proc.stderr
    assert _registry(home) == {}


def test_a_healthy_registry_is_never_discarded(home):
    """The negative case. A recovery path that fires on a GOOD file would pass
    every test above while silently dropping live sessions."""
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    _run("session-start", {"session_id": "sess-B", "cwd": "/other/tree"}, home)
    assert sorted(_registry(home)) == ["sess-A", "sess-B"]


# --- the tty prompt must be bounded -------------------------------------------

def _function_source(src: str, name: str) -> str:
    """The source of ONE top-level function, bounded by the parser.

    This was `src[src.index("def _read_user_choice"):]` then
    `block[:block.index("\\ndef ", 1)]`. Three ways that misbehaves, all of
    them turning a harmless refactor into a red suite or a silent hole:
    `index` raises `ValueError: substring not found` (an ERROR, not a
    readable failure) the day the function is last in the file or is followed
    only by a `class` or an `if __name__` guard; the opening `index` matches
    the first occurrence anywhere, including a docstring mention; and a
    rename produces the same crash rather than a message naming the function.
    `ast` knows where the function starts and ends.
    """
    tree = ast.parse(src)
    matches = [n for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == name]
    assert len(matches) == 1, (
        f"expected exactly one top-level def {name}(...) in {HOOK.name}, "
        f"found {len(matches)}")
    return ast.get_source_segment(src, matches[0]) or ""


def test_the_prompt_does_not_call_blocking_readline():
    src = HOOK.read_text(encoding="utf-8")
    block = _function_source(src, "_read_user_choice")
    # Comment lines out. The comment explaining the fix names `tty.readline()`,
    # and the first version of this test matched its own explanation. That is
    # the same trap two guard tests fell into earlier the same night.
    code = "\n".join(ln for ln in block.splitlines()
                     if not ln.strip().startswith("#"))
    assert "readline()" not in code, (
        "the POSIX branch calls tty.readline() again; select fires on one byte "
        "in raw mode and readline then waits for a newline forever"
    )
    assert "deadline" in block, "no deadline bounds the read"
    assert re.search(r"select\.select\(\[tty\], \[\], \[\], remaining\)", block), (
        "select is no longer called with the REMAINING time, so a slow typist "
        "can still exceed the caller's timeout without bound"
    )


def test_the_windows_branch_is_bounded_on_the_monotonic_clock():
    """The same promise, on the branch that had stopped keeping it.

    The rewrite above bounded the POSIX branch and added the sentence "every wait
    is bounded, so the worst case is the timeout the caller asked for". The win32
    branch twenty lines up was left on `time.time()`, so an NTP correction or a
    manual clock change that stepped the system clock BACKWARD by N seconds
    extended that loop by N, blocking the Stop hook and the session exit with it.
    Found by the 2026-08-31 audit of the hooks family.

    A source guard, and stated plainly: this branch CANNOT be driven on Linux.
    `sys.platform == "win32"` is false here and the body imports `msvcrt`, which
    this interpreter does not have, so no behavioural test on this machine reaches
    the loop at all. MEASURED 2026-08-31 by reverting the branch to `_t.time()`
    and running every test file that names bridge-hook (276 tests): all 276
    passed. What this guard establishes is that the wrong clock cannot come back
    unnoticed; it does not establish that the loop behaves correctly on Windows,
    which nothing in this suite can.
    """
    src = HOOK.read_text(encoding="utf-8")
    block = _function_source(src, "_read_user_choice")
    marker = '        if sys.platform == "win32":'
    closer = "\n        else:"
    assert marker in block, "the win32 branch is gone; re-derive this guard"
    assert closer in block, "the POSIX branch is gone; re-derive this guard"
    win = block[block.index(marker):]
    win = win[:win.index(closer)]
    code = "\n".join(ln for ln in win.splitlines()
                     if not ln.strip().startswith("#"))
    # A floor: without it, a slice that had drifted to empty would satisfy every
    # assertion below while reading no loop at all.
    assert "msvcrt.kbhit()" in code, (
        f"the slice does not contain the win32 wait loop: {code!r}")
    assert "_t.time()" not in code, (
        "the win32 wait is back on the wall clock; a backward clock step "
        "lengthens it and blocks the session exit"
    )
    assert code.count("_t.monotonic()") == 2, (
        "the win32 deadline and its loop test are not both on the monotonic "
        f"clock (found {code.count('_t.monotonic()')} of 2)"
    )


def test_the_prompt_timeout_is_still_documented():
    """The docstring promise is what makes the bound a contract."""
    src = HOOK.read_text(encoding="utf-8")
    assert "Prompt timeout is 5s default" in src
