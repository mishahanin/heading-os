"""``conversations._trim_participants`` trusted the contents of a dict.

The module exists to defend a hand-editable fetch file: ``_as_text``,
``_as_count`` and ``_as_mapping`` all sit there because a field the schema types
as a string can arrive as a number, a list or null. ``_trim_participants``
checked that the participant WAS a dict and then took ``p["name"]`` verbatim,
so a non-string name survived the truthiness filter below it and went out over
the wire as a participant.

Measured 2026-08-29 before the fix::

    _trim_participants([{"name": 5}, {"email": ["a"]}, {"name": None, "email": 7}])
    -> ([5, ['a'], 7], 0)

Every one of those three is the value class ``_as_text`` was written to
neutralise everywhere else in the same file.
"""
import json

import pytest

from scripts.bridge_daemon.sources import conversations


BAD_PARTICIPANTS = [
    {"name": 5},
    {"email": ["a@example.com"]},
    {"name": None, "email": 7},
    {"name": {"nested": "dict"}},
    {"name": True},
]


# ============================================================
# The helper
# ============================================================

def test_a_numeric_name_does_not_become_a_participant():
    trimmed, _extra = conversations._trim_participants([{"name": 5}])
    assert trimmed == []


def test_every_returned_participant_is_a_string():
    trimmed, _extra = conversations._trim_participants(BAD_PARTICIPANTS)
    assert all(isinstance(t, str) for t in trimmed), trimmed


def test_a_non_string_name_falls_through_to_a_usable_email():
    trimmed, _extra = conversations._trim_participants(
        [{"name": 5, "email": "bond@example.com"}]
    )
    assert trimmed == ["bond@example.com"]


def test_good_participants_still_come_through():
    """Corpus guard: the coercion did not simply empty the list."""
    trimmed, extra = conversations._trim_participants([
        {"name": "James Bond"},
        {"email": "moneypenny@example.com"},
        "q@example.com",
        {"name": "Felix Leiter"},
    ])
    assert trimmed == ["James Bond", "moneypenny@example.com", "q@example.com"]
    assert extra == 1


# ============================================================
# Through the endpoint payload
# ============================================================

@pytest.fixture
def workspace(tmp_path):
    fetch = tmp_path / conversations.LATEST_FETCH_FILE
    fetch.parent.mkdir(parents=True)
    fetch.write_text(json.dumps({"conversations": [
        {
            "id": "c-1",
            "topic": "Acme Telecom rollout",
            "participants": BAD_PARTICIPANTS,
            "latest_datetime": "2026-08-01T10:00:00Z",
        },
        {
            "id": "c-2",
            "topic": "Quarterly review",
            "participants": [{"name": "James Bond"}],
            "latest_datetime": "2026-08-02T10:00:00Z",
        },
    ]}), encoding="utf-8")
    return tmp_path


def test_the_payload_carries_a_row_at_all(workspace):
    """Corpus guard: an empty conversations list would pass the check below."""
    rows = conversations.list_conversations(workspace)["conversations"]
    assert [r["id"] for r in rows] == ["c-2", "c-1"]


def test_no_non_string_participant_reaches_the_payload(workspace):
    rows = conversations.list_conversations(workspace)["conversations"]
    for row in rows:
        for participant in row["participants"]:
            assert isinstance(participant, str), (
                f"{row['id']} published a {type(participant).__name__} participant"
            )


def test_the_good_row_still_names_its_participant(workspace):
    rows = conversations.list_conversations(workspace)["conversations"]
    good = next(r for r in rows if r["id"] == "c-2")
    assert good["participants"] == ["James Bond"]
