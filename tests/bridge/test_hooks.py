"""End-to-end subprocess tests for the bridge hook router.

The hook is invoked by Claude Code as a child process with stdin payload.
These tests exercise the real subprocess interface, not the in-process API,
so any change to the hook contract is caught here.

**One call in this file was unbounded, against the rule stated 20 lines below.**
`test_session_start_with_malformed_stdin_returns_one` is the only test here that
bypasses `_invoke`, and it passed no `timeout=`, so the ceiling
`HOOK_CALL_CEILING_S` exists to enforce did not apply to it. A hook that
regressed into an indefinite wait on unparseable stdin would therefore hang the
run rather than fail it, which is precisely the failure the comment below
records having already happened once.

Stated honestly: this one is REASONED, not measured. The other findings in this
shard were confirmed by mutating production code and observing a green suite; a
hang cannot be demonstrated that way without writing a hook that hangs and then
waiting out CI, which is the cost the fix exists to avoid paying. What IS
measured is the guard: `test_every_subprocess_call_in_this_file_is_bounded`
below goes red when the `timeout=` is removed again, and that transcript is in
the fix report.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "bridge-hook.py"


# Every hook call is bounded. Two tests in this file exist to prove the Stop
# hook does not hang waiting for terminal input, and until 2026-08-30 the
# helper they run it through had no `timeout=` at all: a hook that regressed
# into an indefinite wait made the REGRESSION TEST hang, so the run never
# reached the `elapsed < 4` assertion and CI blocked until the outer job was
# killed. A test for a hang must fail on the hang, not become it.
#
# 30s, not 4 or 9: this is the "something is badly wrong" ceiling, deliberately
# well above the per-test elapsed bounds, so a slow machine fails on the
# assertion that names the real contract rather than on a transport timeout.
HOOK_CALL_CEILING_S = 30


def _invoke(subcommand: str, payload: dict) -> subprocess.CompletedProcess:
    """Helper: run the hook with stdin payload, return CompletedProcess.
    HOME/USERPROFILE must already be set by the caller (via _setup_env)."""
    return subprocess.run(
        [sys.executable, str(HOOK), subcommand],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=HOOK_CALL_CEILING_S,
    )


def _setup_env(tmp_path, monkeypatch):
    """Both HOME (POSIX) and USERPROFILE (Windows) point at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def test_session_start_writes_registry(tmp_path, monkeypatch):
    """SessionStart hook writes one entry keyed by cwd containing session_id + metadata."""
    _setup_env(tmp_path, monkeypatch)
    payload = {
        "session_id": "sid-abc",
        "transcript_path": "/path/to/transcript.jsonl",
        "cwd": str(tmp_path / "ws"),
        "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    assert r.stdout == "", f"SessionStart hook leaked to stdout: {r.stdout!r}"
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    assert reg.exists()
    data = json.loads(reg.read_text())
    # Keyed by session_id since 2026-08-23. It was cwd, which collapsed two
    # sessions in one directory into one entry and let the first to exit
    # deregister the live one. See tests/test_bridge_hook_session_registry.py.
    assert data["sid-abc"]["session_id"] == "sid-abc"
    assert data["sid-abc"]["cwd"] == str(tmp_path / "ws")
    assert data["sid-abc"]["transcript_path"] == "/path/to/transcript.jsonl"
    assert "started_at" in data["sid-abc"]


def test_session_start_dedupes_on_session_id(tmp_path, monkeypatch):
    """Two SessionStart events for the SAME session produce one registry entry.

    This used to dedupe on cwd, which also collapsed two DIFFERENT sessions in
    one directory. Re-registering one session is the case worth deduping;
    two sessions sharing a directory is not, and is covered by
    tests/test_bridge_hook_session_registry.py."""
    _setup_env(tmp_path, monkeypatch)
    payload = {
        "session_id": "sid-abc",
        "transcript_path": "/x",
        "cwd": str(tmp_path / "ws"),
        "source": "startup",
        "hook_event_name": "SessionStart",
    }
    for _ in range(2):
        r = _invoke("session-start", payload)
        assert r.returncode == 0
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    data = json.loads(reg.read_text())
    assert len(data) == 1


def test_session_end_removes_only_the_session_it_names(tmp_path, monkeypatch):
    """SessionEnd removes the entry for the given SESSION, not for the cwd.

    Two sessions in ONE directory, which is the ordinary case on this machine
    and the reason the registry was rekeyed on 2026-08-23. Ending the first
    must leave the second registered.

    Until 2026-08-30 this registered a single session and its docstring still
    described the retired cwd contract, so removal-by-cwd and
    removal-by-session-id were indistinguishable here: an implementation that
    deregistered every entry sharing the cwd - the exact regression the
    rekeying was done to prevent - passed. The sibling
    `test_session_start_writes_registry` already stated the current contract
    in a comment; this one now measures it.
    """
    _setup_env(tmp_path, monkeypatch)
    cwd = str(tmp_path / "ws")
    for sid in ("sid-abc", "sid-def"):
        _invoke("session-start", {
            "session_id": sid, "transcript_path": f"/x/{sid}", "cwd": cwd,
            "source": "startup", "hook_event_name": "SessionStart",
        })
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    assert set(json.loads(reg.read_text())) == {"sid-abc", "sid-def"}

    r = _invoke("session-end", {"session_id": "sid-abc", "cwd": cwd,
                                "hook_event_name": "SessionEnd"})
    assert r.returncode == 0
    assert r.stdout == "", f"SessionEnd hook leaked to stdout: {r.stdout!r}"
    data = json.loads(reg.read_text())
    assert "sid-abc" not in data
    assert cwd not in data
    assert "sid-def" in data, (
        "ending one session deregistered the other live session in the same "
        "directory; the registry is keyed by session_id, not by cwd")


def test_session_end_is_idempotent_for_an_unregistered_session(tmp_path, monkeypatch):
    """SessionEnd for a session that was never registered is a no-op.

    Named for the session, not the cwd: the registry has been keyed by
    session_id since 2026-08-23 and the old wording described a lookup the
    hook no longer performs.
    """
    _setup_env(tmp_path, monkeypatch)
    payload = {"session_id": "sid-never-registered",
               "cwd": str(tmp_path / "never-registered"),
               "hook_event_name": "SessionEnd"}
    r = _invoke("session-end", payload)
    assert r.returncode == 0


def test_unknown_subcommand_returns_one(tmp_path, monkeypatch):
    """An unrecognized subcommand prints to stderr and exits 1."""
    _setup_env(tmp_path, monkeypatch)
    r = _invoke("bogus-subcommand", {})
    assert r.returncode == 1
    assert "unknown" in r.stderr.lower()


def test_session_start_with_missing_session_id_returns_one(tmp_path, monkeypatch):
    """If the payload lacks session_id, hook exits 1 with stderr message."""
    _setup_env(tmp_path, monkeypatch)
    payload = {"cwd": str(tmp_path / "ws"), "hook_event_name": "SessionStart"}
    r = _invoke("session-start", payload)
    assert r.returncode == 1
    assert "session_id" in r.stderr or "cwd" in r.stderr


def test_session_start_with_malformed_stdin_returns_one(tmp_path, monkeypatch):
    """Malformed stdin JSON falls through to missing-fields error (returncode 1)."""
    _setup_env(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(HOOK), "session-start"],
        input="not-json{garbage",
        capture_output=True,
        text=True,
        # The only call in this file that does not go through `_invoke`, and it
        # carried no bound until 2026-08-31. A hook that hangs on unparseable
        # stdin would have hung the run instead of failing it.
        timeout=HOOK_CALL_CEILING_S,
    )
    assert r.returncode == 1
    # Falls into session_start with empty payload -> missing session_id/cwd
    assert "session_id" in r.stderr or "cwd" in r.stderr


def test_every_subprocess_call_in_this_file_is_bounded():
    """No `subprocess.run` here may omit `timeout=`.

    `_invoke` carries the ceiling, and for eleven of the twelve hook calls in
    this file that was enough. The twelfth was written inline, because the test
    needed to send raw bytes rather than a JSON payload, and the ceiling did not
    come with it. That is the shape a comment cannot prevent: the next test that
    needs an unusual stdin will also be written inline.

    So this asks the AST instead of asking the author. Every `subprocess.run`
    call in this module must pass `timeout`, whether by keyword or by unpacking
    a dict that names it. `_invoke`'s own call is included and satisfies it.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    calls: list[int] = []
    unbounded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_run = (isinstance(func, ast.Attribute) and func.attr == "run"
                  and isinstance(func.value, ast.Name)
                  and func.value.id == "subprocess")
        if not is_run:
            continue
        calls.append(node.lineno)
        # `timeout=X`, or `**kwargs` that might carry it (conservative: a
        # double-star unpack is accepted rather than reported, since its
        # contents are not knowable here).
        bounded = any(kw.arg == "timeout" or kw.arg is None
                      for kw in node.keywords)
        if not bounded:
            unbounded.append(node.lineno)

    # Anti-vacuity. A walk that stopped matching `subprocess.run` would report a
    # bounded file over nothing at all, which is how the unbounded call survived
    # in the first place: nothing was looking.
    assert len(calls) >= 2, (
        f"only {len(calls)} subprocess.run call(s) reached the guard: {calls}")
    assert not unbounded, (
        "these subprocess.run calls pass no `timeout=`, so a hook that hangs "
        "will hang the test run instead of failing it. Add "
        "`timeout=HOOK_CALL_CEILING_S`:\n  "
        + "\n  ".join(f"{Path(__file__).name}:{n}" for n in unbounded))


def test_session_start_recovers_from_corrupt_registry(tmp_path, monkeypatch):
    """A corrupt registry file is silently overwritten with a clean entry."""
    _setup_env(tmp_path, monkeypatch)
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    reg.parent.mkdir(parents=True)
    reg.write_text("GARBAGE{not-json{")
    payload = {
        "session_id": "sid-after-corruption",
        "cwd": str(tmp_path / "ws"),
        "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    data = json.loads(reg.read_text())
    assert data["sid-after-corruption"]["session_id"] == "sid-after-corruption"


def test_session_start_records_parseable_started_at(tmp_path, monkeypatch):
    """started_at field round-trips through datetime.fromisoformat with UTC tzinfo."""
    from datetime import datetime, timezone
    _setup_env(tmp_path, monkeypatch)
    cwd = str(tmp_path / "ws")
    payload = {
        "session_id": "sid-ts", "cwd": cwd, "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    data = json.loads((tmp_path / ".claude" / "state" / "active-sessions.json").read_text())
    parsed = datetime.fromisoformat(data["sid-ts"]["started_at"])
    assert parsed.tzinfo == timezone.utc


def test_session_start_records_int_pid(tmp_path, monkeypatch):
    """pid field is recorded as a positive integer (Claude Code process PID)."""
    _setup_env(tmp_path, monkeypatch)
    cwd = str(tmp_path / "ws")
    payload = {
        "session_id": "sid-pid", "cwd": cwd, "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    data = json.loads((tmp_path / ".claude" / "state" / "active-sessions.json").read_text())
    pid = data["sid-pid"]["pid"]
    assert isinstance(pid, int)
    assert pid > 0


def test_stop_without_origin_is_noop(tmp_path, monkeypatch):
    """When BRIDGE_ORIGIN is unset, /stop returns 0 with no prompt (background safe)."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.delenv("BRIDGE_ORIGIN", raising=False)
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    assert r.returncode == 0
    # What the hook ACTUALLY prints when the gate opens is
    # `bridge: [stay (Enter) / browser (b)] - Ns to stay:` on stdout and
    # `bridge: stay.` on stderr. The old assertion searched for the literal
    # "stay or browser", a phrase that appears in the hook's DOCSTRING and on no
    # output path. It could not fail, so a broken origin gate - and the several
    # seconds of blocking prompt that comes with it - would have passed here.
    combined = (r.stdout + r.stderr).lower()
    assert "bridge:" not in combined, (
        f"the Stop hook spoke with no BRIDGE_ORIGIN set:\n{combined!r}"
    )


def test_stop_with_browser_origin_prompts_and_defaults_stay(tmp_path, monkeypatch):
    """When BRIDGE_ORIGIN=browser is set, the hook prompts. With no tty + short
    timeout, it defaults to 'stay' and writes the decision to stderr."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "1")  # speed up test
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    assert r.returncode == 0
    assert "stay" in r.stderr.lower()


def test_stop_does_not_hang_when_no_tty(tmp_path, monkeypatch):
    """Regression: the prompt must read from /dev/tty (POSIX) or the Win32
    console (Windows), NOT from sys.stdin (which delivered the JSON payload
    and is at EOF). With no controlling tty in pytest subprocess context,
    the helper must short-circuit so the hook defaults to stay within
    BRIDGE_STOP_TIMEOUT, not the subprocess.run() timeout."""
    import time as _t
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "1")
    started = _t.time()
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    elapsed = _t.time() - started
    assert r.returncode == 0
    assert "stay" in r.stderr.lower()
    assert elapsed < 4, f"hook hung for {elapsed:.1f}s - tty detection broken"


# Direct unit tests for the helpers (no subprocess - testing pure logic)

def test_find_daemon_state_finds_at_ancestor(tmp_path):
    """_find_daemon_state walks up from start looking for .daemon-state/port."""
    # Import the hook module directly for unit-test access to helpers.
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_hook_helpers", HOOK)
    hook_mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_hook_helpers"] = hook_mod
    spec.loader.exec_module(hook_mod)
    # Plant .daemon-state/port at tmp_path, walk up from a nested subdir.
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("31415")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = hook_mod._find_daemon_state(nested)
    assert found == state_dir


def test_find_daemon_state_returns_none_when_absent(tmp_path):
    """Returns None when no .daemon-state/port exists in the ancestor chain."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_hook_helpers", HOOK)
    hook_mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_hook_helpers"] = hook_mod
    spec.loader.exec_module(hook_mod)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert hook_mod._find_daemon_state(nested) is None


def _hook_module():
    """The hook loaded in-process, for the paths a subprocess cannot reach.

    `stop()` reads the keyboard, and a pytest subprocess has no tty, so what
    the user TYPED can only be driven by replacing `_read_user_choice` on an
    imported module.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_hook_helpers", HOOK)
    hook_mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_hook_helpers"] = hook_mod
    spec.loader.exec_module(hook_mod)
    return hook_mod


@pytest.mark.parametrize("typed", ["b", "browser", "B", "Browser ", " browser"])
def test_the_word_the_prompt_offers_selects_the_browser(tmp_path, monkeypatch,
                                                        capsys, typed):
    """The prompt advertised "browser" and only the letter "b" matched it.

    Found by the 2026-08-24 campaign (shard `hooks-00-p2`, finding 4). The
    prompt reads `[stay (Enter) / browser (b)]`, presenting the full word as a
    labelled option, and the match was exact equality against `"b"`. A user who
    typed `browser` and pressed Enter produced `choice == "browser"`, fell into
    the `else`, and was told "stay" - the opposite of the request, with nothing
    saying the input had been rejected.

    Case and surrounding whitespace are covered because `_read_user_choice`
    already applies `.strip().lower()` to whatever it read, so those forms
    reach `stop()` normalised and a match that missed them would be a second
    bug of the same shape.
    """
    hook_mod = _hook_module()
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setattr(hook_mod, "_read_user_choice",
                        lambda timeout: typed.strip().lower())
    called = {}
    monkeypatch.setattr(hook_mod, "_trigger_return",
                        lambda sid, cwd: called.setdefault("sid", sid))

    assert hook_mod.stop({"session_id": "sid-1", "cwd": str(tmp_path)}) == 0
    err = capsys.readouterr().err
    assert called.get("sid") == "sid-1", (
        f"typing {typed!r} at a prompt that offers 'browser' did not return to "
        f"the browser; the hook said: {err!r}")
    assert "returning to browser" in err


@pytest.mark.parametrize("typed", ["", "x", "stay", "brow", "browsers"])
def test_anything_else_still_stays(tmp_path, monkeypatch, capsys, typed):
    """The anchor. Without it the fix passes by returning to the browser always.

    Empty string is the important member: it is what a timeout and a headless
    run both produce, and "stay" is the documented default for both.
    """
    hook_mod = _hook_module()
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setattr(hook_mod, "_read_user_choice", lambda timeout: typed)

    def _must_not_run(sid, cwd):
        raise AssertionError(f"{typed!r} was read as a request for the browser")

    monkeypatch.setattr(hook_mod, "_trigger_return", _must_not_run)
    assert hook_mod.stop({"session_id": "sid-1", "cwd": str(tmp_path)}) == 0
    assert "bridge: stay." in capsys.readouterr().err


def test_the_headless_promise_names_the_platform_it_holds_on():
    """A docstring that promised the POSIX answer for Windows too.

    Found by the 2026-08-24 campaign (shard `hooks-00-p2`, finding 3). The line
    read "If no tty is available (headless `claude -p`, CI, background daemon),
    return empty string", unconditionally. On POSIX that is immediate: opening
    `/dev/tty` raises and the handler catches it. On Windows in a process with
    no console, `msvcrt.kbhit()` returns 0 rather than raising, so that handler
    never fires and the empty string arrives only after the full timeout.

    This pins the DOCUMENTATION, and says so: the win32 branch still has no
    console detection, and nothing here can measure Windows. If someone adds
    the detection, this test is the one that has to change, and its failure
    message says so rather than leaving them to guess.
    """
    hook_mod = _hook_module()
    doc = hook_mod._read_user_choice.__doc__ or ""
    fn = next(n for n in ast.walk(ast.parse(Path(HOOK).read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "_read_user_choice")
    # The docstring is DROPPED before the code is searched. It names
    # `GetConsoleWindow` itself, as the thing a future author should add, and a
    # whole-function match would read that instruction as the fix already
    # landing. A test that cannot tell prose from code is the shape this
    # workspace has been bitten by before.
    body = [n for n in fn.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    code = "\n".join(ast.unparse(n) for n in body)
    if "GetConsoleWindow" in code:
        raise AssertionError(
            "the win32 branch now detects an absent console, so the docstring "
            "caveat this test pins is obsolete: delete the caveat paragraph "
            "and rewrite this test against the new behaviour")
    assert "Windows" in doc and "timeout" in doc, (
        "the docstring must say that the Windows no-console path costs the "
        "full timeout rather than returning immediately, because the code "
        "still has no console check")
    assert "msvcrt.kbhit()" in doc, (
        "name the call that makes the difference, or the next reader cannot "
        "tell this caveat from hedging")


def test_stop_handles_non_numeric_timeout_env(tmp_path, monkeypatch):
    """A non-numeric BRIDGE_STOP_TIMEOUT falls back to 5, and says so.

    The fallback is asserted on the VALUE the hook resolved, not on how long
    the process took. The old test asserted `elapsed < 9` under a comment
    saying the expected interval was [5, 9): with no controlling tty the
    prompt short-circuits and nothing waits at all, so the measurement could
    never have distinguished a 5-second fallback from a 0-second one, and a
    hook that silently fell back to zero passed. The prompt line the hook
    prints carries the resolved number, which is the fact worth pinning.
    """
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "not-a-number")
    import time as _t
    started = _t.time()
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    elapsed = _t.time() - started
    assert r.returncode == 0
    assert "stay" in r.stderr.lower()
    assert "5s to stay" in r.stderr, (
        f"the bad env var did not fall back to 5s: {r.stderr!r}")
    assert elapsed < 9, f"hook hung for {elapsed:.1f}s on bad timeout env"


def test_stop_uses_a_valid_timeout_env_rather_than_the_fallback(tmp_path, monkeypatch):
    """The anchor for the test above: a good value is not silently replaced.

    Without this, a hook that ignored BRIDGE_STOP_TIMEOUT entirely and always
    printed 5 would satisfy the fallback assertion. 7 is neither the default
    nor a value used anywhere else in this file.
    """
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "7")
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    assert r.returncode == 0
    assert "7s to stay" in r.stderr, (
        f"a valid BRIDGE_STOP_TIMEOUT was not honoured: {r.stderr!r}")
