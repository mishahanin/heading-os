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


# A model version this skill's judge layer does not have: the two retired council
# families are as stale as an old Opus when a live sentence still names them with
# a release attached.
_VERSIONED_MODEL = re.compile(
    r"\b(claude|opus|sonnet|haiku|gemini|grok)[- ]?\d", re.IGNORECASE)


def test_the_operator_overview_pins_no_model_where_it_describes_scrutinize():
    """The gap this guard had, found by /scrutinize on the run that built it.

    Everything above scans `.claude/skills/scrutinize/` and the dispatcher. The
    operator's tool index lives outside both, in the DATA overlay, and it
    describes the same judge layer in prose. So it carried
    `Claude Opus 4.7 / Gemini 3.5 Flash / Grok 4.3` through the very change whose
    subject was removing that literal, four lines below the entry recording the
    removal.

    Scoped to lines that mention scrutinize, because the same file legitimately
    names model versions when describing other tools. Skipped, never failed, when
    the overlay is absent: a public clone has no operator overview, and a guard
    that fails on its absence teaches people to delete it.
    """
    from scripts.utils.workspace import get_data_root

    overview = get_data_root() / "reference" / "workspace-overview.md"
    if not overview.is_file():
        pytest.skip("no operator overview (data overlay absent, e.g. a public clone)")

    hits = []
    for lineno, line in enumerate(overview.read_text(encoding="utf-8").splitlines(), 1):
        if "scrutinize" not in line.lower():
            continue
        match = _VERSIONED_MODEL.search(line)
        if match:
            hits.append(f"workspace-overview.md:{lineno}: {match.group(0)!r}")
    assert not hits, (
        "the operator overview pins a model version while describing /scrutinize. "
        "The judge roster is the running session's Claude, never pinned, and the "
        "Kimi pin resolved through council-models.json.\n" + "\n".join(hits))


def test_the_scan_still_finds_the_skill_files():
    """An empty parametrize is one silent skip, not a failure. The skill tree
    ships with the engine, so an empty result means the directory moved.
    16 on 2026-08-26.
    """
    found = _skill_files()

    assert len(found) >= 10, f"only {len(found)} skill files reached the pin gate"
