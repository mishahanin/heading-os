"""Unit tests for /conversations source (Phase 1.88)."""
import json
from pathlib import Path

import pytest

from scripts.bridge_daemon.sources.conversations import (
    CONVERSATIONS_ROW_CAP,
    PARTICIPANT_CAP,
    list_conversations,
)

FETCH_REL = "outputs/operations/email-intelligence/_latest-fetch.json"


def _write_fetch(tmp_path, conversations):
    p = tmp_path / FETCH_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"conversations": conversations}), encoding="utf-8")
    return p


def test_empty_when_no_fetch_file(tmp_path):
    r = list_conversations(tmp_path)
    assert r["conversations"] == []
    assert r["total"] == 0
    assert r["data_time"] is None
    # Counts dict is always shaped the same.
    assert r["counts"] == {"by_priority": {}, "by_category": {}, "by_direction": {}}


def test_parses_minimal_conversation(tmp_path):
    _write_fetch(tmp_path, [{
        "id": "c1",
        "topic": "Hello",
        "direction": "inbound",
        "priority": "high",
        "message_count": 3,
        "latest_datetime": "2026-05-18T09:00:00+04:00",
        "participants": [{"name": "Alice", "email": "alice@example.com"}],
        "analysis": {"category": "deal", "summary": "Wants a call about Q3 pricing.", "priority": "high"},
        "crm_context": {"name": "Alice Adams", "company": "Acme"},
    }])
    r = list_conversations(tmp_path)
    assert r["total"] == 1
    c = r["conversations"][0]
    assert c["id"] == "c1"
    assert c["topic"] == "Hello"
    assert c["direction"] == "inbound"
    assert c["priority"] == "high"
    assert c["category"] == "deal"
    assert c["message_count"] == 3
    assert c["participants"] == ["Alice"]
    assert c["participants_extra"] == 0
    assert c["contact_name"] == "Alice Adams"
    assert c["contact_company"] == "Acme"
    assert "Q3 pricing" in c["summary"]


def test_counts_aggregate_priority_category_direction(tmp_path):
    _write_fetch(tmp_path, [
        {"id": "a", "topic": "T", "direction": "inbound", "priority": "urgent",
         "analysis": {"category": "deal"}},
        {"id": "b", "topic": "T", "direction": "inbound", "priority": "high",
         "analysis": {"category": "deal"}},
        {"id": "c", "topic": "T", "direction": "outbound", "priority": "high",
         "analysis": {"category": "intro"}},
    ])
    r = list_conversations(tmp_path)
    assert r["counts"]["by_priority"] == {"urgent": 1, "high": 2}
    assert r["counts"]["by_category"] == {"deal": 2, "intro": 1}
    assert r["counts"]["by_direction"] == {"inbound": 2, "outbound": 1}


def test_participants_capped_with_extra_count(tmp_path):
    parts = [{"name": f"User {i}"} for i in range(PARTICIPANT_CAP + 4)]
    _write_fetch(tmp_path, [{
        "id": "c", "topic": "T", "direction": "inbound",
        "participants": parts,
    }])
    r = list_conversations(tmp_path)
    c = r["conversations"][0]
    assert len(c["participants"]) == PARTICIPANT_CAP
    assert c["participants_extra"] == 4


# ============================================================
# _trim_participants: the ONE shape branch nothing in the tree reaches
# ============================================================
# The body opens with `if not isinstance(parts, list): return [], 0`, and full
# suite branch coverage on 2026-08-31 over 19,835 tests reported:
#
#     scripts/bridge_daemon/sources/conversations.py  97  3  38  2  96%
#         Missing 85, 97->87, 162-163
#
# Line 85 is that guard's `return`. Not one test in the tree executed it, so
# nothing could assert it. `97->87` is the third path out of the loop, where a
# participant is neither a dict nor a string.
#
# MEASURED the same day in a clone under /tmp, deleting the list guard:
#
#     pytest tests/bridge  -> 1312 passed, 1 skipped   (mutation survived)
#
# and, carried into a whole-suite run alongside five other mutations,
#
#     pytest tests -> 12 failed, 19810 passed, 14 skipped (0:35:43)
#
# with none of those twelve attributable to this one. That wider check is
# what separated this finding from the bare-string one below, which the
# same directory-scoped run had also called a gap.
#
# and, on the same clone:
#
#   participants='Alice Adams' -> ['A', 'l', 'i']  extra=8
#   participants=7             -> TypeError: 'int' object is not subscriptable
#   participants={'name': ...} -> TypeError: unhashable type: 'slice'
#
# The first is the one to notice: no exception, no log, three letters rendered
# as three people on the /conversations page. The fetch file is written by a
# separate pipeline and is hand-editable (the premise every other guard in this
# module is written on), so a `participants` that arrives as one name rather
# than a list of one is an ordinary shape for it to take.
#
# What is NOT a gap, recorded because a directory-scoped mutation run said it
# was: deleting `elif isinstance(p, str): trimmed.append(p)` also survived
# `tests/bridge`, and does NOT survive the whole suite. It is caught by
# `tests/test_a_participant_name_that_was_never_a_string.py::test_good_
# participants_still_come_through`, which passes `"q@example.com"` as a bare
# string and asserts it comes back. The bare-string test kept below is the
# anchor for the parametrised refusal directly under it (without one, `return
# [], 0` for every input would satisfy the whole block), not a second home for
# a guard that already has one.

@pytest.mark.parametrize("bad", ["Alice Adams", 7, {"name": "Alice"}, None, 1.5])
def test_a_participants_field_that_is_not_a_list_yields_no_participants(tmp_path, bad):
    """One case per shape. A string is the dangerous one: without the guard it
    is sliced into characters and each character passes the `isinstance(p, str)`
    branch, so the page renders letters as people instead of raising."""
    _write_fetch(tmp_path, [{"id": "c", "topic": "T", "direction": "inbound",
                             "participants": bad}])
    r = list_conversations(tmp_path)
    c = r["conversations"][0]
    assert c["participants"] == [], f"{bad!r} produced {c['participants']!r}"
    assert c["participants_extra"] == 0


def test_a_bare_string_participant_is_kept(tmp_path):
    """The documented alternative shape, and the anchor for the test above.

    Without this, `return [], 0` for every input would satisfy the parametrised
    test entirely, and so would deleting the `isinstance(p, str)` branch that
    supports the shape the docstring promises.
    """
    _write_fetch(tmp_path, [{
        "id": "c", "topic": "T", "direction": "inbound",
        "participants": ["Alice Adams", {"name": "Bob Jones"},
                         {"email": "carol@example.com"}],
    }])
    c = list_conversations(tmp_path)["conversations"][0]
    assert c["participants"] == ["Alice Adams", "Bob Jones",
                                 "carol@example.com"]
    assert c["participants_extra"] == 0


def test_a_participant_that_is_neither_a_dict_nor_a_string_is_dropped(tmp_path):
    """The third branch out of the loop, which coverage reported as never
    taken. A row must not be lost for one bad entry among good ones."""
    _write_fetch(tmp_path, [{
        "id": "c", "topic": "T", "direction": "inbound",
        "participants": ["Alice Adams", 42, ["nested"], None],
    }])
    c = list_conversations(tmp_path)["conversations"][0]
    assert c["participants"] == ["Alice Adams"]
    # PARTICIPANT_CAP is 3, and `extra` counts the RAW overflow, so a
    # four-entry list reports one extra whatever the entries were.
    assert c["participants_extra"] == 1


def test_summary_truncated_at_200_chars(tmp_path):
    long = "x " * 200  # 400 chars
    _write_fetch(tmp_path, [{
        "id": "c", "topic": "T", "direction": "inbound",
        "analysis": {"summary": long},
    }])
    r = list_conversations(tmp_path)
    assert r["conversations"][0]["summary"].endswith("...")
    assert len(r["conversations"][0]["summary"]) <= 203  # 200 + '...'


def test_sort_by_latest_datetime_desc(tmp_path):
    _write_fetch(tmp_path, [
        {"id": "old", "topic": "Old", "latest_datetime": "2026-05-10T00:00:00+04:00"},
        {"id": "new", "topic": "New", "latest_datetime": "2026-05-18T00:00:00+04:00"},
        {"id": "mid", "topic": "Mid", "latest_datetime": "2026-05-14T00:00:00+04:00"},
    ])
    r = list_conversations(tmp_path)
    topics = [c["topic"] for c in r["conversations"]]
    assert topics == ["New", "Mid", "Old"]


def test_no_timestamp_entries_sort_last(tmp_path):
    _write_fetch(tmp_path, [
        {"id": "with", "topic": "Has TS", "latest_datetime": "2026-05-10T00:00:00+04:00"},
        {"id": "no",   "topic": "No TS",  "latest_datetime": ""},
    ])
    r = list_conversations(tmp_path)
    topics = [c["topic"] for c in r["conversations"]]
    assert topics == ["Has TS", "No TS"]


def test_malformed_json_returns_empty(tmp_path):
    p = tmp_path / FETCH_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json", encoding="utf-8")
    r = list_conversations(tmp_path)
    assert r["total"] == 0
    assert r["conversations"] == []


def test_non_list_conversations_returns_empty(tmp_path):
    p = tmp_path / FETCH_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"conversations": "oops"}), encoding="utf-8")
    r = list_conversations(tmp_path)
    assert r["total"] == 0


def test_data_time_set_to_file_mtime(tmp_path):
    _write_fetch(tmp_path, [{"id": "c", "topic": "T"}])
    r = list_conversations(tmp_path)
    assert r["data_time"] is not None
    assert r["data_time"].startswith("20")  # ISO 8601 starts with year