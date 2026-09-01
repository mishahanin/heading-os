"""Unit tests for the /inbox real-data source.

What is actually covered here: the priority bands, the dismiss workflow
(Phase 1.62 / 1.92), the defer workflow, conversation drill-down
(Phase 1.34 / 1.100), and the crm-logged mark (Phase 1.33).

This said "bands, defer, unread mirror" until 2026-08-30. "Unread mirror"
appeared nowhere else in the file - not in a test, a helper, a comment or a
section header - while three of the five areas above went unmentioned. A
contents line that names a section the file does not have sends a reader
looking for coverage that was never written.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.utils.workspace import get_default_tz

from scripts.bridge_daemon.sources.inbox import (
    read_inbox,
    read_conversation,
    mark_dismissed,
    undo_dismissed,
    read_dismiss_log,
    MAX_RAW_EMAILS_RETURNED,
    PROPOSED_ACTIONS_CAP,
    mark_deferred,
    undo_deferred,
    read_defer_log,
    defer_log_recent,
    read_crm_logged,
    mark_crm_logged,
)


def _future(days=3):
    """An ISO date `days` in the future - a valid defer target."""
    return (datetime.now(get_default_tz()).date() + timedelta(days=days)).isoformat()


def _write_state(workspace_root, conversations, last_run="2026-05-18T10:00:00+00:00"):
    state_dir = workspace_root / "outputs" / "operations" / "email-intelligence"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps({"conversations": conversations, "last_run": last_run}),
        encoding="utf-8",
    )


def _write_fetch(workspace_root, conversations, timestamp="2026-05-20T10:00:00+00:00"):
    """Write a _latest-fetch.json with the analyzed-conversation schema
    that read_inbox (Phase 1.32) bands by priority."""
    fetch_dir = workspace_root / "outputs" / "operations" / "email-intelligence"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    (fetch_dir / "_latest-fetch.json").write_text(
        json.dumps({"run_info": {"timestamp": timestamp}, "conversations": conversations}),
        encoding="utf-8",
    )


def _conv(conv_id, priority="P2", topic=None, latest="2026-05-20T09:00:00+00:00", **extra):
    """Build a minimal analyzed conversation for _latest-fetch.json."""
    conv = {
        "id": conv_id,
        "topic": topic or f"Thread {conv_id}",
        "priority": priority,
        "latest_datetime": latest,
        "analysis": {
            "priority": priority,
            "category": "task",
            "summary": f"summary of {conv_id}",
            "proposed_actions": ["do x", "do y"],
        },
    }
    conv.update(extra)
    return conv


def test_read_inbox_empty_when_no_fetch(tmp_path):
    """No _latest-fetch.json -> empty bands, zero counts, None data_time."""
    result = read_inbox(tmp_path)
    assert result["bands"] == {"needs-you": [], "fyi": [], "noise": []}
    assert result["counts"] == {"needs-you": 0, "fyi": 0, "noise": 0}
    assert result["data_time"] is None


def test_read_inbox_bands_by_priority(tmp_path):
    """P1/P2 -> needs-you, P3 -> fyi, P4 -> noise."""
    _write_fetch(tmp_path, [
        _conv("p1", "P1"), _conv("p2", "P2"),
        _conv("p3", "P3"), _conv("p4", "P4"),
    ])
    result = read_inbox(tmp_path)
    assert {r["id"] for r in result["bands"]["needs-you"]} == {"p1", "p2"}
    assert [r["id"] for r in result["bands"]["fyi"]] == ["p3"]
    assert [r["id"] for r in result["bands"]["noise"]] == ["p4"]
    assert result["counts"] == {"needs-you": 2, "fyi": 1, "noise": 1}
    assert result["data_time"] == "2026-05-20T10:00:00+00:00"


def test_read_inbox_unknown_priority_defaults_to_fyi(tmp_path):
    """Missing or garbage priority falls back to P3/fyi - never crashes."""
    _write_fetch(tmp_path, [
        _conv("none", priority=None),
        _conv("garbage", priority="P9"),
    ])
    result = read_inbox(tmp_path)
    assert {r["id"] for r in result["bands"]["fyi"]} == {"none", "garbage"}
    assert all(r["priority"] == "P3" for r in result["bands"]["fyi"])


def test_read_inbox_row_carries_analysis(tmp_path):
    """A banded row carries summary, recommended actions, source, sender."""
    _write_fetch(tmp_path, [
        _conv("c1", "P1", participants=[
            {"name": "Ada Lovelace", "email": "ada@x.com", "role": "sender"},
            {"name": "Misha", "email": "ceo@31c.io", "role": "recipient"},
        ]),
    ])
    row = read_inbox(tmp_path)["bands"]["needs-you"][0]
    assert row["source"] == "email"
    assert row["summary"] == "summary of c1"
    assert row["proposed_actions"] == ["do x", "do y"]
    assert row["sender"] == "Ada Lovelace"
    assert row["band"] == "needs-you"


def test_read_inbox_caps_proposed_actions(tmp_path):
    """No more than PROPOSED_ACTIONS_CAP recommended actions per card."""
    many = [f"action {i}" for i in range(20)]
    _write_fetch(tmp_path, [
        _conv("c1", "P1", analysis={"priority": "P1", "proposed_actions": many}),
    ])
    row = read_inbox(tmp_path)["bands"]["needs-you"][0]
    assert len(row["proposed_actions"]) == PROPOSED_ACTIONS_CAP


def test_read_inbox_sorts_band_recent_first(tmp_path):
    """Within a band, rows sort most-recent-first by latest_datetime."""
    _write_fetch(tmp_path, [
        _conv("old", "P2", latest="2026-01-01T00:00:00+00:00"),
        _conv("new", "P2", latest="2026-05-20T00:00:00+00:00"),
        _conv("mid", "P2", latest="2026-03-01T00:00:00+00:00"),
    ])
    ids = [r["id"] for r in read_inbox(tmp_path)["bands"]["needs-you"]]
    assert ids == ["new", "mid", "old"]


def test_read_inbox_corrupt_fetch_returns_empty(tmp_path):
    """Malformed _latest-fetch.json -> empty bands, no exception."""
    fetch_dir = tmp_path / "outputs" / "operations" / "email-intelligence"
    fetch_dir.mkdir(parents=True)
    (fetch_dir / "_latest-fetch.json").write_text("not-json{", encoding="utf-8")
    result = read_inbox(tmp_path)
    assert result["counts"] == {"needs-you": 0, "fyi": 0, "noise": 0}


# ============================================================
# The fetch file is hand-editable, so its SHAPE is untrusted
# ============================================================
# `_latest-fetch.json` is written by `scripts/email-intelligence.py` and edited
# by hand often enough that `read_inbox`, `_inbox_row` and `read_conversation`
# each carry a shape guard, every one of them added after a 500. Until
# 2026-08-31 not one of those guards had a negative case: every fixture in this
# file goes through `_write_fetch`, which always hands them a list of dicts.
# MEASURED the same day by deleting the container guard
# (`sources/inbox.py:490`, `if not isinstance(conversations, list)`) and running
# the whole directory: 1349 passed, 1 skipped. Nothing in `tests/bridge/` moved.
# A guard nothing ever made refuse is not a guard.

@pytest.mark.parametrize("conversations", [None, {"c1": {}}, "a string", 7])
def test_a_fetch_whose_conversations_are_not_a_list_degrades(tmp_path, conversations):
    """Valid JSON of the wrong shape is the case `json.JSONDecodeError` cannot
    see, and iterating it would be `TypeError` at best and a per-character walk
    at worst."""
    _write_fetch(tmp_path, conversations)

    result = read_inbox(tmp_path)

    assert result["bands"] == {"needs-you": [], "fyi": [], "noise": []}
    assert result["counts"] == {"needs-you": 0, "fyi": 0, "noise": 0}
    # Every documented key is still present: a consumer indexing the degraded
    # payload must not hit a KeyError either.
    for key in ("dismissed_count", "dismiss_log_count", "deferred_count",
                "defer_log_count", "data_time"):
        assert key in result


@pytest.mark.parametrize("conversations", [None, {"c1": {}}, "a string", 7])
def test_the_drill_down_refuses_the_same_shape(tmp_path, conversations):
    """`read_conversation` reads the same list and needs the same answer."""
    _write_fetch(tmp_path, conversations)

    r = read_conversation(tmp_path, "c1")

    assert r["ok"] is False
    assert "schema" in r["error"]


def test_a_non_mapping_run_info_does_not_take_the_page_down(tmp_path):
    """`run_info` is read for `data_time`. `or {}` only replaces a FALSY value,
    so a string ran straight into `.get` and raised AttributeError, which no
    `except (json.JSONDecodeError, OSError)` catches."""
    fetch_dir = tmp_path / "outputs" / "operations" / "email-intelligence"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    (fetch_dir / "_latest-fetch.json").write_text(
        json.dumps({"run_info": "2026-05-20T10:00:00+00:00",
                    "conversations": [_conv("c1", "P1")]}),
        encoding="utf-8")

    result = read_inbox(tmp_path)

    assert [r["id"] for r in result["bands"]["needs-you"]] == ["c1"]
    assert result["data_time"] is None


@pytest.mark.parametrize("analysis", ["oops", ["a", "b"], 3])
def test_a_conversation_whose_analysis_is_not_a_dict_still_bands(tmp_path, analysis):
    """One malformed conversation must not take every band down with it."""
    _write_fetch(tmp_path, [_conv("good", "P1"),
                            {"id": "bad", "priority": "P1", "analysis": analysis}])

    result = read_inbox(tmp_path)

    rows = {r["id"]: r for r in result["bands"]["needs-you"]}
    assert set(rows) == {"good", "bad"}
    assert rows["bad"]["summary"] == ""
    assert rows["bad"]["proposed_actions"] == []


def test_read_inbox_skips_non_dict_and_idless(tmp_path):
    """Conversation entries that aren't dicts, or lack an id, are skipped
    cleanly - no AttributeError, no crash."""
    _write_fetch(tmp_path, [
        _conv("good", "P1"),
        ["not", "a", "dict"],
        "garbage",
        None,
        {"topic": "no id here", "priority": "P1"},
    ])
    result = read_inbox(tmp_path)
    assert [r["id"] for r in result["bands"]["needs-you"]] == ["good"]


# ============================================================
# Phase 1.62: dismiss workflow
# ============================================================
def test_mark_dismissed_writes_log(tmp_path):
    r = mark_dismissed(tmp_path, "conv-abc", note="duplicate of c2")
    assert r["ok"] is True
    assert "conv-abc" in read_dismiss_log(tmp_path)


def test_mark_dismissed_rejects_empty(tmp_path):
    assert mark_dismissed(tmp_path, "")["ok"] is False
    assert mark_dismissed(tmp_path, "   ")["ok"] is False
    assert mark_dismissed(tmp_path, "x" * 600)["ok"] is False


def test_a_dismiss_log_line_with_no_usable_id_is_skipped(tmp_path):
    """The dismiss log is append-only JSONL on disk, so a hand-edited or
    partially-restored line reaches `read_dismiss_log` as valid JSON of the
    wrong shape.

    MEASURED 2026-08-31 by deleting the `isinstance(conv_id, str)` guard at
    `sources/inbox.py:62` and running `tests/bridge`: 1349 passed, 1 skipped.
    Nothing fed it a bad id, so an entry keyed `null` or `7` would have entered
    the dismissed SET and been compared against real conversation ids.
    """
    from scripts.bridge_daemon.sources.inbox import DISMISS_LOG_FILE
    log = tmp_path / DISMISS_LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"conv_id": "real-one", "ts": "2026-08-31T10:00:00+00:00"}) + "\n"
        + json.dumps({"conv_id": None, "ts": "x"}) + "\n"
        + json.dumps({"conv_id": 7, "ts": "x"}) + "\n"
        + json.dumps({"conv_id": "", "ts": "x"}) + "\n"
        + json.dumps({"ts": "x"}) + "\n",
        encoding="utf-8")

    assert read_dismiss_log(tmp_path) == {"real-one"}

    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent
    assert [r["conv_id"] for r in dismiss_log_recent(tmp_path)] == ["real-one"]


def test_undo_dismissed_rejects_what_mark_dismissed_rejects(tmp_path):
    """The two halves of one workflow have to agree on what an id is.

    `mark_dismissed` has had this negative case since it was written;
    `undo_dismissed` never did. MEASURED 2026-08-31 by deleting its guard
    (`sources/inbox.py:156`): the whole directory stayed green, so an empty
    conv_id would have been written as a tombstone line nothing can ever match.
    """
    assert undo_dismissed(tmp_path, "")["ok"] is False
    assert undo_dismissed(tmp_path, "   ")["ok"] is False
    assert undo_dismissed(tmp_path, None)["ok"] is False  # type: ignore[arg-type]
    from scripts.bridge_daemon.sources.inbox import DISMISS_LOG_FILE
    assert not (tmp_path / DISMISS_LOG_FILE).exists(), "a refused undo must write nothing"


def test_mark_deferred_rejects_an_oversized_id(tmp_path):
    """The same 500-character bound `mark_dismissed` enforces, on the defer
    half, where nothing had ever exercised it (measured 2026-08-31:
    `sources/inbox.py:263` deleted, directory still green)."""
    assert mark_deferred(tmp_path, "x" * 501, _future())["ok"] is False
    assert mark_deferred(tmp_path, "x" * 500, _future())["ok"] is True


def test_undo_dismissed_cancels(tmp_path):
    mark_dismissed(tmp_path, "conv-abc")
    assert "conv-abc" in read_dismiss_log(tmp_path)
    r = undo_dismissed(tmp_path, "conv-abc")
    assert r["ok"] is True
    assert "conv-abc" not in read_dismiss_log(tmp_path)


def test_read_inbox_filters_dismissed(tmp_path):
    """Dismissed conversation IDs are excluded from bands + counted separately."""
    _write_fetch(tmp_path, [_conv("c1", "P1"), _conv("c2", "P1")])
    mark_dismissed(tmp_path, "c2")
    result = read_inbox(tmp_path)
    assert {r["id"] for r in result["bands"]["needs-you"]} == {"c1"}
    assert result["dismissed_count"] == 1


def test_dismiss_undo_cycle_restores_visibility(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", "P1")])
    mark_dismissed(tmp_path, "c1")
    assert read_inbox(tmp_path)["bands"]["needs-you"] == []
    undo_dismissed(tmp_path, "c1")
    assert len(read_inbox(tmp_path)["bands"]["needs-you"]) == 1


# ============================================================
# Phase 1.34: read_conversation (drill-down)
# ============================================================
def _write_latest_fetch(workspace_root, conversations):
    fetch_dir = workspace_root / "outputs" / "operations" / "email-intelligence"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    (fetch_dir / "_latest-fetch.json").write_text(
        json.dumps({"run_info": {}, "conversations": conversations}),
        encoding="utf-8",
    )


def test_read_conversation_no_fetch_file(tmp_path):
    """Missing _latest-fetch.json -> graceful error."""
    r = read_conversation(tmp_path, "anything")
    assert r["ok"] is False
    assert "fetch" in r["error"]


def test_read_conversation_missing_id(tmp_path):
    """Empty / None id -> error before touching disk."""
    assert read_conversation(tmp_path, "")["ok"] is False
    assert read_conversation(tmp_path, None)["ok"] is False  # type: ignore[arg-type]


def test_read_conversation_not_in_fetch_window(tmp_path):
    """ID not in the latest fetch -> older-than-window error."""
    _write_latest_fetch(tmp_path, [
        {"id": "abc", "topic": "Recent thread"},
    ])
    r = read_conversation(tmp_path, "old-id")
    assert r["ok"] is False
    assert "older than last fetch" in r["error"]


def test_read_conversation_happy_path(tmp_path):
    """Matching id -> rich conversation payload."""
    _write_latest_fetch(tmp_path, [
        {
            "id": "abc",
            "topic": "Test thread",
            "direction": "bidirectional",
            "priority": "P1",
            "message_count": 3,
            "latest_datetime": "2026-05-18T10:00:00+00:00",
            "participants": [
                {"name": "Alice", "email": "a@x.com", "role": "sender"},
                {"name": "Bob", "email": "b@x.com", "role": "recipient"},
            ],
            "is_internal": False,
            "crm_context": {
                "contact_slug": "alice",
                "name": "Alice",
                "company": "X Co",
                "last_touch": "2026-05-10",
                "days_since": 8,
                "cadence": "7",
            },
            "pipeline_context": {
                "company": "X Co",
                "stage": "Negotiation",
                "est_value": "$1M",
            },
            "analysis": {
                "category": "deal",
                "summary": "Big deal incoming",
                "proposed_actions": ["Send NDA", "Schedule demo"],
                "commitments": ["Misha to send NDA by Friday"],
                "relationship_signal": "warm",
            },
            "raw_emails": [
                {"from": "a@x.com", "to": ["b@x.com"], "subject": "RE: Demo", "body": "Hi Bob"},
            ],
        },
    ])
    r = read_conversation(tmp_path, "abc")
    assert r["ok"] is True
    c = r["conversation"]
    assert c["topic"] == "Test thread"
    assert c["priority"] == "P1"
    assert c["analysis"]["summary"] == "Big deal incoming"
    assert len(c["analysis"]["proposed_actions"]) == 2
    assert len(c["analysis"]["commitments"]) == 1
    assert c["crm_context"]["name"] == "Alice"
    assert c["pipeline_context"]["stage"] == "Negotiation"
    assert len(c["participants"]) == 2
    assert c["raw_emails_truncated"] is False


def test_read_conversation_truncates_long_body(tmp_path):
    """Bodies over RAW_EMAIL_SNIPPET_BYTES are clipped."""
    long_body = "A" * 3000
    _write_latest_fetch(tmp_path, [
        {
            "id": "abc",
            "topic": "Long",
            "raw_emails": [
                {"from": "a@x.com", "subject": "S", "body": long_body},
            ],
        },
    ])
    r = read_conversation(tmp_path, "abc")
    assert r["ok"] is True
    body = r["conversation"]["raw_emails"][0]["body"]
    assert len(body) < 3000
    assert body.endswith("...")


def test_read_conversation_caps_raw_email_count(tmp_path):
    """More than MAX_RAW_EMAILS_RETURNED -> truncated flag + capped list.

    The cap is imported, not retyped. `== 5` was the literal here until
    2026-08-30, which turns any change to the constant into a red test on
    correct behaviour - and the sibling
    `test_read_conversation_does_not_flag_a_chain_at_the_cap` below has no way
    to be right about the boundary if the two spell it differently. The same
    file already imports PROPOSED_ACTIONS_CAP for the identical shape.
    """
    _write_latest_fetch(tmp_path, [
        {
            "id": "abc",
            "topic": "Many messages",
            "raw_emails": [{"from": f"a{i}@x.com"}
                           for i in range(MAX_RAW_EMAILS_RETURNED * 2)],
        },
    ])
    r = read_conversation(tmp_path, "abc")
    assert r["ok"] is True
    assert r["conversation"]["raw_emails_truncated"] is True
    assert len(r["conversation"]["raw_emails"]) == MAX_RAW_EMAILS_RETURNED


def test_read_conversation_does_not_flag_a_chain_at_the_cap(tmp_path):
    """The case ON the line: exactly the cap is not truncation.

    The test above only ever fed twice the cap, so nothing distinguished
    "truncate above N" from "truncate at or above N", or from "always set the
    flag". A chain of exactly MAX_RAW_EMAILS_RETURNED is complete.
    """
    _write_latest_fetch(tmp_path, [
        {
            "id": "exact",
            "topic": "Exactly the cap",
            "raw_emails": [{"from": f"a{i}@x.com"}
                           for i in range(MAX_RAW_EMAILS_RETURNED)],
        },
    ])
    r = read_conversation(tmp_path, "exact")
    assert r["ok"] is True
    assert r["conversation"]["raw_emails_truncated"] is False
    assert len(r["conversation"]["raw_emails"]) == MAX_RAW_EMAILS_RETURNED


def test_read_conversation_corrupt_json(tmp_path):
    """Unreadable JSON -> graceful error, no crash."""
    fetch_dir = tmp_path / "outputs" / "operations" / "email-intelligence"
    fetch_dir.mkdir(parents=True)
    (fetch_dir / "_latest-fetch.json").write_text("{not valid json", encoding="utf-8")
    r = read_conversation(tmp_path, "abc")
    assert r["ok"] is False
    assert "unreadable" in r["error"]


# ============================================================
# Phase 1.92: dismiss_log_recent + dismiss_log_count
# ============================================================
def test_dismiss_log_recent_empty_when_no_log(tmp_path):
    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent
    assert dismiss_log_recent(tmp_path) == []


def test_dismiss_log_recent_returns_active_entries(tmp_path):
    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent
    mark_dismissed(tmp_path, "conv-a", note="not relevant")
    mark_dismissed(tmp_path, "conv-b")
    rows = dismiss_log_recent(tmp_path)
    ids = {r["conv_id"] for r in rows}
    assert ids == {"conv-a", "conv-b"}
    a = next(r for r in rows if r["conv_id"] == "conv-a")
    assert a["note"] == "not relevant"
    assert a["ts"]
    assert a["date"]


def test_dismiss_log_recent_uses_topic_from_fetch_when_present(tmp_path):
    """If the conversation still exists in _latest-fetch, surface its topic."""
    import json
    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent
    fetch = tmp_path / "outputs" / "operations" / "email-intelligence" / "_latest-fetch.json"
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(json.dumps({"conversations": [
        {"id": "c1", "topic": "Mashreq KYC question"},
    ]}), encoding="utf-8")
    mark_dismissed(tmp_path, "c1")
    rows = dismiss_log_recent(tmp_path)
    assert rows[0]["topic"] == "Mashreq KYC question"


def test_dismiss_log_recent_falls_back_to_conv_id(tmp_path):
    """When no fetch file is available, topic falls back to conv_id."""
    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent
    mark_dismissed(tmp_path, "isolated-id")
    rows = dismiss_log_recent(tmp_path)
    assert rows[0]["topic"] == "isolated-id"


def test_dismiss_log_recent_excludes_tombstones(tmp_path):
    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent
    mark_dismissed(tmp_path, "conv-x")
    undo_dismissed(tmp_path, "conv-x")
    rows = dismiss_log_recent(tmp_path)
    assert all(r["conv_id"] != "conv-x" for r in rows)


def test_dismiss_log_recent_orders_ts_desc(tmp_path, monkeypatch):
    """Newest dismissal first.

    The clock is driven, not slept through. `mark_dismissed` stamps `ts` from
    `datetime.now`, and a 10 ms sleep only separates the two entries while the
    wall clock moves forward; a WSL2 host resync can step it backwards and
    invert them. Sibling flake to `test_sorted_by_mtime_desc`, same cause,
    same cure: state the two instants instead of hoping for them.
    """
    from scripts.bridge_daemon.sources import inbox as inbox_mod
    from scripts.bridge_daemon.sources.inbox import dismiss_log_recent

    # A holder, not an iterator: `mark_dismissed` reads the clock twice per
    # call (UTC for `ts`, local for `date`), so a per-call sequence would
    # desynchronise the moment a field is added or removed.
    moment = [datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)]

    class _HeldClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment[0] if tz in (None, timezone.utc) else moment[0].astimezone(tz)

    monkeypatch.setattr(inbox_mod, "datetime", _HeldClock)

    mark_dismissed(tmp_path, "first")
    moment[0] += timedelta(seconds=1)
    mark_dismissed(tmp_path, "second")
    rows = dismiss_log_recent(tmp_path)
    assert rows[0]["conv_id"] == "second"
    assert rows[1]["conv_id"] == "first"


def test_read_conversation_falls_back_to_state_when_not_in_fetch(tmp_path):
    """Phase 1.100: when a conv_id is in state.json but not in
    _latest-fetch.json, return a degraded payload instead of erroring."""
    # Latest-fetch has 'modern' but the user clicks on 'older' which is
    # still in state.json's rolling window.
    _write_latest_fetch(tmp_path, [
        {"id": "modern", "topic": "Modern thread", "raw_emails": []},
    ])
    _write_state(tmp_path, {
        "older": {"topic": "Older thread (limited info)", "last_seen": "2026-04-22T10:00:00+00:00"},
        "modern": {"topic": "Modern thread", "last_seen": "2026-05-18T10:00:00+00:00"},
    })
    r = read_conversation(tmp_path, "older")
    assert r["ok"] is True
    assert r["conversation"]["topic"] == "Older thread (limited info)"
    assert r["conversation"]["degraded"] is True
    assert "older than" in r["conversation"]["degraded_reason"].lower()
    # Analysis fields are present but empty - frontend can render them safely.
    assert r["conversation"]["analysis"]["summary"] == ""
    assert r["conversation"]["analysis"]["proposed_actions"] == []
    # The fetch-present case stays rich (no degraded flag).
    r2 = read_conversation(tmp_path, "modern")
    assert r2["ok"] is True
    assert r2["conversation"].get("degraded") is not True


def test_read_conversation_fallback_when_no_fetch_file(tmp_path):
    """Even when _latest-fetch.json doesn't exist, the state.json
    fallback should still surface basic info instead of failing."""
    _write_state(tmp_path, {
        "c-only-state": {"topic": "Only in state", "last_seen": "2026-05-18T08:00:00+00:00"},
    })
    r = read_conversation(tmp_path, "c-only-state")
    assert r["ok"] is True
    assert r["conversation"]["degraded"] is True
    assert r["conversation"]["topic"] == "Only in state"


def test_read_conversation_still_errors_when_truly_missing(tmp_path):
    """If the conv_id isn't in fetch OR state, return the original error."""
    _write_latest_fetch(tmp_path, [
        {"id": "present", "topic": "Present", "raw_emails": []},
    ])
    _write_state(tmp_path, {
        "present": {"topic": "Present", "last_seen": "2026-05-18T08:00:00+00:00"},
    })
    r = read_conversation(tmp_path, "ghost-conv")
    assert r["ok"] is False
    assert "older" in r["error"].lower()


def test_read_inbox_surfaces_dismiss_log_count(tmp_path):
    """dismiss_log_count is the total active dismiss set; dismissed_count
    is only those filtered out of the current fetch."""
    _write_fetch(tmp_path, [_conv("c1", "P1")])
    mark_dismissed(tmp_path, "c1")
    mark_dismissed(tmp_path, "long-gone")  # never in the fetch
    r = read_inbox(tmp_path)
    assert r["dismiss_log_count"] == 2
    # dismissed_count only counts conversations actually filtered out of
    # the current fetch (the older 'long-gone' was never in it).
    assert r["dismissed_count"] == 1


# ============================================================
# Phase 1.33: defer workflow
# ============================================================
def test_mark_deferred_accepts_future_date(tmp_path):
    r = mark_deferred(tmp_path, "c1", _future(3), note="busy week")
    assert r["ok"] is True
    assert "c1" in read_defer_log(tmp_path)


def test_mark_deferred_rejects_past_and_garbage(tmp_path):
    past = (datetime.now(get_default_tz()).date() - timedelta(days=1)).isoformat()
    assert mark_deferred(tmp_path, "c1", past)["ok"] is False
    assert mark_deferred(tmp_path, "c1", datetime.now(get_default_tz()).date().isoformat())["ok"] is False
    assert mark_deferred(tmp_path, "c1", "not-a-date")["ok"] is False
    assert mark_deferred(tmp_path, "", _future())["ok"] is False


def test_read_defer_log_excludes_expired(tmp_path):
    """A defer whose date has arrived is no longer reported as deferred."""
    mark_deferred(tmp_path, "future", _future(5))
    assert "future" in read_defer_log(tmp_path)
    far = datetime.now(get_default_tz()).date() + timedelta(days=10)
    assert "future" not in read_defer_log(tmp_path, today=far)


def test_undo_deferred_cancels(tmp_path):
    mark_deferred(tmp_path, "c1", _future())
    assert "c1" in read_defer_log(tmp_path)
    assert undo_deferred(tmp_path, "c1")["ok"] is True
    assert "c1" not in read_defer_log(tmp_path)


def test_read_inbox_filters_deferred(tmp_path):
    """A deferred conversation drops out of the bands and is counted."""
    _write_fetch(tmp_path, [_conv("c1", "P1"), _conv("c2", "P1")])
    mark_deferred(tmp_path, "c2", _future())
    result = read_inbox(tmp_path)
    assert {r["id"] for r in result["bands"]["needs-you"]} == {"c1"}
    assert result["deferred_count"] == 1
    assert result["defer_log_count"] == 1


def test_read_inbox_deferred_resurfaces_after_date(tmp_path):
    """Once the defer date passes, the conversation reappears in its band."""
    _write_fetch(tmp_path, [_conv("c1", "P1")])
    mark_deferred(tmp_path, "c1", _future(2))
    assert read_inbox(tmp_path)["bands"]["needs-you"] == []
    far = datetime.now(timezone.utc) + timedelta(days=5)
    assert len(read_inbox(tmp_path, now=far)["bands"]["needs-you"]) == 1


def test_read_inbox_flags_aging(tmp_path):
    """A conversation unread more than 24h is flagged aging; a fresh one is not."""
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    _write_fetch(tmp_path, [
        _conv("fresh", "P1", latest="2026-05-20T06:00:00+00:00"),   # 6h old
        _conv("old", "P1", latest="2026-05-17T06:00:00+00:00"),     # 3 days old
    ])
    rows = {r["id"]: r for r in read_inbox(tmp_path, now=now)["bands"]["needs-you"]}
    assert rows["fresh"]["aging"] is False
    assert rows["old"]["aging"] is True


def test_defer_log_recent_lists_active(tmp_path):
    mark_deferred(tmp_path, "c1", _future(3), note="later")
    rows = defer_log_recent(tmp_path)
    assert len(rows) == 1
    assert rows[0]["conv_id"] == "c1"
    assert rows[0]["defer_until"] == _future(3)
    assert rows[0]["note"] == "later"


def test_defer_log_recent_drops_a_defer_whose_date_has_arrived(tmp_path):
    """The footer lists what is STILL deferred, not what was ever deferred.

    `read_defer_log` has this case (`test_read_defer_log_excludes_expired`);
    the footer's own copy of the predicate did not, so an expired defer would
    have sat in the 'Deferred' list beside conversations that really are
    hidden. MEASURED 2026-08-31 by deleting the `until is None or until <=
    today` skip at `sources/inbox.py:310`: the whole directory stayed green.
    """
    mark_deferred(tmp_path, "expiring", _future(2), note="short hold")
    mark_deferred(tmp_path, "still-held", _future(9))

    far = datetime.now(get_default_tz()).date() + timedelta(days=5)
    rows = defer_log_recent(tmp_path, today=far)

    assert [r["conv_id"] for r in rows] == ["still-held"]


def test_undo_deferred_rejects_what_mark_deferred_rejects(tmp_path):
    """The defer half of the same writer/undo symmetry as the dismiss log."""
    from scripts.bridge_daemon.sources.inbox import DEFER_LOG_FILE
    assert undo_deferred(tmp_path, "")["ok"] is False
    assert undo_deferred(tmp_path, "   ")["ok"] is False
    assert undo_deferred(tmp_path, None)["ok"] is False  # type: ignore[arg-type]
    assert not (tmp_path / DEFER_LOG_FILE).exists(), "a refused undo must write nothing"


def test_a_state_file_whose_conversations_are_not_a_dict_degrades(tmp_path):
    """The drill-down's state.json fallback reads a MAPPING, and state.json is
    hand-editable too. A list there would have reached `.get` on a list.

    MEASURED 2026-08-31 by deleting the `isinstance(convs, dict)` guard at
    `sources/inbox.py:573`: `tests/bridge` stayed green, so nothing had ever
    handed the fallback anything but a dict.
    """
    state_dir = tmp_path / "outputs" / "operations" / "email-intelligence"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps({"conversations": [{"id": "c1"}], "last_run": "2026-05-18T10:00:00+00:00"}),
        encoding="utf-8")

    r = read_conversation(tmp_path, "c1")

    assert r["ok"] is False
    assert "fetch" in r["error"]


# ============================================================
# Phase 1.33: crm-logged flag
# ============================================================
def test_crm_logged_round_trip(tmp_path):
    assert read_crm_logged(tmp_path) == set()
    ok, _err = mark_crm_logged(tmp_path, "c1", "alex-rivera")
    assert ok is True
    assert "c1" in read_crm_logged(tmp_path)


def test_read_inbox_flags_crm_logged(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", "P1"), _conv("c2", "P1")])
    mark_crm_logged(tmp_path, "c1", "someone")
    rows = {r["id"]: r for r in read_inbox(tmp_path)["bands"]["needs-you"]}
    assert rows["c1"]["crm_logged"] is True
    assert rows["c2"]["crm_logged"] is False
