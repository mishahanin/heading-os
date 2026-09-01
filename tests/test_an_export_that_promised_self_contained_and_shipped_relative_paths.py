#!/usr/bin/env python3
"""Three quoting blind spots in `scripts/pencil-export.py`.

`resolve_fonts` and `inline_html` between them promise a portable single file:
"Existing @font-face rules that point at missing `assets/*` files are re-pointed
at resolved fonts too", "inline every remaining url()/src (images) as base64",
and `main` prints the result as "(self-contained)". Each promise was written
against one CSS spelling and CSS has three.

1. `_embed_src` matched an unquoted `src:url(assets/x.woff2)` - the pattern says
   so explicitly - but rewrote only the `"url"` and `'url'` spellings, so the
   unquoted form kept pointing at a file the export does not contain. No warning
   fired, because the font file itself WAS found.
2. `inline_html` matched only `url("...")` / `url('...')` and only `src="..."`.
   An unquoted `url(images/a.png)` and a single-quoted `src='images/a.png'` are
   both valid and both survived, so the "portable" HTML 404s its images the
   moment it moves off the export directory.
3. `resolve_fonts` read `font-family` only in its double-quoted form. A family
   written `font-family:'Brand'` was invisible to BOTH the `used` and the
   `declared` sets at once, so no @font-face was synthesised and the verbose
   "no font file for used family" line could not fire either.

Hermetic: no playwright, no Pencil, no real font binary. The bytes need not be a
font for the rewriting to be checkable.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# hyphenated filename -> load as a module by path
_spec = importlib.util.spec_from_file_location(
    "pencil_export_quoting", ROOT / "scripts" / "pencil-export.py"
)
pencil_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pencil_export)

# Invented brand names. Engine law: no real entity may appear in this repo.
FAMILY = "Bond Sans"
FONT_FILE = "Bond-Sans-Regular.woff2"
DATA_PREFIX = "data:font/woff2;base64,"


def _font_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fonts"
    d.mkdir()
    (d / FONT_FILE).write_bytes(b"not-really-a-woff2")
    return d


# ------------------------------------------------------------------
# 1. @font-face src re-pointing, all three quotings
# ------------------------------------------------------------------

def _face(src: str) -> str:
    return '<head></head><style>@font-face{font-family:"%s";src:url(%s)}</style>' % (
        FAMILY, src,
    )


def test_an_unquoted_font_face_src_is_re_pointed(tmp_path):
    out = pencil_export.resolve_fonts(_face(f"assets/{FONT_FILE}"), [_font_dir(tmp_path)])
    assert f"assets/{FONT_FILE}" not in out, out
    assert DATA_PREFIX in out


def test_the_quoted_font_face_srcs_are_still_re_pointed(tmp_path):
    fonts = _font_dir(tmp_path)
    for src in (f'"assets/{FONT_FILE}"', f"'assets/{FONT_FILE}'"):
        out = pencil_export.resolve_fonts(_face(src), [fonts])
        assert f"assets/{FONT_FILE}" not in out, (src, out)
        assert DATA_PREFIX in out, src


def test_an_already_inlined_src_is_left_alone(tmp_path):
    html = _face("data:font/woff2;base64,QUJD")
    assert pencil_export.resolve_fonts(html, [_font_dir(tmp_path)]) == html


def test_a_font_face_with_no_matching_file_is_left_alone(tmp_path):
    html = _face("assets/Nonexistent-Face.woff2")
    out = pencil_export.resolve_fonts(html, [_font_dir(tmp_path)])
    assert "assets/Nonexistent-Face.woff2" in out


# ------------------------------------------------------------------
# 2. inline_html: unquoted url() and single-quoted src
# ------------------------------------------------------------------

def _work_dir(tmp_path: Path) -> Path:
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n-fake")
    return tmp_path


def _inline(tmp_path: Path, body: str) -> str:
    work = _work_dir(tmp_path) / "work.html"
    work.write_text(body, encoding="utf-8")
    return pencil_export.inline_html(work, tmp_path / "out.html").read_text(encoding="utf-8")


def test_an_unquoted_css_url_is_inlined(tmp_path):
    out = _inline(tmp_path, '<div style="background:url(images/a.png)"></div>')
    assert "images/a.png" not in out, out
    assert "data:image/png;base64," in out


def test_a_single_quoted_src_attribute_is_inlined(tmp_path):
    out = _inline(tmp_path, "<img src='images/a.png'>")
    assert "images/a.png" not in out, out
    assert "data:image/png;base64," in out


def test_the_forms_that_already_worked_still_work(tmp_path):
    out = _inline(
        tmp_path,
        '<img src="images/a.png">'
        "<div style=\"background:url('images/a.png')\"></div>"
        '<div style="background:url(&#34;images/a.png&#34;)"></div>'.replace("&#34;", '"'),
    )
    assert "images/a.png" not in out, out
    assert out.count("data:image/png;base64,") == 3


def test_absolute_and_missing_references_are_left_alone(tmp_path):
    body = (
        '<img src="data:image/png;base64,QUJD">'
        '<img src="https://example.invalid/y.png">'
        '<img src="file:///tmp/z.png">'
        '<div style="background:url(images/gone.png)"></div>'
    )
    assert _inline(tmp_path, body) == body


@pytest.mark.parametrize("body", ['<img src="images/gone.png">',
                                  "<img src='images/gone.png'>"],
                         ids=["double-quoted", "single-quoted"])
def test_a_missing_src_keeps_its_path_and_does_not_become_the_word_None(
        tmp_path, body):
    """`_src`'s `if u else m.group(0)`, which nothing was measuring.

    The row above covers the `url()` branch's missing file and the SCHEME cases
    for `src=`, but never a `src=` pointing at a file that is simply not there.
    MEASURED 2026-09-01: dropping the `else m.group(0)` from `_src` left this
    file green at 12 passed, and a missing image then rendered as
    `src="None"` - the literal four characters. That is strictly worse than the
    relative path it replaced: a relative path still resolves for anyone who
    keeps the export directory, and `src="None"` resolves for nobody and names
    no file the operator can go and find.

    Both quotings, because `_src` handles both and each writes the quote back
    from its own capture group. Parametrized rather than looped: `_work_dir`
    calls `mkdir()` without `exist_ok`, so a second `_inline` under one
    `tmp_path` raises `FileExistsError` before reaching the assertion.
    """
    out = _inline(tmp_path, body)
    assert out == body, out
    assert "None" not in out


def test_an_already_inlined_src_survives_a_basename_that_would_resolve(tmp_path):
    """The `url.startswith("data:")` early-out in `_embed_src`.

    The existing already-inlined row cannot see it: `Path("data:font/woff2;
    base64,QUJD").name` is `base64,QUJD`, which resolves to no font, so the
    function returns the rule untouched with or without the guard. Measured
    2026-09-01, deleting the guard left this file green at 12 passed.

    The base64 alphabet INCLUDES `/`, so the tail of a real payload after its
    last slash is an arbitrary string - and if it ever coincided with a font
    file's name, `_find_font_file`'s `by_basename` branch would hit and the
    export would swap an already-embedded face for a different file's bytes.
    The construction below is that coincidence, made deterministic.
    """
    inlined = f"data:font/woff2;base64,QUJD/{FONT_FILE}"
    html = _face(inlined)
    fonts = _font_dir(tmp_path)
    # The premise: without the guard this URL WOULD resolve to a real file.
    assert pencil_export._find_font_file(
        FAMILY, [fonts], by_basename=Path(inlined).name) is not None
    assert pencil_export.resolve_fonts(html, [fonts]) == html


# ------------------------------------------------------------------
# 3. single-quoted font-family
# ------------------------------------------------------------------

def test_a_single_quoted_family_gets_a_synthesised_font_face(tmp_path):
    html = "<head></head><div style=\"font-family:'%s'\">x</div>" % FAMILY
    out = pencil_export.resolve_fonts(html, [_font_dir(tmp_path)])
    assert "@font-face" in out, out
    assert DATA_PREFIX in out


def test_a_double_quoted_family_still_gets_one(tmp_path):
    html = '<head></head><div style=\'font-family:"%s"\'>x</div>' % FAMILY
    out = pencil_export.resolve_fonts(html, [_font_dir(tmp_path)])
    assert "@font-face" in out, out


def test_a_family_already_declared_in_single_quotes_is_not_synthesised_twice(tmp_path):
    html = (
        "<head></head><style>@font-face{font-family:'%s';"
        "src:url(data:font/woff2;base64,QUJD)}</style>"
        "<div style=\"font-family:'%s'\">x</div>" % (FAMILY, FAMILY)
    )
    out = pencil_export.resolve_fonts(html, [_font_dir(tmp_path)])
    assert out.count("@font-face") == 1, out


def test_a_used_family_with_no_font_file_is_reported_not_invented(tmp_path, capsys):
    html = "<head></head><div style=\"font-family:'Q Branch Mono'\">x</div>"
    out = pencil_export.resolve_fonts(html, [_font_dir(tmp_path)], verbose=True)
    assert "@font-face" not in out
    assert "Q Branch Mono" in capsys.readouterr().out
