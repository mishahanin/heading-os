"""Tests for scripts/generate-skill-router.py (F-5.1).

Golden-file on a fixture skills tree, idempotency (write -> check green),
marker-preservation (text outside the markers survives a --write), missing-block
failure with the offending path, and pipe round-trip. The generator is loaded via
importlib (its filename is kebab-case) and its module-level SKILLS_DIR / ROUTER_FILE
globals are monkeypatched onto a tmp fixture so no test touches the real tree.
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
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "SKILLS_DIR", skills)
    monkeypatch.setattr(gen, "ROUTER_FILE", router)
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
    assert gen.cmd_write(rows) == 0
    # Second write is a no-op; check is green.
    assert gen.cmd_check(rows) == 0
    text = router.read_text(encoding="utf-8")
    assert "| `/alpha` |" in text and "| `/bravo` |" in text
    assert "STALE CONTENT" not in text


def test_marker_preservation(fixture_tree):
    skills, router = fixture_tree
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    rows, _ = gen.load_routing_rows()
    gen.cmd_write(rows)
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
    gen.cmd_write(rows)
    text = router.read_text(encoding="utf-8")
    assert r"--mode={a\|b\|c}" in text  # escaped in the rendered table
    assert gen.cmd_check(rows) == 0     # and idempotent


def test_already_escaped_pipe_not_double_escaped(fixture_tree):
    skills, router = fixture_tree
    _write_skill(
        skills, "flagskill",
        "x-heading-routing:\n  category: Operations\n  triggers:\n"
        "    - 'flags: --mode={a\\|b}'\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: manual\n",
    )
    rows, _ = gen.load_routing_rows()
    gen.cmd_write(rows)
    text = router.read_text(encoding="utf-8")
    assert r"--mode={a\|b}" in text
    assert r"--mode={a\\|b}" not in text  # no double escaping


def test_split_by_category_stub_exits_2(fixture_tree, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["generate-skill-router.py", "--split-by-category"])
    assert gen.main() == 2


def test_missing_markers_errors(tmp_path, monkeypatch):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    router = tmp_path / "skill-router.md"
    router.write_text("# Skill Router\n\nno markers here\n", encoding="utf-8")
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "SKILLS_DIR", skills)
    monkeypatch.setattr(gen, "ROUTER_FILE", router)
    _write_skill(skills, "alpha", "x-heading-routing:\n  category: Intel\n  triggers:\n    - a\n  exclusions:\n    - N/A\n  compound: \"No\"\n  router: auto\n")
    rows, _ = gen.load_routing_rows()
    assert gen.cmd_check(rows) == 2  # markers absent -> ValueError -> exit 2
