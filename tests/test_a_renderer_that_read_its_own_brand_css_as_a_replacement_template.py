#!/usr/bin/env python3
"""Shard scripts-05-p2: three ways `scripts/design-studio.py` lost a render.

1. THE BRAND CSS WAS READ AS A REPLACEMENT TEMPLATE. `inject_brand_css` spliced
   the stylesheet into the document with `re.sub(r'</head>', style_tag, ...)`,
   and the replacement argument of `re.sub` is a TEMPLATE, not a literal: every
   backslash in the CSS is an escape. `brand.css` is font-heavy, which is
   exactly where glyph rules live, so `content: "\\e900"` raised
   `re.error: bad escape \\e` out of the render and `content: "\\f0c9"` exited 0
   and shipped a PNG whose injected CSS held a form feed followed by `0c9`.

2. THE SOURCE HTML OVERWROTE THE DELIVERABLE. `save_source_html` derived its
   path with `output_path.with_suffix(".html")`, which returns the path
   UNCHANGED when the output already ends `.html`, and nothing compared the
   two. `render --html "<p>hi</p>" -o /tmp/post.html` screenshotted to that
   path, printed "[OK] Screenshot saved", and then wrote the HTML source over
   it. The operator was left with no PNG at all under a message saying one
   existed.

3. `scratch_name`'S DOCSTRING DESCRIBED A LIVE DEFECT THAT HAD BEEN FIXED. It
   said the default artifact names were still the one-second
   `render-{timestamp()}.png` in a shared directory, while `cmd_render` and
   `cmd_pdf` both route their default through `scratch_name`, which is PID- and
   sequence-unique. A reader acting on it would hunt a non-bug, or "fix" code
   that is already correct.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STUDIO = ROOT / "scripts" / "design-studio.py"


@pytest.fixture(scope="module")
def ds():
    """Load design-studio.py as a module (hyphen in filename)."""
    spec = importlib.util.spec_from_file_location("design_studio_shard", str(STUDIO))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def brand(tmp_path, monkeypatch, ds):
    """Point `inject_brand_css` at a brand.css this test writes.

    A factory, so each case chooses the CSS. The real
    `.claude/skills/design/references/brand.css` holds no backslash today, which
    is precisely why this defect was latent: the first glyph rule anyone adds
    to it is the one that crashes the render.
    """
    def _write(css: str) -> None:
        css_path = (tmp_path / ".claude" / "skills" / "design" / "references")
        css_path.mkdir(parents=True, exist_ok=True)
        (css_path / "brand.css").write_text(css, encoding="utf-8")
        monkeypatch.setattr(ds, "get_workspace_root", lambda p=tmp_path: p)
        # The font rewriter would otherwise reach the real data root.
        monkeypatch.setattr(ds, "get_data_root", lambda p=tmp_path: p)
    return _write


# ============================================================
# 1. The brand CSS read as a replacement template
# ============================================================

def test_an_unknown_glyph_escape_in_brand_css_does_not_crash_the_render(brand, ds):
    r"""`\e900` is an icon-font codepoint and not a Python escape.

    `re.sub` rejects an unrecognised ASCII-letter escape in the replacement
    with `re.error: bad escape \e`, which reached the caller as a traceback out
    of a render the operator had already paid Playwright for.
    """
    brand('.x::before { content: "\\e900"; }')
    out = ds.inject_brand_css("<html><head></head><body>x</body></html>", "31c")
    assert "\\e900" in out, (
        "the glyph escape did not survive the injection verbatim; the CSS is "
        "being read as a replacement template again")


def test_a_recognised_escape_in_brand_css_is_not_silently_expanded(brand, ds):
    r"""The worse half of the same defect, because it exits 0.

    `\f0c9` is a Font Awesome codepoint. As a replacement template `\f` is a
    form feed, so the injected rule became `content: "\x0c0c9"` and the render
    succeeded, shipping a PNG whose brand CSS was corrupted with no error
    anywhere.
    """
    brand('.i::before { content: "\\f0c9"; }')
    out = ds.inject_brand_css("<html><head></head><body>x</body></html>", "31c")
    assert "\\f0c9" in out, "a recognised escape was expanded into the document"
    assert "\x0c" not in out, "a form feed reached the injected stylesheet"


def test_a_backslash_group_reference_in_brand_css_survives(brand, ds):
    r"""`\1` is a group reference in a replacement template, and there is no
    group 1 in `r'</head>'`, so this raised `re.error: invalid group reference`.
    """
    brand('.q::after { content: "\\1F600"; }')
    out = ds.inject_brand_css("<html><head></head><body>x</body></html>", "31c")
    assert "\\1F600" in out


def test_the_stylesheet_still_lands_before_the_closing_head(brand, ds):
    """Or the fix above is just "stop injecting anything"."""
    brand(".ok { color: red; }")
    out = ds.inject_brand_css("<html><head><title>t</title></head><body/></html>",
                              "31c")
    assert ".ok { color: red; }" in out
    assert out.index(".ok { color: red; }") < out.index("</head>")
    assert out.count("</head>") == 1, "the closing tag was duplicated or dropped"


def test_a_document_with_no_head_still_gets_one_built_around_it(brand, ds):
    """The other branch of `inject_brand_css`, which never used `re.sub`."""
    brand('.x::before { content: "\\e900"; }')
    out = ds.inject_brand_css("<div>bare</div>", "31c")
    assert "<div>bare</div>" in out
    assert "\\e900" in out
    assert out.startswith("<!DOCTYPE html>")


# ============================================================
# 2. The source HTML that overwrote the deliverable
# ============================================================

def test_the_source_html_never_takes_the_output_path(tmp_path, ds):
    """`-o post.html` must leave the rendered artifact where it was written."""
    output = tmp_path / "post.html"
    output.write_bytes(b"\x89PNG\r\n\x1a\npretend-screenshot")

    source = ds.save_source_html("<p>hi</p>", output, from_inline=True)

    assert source != output, (
        "save_source_html returned the output path itself, so it overwrote the "
        "artifact the caller had just reported as saved")
    assert output.read_bytes().startswith(b"\x89PNG"), (
        "the rendered artifact was overwritten with its own HTML source")
    assert source.read_text(encoding="utf-8") == "<p>hi</p>"


def test_the_ordinary_png_output_still_gets_its_html_twin_beside_it(tmp_path, ds):
    """The common path must keep the plain `.html` name it always had."""
    output = tmp_path / "post.png"
    source = ds.save_source_html("<p>hi</p>", output, from_inline=True)
    assert source == tmp_path / "post.html"
    assert source.read_text(encoding="utf-8") == "<p>hi</p>"


def test_a_file_sourced_render_writes_no_twin_at_all(tmp_path, ds):
    """`--file` already has a source on disk; only `--html` needs saving."""
    output = tmp_path / "post.html"
    assert ds.save_source_html("<p>hi</p>", output, from_inline=False) is None
    assert not output.exists()


# ============================================================
# 3. The docstring that reported a fixed defect as live
# ============================================================

def _function_source(name: str) -> str:
    tree = ast.parse(STUDIO.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = STUDIO.read_text(encoding="utf-8").splitlines()
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found in scripts/design-studio.py")


def test_both_default_output_names_are_built_by_scratch_name():
    """The fact the docstring has to agree with, established from the AST.

    Asked structurally, so this half cannot be satisfied by the prose the next
    test reads.
    """
    source = STUDIO.read_text(encoding="utf-8")
    tree = ast.parse(source)
    handlers = {"cmd_render": [], "cmd_pdf": []}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in handlers:
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "scratch_name"):
                    handlers[node.name].append(call)
    for name, calls in handlers.items():
        assert calls, (
            f"{name} no longer builds its default output name through "
            f"scratch_name, so two same-second renders can overwrite each "
            f"other's deliverable again")


def test_the_scratch_name_docstring_does_not_report_the_fixed_half_as_live():
    """A present-tense claim about a defect the code no longer has.

    The docstring said `outputs/design/render-{timestamp()}.png` "is the same
    one-second name in the same shared directory" and that "the losing render
    is a deliverable overwritten with no error", of a function both default
    paths now route through. History is welcome here; a present tense is not.

    Whitespace is COLLAPSED before the search, because a docstring is wrapped by
    hand and either claim can land with its words on two lines. A per-line
    search reported clean on exactly that re-wrap, which is the same
    true-by-construction trap the pattern is meant to catch.
    """
    doc = " ".join(_function_source("scratch_name").split())
    live_claims = [claim for claim in
                   ("is the same one-second name",
                    "is a deliverable overwritten")
                   if re.search(rf"\b{re.escape(claim)}\b", doc)]
    assert not live_claims, (
        "scratch_name's docstring still states the default-name collision in "
        "the present tense, while the test above proves both callers route "
        f"through it: {live_claims}")


def test_the_scratch_name_docstring_still_records_why_it_exists():
    """The other direction: deleting the paragraph is not the fix.

    The collision is the whole reason this function is not just `timestamp()`,
    and a docstring stripped of it invites the next author to simplify it away.
    """
    doc = _function_source("scratch_name")
    assert "one-second resolution" in doc
    assert "skill-orchestrator Pattern" in doc, (
        "the parallel-dispatch reason for the collision was dropped")


def test_two_scratch_names_in_the_same_second_differ(ds):
    """The behaviour behind all of the above, asked directly."""
    first = ds.scratch_name("render", ".png")
    second = ds.scratch_name("render", ".png")
    assert first != second, "two calls produced the same default artifact name"
