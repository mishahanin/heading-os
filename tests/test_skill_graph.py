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
market-brief,intel,,,outputs/intelligence,datastore
"""

# `market-brief` is the near-miss the prefix rule exists to reject:
# `outputs/intel` is a SUBSTRING of `outputs/intelligence` and not a path prefix
# of it. Added 2026-09-01 after `if produces and (p == produces or
# p.startswith(produces + "/"))` was replaced with `if produces and produces in
# p` and this file stayed green - the fixture held no pair of skills whose
# produces_in shared a leading string, so nothing separated the two rules.


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


def test_load_really_strips_whitespace_off_every_cell(tmp_path):
    """The name said "strips"; the assertion above only checked "fills".

    MEASURED 2026-09-01 by deleting the `.strip()` from `load`: this file stayed
    green at 20 passed. The strip is load-bearing - `_row` lowercases the skill
    cell but does not strip it, so one padded column in the CSV makes `followers`,
    `predecessors` and `show` return nothing for that skill with no error at all.
    """
    f = tmp_path / "padded.csv"
    f.write_text(
        "skill,phase,preceded_by,followed_by,produces_in,consumes_from\n"
        "  osint  , intel ,, competitor-intel | deal-strategy , outputs/intel , datastore \n",
        encoding="utf-8")
    rows = skill_graph.load(f)

    assert rows[0]["skill"] == "osint"
    assert rows[0]["phase"] == "intel"
    assert rows[0]["produces_in"] == "outputs/intel"
    # And the padding does not survive into the answers the CLI prints.
    assert skill_graph.followers(rows, "osint") == ["competitor-intel", "deal-strategy"]
    assert skill_graph.by_output_dir(rows, "outputs/intel/x.md") == ["osint"]


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
    # outputs/intel is shared by osint + competitor-intel. The ORDER is the
    # tiebreak `t[1]` in the comparator: equal prefix lengths, alphabetical.
    # `set(...)` hid it, and the fixture lists osint first - the reverse of
    # alphabetical - so dropping the tiebreak changed nothing visible while
    # by_output_dir stopped being deterministic across catalog reorderings.
    # /next relies on it to name the producing skill for an output path.
    skills = skill_graph.by_output_dir(rows, "outputs/intel/osint/2026-06-04_osint_exampletelco.md")
    assert skills == ["competitor-intel", "osint"], skills


def test_by_output_dir_orders_most_specific_first(rows):
    # implement (outputs/operations/implement) is a deeper prefix than ops-parent
    # (outputs/operations); the docstring promises most-specific-first ordering.
    order = skill_graph.by_output_dir(rows, "outputs/operations/implement/_trajectory_x.jsonl")
    assert order == ["implement", "ops-parent"]


def test_the_order_comes_from_the_comparator_not_the_csv_row_order(tmp_path):
    """`load` preserves CSV order, and the shipped fixture happens to list the
    deeper skill first. So the asserted order was already the PRE-SORT order,
    and deleting `matches.sort(...)` entirely left both order tests green.

    Feeding the same rows in every order and demanding one answer is what makes
    the comparator the thing under test. Reversing the two rows in the fixture
    used to change the test result while production code stood still, which is
    the tell that row order was being measured.
    """
    import itertools

    header = "skill,phase,preceded_by,followed_by,produces_in,consumes_from\n"
    parent = "ops-parent,operations,,,outputs/operations,\n"
    child = "implement,operations,create-plan,evaluate,outputs/operations/implement,plans\n"
    other = "osint,intel,,,outputs/intel,datastore\n"

    seen = set()
    for perm in itertools.permutations([parent, child, other]):
        f = tmp_path / "skill-graph.csv"
        f.write_text(header + "".join(perm), encoding="utf-8")
        got = skill_graph.by_output_dir(
            skill_graph.load(f), "outputs/operations/implement/_trajectory_x.jsonl")
        seen.add(tuple(got))
    assert seen == {("implement", "ops-parent")}, (
        f"by_output_dir answers differently depending on CSV row order: {seen}"
    )


def test_the_tiebreak_is_alphabetical_whatever_the_csv_order(tmp_path):
    """The secondary key `t[1]`, pinned the same way. Two skills at the same
    prefix depth must come back in one fixed order, or the same output path
    names a different producing skill after an unrelated catalog edit."""
    import itertools

    header = "skill,phase,preceded_by,followed_by,produces_in,consumes_from\n"
    zulu = "zulu-skill,intel,,,outputs/intel,datastore\n"
    alpha = "alpha-skill,intel,,,outputs/intel,datastore\n"

    seen = set()
    for perm in itertools.permutations([zulu, alpha]):
        f = tmp_path / "skill-graph.csv"
        f.write_text(header + "".join(perm), encoding="utf-8")
        seen.add(tuple(skill_graph.by_output_dir(
            skill_graph.load(f), "outputs/intel/x.md")))
    assert seen == {("alpha-skill", "zulu-skill")}, (
        f"equal-depth matches are not ordered deterministically: {seen}"
    )


def test_by_output_dir_no_match_returns_empty(rows):
    assert skill_graph.by_output_dir(rows, "outputs/nowhere/file.md") == []


def test_by_output_dir_ignores_blank_produces_in(rows):
    # 'lonely' has empty produces_in and must never match any path
    assert "lonely" not in skill_graph.by_output_dir(rows, "outputs/intel/x.md")


@pytest.mark.parametrize("path", ["", "/", "  ", "///"])
def test_a_blank_path_matches_no_skill_at_all(rows, path):
    """The clause `test_by_output_dir_ignores_blank_produces_in` names, reached.

    That test asks about a blank `produces_in` using a NON-blank path, and on a
    non-blank path the `produces and ...` guard is unreachable: `"" == p` is
    already False and `p.startswith("/")` is already False. So the guard could be
    deleted with the file green - MEASURED 2026-09-01, 20 passed.

    A blank path is where it bites. `p` normalizes to `""`, `p == produces` is
    `"" == ""`, and every skill with no produces_in comes back as the producer of
    a path that names nothing. `/next` reads this answer to say which skill wrote
    a file, so a blank input must produce no claim rather than a wrong one.
    """
    assert skill_graph.by_output_dir(rows, path) == []


def test_produces_in_must_be_a_path_prefix_not_a_substring(rows):
    """`outputs/intel` is a substring of `outputs/intelligence` and not a prefix.

    MEASURED 2026-09-01 with the prefix test replaced by `produces in p`: green.
    The fixture had no pair of skills sharing a leading string, so nothing in it
    could tell the two rules apart. `market-brief` is now that pair.
    """
    assert skill_graph.by_output_dir(rows, "outputs/intelligence/x.md") == ["market-brief"]
    # And the near-miss does not leak the other way either.
    assert "market-brief" not in skill_graph.by_output_dir(rows, "outputs/intel/x.md")
    # A prefix that stops mid-segment is not a match: `outputs/inte` names nothing.
    assert skill_graph.by_output_dir(rows, "outputs/intelx/y.md") == []


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
