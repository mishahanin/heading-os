"""Cross-page docs search stays intact after the catalog split (F-8.2).

The 191 KB skills monolith was split into per-category pages; the site's
client-side search must still deep-link to an individual skill on its new
category page. There is no Node search harness (and CI has no Node), so this
asserts the same contract in pure Python against the committed
``docs/assets/search-index.json`` that ``docs/assets/search.js`` consumes.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INDEX = DOCS / "assets" / "search-index.json"

# Derived from disk, not written down. The literal list of eight that used to
# live here only ever checked the pages that existed the day it was typed: a
# ninth category page added by the generator would have been indexed or not
# indexed with nothing to say so. `skills-mcp-plugins.html` is excluded because
# it is a hand-authored page about plugins, not a generated skill category.
CATEGORY_PAGES = sorted(
    p.name for p in DOCS.glob("skills-*.html")
    if p.name != "skills-mcp-plugins.html"
)
# Floor, so a moved docs directory or a renamed prefix reports as a failure
# rather than as eight silently-skipped assertions.
assert len(CATEGORY_PAGES) >= 8, (
    f"only {len(CATEGORY_PAGES)} category page(s) found under {DOCS}; "
    "the split produced eight"
)


def _records() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def test_index_exists_and_nonempty():
    assert INDEX.exists(), "search-index.json is missing; run regenerate-docs-html.py --all"
    assert len(_records()) > 0


def test_moved_skill_deep_links_to_category_page():
    """A search for a moved skill (osint) must return a record whose url deep-links
    to its new category page anchor (skills-intel.html#s-osint), not the old
    monolith."""
    records = _records()
    osint = [r for r in records if r["a"] == "s-osint"]
    assert osint, "no search record anchored at s-osint"
    assert any(r["u"] == "skills-intel.html" for r in osint), (
        f"osint does not deep-link to skills-intel.html; got {[r['u'] for r in osint]}"
    )
    # The deep-link the client builds is url#anchor.
    rec = next(r for r in osint if r["u"] == "skills-intel.html")
    assert rec["a"] == "s-osint"


def test_every_category_page_is_indexed():
    """Each split category page contributes at least one search record, so search
    reaches every page."""
    by_page = {r["u"] for r in _records()}
    missing = [p for p in CATEGORY_PAGES if p not in by_page]
    assert not missing, f"category pages absent from the search index: {missing}"


def test_category_pages_carry_per_skill_anchors():
    """Category-page records include per-skill anchors (s-*), so search deep-links to
    individual skills rather than only the page top."""
    anchored = {
        r["u"]
        for r in _records()
        if r["u"] in CATEGORY_PAGES and r["a"].startswith("s-")
    }
    missing = [p for p in CATEGORY_PAGES if p not in anchored]
    assert not missing, f"category pages without any per-skill anchor: {missing}"


def test_old_catalog_url_still_resolves_as_index():
    """The old skills-mcp-plugins.html URL must not 404: it is now the catalog index
    (no per-skill <section> cards, links out to the category pages)."""
    page = DOCS / "skills-mcp-plugins.html"
    assert page.exists(), "skills-mcp-plugins.html was removed; the old URL would 404"
    text = page.read_text(encoding="utf-8")
    assert "Skill reference by category" in text, "index page lost its category navigation"
    assert '<section class="skill"' not in text, (
        "the index still carries per-skill cards; they should live on the category pages"
    )
    # The MCP servers and Plugins sections are preserved on the index.
    assert "<h2>MCP servers</h2>" in text
    assert "<h2>Plugins</h2>" in text
