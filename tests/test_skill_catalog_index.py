"""The skill catalog calls itself complete, so it has to be.

Found by the 2026-08-23 audit. `docs/skills-mcp-plugins.html` carries the quick
index for the whole public skills catalog and describes itself as "The full skill
catalog". It listed 92 of the 95 skills the category pages document: `/census`,
`/brain-audit` and `/modem-tune` had cards nobody could navigate to. The
per-category cards also claimed "Operations: daily drivers — 12 skills" against a
page holding 13.

Both are hand-maintained HTML, which is a legitimate choice — the cards carry
prose a generator could not write. What is not legitimate is a hand-maintained
index that says "full" and drifts silently. A reader, or a search indexer,
concludes the three missing skills do not exist.

These tests are the missing half of that choice: keep authoring by hand, and let
the suite notice when the index falls behind.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
CATALOG = DOCS / "skills-mcp-plugins.html"

_INDEX_LINK = re.compile(r'href="(skills-[a-z-]+\.html)#s-([a-z0-9-]+)"')
_CARD_ID = re.compile(r'id="s-([a-z0-9-]+)"')
# Anchored to ONE card. A `.*?` across the whole document would let the first
# `href` in the file pair with a later card's count — the first draft of this
# test did exactly that and blamed the catalog page for its own greedy regex.
_CARD_COUNT = re.compile(
    r'<a class="card" href="(skills-[a-z-]+\.html)">\s*<h3>[^<]*</h3>\s*'
    r'<p>(\d+) skills, documented in full\.</p>')


def _category_pages() -> list[Path]:
    return [p for p in sorted(DOCS.glob("skills-*.html")) if p != CATALOG]


def _documented() -> dict[str, str]:
    found: dict[str, str] = {}
    for page in _category_pages():
        for anchor in _CARD_ID.findall(page.read_text(encoding="utf-8")):
            found[anchor] = page.name
    return found


def _indexed() -> dict[str, str]:
    return {anchor: page
            for page, anchor in _INDEX_LINK.findall(CATALOG.read_text(encoding="utf-8"))}


def test_every_documented_skill_appears_in_the_index():
    missing = sorted(set(_documented()) - set(_indexed()))
    assert missing == [], (
        "skills with a card but no index row, so the catalog that calls itself "
        f"full cannot reach them: {missing}"
    )


def test_the_index_points_at_no_card_that_does_not_exist():
    """A dead index row is a 404 inside the page that promises completeness."""
    orphans = sorted(set(_indexed()) - set(_documented()))
    assert orphans == [], f"index rows with no card: {orphans}"


def test_each_index_row_names_the_page_the_card_is_actually_on():
    documented = _documented()
    wrong = {anchor: (page, documented[anchor])
             for anchor, page in _indexed().items()
             if anchor in documented and page != documented[anchor]}
    assert wrong == {}, f"index rows pointing at the wrong page: {wrong}"


def test_each_category_card_states_the_count_its_page_holds():
    text = CATALOG.read_text(encoding="utf-8")
    documented = _documented()
    wrong = []
    for page, claimed in _CARD_COUNT.findall(text):
        actual = sum(1 for source in documented.values() if source == page)
        if int(claimed) != actual:
            wrong.append(f"{page}: card says {claimed}, page documents {actual}")
    assert wrong == [], "; ".join(wrong)


def test_the_detector_is_not_vacuous():
    """Every regex here must match something, or all four tests pass on nothing."""
    assert len(_documented()) > 80
    assert len(_indexed()) > 80
    assert len(_CARD_COUNT.findall(CATALOG.read_text(encoding="utf-8"))) >= 8
