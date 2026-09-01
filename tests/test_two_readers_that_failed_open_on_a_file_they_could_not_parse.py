#!/usr/bin/env python3
"""Two guards that answered in the dangerous direction on an unreadable file.

Audited and independently re-measured 2026-08-31. Both defects are in code whose
own docstring already promises the opposite behaviour, which is why neither was
caught by reading: the promise is right there, one screen above the line that
breaks it.

1. `scripts/utils/workspace.py`, an unhashable destination LEAF took the leak
   wall's classifier down. `default not in legal` and `value in legal` hash the
   candidate against a set, so a list- or dict-valued leaf in
   `config/routing-map.yaml` raised out of the resolver instead of resolving.
   MEASURED: a map holding `rules: {crm/: [private]}` and a map holding
   `default: [private]` each raised `TypeError: unhashable type: 'list'` from
   `_load_routing_map_cached`, and `get_routing_destination` has no handler for
   it while its own docstring reads "Fails closed: load_routing_map() already
   defaults to 'private' on error". `.claude/rules/classification.md` states the
   same contract: "a *broken* map fails closed to `private`".

   Invisible because the BLOCK-level version of the identical slip was already
   fixed, with a comment calling a stray `-` "the commonest YAML slip". The eye
   that fixed `rules:` written as a list did not go one level further down to
   the value under a key. `tests/test_routing_map_cache.py` covers an
   unparseable map, a missing map and a wrong-major map, and no non-string
   destination.

   Reachable on the push path: called once per tracked file by
   `scripts/leak-guard.py` and `scripts/utils/engine_guard.py`, both of which
   sit under the unbypassable push-time content scan.

2. `scripts/inbox_pulse/cost.py`, the daily spend cap failed OPEN on a corrupt
   ledger. `check_daily_cap` reads through `_load_state`, which swallowed a
   corrupt file and returned `{"daily_totals": {}}`, indistinguishable from a
   ledger that does not exist yet.
   MEASURED on one ledger file in two states: with
   `{"daily_totals": {<today>: {"spend_usd": 9.99}}}` on disk the cap answered
   True, and after overwriting that same file with `not json at all` it answered
   False on nothing but a log line. The next `record_call` then rewrote the file
   from near zero, so the day restarted with the cap off.

   Invisible because the docstring two lines above the read argues fail-closed
   for the sibling case, in words that cover this one exactly ("Spend that
   cannot be recorded is spend that must not happen"), so a reader checking the
   corrupt path found the right principle stated and stopped there.
   `tests/inbox_pulse/test_cost_stub.py` covers below, at and above the
   threshold; `tests/test_a_day_that_could_not_be_read_and_was_called_quiet.py`
   covers the refused-data-root path. Neither writes a corrupt ledger.

Every guard below is pinned from both sides. A fail-closed fix is trivially
"green" if it just answers `private` or `True` to everything, so each broken
input is paired with a sane one that must still resolve the other way.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import workspace  # noqa: E402

SANE_MAP = """\
version: 1
default: engine
rules:
  crm/: private
  knowledge/shared/: corporate
"""


# ============================================================
# 1. an unhashable leaf in the routing map
# ============================================================

@pytest.fixture
def map_root(tmp_path, monkeypatch):
    """A scratch workspace whose routing map this test writes, cache cleared."""
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    workspace._load_routing_map_cached.cache_clear()
    yield tmp_path
    workspace._load_routing_map_cached.cache_clear()


def _write_map(root: Path, text: str) -> None:
    (root / "config" / "routing-map.yaml").write_text(text, encoding="utf-8")
    workspace._load_routing_map_cached.cache_clear()


BROKEN_LEAVES = {
    "rule value is a list": "version: 1\ndefault: engine\nrules:\n  crm/:\n    - private\n",
    "rule value is a mapping": "version: 1\ndefault: engine\nrules:\n  crm/:\n    dest: private\n",
    "rule value is an int": "version: 1\ndefault: engine\nrules:\n  crm/: 3\n",
    "rule value is empty": "version: 1\ndefault: engine\nrules:\n  crm/:\n",
}


@pytest.mark.parametrize("label", sorted(BROKEN_LEAVES))
def test_a_non_string_rule_destination_resolves_private_instead_of_raising(
        map_root, label):
    """The path the broken rule governs must land on CEO data, not the engine."""
    _write_map(map_root, BROKEN_LEAVES[label])
    assert workspace.get_routing_destination("crm/contacts/a.md") == "private", (
        f"{label}: a broken destination let a CRM path resolve shareable")


@pytest.mark.parametrize("label", sorted(BROKEN_LEAVES))
def test_a_non_string_rule_destination_never_reaches_the_caller_as_typeerror(
        map_root, label):
    """The leak wall calls this once per tracked file; it must answer, not raise.

    Stated separately from the assertion above because a TypeError escaping the
    resolver is the failure mode, and `pytest.raises` on the wrong exception
    type reads as a pass if the destination assertion is all that is checked.
    """
    _write_map(map_root, BROKEN_LEAVES[label])
    try:
        workspace.load_routing_map()
    except TypeError as exc:  # pragma: no cover - this is the defect
        pytest.fail(f"{label}: loader raised TypeError instead of failing closed: {exc}")


BROKEN_DEFAULTS = {
    "default is a list": "version: 1\ndefault:\n  - private\nrules:\n  scripts/: engine\n",
    "default is a mapping": "version: 1\ndefault:\n  dest: engine\nrules:\n  scripts/: engine\n",
}


@pytest.mark.parametrize("label", sorted(BROKEN_DEFAULTS))
def test_a_non_string_default_fails_the_whole_map_closed(map_root, label):
    """An unmatched path under a broken default must be treated as data."""
    _write_map(map_root, BROKEN_DEFAULTS[label])
    assert workspace.get_routing_destination("scripts/anything.py") == "private", (
        f"{label}: an unmatched path resolved shareable under a broken default")
    assert workspace.load_routing_map()["default"] == "private"


def test_a_broken_leaf_is_reported_on_stderr_rather_than_swallowed(map_root, capsys):
    """Failing closed silently reclassifies a subtree with no trace of why."""
    _write_map(map_root, BROKEN_LEAVES["rule value is a list"])
    workspace.load_routing_map()
    err = capsys.readouterr().err
    assert "routing-map" in err and "crm/" in err, (
        f"nothing on stderr named the broken rule; got {err!r}")


def test_a_sane_map_still_resolves_all_three_destinations(map_root):
    """Anchor: answering 'private' to everything would pass every test above."""
    _write_map(map_root, SANE_MAP)
    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"
    assert workspace.get_routing_destination("knowledge/shared/x.md") == "corporate"
    assert workspace.get_routing_destination("scripts/calibrate.py") == "engine"


def test_one_broken_rule_does_not_reclassify_its_sane_siblings(map_root):
    """The `:769` precedent coerces the bad rule, it does not discard the map."""
    _write_map(map_root, "version: 1\ndefault: engine\nrules:\n"
                         "  crm/:\n    - private\n"
                         "  knowledge/shared/: corporate\n")
    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"
    assert workspace.get_routing_destination("knowledge/shared/x.md") == "corporate", (
        "a sibling rule was dropped along with the broken one")


# ============================================================
# 2. a spend cap that switched itself off on a corrupt ledger
# ============================================================

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point the cost stub's state dir at tmp_path and hand back the file path."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    from scripts.inbox_pulse import cost
    return cost, cost._state_path()


CORRUPT_LEDGERS = {
    "not json at all": "not json at all",
    "truncated mid-write": '{"daily_totals": {"2026-08-31": {"spend_us',
    "empty file": "",
    "html error page": "<html><body>502 Bad Gateway</body></html>",
}


@pytest.mark.parametrize("label", sorted(CORRUPT_LEDGERS))
def test_a_corrupt_ledger_reports_the_cap_as_reached(ledger, label):
    """Spend that cannot be READ has the same standing as spend that cannot be recorded."""
    cost, path = ledger
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CORRUPT_LEDGERS[label], encoding="utf-8")
    assert cost.check_daily_cap() is True, (
        f"{label}: the hard cap switched itself off on a ledger it could not parse")


def test_a_corrupt_ledger_still_says_so_in_the_log(ledger, caplog):
    """The fix must not buy fail-closed by dropping the diagnostic."""
    cost, path = ledger
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.cost"):
        cost.check_daily_cap()
    assert any("cost-tracker" in r.getMessage() for r in caplog.records), (
        f"no cost-tracker warning was logged; got {[r.getMessage() for r in caplog.records]}")


def test_an_absent_ledger_is_a_real_zero_and_not_a_corrupt_one(ledger):
    """Anchor: returning True on every read would pass the four cases above.

    A first run on a fresh install has no ledger, and that is an honest zero.
    Coercing it to "cap reached" would refuse every LLM call the daemon ever
    makes, which is the opposite failure.
    """
    cost, path = ledger
    assert not path.exists()
    assert cost.check_daily_cap() is False


def test_a_readable_ledger_below_the_cap_still_answers_false(ledger):
    """Anchor on the other side of the threshold, through a real recorded call."""
    cost, path = ledger
    today = cost._today_str()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"daily_totals": {today: {"spend_usd": 0.01}}}),
                    encoding="utf-8")
    assert cost.check_daily_cap() is False
    path.write_text(json.dumps({"daily_totals": {today: {"spend_usd": 9.99}}}),
                    encoding="utf-8")
    assert cost.check_daily_cap() is True


def test_record_call_still_recovers_rather_than_dropping_the_call(ledger):
    """`record_call` is a WRITE and keeps the non-strict read on purpose.

    It holds the only copy of the call it was handed, so re-founding the day
    from zero loses less than refusing to record. Pinned so a later tightening
    of `_load_state` cannot make a corrupt ledger silently discard usage.
    """
    cost, path = ledger
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    cost.record_call("claude-haiku-test", input_tokens=1000, output_tokens=100)
    state = json.loads(path.read_text(encoding="utf-8"))
    day = state["daily_totals"][cost._today_str()]
    assert day["calls_haiku"] == 1
    assert day["haiku_input_tokens"] == 1000
