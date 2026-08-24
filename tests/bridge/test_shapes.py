"""The three shape guards, and the incidents behind each one.

`scripts/bridge_daemon/_shapes.py` exists because each of these was fixed in
exactly one module and left broken in the eight siblings with the same read.
These tests pin the guards themselves; the per-module tests pin that each
reader actually calls them.
"""
from __future__ import annotations

import pytest

from scripts.bridge_daemon._shapes import as_mapping, entry_ts, is_undo


# ============================================================
# as_mapping: valid JSON of the wrong shape
# ============================================================

@pytest.mark.parametrize("value", [[], ["a"], "junk", 42, 0.5, None, True, ()])
def test_a_non_mapping_becomes_an_empty_mapping(value):
    assert as_mapping(value) == {}


def test_a_mapping_passes_through_unchanged():
    payload = {"conversations": [1, 2]}
    assert as_mapping(payload) is payload


def test_the_guard_makes_dot_get_safe():
    """The actual failure: `.get` on a list is an AttributeError, and no
    `except (json.JSONDecodeError, OSError)` around the read catches it."""
    with pytest.raises(AttributeError):
        [].get("conversations")            # what the readers used to do
    assert as_mapping([]).get("conversations", []) == []


# ============================================================
# entry_ts: one null timestamp poisons the whole sort
# ============================================================

@pytest.mark.parametrize("bad", [None, 17, 1.5, [], {}, True])
def test_a_non_string_timestamp_becomes_an_empty_string(bad):
    assert entry_ts({"ts": bad}) == ""


def test_a_string_timestamp_is_returned_verbatim():
    assert entry_ts({"ts": "2026-01-01T00:00:00+00:00"}) == "2026-01-01T00:00:00+00:00"


def test_a_missing_timestamp_is_an_empty_string():
    assert entry_ts({}) == ""


def test_an_alternate_key_is_supported():
    assert entry_ts({"created_at": "x"}, key="created_at") == "x"


def test_one_null_row_no_longer_takes_the_sort_down():
    rows = [{"ts": "2026-01-02T00:00:00Z"}, {"ts": None},
            {"ts": "2026-01-01T00:00:00Z"}]
    with pytest.raises(TypeError):
        sorted(rows, key=lambda r: r.get("ts", ""), reverse=True)
    ordered = sorted(rows, key=entry_ts, reverse=True)
    assert [entry_ts(r) for r in ordered] == [
        "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z", ""]


# ============================================================
# is_undo: a tombstone that resurrected the item it buried
# ============================================================

@pytest.mark.parametrize("value", [True, 1, "yes", ["x"], {"a": 1}, 0.5])
def test_any_truthy_undo_is_a_tombstone(value):
    assert is_undo({"undo": value}) is True


@pytest.mark.parametrize("value", [False, 0, "", None, [], {}])
def test_any_falsy_undo_is_not_a_tombstone(value):
    assert is_undo({"undo": value}) is False


def test_a_missing_undo_is_not_a_tombstone():
    assert is_undo({"conv_id": "a"}) is False


def test_the_hand_edited_one_used_to_slip_through():
    entry = {"undo": 1}
    assert (entry.get("undo") is True) is False, "the old test"
    assert is_undo(entry) is True, "the new one"
