#!/usr/bin/env python3
"""`scripts/calibrate.py` promised four things its code did not do.

Each promise is written down in the module — in a docstring, a flag's help, or
the envelope schema — and each was broken in a way that produced a confident
wrong answer rather than an error:

* **"Tolerate malformed lines."** Unparseable JSON was skipped; a well-formed
  line holding a NON-OBJECT (`null`, `123`, `"text"`) was appended as an event,
  and the first `.get()` downstream killed the whole run with an
  AttributeError. One odd line lost the entire envelope.
* **"Drop oldest user_turns until it fits within max_bytes."** It shed only
  `user_turns`, stopped when that list emptied, and returned an envelope that
  could still be many times the budget, stamped `truncated: True`, exit 0.
  This module's own measurement found assistant turns carry 96% of the prose:
  the loop shed the lighter side and never touched the heavy one.
* **`--since-utc`** compared ISO strings lexicographically. `'+' < 'Z'`, so
  `...T10:00:00+00:00` sorts before `...T10:00:00Z` while naming the same
  instant, and an event exactly at the threshold was kept or dropped by
  notation alone.
* **`ceo_only_paths`** was a hardcoded `[]` under two docstrings that called it
  enumerated, so a consumer could not tell "none exist" from "stub" — the
  coverage claim `.claude/rules/scope-claims.md` forbids.

Fixed 2026-08-24.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import calibrate  # noqa: E402


def _write(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _turn(role: str, ts: str, text: str) -> str:
    return json.dumps({"type": role, "timestamp": ts,
                       "message": {"content": text}})


# ---------------------------------------------------------------------------
# Tolerating malformed lines
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scalar", ["null", "123", '"text"', "true", "[]"])
def test_a_valid_json_non_object_line_is_skipped_not_fatal(tmp_path, scalar):
    path = _write(tmp_path,
                  _turn("user", "2026-08-22T10:00:00Z", "hello"),
                  scalar,
                  _turn("assistant", "2026-08-22T10:00:01Z", "hi"))
    events, skipped = calibrate.parse_jsonl(path)
    assert skipped == [2], f"line 2 held {scalar} and was not reported as skipped"
    assert len(events) == 2
    env = calibrate.build_envelope(path, events)  # used to raise AttributeError
    assert len(env["user_turns"]) == 1
    assert len(env["assistant_turns"]) == 1


def test_a_scalar_line_does_not_poison_the_session_timestamps(tmp_path):
    """`events[0].get(...)` and `events[-1].get(...)` crashed on the same line."""
    path = _write(tmp_path, "null",
                  _turn("user", "2026-08-22T10:00:00Z", "hello"), "42")
    events, _ = calibrate.parse_jsonl(path)
    env = calibrate.build_envelope(path, events)
    assert env["started_at_utc"] == "2026-08-22T10:00:00Z"
    assert env["ended_at_utc"] == "2026-08-22T10:00:00Z"


def test_unparseable_json_is_still_tolerated(tmp_path):
    """The half that already worked must keep working."""
    path = _write(tmp_path, "{not json", _turn("user", "t", "hi"))
    events, skipped = calibrate.parse_jsonl(path)
    assert skipped == [1] and len(events) == 1


def test_a_null_tool_input_does_not_crash(tmp_path):
    """`.get("input", {})` defaults only when the KEY is absent."""
    path = _write(tmp_path, json.dumps(
        {"type": "tool_use", "tool": "Bash", "input": None, "timestamp": "t"}))
    events, _ = calibrate.parse_jsonl(path)
    calibrate.build_envelope(path, events)  # used to raise


def test_a_string_tool_input_does_not_crash(tmp_path):
    path = _write(tmp_path, json.dumps(
        {"type": "tool_use", "tool": "Bash", "input": "ls", "timestamp": "t"}))
    events, _ = calibrate.parse_jsonl(path)
    calibrate.build_envelope(path, events)


def test_a_null_exit_code_is_not_an_error(tmp_path):
    """`None != 0` recorded every unstamped result as a failure."""
    path = _write(tmp_path, json.dumps(
        {"type": "tool_result", "tool": "Bash", "exit_code": None,
         "stderr": "", "timestamp": "t"}))
    events, _ = calibrate.parse_jsonl(path)
    env = calibrate.build_envelope(path, events)
    assert env["tool_errors"] == [], (
        "a missing exit code means the harness did not record one, not that "
        "the tool failed"
    )


def test_a_real_nonzero_exit_code_is_still_an_error(tmp_path):
    """Anchor: the guard above must not swallow genuine failures."""
    path = _write(tmp_path, json.dumps(
        {"type": "tool_result", "tool": "Bash", "exit_code": 2,
         "stderr": "boom", "timestamp": "t"}))
    events, _ = calibrate.parse_jsonl(path)
    env = calibrate.build_envelope(path, events)
    assert len(env["tool_errors"]) == 1 and env["tool_errors"][0]["exit_code"] == 2


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def _fat_envelope(user_kb: int, assistant_kb: int) -> dict:
    return {
        "session_id": "s", "session_path": "p", "started_at_utc": "",
        "ended_at_utc": "", "event_count": 0, "truncated": False,
        "user_turns": [{"ts": f"2026-08-22T10:00:0{i}Z", "text": "u" * 1000}
                       for i in range(user_kb)],
        "assistant_turns": [{"ts": f"2026-08-22T10:00:0{i}Z", "text": "a" * 1000}
                            for i in range(assistant_kb)],
        "tool_errors": [], "system_reminders": [],
    }


def test_truncation_sheds_the_heavy_side_too(tmp_path):
    """The reported defect: one huge assistant turn, one small user turn."""
    env = {
        "session_id": "s", "session_path": "p", "started_at_utc": "",
        "ended_at_utc": "", "event_count": 0, "truncated": False,
        "user_turns": [{"ts": "2026-08-22T10:00:00Z", "text": "hi"}],
        "assistant_turns": [{"ts": "2026-08-22T10:00:01Z", "text": "A" * 50_000}],
        "tool_errors": [], "system_reminders": [],
    }
    out = calibrate.apply_truncation(env, 1000)
    assert out["truncated"] is True
    assert calibrate.envelope_bytes(out) <= 1000, (
        "the loop emptied user_turns and returned; the 50 KB assistant turn "
        "was never touched"
    )


def test_truncation_keeps_the_recent_end_of_both_sides(tmp_path):
    """Shedding one list to exhaustion leaves questions with no answers."""
    env = _fat_envelope(user_kb=8, assistant_kb=8)
    out = calibrate.apply_truncation(env, 6000)
    assert out["user_turns"] and out["assistant_turns"], (
        "one side was drained completely; the surviving tail must stay a "
        "two-sided conversation"
    )
    # What survives is the tail, not the head.
    assert out["user_turns"][-1]["ts"] == "2026-08-22T10:00:07Z"
    assert out["assistant_turns"][-1]["ts"] == "2026-08-22T10:00:07Z"


def test_truncation_sheds_reminders_before_prose():
    env = _fat_envelope(user_kb=3, assistant_kb=3)
    env["system_reminders"] = [{"ts": "2026-08-22T09:00:00Z", "text": "r" * 20_000}]
    out = calibrate.apply_truncation(env, 7000)
    assert out["system_reminders"] == []
    assert len(out["user_turns"]) == 3 and len(out["assistant_turns"]) == 3, (
        "harness boilerplate is the least of the signal and must go first"
    )


def test_an_envelope_within_budget_is_untouched():
    env = _fat_envelope(user_kb=1, assistant_kb=1)
    out = calibrate.apply_truncation(env, 1_000_000)
    assert out["truncated"] is False
    assert len(out["user_turns"]) == 1 and len(out["assistant_turns"]) == 1


def test_an_impossible_budget_is_reported_not_faked(tmp_path, capsys):
    """`--max-bytes 10` cannot be met by shedding lists. Say so."""
    path = _write(tmp_path, _turn("user", "2026-08-22T10:00:00Z", "x" * 5000))
    rc = calibrate.main(["--session", str(path), "--no-workspace", "--max-bytes", "10"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "could not be met" in err, (
        "an over-budget envelope shipped with `truncated: true` and no warning; "
        "--max-bytes is a contract a consumer relies on"
    )


# ---------------------------------------------------------------------------
# --since-utc
# ---------------------------------------------------------------------------

def test_since_utc_compares_instants_not_strings():
    """`+00:00` sorts before `Z` although they name the same moment."""
    events = [{"type": "user", "timestamp": "2026-08-22T10:00:00+00:00"}]
    kept = calibrate.filter_since(events, "2026-08-22T10:00:00Z")
    assert kept == events, (
        "an event exactly at the threshold was dropped because '+' < 'Z'"
    )


def test_since_utc_still_drops_what_is_genuinely_older():
    """Anchor: a filter that keeps everything is not a filter."""
    events = [
        {"type": "user", "timestamp": "2026-08-22T09:59:59Z"},
        {"type": "user", "timestamp": "2026-08-22T10:00:01Z"},
    ]
    kept = calibrate.filter_since(events, "2026-08-22T10:00:00Z")
    assert [e["timestamp"] for e in kept] == ["2026-08-22T10:00:01Z"]


def test_since_utc_handles_a_non_utc_offset():
    """13:00+03:00 is 10:00Z, so it is exactly at the threshold."""
    events = [{"type": "user", "timestamp": "2026-08-22T13:00:00+03:00"}]
    assert calibrate.filter_since(events, "2026-08-22T10:00:00Z") == events


def test_an_unreadable_event_timestamp_is_kept():
    """Over-report rather than silently discard a turn the filter cannot read."""
    events = [{"type": "user", "timestamp": "not-a-time"},
              {"type": "user"}]
    assert calibrate.filter_since(events, "2026-08-22T10:00:00Z") == events


def test_a_garbage_since_utc_is_a_clean_error(tmp_path, capsys):
    path = _write(tmp_path, _turn("user", "2026-08-22T10:00:00Z", "hi"))
    rc = calibrate.main(["--session", str(path), "--no-workspace",
                         "--since-utc", "yesterday"])
    assert rc == 1
    assert "not an ISO timestamp" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# ceo_only_paths
# ---------------------------------------------------------------------------

def test_ceo_only_paths_is_enumerated_not_a_stub():
    paths = calibrate._ceo_only_paths()
    assert paths, (
        "the field is documented as enumerated in two docstrings; an always-"
        "empty list makes 'none exist' indistinguishable from 'not implemented'"
    )
    assert "outputs/" in paths and "crm/contacts/" in paths


def test_ceo_only_paths_comes_from_the_routing_map(monkeypatch):
    """A second hand-kept list is the copy that stops being updated."""
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_routing_map", lambda: {
        "default": "engine",
        "rules": {"secret/": "private", "docs/": "engine", "shared/": "corporate"},
    })
    assert calibrate._ceo_only_paths() == ["secret/"]


def test_a_broken_routing_map_does_not_take_the_envelope_down(monkeypatch, capsys):
    import scripts.utils.workspace as ws

    def boom():
        raise RuntimeError("map is corrupt")

    monkeypatch.setattr(ws, "load_routing_map", boom)
    assert calibrate._ceo_only_paths() == []
    assert "routing map unreadable" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# locate_session
# ---------------------------------------------------------------------------

def test_a_transcript_that_vanishes_mid_sort_is_skipped(tmp_path, monkeypatch):
    """glob lists, then stat reads: a rotated file crashed the run with exit 1."""
    (tmp_path / "a.jsonl").write_text("{}\n")
    (tmp_path / "b.jsonl").write_text("{}\n")
    gone = tmp_path / "b.jsonl"

    real_stat = Path.stat

    def flaky_stat(self, *a, **k):
        if self == gone:
            raise FileNotFoundError(str(gone))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    assert calibrate.locate_session(tmp_path) == tmp_path / "a.jsonl"
