#!/usr/bin/env python3
"""`docs/EXTENDING.md` quoted two trigger-corpus figures that nothing recounted.

MEASURED 2026-08-29, with the count definition the harness itself uses
(`scripts/utils/router_payload.load_triggers` returns the parsed array, so a
skill's case count is its length):

    tracked .claude/skills/*/triggers.json   ->  70 files
    cases across them                        -> 730 cases

The page said 69 and 710. It also dated both figures to 2026-08-03 in one
clause and asserted them in the present tense in the next, which is the exact
shape that made them safe to ignore: a reader who noticed the date could not
tell whether the number was stale or merely old.

Re-typing today's number only resets the clock, so the figures were made
derived. `scripts/dev/check-readme-numbers.py` already existed to hold the
README front doors against a count collected from CI, and it grew a third
figure rather than a second guard being written beside it. The `readme-numbers`
pre-commit hook now fires on `docs/EXTENDING.md` and on any
`.claude/skills/*/triggers.json`, which is the moment the figure can go wrong.

This file holds three things: that the guard's arithmetic matches the harness's
own reading, that the live page agrees with the tree, and that the guard is
actually wired to the files that invalidate it.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

GUARD_PATH = ROOT / "scripts" / "dev" / "check-readme-numbers.py"
PAGE = ROOT / "docs" / "EXTENDING.md"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("check_readme_numbers_f2", GUARD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_readme_numbers_f2"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tracked_trigger_files() -> list[Path]:
    return tracked_paths((".claude/skills/*/triggers.json",))


# ============================================================
# The predicate, measured on synthetic input in both directions
# ============================================================

_MATCHING = (
    "70 routing-sensitive skills carry `triggers.json`, and they hold 730 cases "
    "between them.\n"
)
_STALE = (
    "69 routing-sensitive skills carry `triggers.json`, and they hold 710 cases "
    "between them.\n"
)
_REWORDED = "The skills carry trigger files, and there are quite a few cases.\n"
_INCONSISTENT = _MATCHING + "Elsewhere: 12 routing-sensitive skills carry the file.\n"


def test_the_predicate_is_silent_when_the_page_agrees_with_the_tree(guard):
    assert guard.trigger_figure_problems(_MATCHING, "page.md", 70, 730) == []


def test_the_predicate_names_both_stale_figures(guard):
    """The measured defect. Both numbers were wrong, and each is reported."""
    problems = guard.trigger_figure_problems(_STALE, "page.md", 70, 730)
    assert len(problems) == 2, problems
    assert "says 69 skills carrying triggers.json, the tree holds 70" in problems[0]
    assert "says 710 trigger cases, the tree holds 730" in problems[1]


def test_the_predicate_names_one_stale_figure_and_leaves_the_other_alone(guard):
    """A guard that reported both whenever either moved would train the reader to
    fix the wrong sentence."""
    problems = guard.trigger_figure_problems(_MATCHING, "page.md", 70, 731)
    assert len(problems) == 1, problems
    assert "trigger cases" in problems[0]


def test_a_reworded_sentence_is_a_failure_and_not_a_pass(guard):
    """The failure mode of every regex-anchored documentation guard: the prose
    moves, the pattern stops matching, and the page reads as checked while
    nothing is checked."""
    problems = guard.trigger_figure_problems(_REWORDED, "page.md", 70, 730)
    assert len(problems) == 2, problems
    assert all("checks nothing" in p for p in problems)


def test_two_disagreeing_figures_on_one_page_are_a_failure(guard):
    problems = guard.trigger_figure_problems(_INCONSISTENT, "page.md", 70, 730)
    assert len(problems) == 1, problems
    assert "inconsistent" in problems[0]
    assert "[12, 70]" in problems[0]


# ============================================================
# The guard's arithmetic is the harness's arithmetic
# ============================================================

def test_the_guard_finds_the_same_files_the_tree_holds(guard):
    """`git ls-files` inside the guard against `tracked_paths` here: two different
    questions to git, which must return the same answer."""
    assert sorted(guard.tracked_trigger_files()) == sorted(_tracked_trigger_files())


def test_the_guard_counts_cases_the_way_the_router_harness_reads_them(guard):
    """`load_triggers` is what `scripts/skill-trigger-test.py` runs, so it defines
    what a "case" is. A guard counting anything else documents a different number
    than the one the harness would report."""
    from scripts.utils.router_payload import load_triggers

    harness_total = sum(
        len(load_triggers(path.parent)) for path in _tracked_trigger_files()
    )
    assert guard.count_trigger_cases(_tracked_trigger_files()) == harness_total


def test_a_triggers_file_that_is_not_an_array_stops_the_guard(guard, tmp_path):
    """`load_triggers` raises on a non-array. Counting it as zero would move the
    documented figure for a reason no reader of the page could see."""
    bad = tmp_path / "triggers.json"
    bad.write_text(json.dumps({"cases": [1, 2, 3]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        guard.count_trigger_cases([bad])


# ============================================================
# The live page
# ============================================================

def test_the_extending_page_agrees_with_the_tree(guard):
    problems, files, cases = guard.check_trigger_figures()
    assert problems == [], problems
    assert (files, cases) == (len(_tracked_trigger_files()),
                              guard.count_trigger_cases(_tracked_trigger_files()))


def test_the_whole_guard_exits_zero_on_the_trigger_figures(guard, tmp_path, monkeypatch):
    """End to end through `main()`, with only the security-test count stubbed:
    that one shells out to pytest and is another guard's subject. A green run
    here is the same run the `readme-numbers` pre-commit hook performs."""
    monkeypatch.setattr(guard, "derive_security_test_count",
                        lambda: guard._extract(guard._SEC_RE,
                                               (ROOT / "README.md").read_text(encoding="utf-8"),
                                               ROOT / "README.md", "security tests"))
    monkeypatch.setattr(sys, "argv", ["check-readme-numbers.py", "--quiet"])
    assert guard.main() == 0


def test_the_page_no_longer_dates_the_figures_instead_of_deriving_them():
    """The old paragraph carried "counted on 2026-08-03" as its warrant. A date is
    not a warrant; it is a note that the number was true once."""
    text = PAGE.read_text(encoding="utf-8")
    # `next(...)` with no default raised StopIteration on a reworded sentence,
    # so the one event this guard exists to catch - the wording moving out
    # from under it, which the sibling
    # `test_a_reworded_sentence_is_a_failure_and_not_a_pass` calls the
    # dangerous case - surfaced as a bare generator traceback with no sentence
    # naming what went wrong.
    sentence = next(
        (line for line in text.splitlines()
         if "routing-sensitive skills carry" in line),
        None,
    )
    assert sentence is not None, (
        f"the figure sentence is gone from {PAGE.name}; this guard anchors on "
        "the phrase 'routing-sensitive skills carry' and has nothing to read")

    # The paragraph, not a fixed 600 characters. A character count is a claim
    # about layout: reflowing the page moves the warrant in or out of the
    # window with no change in meaning, in either direction.
    paragraphs = [p for p in text.split("\n\n") if sentence in p]
    assert len(paragraphs) == 1, (
        f"the figure sentence appears in {len(paragraphs)} paragraphs")
    window = paragraphs[0]
    assert "check-readme-numbers.py" in window, (
        "the figures must name the guard that derives them")
    assert not re.search(r"counted on 20\d\d-\d\d-\d\d", window), window


# ============================================================
# The guard is wired to the files that invalidate it
# ============================================================

def _hook(hook_id: str) -> dict:
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == hook_id:
                return hook
    raise AssertionError(f"no {hook_id!r} hook in .pre-commit-config.yaml")


def test_the_hook_fires_on_the_page_and_on_a_triggers_file():
    """A derived figure whose guard never runs is an asserted figure with extra
    steps. Both sides of the comparison must arm the hook."""
    pattern = re.compile(_hook("readme-numbers")["files"])
    assert pattern.match("docs/EXTENDING.md")
    sample = _tracked_trigger_files()[0].relative_to(ROOT).as_posix()
    assert pattern.match(sample), sample


def test_the_hook_does_not_fire_on_an_unrelated_file():
    """The other direction. A pattern loose enough to match everything would run
    the guard on every commit and teach the operator to skip it."""
    pattern = re.compile(_hook("readme-numbers")["files"])
    assert not pattern.match("scripts/ops-radar.py")
    assert not pattern.match("docs/QUICKSTART.md")


# ============================================================
# The sweep reaches a real corpus
# ============================================================

def test_the_sweep_reaches_a_real_corpus(guard):
    """Green over an empty corpus otherwise: zero files hold zero cases, and a
    page saying 0 and 0 would pass every rule above. 70 files, 730 cases on
    2026-08-29."""
    files = _tracked_trigger_files()
    assert len(files) >= 60, f"only {len(files)} triggers.json files found"
    cases = guard.count_trigger_cases(files)
    assert cases >= 600, f"only {cases} trigger cases read"
    names = {p.parent.name for p in files}
    assert "osint" in names, sorted(names)[:10]
