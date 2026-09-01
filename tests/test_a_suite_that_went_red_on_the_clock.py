"""Forty tests failed because a cache was eighteen minutes past its TTL.

MEASURED 2026-08-31. Ten test files, forty tests, all failing with
`NetworkAccessRefused` against `160.79.104.10:443`. Nobody had changed the code
they cover. The cause was `.cache/claude-models.json` sitting at 24.3 hours
against `claude_models.CACHE_TTL_SECONDS` of 24 hours: inside the window every
one of them passed, outside it `fetch_from_api()` reached for the Anthropic
Models API and this suite's egress guard refused the connection.

A suite that turns red on wall-clock time rather than on code is worse than a
suite with a failing test in it, because the next person to see it has no diff
to read and will reasonably conclude the failure is theirs. It also erodes the
habit the guard depends on: forty red tests that go green again after a `git
pull` teach a reader to re-run rather than investigate.

The fix is the `_pin_model_resolution` autouse fixture in `tests/conftest.py`,
which pins `claude_models.fetch_from_api` to `{}` so resolution always degrades
to the cache and finally to `BASELINE`. That is the same thing
`tests/test_no_claude_model_pins.py` already did for itself; the fixture makes
it the default rather than something each test author has to remember.

This file is the regression guard, and it asks the question the way it actually
bites: with the cache made stale, does anything reach the network?

It does NOT test `claude_models` itself. `resolve()`, `latest()` and the
BASELINE floor have their own tests. The subject here is the SUITE's
determinism.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from scripts.utils import claude_models

ROOT = Path(__file__).resolve().parent.parent
CONFTEST = ROOT / "tests" / "conftest.py"


def test_the_pin_is_installed_for_this_test():
    """The fixture is autouse, so it is already in force here.

    `dict` is what `conftest` sets it to, and `dict()` is `{}`. Asked as
    identity rather than by calling it, so the assertion cannot be satisfied by
    some other function that happens to return an empty mapping.
    """
    assert claude_models.fetch_from_api is dict, (
        "the autouse pin in tests/conftest.py is not in force, so any test whose "
        "code path resolves a model id will reach the Models API the moment the "
        "24-hour cache expires")


def test_resolution_survives_a_cache_older_than_its_ttl(monkeypatch, tmp_path):
    """The exact condition that produced the forty failures.

    A cache file with a `fetched_at` beyond the TTL, and no network. Before the
    fixture this raised `NetworkAccessRefused` from the egress guard.

    It resolves to the STALE VALUE, not to BASELINE, and that is the design
    rather than a leak. The first draft of this test asserted BASELINE and was
    wrong: `latest()` tries the fresh cache, then the API, then the stale cache,
    and only then the floor. Yesterday's real answer beats a pinned constant
    that may be months old, so the stale read is the better answer and the
    ordering is deliberate. What matters for THIS file is only that no network
    call happened and the result is deterministic.
    """
    stale = tmp_path / "claude-models.json"
    stale.write_text(json.dumps({
        "fetched_at": time.time() - (claude_models.CACHE_TTL_SECONDS + 3600),
        "models": {"opus": "claude-opus-from-a-stale-cache"},
    }), encoding="utf-8")
    monkeypatch.setattr(claude_models, "cache_path", lambda: stale)
    monkeypatch.setattr(claude_models, "config_path", lambda: tmp_path / "absent.json")
    claude_models._RESOLVED.clear()
    claude_models._FETCH_FAILED = False

    resolved = claude_models.latest("opus")

    assert resolved == "claude-opus-from-a-stale-cache", (
        f"an expired cache resolved to {resolved!r}; the documented fall-through "
        f"is fresh cache, API, STALE cache, then BASELINE, and nothing here may "
        f"reach the network to decide it")


def test_no_cache_at_all_falls_through_to_the_baseline_floor(monkeypatch, tmp_path):
    """The last rung of the ladder, which is what a public clone stands on."""
    monkeypatch.setattr(claude_models, "cache_path", lambda: tmp_path / "absent.json")
    monkeypatch.setattr(claude_models, "config_path", lambda: tmp_path / "absent.json")
    claude_models._RESOLVED.clear()
    claude_models._FETCH_FAILED = False
    assert claude_models.latest("opus") == claude_models.BASELINE["opus"]


def test_a_fresh_cache_is_still_preferred_over_the_baseline(monkeypatch, tmp_path):
    """The other direction, so the test above is not passing over a broken cache
    reader that ignores every file it is given."""
    fresh = tmp_path / "claude-models.json"
    fresh.write_text(json.dumps({
        "fetched_at": time.time(),
        "models": {"opus": "claude-opus-from-a-fresh-cache"},
    }), encoding="utf-8")
    monkeypatch.setattr(claude_models, "cache_path", lambda: fresh)
    monkeypatch.setattr(claude_models, "config_path", lambda: tmp_path / "absent.json")
    claude_models._RESOLVED.clear()
    claude_models._FETCH_FAILED = False

    assert claude_models.latest("opus") == "claude-opus-from-a-fresh-cache"


def test_the_pin_can_be_opted_out_of_by_marking_the_test():
    """A test that genuinely needs the live API must be able to get it back.

    Asked of the fixture's SOURCE rather than by running a network test, because
    running one here would be the very thing the egress guard exists to stop.
    The markers are the same set the egress guard honours, so a test cannot be
    allowed out to the network while still being pinned, or pinned while allowed
    out, which would be two controls disagreeing about one intent.
    """
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"), filename=str(CONFTEST))
    fixture = next(
        (node for node in tree.body
         if isinstance(node, ast.FunctionDef) and node.name == "_pin_model_resolution"),
        None,
    )
    assert fixture is not None, (
        "tests/conftest.py no longer defines _pin_model_resolution; the suite is "
        "back to failing whenever the model cache expires")

    autouse = any(
        isinstance(d, ast.Call) and any(
            kw.arg == "autouse" and getattr(kw.value, "value", None) is True
            for kw in d.keywords)
        for d in fixture.decorator_list
    )
    assert autouse, "the pin is defined but not autouse, so it protects nothing"

    names = {n.id for n in ast.walk(fixture) if isinstance(n, ast.Name)}
    assert "_MODEL_PIN_MARKERS" in names, (
        "the fixture no longer consults a marker set, so a test that needs the "
        "live Models API has no way to ask for it")


@pytest.mark.parametrize("family", sorted(claude_models.BASELINE))
def test_every_family_resolves_to_something_without_a_network(family, monkeypatch, tmp_path):
    """The floor holds for every family, not only the one the last bug touched.

    A public clone with no API key and no cache is exactly this state, and it is
    also the state CI runs in.
    """
    monkeypatch.setattr(claude_models, "cache_path", lambda: tmp_path / "absent.json")
    monkeypatch.setattr(claude_models, "config_path", lambda: tmp_path / "absent.json")
    claude_models._RESOLVED.clear()
    claude_models._FETCH_FAILED = False

    resolved = claude_models.latest(family)
    assert resolved and resolved.startswith("claude-"), resolved
