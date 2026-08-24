"""Shard 03-p3: a permanent record built from an unvalidated model reply.

`summarize` read `topics`, `considered` and `open` with a bare comprehension
over whatever the 4B model returned. A reply of
`"topics": "crm sync, memory index"` -- a deviation, not a malformed reply --
iterated the STRING, so `topics` became `['c', 'r', 'm', ' ', 's', 'y']` and
that went into the entry's heading and frontmatter. Chronicle entries are
immutable: `already_chronicled` makes sure the session is never redone.

The same function's transport catch named `URLError, TimeoutError,
JSONDecodeError` and promised "None on a hard failure". A server that drops the
connection mid-response raises `RemoteDisconnected`, which is none of those, so
the exception left `summarize`, left `cmd_build`'s loop, and killed an
unattended nightly job designed to fail one session at a time.

A `{"skip": true}` verdict was persisted nowhere, so once `capped_marker` pinned
the date cutoff at a persistently failing session, every skipped session newer
than it was re-summarized on every nightly run -- a full model prefill each
time, the set growing, every run exiting 0.

`--days`-style counters and hints round it out: `written += 1` ran on the
dry-run branch too, printing "3 written ... (dry-run, nothing saved)"; and
`classification-health.py --outputs-drift` printed a pin instruction naming
`findings[0]` for every finding.

Tests: this file.
"""
from __future__ import annotations

import http.client
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.chronicle as ch  # noqa: E402


# ==========================================================================
# 1 - the topic list shredded into single letters
# ==========================================================================

@pytest.mark.parametrize("field,limit", [("topics", 6), ("considered", 8), ("open", 6)])
def test_a_string_answer_is_never_iterated_as_characters(field, limit):
    out = ch._string_list("crm sync, memory index", limit)
    assert out == ["crm sync", "memory index"], \
        f"{field}'s reader split a string into characters"
    assert all(len(v) > 1 for v in out), "single-character fragments survived"


def test_a_proper_list_is_unchanged():
    assert ch._string_list(["crm sync", " memory index "], 6) == \
        ["crm sync", "memory index"]


def test_a_dict_yields_nothing_rather_than_its_keys():
    assert ch._string_list({"crm": 1, "memory": 2}, 6) == [], \
        "a dict answer contributed its keys as topics"


def test_none_and_absent_are_empty():
    assert ch._string_list(None, 6) == []
    assert ch._string_list("", 6) == []
    assert ch._string_list("   ", 6) == []


def test_a_number_yields_nothing():
    assert ch._string_list(7, 6) == []


def test_the_cap_is_applied():
    assert len(ch._string_list([f"t{i}" for i in range(50)], 6)) == 6
    assert len(ch._string_list(",".join(f"t{i}" for i in range(50)), 8)) == 8


def test_empty_fragments_of_a_string_are_dropped():
    assert ch._string_list("a,,b, ,c", 6) == ["a", "b", "c"]


# ==========================================================================
# 2 - the transport failure that killed the whole run
# ==========================================================================

class _Reply:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _summarize_with(monkeypatch, opener):
    monkeypatch.setattr(ch, "ollama_url", lambda: "http://127.0.0.1:1/api/generate")
    monkeypatch.setattr(ch.urllib.request, "urlopen", opener)
    return ch.summarize("x" * 500)


@pytest.mark.parametrize("exc", [
    http.client.RemoteDisconnected("closed"),
    http.client.IncompleteRead(b""),
    ConnectionResetError("peer reset"),
    urllib.error.URLError("refused"),
    TimeoutError("slow"),
    OSError("broken pipe"),
])
def test_every_transport_failure_returns_none(monkeypatch, exc, capsys):
    """The docstring promises None on a hard failure. All of these are one."""
    def _boom(*a, **k):
        raise exc

    assert _summarize_with(monkeypatch, _boom) is None, \
        f"{type(exc).__name__} escaped summarize and would kill the whole run"
    assert "model call failed" in capsys.readouterr().err


def test_a_body_that_is_not_json_returns_none(monkeypatch, capsys):
    class _Garbage:
        def read(self):
            return b"<html>502</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    assert _summarize_with(monkeypatch, lambda *a, **k: _Garbage()) is None


def test_a_json_body_that_is_not_an_object_returns_none(monkeypatch):
    """`.get` on a list raises past every guard; it must not reach that."""
    assert _summarize_with(monkeypatch, lambda *a, **k: _Reply(["unexpected"])) is None


def test_a_good_reply_still_summarizes(monkeypatch):
    payload = {"response": json.dumps(
        {"gist": "Discussed CRM sync.", "topics": "crm sync, memory index",
         "class": "business"})}
    out = _summarize_with(monkeypatch, lambda *a, **k: _Reply(payload))
    assert out is not None
    assert out["gist"] == "Discussed CRM sync."
    assert out["topics"] == ["crm sync", "memory index"], \
        "the end-to-end path still shreds a string answer"


@pytest.mark.parametrize("field,key", [
    ("considered", "considered"), ("open", "open"),
])
def test_every_list_field_is_checked_end_to_end(monkeypatch, field, key):
    """Not just `topics`. Each field has its own reader call to get wrong.

    The mutations that reverted `considered` and `open` to the bare
    comprehension survived a suite that only exercised `topics` through
    summarize() and the rest through `_string_list` directly.
    """
    payload = {"response": json.dumps(
        {"gist": "Discussed CRM sync.", "class": "business",
         field: "first option, second option"})}
    out = _summarize_with(monkeypatch, lambda *a, **k: _Reply(payload))
    assert out[key] == ["first option", "second option"], \
        f"{field} was iterated as characters"


def test_a_skip_verdict_survives_the_path(monkeypatch):
    payload = {"response": json.dumps({"skip": True})}
    assert _summarize_with(monkeypatch, lambda *a, **k: _Reply(payload)) == {"skip": True}


# ==========================================================================
# 3 & 4 - the counter that lied, and the skip that cost a prefill a night
# ==========================================================================

@pytest.fixture()
def chronicle_root(tmp_path, monkeypatch):
    root = tmp_path / "chronicle"
    (root / "business").mkdir(parents=True)
    (root / "personal").mkdir(parents=True)
    monkeypatch.setattr(ch, "chronicle_root", lambda: root)
    return root


def test_a_model_skip_is_recorded(chronicle_root):
    ch.record_skipped("abc123")
    assert ch.read_skipped() == {"abc123"}
    assert (chronicle_root / ".skipped-sessions").is_file()


def test_recorded_skips_accumulate(chronicle_root):
    ch.record_skipped("abc123")
    ch.record_skipped("def456")
    assert ch.read_skipped() == {"abc123", "def456"}


def test_no_skip_file_is_an_empty_set(chronicle_root):
    assert ch.read_skipped() == set()


def test_a_recorded_skip_is_never_selected_again(chronicle_root, tmp_path):
    """This is the whole point: no re-selection means no repeat prefill."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for stem in ("aaa", "bbb"):
        (sessions / f"{stem}.jsonl").write_text("{}\n", encoding="utf-8")

    before = {p.stem for p in ch.select_sessions(sessions, None, True, 100)}
    assert before == {"aaa", "bbb"}

    ch.record_skipped("aaa")
    after = {p.stem for p in ch.select_sessions(sessions, None, True, 100)}
    assert after == {"bbb"}, \
        "a session the model already judged empty was queued for another prefill"


def _build(monkeypatch, tmp_path, chronicle_root, summary, dry_run):
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / "aaa.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(ch, "parse_jsonl", lambda p: ([], None))
    monkeypatch.setattr(ch, "build_envelope", lambda p, e: {})
    monkeypatch.setattr(ch, "apply_truncation", lambda env, n: env)
    monkeypatch.setattr(ch, "envelope_body", lambda env: "x" * 5000)
    monkeypatch.setattr(ch, "_session_date", lambda env, p: "2026-08-25")
    monkeypatch.setattr(ch, "summarize", lambda body: summary)

    args = type("A", (), {"sessions_dir": sessions, "since": None, "backfill": True,
                          "limit": 100, "dry_run": dry_run})()
    return ch.cmd_build(args)


def test_a_dry_run_does_not_claim_anything_was_written(
        monkeypatch, tmp_path, chronicle_root, capsys):
    summary = {"gist": "g", "topics": ["t"], "personal": False,
               "reasoning": "", "considered": [], "open": []}
    _build(monkeypatch, tmp_path, chronicle_root, summary, dry_run=True)
    out = capsys.readouterr().out
    done = [ln for ln in out.splitlines() if "done:" in ln][0]
    assert "1 written" not in done, \
        "the summary line said written and nothing-saved in the same breath"
    assert "1 would be written" in done, \
        "the dry run did not report the count it would have written"
    assert "nothing saved" in done


def test_a_real_run_still_counts_what_it_wrote(
        monkeypatch, tmp_path, chronicle_root, capsys):
    summary = {"gist": "g", "topics": ["t"], "personal": False,
               "reasoning": "", "considered": [], "open": []}
    _build(monkeypatch, tmp_path, chronicle_root, summary, dry_run=False)
    done = [ln for ln in capsys.readouterr().out.splitlines() if "done:" in ln][0]
    assert "1 written" in done
    assert "nothing saved" not in done


def test_a_dry_run_records_no_skip(monkeypatch, tmp_path, chronicle_root):
    _build(monkeypatch, tmp_path, chronicle_root, {"skip": True}, dry_run=True)
    assert ch.read_skipped() == set(), "a dry run wrote to the skip ledger"


def test_a_real_run_records_the_skip(monkeypatch, tmp_path, chronicle_root):
    _build(monkeypatch, tmp_path, chronicle_root, {"skip": True}, dry_run=False)
    assert ch.read_skipped() == {"aaa"}


# ==========================================================================
# 5 - the pin instruction that named one path for all of them
# ==========================================================================

def _drift_output(findings):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "classification_health_03p3", ROOT / "scripts" / "classification-health.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["classification_health_03p3"] = mod
    spec.loader.exec_module(mod)

    buf = io.StringIO()
    real_stdout, sys.stdout = sys.stdout, buf
    try:
        mod.print_outputs_drift(findings)
    finally:
        sys.stdout = real_stdout
    return buf.getvalue()


def test_every_drifted_path_appears_in_the_pin_instruction():
    out = _drift_output([
        {"path": "outputs/alpha", "file_count": 6},
        {"path": "outputs/beta", "file_count": 7},
        {"path": "outputs/gamma", "file_count": 9},
    ])
    pin_block = out.split("To pin", 1)[1]
    for name in ("alpha", "beta", "gamma"):
        assert f'"outputs/{name}": private' in pin_block, \
            f"an operator following the instruction would never pin {name}"


def test_a_single_finding_still_reads_naturally():
    out = _drift_output([{"path": "outputs/alpha", "file_count": 6}])
    assert '"outputs/alpha": private' in out
    assert "routing-map.yaml" in out


def test_no_findings_prints_a_pass_and_no_instruction():
    out = _drift_output([])
    assert "[PASS]" in out
    assert "To pin" not in out
