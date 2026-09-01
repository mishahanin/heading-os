"""Tests for the R6 principle -> skill proposal core (CEO-only).

Covers the two-signal eligibility gate (type+Application AND reflection-derived),
the load-bearing safety assertion (the target SKILL.md is byte-identical after
build_proposal - it is structurally incapable of writing a skill file), phraser
degradation, and the --write-artifact boundary (only under outputs/, never under
.claude/skills/).

build_proposal is snake_case importable; the CLI is kebab-case, importlib-loaded.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from scripts.utils import workspace  # noqa: E402
from scripts.utils.odin_skill_proposal import build_proposal  # noqa: E402

ELIGIBLE = """---
id: "20260601120000"
title: Gate exposure on signed mNDA
type: principle
sources: ["20260530120000", "20260530120001"]
confidence: medium
keywords: [partnerships, sales]
created: 2026-06-01
---

## Principle
Gate product exposure on a signed mNDA.

## Evidence
Matured from two lived episodes (2026-05-30 to 2026-05-31), CEO-confirmed in `reflect` on 2026-06-01:
- episode one
- episode two

## Application
- Treat the mNDA as a precondition for any demo, not a formality to chase afterward.
- Keep deep technical exposure gated until it is signed.
"""

NO_APPLICATION = """---
id: "20260601120002"
title: No application
type: principle
sources: ["20260530120000"]
confidence: medium
keywords: [sales]
created: 2026-06-01
---

## Principle
A principle with evidence but no how-to.

## Evidence
Matured from one lived episode, CEO-confirmed in `reflect` on 2026-06-01.
"""

NOT_REFLECTION_DERIVED = """---
id: "20260601120003"
title: Book abstraction
type: principle
sources: ["misha-direct"]
confidence: high
keywords: [leadership]
created: 2026-06-01
---

## Principle
A high-confidence book/teach principle.

## Application
- Apply this leadership idea broadly.
"""

# The documented hard case (mirrors ceo-growth-treadmill): episode-id-shaped
# `sources` + `## Application` but NO "Matured from ... reflect" body. The gate
# keys off the body string, NOT the sources shape, so this MUST be refused. A
# future refactor that keyed eligibility off `sources` shape would wrongly
# accept it - this fixture pins that distinction.
EPISODE_SOURCED_NO_REFLECT = """---
id: "20260601120004"
title: Book principle with episode-shaped sources
type: principle
sources: ["20260530120000", "20260530120001"]
confidence: high
keywords: [leadership]
created: 2026-06-01
---

## Principle
A book/teach principle that happens to cite episode-id-shaped sources.

## Application
- Apply this leadership idea broadly.
"""

SKILL_MD = """---
name: test-skill
description: a test skill
---

# Test Skill

## Checklist
- existing item
"""


@pytest.fixture(autouse=True)
def _point_data_root_at_tmp(tmp_path, monkeypatch):
    """Point the data-root seam at tmp_path so `_ws()`'s fixtures are what is read.

    `get_knowledge_dir()` and `get_outputs_dir()` branch on `is_ceo_workspace()`,
    but both arms honour `HEADING_OS_DATA` first — the CEO arm through
    `get_data_root()`, the exec arm through `get_exec_data_root()` — so one env
    override covers the suite whatever this machine's identity says.

    The old comment claimed otherwise: "is_ceo_workspace() is already True (the
    real workspace root is ceo-master)". Wrong twice. The check reads the `type`
    field of the machine-local `.workspace-identity.json`, not the directory
    name, and the directory has been `.heading-os` since the 2026-06-15 cutover.
    The 2026-08-23 audit read that comment and reported the suite as passing by
    luck; measuring it (the test below) showed the seam is sound and only the
    comment was stale. Both arms are pinned by that test so the claim above
    cannot rot the same way.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))


def test_the_data_root_override_holds_under_either_workspace_identity(tmp_path, monkeypatch):
    """The property the autouse fixture rests on, asserted rather than assumed."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    for kind in ("ceo-master", "exec-workspace"):
        monkeypatch.setattr(
            workspace, "get_workspace_identity",
            lambda kind=kind: {"role": "admin", "slug": "t", "type": kind})
        assert workspace.get_knowledge_dir() == tmp_path / "knowledge", kind
        assert workspace.get_outputs_dir() == tmp_path / "outputs", kind


def _ws(tmp_path: Path, principle_slug: str, principle_text: str,
        skill_name: str = "test-skill", skill_text: str = SKILL_MD) -> Path:
    (tmp_path / "knowledge" / "odin-brain" / "principles").mkdir(parents=True)
    (tmp_path / "knowledge" / "odin-brain" / "principles" / f"{principle_slug}.md").write_text(
        principle_text, encoding="utf-8")
    skill_dir = tmp_path / ".claude" / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
    return tmp_path


def test_eligible_principle_produces_proposal(tmp_path):
    ws = _ws(tmp_path, "gate-mnda", ELIGIBLE)
    r = build_proposal("gate-mnda", "test-skill", workspace_root=ws)
    assert r["ok"] is True
    assert r["unified_diff"]
    assert "gate-mnda" in r["proposed_step"]
    assert r["target_section"] == "## Checklist"


def test_skill_file_is_never_mutated(tmp_path):
    """The load-bearing safety assertion: build_proposal must leave SKILL.md
    byte-identical - it proposes, it never writes."""
    ws = _ws(tmp_path, "gate-mnda", ELIGIBLE)
    skill_file = ws / ".claude" / "skills" / "test-skill" / "SKILL.md"
    before = skill_file.read_bytes()
    build_proposal("gate-mnda", "test-skill", workspace_root=ws)
    assert skill_file.read_bytes() == before


def test_no_application_refused(tmp_path):
    ws = _ws(tmp_path, "no-app", NO_APPLICATION)
    r = build_proposal("no-app", "test-skill", workspace_root=ws)
    assert r["ok"] is False
    assert "Application" in r["error"]


def test_not_reflection_derived_refused(tmp_path):
    ws = _ws(tmp_path, "book", NOT_REFLECTION_DERIVED)
    r = build_proposal("book", "test-skill", workspace_root=ws)
    assert r["ok"] is False
    assert "reflection-derived" in r["error"]


def test_episode_sourced_but_no_reflect_body_refused(tmp_path):
    """The hard case the docstring exists to catch: episode-id-shaped sources +
    Application, but no 'Matured from ... reflect' body -> still refused. A
    sources-shape gate would wrongly accept this; the body-string gate rejects it."""
    ws = _ws(tmp_path, "book-episodes", EPISODE_SOURCED_NO_REFLECT)
    r = build_proposal("book-episodes", "test-skill", workspace_root=ws)
    assert r["ok"] is False
    assert "reflection-derived" in r["error"]


NOT_A_PRINCIPLE = """---
id: "20260601120005"
title: An episode, not a principle
type: episode
sources: ["20260530120000"]
confidence: medium
keywords: [sales]
created: 2026-06-01
---

## Principle
Matured from one lived episode, CEO-confirmed in `reflect` on 2026-06-01.

## Application
- Do the thing.
"""


def test_a_note_that_is_not_a_principle_is_refused(tmp_path):
    """`type` is the first gate and it had no witness.

    Every fixture in this file carried `type: principle`, so replacing
    `if fm.get("type") != "principle":` with `if False:` left the file green.
    The fixture here is the realistic near-miss, not an obviously invalid one:
    an EPISODE that satisfies every later gate (Application section, a
    reflection-derived provenance line), so only the type check can refuse it.
    An episode is raw, unreviewed material; turning one into a skill checklist
    step is the whole thing this gate exists to stop.
    """
    ws = _ws(tmp_path, "an-episode", NOT_A_PRINCIPLE)
    r = build_proposal("an-episode", "test-skill", workspace_root=ws)
    assert r["ok"] is False
    assert "not a principle" in r["error"]
    assert "episode" in r["error"]


def test_a_phraser_that_returns_a_non_string_falls_back(tmp_path):
    """`if phrased and isinstance(phrased, str)` -- the isinstance half.

    Only the raising phraser was covered, so dropping the type check left the
    file green. A phraser handing back a dict or a list (an un-unwrapped model
    envelope is the ordinary way to get one) would then become `proposed_step`
    verbatim and land in the unified diff shown to the operator.
    """
    ws = _ws(tmp_path, "gate-mnda", ELIGIBLE)
    for bad in ({"text": "a step"}, ["a step"], 17, b"a step"):
        r = build_proposal("gate-mnda", "test-skill", workspace_root=ws,
                           phraser=lambda bad=bad, **kw: bad)
        assert r["ok"] is True
        assert isinstance(r["proposed_step"], str)
        assert "(see principle: gate-mnda)" in r["proposed_step"], bad


def test_unknown_skill_refused(tmp_path):
    ws = _ws(tmp_path, "gate-mnda", ELIGIBLE)
    r = build_proposal("gate-mnda", "no-such-skill", workspace_root=ws)
    assert r["ok"] is False
    assert "unknown skill" in r["error"]


def test_phraser_failure_falls_back_to_template(tmp_path):
    ws = _ws(tmp_path, "gate-mnda", ELIGIBLE)

    def _boom(**kwargs):
        raise RuntimeError("phraser blew up")

    r = build_proposal("gate-mnda", "test-skill", workspace_root=ws, phraser=_boom)
    assert r["ok"] is True  # the raise was swallowed
    assert "(see principle: gate-mnda)" in r["proposed_step"]  # deterministic template


def test_write_artifact_stays_under_outputs(tmp_path, monkeypatch):
    """The CLI's --write-artifact writes only under outputs/, never under
    .claude/skills/."""
    spec = importlib.util.spec_from_file_location(
        "odin_skill_proposal_cli", str(ROOT / "scripts" / "odin-skill-proposal.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    result = {
        "ok": True, "principle_slug": "gate-mnda", "skill_name": "proposal",
        "target_section": "## Checklist", "proposed_step": "- step (see principle: gate-mnda)",
        "unified_diff": "--- a\n+++ b\n@@ -1 +1,2 @@\n x\n+- step\n", "rationale": "because",
    }
    path = cli._write_artifact(result)
    assert path.exists()
    assert path.is_relative_to(tmp_path / "outputs" / "operations" / "odin" / "skill-proposals")
    # nothing was created under .claude/skills/
    assert not (tmp_path / ".claude" / "skills").exists()
