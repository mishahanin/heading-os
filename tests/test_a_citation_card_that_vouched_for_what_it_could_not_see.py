"""A visual citation report that showed a page it could not vouch for.

`docparse report` exists to answer one question: WHERE does this document say
this. It answers it with a page image and a highlight box drawn over the words.
A reader trusts that pairing, which is the whole point of the format.

The card had exactly one way to say it could not answer: the ambiguity note,
which fires when a basename resolves to two parsed documents. That note was
added deliberately, with a comment that states the principle - "showing SOME
page under an unresolved name is the defect". Four other ways of not being able
to answer stayed silent, and each of them rendered a card indistinguishable
from a good one:

1. The quote is not in the page's extracted text. `find_boxes_for_quote`
   returns `[]`, which is the tool ESTABLISHING that the document does not say
   it there. The card still embedded the page image, still printed the quote
   under the label "Cited Text", drew no highlight, and said nothing. A missing
   highlight reads as a rendering nicety, not as a refutation.
2. The page number is not among the parsed pages. `page_data` is None, the page
   size falls back to an invented 800x600, and the screenshot lookup could
   still return an image - so a real page image was shown under a made-up
   coordinate space.
3. The named document is not in the parse data at all. Same silence.
4. `cmd_report` keys `page_screenshots` by BASENAME. The citation's `file`
   string was used as the key verbatim, so a citation naming the FULL PATH
   matched no screenshot and lost its image and highlight. That is the exact
   remedy the ambiguity note tells the operator to use: "Cite the full path to
   resolve it." Following the tool's own advice silently degraded the output.

And the header counted the wrong thing. `pages_cited = len(page_screenshots)`
was printed as "N pages cited": a screenshot that failed (cmd_report logs it
and carries on) or two documents sharing a basename both made the header report
fewer pages than the cards below it displayed.

One fix landed in one of five places. The other four are here.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location("docparse", ROOT / "scripts" / "docparse.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dp = _load()

IMG = 'class="page-image"'
NOTE_RE = re.compile(r'<div class="cite-(?:ambiguous|caveat)">(.*?)</div>', re.S)

# A page whose extracted text is known, so "the quote is on it" and "the quote
# is not on it" are two different, controlled inputs rather than one accident.
PAGE_TEXT = "revenue was 4.2m"
ON_THE_PAGE = PAGE_TEXT
NOT_ON_THE_PAGE = "the ceiling is 9.8m"


def _parse_data(*paths, pages=(1,)):
    return {"files": [
        {"file": p, "file_name": Path(p).name,
         "pages": [{"page_num": n, "width_pt": 612, "height_pt": 792,
                    "text_items": [{"text": PAGE_TEXT, "x": 10, "y": 10,
                                    "width": 100, "height": 12}]}
                   for n in pages]}
        for p in paths
    ]}


def _shots(*keys):
    return dict.fromkeys(keys, b"\xff\xd8\xff\xe0-stand-in-jpeg")


def _render(file_ref, quote, page=1, parse_data=None, shots=None):
    return dp._generate_report_html(
        question="q", answer_md="a",
        citations=[{"id": 1, "file": file_ref, "page": page,
                    "quote": quote, "relevance": "r"}],
        page_screenshots=shots if shots is not None else _shots(("real.pdf", 1)),
        parse_data=parse_data if parse_data is not None else _parse_data("/docs/real.pdf"),
    )


def _notes(html_text):
    """The note bodies only.

    A plain `"cite-caveat" in html` would also match the STYLESHEET rule, which
    is present on every report ever rendered. That check passes on the very
    document it is meant to fail, so the notes are extracted by their opening
    tag instead.
    """
    return [n.strip() for n in NOTE_RE.findall(html_text)]


# ==========================================================================
# The healthy card is unchanged
# ==========================================================================

def test_a_quote_that_is_on_the_page_gets_its_image_its_highlight_and_no_note():
    out = _render("real.pdf", ON_THE_PAGE)
    assert IMG in out
    assert out.count("<rect ") == 1
    assert _notes(out) == [], "a good citation was given a caveat it does not need"


# ==========================================================================
# 1 - the quote the tool proved is not there
# ==========================================================================

def test_a_quote_absent_from_the_page_is_named_as_absent():
    out = _render("real.pdf", NOT_ON_THE_PAGE)
    notes = _notes(out)
    assert notes, "the tool established the quote is not on the page and said nothing"
    assert any("NOT found in the extracted text" in n for n in notes)


def test_the_absent_quote_note_names_the_page_and_the_document():
    out = _render("real.pdf", NOT_ON_THE_PAGE)
    note = " ".join(_notes(out))
    assert "page 1" in note
    assert "real.pdf" in note


def test_the_page_is_still_shown_for_an_absent_quote():
    """Withholding it would hide the evidence that refutes the citation.

    The reader needs to see the page to check the claim for themselves. What
    was missing was the sentence saying the tool could not find the quote on
    it, not the image.
    """
    out = _render("real.pdf", NOT_ON_THE_PAGE)
    assert IMG in out
    assert out.count("<rect ") == 0


def test_an_empty_quote_is_reported_as_nothing_to_locate():
    """Distinct from "not found": there was no claim to check in the first place."""
    out = _render("real.pdf", "   ")
    notes = _notes(out)
    assert any("no quote" in n for n in notes)
    assert not any("NOT found" in n for n in notes)


# ==========================================================================
# 2 - the page that is not in the parse data
# ==========================================================================

def test_a_page_outside_the_parse_data_is_named_and_gets_no_image():
    out = _render("real.pdf", ON_THE_PAGE, page=7,
                  shots=_shots(("real.pdf", 7)))
    assert IMG not in out, "a page image was shown for a page the parse data does not have"
    assert any("not among the parsed pages" in n for n in _notes(out))


def test_a_page_outside_the_parse_data_never_reaches_the_invented_page_size():
    """800x600 is the fallback, and it is not any real page.

    An image drawn inside a viewBox of an invented size puts every highlight in
    a made-up coordinate space, so the image is withheld rather than shown
    under a wrong frame.
    """
    out = _render("real.pdf", ON_THE_PAGE, page=7, shots=_shots(("real.pdf", 7)))
    assert "viewBox" not in out


# ==========================================================================
# 3 - the document that was never parsed
# ==========================================================================

def test_a_document_absent_from_the_parse_data_is_named_and_gets_no_image():
    out = _render("ghost.pdf", ON_THE_PAGE, shots=_shots(("ghost.pdf", 1)))
    assert IMG not in out
    assert any("No parsed document named" in n for n in _notes(out))


def test_the_absent_document_note_escapes_the_name():
    out = _render("<b>.pdf", ON_THE_PAGE, shots={})
    note = " ".join(_notes(out))
    assert "<b>" not in note, "a raw tag from a citation's file name reached the report"
    assert "&lt;b&gt;.pdf" in note


# ==========================================================================
# 4 - the full path, which is the remedy the tool itself recommends
# ==========================================================================

def test_citing_the_full_path_still_gets_the_page_image():
    """The ambiguity note says "Cite the full path to resolve it."

    `page_screenshots` is keyed by basename, so the full path matched no key
    and the advice cost the reader the image and the highlight it was given to
    protect.
    """
    out = _render("/docs/real.pdf", ON_THE_PAGE)
    assert IMG in out, "following the tool's own advice lost the page image"


def test_citing_the_full_path_still_gets_the_highlight():
    out = _render("/docs/real.pdf", ON_THE_PAGE)
    assert out.count("<rect ") == 1


def test_citing_the_full_path_earns_no_caveat():
    out = _render("/docs/real.pdf", ON_THE_PAGE)
    assert _notes(out) == []


def test_the_basename_spelling_and_the_full_path_spelling_agree():
    """Two ways of naming one document must produce the same evidence."""
    by_name = _render("real.pdf", ON_THE_PAGE)
    by_path = _render("/docs/real.pdf", ON_THE_PAGE)
    assert (IMG in by_name) == (IMG in by_path)
    assert by_name.count("<rect ") == by_path.count("<rect ")


# ==========================================================================
# 5 - a screenshot that was never captured
# ==========================================================================

def test_a_missing_screenshot_is_named_rather_than_left_blank():
    out = _render("real.pdf", ON_THE_PAGE, shots={})
    assert IMG not in out
    assert any("No page image was captured" in n for n in _notes(out))


def test_a_missing_screenshot_and_an_absent_quote_are_both_reported():
    """Independent problems. A second one hidden behind the first is the defect again."""
    notes = _notes(_render("real.pdf", NOT_ON_THE_PAGE, shots={}))
    assert any("NOT found in the extracted text" in n for n in notes)
    assert any("No page image was captured" in n for n in notes)


# ==========================================================================
# The ambiguous name keeps its own note, and only its own
# ==========================================================================

def test_an_ambiguous_name_still_gets_the_ambiguity_note_and_no_image():
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    out = _render("report.pdf", ON_THE_PAGE, parse_data=pd,
                  shots=_shots(("report.pdf", 1)))
    assert IMG not in out
    assert any("More than one parsed document" in n for n in _notes(out))


def test_an_ambiguous_name_is_not_also_told_its_screenshot_is_missing():
    """The one note already explains the withheld image; a second is noise."""
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    notes = _notes(_render("report.pdf", ON_THE_PAGE, parse_data=pd,
                           shots=_shots(("report.pdf", 1))))
    assert len(notes) == 1


def test_the_caveat_class_has_a_stylesheet_rule():
    """A note rendered with no rule for its class is a note nobody sees."""
    out = _render("real.pdf", NOT_ON_THE_PAGE)
    assert ".cite-caveat {" in out


# ==========================================================================
# The header counts what its caption says
# ==========================================================================

def _multi_page_report(shots):
    pd = _parse_data("/docs/real.pdf", pages=(1, 2, 3))
    citations = [{"id": i, "file": "real.pdf", "page": p, "quote": ON_THE_PAGE,
                  "relevance": "r"} for i, p in enumerate((1, 2, 3), 1)]
    return dp._generate_report_html(
        question="q", answer_md="a", citations=citations,
        page_screenshots=shots, parse_data=pd)


def _header(html_text):
    return next(line for line in html_text.splitlines() if "pages cited" in line)


def test_the_pages_cited_count_does_not_move_when_a_screenshot_fails():
    every = _shots(*[("real.pdf", n) for n in (1, 2, 3)])
    assert "3 pages cited" in _header(_multi_page_report(every))
    partial = _shots(("real.pdf", 1), ("real.pdf", 2))
    assert "3 pages cited" in _header(_multi_page_report(partial))


def test_the_pages_cited_count_survives_every_screenshot_failing():
    """It read `len(page_screenshots)`, so a run whose screenshots all failed
    printed "0 pages cited" above three citation cards."""
    assert "3 pages cited" in _header(_multi_page_report({}))


def test_two_citations_on_one_page_count_as_one_cited_page():
    pd = _parse_data("/docs/real.pdf")
    citations = [{"id": i, "file": "real.pdf", "page": 1, "quote": ON_THE_PAGE,
                  "relevance": "r"} for i in (1, 2)]
    out = dp._generate_report_html(
        question="q", answer_md="a", citations=citations,
        page_screenshots=_shots(("real.pdf", 1)), parse_data=pd)
    assert "1 pages cited" in _header(out)
    assert "2 citations" in _header(out)


def test_the_file_list_in_the_header_is_ordered():
    """It was built from a bare `set`, so two runs over identical input put the
    names in different orders in a document the operator keeps."""
    pd = _parse_data("/docs/a.pdf", "/docs/b.pdf", "/docs/c.pdf")
    citations = [{"id": i, "file": n, "page": 1, "quote": ON_THE_PAGE,
                  "relevance": "r"}
                 for i, n in enumerate(("c.pdf", "a.pdf", "b.pdf"), 1)]
    header = _header(dp._generate_report_html(
        question="q", answer_md="a", citations=citations,
        page_screenshots={}, parse_data=pd))
    assert header.index("a.pdf") < header.index("b.pdf") < header.index("c.pdf")


# ==========================================================================
# 6 - the cut the delivered document never mentioned
# ==========================================================================

def test_a_capped_report_says_so_in_the_document():
    """`--max-pages` warned on stderr and nowhere else.

    The HTML is the artifact the operator keeps and forwards. A reader opening
    it later saw cards with no page image and no way to learn the tool CHOSE
    not to capture them rather than failed to.
    """
    out = dp._generate_report_html(
        question="q", answer_md="a",
        citations=[{"id": 1, "file": "real.pdf", "page": 1,
                    "quote": ON_THE_PAGE, "relevance": "r"}],
        page_screenshots=_shots(("real.pdf", 1)),
        parse_data=_parse_data("/docs/real.pdf"),
        capped_pages=4)
    assert "limited to a maximum number of" in out
    assert "4 cited page(s)" in out


def test_an_uncapped_report_carries_no_cap_note():
    out = _render("real.pdf", ON_THE_PAGE)
    assert "limited to a maximum number of" not in out


def test_the_cap_note_defaults_to_absent():
    """The parameter is optional, so an old caller must not grow a warning."""
    out = dp._generate_report_html(
        question="q", answer_md="a",
        citations=[{"id": 1, "file": "real.pdf", "page": 1,
                    "quote": ON_THE_PAGE, "relevance": "r"}],
        page_screenshots=_shots(("real.pdf", 1)),
        parse_data=_parse_data("/docs/real.pdf"))
    assert "limited to a maximum number of" not in out


def _run_cmd_report(monkeypatch, tmp_path, citations, parse_data, max_pages):
    """Drive `cmd_report` end to end with liteparse stubbed.

    Written because a test that calls `_generate_report_html(capped_pages=N)`
    proves the note renders and proves NOTHING about whether the cap ever
    computes N. That is the gap a mutation on the arithmetic walks straight
    through.
    """
    import json
    parse_json = tmp_path / "p.json"
    cit_json = tmp_path / "c.json"
    parse_json.write_text(json.dumps(parse_data), encoding="utf-8")
    cit_json.write_text(json.dumps(
        {"question": "q", "answer_md": "a", "citations": citations}), encoding="utf-8")

    class _Shot:
        def __init__(self, page_num):
            self.page_num = page_num
            self.image_bytes = b"\x89PNG\r\n\x1a\n-stand-in"

    class _Parser:
        def screenshot(self, fpath, target_pages="", **k):
            pages = [int(p) for p in target_pages.split(",") if p]
            return type("R", (), {"screenshots": [_Shot(p) for p in pages]})()

    monkeypatch.setitem(
        sys.modules, "liteparse",
        type("m", (), {"LiteParse": staticmethod(lambda *a, **k: _Parser())}))
    monkeypatch.setattr(dp.shutil, "which", lambda name: "/usr/bin/liteparse")
    monkeypatch.setattr(dp, "_png_to_jpeg", lambda data, quality=85: data)

    args = type("A", (), {
        "parse_json": str(parse_json), "citations": str(cit_json),
        "output_dir": str(tmp_path), "max_pages": max_pages,
        "title": "t", "no_pdf": True,
    })()
    dp.cmd_report(args)
    return (tmp_path / "docparse-report.html").read_text(encoding="utf-8")


def test_cmd_report_writes_the_cap_into_the_document(monkeypatch, tmp_path):
    pd = _parse_data("/docs/real.pdf", pages=(1, 2, 3, 4, 5))
    citations = [{"id": i, "file": "real.pdf", "page": p, "quote": ON_THE_PAGE,
                  "relevance": "r"} for i, p in enumerate(range(1, 6), 1)]
    out = _run_cmd_report(monkeypatch, tmp_path, citations, pd, max_pages=2)
    assert "limited to a maximum number of" in out
    assert "3 cited page(s)" in out, "the dropped-page count was not computed from the cut"


def test_cmd_report_writes_no_cap_note_when_nothing_was_cut(monkeypatch, tmp_path):
    pd = _parse_data("/docs/real.pdf", pages=(1, 2))
    citations = [{"id": i, "file": "real.pdf", "page": p, "quote": ON_THE_PAGE,
                  "relevance": "r"} for i, p in enumerate((1, 2), 1)]
    out = _run_cmd_report(monkeypatch, tmp_path, citations, pd, max_pages=20)
    assert "limited to a maximum number of" not in out


def test_cmd_report_still_counts_every_cited_page_after_a_cut(monkeypatch, tmp_path):
    """The header reports the citations, not what survived the cap."""
    pd = _parse_data("/docs/real.pdf", pages=(1, 2, 3, 4, 5))
    citations = [{"id": i, "file": "real.pdf", "page": p, "quote": ON_THE_PAGE,
                  "relevance": "r"} for i, p in enumerate(range(1, 6), 1)]
    out = _run_cmd_report(monkeypatch, tmp_path, citations, pd, max_pages=2)
    assert "5 pages cited" in _header(out)
