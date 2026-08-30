"""Adjudication: `find_boxes_for_quote` highlights the FIRST match, and that is
not the defect it looks like.

The audit claim was that `pos = concat.find(norm_quote)` locates only the first
occurrence, so a citation about the second occurrence of a quote on a page draws
its highlight over the first one, inside a report whose whole claim is "here is
where it says X".

VERDICT: VOID. No caller can ask about a non-first occurrence, because nothing
in the input distinguishes one occurrence from another.

- There is ONE in-repo call site, `_generate_report_html`, and it passes exactly
  three things: the resolved page's `text_items`, the citation's `quote` string,
  and the DPI. It is already scoped to a single page by `_resolve_parse_file`
  plus the `page_num` walk above it.
- A citation carries `id`, `file`, `page`, `quote`, `relevance`, and nothing
  else. `id` and `relevance` never reach the matcher. Two citations, one about
  the first occurrence and one about the second, are therefore byte-identical in
  every field the matcher sees. "The second occurrence" is not a question the
  function is capable of being asked, so it cannot answer it wrongly.
- The box it does return covers text that genuinely reads the quoted string, so
  the report's claim holds for the card it draws.

What is TRUE and stays open: where a quote occurs N times on a page, only the
first is highlighted and the report says nothing about the other N-1. That is
under-specification in the citation schema, not a wrong box, and closing it
means a producer-side field (an occurrence ordinal, or a character offset) that
the skill would have to emit. Adding the consumer half alone would be dead code.

These tests pin the verdict so the next audit finds the answer instead of
re-deriving the question, and the schema test is the tripwire: the day a
citation gains a field that CAN select an occurrence, it fails and the finding
becomes real.

Tests: scripts/docparse.py
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCPARSE_SRC = ROOT / "scripts" / "docparse.py"


def _load():
    spec = importlib.util.spec_from_file_location("docparse_occurrence", DOCPARSE_SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["docparse_occurrence"] = mod
    spec.loader.exec_module(mod)
    return mod


dp = _load()
TREE = ast.parse(DOCPARSE_SRC.read_text(encoding="utf-8"))


def _item(text: str, y: float) -> dict:
    return {"text": text, "x": 72.0, "y": y, "width": 50.0, "height": 10.0}


TWICE_ON_THE_PAGE = [
    _item("net position ", 100.0),
    _item("filler ", 200.0),
    _item("net position ", 300.0),
]


# ==========================================================================
# 1 - what a caller is able to ask
# ==========================================================================

def test_there_is_exactly_one_call_site_and_it_passes_a_page_and_a_string():
    """Asked of the AST, not of a grep: a wildcard or a rename would still show."""
    calls = [node for node in ast.walk(TREE)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "find_boxes_for_quote"]
    assert len(calls) == 1, [ast.unparse(c) for c in calls]
    assert ast.unparse(calls[0]) == (
        "find_boxes_for_quote(page_data.get('text_items', []), quote_text, dpi)"
    ), "a new argument reached the matcher; re-adjudicate the occurrence finding"


def test_a_citation_carries_no_field_that_could_select_an_occurrence():
    """The tripwire. A citation that gains an ordinal makes the finding real."""
    report = next(n for n in ast.walk(TREE)
                  if isinstance(n, ast.FunctionDef) and n.name == "_generate_report_html")
    keys = {n.args[0].value for n in ast.walk(report)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "cit" and n.args and isinstance(n.args[0], ast.Constant)}
    assert keys == {"id", "file", "page", "quote", "relevance"}, (
        f"the citation schema changed to {sorted(keys)}; if any new field can "
        f"name WHICH occurrence is meant, first-match-wins becomes a real defect"
    )


def test_two_citations_about_different_occurrences_are_the_same_question(monkeypatch):
    """Run the real report path over a page that says the same thing twice."""
    parse_data = {"files": [{
        "file": "/docs/ledger.pdf",
        "file_name": "ledger.pdf",
        "pages": [{
            "page_num": 1, "width_pt": 595.0, "height_pt": 842.0,
            "text": "net position ... net position",
            "text_items": TWICE_ON_THE_PAGE,
        }],
    }]}
    citations = [
        {"id": 1, "file": "ledger.pdf", "page": 1, "quote": "net position",
         "relevance": "the opening line"},
        {"id": 2, "file": "ledger.pdf", "page": 1, "quote": "net position",
         "relevance": "the restatement further down the page"},
    ]

    asked = []
    original = dp.find_boxes_for_quote

    def _spy(text_items, quote, dpi=dp.DEFAULT_DPI):
        asked.append((tuple(sorted(i["y"] for i in text_items)), quote, dpi))
        return original(text_items, quote, dpi)

    monkeypatch.setattr(dp, "find_boxes_for_quote", _spy)
    dp._generate_report_html("what is the net position?", "It is stated twice [1][2].",
                             citations, {}, parse_data)

    assert len(asked) == 2
    assert asked[0] == asked[1], (
        "the two citations reached the matcher as different questions; if they "
        "can differ, the matcher can answer the wrong one"
    )


# ==========================================================================
# 2 - what the matcher actually does, pinned
# ==========================================================================

def test_the_highlight_lands_on_text_that_really_reads_the_quote():
    """Not an arbitrary box: the card's claim holds for the box it draws."""
    boxes = dp.find_boxes_for_quote(TWICE_ON_THE_PAGE, "net position", dpi=72)
    assert boxes, "a quote present on the page was not located at all"
    ys = {b["y"] for b in boxes}
    covered = {i["y"] for i in TWICE_ON_THE_PAGE if "net position" in i["text"]}
    assert ys <= covered, f"the highlight sits at {ys}, off every occurrence"


def test_only_the_first_of_several_identical_occurrences_is_highlighted():
    """The behaviour, stated so a future change is a decision and not a surprise."""
    boxes = dp.find_boxes_for_quote(TWICE_ON_THE_PAGE, "net position", dpi=72)
    assert [b["y"] for b in boxes] == [100.0]


def test_a_unique_quote_is_unaffected_by_any_of_this():
    boxes = dp.find_boxes_for_quote(TWICE_ON_THE_PAGE, "filler", dpi=72)
    assert [b["y"] for b in boxes] == [200.0]


@pytest.mark.parametrize("quote", ["", "no such phrase"])
def test_a_quote_that_is_not_there_draws_nothing(quote):
    assert dp.find_boxes_for_quote(TWICE_ON_THE_PAGE, quote, dpi=72) == []
