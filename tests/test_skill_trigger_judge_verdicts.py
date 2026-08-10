"""What the harness does when the JUDGE fails, as opposed to when the ROUTER fails.

These two are the same number until something separates them, and on 2026-08-10 they
were not separated: the judge family resolved to a newer, more verbose Sonnet, its
verdict JSON no longer fitted the 300-token ceiling, and 47 truncated or empty replies
across 32 skills were scored as routing misses. The nightly trend recorded a 5-point
fleet-wide drop and the Tier-B alert named /voss at -38 points, while no commit had
touched `.claude/skills/` or `.claude/rules/` for two days.

So: a reply with no usable verdict is retried once, then counted as UNMEASURED and kept
out of the pass rate entirely - never as evidence that the router got it wrong.

The module is hyphenated, so it is loaded by path (importlib) following the
tests/test_routing_gate_changed.py precedent.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "skill_trigger_test", ROOT / "scripts" / "skill-trigger-test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Messages:
    """A judge that replies with the next scripted text, and records every call."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._replies.pop(0) if self._replies else ""
        return _Response(text)


class _Client:
    def __init__(self, replies):
        self.messages = _Messages(replies)


VERDICT_YES = '{"routes_to_target": true, "skill": "/voss", "reason": "explicit trigger"}'
# The exact shape the live run produced: valid JSON cut off mid-`reason`, because the
# reply ran past the token ceiling.
TRUNCATED = '{"routes_to_target": true, "skill": "/voss", "reason": "explicit \'difficult conv'


def _wire(mod, monkeypatch, cases):
    monkeypatch.setattr(mod, "load_triggers", lambda _d: cases)
    monkeypatch.setattr(mod, "load_skill_description", lambda _d: "desc")


def test_a_judge_non_answer_is_unmeasured_not_a_routing_miss(monkeypatch):
    mod = _load()
    _wire(mod, monkeypatch, [{"query": "q", "should_trigger": True}])
    client = _Client(["", ""])  # both attempts come back empty

    r = mod.run_skill(client, "m", "rules", "voss")

    assert r["errored"] == 1
    assert r["cases"] == 0, "an unanswered case must leave the denominator"
    assert r["passed"] == 0
    assert r["results"][0]["ok"] is None, "None means unmeasured; False would mean the router erred"


def test_a_truncated_verdict_is_not_scored_as_a_miss(monkeypatch):
    """The literal 2026-08-10 payload: JSON cut off mid-string by the token ceiling."""
    mod = _load()
    _wire(mod, monkeypatch, [{"query": "q", "should_trigger": True}])
    client = _Client([TRUNCATED, TRUNCATED])

    r = mod.run_skill(client, "m", "rules", "voss")

    assert r["cases"] == 0 and r["errored"] == 1


def test_the_judge_is_retried_once_before_a_case_is_given_up_on(monkeypatch):
    mod = _load()
    _wire(mod, monkeypatch, [{"query": "q", "should_trigger": True}])
    client = _Client(["", VERDICT_YES])  # empty first, real verdict second

    r = mod.run_skill(client, "m", "rules", "voss")

    assert len(client.messages.calls) == 2
    assert r["cases"] == 1 and r["passed"] == 1 and r["errored"] == 0


def test_a_good_verdict_costs_exactly_one_call(monkeypatch):
    """The retry must be a fallback, not a doubling of every nightly run's bill."""
    mod = _load()
    _wire(mod, monkeypatch, [{"query": "q", "should_trigger": True}])
    client = _Client([VERDICT_YES, VERDICT_YES])

    mod.run_skill(client, "m", "rules", "voss")

    assert len(client.messages.calls) == 1


def test_a_real_routing_miss_still_counts_against_the_rate(monkeypatch):
    """The boundary. Excluding non-answers must not excuse a wrong answer."""
    mod = _load()
    _wire(mod, monkeypatch, [{"query": "q", "should_trigger": False}])
    client = _Client([VERDICT_YES])  # judge says it routes; the case says it must not

    r = mod.run_skill(client, "m", "rules", "voss")

    assert r["cases"] == 1 and r["passed"] == 0 and r["errored"] == 0
    assert r["results"][0]["ok"] is False


def test_the_token_ceiling_clears_a_verdict_by_a_wide_margin(monkeypatch):
    """A ceiling trimmed to today's judge is a ceiling that breaks on the next one.

    Pinned as a number rather than as prose because the failure it prevents is
    silent: a truncated reply is well-formed right up to the byte it stops at.
    """
    mod = _load()
    _wire(mod, monkeypatch, [{"query": "q", "should_trigger": True}])
    client = _Client([VERDICT_YES])

    mod.run_skill(client, "m", "rules", "voss")

    assert client.messages.calls[0]["max_tokens"] >= 800
