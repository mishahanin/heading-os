"""The executor treated "it parsed" as "it is a queue", and crashed on the rest.

`action-queue-execute.py`'s `main` catches `json.JSONDecodeError` beside
`OSError` and routes both to a stderr diagnostic plus exit 1, because an
unreadable send queue must never look like an empty one. JSON that PARSES into
something that is not a queue document walked straight past that guard into
`data.get("actions", [])` and `card.get("status")`.

Measured before the fix, each a raw AttributeError with no diagnostic on stderr
and no JSON array on the stdout the documented caller captures:

    [1,2]                  -> 'list' object has no attribute 'get'
    "hello"                -> 'str' object has no attribute 'get'
    {"actions": {"a": 1}}  -> 'str' object has no attribute 'get'
    {"actions": [1,2]}     -> 'int' object has no attribute 'get'

`send_card` is stubbed at the module seam in the one test that reaches it; no
subprocess is spawned and no mail path is touched.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AQE = _load("action-queue-execute", "action_queue_execute_shape")

# Each parses as JSON and is not a queue document. Asserted non-empty below so
# the loop cannot pass by iterating nothing.
NOT_A_QUEUE = [
    "[1,2]",
    '"hello"',
    "null",
    "17",
    '{"actions": {"a": 1}}',
    '{"actions": "approved"}',
    '{"actions": [1,2]}',
    '{"actions": [{"id": "a"}, "not-a-card"]}',
]


@pytest.fixture()
def queue_at(tmp_path, monkeypatch):
    """Point the executor at a queue file under tmp_path, never the live tree."""
    outputs = tmp_path / "outputs"
    (outputs / "operations/action-queue").mkdir(parents=True)
    monkeypatch.setattr(AQE, "get_outputs_dir", lambda: outputs)
    monkeypatch.setattr(AQE, "get_workspace_root", lambda: tmp_path)
    return outputs / "operations/action-queue/queue.json"


def test_the_malformed_corpus_is_not_empty():
    """A shape sweep over nothing passes over nothing."""
    assert len(NOT_A_QUEUE) >= 8
    for text in NOT_A_QUEUE:
        json.loads(text)  # every case must PARSE, or it tests the old guard


@pytest.mark.parametrize("text", NOT_A_QUEUE)
def test_json_that_is_not_a_queue_document_fails_honestly(queue_at, capsys, text):
    queue_at.write_text(text, encoding="utf-8")

    rc = AQE.main()
    out = capsys.readouterr()

    assert rc == 1, f"{text} exited 0 - an unreadable queue must not report success"
    assert out.err.strip(), f"{text} produced no diagnostic on stderr"
    assert json.loads(out.out) == [], f"{text} broke the stdout array contract"


def test_a_well_formed_queue_is_still_processed(queue_at, capsys, monkeypatch):
    """The other direction: the shape check must not reject a real queue."""
    sent = []

    def fake_send(engine_root, card, now=None):
        sent.append(card["id"])
        return {"action_id": card["id"], "result": "sent", "classification": "sent",
                "attempt": 0}

    monkeypatch.setattr(AQE, "send_card", fake_send)
    queue_at.write_text(json.dumps({"actions": [
        {"id": "aq-bond-1", "action_type": "email_send", "status": "approved",
         "to": "james.bond@example.com", "draft_body": "Body."},
        {"id": "aq-bond-2", "action_type": "email_send", "status": "pending",
         "to": "q@example.com", "draft_body": "Body."},
    ]}), encoding="utf-8")

    rc = AQE.main()
    out = capsys.readouterr()

    assert rc == 0
    assert sent == ["aq-bond-1"], "only the approved card may be sent"
    assert [r["action_id"] for r in json.loads(out.out)] == ["aq-bond-1"]


def test_an_empty_queue_is_still_a_clean_zero(queue_at, capsys):
    """Absent and unreadable stay different facts, and so does empty."""
    queue_at.write_text('{"actions": []}', encoding="utf-8")

    rc = AQE.main()
    out = capsys.readouterr()

    assert rc == 0
    assert json.loads(out.out) == []
    assert out.err == ""
