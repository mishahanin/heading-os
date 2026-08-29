"""A list marker that took the paragraph break above it.

`strip_markdown_noise` removed the indentation before a bullet with `^[\\s]*`.
`\\s` includes the newline, and under `re.MULTILINE` `^` also anchors at the
start of the BLANK line above the list, so the strip consumed the paragraph
separator along with the marker.

MEASURED 2026-08-29, before the fix. A lead-in paragraph, a three-item list and
a closing paragraph came back from `get_paragraphs` as TWO paragraphs. Written
with the same three lines as plain prose, the same document came back as three.
The cost is not cosmetic: on the merged form `check_burstiness` returned
nothing, and on the unmerged form it returned a `burstiness_violation` for the
monotone list. A real finding was swallowed by a whitespace class, and
`paragraph_count` reported 2 for a visibly three-paragraph document.

Every paragraph-based check reads `get_paragraphs`, so the blast radius covered
`check_burstiness`, `check_specificity`, `check_transition_openers` and
`check_over_fragmentation` at once. Nothing pinned any of it: a sweep of the
suite found no list-bearing input to either function.

The second half of this file pins the 200-word boundary. `check_burstiness`
tests `total_words >= 200` and its docstring said "greater than 200 words", so
a document of exactly 200 words produced a blocking `burstiness_systemic` the
docstring said could not fire. The nearest existing assertion used a 528-word
body, which cannot tell the two readings apart.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "humanization-check.py"


@pytest.fixture(scope="module")
def hc():
    spec = importlib.util.spec_from_file_location("humanization_check_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["humanization_check_probe"] = module
    spec.loader.exec_module(module)
    return module


LEAD = "The Kigali rollout shipped on 14 March. It held. Two engineers carried it."
CLOSE = "Closing paragraph text."
LIST_BODY = "- One item here now.\n- Two items here now.\n- Three items here now."
PROSE_BODY = "One item here now.\nTwo items here now.\nThree items here now."


# ============================================================
# The paragraph break survives the marker strip
# ============================================================

def test_a_list_is_its_own_paragraph(hc):
    doc = f"{LEAD}\n\n{LIST_BODY}\n\n{CLOSE}"
    assert len(hc.get_paragraphs(doc)) == 3


def test_the_list_form_and_the_prose_form_agree(hc):
    """The whole defect in one comparison: the same three blocks counted
    differently depending only on whether the middle one wore bullets."""
    listed = hc.get_paragraphs(f"{LEAD}\n\n{LIST_BODY}\n\n{CLOSE}")
    prosed = hc.get_paragraphs(f"{LEAD}\n\n{PROSE_BODY}\n\n{CLOSE}")
    assert len(listed) == len(prosed) == 3


@pytest.mark.parametrize("marker", ["- item", "* item", "+ item", "1. item"])
def test_the_blank_line_above_any_marker_survives(hc, marker):
    assert hc.strip_markdown_noise(f"lead\n\n{marker}") == "lead\n\nitem"


# ============================================================
# The other direction: the strip still strips
# ============================================================

STILL_STRIPPED = [
    ("  - item", "item"),
    ("\t- item", "item"),
    ("    * item", "item"),
    ("  3. item", "item"),
    ("- item", "item"),
]


@pytest.mark.parametrize("raw, expected", STILL_STRIPPED,
                         ids=[r.strip() + "|" + repr(r[:2]) for r, _ in STILL_STRIPPED])
def test_indentation_before_a_marker_still_goes(hc, raw, expected):
    """Both directions on one function. A body that stopped stripping entirely
    would satisfy the paragraph tests above and break nothing else in them."""
    assert hc.strip_markdown_noise(raw) == expected


def test_a_horizontal_rule_is_not_a_list(hc):
    assert hc.strip_markdown_noise("a\n\n---\n\nb") == "a\n\n---\n\nb"


# ============================================================
# The consequence the merge hid
# ============================================================

def _types(findings) -> list[str]:
    return [f.get("type") if isinstance(f, dict) else f for f in findings]


def test_a_monotone_list_paragraph_is_visible_to_burstiness(hc):
    """The finding that disappeared. Three sentences of equal length, in a
    list, inside a document whose other paragraphs vary."""
    doc = f"{LEAD}\n\n{LIST_BODY}\n\n{CLOSE}"
    assert "burstiness_violation" in _types(hc.check_burstiness(doc))


# ============================================================
# The 200-word boundary the docstring disagreed with
# ============================================================

def _monotone(paragraphs: int) -> str:
    sentence = " ".join(["word"] * 10) + "."
    return "\n\n".join([" ".join([sentence] * 4)] * paragraphs)


def test_exactly_two_hundred_words_is_systemic(hc):
    body = _monotone(5)
    assert len(hc.strip_markdown_noise(body).split()) == 200
    assert "burstiness_systemic" in _types(hc.check_burstiness(body))


def test_below_two_hundred_words_is_not_systemic(hc):
    """The other side of the same line, so a gate that fired on everything
    could not pass the case above."""
    body = _monotone(4)
    assert len(hc.strip_markdown_noise(body).split()) < 200
    assert "burstiness_systemic" not in _types(hc.check_burstiness(body))


def test_the_docstring_no_longer_contradicts_the_gate(hc):
    """The docstring is what a reader trusts about a blocking gate, so the
    off-by-one in it is the defect, not a note about one."""
    doc = hc.check_burstiness.__doc__ or ""
    # The STATEMENT of the gate, not every mention of the old wording: the
    # corrected docstring quotes ">200 words" when it records what it used to
    # say, and a test that banned the substring would forbid the history.
    assert "prose (>200 words" not in doc
    assert "prose (200+ words" in doc
