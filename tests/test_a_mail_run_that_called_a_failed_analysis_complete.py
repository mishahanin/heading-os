"""The run-status flag that decides "partial" was measured for two of its three inputs.

`scripts/email-intelligence.py` computes one self-report per run:

    "status": ("partial" if (folder_errors or truncated_folders or failed_conv_ids)
               else "complete")     # main(), the time-window path
    "status": ("partial" if (unread_truncated or failed_conv_ids)
               else "complete")     # run_unread_mode(), the bridge-tick path

The comment above the second one records why the third input was added: "a total
model outage wrote a feed made entirely of 'Review manually' cards and labelled
the run `complete`". Nothing then pinned it, and the unread path had no status
assertion at all.

MEASURED 2026-09-01 by mutation, over
`tests/test_a_mail_run_that_reports_what_it_missed.py` +
`tests/test_a_mail_run_that_printed_progress_into_its_own_payload.py` +
the two merge suites, in a fresh git-initialised scratch tree (baseline
233 passed / 0 failed):

    dropping `failed_conv_ids` from main()'s condition        233 passed, 0 failed
    pinning run_unread_mode()'s status to "complete"          233 passed, 0 failed
    dropping `unread_truncated` from the unread condition     233 passed, 0 failed

Three survivors. Two of the same suites' mutations DID die
(`folder_errors` and `truncated_folders` removed from main() cost 5 and 4
failures), so the guard existed for the fetch inputs and stopped one input
short, on the one input that reports a MODEL outage rather than a mailbox one.
A monitoring flag that is green whenever the failure is of the untested kind is
the shape this workspace has already paid 33 silent hours for.

With this file the same three mutations fail. Exchange, the model and the CRM
are fakes; nothing reaches a network, a mailbox or the operator's overlay.

Run: .venv/bin/python -m pytest
tests/test_a_mail_run_that_called_a_failed_analysis_complete.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ei = _load("email_intelligence_status_probe", "scripts/email-intelligence.py")


# ============================================================
# A mailbox with one external thread in it
# ============================================================

class _Addr:
    def __init__(self, email, name=None):
        self.email_address = email
        self.name = name or email


class _ConvId:
    def __init__(self, cid):
        self.id = cid


class _Item:
    def __init__(self, n, conv="conv-1", is_read=False):
        self.message_id = f"<m{n}@example.test>"
        self.id = f"item-{n}"
        self.conversation_id = _ConvId(conv)
        self.conversation_topic = "Topic"
        self.subject = f"Subject {n}"
        self.sender = _Addr("them@example.test", "Them")
        self.to_recipients = [_Addr("misha.hanin@31c.io", "Misha")]
        self.cc_recipients = []
        self.text_body = f"body {n}"
        self.body = None
        self.datetime_received = None
        self.datetime_sent = None
        self.in_reply_to = ""
        self.item_class = "IPM.Note"
        self.importance = "Normal"
        self.has_attachments = False
        self.is_read = is_read
        self.saved_fields = []

    def save(self, update_fields=None):
        self.saved_fields.append(tuple(update_fields or ()))


class _QuerySet:
    def __init__(self, items):
        self._items = list(items)

    def filter(self, **kw):
        items = self._items
        if "is_read" in kw:
            items = [i for i in items if i.is_read == kw["is_read"]]
        return _QuerySet(items)

    def all(self):
        return _QuerySet(self._items)

    def only(self, *fields):
        return self

    def order_by(self, key):
        return self

    def __getitem__(self, sl):
        return _QuerySet(self._items[sl])

    def __iter__(self):
        return iter(self._items)


class _Account:
    def __init__(self, inbox_items=(), sent_items=()):
        self.inbox = _QuerySet(inbox_items)
        self.sent = _QuerySet(sent_items)


def _msg(mid, conv="conv-1"):
    """One message in the shape `fetch_emails` normalises to.

    Keys copied from that function's own `results.append({...})`, not invented:
    a dict missing one of them fails inside `filter_noise` with a KeyError long
    before any status flag is computed.
    """
    return {
        "message_id": mid, "conversation_id": conv, "conversation_topic": "T",
        "subject": "S", "sender_name": "Them", "sender_email": "them@example.test",
        "to": [{"name": "Misha", "email": "misha.hanin@31c.io"}], "cc": [],
        "body": "b", "body_preview": "p",
        "datetime": "2026-09-01T00:00:00+00:00", "in_reply_to": "",
        "item_class": "IPM.Note", "importance": "Normal",
        "has_attachments": False, "direction": "incoming",
    }


def _analysed_ok(convs, *a, **kw):
    """A SUCCESSFUL analysis, one per conversation."""
    return [{"category": "fyi", "priority": "P3", "summary": c["topic"],
             "proposed_actions": [], "commitments": [],
             "relationship_signal": "stable"} for c in convs]


def _analysed_failed(convs, *a, **kw):
    """What a model outage really produces: the module's own placeholder.

    Built by calling `_fallback_analysis` rather than by hand-writing a dict
    with `analysis_failed` in it. The status flag reads that key, so a
    hand-written stand-in would pin this file's idea of the marker instead of
    the module's, and a rename would leave these tests green over a flag that
    had stopped firing.
    """
    return [ei._fallback_analysis(c) for c in convs]


# ============================================================
# The time-window path (main)
# ============================================================

@pytest.fixture
def offline_run(monkeypatch, tmp_path):
    monkeypatch.setattr(ei, "state_file", lambda p=tmp_path / "state.json": p)
    monkeypatch.setattr(ei, "connect_exchange", lambda: _Account())
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)
    monkeypatch.setattr(ei, "_load_ignore_patterns", list)
    monkeypatch.setattr(ei, "fetch_emails",
                        lambda a, f, c: ([_msg("<m1@example.test>")] if f == "inbox"
                                         else [], False))

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["email-intelligence.py", *argv])
        ei.main()

    return run


def test_the_fixture_really_delivers_a_conversation_to_analyse(offline_run,
                                                               monkeypatch, capsys):
    """Pins the arrangement. Over an EMPTY mailbox there is nothing to fail to
    analyse, so every assertion below would hold with the flag deleted."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_ok)
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_info"]["conversations_processed"] == 1


def test_an_unanalysed_conversation_makes_the_time_window_run_partial(
        offline_run, monkeypatch, capsys):
    """THE case. A model outage over a mailbox that fetched perfectly."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_failed)
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)

    assert payload["run_info"]["analysis_failures"] == 1
    assert payload["run_info"]["status"] == "partial"
    # Neither fetch input fired, so nothing but the analysis can be carrying it.
    assert payload["run_info"]["folder_errors"] == {}
    assert payload["run_info"]["truncated_folders"] == []


def test_an_analysed_run_over_the_same_mailbox_is_complete(offline_run,
                                                           monkeypatch, capsys):
    """The mirror, on one changed input. A flag stuck at "partial" would pass
    the test above and make the word meaningless."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_ok)
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)

    assert payload["run_info"]["analysis_failures"] == 0
    assert payload["run_info"]["status"] == "complete"


def test_the_unanalysed_messages_are_held_back_from_the_state_commit(
        offline_run, monkeypatch, capsys):
    """Saying "partial" is half of it; the other half is fetching them again.
    A run that reported partial and still committed the ids would lose the mail
    just as thoroughly, and more quietly."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_failed)
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)

    assert payload["state_commit"]["message_ids"] == []


# ============================================================
# The bridge-tick path (run_unread_mode)
# ============================================================

@pytest.fixture
def unread_run(monkeypatch, tmp_path):
    state_file = tmp_path / "nested" / "state.json"
    monkeypatch.setattr(ei, "state_file", lambda p=state_file: p)
    monkeypatch.setattr(ei, "_connect_with_retries", lambda: _Account())
    monkeypatch.setattr(ei, "_load_ignore_patterns", list)
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)
    return state_file.parent / "_latest-fetch.json"


def _unread_summary(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_a_clean_unread_tick_reports_complete(unread_run, monkeypatch, capsys):
    """The anchor for the two below, and for the flag being reachable at all."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_ok)
    monkeypatch.setattr(
        ei, "fetch_emails",
        lambda a, f, cutoff=None, unread_only=False: ([_msg("<u1@example.test>")], False))

    ei.run_unread_mode()
    summary = _unread_summary(capsys)

    assert summary["unread_count"] == 1
    assert summary["analysis_failures"] == 0
    assert summary["status"] == "complete"


def test_an_unanalysed_unread_tick_reports_partial(unread_run, monkeypatch, capsys):
    """The outage the module's own comment describes: every card in the feed
    says "Review manually" and the tick called itself complete."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_failed)
    monkeypatch.setattr(
        ei, "fetch_emails",
        lambda a, f, cutoff=None, unread_only=False: ([_msg("<u1@example.test>")], False))

    ei.run_unread_mode()
    summary = _unread_summary(capsys)

    assert summary["analysis_failures"] == 1
    assert summary["status"] == "partial"


def test_a_truncated_unread_fetch_reports_partial(unread_run, monkeypatch, capsys):
    """The other input to the same expression, and it had no case either."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_ok)
    monkeypatch.setattr(
        ei, "fetch_emails",
        lambda a, f, cutoff=None, unread_only=False: ([_msg("<u1@example.test>")], True))

    ei.run_unread_mode()
    summary = _unread_summary(capsys)

    assert summary["analysis_failures"] == 0, "truncation must carry this alone"
    assert summary["status"] == "partial"


def test_the_partial_tick_says_so_on_stderr_as_well(unread_run, monkeypatch, capsys):
    """The bridge reads stdout; the operator reads the terminal. A flag nobody
    prints is a flag nobody sees."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_failed)
    monkeypatch.setattr(
        ei, "fetch_emails",
        lambda a, f, cutoff=None, unread_only=False: ([_msg("<u1@example.test>")], False))

    ei.run_unread_mode()
    captured = capsys.readouterr()

    assert "PARTIAL" in captured.err
    assert json.loads(captured.out)["status"] == "partial"


def test_the_feed_still_carries_the_conversation_it_could_not_analyse(
        unread_run, monkeypatch, capsys):
    """Report and degrade, per the module's stated choice. A tick that raised
    instead would blank the dashboard, which is the worse of the two."""
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_failed)
    monkeypatch.setattr(
        ei, "fetch_emails",
        lambda a, f, cutoff=None, unread_only=False: ([_msg("<u1@example.test>")], False))

    ei.run_unread_mode()
    capsys.readouterr()

    feed = json.loads(unread_run.read_text(encoding="utf-8"))
    assert len(feed["conversations"]) == 1
