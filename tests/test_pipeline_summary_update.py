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
