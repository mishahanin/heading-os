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


# --- the tty prompt must be bounded -------------------------------------------

def test_the_prompt_does_not_call_blocking_readline():
    src = HOOK.read_text(encoding="utf-8")
    block = src[src.index("def _read_user_choice"):]
    block = block[:block.index("\ndef ", 1)]
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


def test_the_prompt_timeout_is_still_documented():
    """The docstring promise is what makes the bound a contract."""
    src = HOOK.read_text(encoding="utf-8")
    assert "Prompt timeout is 5s default" in src
