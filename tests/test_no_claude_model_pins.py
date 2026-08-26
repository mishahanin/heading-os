"""No engine script may name a Claude release. Families only.

CEO directive, 2026-08-09: drop the version pins everywhere, not just in
`/scrutinize`. A caller asks for a family (`opus`, `sonnet`, `haiku`, `fable`)
and `scripts/utils/claude_models.latest` returns whatever the newest release in
that family is on the day it runs, so a new flagship reaches every caller with
no code edit.

The pins this replaced were real and quietly stale: `skill-trigger-test.py`, the
LLM judge that decides whether a skill routes correctly and gates
`/push-updates`, was judging on Sonnet 4.6 five months after Sonnet 5 shipped.
`draft_critique.py`, which reviews outbound email before a human sends it, was
on Opus 4.7. Nothing failed loudly; the work just happened on older models.

Two exemptions, both deliberate:

* `scripts/utils/claude_models.py` carries the BASELINE floor, used only when the
  override, the cache, and the Models API have all failed. One documented literal
  in one file is the price of never crashing offline.
* History files (changelogs, version histories, audit reports) RECORD what was
  true on a past date and must keep saying so.

Not covered here, on purpose: `.claude/skills/*/evals/benchmark.json`. Those
carry a `model` field that `run-skill-eval.py` WRITES to record which model ran
the eval. A run record naming the model that ran is correct, not a stale pin.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# A Claude model named with a version: `claude-opus-5`, `claude-sonnet-4-6`,
# `claude-haiku-4-5-20251001`. Bare `opus` / `sonnet` / `haiku` are families and
# are exactly what callers are supposed to use.
_PINNED_ID = re.compile(r"claude-(opus|sonnet|haiku|fable)-[\d]", re.IGNORECASE)

# The one file allowed to hold the floor.
_RESOLVER = SCRIPTS / "utils" / "claude_models.py"


def _guarded_sources() -> list[Path]:
    """Python AND the config templates beside it.

    Python-only was the first version of this guard, and it missed the pin that
    mattered: `sentinel_config.example.yaml` set `model: "claude-haiku-4-5-..."`,
    so `config.get("model") or latest("haiku")` never reached the resolver and
    the daemon stayed frozen while the code looked de-pinned. A guard that reads
    only one file type finds only one kind of pin.
    """
    patterns = ("*.py", "*.yaml", "*.yml", "*.json")
    return [
        p
        for pattern in patterns
        for p in sorted(SCRIPTS.rglob(pattern))
        if p != _RESOLVER and "__pycache__" not in p.parts
    ]


@pytest.mark.parametrize("path", _guarded_sources(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_engine_script_pins_a_claude_release(path):
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = _PINNED_ID.search(line)
        if match:
            hits.append(f"{path.relative_to(ROOT)}:{lineno}: {match.group(0)!r}")
    assert not hits, (
        "A Claude release id freezes this caller on the day it was typed. Ask for "
        "a family instead:\n"
        "    from scripts.utils import claude_models\n"
        "    model = claude_models.latest('sonnet')      # newest Sonnet, today\n"
        "    model = claude_models.resolve(user_value)   # family or explicit id\n"
        + "\n".join(hits))


def test_the_resolver_answers_every_family(monkeypatch):
    """Pinned offline: the fetch is stubbed, so this asserts the BASELINE floor.

    The chain DEGRADES safely without a network; it does not avoid one. With a
    key in `.env` and a cold cache the unstubbed call really does hit HTTPS, so
    stubbing is what makes this a test of the floor rather than of the weather.
    """
    from scripts.utils import claude_models

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_models, "fetch_from_api", dict)
    monkeypatch.setattr(claude_models, "_cached", lambda *, allow_stale: {})
    monkeypatch.setattr(claude_models, "_read_json", lambda path: {})
    monkeypatch.setattr(claude_models, "_RESOLVED", {})

    for family in claude_models.FAMILIES:
        model = claude_models.latest(family)
        assert model == claude_models.BASELINE[family], (family, model)


def test_an_unknown_family_is_rejected_rather_than_guessed():
    from scripts.utils.claude_models import latest

    with pytest.raises(ValueError):
        latest("mythos")


def test_an_explicit_model_id_passes_through_untouched():
    """A one-off reproduction of an older run must still be possible."""
    from scripts.utils.claude_models import resolve

    assert resolve("claude-opus-4-8") == "claude-opus-4-8"
    assert resolve("sonnet").startswith("claude-sonnet")
    assert resolve(None).startswith("claude-sonnet")


def test_the_baseline_floor_names_a_real_family_for_each_key():
    from scripts.utils.claude_models import BASELINE, FAMILIES

    assert set(BASELINE) == set(FAMILIES)
    for family, model in BASELINE.items():
        assert model.startswith(f"claude-{family}-"), (family, model)


def test_the_scan_still_finds_the_sources():
    """An empty parametrize is one silent skip, not a failure. Every tree read
    here ships with the engine, so an empty result means the glob or the layout
    moved. 380 on 2026-08-26.
    """
    found = _guarded_sources()

    assert len(found) >= 250, f"only {len(found)} sources reached the model-pin gate"
