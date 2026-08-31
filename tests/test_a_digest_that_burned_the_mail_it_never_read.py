#!/usr/bin/env python3
"""An email-intel run that lost the model still said it had done the job.

Measured on 2026-08-29 with the vendor chain made to raise the way an exhausted
anthropic then gemini then grok chain raises. One inbound renewal thread went
in. What came out:

    last_run_status       : 'complete'
    processed_message_ids : ['<renewal-1@bigcustomer.example>']
    next run would analyse: []                       <- gone, permanently

The digest rendered the thread as `P3 / fyi / "Review manually"`, which is the
line a reader skims past, and Layer 5 of `filter_noise` then dropped the id on
every later run. Nothing on disk recorded that the conversation had never been
looked at.

Three separate things made that possible and all three are pinned here.

1. `_fallback_analysis` returned the same SHAPE as a real analysis with no
   marker on it, so no caller could tell a placeholder from a judgement.
2. `run_info["status"]` counted only fetch problems as partiality. A run that
   analysed nothing at all reported `complete`.
3. `commit_payload["message_ids"]` was unconditional over everything fetched.
   The deferred `--commit-state` path replays that payload verbatim and cannot
   re-derive which analyses failed, so the pruning has to happen where the
   payload is built or the skill's Phase 5 inherits the same loss.

`scripts/sentinel.py` has left an unanalysed item unprocessed for the next
cycle since it was written. This is the second copy of that rule, and it was
missing.

A fourth defect is pinned here because it is what let the measurement damage
the operator's own data: `StateManager.__init__` captured the then-constant
`STATE_FILE` (now the `state_file()` resolver) as a
DEFAULT ARGUMENT, evaluated once at import, so patching the module global
redirected nothing and a no-argument construction wrote the real overlay.
"""
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "email_intelligence_burn", ROOT / "scripts" / "email-intelligence.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["email_intelligence_burn"] = mod
    spec.loader.exec_module(mod)
    return mod


ei = _load()


def _msg(mid, conv, topic="Topic"):
    return {
        "message_id": mid, "conversation_id": conv, "conversation_topic": topic,
        "item_class": "IPM.Note", "subject": topic,
        "sender_email": "buyer@bigcustomer.example", "sender_name": "A Buyer",
        "to": [{"email": "misha.hanin@31c.io", "name": "Misha Hanin"}],
        "cc": [], "body_preview": "p", "body": "b",
        "datetime": "2026-08-29T06:00:00+00:00", "direction": "incoming",
    }


@pytest.fixture
def offline_client(monkeypatch):
    """`analyze_conversations` builds a real Anthropic client before it batches.

    Without this the tests below pass on a workstation that has ANTHROPIC_API_KEY
    in .env and fail in CI, which has none. The key is never used: the one call
    that would reach the network, `call_anthropic_with_fallback`, is replaced in
    each test. Constructing the client is offline. `anthropic` is imported inside
    the function, so the seam here is the key lookup, not the module attribute.
    """
    monkeypatch.setattr(ei, "load_api_key", lambda *a, **kw: "not-a-real-key")


def _conv(cid, topic="Topic"):
    """The shape `analyze_conversations` reads, with nothing it does not use."""
    return {"id": cid, "topic": topic, "direction": "incoming", "message_count": 1,
            "is_internal": False, "raw_emails": [], "emails": [], "crm_context": None}


def _ok(conv):
    return {"category": "fyi", "priority": "P3", "summary": conv["topic"],
            "proposed_actions": [], "commitments": [],
            "relationship_signal": "stable"}


class _Run:
    """One end-to-end `main()` with Exchange and the model replaced."""

    def __init__(self, monkeypatch, tmp_path, inbox, analyse):
        self.state_file = tmp_path / "state.json"
        monkeypatch.setattr(ei, "state_file", lambda p=self.state_file: p)
        monkeypatch.setattr(ei, "connect_exchange", lambda *a, **kw: object())
        monkeypatch.setattr(ei, "_connect_with_retries", lambda *a, **kw: object())
        monkeypatch.setattr(ei, "_load_ignore_patterns", list)
        monkeypatch.setattr(ei, "load_crm_contacts", dict)
        monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
        monkeypatch.setattr(ei, "load_viraid_state", dict)
        monkeypatch.setattr(ei, "analyze_conversations", analyse)
        monkeypatch.setattr(
            ei, "fetch_emails",
            lambda a, folder, cutoff=None, limit=100, unread_only=False:
                ([dict(m) for m in inbox], False) if folder.lower().startswith("inbox")
                else ([], False))
        self._monkeypatch = monkeypatch

    def go(self, *argv):
        self._monkeypatch.setattr(sys, "argv",
                                  ["email-intelligence.py", "--hours", "24", *argv])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ei.main()
        return buf.getvalue()

    @property
    def committed_ids(self):
        if not self.state_file.exists():
            return []
        return json.loads(self.state_file.read_text(encoding="utf-8")) \
            .get("processed_message_ids", [])

    @property
    def committed_status(self):
        if not self.state_file.exists():
            return None
        return json.loads(self.state_file.read_text(encoding="utf-8")) \
            .get("last_run_status")


def _dead(convs, *a, **kw):
    return [ei._fallback_analysis(c) for c in convs]


def _mixed(convs, *a, **kw):
    """The first conversation is analysed; the second is not."""
    return [_ok(c) if i == 0 else ei._fallback_analysis(c)
            for i, c in enumerate(convs)]


def _all_ok(convs, *a, **kw):
    return [_ok(c) for c in convs]


# ============================================================
# 1. A placeholder says it is a placeholder
# ============================================================

def test_a_placeholder_analysis_is_marked_as_one():
    placeholder = ei._fallback_analysis({"topic": "Renewal"})
    assert placeholder["analysis_failed"] is True, (
        "nothing else distinguishes a placeholder from a real analysis")


def test_a_real_analysis_carries_no_such_marker():
    assert "analysis_failed" not in _ok({"topic": "Renewal"})


def test_the_placeholder_still_renders_as_a_card():
    """The marker must not break the digest: the card is why it exists."""
    placeholder = ei._fallback_analysis({"topic": "Renewal"})
    for key in ("category", "priority", "summary", "proposed_actions",
                "commitments", "relationship_signal"):
        assert key in placeholder, key


# ============================================================
# 2. A run that analysed nothing keeps its mail
# ============================================================

def test_a_total_model_outage_commits_no_message_ids(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _dead)
    run.go()
    assert run.committed_ids == [], (
        "an unanalysed message was marked processed and is now unreachable")


def test_a_total_model_outage_is_reported_as_partial(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _dead)
    run.go()
    assert run.committed_status == "partial"


def test_a_total_model_outage_says_so_in_the_terminal(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _dead)
    out = run.go()
    assert "not analysed" in out, (
        "the only sign of a dead model was a P3 card that reads like a verdict")


def test_the_next_run_sees_that_mail_again(monkeypatch, tmp_path):
    """The point of not committing: Layer 5 must not drop it."""
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _dead)
    run.go()
    state = ei.StateManager(path=run.state_file)
    kept, dropped = ei.filter_noise([_msg("<a@x>", "conv-a")], state, [],
                                    check_processed=True)
    assert [m["message_id"] for m in kept] == ["<a@x>"]
    assert dropped == 0


# ============================================================
# 3. Partial failure prunes only what failed
# ============================================================

def test_only_the_failed_conversations_mail_is_held_back(monkeypatch, tmp_path):
    inbox = [_msg("<a@x>", "conv-a", "Analysed"), _msg("<b@x>", "conv-b", "Not analysed")]
    run = _Run(monkeypatch, tmp_path, inbox, _mixed)
    run.go()
    committed = set(run.committed_ids)
    assert "<a@x>" in committed, "a successfully analysed message must be done"
    assert "<b@x>" not in committed, "the failed conversation's mail was burned"


def test_a_partial_failure_is_still_partial(monkeypatch, tmp_path):
    inbox = [_msg("<a@x>", "conv-a"), _msg("<b@x>", "conv-b")]
    run = _Run(monkeypatch, tmp_path, inbox, _mixed)
    run.go()
    assert run.committed_status == "partial"


# ============================================================
# 4. A good run is unchanged. Without this the fix could be "always partial"
# ============================================================

def test_a_successful_run_still_commits_every_id(monkeypatch, tmp_path):
    inbox = [_msg("<a@x>", "conv-a"), _msg("<b@x>", "conv-b")]
    run = _Run(monkeypatch, tmp_path, inbox, _all_ok)
    run.go()
    assert set(run.committed_ids) == {"<a@x>", "<b@x>"}


def test_a_successful_run_is_still_complete(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _all_ok)
    run.go()
    assert run.committed_status == "complete"


def test_a_successful_run_reports_no_analysis_failures(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _all_ok)
    out = run.go("--json")
    assert json.loads(out)["run_info"]["analysis_failures"] == 0


# ============================================================
# 5. The deferred commit path inherits the same rule
# ============================================================

def _json_payload(run, *argv):
    return json.loads(run.go("--json", *argv))


def test_the_json_payload_carries_the_partial_status(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _dead)
    payload = _json_payload(run)
    assert payload["run_info"]["status"] == "partial"
    assert payload["state_commit"]["status"] == "partial"


def test_the_json_payload_has_already_pruned_the_failed_ids(monkeypatch, tmp_path):
    """`commit_state_from_file` replays the payload and cannot re-derive this."""
    inbox = [_msg("<a@x>", "conv-a", "Analysed"), _msg("<b@x>", "conv-b", "Not analysed")]
    run = _Run(monkeypatch, tmp_path, inbox, _mixed)
    payload = _json_payload(run)
    assert payload["state_commit"]["message_ids"] == ["<a@x>"]
    assert [c["id"] for c in payload["state_commit"]["conversations"]] == ["conv-a"]


def test_a_json_run_commits_nothing_by_itself(monkeypatch, tmp_path):
    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _all_ok)
    _json_payload(run)
    assert run.committed_ids == [], "--json defers the commit to Phase 5"


def test_replaying_the_payload_commits_exactly_the_pruned_set(monkeypatch, tmp_path):
    inbox = [_msg("<a@x>", "conv-a", "Analysed"), _msg("<b@x>", "conv-b", "Not analysed")]
    run = _Run(monkeypatch, tmp_path, inbox, _mixed)
    payload = _json_payload(run)

    saved = tmp_path / "run.json"
    saved.write_text(json.dumps(payload), encoding="utf-8")
    fresh_state = tmp_path / "committed.json"
    state = ei.StateManager(path=fresh_state)
    ei.commit_state_from_file(saved, state)
    state.save()

    on_disk = json.loads(fresh_state.read_text(encoding="utf-8"))
    assert on_disk["processed_message_ids"] == ["<a@x>"]
    assert on_disk["last_run_status"] == "partial"


# ============================================================
# 6. Every placeholder site carries the marker, not just the one that was found
# ============================================================

# (reply, why, expected_unmarked). The third field is the EXACT number of
# conversations this reply may leave without the `analysis_failed` marker, for
# a batch of two.
#
# It used to be `<= 1` for every row, which is an off-by-one written into the
# assertion. Four of the five replies below carry no valid analysis for either
# conversation, so their correct count is zero; `<= 1` accepted one. That is the
# whole data-loss mode this file exists to close: one placeholder site that
# forgets the marker leaves one unanalysed conversation looking analysed, its
# message ids get committed as processed, and Layer 5 never looks at that mail
# again. `<= 1` could not see it.
#
# The counts below are MEASURED against `analyze_conversations`, not reasoned
# from the reply text; see the note on the bare-object row.
_BATCH_REPLIES = [
    ("not-json-at-all", "the vendor returned prose", 0),
    ("[]", "an empty array for a two-conversation batch", 0),
    ('["not an object"]', "a string where an analysis belongs", 0),
    # A bare JSON object, and it does NOT reach the one-object path: its own
    # `"proposed_actions": []` is the first `[...]` in the text, so
    # `_extract_json_array` returns that empty array and the whole batch is
    # padded. Measured 2026-08-30. The row is kept because that is real
    # behaviour worth pinning; the case it was WRITTEN for is the row below it,
    # which reaches the one-object path for real.
    ('{"category": "fyi", "priority": "P2", "summary": "s", "proposed_actions": [],'
     ' "commitments": [], "relationship_signal": "stable"}',
     "an object whose own empty array is found first", 0),
    ('[{"category": "fyi", "priority": "P2", "summary": "s", "proposed_actions": [],'
     ' "commitments": [], "relationship_signal": "stable"}]',
     "one object for a two-conversation batch", 1),
    ('42', "a number", 0),
]


@pytest.mark.parametrize("reply,why,expected_unmarked", _BATCH_REPLIES,
                         ids=[w for _, w, _ in _BATCH_REPLIES])
def test_a_conversation_the_model_did_not_answer_about_is_marked(
        monkeypatch, offline_client, reply, why, expected_unmarked):
    """Six sites emit a placeholder. A seventh added without the marker would
    silently under-prune, so the marker is asserted per malformed reply rather
    than only on the exception path that was originally found.

    The count is EXACT per reply. A reply that answers about one conversation
    out of two may leave exactly one unmarked; a reply that answers about
    neither may leave none.
    """
    class _Result:
        vendor = "test-vendor"
        text = reply
        primary_error = None
        fallback_triggered = False

    monkeypatch.setattr(ei, "call_anthropic_with_fallback", lambda *a, **kw: _Result())
    convs = [_conv("conv-a", "A"), _conv("conv-b", "B")]
    analyses = ei.analyze_conversations(convs, {}, "")
    assert len(analyses) == len(convs)
    unmarked = [a for a in analyses if not a.get("analysis_failed")]
    assert len(unmarked) == expected_unmarked, (
        f"{why}: {len(unmarked)} conversations were treated as analysed, "
        f"expected {expected_unmarked}")


def test_a_dead_chain_marks_every_conversation_in_the_batch(monkeypatch, offline_client):
    def _raise(*a, **kw):
        raise RuntimeError("anthropic 529; gemini 503; grok timeout")

    monkeypatch.setattr(ei, "call_anthropic_with_fallback", _raise)
    convs = [_conv(f"conv-{i}", str(i)) for i in range(3)]
    analyses = ei.analyze_conversations(convs, {}, "")
    assert all(a.get("analysis_failed") for a in analyses)


def test_a_dead_chain_is_announced_without_the_verbose_flag(
        monkeypatch, offline_client, capsys):
    def _raise(*a, **kw):
        raise RuntimeError("anthropic 529; gemini 503; grok timeout")

    monkeypatch.setattr(ei, "call_anthropic_with_fallback", _raise)
    ei.analyze_conversations(
        [_conv("conv-a", "A")], {}, "")
    assert "FAILED" in capsys.readouterr().err, (
        "the failure was visible only under --verbose, and the scheduled run "
        "does not pass it")


# ============================================================
# 7. The state path is resolved when the object is built, not at import
# ============================================================

def test_patching_the_module_state_file_actually_redirects(monkeypatch, tmp_path):
    """A default argument evaluated at import froze the operator's real path."""
    target = tmp_path / "redirected.json"
    monkeypatch.setattr(ei, "state_file", lambda p=target: p)
    assert ei.StateManager().path == target


def test_the_state_path_is_not_captured_as_a_default_argument():
    """The negative case, so the import-time capture cannot come back."""
    assert ei.StateManager.__init__.__defaults__ == (None,)


def test_an_explicit_path_still_wins(tmp_path):
    explicit = tmp_path / "explicit.json"
    assert ei.StateManager(path=explicit).path == explicit
    assert ei.StateManager(explicit).path == explicit, "positional callers exist"


def test_a_full_run_writes_nothing_outside_the_patched_path(monkeypatch, tmp_path):
    """The test that would have caught the 2026-08-29 incident."""
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    sentinel = overlay / "state.json"
    sentinel.write_text('{"version": 1, "processed_message_ids": ["<real@x>"]}',
                        encoding="utf-8")
    before = sentinel.read_bytes()

    run = _Run(monkeypatch, tmp_path, [_msg("<a@x>", "conv-a")], _all_ok)
    run.go()

    assert sentinel.read_bytes() == before, "the run reached outside its own state file"
    assert run.state_file.exists(), "and it did write the one it was given"
