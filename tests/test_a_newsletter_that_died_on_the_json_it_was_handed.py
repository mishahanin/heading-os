#!/usr/bin/env python3
"""Shard 08-p2: the newsletter generator against the document it is given.

`scripts/generate-newsletter-html.py` reads an external JSON document and
renders a briefing that is mailed and printed to PDF. Nine findings, and seven
of them were the same class in seven places: a value whose SHAPE the generator
assumed, met by a value that had a different one, raising AttributeError with
nothing catching it. The cost each time is not a wrong pixel, it is no
newsletter at all.

The file had already been fixed for this three times, each with a comment
saying so. `build_the_heading` coerces its body ("no newsletter at all"),
`build_signal_watch` coerces its list items ("no newsletter was produced at all"
for a bare number), and `build_masthead` names the pattern outright: "fixing one
of the two is this repository's usual defect". Its three body siblings and its
three list siblings had not been fixed, and neither had the top-level object
check in `main`.

The other two findings were quieter. A markdown link whose URL carries balanced
parentheses (`https://en.wikipedia.org/wiki/Gulf_(geography)`) was truncated at
the first `)`, and the scheme check still passed on the truncated string, so a
dead href shipped with a stray `)` beside it in the rendered text. And
`--images` accepted a well-formed mapping naming a section nothing renders,
stored it and never read it: `sea-stat=/tmp/img.png` exited 0 with no image and
no word said.

Two findings from that shard are NOT here because they are already dead.
`build_navigation_chart` escapes its string-branch region key (fixed
2026-08-31, and the comment in the source records it), and the PDF path's
offline stall has its own file below.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "newsletter_08p2", ROOT / "scripts" / "generate-newsletter-html.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["newsletter_08p2"] = gen
_spec.loader.exec_module(gen)


# ---------------------------------------------------------------------------
# Finding 1 - a markdown link whose URL carries parentheses
# ---------------------------------------------------------------------------


def test_a_url_with_balanced_parentheses_is_not_cut_at_the_first_bracket():
    """The lazy `(.+?)` stopped at the first `)`, not the closing one.

    Further Reading is fed from scraped external links, so parenthesised
    Wikipedia and docs URLs are ordinary content rather than an edge case.
    """
    out = gen.markdown_to_html(
        "See [Wikipedia](https://en.wikipedia.org/wiki/Gulf_(geography)) now")

    assert 'href="https://en.wikipedia.org/wiki/Gulf_(geography)"' in out, (
        f"the href was truncated at the inner bracket: {out}")
    assert "</a>)" not in out, (
        f"the leftover bracket was emitted as body text after the anchor: "
        f"{out}")


def test_an_ordinary_url_and_two_links_on_one_line_still_render():
    """The counter-case. A URL group that swallowed too much would join two
    adjacent links into one href and pass the test above."""
    out = gen.markdown_to_html(
        "[a](https://example.test/one) and [b](https://example.test/two)")

    assert 'href="https://example.test/one"' in out, out
    assert 'href="https://example.test/two"' in out, out
    assert out.count("<a href=") == 2, f"the two links collapsed into one: {out}"


def test_a_javascript_url_with_parentheses_is_still_refused():
    """The scheme check must survive the wider capture. `javascript:alert(1)`
    is the shape that both carries brackets and must never become an href."""
    out = gen.markdown_to_html("[x](javascript:alert(1))")

    assert "href" not in out, f"a javascript: link became live: {out}"
    assert "<a " not in out, out


# ---------------------------------------------------------------------------
# Finding 2 - a section handed a plain string
# ---------------------------------------------------------------------------


def test_a_navigation_chart_given_as_a_string_still_renders_a_newsletter():
    """Every sibling builder tolerates a bare string; this one iterated it as
    characters and then called `.get` on it."""
    out = gen.build_navigation_chart("GCC and CIS update")

    assert "GCC and CIS update" in out, (
        f"the region text was dropped rather than rendered: {out}")
    assert "region-table" in out, out


# ---------------------------------------------------------------------------
# Finding 3 - a body that is not a string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [
    "build_sea_state", "build_cyber_front", "build_market_depth",
])
def test_a_non_string_body_does_not_kill_the_section(builder):
    """`build_the_heading` was hardened with `str(... or "")` and its three
    siblings were not. A JSON number passed the falsy guard in
    `markdown_to_html` and died on `.strip()`."""
    out = getattr(gen, builder)({"body": 42})

    assert "42" in out, f"{builder} dropped the body instead of rendering it: {out}"


def test_a_non_string_region_body_does_not_kill_the_navigation_chart():
    """The fourth site of the same defect, inside the region loop."""
    out = gen.build_navigation_chart({"gcc": {"body": 42}})

    assert "42" in out, out


# ---------------------------------------------------------------------------
# Finding 4 - a list entry that is not an object
# ---------------------------------------------------------------------------


def test_a_bare_string_in_recommended_reading_does_not_kill_the_render():
    """`build_signal_watch` was fixed for exactly this and its siblings were
    not. A scraped list of bare URLs is the realistic input."""
    out = gen.build_recommended_reading(["https://example.test/article"])

    assert "https://example.test/article" in out, (
        f"the entry vanished instead of rendering: {out}")


def test_a_bare_number_in_indicators_does_not_kill_the_render():
    out = gen.build_indicators([1, 2])

    assert ">1<" in out and ">2<" in out, out


def test_a_bare_string_in_market_depth_stats_does_not_kill_the_render():
    out = gen.build_market_depth({"stats": ["17 days"]})

    assert "17 days" in out, out


def test_a_well_formed_reading_item_still_renders_its_url_and_source():
    """The counter-case. Coercing every entry to a title would pass the bare
    string test above and silently drop the url of a real one."""
    out = gen.build_recommended_reading([
        {"title": "T", "url": "https://example.test/a", "source": "S"}])

    assert 'href="https://example.test/a"' in out, out
    assert ">T<" in out and "S" in out, out


# ---------------------------------------------------------------------------
# Finding 9 - the indicator docstring contradicted the code
# ---------------------------------------------------------------------------


def test_the_indicator_docstring_does_not_promise_a_column_count():
    """The docstring said "5 equal columns" and no count check exists.

    `newsletter.css` settles which half was wrong: `.indicators` is
    `display:flex` and `.ind` is `flex:1`, so any number of items lays out as
    equal columns and the CODE was right. Asserted against the rendered
    output rather than the prose alone, so the two cannot drift apart again.
    """
    # The SUMMARY line only. The body below it quotes the old wording to
    # explain what was wrong, which is not the same thing as promising it.
    summary = (gen.build_indicators.__doc__ or "").strip().split("\n")[0]
    assert "5 equal columns" not in summary, (
        f"the docstring still promises five columns while the code renders one "
        f"per item: {summary!r}")
    assert "per item" in summary, (
        f"the summary no longer states the count the code actually renders: "
        f"{summary!r}")

    two = gen.build_indicators([{"value": 1, "label": "a"},
                                {"value": 2, "label": "b"}])
    assert two.count('class="ind"') == 2, two


# ---------------------------------------------------------------------------
# Findings 7 and 8 - the CLI's two remaining silent or ugly failures
# ---------------------------------------------------------------------------


def _run_cli(tmp_path: Path, payload, *extra: str):
    """Invoke the generator as the operator does, and return the process."""
    doc = tmp_path / "in.json"
    doc.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate-newsletter-html.py"),
         str(doc), "--no-pdf", "--output-dir", str(tmp_path / "out"), *extra],
        capture_output=True, text=True, cwd=str(ROOT))


def test_a_top_level_json_list_is_refused_cleanly_not_with_a_traceback(tmp_path):
    """`json.load` accepts any JSON VALUE. The script produces a clean `Error:`
    for a missing file, a malformed `--images` and a bad date; the most basic
    check of all raised AttributeError with a full traceback instead."""
    proc = _run_cli(tmp_path, [1, 2, 3])

    assert proc.returncode == 1, proc
    assert "Traceback" not in proc.stderr, (
        f"the operator still gets a traceback for a wrong-shaped document: "
        f"{proc.stderr}")
    assert "must hold a JSON object" in proc.stderr, proc.stderr


def test_an_images_section_nothing_renders_is_refused_rather_than_ignored(tmp_path):
    """A typo like `sea-stat=` was accepted, stored and never read: exit 0, no
    image, no message. The malformed-mapping branch two lines above it in the
    source already refuses; this shape did not."""
    proc = _run_cli(tmp_path, {"date": "2026-09-02"},
                    "--images", "sea-stat=/tmp/nope.png")

    assert proc.returncode == 2, proc
    assert "sea-stat" in proc.stderr, proc.stderr


def test_the_one_real_images_section_is_still_accepted(tmp_path):
    """The counter-case. Refusing every mapping would pass the test above and
    break the only image the generator actually renders."""
    proc = _run_cli(tmp_path, {"date": "2026-09-02"},
                    "--images", "sea_state=/tmp/nope.png")

    assert proc.returncode == 0, proc
    assert "Newsletter generated" in proc.stdout, proc.stdout
