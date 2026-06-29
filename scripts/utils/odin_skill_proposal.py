"""odin_skill_proposal.py - the principle -> skill rewrite loop core (R6, CEO-only).

Takes a reflection-derived how-to principle and a target skill, and drafts a
PROPOSED checklist-step insertion as a unified diff. It NEVER writes the skill
file - it returns a proposal for the CEO to apply by hand. The proposal core is
structurally incapable of mutating anything under .claude/skills/.

Eligibility is a two-signal deterministic gate (no model classification):
  1. The principle is `type: principle` and has a non-empty `## Application`
     section (the how-to *shape*).
  2. The principle is *reflection-derived* - its Evidence body carries the
     "Matured from ... `reflect`" attribution string that every reflect-matured
     principle gets. This is the discriminator: a high-confidence book/teach
     principle with an `## Application` section (e.g. ceo-growth-treadmill, which
     even has episode-id-shaped `sources`) is correctly refused, because the
     thing that belongs in a 31C skill checklist is a lived, episode-matured
     how-to, not a book abstraction.

Public surface:
  build_proposal(principle_path, skill_name, *, workspace_root=None,
                 section=None, phraser=None) -> dict
    -> {ok: True, principle_slug, skill_name, target_section, proposed_step,
        unified_diff, rationale}  OR  {ok: False, error}

`phraser`, if given, is a callable that phrases the proposed step; any exception
it raises is swallowed and the deterministic template is used instead. The
proposal never makes a model call on its own.

Consumed by: scripts/odin-skill-proposal.py (CLI) and the `## Mode:
skill-proposal` flow in .claude/skills/odin/references/mode-catalog.md.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.markdown import parse_frontmatter
from scripts.utils.workspace import get_workspace_root, get_knowledge_dir

# A reflection-derived principle's Evidence body carries this attribution.
_REFLECTION_RE = re.compile(r"matured from\b.*?\breflect", re.IGNORECASE | re.DOTALL)
# Headings that make a natural checklist insertion point, most-specific first.
_SECTION_RE = re.compile(r"^#{2,}\s+.*(checklist|pre-?flight|phase|steps)", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")


def _resolve_principle_path(principle_path: str, workspace_root: Path) -> Path:
    p = Path(principle_path)
    if p.suffix == ".md" or "/" in str(principle_path):
        return p if p.is_absolute() else (workspace_root / p)
    return get_knowledge_dir() / "odin-brain" / "principles" / f"{principle_path}.md"


def _extract_section(body: str, heading_word: str) -> str:
    """Return the text under a `## {heading_word}` heading, up to the next
    heading. Empty string if the section is absent or empty."""
    out: list[str] = []
    capture = False
    head_re = re.compile(rf"^#{{2,}}\s+{re.escape(heading_word)}\s*$", re.IGNORECASE)
    for line in body.splitlines():
        if head_re.match(line):
            capture = True
            continue
        if capture and _ANY_HEADING_RE.match(line):
            break
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def _first_actionable_line(section_text: str) -> str:
    for line in section_text.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[-*]\s+", "", s)        # strip a leading bullet marker
        s = re.sub(r"^\d+\.\s+", "", s)        # strip a leading ordinal
        return s
    return ""


def _choose_target_section(skill_lines: list[str], section: str | None) -> tuple[int | None, str | None]:
    """Return (heading_line_index, heading_text). Prefer an explicit `section`
    arg, then a checklist/phase-style heading, then the first heading."""
    if section:
        for i, line in enumerate(skill_lines):
            if line.strip().lstrip("#").strip().lower() == section.strip().lower():
                return i, line.strip()
    for i, line in enumerate(skill_lines):
        if _SECTION_RE.match(line):
            return i, line.strip()
    for i, line in enumerate(skill_lines):
        if _ANY_HEADING_RE.match(line):
            return i, line.strip()
    return None, None


def build_proposal(
    principle_path: str,
    skill_name: str,
    *,
    workspace_root: Path | None = None,
    section: str | None = None,
    phraser=None,
) -> dict:
    """Draft a proposed checklist-step edit. NEVER writes the skill file."""
    workspace_root = workspace_root or get_workspace_root()

    principle_file = _resolve_principle_path(principle_path, workspace_root)
    if not principle_file.exists():
        return {"ok": False, "error": f"principle not found: {principle_path}"}
    try:
        text = principle_file.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"principle unreadable: {e}"}
    slug = principle_file.stem

    fm, body = parse_frontmatter(text)
    if fm.get("type") != "principle":
        return {"ok": False, "error": f"not a principle (type={fm.get('type')!r})"}

    application = _extract_section(body, "Application")
    if not application:
        return {"ok": False, "error": "not a how-to principle (no Application section)"}

    if not _REFLECTION_RE.search(body):
        return {"ok": False,
                "error": "not reflection-derived (book/abstraction principle, not a lived 31C how-to)"}

    skill_file = workspace_root / ".claude" / "skills" / skill_name / "SKILL.md"
    if not skill_file.exists():
        return {"ok": False, "error": f"unknown skill: {skill_name}"}
    try:
        skill_text = skill_file.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"skill unreadable: {e}"}

    bullet_src = _first_actionable_line(application) or fm.get("title", slug)
    proposed_step = f"- {bullet_src} (see principle: {slug})"
    if phraser is not None:
        try:
            phrased = phraser(slug=slug, application=application, skill_name=skill_name,
                              fallback=proposed_step)
            if phrased and isinstance(phrased, str):
                proposed_step = phrased.strip()
        except Exception as exc:  # noqa: BLE001 - a phraser failure must degrade to the template, never raise
            print(f"odin_skill_proposal: phraser failed, using template: {exc}", file=sys.stderr)

    skill_lines = skill_text.splitlines(keepends=True)
    idx, heading_text = _choose_target_section(skill_lines, section)

    skill_rel = str(skill_file.relative_to(workspace_root)) if skill_file.is_relative_to(workspace_root) else str(skill_file)
    if idx is None:
        return {
            "ok": True, "principle_slug": slug, "skill_name": skill_name,
            "target_section": None, "proposed_step": proposed_step, "unified_diff": "",
            "rationale": (f"{slug} is a reflection-derived how-to principle. No `##` heading "
                          f"found in {skill_name}/SKILL.md to attach a checklist step - the CEO "
                          f"chooses placement manually."),
        }

    mod_lines = list(skill_lines)
    step_line = proposed_step if proposed_step.endswith("\n") else proposed_step + "\n"
    mod_lines.insert(idx + 1, step_line)
    unified_diff = "".join(difflib.unified_diff(
        skill_lines, mod_lines, fromfile=f"a/{skill_rel}", tofile=f"b/{skill_rel}", n=3))

    return {
        "ok": True,
        "principle_slug": slug,
        "skill_name": skill_name,
        "target_section": heading_text,
        "proposed_step": proposed_step,
        "unified_diff": unified_diff,
        "rationale": (f"{slug} is a reflection-derived how-to principle (Application section "
                      f"present, matured from episodes). Proposed as a checklist step under "
                      f'"{heading_text}" in {skill_name}/SKILL.md. Proposal only - apply by hand.'),
    }


if __name__ == "__main__":  # smoke
    import json as _json
    r = build_proposal("gate-product-exposure-on-signed-mnda", "proposal")
    print(_json.dumps({k: (v[:200] if isinstance(v, str) else v) for k, v in r.items()}, indent=2))
