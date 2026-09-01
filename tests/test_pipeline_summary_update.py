"""`pipeline-summary.py --update` must replace one section, never the tail.

The 2026-08-23 defect: the removal pattern ended with an `\\Z` alternative, so
when neither of the two hard-coded following headings ("## Stage Definitions",
"## Active Deals") was present -- a renamed or reordered section is enough --
the lazy body expanded to end-of-file and the write discarded everything after
"## Pipeline Summary". Silent data loss in the operator's deal pipeline.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "pipeline_summary", ROOT / "scripts" / "pipeline-summary.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["pipeline_summary"] = mod
_spec.loader.exec_module(mod)

_TAIL = "## Government & Regulator Engagements"


def _doc(*sections: str) -> str:
    head = "# Pipeline\n\nFreshness: 2026-08-23\n\n---\n\n"
    return head + "\n\n---\n\n".join(sections) + "\n"


def _replace(content: str, summary: str) -> str:
    """The production replacement, called directly so the test needs no CRM."""
    return mod.replace_summary_block(content, summary)


def test_a_renamed_following_section_does_not_eat_the_tail():
    content = _doc(
        "## Pipeline Summary\n\n| Metric | Value |\n|---|---|\n| Total | 1 |",
        "## Deal Stages\n\nrenamed, so neither hard-coded lookahead matches",
        f"{_TAIL}\n\none row the operator cannot afford to lose",
    )
    out = _replace(content, "## Pipeline Summary\n\nfresh numbers")

    assert "fresh numbers" in out
    assert "## Deal Stages" in out, "the renamed section was deleted"
    assert _TAIL in out, "the tail of pipeline.md was deleted"
    assert "one row the operator cannot afford to lose" in out


def test_the_known_layout_still_replaces_in_place():
    content = _doc(
        "## Pipeline Summary\n\nstale numbers",
        "## Stage Definitions\n\nunchanged",
        "## Active Deals\n\nunchanged",
    )
    out = _replace(content, "## Pipeline Summary\n\nfresh numbers")
    assert "fresh numbers" in out
    assert "stale numbers" not in out
    assert out.count("## Pipeline Summary") == 1
    assert "## Stage Definitions" in out and "## Active Deals" in out


def _rules(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() == "---")


# What `generate_summary` really hands `replace_summary_block`: `"\n".join(lines)`
# over a list whose last element is `""`, so exactly one trailing newline. Every
# fixture below that exercises the SEPARATOR uses this shape, because the
# separator lands immediately after whatever the summary ends with. A fixture
# without the trailing newline glues `---` onto the last word and manufactures a
# failure the generator cannot produce.
_SUMMARY = "## Pipeline Summary\n\n| Metric | Value |\n|---|---|\n| Total | 2 |\n"


def test_the_separator_before_the_next_section_survives_the_replacement():
    """The `---` rule belongs to the section BELOW, so it must be given back.

    `replace_summary_block` walks back over the trailing rule precisely so the
    replaced span stops short of it. Measured 2026-09-01 by removing that
    walk-back (`end = len(stripped)`, no `endswith("---")` arm): every assertion
    in this file stayed green while `--update` deleted the horizontal rule
    between the summary and the section after it, once per run. Nothing counted
    the rules, so nothing noticed them going.
    """
    content = _doc(
        "## Pipeline Summary\n\nstale numbers",
        "## Stage Definitions\n\nunchanged",
        "## Active Deals\n\nunchanged",
    )
    before = _rules(content)
    assert before == 3, f"fixture shape changed; it now carries {before} rules"
    out = _replace(content, _SUMMARY)
    assert _rules(out) == before, (
        f"the replacement consumed a `---` rule: {before} before, "
        f"{_rules(out)} after.\n{out!r}"
    )
    assert "---\n\n## Stage Definitions" in out, (
        "the rule that separates the summary from the next section is gone"
    )


def test_a_second_replacement_over_the_first_output_changes_nothing():
    """Idempotence, which is what a lost separator actually costs.

    One dropped rule is cosmetic; a `--update` that drops one on EVERY run
    erodes the operator's pipeline.md a rule at a time, and a single-pass
    assertion cannot see that. `--update` runs on a cadence, so the second pass
    is the one that reports the damage.
    """
    content = _doc(
        "## Pipeline Summary\n\nstale numbers",
        "## Stage Definitions\n\nunchanged",
        "## Active Deals\n\nunchanged",
    )
    once = _replace(content, _SUMMARY)
    twice = _replace(once, _SUMMARY)
    assert once == twice, "a second --update over the first output changed it"


def test_a_trailing_summary_is_replaced_not_duplicated():
    content = _doc("## Active Deals\n\nrows", "## Pipeline Summary\n\nstale numbers")
    out = _replace(content, "## Pipeline Summary\n\nfresh numbers")
    assert out.count("## Pipeline Summary") == 1
    assert "fresh numbers" in out and "stale numbers" not in out
    assert "## Active Deals" in out


def test_no_summary_section_inserts_without_loss():
    content = _doc("## Active Deals\n\nrows", f"{_TAIL}\n\nrows")
    out = _replace(content, "## Pipeline Summary\n\nfresh numbers")
    assert "fresh numbers" in out
    assert "## Active Deals" in out and _TAIL in out


def test_the_insert_lands_after_the_intro_rule_not_above_the_title():
    """WHERE a first summary is inserted is behaviour, not decoration.

    The insert branch looks for the intro `---` rule and falls back to the top
    of the file only when there is none. Measured 2026-09-01 by forcing that
    fallback (`first_sep = -1`): the summary was prepended ABOVE the `# Pipeline`
    title, and every assertion in this file stayed green because none of them
    asked where it went. A document whose H1 is no longer the first line reads
    as broken to every renderer downstream.
    """
    content = _doc("## Active Deals\n\nrows", f"{_TAIL}\n\nrows")
    out = _replace(content, "## Pipeline Summary\n\nfresh numbers")

    assert out.startswith("# Pipeline\n"), (
        f"the summary was inserted above the document title: {out[:80]!r}")
    assert out.index("Freshness: 2026-08-23") < out.index("## Pipeline Summary"), (
        "the summary was inserted before the intro block it is meant to follow")
    assert out.index("## Pipeline Summary") < out.index("## Active Deals"), (
        "the summary was not inserted at the top of the section list")
