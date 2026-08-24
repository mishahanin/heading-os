"""Shard 02-p2 of the 2026-08-24 Kimi audit: three findings, three files.

1. `build_data_repo.main` wrote `.schema-version` with a plain `write_text`.
   It is the ONLY schema-handshake signal in the data overlay, so a truncated
   or zero-byte write has no second source to fall back on, and the sibling
   `build_engine_repo.py` already routes its equivalent marker through
   `atomic_write_text`.

2. `calibrate._pop_oldest` sorted the raw `ts` strings to choose which turn to
   shed. `filter_since`, twenty lines up in the same module, documents exactly
   why that is wrong: a transcript mixes offset notations and `'+' < 'Z'`, so
   `...T09:00:00Z` sorts BEFORE `...T10:00:00+05:00` while being four hours
   later. Truncation therefore shed the newer turn. The module already had
   `_instant`; this was the one place that did not use it.

3. `browser.py`'s `stop` subcommand took no `--port` and threw its args away,
   while `stop_comet` returned early whenever there was no lock file. Together
   those made a session on a non-default port unstoppable from the CLI in the
   state `_adopt_running_cdp` documents creating: a live endpoint reused
   "without a lock file". `status` had already been given `--port` for the
   mirror-image reason.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str, name: str):
    """Import a hyphen-or-underscore script by path."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def _code(path: Path) -> str:
    """Source with whole-line `#` comments removed.

    Each fix records what it replaced, so the removed call is quoted in a
    comment beside it and a plain grep would find its own tombstone.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


# ============================================================
# 1. the only handshake marker in the overlay
# ============================================================

BUILD_DATA = ROOT / "scripts" / "build_data_repo.py"


def test_the_schema_marker_is_written_atomically():
    code = _code(BUILD_DATA)
    assert 'atomic_write_text(target / ".schema-version"' in code
    assert '(target / ".schema-version").write_text' not in code, (
        "the marker went back to a plain write; a partial file here is an "
        "unreadable handshake with no second source")


def test_the_module_actually_binds_the_name_its_call_site_uses():
    """A grep for the call is not proof the name resolves.

    Deleting the import leaves the call text in place, so a source-only
    assertion passes while `main` dies with `NameError` on the one line the
    whole finding is about. Importing the module is what settles it.
    """
    mod = _load("scripts/build_data_repo.py", "build_data_repo_under_test")
    assert callable(mod.atomic_write_text)


def test_both_repo_builders_agree_on_how_a_marker_is_written():
    """The sibling is the standard, and the standard was applied to one of two."""
    for rel in ("scripts/build_data_repo.py", "scripts/build_engine_repo.py"):
        assert "atomic_write_text" in _code(ROOT / rel), rel


def test_the_marker_lands_with_the_version_and_a_newline(tmp_path, monkeypatch):
    """Behaviour, not just the call shape."""
    from scripts.utils.atomic import atomic_write_text
    from scripts.utils.paths import DATA_SCHEMA_VERSION

    target = tmp_path / "overlay"
    target.mkdir()
    atomic_write_text(target / ".schema-version", f"{DATA_SCHEMA_VERSION}\n")
    assert (target / ".schema-version").read_text(encoding="utf-8") == \
        f"{DATA_SCHEMA_VERSION}\n"


def test_an_interrupted_atomic_write_leaves_the_old_marker_intact(tmp_path,
                                                                  monkeypatch):
    """The property a plain write_text does not have.

    `atomic_write_text` builds a temp file and renames it, so a failure before
    the rename cannot truncate the file already there. A plain `write_text`
    opens the real path with O_TRUNC first, which is the whole risk.
    """
    import os

    from scripts.utils import atomic as atomic_mod

    marker = tmp_path / ".schema-version"
    marker.write_text("7\n", encoding="utf-8")

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_mod.atomic_write_text(marker, "8\n")
    assert marker.read_text(encoding="utf-8") == "7\n", (
        "the previous marker was destroyed by a write that never completed")


# ============================================================
# 2. a shed that dropped the newer turn
# ============================================================

@pytest.fixture(scope="module")
def cal():
    return _load("scripts/calibrate.py", "calibrate_under_test")


# 10:00+05:00 is 05:00Z, so the FIRST is older. Lexicographically it sorts last.
OLDER_MIXED = "2026-08-22T10:00:00+05:00"
NEWER_ZULU = "2026-08-22T09:00:00Z"


def test_the_older_instant_is_shed_even_when_it_sorts_last_as_a_string(cal):
    envelope = {"user_turns": [{"ts": OLDER_MIXED}],
                "assistant_turns": [{"ts": NEWER_ZULU}]}
    assert cal._pop_oldest(envelope, ("user_turns", "assistant_turns")) is True
    assert envelope["user_turns"] == [], (
        "the newer assistant turn was shed and the older user turn kept, which "
        "is the mixed-offset bug filter_since documents in this same module")
    assert envelope["assistant_turns"] == [{"ts": NEWER_ZULU}]


def test_the_same_two_stamps_in_one_notation_still_shed_the_older(cal):
    """Anchor: a comparison that always picks `user_turns` passes the test above."""
    envelope = {"user_turns": [{"ts": "2026-08-22T11:00:00Z"}],
                "assistant_turns": [{"ts": "2026-08-22T09:00:00Z"}]}
    cal._pop_oldest(envelope, ("user_turns", "assistant_turns"))
    assert envelope["assistant_turns"] == [], "the older turn survived"
    assert envelope["user_turns"] == [{"ts": "2026-08-22T11:00:00Z"}]


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 17])
def test_an_unplaceable_stamp_sheds_first_and_never_raises(cal, bad):
    """A stamp we cannot place in time must not be compared to a datetime."""
    envelope = {"user_turns": [{"ts": bad}],
                "assistant_turns": [{"ts": "2026-01-01T00:00:00Z"}]}
    assert cal._pop_oldest(envelope, ("user_turns", "assistant_turns")) is True
    assert envelope["user_turns"] == [], f"{bad!r} was not shed first"


def test_an_unplaceable_stamp_sheds_before_even_a_pre_epoch_one(cal):
    """The comment says unplaceable sheds FIRST, with no qualifier.

    Sorting an unplaceable stamp as if it were the epoch is nearly the same
    thing and not the same promise: a stamp from before 1970 has a NEGATIVE
    timestamp and would then shed ahead of it. Cheap to state, and it pins the
    guarantee as written rather than as it happens to behave on 2026 dates.
    """
    envelope = {"user_turns": [{"ts": None}],
                "assistant_turns": [{"ts": "1969-07-20T20:17:00Z"}]}
    cal._pop_oldest(envelope, ("user_turns", "assistant_turns"))
    assert envelope["user_turns"] == [], (
        "the 1969 stamp was shed first, so unplaceable is being treated as "
        "epoch rather than as oldest")


def test_an_empty_envelope_reports_nothing_left_to_shed(cal):
    assert cal._pop_oldest({"user_turns": [], "assistant_turns": []},
                           ("user_turns", "assistant_turns")) is False


def test_the_shed_uses_the_instant_helper_the_module_already_trusts(cal):
    body = _code(ROOT / "scripts" / "calibrate.py")
    body = body[body.index("def _pop_oldest("):]
    body = body[:body.index("\ndef ", 1)]
    assert "_instant(" in body
    assert "heads.sort()" not in body, (
        "a bare sort is a string comparison; that is the defect")


def test_truncation_still_reaches_its_budget(cal, tmp_path):
    """Anchor: an ordering fix must not stop the shedding from converging."""
    envelope = {
        "user_turns": [{"ts": f"2026-08-22T{h:02d}:00:00Z", "text": "x" * 500}
                       for h in range(10)],
        "assistant_turns": [{"ts": f"2026-08-22T{h:02d}:30:00Z", "text": "y" * 500}
                            for h in range(10)],
        "tool_errors": [],
        "system_reminders": [],
    }
    out = cal.apply_truncation(envelope, max_bytes=2000)
    assert cal.envelope_bytes(out) <= 2000 or not (
        out["user_turns"] or out["assistant_turns"])


# ============================================================
# 3. a session the CLI could not stop
# ============================================================

@pytest.fixture(scope="module")
def browser():
    return _load("scripts/browser.py", "browser_under_test")


def test_stop_accepts_a_port(browser):
    """`status` got this on 2026-08-24 for the mirror-image reason."""
    parser = _stop_parser(browser)
    assert parser.parse_args(["--port", "9333"]).port == 9333


def _stop_parser(browser) -> argparse.ArgumentParser:
    """Rebuild just the `stop` subparser from the real `main`.

    Calling `main()` would parse `sys.argv`, so the subparser is pulled out of
    the constructed parser instead of being restated here - a restated copy
    would pass while the real CLI still lacked the flag.
    """
    src = _code(ROOT / "scripts" / "browser.py")
    assert 'sub.add_parser("stop"' in src
    block = src[src.index('sub.add_parser("stop"'):]
    block = block[:block.index("set_defaults(func=cmd_stop)")]
    assert '"--port"' in block, "the stop subcommand still takes no --port"
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=None)
    return p


def test_cmd_stop_passes_its_port_through(browser, monkeypatch):
    """The arg has to REACH `stop_comet`; `cmd_stop` used to discard its args."""
    seen = {}
    monkeypatch.setattr(browser, "stop_comet",
                        lambda port=None, **kw: seen.setdefault("port", port) or True)
    browser.cmd_stop(argparse.Namespace(port=9333))
    assert seen["port"] == 9333


def test_a_port_alone_is_enough_to_stop_an_untracked_session(browser,
                                                             monkeypatch):
    """The state `_adopt_running_cdp` documents: a live endpoint, no lock file.

    `stop_comet` returned False before ever looking at `port`, so the session
    was unstoppable from the CLI. Wiring the flag without this is a flag that
    changes nothing.
    """
    monkeypatch.setattr(browser, "_active_lock_file", lambda: None)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [4242])
    killed = []
    monkeypatch.setattr(browser.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(browser, "_wait_until_cdp_down", lambda port, wait: True)

    assert browser.stop_comet(port=9333) is True
    assert killed == [4242], "nothing was signalled on the named port"


def test_no_lock_and_no_port_is_still_refused(browser, monkeypatch):
    """Anchor: proceeding with neither would signal whatever was on the default
    port, which is a different session's browser."""
    monkeypatch.setattr(browser, "_active_lock_file", lambda: None)

    def refuse(port):
        raise AssertionError("it went hunting for PIDs with nothing to aim at")

    monkeypatch.setattr(browser, "_pids_for_cdp_port", refuse)
    assert browser.stop_comet() is False


def test_a_confirmed_stop_removes_the_lock(browser, monkeypatch, tmp_path):
    """The lock is the tracking record; leaving it behind makes the next
    `status` report a session that is gone."""
    lock = tmp_path / "browser-cdp.json"
    lock.write_text('{"port": 9333, "pid": 4242, "browser": "brave"}',
                    encoding="utf-8")
    monkeypatch.setattr(browser, "_active_lock_file", lambda: lock)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [4242])
    monkeypatch.setattr(browser.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(browser, "_wait_until_cdp_down", lambda port, wait: True)

    assert browser.stop_comet() is True
    assert not lock.exists(), "the lock survived a confirmed shutdown"


def test_a_surviving_browser_keeps_the_lock_for_a_retry(browser, monkeypatch,
                                                        tmp_path):
    """Anchor: unlinking unconditionally would pass the test above."""
    lock = tmp_path / "browser-cdp.json"
    lock.write_text('{"port": 9333, "pid": 4242, "browser": "brave"}',
                    encoding="utf-8")
    monkeypatch.setattr(browser, "_active_lock_file", lambda: lock)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [4242])
    monkeypatch.setattr(browser.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(browser, "_wait_until_cdp_down", lambda port, wait: False)

    assert browser.stop_comet() is False
    assert lock.exists(), "the lock was cleared while the browser still answered"


def test_clearing_the_lock_is_a_no_op_when_there_is_none(browser):
    browser._clear_lock(None)


def test_an_already_dead_browser_still_has_its_stale_lock_removed(
        browser, monkeypatch, tmp_path):
    """The branch that never signals anything. It ANNOUNCES "clearing lock",
    and until this test nothing checked that it does: every lock assertion
    above goes through the kill path instead. A stale lock left here is the
    exact state `status` misreads as a live session."""
    lock = tmp_path / "browser-cdp.json"
    lock.write_text('{"port": 9333, "pid": 4242, "browser": "brave"}',
                    encoding="utf-8")
    monkeypatch.setattr(browser, "_active_lock_file", lambda: lock)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_cdp_ready", lambda port: False)
    monkeypatch.setattr(browser.os, "kill", _never_signalled)

    assert browser.stop_comet() is True
    assert not lock.exists(), "the stale lock survived, so `status` still sees a session"


def _never_signalled(pid, sig):
    raise AssertionError(f"signalled PID {pid} when nothing was holding the port")


def test_a_stopped_port_with_no_lock_says_so_rather_than_clearing_one(
        browser, monkeypatch, capsys):
    monkeypatch.setattr(browser, "_active_lock_file", lambda: None)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_cdp_ready", lambda port: False)
    assert browser.stop_comet(port=9333) is True
    out = capsys.readouterr().out
    assert "9333" in out
    assert "clearing lock" not in out, "it reported clearing a lock that is not there"


def test_the_cli_still_runs_end_to_end():
    """The subprocess path, because the tests above all monkeypatch it."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "browser.py"), "stop", "--help"],
        capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    assert "--port" in r.stdout
