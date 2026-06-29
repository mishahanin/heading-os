"""Unit tests for /conversations source (Phase 1.88)."""
import json
from pathlib import Path

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