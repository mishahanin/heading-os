"""Tests for scripts/generate-skill-router.py (F-5.1 + F-5.2 split).

Golden-file on a fixture skills tree, idempotency (split write -> check green),
marker-preservation (text outside the markers survives a write), missing-block
failure with the offending path, and pipe round-trip. F-5.2 adds: split write creates
the compact 2-column core index AND the per-category 4-column detail files; `--check`
detects a category-file drift or a missing file; `--split-by-category` is a synonym of
the default write; and the split is semantics-preserving (on-disk category rows, parsed
as bytes, equal the flat monolith). The generator is loaded via importlib (its filename
is kebab-case) and its module-level SKILLS_DIR / ROUTER_FILE / CATEGORY_FILE_DIR globals
are monkeypatched onto a tmp fixture so no test touches the real tree.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GEN_PATH = ROOT / "scripts" / "generate-skill-router.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("generate_skill_router", GEN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_gen()


def _write_skill(skills_dir: Path, name: str, routing_block: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: \"test {name}\"\n{routing_block}---\n\n# {name}\n\nbody\n",
        encoding="utf-8",
    )


def _router_skeleton() -> str:
    return (
        "# Skill Router\n\nintro text (must be preserved)\n\n"
        "## Skill Registry\n\n"
        f"{gen.MARKER_BEGIN}\n"
        "STALE CONTENT TO BE REPLACED\n"
        f"{gen.MARKER_END}\n\n"
        "## Compound Workflow Triggers\n\ntail text (must be preserved)\n"
    )


@pytest.fixture
def fixture_tree(tmp_path, monkeypatch):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    router = tmp_path / "skill-router.md"
    router.write_text(_router_skeleton(), encoding="utf-8")
    cat_dir = tmp_path / "reference" / "skill-router"
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "SKILLS_DIR", skills)
    monkeypatch.setattr(gen, "ROUTER_FILE", router)
    monkeypatch.setattr(gen, "CATEGORY_FILE_DIR", cat_dir)
    return skills, router


# --- pure-function golden ------------------------------------------------------

def test_render_row_backtick_wrapped_and_joined():
    row = {
        "name": "osint",
        "category": "Intel",
        "label": "/osint",
        "triggers": ["investigate", "research"],
        "exclusions": ['"validate" -> /validate', "market -> /market-brief"],
        "compound": "Yes: Deal Intel",
        "router": "auto",
    }
    assert gen.render_row(row) == (
        '| `/osint` | investigate, research | '
        '"validate" -> /validate; market -> /market-brief | Yes: Deal Intel |'
    )


def test_render_registry_orders_by_name_within_category():
    rows = [
        {"name": "zebra", "category": "Intel", "label": "/zebra", "triggers": ["z"], "exclusions": ["N/A"], "compound": "No", "router": "auto"},
        {"name": "alpha", "category": "Intel", "label": "/alpha", "triggers": ["a"], "exclusions": ["N/A"], "compound": "No", "router": "auto"},
    ]
    region = gen.render_registry(rows)
    assert region.index("| `/alpha` |") < region.index("| `/zebra` |")
    # Every category header is emitted, in the fixed order.
    positions = [region.index(f"### {c}") for c in gen.CATEGORY_ORDER]
    assert positions == sorted(positions)


# --- write / check on the fixture tree ----------------------------------------

def test_write_then_check_is_idempotent(fixture_tree):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    _write_skill(skills, "bravo", "x-heading-routing:\n  category: CRM\n  triggers:\n    - b\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")

    rows, errors = gen.load_routing_rows()
    assert errors == []
    assert gen.cmd_split_write(rows) == 0
    # Second check is green (both layers idempotent).
    assert gen.cmd_split_check(rows) == 0
    text = router.read_text(encoding="utf-8")
    assert "| `/alpha` |" in text and "| `/bravo` |" in text
    assert "STALE CONTENT" not in text


def test_marker_preservation(fixture_tree):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    rows, _ = gen.load_routing_rows()
    gen.cmd_split_write(rows)
    text = router.read_text(encoding="utf-8")
    assert "intro text (must be preserved)" in text
    assert "tail text (must be preserved)" in text
    assert "## Skill Registry" in text
    assert "## Compound Workflow Triggers" in text


def test_missing_block_fails_with_path(fixture_tree, capsys, monkeypatch):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    # A skill with no x-heading-routing block at all.
    d = skills / "orphan"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: orphan\ndescription: \"no routing\"\n---\n\n# orphan\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["generate-skill-router.py", "--check"])
    rc = gen.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "orphan/SKILL.md" in err
    assert "x-heading-routing" in err


def test_bad_category_fails(fixture_tree):
    skills, _ = fixture_tree
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Nonsense\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    rows, errors = gen.load_routing_rows()
    assert rows == []
    assert any("category" in e for e in errors)


def test_pipe_round_trip(fixture_tree):
    skills, router = fixture_tree
    # A raw pipe in a trigger must render escaped and survive a check.
    _write_skill(
        skills, "flagskill",
        "x-heading-routing:\n  category: Operations\n  triggers:\n"
        "    - 'flags: --mode={a|b|c}'\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: manual\n",
    )
    rows, errors = gen.load_routing_rows()
    assert errors == []
    gen.cmd_split_write(rows)
    text = router.read_text(encoding="utf-8")
    assert r"--mode={a\|b\|c}" in text  # escaped in the core index (triggers stay in-core)
    # And escaped in the category detail layer (flagskill is Operations).
    detail = (gen.CATEGORY_FILE_DIR / "operations.md").read_text(encoding="utf-8")
    assert r"--mode={a\|b\|c}" in detail
    assert gen.cmd_split_check(rows) == 0     # and idempotent across both layers


def test_already_escaped_pipe_not_double_escaped(fixture_tree):
    skills, router = fixture_tree
    _write_skill(
        skills, "flagskill",
        "x-heading-routing:\n  category: Operations\n  triggers:\n"
        "    - 'flags: --mode={a\\|b}'\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: manual\n",
    )
    rows, _ = gen.load_routing_rows()
    gen.cmd_split_write(rows)
    text = router.read_text(encoding="utf-8")
    assert r"--mode={a\|b}" in text
    assert r"--mode={a\\|b}" not in text  # no double escaping


_INTEL = ("x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n"
          "  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
_CRM = ("x-heading-routing:\n  category: CRM\n  triggers:\n    - b\n"
        "  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")


def test_split_by_category_is_synonym_of_default_write(fixture_tree, monkeypatch):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", _INTEL)
    monkeypatch.setattr(sys, "argv", ["generate-skill-router.py", "--split-by-category"])
    assert gen.main() == 0  # no longer a stub; produces the split output
    assert "| `/alpha` |" in router.read_text(encoding="utf-8")
    assert (gen.CATEGORY_FILE_DIR / "intel.md").exists()


def test_split_write_creates_core_index_and_category_files(fixture_tree):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", _INTEL)
    _write_skill(skills, "bravo", _CRM)
    rows, _ = gen.load_routing_rows()
    assert gen.cmd_split_write(rows) == 0
    core = router.read_text(encoding="utf-8")
    # The core index is 2-column; the 4-column header lives only in the category files.
    assert gen.CORE_TABLE_HEADER in core
    assert gen.TABLE_HEADER not in core
    intel = (gen.CATEGORY_FILE_DIR / "intel.md").read_text(encoding="utf-8")
    assert intel.startswith("# Skill Router — Intel")
    assert gen.TABLE_HEADER in intel
    assert "| `/alpha` |" in intel
    assert "| `/bravo` |" in (gen.CATEGORY_FILE_DIR / "crm.md").read_text(encoding="utf-8")


def test_split_check_detects_category_file_drift(fixture_tree):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", _INTEL)
    rows, _ = gen.load_routing_rows()
    gen.cmd_split_write(rows)
    p = gen.CATEGORY_FILE_DIR / "intel.md"
    p.write_text(p.read_text(encoding="utf-8") + "| `/rogue` | x | y | z |\n", encoding="utf-8")
    assert gen.cmd_split_check(rows) == 1


def test_split_check_detects_missing_category_file(fixture_tree):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", _INTEL)
    rows, _ = gen.load_routing_rows()
    gen.cmd_split_write(rows)
    (gen.CATEGORY_FILE_DIR / "intel.md").unlink()
    assert gen.cmd_split_check(rows) == 1


def test_semantics_preserved_on_disk(fixture_tree):
    """M3: PARSE the on-disk category rows (not re-render) and compare to the flat monolith."""
    skills, router = fixture_tree
    _write_skill(skills, "alpha", _INTEL)
    _write_skill(skills, "bravo", _CRM)
    _write_skill(skills, "charlie", _INTEL)
    rows, _ = gen.load_routing_rows()
    gen.cmd_split_write(rows)
    # Reference: the flat monolith row lines (what --flat prints).
    flat_rows = [ln for ln in gen.render_registry(rows).splitlines() if ln.startswith("| `")]
    # Actual: parsed from the on-disk files (bytes), in category-then-name order.
    disk_rows = []
    for cat in gen.CATEGORY_ORDER:
        p = gen.CATEGORY_FILE_DIR / f"{gen.category_slug(cat)}.md"
        if p.exists():
            disk_rows += [ln for ln in p.read_text(encoding="utf-8").splitlines()
                          if ln.startswith("| `")]
    assert disk_rows == flat_rows
    assert len(disk_rows) == 3


def test_missing_markers_errors(tmp_path, monkeypatch):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    router = tmp_path / "skill-router.md"
    router.write_text("# Skill Router\n\nno markers here\n", encoding="utf-8")
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "SKILLS_DIR", skills)
    monkeypatch.setattr(gen, "ROUTER_FILE", router)
    monkeypatch.setattr(gen, "CATEGORY_FILE_DIR", tmp_path / "reference" / "skill-router")
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    rows, _ = gen.load_routing_rows()
    assert gen.cmd_split_check(rows) == 2  # markers absent -> ValueError -> exit 2


# ============================================================
# A non-string trigger must FAIL the gate, not render as a repr
# ============================================================

_COLON_TRIGGER = (
    "x-heading-routing:\n"
    "  category: Operations\n"
    "  triggers:\n"
    "    - Nothing lowers the mode except the operator: the done marker leaves it up\n"
    "  exclusions:\n"
    "    - N/A\n"
    '  compound: "No"\n'
    "  router: manual\n"
)


def _load(skills, tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "SKILLS_DIR", skills)
    return gen.load_routing_rows()


def test_an_unquoted_colon_in_a_trigger_is_an_error_not_a_dict_repr(tmp_path, monkeypatch):
    """The exact 2026-08-20 corruption, in miniature.

    A YAML list item carrying an unquoted `colon space` parses as a MAPPING. The
    generator used to `str()` whatever it got, so the mapping's Python repr -
    braces, quoted key, quoted value - was written into
    `.claude/rules/skill-router.md`, an always-on rule injected into every
    session. Both gates stayed green: `--check` compared a corrupt generation
    against the corrupt file and found them equal.
    """
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    _write_skill(skills, "alpha", _COLON_TRIGGER)

    rows, errors = _load(skills, tmp_path, monkeypatch)

    assert rows == [], "a skill with a malformed trigger must not produce a row"
    assert len(errors) == 1
    assert "not a string" in errors[0]
    assert "unquoted 'colon space'" in errors[0], (
        "the message must name the cause; a bare type error sends the reader "
        "hunting through YAML"
    )


def test_the_same_rule_holds_for_exclusions(tmp_path, monkeypatch):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    _write_skill(skills, "alpha", (
        "x-heading-routing:\n"
        "  category: Operations\n"
        "  triggers:\n"
        "    - fine\n"
        "  exclusions:\n"
        "    - some signal: goes to /other\n"
        '  compound: "No"\n'
        "  router: manual\n"
    ))
    rows, errors = _load(skills, tmp_path, monkeypatch)
    assert rows == []
    assert "exclusions" in errors[0]


def test_a_well_formed_skill_is_unaffected(tmp_path, monkeypatch):
    """The guard must not reject the ordinary case, including a QUOTED colon,
    which is how the sentence is written correctly."""
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    _write_skill(skills, "alpha", (
        "x-heading-routing:\n"
        "  category: Operations\n"
        "  triggers:\n"
        '    - "Nothing lowers it except the operator: the marker leaves it up"\n'
        "    - a second plain trigger\n"
        "  exclusions:\n"
        "    - N/A\n"
        '  compound: "No"\n'
        "  router: manual\n"
    ))
    rows, errors = _load(skills, tmp_path, monkeypatch)
    assert errors == []
    assert len(rows) == 1
    assert rows[0]["triggers"] == [
        "Nothing lowers it except the operator: the marker leaves it up",
        "a second plain trigger",
    ]
