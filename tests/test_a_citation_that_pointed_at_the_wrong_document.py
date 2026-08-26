"""Shard 05-p1 (docparse): four ways a confident citation pointed somewhere else.

`_cache_key` was computed from the FILE -- path, size, mtime, password -- while
`parse_document` varies on `pages` and `dpi` too. A run with `--pages 1-2` wrote
a two-page document into the cache, and every later full parse of that file was
served those two pages as the whole thing. The cached dict even carries
`"dpi": dpi` from whichever run populated it, so the substitution labelled
itself accurately while being wrong.

`_find_page_in_parse` matched on BASENAME, and `parse --files dirA dirB`
auto-discovers both, so two different documents called `report.pdf` collided.
The first one found answered for both: a citation about the second rendered the
first document's page image and the first document's highlight boxes, with
nothing on the card to say so.

`find_boxes_for_quote` re-implements `_normalize_text` inline, because it has to
carry a character-to-item mapping along. The two copies disagreed twice. On
whitespace: `_normalize_text` collapses `\\s+`, the inline walk tested
`c in " \\t\\n\\r"`, and `\\s` also matches U+000B, U+000C (form feed, ordinary in
extracted PDF text), U+0085, U+2028 and U+2029 -- so a quote spanning a form
feed missed, rendered without a highlight, and tripped the drift warning that
exists to catch exactly that. On case: `.lower()` was applied to the joined
string while the mapping kept its pre-lowering length, so a character that
lowercases to two code points (U+0130) shifted every index after it and moved
the boxes onto neighbouring text -- silently, because the guard lowercased both
sides identically and never saw a difference.

The report's FIFTH finding is refuted here rather than fixed: see
`test_the_windows_case_claim_is_true_where_it_is_made`.

Tests: this file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location("docparse_05p1", ROOT / "scripts" / "docparse.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["docparse_05p1"] = mod
    spec.loader.exec_module(mod)
    return mod


dp = _load()


@pytest.fixture()
def doc(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fixture")
    return p


# ==========================================================================
# 1 - the cache key that answered for a request it never saw
# ==========================================================================

def test_a_page_range_changes_the_cache_key(doc):
    """The defect verbatim: `--pages 1-2` must not answer a full parse."""
    assert dp._cache_key(doc, "", "1-2", 150) != dp._cache_key(doc, "", None, 150)


def test_two_different_page_ranges_do_not_share_an_entry(doc):
    assert dp._cache_key(doc, "", "1-2", 150) != dp._cache_key(doc, "", "3-4", 150)


def test_no_page_range_does_not_collide_with_a_real_one(doc):
    """The absent-range sentinel must be a value no `--pages` string can be."""
    assert dp._cache_key(doc, "", None, 150) != dp._cache_key(doc, "", "1", 150)


def test_the_sentinel_is_not_a_page_number(doc):
    assert dp._cache_key(doc, "", None, 150) != dp._cache_key(doc, "", "0", 150)


def test_an_explicit_all_is_the_same_request_as_none(doc):
    """`--pages all` is not a page range this tool accepts, so the collision
    it would cause is theoretical -- recorded so the choice is deliberate."""
    assert dp._cache_key(doc, "", "all", 150) == dp._cache_key(doc, "", None, 150)


def test_dpi_changes_the_cache_key(doc):
    assert dp._cache_key(doc, "", None, 150) != dp._cache_key(doc, "", None, 300)


def test_the_same_request_still_hits(doc):
    assert dp._cache_key(doc, "", "1-2", 150) == dp._cache_key(doc, "", "1-2", 150)


def test_the_password_still_separates_entries(doc):
    assert dp._cache_key(doc, "secret", None, 150) != dp._cache_key(doc, "", None, 150)


def test_the_default_arguments_match_an_explicit_full_parse(doc):
    """A caller omitting both must land where `parse_document` lands."""
    assert dp._cache_key(doc) == dp._cache_key(doc, "", None, dp.DEFAULT_DPI)


def test_a_changed_file_still_invalidates(doc):
    before = dp._cache_key(doc, "", None, 150)
    doc.write_bytes(b"%PDF-1.4 fixture, longer now")
    assert dp._cache_key(doc, "", None, 150) != before


def test_two_files_do_not_share_a_key(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"x"), b.write_bytes(b"x")
    assert dp._cache_key(a) != dp._cache_key(b)


def test_parse_document_passes_the_request_into_the_key(doc, monkeypatch):
    """The key must be built from THIS call's arguments, not the file alone."""
    seen = []
    monkeypatch.setattr(dp, "_cache_key", lambda *a, **k: (seen.append((a, k)), "k")[1])
    monkeypatch.setattr(dp, "_cache_get", lambda key: {"pages": []})
    dp.parse_document(doc, pages="7-9", dpi=201)
    args, _kwargs = seen[0]
    assert "7-9" in args, "the page range never reached the cache key"
    assert 201 in args, "the dpi never reached the cache key"


def test_the_key_is_a_hex_digest(doc):
    key = dp._cache_key(doc)
    assert len(key) == 64 and int(key, 16) >= 0


def test_the_key_is_not_the_bare_path_hash(doc):
    """A key that ignored the request would equal the old path-only digest."""
    stat = doc.stat()
    old = hashlib.sha256(
        f"{doc.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    ).hexdigest()
    assert dp._cache_key(doc) != old


# ==========================================================================
# 2 - the name that identified two documents
# ==========================================================================

def _parse_data(*paths):
    return {"files": [
        {"file": p, "file_name": Path(p).name,
         "pages": [{"page_num": 1, "text_items": [], "width_pt": 10, "height_pt": 10}]}
        for p in paths
    ]}


def test_a_shared_basename_resolves_to_nothing():
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    parse_file, ambiguous = dp._resolve_parse_file(pd, "report.pdf")
    assert parse_file is None
    assert ambiguous is True, "the first match answered for a name meaning two files"


def test_a_full_path_resolves_the_ambiguity():
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    parse_file, ambiguous = dp._resolve_parse_file(pd, "/b/report.pdf")
    assert ambiguous is False
    assert parse_file["file"] == "/b/report.pdf"


def test_a_unique_basename_still_resolves():
    pd = _parse_data("/a/report.pdf", "/b/other.pdf")
    parse_file, ambiguous = dp._resolve_parse_file(pd, "other.pdf")
    assert ambiguous is False
    assert parse_file["file"] == "/b/other.pdf"


def test_an_unknown_name_is_missing_not_ambiguous():
    pd = _parse_data("/a/report.pdf")
    assert dp._resolve_parse_file(pd, "absent.pdf") == (None, False)


def test_three_matches_are_ambiguous_too():
    pd = _parse_data("/a/r.pdf", "/b/r.pdf", "/c/r.pdf")
    assert dp._resolve_parse_file(pd, "r.pdf")[1] is True


def test_find_page_reports_the_ambiguity_to_its_caller():
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    page, ambiguous = dp._find_page_in_parse(pd, "report.pdf", 1)
    assert page is None and ambiguous is True


def test_find_page_still_returns_the_page_when_the_name_is_unique():
    pd = _parse_data("/a/report.pdf")
    page, ambiguous = dp._find_page_in_parse(pd, "report.pdf", 1)
    assert ambiguous is False and page["page_num"] == 1


def test_a_missing_page_number_is_not_ambiguity():
    pd = _parse_data("/a/report.pdf")
    assert dp._find_page_in_parse(pd, "report.pdf", 99) == (None, False)


def _render(citations, pd, shots=None):
    return dp._generate_report_html(
        question="q", answer_md="a", citations=citations,
        page_screenshots=shots or {}, parse_data=pd,
    )


def test_the_report_names_the_ambiguity_on_the_card():
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    out = _render([{"id": 1, "file": "report.pdf", "page": 1, "quote": "x"}], pd)
    assert '<div class="cite-ambiguous">' in out
    assert "More than one parsed document" in out


def test_the_report_withholds_the_image_when_the_name_is_ambiguous():
    """The image is the part a reader trusts; an unresolved name must not get one."""
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    shots = {("report.pdf", 1): b"\xff\xd8\xff-not-really-a-jpeg"}
    out = _render([{"id": 1, "file": "report.pdf", "page": 1, "quote": "x"}], pd, shots)
    assert 'class="page-image"' not in out, "a page image was shown under an unresolved name"


def test_an_unambiguous_citation_still_gets_its_image():
    pd = _parse_data("/a/report.pdf")
    shots = {("report.pdf", 1): b"\xff\xd8\xff-not-really-a-jpeg"}
    out = _render([{"id": 1, "file": "report.pdf", "page": 1, "quote": "x"}], pd, shots)
    assert 'class="page-image"' in out
    assert "More than one parsed document" not in out


def _note_html(out: str) -> str:
    """The ambiguity note only. The card header escapes the same name, so a
    search over the whole document finds the escaped form either way."""
    start = out.index('<div class="cite-ambiguous">')
    return out[start:out.index("</div>", start)]


def test_the_ambiguity_note_escapes_the_file_name():
    pd = _parse_data("/a/<b>.pdf", "/b/<b>.pdf")
    out = _render([{"id": 1, "file": "<b>.pdf", "page": 1, "quote": "x"}], pd)
    note = _note_html(out)
    assert "<b>" not in note, "a raw tag from a file name reached the report"
    assert "&lt;b&gt;.pdf" in note


def test_the_ambiguous_style_is_defined():
    """A note rendered with no rule for its class is a note nobody sees."""
    out = _render([{"id": 1, "file": "r.pdf", "page": 1, "quote": "x"}],
                  _parse_data("/a/r.pdf", "/b/r.pdf"))
    assert ".cite-ambiguous {" in out, "the class was renamed out of the stylesheet"


def test_the_screenshot_pass_skips_an_ambiguous_name(capsys, monkeypatch, tmp_path):
    """`cmd_report` must not queue a screenshot it cannot attribute."""
    pd = _parse_data("/a/report.pdf", "/b/report.pdf")
    parse_json = tmp_path / "p.json"
    cit_json = tmp_path / "c.json"
    import json
    parse_json.write_text(json.dumps(pd), encoding="utf-8")
    cit_json.write_text(json.dumps({
        "question": "q", "answer_md": "a",
        "citations": [{"id": 1, "file": "report.pdf", "page": 1, "quote": "x"}],
    }), encoding="utf-8")

    shot_calls = []

    class _Parser:
        def screenshot(self, *a, **k):
            shot_calls.append(a)
            raise AssertionError("an ambiguous citation was screenshotted")

    # `cmd_report` does `from liteparse import LiteParse` INSIDE the function
    # (docparse.py:972), so a module attribute on `dp` is shadowed the moment
    # the function runs. sys.modules is what that local import reads. This was
    # `monkeypatch.setattr(dp, "LiteParse", ..., raising=False)`, which bound a
    # name nothing looks up: `_Parser` was never built, the real LiteParse ran,
    # and `shot_calls` stayed empty whatever the code did. The assertion below
    # was true by construction rather than by behaviour.
    monkeypatch.setitem(
        sys.modules, "liteparse",
        type("m", (), {"LiteParse": staticmethod(lambda *a, **k: _Parser())}))
    monkeypatch.setattr(dp.shutil, "which", lambda name: "/usr/bin/liteparse")

    args = type("A", (), {
        "parse_json": str(parse_json), "citations": str(cit_json),
        "output_dir": str(tmp_path), "max_pages": 20, "title": "t", "no_pdf": True,
    })()
    dp.cmd_report(args)
    assert not shot_calls
    assert "AMBIGUOUS" in capsys.readouterr().err


# ==========================================================================
# 3 - the whitespace the two copies disagreed about
# ==========================================================================

def _one_item(text):
    return [{"text": text, "x": 0, "y": 0, "width": 100, "height": 10}]


@pytest.mark.parametrize("sep", ["\x0c", "\x0b", "", "\u2028", "\u2029"])
def test_a_quote_spanning_an_exotic_whitespace_still_matches(sep):
    """`\\s` matches all of these; the inline copy tested only four characters."""
    assert dp.find_boxes_for_quote(_one_item(f"total{sep}amount"), "total amount")


@pytest.mark.parametrize("sep", [" ", "\t", "\n", "\r"])
def test_the_four_ordinary_whitespace_characters_still_match(sep):
    assert dp.find_boxes_for_quote(_one_item(f"total{sep}amount"), "total amount")


def test_no_drift_warning_on_an_exotic_separator(capsys):
    """The guard cried wolf about the very desync it was added to catch."""
    dp.find_boxes_for_quote(_one_item("total\x0camount"), "total amount")
    assert "drifted" not in capsys.readouterr().err


def test_a_run_of_whitespace_collapses_to_one_space():
    assert dp.find_boxes_for_quote(_one_item("total \t\n amount"), "total amount")


def test_a_genuinely_absent_quote_still_returns_nothing():
    assert dp.find_boxes_for_quote(_one_item("total amount"), "net position") == []


def test_the_inline_walk_and_normalize_text_share_one_pattern():
    """One literal, two compiled forms -- so they cannot drift apart again."""
    assert dp._WS_CHAR_RE.pattern == dp._WS_PATTERN
    assert dp._WS_RUN_RE.pattern == dp._WS_PATTERN + "+"


@pytest.mark.parametrize("ch", ["\x0c", "\x0b", "", "\u2028", "\u2029"])
def test_normalize_text_treats_them_as_whitespace_too(ch):
    assert dp._normalize_text(f"a{ch}b") == "a b"


# ==========================================================================
# 4 - the lowercase that was longer than the character it replaced
# ==========================================================================

def test_a_two_code_point_lowercase_does_not_shift_the_boxes():
    """U+0130 lowercases to two code points; the mapping must grow with it.

    The first version of this test used `["İstanbul ", "report"]` and could not
    fail: the off-by-one shifts the mapping one place left, and every index it
    then reads still belongs to the same item, so a broken mapping returned the
    right answer. Single-character items put an item boundary at every index,
    which is where the shift becomes visible.
    """
    items = [{"text": "İ", "x": 0, "y": 0, "width": 10, "height": 10},
             {"text": "x", "x": 20, "y": 0, "width": 10, "height": 10},
             {"text": "z", "x": 40, "y": 0, "width": 10, "height": 10}]
    boxes = dp.find_boxes_for_quote(items, "x", dpi=72)
    assert len(boxes) == 1
    assert boxes[0]["x"] == 20, "the highlight landed on a different fragment"


def test_a_quote_spanning_the_shift_keeps_both_items():
    items = [{"text": "İ", "x": 0, "y": 0, "width": 10, "height": 10},
             {"text": "x", "x": 20, "y": 0, "width": 10, "height": 10},
             {"text": "z", "x": 40, "y": 0, "width": 10, "height": 10}]
    xs = sorted(b["x"] for b in dp.find_boxes_for_quote(items, "xz", dpi=72))
    # A 10pt gap against a 5pt merge threshold, so these stay two boxes.
    assert xs == [20, 40], "the shifted mapping dropped one of the two items"


def test_the_last_item_is_still_reachable_after_an_expansion():
    """The shifted mapping is one short, so the final index falls off the end."""
    items = [{"text": "İ", "x": 0, "y": 0, "width": 10, "height": 10},
             {"text": "x", "x": 20, "y": 0, "width": 10, "height": 10},
             {"text": "z", "x": 40, "y": 0, "width": 10, "height": 10}]
    boxes = dp.find_boxes_for_quote(items, "z", dpi=72)
    assert boxes, "the match produced no box at all"
    assert boxes[0]["x"] == 40


def test_the_expanding_character_is_really_expanding():
    """Pins the premise: without it the test above proves nothing."""
    assert len("İ".lower()) == 2


def test_matching_stays_case_insensitive():
    assert dp.find_boxes_for_quote(_one_item("Total Amount"), "total amount")


def test_an_uppercase_quote_matches_lowercase_text():
    assert dp.find_boxes_for_quote(_one_item("total amount"), "TOTAL AMOUNT")


def test_no_drift_warning_on_the_expanding_character(capsys):
    items = [{"text": "İstanbul report", "x": 0, "y": 0, "width": 50, "height": 10}]
    dp.find_boxes_for_quote(items, "report")
    assert "drifted" not in capsys.readouterr().err


def test_the_drift_guard_still_fires_when_the_copies_really_disagree(monkeypatch, capsys):
    """Weakening the guard would be the cheap way to pass the tests above."""
    monkeypatch.setattr(dp, "_normalize_text", lambda text: "deliberately different")
    dp.find_boxes_for_quote(_one_item("total amount"), "total")
    assert "drifted" in capsys.readouterr().err


def test_a_quote_crossing_two_items_returns_a_merged_box():
    items = [{"text": "total ", "x": 0, "y": 0, "width": 30, "height": 10},
             {"text": "amount", "x": 30, "y": 0, "width": 30, "height": 10}]
    assert dp.find_boxes_for_quote(items, "total amount", dpi=72)


def test_empty_input_is_still_empty_output():
    assert dp.find_boxes_for_quote([], "anything") == []
    assert dp.find_boxes_for_quote(_one_item("text"), "") == []


# ==========================================================================
# 5 - the finding that was refuted, recorded so it is not re-derived
# ==========================================================================

def test_the_windows_case_claim_is_true_where_it_is_made():
    """Report F5 said `Path.resolve()` does not case-normalize on Windows.

    It does, for a file that exists. `ntpath.realpath` calls
    `nt._getfinalpathname` (`GetFinalPathNameByHandle`), which returns the path
    as stored on disk, casing included; the non-strict fallback that leaves a
    tail un-canonicalized is only reached when the path cannot be opened.
    `_cache_key` calls `stat()` before `resolve()`, so the strict route is the
    one it takes. The docstring now says why, instead of just asserting it.

    Asserted on the source of the stdlib rather than on behaviour, because this
    is Linux and the Windows branch cannot execute here -- which is also why the
    claim went unchecked for so long.
    """
    ntpath_src = Path(sys.modules["ntpath"].__file__).read_text(encoding="utf-8") \
        if "ntpath" in sys.modules else None
    if ntpath_src is None:
        import ntpath
        ntpath_src = Path(ntpath.__file__).read_text(encoding="utf-8")
    body = ntpath_src[ntpath_src.index("    def realpath(path, *, strict=False):"):]
    assert "path = _getfinalpathname(path)" in body, \
        "realpath no longer canonicalizes through the filesystem; recheck the docstring"


def test_the_docstring_states_why_the_windows_claim_holds():
    doc = dp._cache_key.__doc__
    assert "stat()" in doc, "the claim is asserted without the condition that makes it true"
    assert "_getfinalpathname" in doc
