"""k3's read of shard 06-p2, against the same files I had just finished auditing.

I had gone through these five files carefully, fixed five defects, written 45
tests and taken 30/30 on mutations. k3 then read the same shard and named ten
more. These are the ones that survived verification -- every one of them a thing
my own pass had walked straight past.

1. THE SPAWN THAT CONFIRMED NOTHING, ON THE PLATFORM WE RUN.
   `_spawn_detached_daemon` returns `proc.pid` on POSIX. A `Popen` that returns
   a pid proves the INTERPRETER launched, not the daemon: a missing
   `fireside-bot-daemon.py`, an import that raises, or an immediate exit all
   leave that pid naming a dead process, and `main()` printed "daemon was NOT
   RUNNING - started pid N" while the daemon stayed down. The Windows branch
   directly below had been rewritten to wait for the pid file, and its comment
   states this exact reason. The fix was applied to the platform this workspace
   does not use, and not to the one it does.

2. THE GUARD THAT DID NOT REACH ITS CONSUMER. I fixed `load_checkpoint` to
   survive a hand-edited file, and `load_roster_names` beside it. Ten lines
   further down, `prior["started_uids"]` still indexed directly, so a checkpoint
   merely MISSING that key raised KeyError and killed the run the guard exists
   to keep alive. Every other read in that block already used `.get`.

3. THE COMMENT THAT SPAWNED A SECOND BOT. `.fireside/remote-host` was read one
   line deep. A `#` on line 1 made the pointer invisible, execution fell through
   to the LOCAL path, and that path calls `_spawn_detached_daemon` -- two bots on
   one Telegram token, which is the disaster the `PermissionError` branch of
   `_daemon_alive` was written to prevent. The `startswith("#")` test proves
   comments were anticipated; only the first line was ever examined.

4. THE WINDOWS LIVENESS THAT READ "I CANNOT TELL" AS "DEAD". Twice: a failing
   `OpenProcess`, and a `GetExitCodeProcess` whose BOOL return was never checked.
   Both answer "dead" and both make `main()` spawn a second daemon -- the same
   hazard POSIX had already been fixed for, in the same function.

5. THE METRIC THAT HID THE COST ITS OWN COMMENT DISCLOSED. `handler_ms` started
   its clock BEFORE the lock, so under contention it converged on `total_ms` and
   reported queueing as handler slowness.

6. THE EXIT CODE THE SKILL WAS WRITTEN AGAINST. `gemini-consult` documents exit 3
   for "API call failed" and caught only `RuntimeError`; anything the proxy layer
   did not wrap escaped as a traceback and exit 1. I had checked that
   `call_model` wraps its OWN exceptions and stopped there, which answered a
   different question than the contract asks.

7. THE EARLIEST TIMESTAMP THAT WAS NOT THE EARLIEST. `_earliest` sorted ISO-8601
   as strings. That is chronological only while every stamp shares one offset,
   and this value decides whether a gate is judged "too young to tell" or "old
   enough, flag it".

8. THE DOCUMENTED OUTPUT FORMAT THAT NO LINE MATCHED.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fp():
    return _load("fireside_pulse_k3", "scripts/fireside-pulse.py")


@pytest.fixture(scope="module")
def gy():
    return _load("gate_yield_cli_k3", "scripts/gate-yield.py")


@pytest.fixture(scope="module")
def gc():
    return _load("gemini_consult_k3", "scripts/gemini-consult.py")


# ============================================================
# 1. The spawn that confirmed nothing
# ============================================================

def _spawn_env(fp, tmp_path, monkeypatch, *, alive_after=None):
    """Make the venv interpreter and daemon script look present, stub Popen."""
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path)
    venv = tmp_path / "scripts" / ".venv-fireside" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "fireside-bot-daemon.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(fp.sys, "platform", "linux")
    monkeypatch.setattr(fp.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"pid": 4242})())
    monkeypatch.setattr(fp.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def _alive():
        calls["n"] += 1
        if alive_after is not None and calls["n"] >= alive_after:
            return True, 9999
        return False, None

    monkeypatch.setattr(fp, "_daemon_alive", _alive)
    return calls


def test_a_spawn_that_never_produced_a_pid_file_reports_failure(fp, tmp_path,
                                                                 monkeypatch):
    """Popen succeeding proves the interpreter launched, not the daemon."""
    _spawn_env(fp, tmp_path, monkeypatch, alive_after=None)
    assert fp._spawn_detached_daemon() is None


def test_the_reported_pid_is_the_daemons_own_not_popens(fp, tmp_path, monkeypatch):
    _spawn_env(fp, tmp_path, monkeypatch, alive_after=1)
    assert fp._spawn_detached_daemon() == 9999


def test_the_posix_spawn_never_returns_the_popen_pid(fp, tmp_path, monkeypatch):
    """4242 is what `proc.pid` would have handed back unverified."""
    _spawn_env(fp, tmp_path, monkeypatch, alive_after=3)
    assert fp._spawn_detached_daemon() != 4242


def test_the_spawn_waits_rather_than_answering_on_the_first_look(fp, tmp_path,
                                                                  monkeypatch):
    """A daemon takes a moment to write its pid file; one look is not enough."""
    calls = _spawn_env(fp, tmp_path, monkeypatch, alive_after=5)
    assert fp._spawn_detached_daemon() == 9999
    assert calls["n"] >= 5


def test_a_missing_interpreter_still_short_circuits(fp, tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path)
    monkeypatch.setattr(fp.sys, "platform", "linux")
    assert fp._spawn_detached_daemon() is None


def test_a_stale_pid_file_over_a_dead_process_is_not_a_started_daemon(
        fp, tmp_path, monkeypatch):
    """`(False, <pid>)` is a real shape, and it means dead, not started.

    `_daemon_alive` returns it whenever a pid FILE survives the process it
    names: on Windows that is `GetExitCodeProcess` reporting anything but
    STILL_ACTIVE, and on either platform it is what a hand-written or
    orphaned `.fireside/daemon.pid` produces. Both halves of the condition
    are load-bearing -- a wait that accepted the pid alone would report
    "started pid N" off a file left behind by the daemon that just died,
    which is the same false confirmation this whole wait was added to end.
    """
    _spawn_env(fp, tmp_path, monkeypatch, alive_after=None)
    monkeypatch.setattr(fp, "_daemon_alive", lambda: (False, 31337))
    assert fp._spawn_detached_daemon() is None


# ============================================================
# 2. The guard that did not reach its consumer
# ============================================================

def test_a_checkpoint_missing_started_uids_does_not_raise(fp, tmp_path, monkeypatch,
                                                           capsys):
    # WORKSPACE too: without it `main()` finds the REAL `.fireside/remote-host`
    # in this workspace and takes the remote path instead of the local one.
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(fp, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(fp, "CHECKPOINT", tmp_path / "cp.json")
    monkeypatch.setattr(fp, "load_checkpoint", lambda: {"swap_events": []})
    monkeypatch.setattr(fp, "_daemon_alive", lambda: (True, 7))
    monkeypatch.setattr(fp, "save_checkpoint", lambda s: None)
    fp.main()
    assert "started" in capsys.readouterr().out


def test_an_empty_checkpoint_dict_does_not_raise(fp, tmp_path, monkeypatch, capsys):
    # WORKSPACE too: without it `main()` finds the REAL `.fireside/remote-host`
    # in this workspace and takes the remote path instead of the local one.
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(fp, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(fp, "CHECKPOINT", tmp_path / "cp.json")
    monkeypatch.setattr(fp, "load_checkpoint", dict)   # a checkpoint with no keys
    monkeypatch.setattr(fp, "_daemon_alive", lambda: (True, 7))
    monkeypatch.setattr(fp, "save_checkpoint", lambda s: None)
    fp.main()
    out = capsys.readouterr().out
    assert "no news" in out or "pulse" in out


def test_a_populated_prior_still_produces_a_delta(fp, tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    state.mkdir()
    (state / "sessions.jsonl").write_text(
        json.dumps({"event_type": "start_received", "user_id": 11,
                    "ts": "2026-08-25T00:00:00+00:00"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(fp, "STATE_DIR", state)
    monkeypatch.setattr(fp, "CHECKPOINT", tmp_path / "cp.json")
    monkeypatch.setattr(fp, "load_checkpoint", lambda: {"started_uids": []})
    monkeypatch.setattr(fp, "_daemon_alive", lambda: (True, 7))
    monkeypatch.setattr(fp, "save_checkpoint", lambda s: None)
    fp.main()
    assert "new /start" in capsys.readouterr().out


def test_a_uid_already_in_the_prior_is_not_reported_as_new_again(
        fp, tmp_path, monkeypatch, capsys):
    """The guard must not cost the subtraction it guards.

    `prior.get("started_uids", [])` replaced a direct index, and a `.get`
    whose result is then ignored is the same defect one line later: every
    member who has ever sent /start would be announced as new on every run,
    turning a delta report into a roster dump and burying the one arrival
    that actually happened.
    """
    state = tmp_path / "state"
    state.mkdir()
    (state / "sessions.jsonl").write_text(
        json.dumps({"event_type": "start_received", "user_id": 11,
                    "ts": "2026-08-25T00:00:00+00:00"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(fp, "STATE_DIR", state)
    monkeypatch.setattr(fp, "CHECKPOINT", tmp_path / "cp.json")
    monkeypatch.setattr(fp, "load_checkpoint", lambda: {"started_uids": [11]})
    monkeypatch.setattr(fp, "_daemon_alive", lambda: (True, 7))
    monkeypatch.setattr(fp, "save_checkpoint", lambda s: None)
    fp.main()
    assert "new /start" not in capsys.readouterr().out


# ============================================================
# 3. The comment that spawned a second bot
# ============================================================

def test_a_comment_on_line_one_does_not_hide_the_remote_host(fp):
    assert fp._remote_host_from("# fireside lives on the VM\nfireside-vm\n") \
        == "fireside-vm"


def test_a_plain_pointer_still_works(fp):
    assert fp._remote_host_from("fireside-vm\n") == "fireside-vm"


def test_several_comment_lines_are_all_skipped(fp):
    assert fp._remote_host_from("# one\n#two\n\n   # three\nvm-host\n") == "vm-host"


def test_a_file_of_only_comments_names_no_host(fp):
    assert fp._remote_host_from("# just a note\n#\n") == ""


def test_an_empty_pointer_names_no_host(fp):
    assert fp._remote_host_from("") == ""
    assert fp._remote_host_from("\n\n  \n") == ""


def test_the_host_is_stripped(fp):
    assert fp._remote_host_from("   fireside-vm   \n") == "fireside-vm"


def test_a_commented_pointer_reaches_the_remote_path_not_the_spawn(fp, tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """The whole point: this used to fall through and spawn a local daemon."""
    monkeypatch.setattr(fp, "WORKSPACE", tmp_path)
    (tmp_path / ".fireside").mkdir()
    (tmp_path / ".fireside" / "remote-host").write_text(
        "# fireside lives on the VM\nfireside-vm\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(fp, "_print_remote_status", lambda h: seen.append(h))
    spawned = []
    monkeypatch.setattr(fp, "_spawn_detached_daemon",
                        lambda: spawned.append(1))
    fp.main()
    assert seen == ["fireside-vm"]
    assert spawned == [], "a local daemon must never be spawned beside the remote one"


# ============================================================
# 4. The Windows liveness that read "cannot tell" as "dead"
# ============================================================

def test_an_unopenable_process_is_not_declared_dead(fp):
    """POSIX answers ALIVE for the equivalent PermissionError, and says why."""
    assert fp._windows_alive(False, False, 0) is True


def test_a_failed_exit_code_query_is_not_declared_dead(fp):
    """The BOOL return was never checked; an unchecked 0 read as 'exited'."""
    assert fp._windows_alive(True, False, 0) is True


def test_a_process_that_really_exited_is_dead(fp):
    assert fp._windows_alive(True, True, 0) is False
    assert fp._windows_alive(True, True, 1) is False


def test_a_still_active_process_is_alive(fp):
    assert fp._windows_alive(True, True, 259) is True


def test_no_uncertain_case_answers_dead(fp):
    """The dangerous direction is a false 'dead': it spawns a second bot."""
    for open_ok, exit_ok in ((False, False), (False, True), (True, False)):
        assert fp._windows_alive(open_ok, exit_ok, 0) is True


# ============================================================
# 5-7. The metric, the exit code, the earliest timestamp
# ============================================================

def test_the_webhook_log_separates_handler_time_from_queue_time():
    src = (ROOT / "scripts" / "fireside_webhook.py").read_text(encoding="utf-8")
    assert "queued_ms=%d" in src
    assert "t_start = time.monotonic()" in src
    # The start must be taken INSIDE the lock block, after `async with`.
    lock_at = src.index("async with handler_lock:")
    assert src.index("t_start = time.monotonic()") > lock_at


# Two webhook logging controls lived here and read the module's SOURCE TEXT.
# The first counted `%` conversions in the success log's format string while its
# own docstring said the format string and its five arguments "have to be
# counted together"; deleting an ARGUMENT survived it, and at runtime `logging`
# raises inside `emit` and writes no line at all. The second asserted that two
# COMMENT phrases appear in the file, which stays true however the code beneath
# them behaves. Replaced, not dropped, by tests that build the real app, POST at
# it, and call `record.getMessage()` on what was actually emitted:
# `tests/test_controls_that_restated_the_code_they_guarded.py`.


def test_a_non_runtime_error_still_exits_with_the_documented_code(gc, monkeypatch):
    monkeypatch.setattr(gc, "consult_gemini",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    assert gc.main(["--mode", "independent", "--question", "x"]) == 3


def test_an_os_error_also_exits_three(gc, monkeypatch):
    monkeypatch.setattr(gc, "consult_gemini",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("socket")))
    assert gc.main(["--mode", "independent", "--question", "x"]) == 3


def test_the_unexpected_failure_names_its_type(gc, monkeypatch, capsys):
    """Exit 3 must not turn a real bug into an anonymous 'API failed'."""
    monkeypatch.setattr(gc, "consult_gemini",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("choices")))
    gc.main(["--mode", "independent", "--question", "x"])
    assert "KeyError" in capsys.readouterr().err


def test_the_missing_key_sentinel_still_exits_two(gc, monkeypatch):
    monkeypatch.setattr(gc, "consult_gemini", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("GEMINI_API_KEY is missing from .env")))
    assert gc.main(["--mode", "independent", "--question", "x"]) == 2


def test_an_ordinary_runtime_error_still_exits_three(gc, monkeypatch):
    monkeypatch.setattr(gc, "consult_gemini", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("Proxy call failed for gemini: 500")))
    assert gc.main(["--mode", "independent", "--question", "x"]) == 3


def test_the_earliest_stamp_is_the_earliest_moment_not_the_smallest_string(gy):
    rows = [{"ts": "2026-01-02T00:00:00+05:00"}, {"ts": "2026-01-01T23:00:00+00:00"}]
    assert gy._earliest(rows) == "2026-01-02T00:00:00+05:00"


def test_a_z_suffix_compares_against_an_explicit_offset(gy):
    rows = [{"ts": "2026-01-01T10:00:00Z"}, {"ts": "2026-01-01T09:00:00+00:00"}]
    assert gy._earliest(rows) == "2026-01-01T09:00:00+00:00"


def test_a_naive_stamp_is_read_as_utc_not_dropped(gy):
    rows = [{"ts": "2026-01-01T09:00:00"}, {"ts": "2026-01-01T10:00:00+00:00"}]
    assert gy._earliest(rows) == "2026-01-01T09:00:00"


def test_an_unparseable_stamp_never_shortens_the_window(gy):
    """Dropping it would make a gate look younger than it is: a hidden finding."""
    rows = [{"ts": "not-a-time"}, {"ts": "2026-01-01T09:00:00+00:00"}]
    assert gy._earliest(rows) == "2026-01-01T09:00:00+00:00"


def test_all_unparseable_still_returns_something(gy):
    assert gy._earliest([{"ts": "junk"}, {"ts": "also-junk"}]) in ("junk", "also-junk")


def test_no_rows_is_still_no_window(gy):
    assert gy._earliest([]) == ""
    assert gy._earliest([{"other": 1}]) == ""


def test_a_single_row_is_its_own_earliest(gy):
    assert gy._earliest([{"ts": "2026-03-03T03:03:03+00:00"}]) == \
        "2026-03-03T03:03:03+00:00"


# ============================================================
# 8. The documented output format
# ============================================================

def test_the_docstring_describes_the_lines_the_code_prints(fp):
    doc = fp.__doc__ or ""
    assert "no news" in doc, "the documented no-change line must be the real one"
    assert 'ok: <last_poll_age>, started <N>/<tribe>' not in doc


def test_the_docstring_no_longer_claims_the_first_run_is_silent(fp):
    doc = fp.__doc__ or ""
    assert "initialise silently" not in doc
    assert "NOT silent" in doc


def test_the_docstring_names_the_no_tick_wording(fp):
    assert "no tick recorded" in (fp.__doc__ or "")
