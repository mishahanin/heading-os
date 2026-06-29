"""Tests for scripts/skill_graph.py — the skill-relationship catalog accessor.

The spine under test: the catalog loads from the shipped CSV with all fields, the edge
lookups (followers/predecessors) split the `|`-delimited cells, and by_output_dir maps an
output path back to its producing skill(s) most-specific-first. Edge cases (a path under a
shared subdir maps to several skills; an unknown skill returns []) are asserted against a
small fixture CSV so the test does not couple to the live catalog's exact edges.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import skill_graph  # noqa: E402

SHIPPED = Path(__file__).resolve().parent.parent / "reference" / "skill-graph.csv"

FIXTURE = """skill,phase,preceded_by,followed_by,produces_in,consumes_from
osint,intel,,competitor-intel|deal-strategy,outputs/intel,datastore
competitor-intel,intel,osint,deal-strategy,outputs/intel,datastore
deal-strategy,strategy,osint|competitor-intel,proposal|partnership-doc,outputs/negotiations,crm
create-plan,operations,deep-think,implement,plans,
implement,operations,create-plan,evaluate|scrutinize,outputs/operations/implement,plans
ops-parent,operations,,,outputs/operations,
lonely,operations,,,,
"""


@pytest.fixture
def rows(tmp_path):
    f = tmp_path / "skill-graph.csv"
    f.write_text(FIXTURE, encoding="utf-8")
    return skill_graph.load(f)


def run(argv, capsys):
    code = skill_graph.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- catalog loads -------------------------------------------------------

def test_shipped_catalog_exists():
    assert SHIPPED.is_file()


def test_shipped_catalog_loads_with_all_fields():
    rows = skill_graph.load(SHIPPED)
    assert len(rows) > 0
    for r in rows:
        for field in skill_graph.FIELDS:
            assert field in r
        assert r["skill"]


def test_load_strips_and_fills(rows):
    assert all(set(skill_graph.FIELDS) <= set(r) for r in rows)


# --- followers / predecessors -------------------------------------------

def test_followers_splits_pipe(rows):
    assert skill_graph.followers(rows, "osint") == ["competitor-intel", "deal-strategy"]


def test_followers_case_insensitive(rows):
    assert skill_graph.followers(rows, "OSINT") == ["competitor-intel", "deal-strategy"]


def test_predecessors_splits_pipe(rows):
    assert skill_graph.predecessors(rows, "deal-strategy") == ["osint", "competitor-intel"]


def test_unknown_skill_returns_empty(rows):
    assert skill_graph.followers(rows, "no-such-skill") == []


def test_empty_edge_returns_empty(rows):
    assert skill_graph.followers(rows, "lonely") == []
    assert skill_graph.predecessors(rows, "lonely") == []


# --- by_output_dir -------------------------------------------------------

def test_by_output_dir_matches_prefix(rows):
    skills = skill_graph.by_output_dir(rows, "outputs/operations/implement/_trajectory_x.jsonl")
    assert "implement" in skills


def test_by_output_dir_shared_subdir_returns_all(rows):
    # outputs/intel is shared by osint + competitor-intel
    skills = skill_graph.by_output_dir(rows, "outputs/intel/osint/2026-06-04_osint_exampletelco.md")
    assert set(skills) == {"osint", "competitor-intel"}


def test_by_output_dir_orders_most_specific_first(rows):
    # implement (outputs/operations/implement) is a deeper prefix than ops-parent
    # (outputs/operations); the docstring promises most-specific-first ordering.
    order = skill_graph.by_output_dir(rows, "outputs/operations/implement/_trajectory_x.jsonl")
    assert order == ["implement", "ops-parent"]


def test_by_output_dir_no_match_returns_empty(rows):
    assert skill_graph.by_output_dir(rows, "outputs/nowhere/file.md") == []


def test_by_output_dir_ignores_blank_produces_in(rows):
    # 'lonely' has empty produces_in and must never match any path
    assert "lonely" not in skill_graph.by_output_dir(rows, "outputs/intel/x.md")


# --- CLI -----------------------------------------------------------------

def test_cli_followers_json(rows, capsys, tmp_path):
    f = tmp_path / "g.csv"
    f.write_text(FIXTURE, encoding="utf-8")
    code, out, _ = run(["--file", str(f), "followers", "osint", "--json"], capsys)
    assert code == 0
    assert json.loads(out) == ["competitor-intel", "deal-strategy"]


def test_cli_show_missing_to_stderr(tmp_path, capsys):
    f = tmp_path / "g.csv"
    f.write_text(FIXTURE, encoding="utf-8")
    code, _, err = run(["--file", str(f), "show", "no-such-skill"], capsys)
    assert code == 1
    assert "not found" in err


def test_cli_missing_file_exits_2(capsys, tmp_path):
    code, _, err = run(["--file", str(tmp_path / "nope.csv"), "followers", "osint"], capsys)
    assert code == 2
    assert "not found" in err
