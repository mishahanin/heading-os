"""Tests for scripts/elicit.py — the elicitation-catalog accessor.

The spine under test: the catalog loads from the shipped CSV, the cheap entry points
(categories/list/show/random) return the right shape, and `list` refuses to dump the
whole catalog without an explicit --category or --all (the no-implicit-bulk invariant).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import elicit  # noqa: E402

CSV = Path(__file__).resolve().parent.parent / "reference" / "elicitation-methods.csv"


@pytest.fixture
def rows():
    return elicit.load(CSV)


def run(argv, capsys):
    code = elicit.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- catalog loads -------------------------------------------------------

def test_catalog_file_exists():
    assert CSV.is_file()


def test_load_returns_rows_with_all_fields(rows):
    assert len(rows) > 0
    for r in rows:
        for field in elicit.FIELDS:
            assert field in r
        assert r["method_name"]
        assert r["description"]


# --- categories ----------------------------------------------------------

def test_categories_counts_sum_to_total(rows):
    cats = elicit.categories(rows)
    assert sum(n for _, n in cats) == len(rows)


def test_categories_command_text(capsys):
    code, out, _ = run(["categories"], capsys)
    assert code == 0
    assert "core" in out


def test_categories_json_shape(capsys):
    code, out, _ = run(["categories", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert all("category" in d and "count" in d for d in data)


# --- list filters --------------------------------------------------------

def test_list_filters_by_category(rows, capsys):
    code, out, _ = run(["list", "--category", "risk", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert data
    assert all(d["category"] == "risk" for d in data)


def test_list_refuses_bare_invocation(capsys):
    code, _, err = run(["list"], capsys)
    assert code == 2
    assert "needs --category" in err


def test_list_all_returns_full_catalog(rows, capsys):
    code, out, _ = run(["list", "--all", "--json"], capsys)
    assert code == 0
    assert len(json.loads(out)) == len(rows)


# --- show ----------------------------------------------------------------

def test_show_resolves_known_method(rows, capsys):
    name = rows[0]["method_name"]
    code, out, _ = run(["show", name, "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert data[0]["method_name"] == name
    assert "output_pattern" in data[0]


def test_show_reports_missing_to_stderr(capsys):
    code, _, err = run(["show", "No Such Method Ever"], capsys)
    assert code == 1
    assert "not found" in err


# --- random --------------------------------------------------------------

def test_random_clamps_n_to_pool(rows, capsys):
    code, out, _ = run(["random", "-n", "9999", "--json"], capsys)
    assert code == 0
    assert len(json.loads(out)) == len(rows)


def test_random_within_category(capsys):
    code, out, _ = run(["random", "--category", "core", "-n", "2", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert all(d["category"] == "core" for d in data)


# --- file errors ---------------------------------------------------------

def test_missing_file_exits_2(capsys, tmp_path):
    code, _, err = run(["--file", str(tmp_path / "nope.csv"), "categories"], capsys)
    assert code == 2
    assert "not found" in err
