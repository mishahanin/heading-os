"""Eval output embedded in the review page must not be able to close its script tag.

Found by the 2026-08-23 audit. `generate_review.generate_html` splices the run
data into a `<script>` block with a bare `json.dumps`::

    return template.replace("/*__EMBEDDED_DATA__*/", f"const EMBEDDED_DATA = {data_json};")

`json.dumps` escapes quotes and backslashes. It does NOT escape `<`. The HTML
parser stops a script block at the first literal `</script` regardless of the
JavaScript syntax around it, so any eval output containing that sequence ends
the block early: the rest of the data becomes visible page text, the page's own
code never runs, and whatever follows the sequence is parsed as HTML.

This is not a hypothetical input. `embed_file` reads any text output a run
produced, `.html` and `.js` included, straight into `content`. A skill under
evaluation that writes an HTML file — which is a perfectly ordinary thing for a
skill to do — silently breaks or hijacks the reviewer's page.

Escaping `<` as `\\u003c` is the fix: valid JSON, identical decoded string, and
it also neutralises `<!--`, which starts the parser's script-data-escaped state.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VIEWER_DIR = ROOT / ".claude" / "skills" / "skill-creator" / "eval-viewer"
GENERATOR = VIEWER_DIR / "generate_review.py"

_spec = importlib.util.spec_from_file_location("_gen_review_under_test", GENERATOR)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

BREAKOUT = "</script><script>window.__pwned = 1;</script>"


def _page(content: str) -> str:
    runs = [{
        "id": "eval-0-with_skill",
        "prompt": "make a page",
        "eval_id": 0,
        "outputs": [{"name": "out.html", "type": "text", "content": content}],
        "grading": {},
    }]
    return gen.generate_html(runs, skill_name="demo")


def test_a_closing_script_tag_in_run_output_cannot_end_the_data_block():
    page = _page(BREAKOUT)
    start = page.index("const EMBEDDED_DATA =")
    # The next `</script` after the assignment must be the block's own closer,
    # not one smuggled in through the data.
    closer = page.index("</script", start)
    between = page[start:closer]
    assert "window.__pwned" not in between or "\\u003c" in between, (
        "run output closed the script block early"
    )
    assert "</script>" not in json_payload(page), "raw </script> survived into the data"


def json_payload(page: str) -> str:
    match = re.search(r"const EMBEDDED_DATA = (.*?);\n", page, re.DOTALL)
    assert match, "could not locate the embedded data assignment"
    return match.group(1)


def test_the_payload_still_decodes_to_the_original_string():
    """Escaping must not corrupt the data it protects."""
    page = _page(BREAKOUT)
    decoded = json.loads(json_payload(page))
    assert decoded["runs"][0]["outputs"][0]["content"] == BREAKOUT


def test_an_html_comment_opener_is_also_escaped():
    """`<!--` puts the HTML parser into script-data-escaped state."""
    page = _page("<!-- a comment in the output -->")
    assert "<!--" not in json_payload(page)
    decoded = json.loads(json_payload(page))
    assert decoded["runs"][0]["outputs"][0]["content"] == "<!-- a comment in the output -->"


@pytest.mark.parametrize("payload", [
    "</SCRIPT >",
    "</script\t>",
    "plain text with no markup at all",
    "a < b and c > d",
])
def test_no_raw_left_angle_bracket_reaches_the_script_block(payload: str):
    page = _page(payload)
    assert "<" not in json_payload(page), payload
    decoded = json.loads(json_payload(page))
    assert decoded["runs"][0]["outputs"][0]["content"] == payload


def test_the_template_placeholder_still_exists():
    """A renamed placeholder would make generate_html a silent no-op."""
    template = (VIEWER_DIR / "viewer.html").read_text(encoding="utf-8")
    assert "/*__EMBEDDED_DATA__*/" in template
