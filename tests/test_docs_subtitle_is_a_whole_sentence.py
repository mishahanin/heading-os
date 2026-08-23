"""The published page subtitle must be a whole sentence, not one wrapped line.

Found by the 2026-08-23 engine audit, then measured wider than reported.

`extract_title()` walked to the first prose line after the H1 and handed THAT
LINE to `_clean_subtitle`. Markdown prose in `docs/` is hard-wrapped at about 80
columns, so on any page whose lead paragraph runs past one line the subtitle was
cut at the wrap point. Fourteen of the generated pages carried one on
2026-08-23: "reads as designed or", "so you can try the engine's", "That means
the", "It carries example house".

The cut is not cosmetic and it is not confined to one surface. The same string
is emitted three times:

  * the grey standfirst under the `<h1>`, read on every page load;
  * `<meta name="description">`, which is what a search engine quotes;
  * every `docs/assets/search-index.json` record built from that page.

Two guards, and they are different in kind:

  1. `test_every_generated_subtitle_matches_its_source` DERIVES the expected
     value by running the generator's own extractor over the `.md`, so it
     catches stale HTML as well as a regressed extractor. It cannot catch a
     defect in the extractor itself -- both sides would move together.
  2. `test_no_subtitle_stops_mid_sentence` states the property independently of
     the implementation, which is the half that survives a wrong extractor.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

_META = re.compile(r'<meta name="description" content="([^"]*)"')


def _generator():
    path = ROOT / "scripts" / "regenerate-docs-html.py"
    spec = importlib.util.spec_from_file_location("regen_docs_html", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEN = _generator()


def _pairs() -> list[tuple[Path, Path]]:
    out = []
    for md in sorted(DOCS.glob("*.md")):
        html = md.with_suffix(".html")
        if html.is_file():
            out.append((md, html))
    return out


def _meta_description(html: Path) -> str:
    m = _META.search(html.read_text(encoding="utf-8"))
    assert m, f"{html.name} has no meta description"
    # The generator escapes the attribute; unescape enough to compare.
    import html as html_stdlib
    return html_stdlib.unescape(m.group(1))


def test_there_are_pages_to_check():
    """A guard that matches nothing passes everything."""
    assert len(_pairs()) >= 15


@pytest.mark.parametrize("md,html", _pairs(), ids=lambda p: p.name)
def test_every_generated_subtitle_matches_its_source(md: Path, html: Path):
    _, expected = GEN.extract_title(md.read_text(encoding="utf-8"), md.stem)
    if not expected:
        return
    assert _meta_description(html) == expected, (
        f"{html.name} carries a subtitle its own source no longer produces. "
        f"Run: .venv/bin/python scripts/regenerate-docs-html.py --all"
    )


@pytest.mark.parametrize("md,html", _pairs(), ids=lambda p: p.name)
def test_no_subtitle_stops_mid_sentence(md: Path, html: Path):
    """Stated without reference to the extractor, so a wrong extractor fails it.

    A subtitle either ends the sentence it started, or was deliberately cut at
    the length limit and says so with an ellipsis.
    """
    text = _meta_description(html).strip()
    if not text:
        return
    assert text[-1] in ".!?:…", (
        f"{html.name} publishes a subtitle that stops mid-sentence: {text[-70:]!r}. "
        f"The lead paragraph is hard-wrapped and only its first line was read."
    )


def test_the_search_index_carries_the_same_whole_sentences():
    """The index is built from the HTML, so a truncated subtitle propagates.

    Scoped to md-sourced pages: a hand-authored page has no lead paragraph the
    extractor ever touched, and its section records are ordinary body prose.
    """
    index = DOCS / "assets" / "search-index.json"
    records = json.loads(index.read_text(encoding="utf-8"))
    by_url = {}
    for rec in records:
        if not rec.get("a"):            # page-level record opens with the subtitle
            by_url.setdefault(rec["u"], rec)
    bad = []
    for _md, html in _pairs():
        rec = by_url.get(html.name)
        if rec is None:
            continue
        subtitle = _meta_description(html).strip()
        if not subtitle:
            continue
        if subtitle not in rec.get("t", ""):
            bad.append(f'{html.name}: index text does not carry the page subtitle')
    assert not bad, (
        "the search index disagrees with the page it was built from:\n"
        + "\n".join(bad)
        + "\nRun: .venv/bin/python scripts/regenerate-docs-html.py --all"
    )


def test_a_lead_paragraph_followed_by_a_list_does_not_swallow_the_list():
    """The join must stop at the first structural line, not run into the list."""
    md = "# T\n\nOne wrapped\nlead paragraph.\n\n- a bullet\n- another\n"
    _, subtitle = GEN.extract_title(md, "T")
    assert subtitle == "One wrapped lead paragraph."


def test_the_join_stops_at_a_blank_line():
    md = "# T\n\nFirst para\ncontinues here.\n\nSecond para must not appear.\n"
    _, subtitle = GEN.extract_title(md, "T")
    assert subtitle == "First para continues here."
