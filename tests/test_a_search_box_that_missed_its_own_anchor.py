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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

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


def test_a_page_with_no_menu_toggle_gets_neither_half(tmp_path):
    """No anchor, no box -- and then no loader either, or the pairing above
    breaks in the other direction."""
    page = tmp_path / "plain.html"
    page.write_text("<!DOCTYPE html>\n<html>\n<body>\n<main></main>\n"
                    "</body>\n</html>\n", encoding="utf-8")

    assert regen.inject_search(page, quiet=True) is True
    out = page.read_text(encoding="utf-8")

    assert 'id="doc-search"' not in out


def test_the_live_site_pages_are_the_corpus_this_guards(tmp_path):
    """The corpus is non-empty and really does use the two-space form, which is
    why the defect above was latent rather than live. A guard asserted over an
    empty `docs/` would pass while measuring nothing."""
    pages = sorted((ROOT / "docs").glob("*.html"))
    assert len(pages) >= 10, f"docs/ holds only {len(pages)} pages"

    anchored = [p for p in pages
                if re.search(r'^[ \t]*<button class="menu-toggle"',
                             p.read_text(encoding="utf-8"), flags=re.MULTILINE)]
    assert len(anchored) >= 10, f"only {len(anchored)} pages carry the anchor"
    for page in anchored:
        assert 'id="doc-search"' in page.read_text(encoding="utf-8"), page.name


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
