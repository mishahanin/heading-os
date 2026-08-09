"""The /scrutinize judge layer must never name a model version.

CEO directive, 2026-08-09: the skill always judges on the LATEST Claude Opus, and
shipping a new Opus must not require editing the skill. A prose sentence saying so
is the same class of control this whole change exists to replace, so it gets a
test.

Two halves. The Claude side has no pin at all - that judge IS the running session,
so whatever Opus the session is on is what judges, and a version literal anywhere
in the skill would freeze it on the day someone typed it. The Kimi side does need
a model id, so it resolves through `config/council-models.json` at call time,
which makes a bump `--set kimi_reasoning=<new>` rather than a code edit.

The literal that prompted this was real: `bias-mitigation.md` priced the judge
layer against "Claude Opus 4.7" months after that stopped being current.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "scrutinize"
DISPATCH = Path(__file__).resolve().parent.parent / "scripts" / "scrutinize-dispatch.py"

# A Claude model named with a version: "Opus 4.7", "opus-4-7", "claude-opus-5",
# "Sonnet 4.5". Bare "Claude" and bare "Opus" are fine - they name the family,
# not a frozen release.
_VERSIONED_CLAUDE = re.compile(
    r"\b(claude[- ]?)?(opus|sonnet|haiku)[- ]?\d", re.IGNORECASE)

# The skill's own version-history file is a changelog: it records what was true on
# a past date and must keep saying so.
_ALLOWED = {"version-history.md"}


def _skill_files():
    return [p for p in SKILL_DIR.rglob("*.md") if p.name not in _ALLOWED]


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.name)
def test_no_claude_version_literal_in_the_skill(path):
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = _VERSIONED_CLAUDE.search(line)
        if match:
            hits.append(f"{path.name}:{lineno}: {match.group(0)!r} in {line.strip()[:90]}")
    assert not hits, (
        "A Claude version literal freezes /scrutinize on the day it was typed. "
        "The Claude judge is the running session; name the family, never a release.\n"
        + "\n".join(hits))


def test_the_dispatcher_pins_no_model_of_either_family():
    text = DISPATCH.read_text(encoding="utf-8")
    assert not _VERSIONED_CLAUDE.search(text), "dispatcher names a Claude release"
    # The Kimi id must come from the resolver, not from a literal assignment.
    assert 'get_model("kimi_reasoning")' in text
    assert not re.search(r'^KIMI_MODEL\s*=\s*["\']', text, re.MULTILINE), (
        "the Kimi pin is a literal again; resolve it through council_models so a "
        "new flagship is a --set, not a code edit")


def test_the_kimi_judge_pin_is_registered_in_the_council_seam():
    from scripts.utils.council_models import PROVIDERS, get_model

    assert "kimi_reasoning" in PROVIDERS
    assert get_model("kimi_reasoning")


def test_claude_is_absent_from_the_council_pin_table():
    """Its absence is the design: there is no Claude version to bump."""
    from scripts.utils.council_models import FALLBACKS

    assert not any("claude" in k.lower() for k in FALLBACKS)
