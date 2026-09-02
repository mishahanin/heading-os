"""`label` is rendered inside a backtick code span, and backticks were not refused.

Found by the 2026-08-24 engine audit campaign, verified still present and fixed
2026-09-02.

`scripts/generate-skill-router.py` renders the Skill column as
``| `{label}` | ...`` in both `render_row` (the per-category detail files) and
`render_core_row` (the compact index spliced into the ALWAYS-ON
`.claude/rules/skill-router.md`). A backtick inside `label` closes that span on
its first occurrence, so the cell renders mangled in a rule injected into every
session.

`_as_cell` already refused the two characters that end a markdown table ROW,
newline and carriage return, for exactly this reason: the corruption is
deterministic, so `--check` regenerates it and passes. The backtick, which ends
the code SPAN, was not on the list.

The refusal is scoped to `label` on purpose. Triggers, exclusions and compound
are not inside a code span and backticks there are ordinary house style:
measured on the live corpus 2026-09-02, 30 of the 94 skills carry at least one,
so refusing the character everywhere would fail a third of the tree over
nothing. That scoping is itself pinned below, because a fix that over-reaches
here would be found only by whoever next edits a SKILL.md.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GEN_PATH = ROOT / "scripts" / "generate-skill-router.py"


def _gen():
    spec = importlib.util.spec_from_file_location("router_code_span_gen", GEN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    return _gen()


def _skill(tree: Path, name: str, routing: str) -> None:
    (tree / name).mkdir(parents=True, exist_ok=True)
    (tree / name / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: fixture\n"
        "x-heading-routing:\n"
        f"{routing}"
        "---\n\nBody.\n",
        encoding="utf-8",
    )


# ============================================================
# The defect
# ============================================================

def test_a_backtick_in_the_label_closes_the_skill_cells_code_span(gen):
    """The unit. `_as_cell` must refuse it for the one field that is a code span."""
    with pytest.raises(ValueError) as excinfo:
        gen._as_cell("/foo `bar` [x]", field="x-heading-routing.label", code_span=True)
    assert "backtick" in str(excinfo.value), str(excinfo.value)
    assert "code span" in str(excinfo.value), (
        "the message must say WHY, or the author removes the wrong character"
    )


def test_a_backticked_label_is_reported_as_an_error_not_rendered(gen, tmp_path, monkeypatch):
    """End to end, through the gate's real output path.

    The contract of `load_routing_rows` is a curated `{rel}: {err}` line naming
    the file, which is what `main` prints and CI fails on. A raise from deeper
    down, or a rendered row, would both be wrong.
    """
    tree = tmp_path / "skills"
    tree.mkdir()
    _skill(tree, "broken", '  category: Operations\n  label: "/broken `x`"\n'
                           '  triggers: ["t"]\n  exclusions: ["N/A"]\n'
                           '  compound: "No"\n  router: auto\n')
    monkeypatch.setattr(gen, "SKILLS_DIR", tree)
    monkeypatch.setattr(gen, "ROOT", tmp_path)

    rows, errors = gen.load_routing_rows()
    assert rows == [], "a label with a backtick was rendered into a row"
    assert len(errors) == 1, errors
    assert "broken/SKILL.md" in errors[0] and "backtick" in errors[0], errors[0]


def test_the_generated_label_default_is_checked_too(gen, tmp_path, monkeypatch):
    """`label` defaults to `/{name}`, and `name` falls back to the directory name.

    Both reach the same code span, so checking only an AUTHORED label would leave
    the default as an unguarded second door into the identical corruption.
    """
    tree = tmp_path / "skills"
    tree.mkdir()
    _skill(tree, "odd", '  category: Operations\n  triggers: ["t"]\n'
                        '  exclusions: ["N/A"]\n  compound: "No"\n  router: auto\n')
    (tree / "odd" / "SKILL.md").write_text(
        (tree / "odd" / "SKILL.md").read_text(encoding="utf-8").replace(
            "name: odd", 'name: "odd`x"'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gen, "SKILLS_DIR", tree)
    monkeypatch.setattr(gen, "ROOT", tmp_path)

    rows, errors = gen.load_routing_rows()
    assert rows == [], "a backtick reached the code span through the label default"
    assert errors and "backtick" in errors[0], errors


# ============================================================
# The anchors: the refusal must not spread past the code span
# ============================================================

def test_a_backtick_in_a_trigger_is_still_allowed(gen):
    """Triggers are not in a code span, and the live corpus is full of them:
    `NEVER auto-trigger. Explicit `/backup` only.` is the house phrasing."""
    value = "NEVER auto-trigger. Explicit `/backup` only."
    assert gen._as_cell(value, field="x-heading-routing.triggers") == value


def test_the_live_corpus_still_loads_without_an_error(gen):
    """The regression the tightening could plausibly cause, asked of the real
    tree on purpose. 30 of 94 skills carry a backtick in a trigger or exclusion;
    if the refusal had been added to `FORBIDDEN_IN_CELL` instead, this fails."""
    rows, errors = gen.load_routing_rows()
    assert errors == [], errors
    assert len(rows) > 50, f"the corpus walk found only {len(rows)} skills"
    assert any("`" in t for r in rows for t in r["triggers"]), (
        "no trigger carries a backtick, so this anchor is measuring nothing"
    )
