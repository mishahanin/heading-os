"""No docs page may carry an inline CSS rule for a class it never uses.

Found by the 2026-08-23 engine audit. Every one of the nine `docs/skills-*.html`
pages defined `h3.cat` and `h3.cat:first-of-type`, and not one contained an
element with `class="cat"`.

The cause is in `scripts/dev/split-skills-catalog.py`: it was a one-time
migration that cut a single large catalog page into nine at each
`<h3 class="cat">` divider. The dividers became each page's `<h1>`, so the rule
styling them had nothing left to style. The script split the BODY and copied the
`<style>` block whole into all nine pages, which left three separate dead
regions:

  * `h3.cat` (2 rules), dead on all nine pages;
  * `a.cmd-link` (3 rules), dead on the eight category pages because only the
    index links to cards;
  * `section.skill` (6 rules), dead on the index because only the category pages
    hold cards.

Harmless to a browser, which is why it survived. Not harmless to a reader: dead
CSS says an element exists. The audit's original claim was that a matching `</h3>`
end tag was orphaned too. That part is refuted, and the refutation is pinned
below rather than dropped, because the same claim will be made again: every
`docs/*.html` page balances its `<h3>` tags exactly.

The `<style>` blocks here are page-local by construction, so an unused selector
in one is genuinely unreachable. That is what makes this checkable at all. The
shared theme in `reference/31c-docs-light-theme.css` is deliberately NOT scanned:
it serves every page, so a class unused on one page is normal there.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

_STYLE = re.compile(r"<style>(.*?)</style>", re.S)
_DECL = re.compile(r"\{[^}]*\}")
_SELECTOR_CLASS = re.compile(r"\.([a-zA-Z][\w-]*)")
_CLASS_ATTR = re.compile(r'class="([^"]*)"')

# Classes applied by `docs/assets/search.js` at runtime, so they never appear in
# the static markup. Empty today; add here WITH the script and line that applies
# the class, never to silence a finding.
RUNTIME_CLASSES: dict[str, str] = {}


def _pages() -> list[Path]:
    return sorted(DOCS.glob("*.html"))


def _dead_selectors(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    blocks = _STYLE.findall(text)
    if not blocks:
        return []
    body = _STYLE.sub("", text)
    used = {c for attr in _CLASS_ATTR.findall(body) for c in attr.split()}
    used |= set(RUNTIME_CLASSES)
    # Strip declaration bodies first, or `0.72rem` reads as a class selector.
    selectors = " ".join(_DECL.sub(" ", b) for b in blocks)
    return sorted(set(_SELECTOR_CLASS.findall(selectors)) - used)


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_every_inline_class_selector_matches_an_element_on_the_page(page):
    dead = _dead_selectors(page)
    assert dead == [], (
        f"{page.relative_to(ROOT)} styles classes it never uses: {dead}. An "
        "inline <style> block is page-local, so these rules can never apply. "
        "Either add the markup or drop the rules."
    )


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_h3_tags_balance(page):
    """Pins the refuted half of the audit finding, so it is not re-raised."""
    text = page.read_text(encoding="utf-8")
    opens = len(re.findall(r"<h3\b", text))
    closes = len(re.findall(r"</h3>", text))
    assert opens == closes, (
        f"{page.name}: {opens} <h3> against {closes} </h3>"
    )


def test_the_split_leftovers_are_gone_specifically():
    """Names the three dead regions, so a re-paste of the old block is caught by
    a test that says what happened rather than by a generic selector diff."""
    pages = sorted(DOCS.glob("skills-*.html"))
    # Every assert below sits inside the loop, so over zero pages this test
    # passes without checking anything. A rename of the split output, or a move
    # of the docs directory, would switch the whole test off in silence. There
    # were 9 skills-*.html pages on 2026-08-26.
    assert len(pages) >= 6, f"the scan collapsed to {len(pages)} files"
    for page in pages:
        text = page.read_text(encoding="utf-8")
        style = "".join(_STYLE.findall(text))
        assert "h3.cat" not in style, f"{page.name} re-grew the h3.cat rules"
        if page.name == "skills-mcp-plugins.html":
            assert "section.skill" not in style, (
                "the catalog index has no skill cards; its section.skill rules "
                "are unreachable"
            )
        else:
            assert "a.cmd-link" not in style, (
                f"{page.name} has no cmd-link elements; only the index does"
            )


def test_the_detector_is_not_vacuous():
    """A regex that finds no <style> block passes every test above."""
    styled = [p.name for p in _pages() if _STYLE.search(p.read_text(encoding="utf-8"))]
    assert len(styled) >= 9, f"only {len(styled)} pages carry an inline style block"
    # And the parser must find real selectors in them, not an empty set.
    sample = DOCS / "skills-crm.html"
    style = "".join(_STYLE.findall(sample.read_text(encoding="utf-8")))
    assert _SELECTOR_CLASS.findall(_DECL.sub(" ", style)), (
        "parsed no class selectors out of a page that has them"
    )
