"""Two defects in the docs generator, both about a page half-wired for search.

1. `inject_search` GUARDED on `'<button class="menu-toggle"'` appearing anywhere
   and then INSERTED with a `str.replace` on `'  <button ...'` -- the same markup
   with exactly two leading spaces. On a hand-authored page indented any other
   way the guard passed, the replace matched nothing, the loader `<script>` in
   the block below was still appended, and the page shipped `assets/search.js`
   with no `#doc-search` element for it to bind to. The function returns True
   either way, so `--nav-sync` reported success over it.

2. The single-file path -- the mode the module docstring designates as hook mode
   -- regenerated the HTML and left `docs/assets/search-index.json` describing
   the page's previous contents. `--all`, `--nav-sync` and `--search-index` all
   rebuild it; only the one an author actually types after editing a page did
   not.

Nothing here touches the real `docs/` tree: the injection tests run on files
under `tmp_path`, and the index test drives `main()` with `regenerate` and
`build_search_index` replaced by recorders, so no page is rendered and no index
is written.

The generator is loaded BY PATH because its name is kebab-cased and so not
importable.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "regen_docs_html", ROOT / "scripts" / "regenerate-docs-html.py")
regen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(regen)


def _page(indent: str) -> str:
    """A hand-authored site page with the menu-toggle button at `indent`."""
    return (
        "<!DOCTYPE html>\n<html>\n<body>\n"
        "<nav>\n"
        f'{indent}<button class="menu-toggle" onclick="void 0">Menu</button>\n'
        "</nav>\n"
        "<main></main>\n"
        "</body>\n</html>\n"
    )


# ============================================================
# 1. The guard and the insertion have to read the same anchor
# ============================================================

@pytest.mark.parametrize("indent", ["  ", "", "    ", "\t"])
def test_the_search_box_lands_whatever_the_button_indentation(tmp_path, indent):
    page = tmp_path / "handmade.html"
    page.write_text(_page(indent), encoding="utf-8")

    assert regen.inject_search(page, quiet=True) is True
    out = page.read_text(encoding="utf-8")

    assert 'id="doc-search"' in out
    # ...and the loader, which is what makes the missing box a broken page
    # rather than a merely absent feature
    assert "assets/search.js" in out


@pytest.mark.parametrize("indent", ["  ", "", "    ", "\t"])
def test_a_page_never_ships_the_loader_without_the_box(tmp_path, indent):
    """The pairing is the invariant. Before the fix a four-space page came out
    of here with `search.js` and no input for it to bind to, and said True."""
    page = tmp_path / "handmade.html"
    page.write_text(_page(indent), encoding="utf-8")

    regen.inject_search(page, quiet=True)
    out = page.read_text(encoding="utf-8")

    assert ("assets/search.js" in out) == ('id="doc-search"' in out)


def test_the_injected_box_carries_the_pages_own_indentation(tmp_path):
    page = tmp_path / "handmade.html"
    page.write_text(_page("    "), encoding="utf-8")

    regen.inject_search(page, quiet=True)
    line = next(ln for ln in page.read_text(encoding="utf-8").splitlines()
                if "search-box" in ln)

    assert line.startswith("    <div"), line


def test_a_two_space_page_is_byte_identical_to_the_old_output(tmp_path):
    """The 37 pages in `docs/` are all indented two spaces, so the regex must
    reproduce the old `str.replace` result exactly or every one of them drifts."""
    page = tmp_path / "handmade.html"
    original = _page("  ")
    page.write_text(original, encoding="utf-8")
    expected = original.replace(
        '  <button class="menu-toggle"',
        regen.SEARCH_BOX + '\n  <button class="menu-toggle"',
        1,
    ).replace("</body>", regen.SEARCH_SCRIPT + "\n</body>", 1)

    regen.inject_search(page, quiet=True)

    assert page.read_text(encoding="utf-8") == expected


def test_injection_is_idempotent(tmp_path):
    page = tmp_path / "handmade.html"
    page.write_text(_page("    "), encoding="utf-8")

    regen.inject_search(page, quiet=True)
    once = page.read_text(encoding="utf-8")
    regen.inject_search(page, quiet=True)

    assert page.read_text(encoding="utf-8") == once
    assert once.count('id="doc-search"') == 1


ANCHORLESS = "<!DOCTYPE html>\n<html>\n<body>\n<main></main>\n</body>\n</html>\n"


def test_a_page_with_no_menu_toggle_gets_neither_half(tmp_path):
    """No anchor, no box -- and then no loader either, or the pairing above
    breaks in the other direction.

    That second sentence was this docstring's claim from the day it was written
    and the assertion for it was never here, so the case went unmeasured and the
    code kept doing it. MEASURED 2026-09-01 on exactly this page: no
    `#doc-search`, `<script src="assets/search.js">` appended, return True. It
    is the same orphan-loader page the indentation fix closed, reached by the
    other route, and only the indentation route had ever been bound.
    """
    page = tmp_path / "plain.html"
    page.write_text(ANCHORLESS, encoding="utf-8")

    assert regen.inject_search(page, quiet=True) is True
    out = page.read_text(encoding="utf-8")

    assert 'id="doc-search"' not in out
    assert "assets/search.js" not in out, (
        "the page shipped the search loader with no #doc-search element for it "
        "to bind to -- the exact failure the anchor fix was for")
    # Nothing was written at all, so `atomic_write_text` never ran and the page
    # is byte-identical. Stated because "no loader" could also be satisfied by a
    # rewrite that dropped something else.
    assert out == ANCHORLESS


def test_the_anchorless_page_is_not_simply_inert(tmp_path):
    """The straw-man check on the case above.

    If `inject_search` never wrote to a page in this shape for some unrelated
    reason - an unreadable file, a missing `</body>`, an early return - the
    assertions above would pass while measuring nothing about the pairing. Give
    the same page an anchor and it must gain BOTH halves.
    """
    page = tmp_path / "anchored.html"
    page.write_text(ANCHORLESS.replace(
        "<main></main>",
        '  <button class="menu-toggle" onclick="void 0">Menu</button>'),
        encoding="utf-8")

    assert regen.inject_search(page, quiet=True) is True
    out = page.read_text(encoding="utf-8")

    assert 'id="doc-search"' in out
    assert "assets/search.js" in out


def test_a_page_that_already_has_the_box_still_gains_the_loader(tmp_path):
    """The bound the pairing must not overshoot.

    A page carrying `#doc-search` from `SITE_SHELL` but no loader is a real
    shape, and keying the loader on "this call injected a box" rather than on
    "the page has one" would leave it without a loader forever.
    """
    page = tmp_path / "shelled.html"
    page.write_text(
        '<!DOCTYPE html>\n<html>\n<body>\n'
        '<div class="search-box"><input id="doc-search"></div>\n'
        "<main></main>\n</body>\n</html>\n", encoding="utf-8")

    assert regen.inject_search(page, quiet=True) is True
    out = page.read_text(encoding="utf-8")

    assert "assets/search.js" in out
    assert out.count('id="doc-search"') == 1


def test_the_live_site_pages_are_the_corpus_this_guards(tmp_path):
    """The corpus is non-empty and really does use the two-space form, which is
    why the defect above was latent rather than live. A guard asserted over an
    empty `docs/` would pass while measuring nothing."""
    pages = sorted((ROOT / "docs").glob("*.html"))
    assert len(pages) >= 10, f"docs/ holds only {len(pages)} pages"

    # A SCAN: a page that vanished between the glob and the read carries no
    # anchor to be missing a search box, so skipping it is the right answer and
    # `read_sources` warns naming it. Both floors below carry the vanished count,
    # so a corpus that shrank underneath them cannot look like a corpus that was
    # always that size. The page text is kept rather than re-read, so the second
    # assertion measures the same bytes the first one classified.
    vanished: list[Path] = []
    anchored = [(p, text) for p, text in read_sources(pages, vanished)
                if re.search(r'^[ \t]*<button class="menu-toggle"',
                             text, flags=re.MULTILINE)]
    assert len(anchored) >= 10, (
        f"only {len(anchored)} pages carry the anchor "
        f"({len(vanished)} vanished mid-walk: {vanished})")
    for page, text in anchored:
        assert 'id="doc-search"' in text, page.name


# ============================================================
# 2. Single-file mode rebuilds the search index
# ============================================================

def _drive_main(monkeypatch, argv):
    """main() with the renderer and the index builder replaced by recorders."""
    rendered, indexed = [], []
    monkeypatch.setattr(regen, "regenerate",
                        lambda md, quiet=False: rendered.append(md) or True)
    monkeypatch.setattr(regen, "build_search_index",
                        lambda quiet=False: indexed.append(quiet) or 0)
    monkeypatch.setattr("sys.argv", ["regenerate-docs-html.py", *argv])
    try:
        regen.main()
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    else:
        code = 0
    return code, rendered, indexed


def test_regenerating_one_site_page_rebuilds_the_search_index(monkeypatch):
    page = regen.SITE_DIR / "QUICKSTART.md"
    assert page.exists(), "the fixture page left docs/; pick another"

    code, rendered, indexed = _drive_main(monkeypatch, [str(page)])

    assert code == 0
    assert rendered == [page]
    assert indexed, "the index was not rebuilt after a single-page regeneration"


def test_a_templates_page_does_not_rebuild_the_site_index(monkeypatch):
    """The other direction. Rebuilding on every path would be a change nothing
    holds in place: `templates/` pages are not in the site index at all, and a
    rebuild there writes `docs/assets/search-index.json` for no reason."""
    page = ROOT / "templates" / "SOME-GUIDE.md"

    code, rendered, indexed = _drive_main(monkeypatch, [str(page)])

    assert code == 0
    assert rendered == [page]
    assert indexed == []


def test_a_failed_regeneration_does_not_rebuild_the_index(monkeypatch):
    """An index built from a page that failed to render is worse than a stale
    one: it looks current."""
    monkeypatch.setattr(regen, "regenerate", lambda md, quiet=False: False)
    indexed = []
    monkeypatch.setattr(regen, "build_search_index",
                        lambda quiet=False: indexed.append(quiet) or 0)
    monkeypatch.setattr("sys.argv", ["regenerate-docs-html.py",
                                     str(regen.SITE_DIR / "QUICKSTART.md")])
    with pytest.raises(SystemExit) as exc:
        regen.main()

    assert exc.value.code == 1
    assert indexed == []


def test_quiet_hook_mode_passes_quiet_through_to_the_index(monkeypatch):
    """Hook mode exists to be silent. An index rebuild that prints its size
    every time a PostToolUse hook fires would undo that."""
    _code, _rendered, indexed = _drive_main(
        monkeypatch, ["--quiet", str(regen.SITE_DIR / "QUICKSTART.md")])

    assert indexed == [True]
