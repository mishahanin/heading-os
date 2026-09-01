"""The shared liveness answer raised on the inputs its callers actually read.

`scripts/utils/pid_liveness.pid_is_running` is a predicate, and every caller
gets its PID out of a file the operator never types: a `.pid` written by a
daemon that may have been killed mid-write, or a `watch.json` whose `pid` field
is whatever JSON survived. The function handled `ProcessLookupError` and
`PermissionError` and nothing else, so two ordinary residues of a crash came
back as exceptions rather than verdicts.

MEASURED 2026-09-01, before the fix:

    pid_is_running(99999999999)  -> OverflowError: signed integer is greater
                                    than maximum        (out of os.kill)
    pid_is_running("1234")       -> TypeError: '<=' not supported between
                                    instances of 'str' and 'int'

and the two reachable paths, both of them read-only commands documented to
REPORT state:

    sentinel --status, PID file holding "99999999999"   -> OverflowError
    marp watch_status(), watch.json {"pid": 99999999999} -> OverflowError
    marp watch_status(), watch.json {"pid": "1234"}      -> TypeError

Neither number can name a process, so False is the answer.

The same run found the third one beside it. All three readers of marp's watch
state catch `(json.JSONDecodeError, KeyError)`. `UnicodeDecodeError` is a
`ValueError` and a SIBLING of `JSONDecodeError`, not a subclass, so a watch
state saved as UTF-16 went straight past:

    marp watch_status(), watch.json written as UTF-16   -> UnicodeDecodeError

over a function that answers "Corrupt watch state file." for every other
unreadable shape.

Nothing here starts a process, signals one, or touches the operator's real
`~/.marp/watch.json`: the state file is redirected to `tmp_path` and the only
live PID used is this interpreter's own.

Run: .venv/bin/python -m pytest
tests/test_a_liveness_answer_that_raised_instead_of_answering.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import pid_liveness  # noqa: E402
from scripts.utils.pid_liveness import PID_CEILING, pid_is_running  # noqa: E402


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# The predicate is total
# ============================================================

# Everything a PID field can hold once it has been through a file. `None` is a
# missing key read with `.get`; the string is a JSON `"pid"`; the float is what
# a JSON number with a decimal point parses to.
NOT_A_PID = [None, "1234", "", 1.5, [1], {"pid": 1}, b"1234"]


@pytest.mark.parametrize("value", NOT_A_PID, ids=lambda v: type(v).__name__ + repr(v)[:8])
def test_a_value_that_is_not_a_pid_answers_false_rather_than_raising(value):
    assert pid_is_running(value) is False


def test_a_number_above_the_platform_ceiling_answers_false():
    assert pid_is_running(PID_CEILING + 1) is False
    assert pid_is_running(2 ** 63) is False
    assert pid_is_running(10 ** 30) is False


def test_the_ceiling_itself_is_still_asked_of_the_operating_system(monkeypatch):
    """The bound needs a case ON the line, or the guard could be off by one and
    silently refuse a PID the kernel would have answered for.

    Both sides answer False, so the ANSWER cannot tell them apart. What
    distinguishes them is whether the question reached the kernel at all, so
    that is what this records. Written without a literal `os.kill(pid, 0)`,
    which is the shape the sibling suite's AST sweep flags as a private copy of
    this very question.
    """
    asked: list[tuple[int, int]] = []

    def recorder(pid, sig):
        asked.append((pid, sig))
        raise ProcessLookupError(pid)

    monkeypatch.setattr(pid_liveness.os, "kill", recorder)

    assert pid_is_running(PID_CEILING) is False
    assert asked == [(PID_CEILING, 0)], "the guard intercepted a PID the kernel takes"

    asked.clear()
    assert pid_is_running(PID_CEILING + 1) is False
    assert asked == [], "an over-range PID was handed to os.kill and would raise"


def test_the_ceiling_is_the_number_the_platform_actually_accepts():
    """The test above asks the guard about its own constant, so a ceiling set to
    any wrong value passes it: MEASURED 2026-09-01, `PID_CEILING = 2 ** 30`
    survived that test and every other one in this file. This pins the constant
    to the kernel instead. A ceiling set too LOW refuses PIDs `os.kill` would
    have answered for, which reintroduces the wrong verdict from the other side.

    The two literal probes are why this file appears in the sibling suite's
    DECLARED_LIVENESS_SITES: they are the measurement, and asking
    `pid_is_running` here would be asking the constant about itself again.
    """
    with pytest.raises(ProcessLookupError):
        os.kill(PID_CEILING, 0)
    with pytest.raises(OverflowError):
        os.kill(PID_CEILING + 1, 0)


def test_a_bool_is_not_read_as_pid_one():
    """`True` is an `int` in Python and would otherwise probe PID 1, which is
    alive on every Linux host: the predicate would answer True for a value that
    names no process at all."""
    assert pid_is_running(True) is False
    assert pid_is_running(False) is False


def test_a_real_live_pid_is_still_alive():
    """The anchor. A guard that answered False for everything would satisfy
    every test above and turn every `stop` in the tree into a no-op."""
    assert pid_is_running(os.getpid()) is True
    assert pid_is_running(1) is True


def test_a_real_dead_pid_is_still_dead():
    assert pid_is_running(2 ** 22 - 1) is False


# ============================================================
# sentinel --status over a PID file that outgrew a C int
# ============================================================

@pytest.fixture
def sentinel(tmp_path, monkeypatch):
    mod = _load("sentinel", "raised_probe_sentinel")
    monkeypatch.setattr(mod, "PID_FILE", tmp_path / "sentinel.pid")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
    return mod


def test_a_status_over_an_over_range_pid_file_reports_instead_of_tracebacking(
        sentinel, capsys):
    sentinel.PID_FILE.write_text("99999999999\n", encoding="utf-8")

    sentinel.check_status()

    out = capsys.readouterr().out
    assert "NOT running" in out
    assert not sentinel.PID_FILE.exists(), "the unusable PID file was left in place"


def test_a_status_over_this_process_still_says_running(sentinel, monkeypatch, capsys):
    """The mirror, and it carries the test above: a `--status` that answered
    "NOT running" unconditionally would pass it and delete a live daemon's
    handle. Identity is stubbed true so this measures the liveness verdict."""
    sentinel.PID_FILE.write_text(f"{os.getpid()}\n", encoding="utf-8")
    monkeypatch.setattr(sentinel, "_pid_is_sentinel", lambda pid: True)

    sentinel.check_status()

    assert "RUNNING" in capsys.readouterr().out
    assert sentinel.PID_FILE.exists()


# ============================================================
# marp watch state: three shapes that used to escape
# ============================================================

@pytest.fixture
def marp(tmp_path, monkeypatch):
    mod = _load("marp_render", "raised_probe_marp")
    monkeypatch.setattr(mod, "WATCH_STATE_FILE", tmp_path / "watch.json")
    return mod


def test_a_watch_state_with_a_string_pid_is_reported_not_raised(marp):
    marp.WATCH_STATE_FILE.write_text(json.dumps({"pid": "1234"}), encoding="utf-8")

    assert marp.watch_status()["running"] is False


def test_a_watch_state_with_an_over_range_pid_is_reported_not_raised(marp):
    marp.WATCH_STATE_FILE.write_text(json.dumps({"pid": 99999999999}),
                                     encoding="utf-8")

    assert marp.watch_status()["running"] is False


def test_a_watch_state_in_the_wrong_encoding_is_called_corrupt(marp):
    """UTF-16 is what an editor on the Windows side of this WSL2 host writes by
    default, and the file lives under the user's home rather than the repo."""
    marp.WATCH_STATE_FILE.write_bytes(json.dumps({"pid": 1}).encode("utf-16"))

    result = marp.watch_status()

    assert result["running"] is False
    assert "Corrupt" in result["message"], result


def test_a_watch_state_truncated_mid_character_is_called_corrupt(marp):
    """The likelier one: a write killed part-way through a multi-byte sequence."""
    marp.WATCH_STATE_FILE.write_bytes(b'{"pid": 1, "source_path": "caf\xc3')

    assert "Corrupt" in marp.watch_status()["message"]


def test_a_corrupt_watch_state_is_not_deleted_by_a_read_only_status(marp):
    """`watch_status` reports. `watch_stop` is the one that removes the file, and
    conflating them would throw away the evidence of what went wrong."""
    marp.WATCH_STATE_FILE.write_bytes(json.dumps({"pid": 1}).encode("utf-16"))

    marp.watch_status()

    assert marp.WATCH_STATE_FILE.exists()


def test_a_watch_stop_over_an_unreadable_state_removes_it_and_says_so(marp):
    marp.WATCH_STATE_FILE.write_bytes(json.dumps({"pid": 1}).encode("utf-16"))

    result = marp.watch_stop()

    assert result["ok"] is False
    assert "Corrupt" in result["message"]
    assert not marp.WATCH_STATE_FILE.exists()


def test_a_watch_start_is_not_blocked_by_an_unreadable_state(marp, tmp_path,
                                                             monkeypatch):
    """The third reader. It treats an unreadable state as "no watch is active",
    which is the only safe reading: refusing to start over a file nobody can
    parse would wedge the command until the operator deleted it by hand.

    `_resolve_marp_bin` is stubbed absent so the call stops one step past the
    state read. marp-cli IS installed on this host, and without the stub this
    test would spawn a real long-lived watch server and write a real theme.
    """
    monkeypatch.setattr(marp, "_resolve_marp_bin", lambda: None)
    marp.WATCH_STATE_FILE.write_bytes(json.dumps({"pid": os.getpid()}).encode("utf-16"))
    source = tmp_path / "deck.md"
    source.write_text("# deck\n", encoding="utf-8")

    result = marp.watch_start(source)

    assert result["error"] == "marp-not-installed", (
        f"the unreadable state stopped the call before the marp check: {result}")


def test_a_watch_start_is_still_blocked_by_a_readable_live_state(marp, tmp_path,
                                                                 monkeypatch):
    """The mirror of the test above, and the reason it cannot just swallow.
    A parseable state naming a live process must still refuse, and it must
    refuse at the STATE check rather than fall through to the marp check the
    test above lands on."""
    monkeypatch.setattr(marp, "_resolve_marp_bin", lambda: None)
    marp.WATCH_STATE_FILE.write_text(
        json.dumps({"pid": os.getpid(), "source_path": "x.md"}), encoding="utf-8")
    source = tmp_path / "deck.md"
    source.write_text("# deck\n", encoding="utf-8")

    result = marp.watch_start(source)

    assert result["error"] == "watch-active", result


def test_a_live_watch_state_still_reports_running(marp):
    """The anchor over all six marp cases. A reader that called everything
    corrupt would satisfy them and make watch mode unusable."""
    marp.WATCH_STATE_FILE.write_text(
        json.dumps({"pid": os.getpid(), "url": "http://localhost:8080"}),
        encoding="utf-8")

    result = marp.watch_status()

    assert result["running"] is True
    assert marp.WATCH_STATE_FILE.exists()
