"""Four tools whose one-line summary claimed more than their method established.

None of these is a logic bug. Each computes the right numbers and then prints a
sentence the numbers do not support, which is the failure `.claude/rules/
scope-claims.md` exists to stop: a measurement that over-claims is trusted, acted
on, and quoted back later as established fact.

* ``prime-health-parallel.run_memory_health`` printed
  ``Memory: N files, L/200 lines. All healthy.`` while never reading the
  ``over_budget`` flag its own helper returns. The number that refutes the claim
  sat in the same sentence, two words to its left.
  ``scripts/memory-hygiene.py`` reads that field from that helper in five places,
  so the deep tool was honest and the panel the operator sees at EVERY session
  start was not.

* ``generate-dashboard`` printed ``Freshness: all current`` whenever the RED
  count was zero. ``collect_freshness`` returns four health values, and both
  YELLOW (8-14 days) and GRAY (no ``Last verified:`` marker, or an impossible
  date) reported as current. Gray is the worse of the two: nothing about that
  file was measured at all.

* ``regenerate-docs-html.build_search_index`` stored the first 1600 characters of
  each section and said nothing about the rest. Measured 2026-08-27 on the live
  index: 51 of 506 sections were cut, so a tenth of the site's prose could not be
  found by searching for a phrase inside it.

* ``inbox-pulse-report._tier_table_rows`` cut its tables at 50 rows under a
  heading that carries no count, so a truncated table read as the complete set.

Run: python3 -m pytest tests/test_four_summaries_that_named_a_coverage_they_never_had.py
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.memory_health import (  # noqa: E402
    MEMORY_BUDGET_LINES,
    compute_memory_defects as _REAL_DEFECTS,
)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


prime = _load("prime_health_summary", "scripts/prime-health-parallel.py")
docs = _load("regen_docs_summary", "scripts/regenerate-docs-html.py")
pulse = _load("inbox_pulse_summary", "scripts/inbox-pulse-report.py")


# ============================================================
# The memory panel that printed a budget and never compared to it
# ============================================================

def _memory_dir(tmp_path: Path, *, index_lines: int) -> Path:
    """A memory directory whose MEMORY.md has exactly `index_lines` lines and
    references every fact file, so ORPHANS and STALE stay out of the way and the
    only thing under test is the budget."""
    d = tmp_path / "memory"
    d.mkdir(parents=True)
    (d / "fact.md").write_text("a fact\n", encoding="utf-8")
    body = ["- [Fact](fact.md) - hook"]
    body += [f"- filler line {i}" for i in range(index_lines - 1)]
    (d / "MEMORY.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    return d


def _run_memory(monkeypatch, memory_dir: Path) -> dict:
    """Point `run_memory_health` at `memory_dir` by patching the resolver it
    uses, rather than by building a fake ~/.claude tree."""
    # `_REAL_DEFECTS` is captured once at import, NOT re-read here. Reading
    # `mh.compute_memory_defects` inside this helper made a second call in the
    # same test patch over the FIRST call's lambda, so it forwarded to the first
    # directory and the boundary test compared one corpus against itself.
    monkeypatch.setattr(
        "scripts.utils.memory_health.compute_memory_defects",
        lambda _d=None: _REAL_DEFECTS(memory_dir),
    )
    return prime.run_memory_health(ROOT)


def test_an_over_budget_index_is_not_reported_as_healthy(tmp_path, monkeypatch):
    """The defect, as the operator met it at session start."""
    d = _memory_dir(tmp_path, index_lines=MEMORY_BUDGET_LINES + 40)

    out = _run_memory(monkeypatch, d)["output"]

    assert "All healthy" not in out
    assert "over its" in out and str(MEMORY_BUDGET_LINES) in out


def test_an_index_inside_its_budget_is_still_healthy(tmp_path, monkeypatch):
    """The negative case. A panel that never says healthy is not a panel."""
    d = _memory_dir(tmp_path, index_lines=10)

    out = _run_memory(monkeypatch, d)["output"]

    assert "All healthy" in out


def test_the_budget_boundary_is_over_not_at(tmp_path, monkeypatch):
    """The line ON the bound, which is where an off-by-one lives. The helper
    computes `lines > MEMORY_BUDGET_LINES`, so exactly the budget is fine."""
    at = _run_memory(monkeypatch, _memory_dir(tmp_path / "a", index_lines=MEMORY_BUDGET_LINES))
    over = _run_memory(
        monkeypatch, _memory_dir(tmp_path / "b", index_lines=MEMORY_BUDGET_LINES + 1))

    assert at["over_budget"] is False
    assert over["over_budget"] is True


def test_the_printed_budget_is_the_constant_not_a_literal(tmp_path, monkeypatch):
    """A hardcoded `/200` beside a flag computed from the constant is how the
    number and the verdict came apart. Moving the constant must move the line."""
    # There used to be a second patch here:
    #
    #     monkeypatch.setattr(prime, "MEMORY_BUDGET_LINES", 3, raising=False)
    #
    # It bound a stranger. `scripts/prime-health-parallel.py` imports the
    # constant INSIDE `run_memory_health` (`from scripts.utils.memory_health
    # import MEMORY_BUDGET_LINES, ...`), so the module carries no attribute of
    # that name -- MEASURED 2026-08-31: `hasattr(prime, "MEMORY_BUDGET_LINES")`
    # is False. `raising=False` turned "this name does not exist" into a silent
    # new attribute nothing reads. The single patch below is what actually
    # reaches the function-local import, and the assertion at the bottom of this
    # test is what proves it: `/3 lines` can only appear if the panel resolved
    # the constant at call time.
    monkeypatch.setattr("scripts.utils.memory_health.MEMORY_BUDGET_LINES", 3)
    assert not hasattr(prime, "MEMORY_BUDGET_LINES"), (
        "prime-health-parallel gained a module-level MEMORY_BUDGET_LINES. It "
        "would shadow the function-local import this test patches, and the "
        "patch above would stop reaching the code under test."
    )
    d = _memory_dir(tmp_path, index_lines=10)

    out = _run_memory(monkeypatch, d)["output"]

    assert "/3 lines" in out
    assert "/200 lines" not in out


def test_the_panel_publishes_the_flag_it_now_reads(tmp_path, monkeypatch):
    """A caller that wants the state without parsing English must be able to."""
    out = _run_memory(monkeypatch, _memory_dir(tmp_path, index_lines=MEMORY_BUDGET_LINES + 5))

    assert out["over_budget"] is True


# ============================================================
# The dashboard that called three states one word
# ============================================================

dash = _load("dashboard_summary", "scripts/generate-dashboard.py")


@pytest.mark.parametrize("health,expected", [
    ("green", "all 1 files current"),
    ("yellow", "1 ageing of 1 files"),
    ("gray", "1 with no readable marker of 1 files"),
    ("red", "1 stale of 1 files"),
])
def test_every_health_band_reaches_the_summary(health, expected):
    """The whole defect in one table: three of these four used to print
    "all current"."""
    assert dash.freshness_summary([{"name": "x", "health": health}]) == expected


def test_a_mixed_set_names_every_band_it_found():
    """Bands are not exclusive. Reporting only the worst one is the same defect
    one step smaller."""
    rows = [{"name": "a", "health": "red"}, {"name": "b", "health": "yellow"},
            {"name": "c", "health": "gray"}, {"name": "d", "health": "green"}]

    out = dash.freshness_summary(rows)

    assert out == "1 stale, 1 ageing, 1 with no readable marker of 4 files"


def test_an_all_green_set_is_the_only_way_to_read_current():
    rows = [{"name": n, "health": "green"} for n in "abc"]

    assert dash.freshness_summary(rows) == "all 3 files current"


def test_main_calls_the_shared_summary_rather_than_a_copy_of_it():
    """The summary lived inline in `main()` and a test could only reproduce it,
    so the test and the script were free to drift apart while both stayed green.

    Asked of the PARSE TREE, and of `main` specifically. This test used to patch
    `dash.freshness_summary`, print through the patched attribute IN THE TEST
    BODY, and assert it saw the sentinel -- which measures the monkeypatch, not
    the script: `main` was never called and could not have been, since it drives
    a dozen collectors over the live workspace. Its docstring claimed "if `main`
    ever stops using the function, this stops seeing the sentinel", and that was
    simply false. What remained was a substring search over the whole file, and
    a substring search is satisfied by a comment.

    MEASURED 2026-09-01: replacing the call in `main` with
    `print(f"  Freshness: {len(freshness)} files")` and leaving
    `# was freshness_summary(freshness)` beside it kept both source assertions
    green while the panel stopped reporting bands entirely. Comments are not in
    the parse tree, so the check below fails on exactly that edit.
    """
    tree = ast.parse((ROOT / "scripts" / "generate-dashboard.py").read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")

    calls = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "freshness_summary"]

    assert len(calls) == 1, (
        "main() no longer calls freshness_summary exactly once, so the terminal "
        "panel is back to a copy of the logic that can drift from the tested one"
    )
    # And no string main evaluates may state a freshness verdict of its own: the
    # collapsed "all current" is the sentence the function exists to replace.
    verdicts = [n.value for n in ast.walk(main)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "Freshness: all current" in n.value]
    assert verdicts == [], verdicts


def test_the_ast_probe_can_actually_fail(tmp_path):
    """The control for the test above. A `next(...)` that never finds `main`, or
    a walk that never finds a call, would make it pass over anything."""
    tree = ast.parse("def main():\n    print(f'  Freshness: {len(x)} files')\n"
                     "    # freshness_summary(freshness)\n")
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "freshness_summary"]

    assert calls == [], "the comment was read as a call"


# ============================================================
# The search index that cut a tenth of the site in silence
# ============================================================

def _page(heading: str, body: str) -> str:
    """The extractor only reads inside `<main class="content">`, so a page
    without that exact wrapper yields no sections at all. Two drafts of these
    tests got that wrong and passed anyway, which is why the pin below exists."""
    return (f"<html><head><title>P</title></head><body><main class=\"content\">"
            f"<h2 id=\"s\">{heading}</h2><p>{body}</p></main></body></html>")


def test_the_page_fixture_really_produces_a_section():
    """Pins every test below. A fixture the extractor ignores makes an empty
    section list, and `all(...)` over an empty list passes."""
    _title, sections = docs._extract_sections(_page("H", "body text"), "p")

    assert len(sections) == 1
    assert sections[0]["heading"] == "H"


def test_a_cut_section_is_flagged_as_truncated():
    long_body = "word " * (docs.SEARCH_TEXT_CAP // 2)
    _title, sections = docs._extract_sections(_page("H", long_body), "p")

    assert sections[0]["truncated"] is True
    assert len(sections[0]["text"]) == docs.SEARCH_TEXT_CAP


def test_a_short_section_is_not_flagged():
    _title, sections = docs._extract_sections(_page("H", "short body"), "p")

    assert sections[0]["truncated"] is False


def test_a_section_exactly_at_the_cap_is_not_called_truncated():
    """The boundary that a length check after the slice cannot see.

    `len(stored) == SEARCH_TEXT_CAP` is true for both a section cut at the cap
    and one that happens to be exactly that long, so the flag is decided from the
    full text before the slice.
    """
    exact = "x" * docs.SEARCH_TEXT_CAP
    _title, sections = docs._extract_sections(_page("H", exact), "p")

    assert len(sections[0]["text"]) == docs.SEARCH_TEXT_CAP
    assert sections[0]["truncated"] is False


def test_one_char_past_the_cap_is_truncated():
    over = "x" * (docs.SEARCH_TEXT_CAP + 1)
    _title, sections = docs._extract_sections(_page("H", over), "p")

    assert sections[0]["truncated"] is True


def test_the_build_reports_the_number_of_cut_sections(tmp_path, monkeypatch, capsys):
    """The whole point: the count reaches a human. A build that cuts a tenth of
    the corpus and prints only a section total has told its reader the index is
    complete."""
    site = tmp_path / "docs"
    site.mkdir()
    (site / "a.html").write_text(_page("Long", "word " * docs.SEARCH_TEXT_CAP), encoding="utf-8")
    (site / "b.html").write_text(_page("Short", "tiny"), encoding="utf-8")
    monkeypatch.setattr(docs, "SITE_DIR", site)
    monkeypatch.setattr(docs, "SEARCH_INDEX_PATH", tmp_path / "out" / "search-index.json")

    docs.build_search_index(quiet=False)

    out = capsys.readouterr().out
    assert "1 of 2 sections were cut" in out
    assert "NOT searchable" in out


def test_the_build_says_nothing_when_nothing_was_cut(tmp_path, monkeypatch, capsys):
    site = tmp_path / "docs"
    site.mkdir()
    (site / "a.html").write_text(_page("Short", "tiny"), encoding="utf-8")
    monkeypatch.setattr(docs, "SITE_DIR", site)
    monkeypatch.setattr(docs, "SEARCH_INDEX_PATH", tmp_path / "out" / "search-index.json")

    docs.build_search_index(quiet=False)

    assert "were cut" not in capsys.readouterr().out


def test_the_truncated_flag_never_reaches_the_shipped_index(tmp_path, monkeypatch):
    """The index is downloaded by every visitor to the public docs site. A new
    key per record would grow it for no reader; the flag is a build-time signal
    only."""
    import json

    site = tmp_path / "docs"
    site.mkdir()
    (site / "a.html").write_text(_page("Long", "word " * docs.SEARCH_TEXT_CAP), encoding="utf-8")
    out_path = tmp_path / "out" / "search-index.json"
    monkeypatch.setattr(docs, "SITE_DIR", site)
    monkeypatch.setattr(docs, "SEARCH_INDEX_PATH", out_path)

    docs.build_search_index(quiet=True)

    records = json.loads(out_path.read_text(encoding="utf-8"))
    assert records
    assert all("truncated" not in r for r in records)
    assert set(records[0]) == {"u", "a", "p", "h", "t"}


# ============================================================
# The report table that ended without saying it had
# ============================================================

def _entries(n: int) -> list[dict]:
    return [{"ts": f"2026-08-27T00:{i:02d}:00", "sender_domain": f"d{i}.example",
             "weight": n - i} for i in range(n)]


def test_a_cut_table_says_how_many_rows_it_dropped():
    body = pulse._tier_table_rows(_entries(60), max_rows=50)
    lines = body.splitlines()

    assert len(lines) == 51
    assert "10 more row(s)" in lines[-1]
    assert lines[-1].startswith("|") and lines[-1].endswith("|")


def test_an_uncut_table_gains_no_note():
    body = pulse._tier_table_rows(_entries(5), max_rows=50)

    assert len(body.splitlines()) == 5
    assert "more row(s)" not in body


def test_a_table_exactly_at_the_cap_gains_no_note():
    """The row ON the bound. 50 of 50 shown is not a truncation."""
    body = pulse._tier_table_rows(_entries(50), max_rows=50)

    assert len(body.splitlines()) == 50
    assert "more row(s)" not in body


def test_one_row_past_the_cap_is_reported():
    body = pulse._tier_table_rows(_entries(51), max_rows=50)

    assert "1 more row(s)" in body


def test_the_note_names_the_rows_that_went_not_just_the_count():
    """"10 more" tells a reader nothing about WHICH ten. The table is sorted by
    weight descending, so the dropped rows are the lowest-weighted ones, and
    saying so is what makes the note usable."""
    body = pulse._tier_table_rows(_entries(60), max_rows=50)

    assert "by weight" in body.splitlines()[-1]


def test_the_rows_that_survive_the_cut_are_the_heaviest_ones():
    """The claim the note makes, asserted as ordering rather than as a phrase.

    The test above checks that the note SAYS "by weight". Nothing checked that
    it is true. MEASURED 2026-09-01: flipping the sort to `reverse=False` left
    every test over this function green while the table showed the fifty
    LIGHTEST rows and the note went on telling the reader the dropped ones were
    the lowest by weight. A summary naming a coverage it does not have is this
    module's whole subject, so the note has to be checked against the rows.
    """
    # SCRAMBLED on the way in. `_entries` emits weight 60 down to 1 already in
    # descending order, so a fixture built from it cannot tell a working sort
    # from no sort at all: MEASURED 2026-09-01, replacing the whole `sorted(...)`
    # with `list(entries)` passed against the plain helper. The interleave below
    # puts the lightest rows first, so arrival order and weight order disagree
    # on the very first row.
    plain = _entries(60)
    scrambled = plain[30:][::-1] + plain[:30][::-1]
    assert [int(e["weight"]) for e in scrambled][:3] == [1, 2, 3], (
        "the scramble stopped scrambling; this test is back to a pre-sorted input")

    body = pulse._tier_table_rows(scrambled, max_rows=50)
    rows = body.splitlines()[:-1]           # everything but the note
    weights = [int(r.split("|")[3].strip()) for r in rows]

    assert len(weights) == 50
    assert weights == sorted(weights, reverse=True), "the table is not weight-descending"
    # `_entries(60)` runs weight 60 down to 1, so the ten that went are 10..1.
    assert weights[0] == 60
    assert weights[-1] == 11, "a lighter row survived the cut than one that was dropped"


def test_an_empty_table_is_still_empty():
    assert pulse._tier_table_rows([], max_rows=50) == ""
