"""R5b pre-approval critique tests: advisory-only, cannot send/approve/dismiss.

Covers the critique core (graceful skip, well-formed advisory dict, no
status/approve/send field), the ``annotate_card`` store helper (stamps fields
without ever changing status), and the daemon critique sweep (``_critique_job``)
that wires them together.

No real model call (the anthropic client is faked / the function is injected) and
no real send. The load-bearing invariants asserted here:

  - ``critique_draft`` NEVER raises - returns None on empty body / missing key;
  - a critique result carries no status/approve/dismiss/send field;
  - ``annotate_card`` cannot change a card's status (a ``status`` kwarg is dropped);
  - the sweep passes the card's ``to`` field as the recipient (regression guard
    against the field-name bug found in scrutiny - cards have no ``recipient`` key);
  - the sweep skips needs_draft and already-critiqued cards, is bounded by
    ``max_per_tick``, and no-ops gracefully when the critic returns None;
  - a critiqued ``email_send`` card still resolves ``gated`` and stays ``pending``
    (the lethal-trifecta control is untouched by the advisory layer).

Run: python3 -m pytest tests/test_draft_critique.py
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bridge_daemon.sources import action_queue as aq
from scripts.utils import draft_critique, tool_risk


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "outputs" / "operations" / "action-queue").mkdir(parents=True)
    # The daemon critique job resolves its action-queue under get_data_root()
    # when no data_root is passed (fail-safe fallback, F-H8). Pin it to this
    # test's tmp tree so the sweep reads the card deposited here, not real data.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    return tmp_path


def _load_critique_job():
    """Import the real _critique_job from the hyphenated daemon module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "bridge-daemon.py"
    spec = importlib.util.spec_from_file_location("bridge_daemon_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._critique_job


def _email_card(to="jane@acme.com", contact="crm/contacts/jane.md", title="Nudge Jane",
                draft_status="ready_for_review", subject="Re: pricing",
                draft_body="Hi Jane, our sovereign tier lists at 347,850 AED. Best, Misha"):
    return {
        "action_type": "email_send", "to": to, "contact_file": contact,
        "title": title, "priority": "P1", "reasoning": "20d overdue",
        "subject": subject, "draft_body": draft_body, "draft_status": draft_status,
    }


def _deposit(root, card):
    res = aq.append_cards(root, [card])
    return res["ids"][0]


def _card(root, aid):
    data = json.loads((root / aq.QUEUE_FILE).read_text(encoding="utf-8"))
    return aq._find(data["actions"], aid)


def _events(root):
    log = root / aq.DISPOSITION_LOG
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _fake_anthropic(monkeypatch, answer_json: str):
    """Install a fake anthropic module whose messages.create returns answer_json."""
    block = types.SimpleNamespace(type="text", text=answer_json)
    resp = types.SimpleNamespace(content=[block])

    class _Msgs:
        def create(self, **kwargs):
            return resp

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Msgs()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    # Make the key present and stop load_env from clobbering the test env.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_env", lambda *a, **k: None)


# ============================================================
# critique_draft core: never raises, advisory-only shape
# ============================================================

def test_critique_draft_none_on_empty_body():
    assert draft_critique.critique_draft("Subj", "") is None
    assert draft_critique.critique_draft("Subj", "   ") is None
    assert draft_critique.critique_draft("Subj", None) is None


def test_critique_draft_none_on_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_env", lambda *a, **k: None)
    # Non-empty body, but no key -> graceful None, never raises, no API import.
    assert draft_critique.critique_draft("Subj", "real body text here") is None


def test_critique_draft_wellformed_advisory_dict(monkeypatch):
    _fake_anthropic(monkeypatch, '{"risk": "high", "flags": ["leaks pricing"], "summary": "Discloses price to external party."}')
    res = draft_critique.critique_draft("Re: pricing", "lists at 347,850 AED", recipient="jane@acme.com")
    assert res is not None
    assert res["risk"] == "high"
    assert res["flags"] == ["leaks pricing"]
    assert res["summary"].startswith("Discloses")
    assert res["model"] == "claude-haiku-4-5-20251001"
    assert "at" in res and "trace_id" in res
    # Advisory ONLY: it can never carry a state-transition / send field.
    forbidden = {"status", "approve", "approved", "dismiss", "dismissed", "sent", "send"}
    assert forbidden.isdisjoint(res.keys())


def test_critique_draft_normalises_unknown_risk(monkeypatch):
    _fake_anthropic(monkeypatch, '{"risk": "catastrophic", "flags": [], "summary": "x"}')
    res = draft_critique.critique_draft("s", "body", recipient="x@y.com")
    assert res is not None and res["risk"] == "medium"  # unknown risk -> conservative


def test_critique_draft_none_on_malformed_json(monkeypatch):
    _fake_anthropic(monkeypatch, "not json at all")
    assert draft_critique.critique_draft("s", "body") is None  # never raises


# ============================================================
# annotate_card: stamps advisory fields, NEVER touches status
# ============================================================

def test_annotate_card_stamps_critique_without_status(root):
    aid = _deposit(root, _email_card())
    assert _card(root, aid)["status"] == "pending"
    crit = {"risk": "medium", "flags": ["tone"], "summary": "fine"}
    res = aq.annotate_card(root, aid, critique=crit)
    assert res["ok"] is True
    card = _card(root, aid)
    assert card["critique"] == crit
    assert card["status"] == "pending"  # unchanged
    assert any(e.get("event") == "annotate" and e.get("action_id") == aid for e in _events(root))


def test_annotate_card_drops_status_kwarg(root):
    aid = _deposit(root, _email_card())
    # Even if a caller tries to smuggle a status change through annotate, it is dropped.
    res = aq.annotate_card(root, aid, status="sent", critique={"risk": "low"})
    assert res["ok"] is True
    card = _card(root, aid)
    assert card["status"] == "pending"     # NOT "sent"
    assert card["critique"] == {"risk": "low"}


def test_annotate_card_status_only_is_rejected(root):
    aid = _deposit(root, _email_card())
    res = aq.annotate_card(root, aid, status="sent")  # nothing left after dropping status
    assert res["ok"] is False and res["error"] == "no fields to annotate"
    assert _card(root, aid)["status"] == "pending"


def test_annotate_card_unknown_id_is_error(root):
    res = aq.annotate_card(root, "does-not-exist", critique={"risk": "low"})
    assert res["ok"] is False and res["error"] == "not found"


# ============================================================
# Daemon critique sweep (_critique_job)
# ============================================================

def test_sweep_stamps_and_passes_to_as_recipient(root, monkeypatch):
    """Regression guard: the sweep must feed the card's `to` field as the
    recipient (cards carry no `recipient` key)."""
    aid = _deposit(root, _email_card(to="jane@acme.com"))
    captured = {}

    def fake(subject, body, recipient=None, *, model=None):
        captured["recipient"] = recipient
        captured["subject"] = subject
        return {"risk": "high", "flags": ["x"], "summary": "s", "model": "haiku", "at": "t", "trace_id": "-"}

    monkeypatch.setattr(draft_critique, "critique_draft", fake)
    _load_critique_job()(root, 3, None)

    assert captured["recipient"] == "jane@acme.com"   # NOT None
    assert captured["subject"] == "Re: pricing"
    assert _card(root, aid)["critique"]["risk"] == "high"


def test_sweep_skips_needs_draft(root, monkeypatch):
    aid = _deposit(root, _email_card(draft_status="needs_draft"))
    calls = []
    monkeypatch.setattr(draft_critique, "critique_draft",
                        lambda *a, **k: calls.append(a) or {"risk": "low"})
    _load_critique_job()(root, 3, None)
    assert calls == []                          # never invoked the critic
    assert "critique" not in _card(root, aid)   # no stamp


def test_sweep_skips_already_critiqued(root, monkeypatch):
    aid = _deposit(root, _email_card())
    aq.annotate_card(root, aid, critique={"risk": "low", "flags": [], "summary": "pre"})
    calls = []
    monkeypatch.setattr(draft_critique, "critique_draft",
                        lambda *a, **k: calls.append(a) or {"risk": "high"})
    _load_critique_job()(root, 3, None)
    assert calls == []                                       # idempotent: not re-critiqued
    assert _card(root, aid)["critique"]["summary"] == "pre"  # original critique preserved


def test_sweep_none_result_leaves_uncritiqued(root, monkeypatch):
    aid = _deposit(root, _email_card())
    monkeypatch.setattr(draft_critique, "critique_draft", lambda *a, **k: None)
    _load_critique_job()(root, 3, None)          # must not raise
    card = _card(root, aid)
    assert card["status"] == "pending"
    assert "critique" not in card


def test_gated_invariant_intact_after_critique(root, monkeypatch):
    aid = _deposit(root, _email_card())
    monkeypatch.setattr(draft_critique, "critique_draft",
                        lambda *a, **k: {"risk": "high", "flags": [], "summary": "s"})
    _load_critique_job()(root, 3, None)
    card = _card(root, aid)
    assert card["critique"]["risk"] == "high"
    assert card["status"] == "pending"                       # still needs the approve click
    assert tool_risk.tier_for("email_send") == "gated"       # send floor unchanged


def test_sweep_bounded_by_max_per_tick(root, monkeypatch):
    for i in range(3):
        _deposit(root, _email_card(to=f"r{i}@acme.com", contact=f"crm/contacts/r{i}.md",
                                   title=f"Nudge {i}"))
    calls = []
    monkeypatch.setattr(draft_critique, "critique_draft",
                        lambda *a, **k: calls.append(a) or {"risk": "low", "flags": [], "summary": "s"})
    _load_critique_job()(root, 2, None)          # cap at 2 model calls this tick
    assert len(calls) == 2
    stamped = sum(1 for c in json.loads((root / aq.QUEUE_FILE).read_text())["actions"]
                  if c.get("critique"))
    assert stamped == 2
