"""No skill may tell the model to reach for an em-dash while others ban it.

Found by the 2026-08-23 audit. Four skills carried "No em-dashes in any prose
this skill generates" (`/align`, `/burst`, `/devil`, and `/canopus`'s own body)
while `canopus/references/planning-gate.md` § Voice said the opposite:

    Never use `--` (two ASCII hyphens) as punctuation; a single em-dash or a
    restructured sentence.

Both are generation instructions, read in the same session, pointing opposite
ways. The operator settled this on 2026-06-30: no em-dash in prose Claude
authors, restructure instead, because by 2026 the character reads to a human
recipient as an "written by AI" tell. `.claude/rules/voice.md` now carries that
as the canonical statement.

SCOPE, deliberately narrow. 84 checked-in skill files and 15 rule files contain
em-dashes in their own documentation prose. That is NOT the defect and this test
does not touch it: `.claude/rules/humanization.md` treats the character as a
detector-side human signal to preserve, and a repo-wide strip would fight it for
no gain. The defect is instructions to the model that contradict each other.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.repo_files import read_sources

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
VOICE = ROOT / ".claude" / "rules" / "voice.md"

# Prose that tells the writer an em-dash is the preferred punctuation.
PRESCRIBES = re.compile(
    r"(a|use a|prefer a|single)\s+em[- ]dash\b(?!\s*(\(`—`\)|,)?\s*(is|are)\s+(fine|preserved))",
    re.I,
)


def _instruction_files() -> list[Path]:
    return sorted(SKILLS.rglob("*.md"))


def test_no_skill_prescribes_an_em_dash_as_punctuation():
    offenders: dict[str, list[str]] = {}
    # SCAN: a skill file that vanished between the rglob and the read prescribes
    # nothing to anybody, so skipping it is the right answer; `read_sources`
    # warns naming it and the count rides the failure message.
    vanished: list[Path] = []
    for path, text in read_sources(_instruction_files(), vanished):
        for line in text.splitlines():
            if PRESCRIBES.search(line):
                offenders.setdefault(path.relative_to(ROOT).as_posix(), []).append(
                    line.strip()[:120]
                )
    assert offenders == {}, (offenders, f"{len(vanished)} file(s) vanished mid-walk")


def test_the_planning_gate_voice_section_now_agrees_with_the_others():
    text = (SKILLS / "canopus" / "references" / "planning-gate.md").read_text(
        encoding="utf-8"
    )
    voice = text.split("## Voice", 1)[1]
    assert "no em-dash either" in voice, voice[:300]
    assert "a single em-dash or a restructured sentence" not in voice


def test_the_four_skills_still_carry_their_ban():
    """The other direction: deleting the bans would also silence the check."""
    for name in ("align", "burst", "devil"):
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert "No em-dashes in any prose this skill generates." in text, name


def test_voice_md_is_the_canonical_statement():
    text = VOICE.read_text(encoding="utf-8")
    assert "No em-dash in prose Claude authors either" in text
    assert "2026-06-30" in text
    assert "canonical home" in text


def test_voice_md_keeps_the_preserve_verbatim_carve_out():
    """Stripping quotes and edited prose would break humanization.md."""
    text = VOICE.read_text(encoding="utf-8")
    assert "preserved verbatim" in text
    assert "third-party quotes" in text
    assert "humanization.md" in text


def test_the_detector_is_not_vacuous():
    """A regex that matches nothing would pass this file forever."""
    assert PRESCRIBES.search("a single em-dash or a restructured sentence")
    assert PRESCRIBES.search("use a em-dash here")
    assert not PRESCRIBES.search("No em-dashes in any prose this skill generates.")
    assert not PRESCRIBES.search(
        "real em-dashes (`—`) are fine and must be preserved verbatim"
    )
