"""A cache that could not tell an analysis from an apology for not having one.

Commit 42f2e1e fixed the time-window path of `scripts/email-intelligence.py`:
when the model chain dies, `_fallback_analysis` returns a dict carrying
`analysis_failed`, the run reports `partial`, and those message ids are kept out
of the dedupe set so the next run retries them. Two survivors of that fix lived
on in `run_unread_mode`, the bridge dashboard's feed, and this file pins both.

SURVIVOR 1 - the placeholder was cached forever. `run_unread_mode` caches an
analysis keyed on the conversation's SET OF MESSAGE IDS. A placeholder went into
that cache exactly like a real analysis, and the id set of a quiet unread thread
never changes - so once the model failed for a conversation, every later tick
scored it a cache hit and never asked the model again. Measured 2026-08-29: run
1 with the model dead, run 2 with the model healthy, and run 2 still served
"Review manually" while reporting `analyzed_cached: 1`. That mode's `status`
counted only `unread_truncated`, so a total model outage wrote a feed made
entirely of placeholders and called the run `complete`.

SURVIVOR 2 - a silent truncation. `fresh_by_id` zipped `to_analyze` against the
analyses with no `strict=`, one of the repository's standing B905 findings. A
short result list dropped its tail without a word: measured 2026-08-29, one
analysis returned for two conversations left the second thread showing a
placeholder while the run said `analyzed_fresh: 2` and `complete`.

The chosen behaviour for survivor 2 is REPORT AND DEGRADE, not raise, because
this runs on a bridge daemon tick and a raise would blank the whole feed
including the conversations that were analysed correctly. The two fixes compose:
the unmatched conversations get `_fallback_analysis`, which marks them
`analysis_failed`, which now both makes the run `partial` and keeps them OUT of
the cache - so the next tick retries them. Loss that is visible and heals.
"""

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


ei = _load("email_intelligence_cache_failure", "scripts/email-intelligence.py")


# ============================================================
# Fixtures: a fetch that returns parsed rows, and a model we can kill
# ============================================================

def _msg(mid: str, topic: str = "Quiet thread", conv_id: str = "conv-quiet") -> dict:
    """One parsed Inbox row, in the shape `fetch_emails` hands back."""
    return {
        "message_id": mid,
        "conversation_id": conv_id,
        "conversation_topic": topic,
        "item_class": "IPM.Note",
        "subject": topic,
        "sender_email": "dana@northwind-example.test",
        "sender_name": "Dana Ferris",
        "to": [{"email": "misha.hanin@31c.io", "name": "Misha Hanin"}],
        "cc": [],
        "body_preview": "preview",
        "body": "body",
        "datetime": "2026-08-29T06:00:00+00:00",
        "direction": "incoming",
        "is_read": False,
    }


class _Model:
    """A stand-in for `analyze_conversations` that counts what it was asked.

    The count is the anti-vacuity instrument: "the cache was used" is a claim
    about a call that did NOT happen, and an absence proves nothing on its own.
    `conversations_seen` records what the model was actually handed.
    """

    def __init__(self, mode="healthy"):
        self.mode = mode
        self.calls = 0
        self.conversations_seen: list[str] = []

    def __call__(self, convs, crm_map, pipeline_text, verbose=False):
        self.calls += 1
        self.conversations_seen.extend(c["id"] for c in convs)
        if self.mode == "dead":
            return [ei._fallback_analysis(c) for c in convs]
        out = [
            {
                "category": "action",
                "priority": "P1",
                "summary": f"REAL ANALYSIS of {c['topic']}",
                "proposed_actions": ["reply today"],
                "commitments": [],
                "relationship_signal": "stable",
            }
            for c in convs
        ]
        if self.mode == "short":
            return out[:-1]
        if self.mode == "long":
            return out + [dict(out[0])]
        return out


@pytest.fixture
def feed(monkeypatch, tmp_path):
    """Drive `run_unread_mode` with Exchange, the CRM and the model replaced.

    `STATE_FILE` is redirected onto tmp_path. `StateManager` resolves it at call
    time, so this genuinely isolates the run - nothing here can reach the
    operator's real data overlay.
    """
    state_file = tmp_path / "outputs" / "operations" / "email-intelligence" / "state.json"
    monkeypatch.setattr(ei, "STATE_FILE", state_file)
    monkeypatch.setattr(ei, "_connect_with_retries", lambda: object())
    monkeypatch.setattr(ei, "_load_ignore_patterns", list)
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)

    fetch_path = state_file.parent / "_latest-fetch.json"

    def run(model, msgs, truncated=False):
        monkeypatch.setattr(ei, "analyze_conversations", model)
        monkeypatch.setattr(
            ei, "fetch_emails",
            lambda a, f, cutoff=None, unread_only=False: ([dict(m) for m in msgs], truncated),
        )
        ei.run_unread_mode()
        return json.loads(fetch_path.read_text(encoding="utf-8"))

    return run


def _analysis_for(payload: dict, topic: str) -> dict:
    for c in payload["conversations"]:
        if c["topic"] == topic:
            return c["analysis"]
    raise AssertionError(f"{topic!r} is not in the feed: "
                         f"{[c['topic'] for c in payload['conversations']]}")


# ============================================================
# Survivor 1 - a placeholder is not a cache hit
# ============================================================

def test_a_thread_the_model_failed_on_is_analysed_again_on_the_next_run(feed):
    """The whole defect in one run pair: the model dies, the thread stays quiet,
    and the dashboard served that apology for as long as the mail was unread."""
    msgs = [_msg("<quiet-1@northwind-example.test>")]
    first = feed(_Model("dead"), msgs)
    assert _analysis_for(first, "Quiet thread")["analysis_failed"] is True

    healthy = _Model("healthy")
    second = feed(healthy, msgs)

    assert healthy.calls == 1, "the model was never asked about the failed thread again"
    assert healthy.conversations_seen == ["conv-quiet"]
    served = _analysis_for(second, "Quiet thread")
    assert served["summary"] == "REAL ANALYSIS of Quiet thread"
    assert not served.get("analysis_failed")
    assert second["run_info"]["analyzed_fresh"] == 1
    assert second["run_info"]["analyzed_cached"] == 0


def test_a_successful_analysis_is_still_served_from_the_cache(feed):
    """The fix must not be "never cache". An unchanged thread that WAS analysed
    costs nothing on the next tick, which is the whole point of the cache."""
    msgs = [_msg("<quiet-1@northwind-example.test>")]
    feed(_Model("healthy"), msgs)

    second_model = _Model("healthy")
    second = feed(second_model, msgs)

    assert second["run_info"]["analyzed_cached"] == 1, (
        "the cache count is the positive evidence; an un-called model alone "
        "would also be true of a run that fetched nothing"
    )
    assert second["run_info"]["analyzed_fresh"] == 0
    assert second_model.calls == 0
    assert second_model.conversations_seen == []
    assert _analysis_for(second, "Quiet thread")["summary"] == "REAL ANALYSIS of Quiet thread"


def test_the_unread_mode_reports_partial_when_an_analysis_failed(feed):
    """`status` counted only `unread_truncated`, so a total model outage wrote a
    feed of placeholders and labelled it complete."""
    payload = feed(_Model("dead"), [_msg("<quiet-1@northwind-example.test>")])
    assert payload["run_info"]["status"] == "partial"
    assert payload["run_info"]["analysis_failures"] == 1


def test_a_fully_analysed_unread_run_is_complete(feed):
    """Anchor for the test above: `partial` must mean something, so the healthy
    run has to come back `complete` with a zero failure count."""
    payload = feed(_Model("healthy"), [_msg("<quiet-1@northwind-example.test>")])
    assert payload["run_info"]["status"] == "complete"
    assert payload["run_info"]["analysis_failures"] == 0


def test_a_truncated_unread_fetch_is_still_partial(feed):
    """The partiality the mode already knew about must survive the new one."""
    payload = feed(_Model("healthy"), [_msg("<quiet-1@northwind-example.test>")], truncated=True)
    assert payload["run_info"]["status"] == "partial"
    assert payload["run_info"]["analysis_failures"] == 0, (
        "a truncated fetch is not an analysis failure; the two reasons stay distinct"
    )


def test_new_mail_in_a_thread_still_invalidates_the_cache(feed):
    """`_cache_key` is the id set, and that check has to keep working - the
    failure marker is an ADDITIONAL reason to re-analyse, not a replacement."""
    feed(_Model("healthy"), [_msg("<quiet-1@northwind-example.test>")])

    changed = _Model("healthy")
    second = feed(changed, [
        _msg("<quiet-1@northwind-example.test>"),
        _msg("<quiet-2@northwind-example.test>"),
    ])

    assert changed.calls == 1, "a reply landed; the prior analysis describes older mail"
    assert second["run_info"]["analyzed_cached"] == 0
    assert second["run_info"]["analyzed_fresh"] == 1


# ============================================================
# Survivor 2 - a short result list is reported, not swallowed
# ============================================================

TWO_THREADS = [
    _msg("<a-1@northwind-example.test>", "First thread", "conv-a"),
    _msg("<b-1@northwind-example.test>", "Second thread", "conv-b"),
]


def test_a_short_analysis_list_leaves_the_unmatched_thread_marked_unanalysed(feed, capsys):
    """Chosen behaviour, asserted through what a caller can see rather than by
    the exception type: the tail is marked `analysis_failed`, the run says
    `partial`, and stderr names the mismatch."""
    payload = feed(_Model("short"), TWO_THREADS)
    err = capsys.readouterr().err

    assert _analysis_for(payload, "First thread")["summary"] == "REAL ANALYSIS of First thread"
    assert _analysis_for(payload, "Second thread")["analysis_failed"] is True
    assert payload["run_info"]["status"] == "partial"
    assert payload["run_info"]["analysis_failures"] == 1
    assert "1 analysis" in err and "2 conversation(s)" in err, (
        f"the mismatch has to be named on stderr, got: {err!r}"
    )


def test_a_short_analysis_list_does_not_lose_the_rest_of_the_feed(feed):
    """The reason this degrades instead of raising. A raise out of a daemon tick
    would blank the dashboard over one upstream batch bug."""
    payload = feed(_Model("short"), TWO_THREADS)
    assert len(payload["conversations"]) == 2, (
        "every unread conversation still reaches the feed, analysed or not"
    )


def test_a_truncated_analysis_is_retried_on_the_next_run(feed):
    """The two fixes compose: the tail was not analysed, so it was not cached,
    so the next tick asks about it. Without the survivor-1 fix this placeholder
    would be permanent."""
    feed(_Model("short"), TWO_THREADS)

    recovered = _Model("healthy")
    second = feed(recovered, TWO_THREADS)

    assert recovered.conversations_seen == ["conv-b"], (
        "only the thread that was never analysed is re-sent; the other is cached"
    )
    assert second["run_info"]["analyzed_cached"] == 1
    assert _analysis_for(second, "Second thread")["summary"] == "REAL ANALYSIS of Second thread"
    assert second["run_info"]["status"] == "complete"


def test_an_over_long_analysis_list_is_reported_too(feed, capsys):
    """A mismatch in the other direction is the same upstream bug. `strict=True`
    on a slice cannot see it, so the length check is what catches it."""
    payload = feed(_Model("long"), TWO_THREADS)
    err = capsys.readouterr().err
    assert "3 analysis/analyses" in err and "2 conversation(s)" in err, (
        f"an extra analysis went unreported, got: {err!r}"
    )
    assert len(payload["conversations"]) == 2


def test_a_matched_analysis_list_says_nothing_and_completes(feed, capsys):
    """Anti-vacuity for the warning: the common path must stay quiet, or the
    stderr line is noise a reader learns to ignore."""
    payload = feed(_Model("healthy"), TWO_THREADS)
    err = capsys.readouterr().err
    assert "NOT analysed" not in err, f"the healthy path warned anyway: {err!r}"
    assert payload["run_info"]["status"] == "complete"
    assert payload["run_info"]["analysis_failures"] == 0


# ============================================================
# The run summary the caller actually reads
# ============================================================
#
# `run_unread_mode` writes `_latest-fetch.json` for the bridge and prints a
# one-line JSON summary on stdout for whoever invoked it. Every assertion above
# reads the FILE. The summary said `{"ok": true, ...}` and named only how many
# conversations were ATTEMPTED, so the two fixes above were invisible to the
# caller: a total model outage printed success. `main()` has printed a PARTIAL
# line since `42f2e1e`, and the unread path is the one the bridge tick calls.

def _summary(capsys) -> dict:
    captured = capsys.readouterr()
    return json.loads(captured.out.strip().splitlines()[-1]), captured.err


def test_the_run_summary_admits_the_run_was_partial(feed, capsys):
    """A caller reading stdout must not be told a placeholder feed is fine."""
    feed(_Model("dead"), [_msg("<quiet-1@northwind-example.test>")])
    summary, _ = _summary(capsys)
    assert summary["status"] == "partial"
    assert summary["analysis_failures"] == 1
    assert summary["unread_count"] == 1


def test_a_model_outage_is_named_on_stderr_without_verbose(feed, capsys):
    """The outage has to be legible without re-running under `--verbose`.

    The length-mismatch branch already warns, but it fires only when
    `analyze_conversations` returns the WRONG COUNT. A model that returns a
    correctly-sized list of failures, which is what an API outage produces,
    reached this line silently.
    """
    feed(_Model("dead"), [_msg("<quiet-1@northwind-example.test>")])
    _, err = _summary(capsys)
    assert "PARTIAL" in err and "1 conversation(s) were not analysed" in err, (
        f"the outage was not reported on stderr, got: {err!r}")


def test_a_healthy_run_summary_is_quiet_and_complete(feed, capsys):
    """Anti-vacuity for both tests above. A warning printed on every run is a
    warning the operator stops reading."""
    feed(_Model("healthy"), [_msg("<quiet-1@northwind-example.test>")])
    summary, err = _summary(capsys)
    assert summary["status"] == "complete"
    assert summary["analysis_failures"] == 0
    assert summary["ok"] is True
    assert "PARTIAL" not in err, f"the healthy path warned anyway: {err!r}"
