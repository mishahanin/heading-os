"""Run content must not be able to inject attributes into the review page.

Found 2026-08-31, in the half of the eval viewer the previous audit could not
reach: `eval-viewer/viewer.html` is JavaScript and was out of that audit's
scope.

`generate_review.py` correctly neutralises `</script` on the Python side before
splicing the run JSON into a `<script>` block. That escaping stops the HTML
parser ending the block early and NOTHING else: `\\u003c` is valid JSON that
decodes back to `<`, so by the time the page's own JavaScript reads the value
every character is back.

The page's defence in the JavaScript layer was::

    function escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }

That is the browser's TEXT-NODE serializer. It escapes ``&``, ``<`` and ``>``,
and it deliberately leaves ``"`` and ``'`` alone, because a text node never
needs them escaped. Most call sites are text contexts, where that is correct.

One is not. The per-assertion benchmark table builds::

    html += '<span class="' + cls + '" title="Run ' + run.run_number + ': '
         +  escapeHtml(exp.evidence || "") + '">' + icon + "</span> ";

`exp.evidence` reaches a DOUBLE-QUOTED ATTRIBUTE. A single ``"`` in it closes
`title=` and everything after is parsed as further attributes on that same
`<span>` - an event handler, for example. No ``<`` is required anywhere, which
is why the Python-side escaping never touched it.

`exp.evidence` is run content: `aggregate_benchmark.py` copies `expectations`
verbatim out of each run's `grading.json` into `benchmark.json`, and
`--benchmark` feeds that straight to the page.

The fix replaces `escapeHtml` with an explicit character map covering
``& < > " ' ` ``, and escapes the interpolations that reached the HTML string
with no escaping at all.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / ".claude" / "skills" / "skill-creator" / "eval-viewer" / "viewer.html"

NODE = shutil.which("node")
# Not a silent skip: node ships on the GitHub ubuntu runners this repo uses and
# on the operator's machine, so this normally executes. If it ever starts
# skipping in CI that is a finding about the runner, not about this test.
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed on this machine")


def _extract(name: str) -> str:
    """Pull one top-level construct out of viewer.html by brace matching.

    Reads the SHIPPED source rather than a copy, so the test cannot pass against
    a function the page does not actually contain.
    """
    source = VIEWER.read_text(encoding="utf-8")
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"could not brace-match function {name} in {VIEWER}")


def _extract_const(name: str) -> str:
    source = VIEWER.read_text(encoding="utf-8")
    start = source.index(f"const {name} = ")
    end = source.index("};", start) + 2
    return source[start:end]


def _run_js(snippet: str) -> str:
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", snippet],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout


def _escape_html(values: list[str]) -> list[str]:
    """Run the page's REAL escapeHtml over `values` in node."""
    snippet = "\n".join([
        _extract_const("HTML_ESCAPES"),
        _extract("escapeHtml"),
        f"const inputs = {json.dumps(values)};",
        "console.log(JSON.stringify(inputs.map(escapeHtml)));",
    ])
    return json.loads(_run_js(snippet).strip())


@requires_node
def test_escape_html_escapes_the_double_quote():
    """The single character the old text-node serializer let through."""
    assert _escape_html(['"']) == ["&quot;"]


@requires_node
def test_an_attribute_breakout_payload_survives_no_bare_quote():
    payload = '" onmouseover="alert(1)'
    escaped, = _escape_html([payload])
    assert '"' not in escaped, f"escapeHtml returned a bare quote: {escaped!r}"
    assert "onmouseover" in escaped, "the text itself must survive, only the quoting changes"


@requires_node
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&#39;"),
        ("`", "&#96;"),
    ],
)
def test_every_html_significant_character_is_escaped(raw, expected):
    assert _escape_html([raw]) == [expected]


@requires_node
def test_ordinary_text_passes_through_unharmed():
    """The accepted case. An escaper that mangles plain prose is not usable."""
    plain = "Found 3 matches in run-2 (pass rate 66%)"
    assert _escape_html([plain]) == [plain]


@requires_node
def test_the_script_terminator_is_still_neutralised_in_the_js_layer_too():
    escaped, = _escape_html(["</script><script>window.pwned=1</script>"])
    assert "<" not in escaped and ">" not in escaped


@requires_node
def test_null_and_undefined_do_not_render_as_the_word_null():
    snippet = "\n".join([
        _extract_const("HTML_ESCAPES"),
        _extract("escapeHtml"),
        "console.log(JSON.stringify([escapeHtml(null), escapeHtml(undefined)]));",
    ])
    assert json.loads(_run_js(snippet).strip()) == ["", ""]


@requires_node
def test_the_title_attribute_cannot_be_closed_by_run_evidence():
    """Rebuild the exact vulnerable line and prove the payload stays inside.

    The construction below is copied from `renderBenchmark`'s per-assertion
    table. Asserting on the assembled string is what makes this a test of the
    injection rather than a test of a helper in isolation.
    """
    snippet = "\n".join([
        _extract_const("HTML_ESCAPES"),
        _extract("escapeHtml"),
        'const run = { run_number: 1 };',
        'const exp = { passed: true, evidence: \'" onmouseover="alert(1)\' };',
        'const cls = "benchmark-delta-positive";',
        'const html = \'<span class="\' + cls + \'" title="Run \' + escapeHtml(run.run_number)'
        ' + \': \' + escapeHtml(exp.evidence || "") + \'">ok</span>\';',
        "console.log(JSON.stringify(html));",
    ])
    html = json.loads(_run_js(snippet).strip())

    # Parse it as a browser would, and ask what attributes the tag ACTUALLY has.
    # Asserting on the raw string was the first version of this test and it was
    # wrong: it stripped the &quot; entities back out and then found the payload
    # in the plain text, which proves nothing - the payload is SUPPOSED to
    # survive as text inside the title.
    class _Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    parser = _Parser()
    parser.feed(html)

    assert len(parser.tags) == 1, f"the payload opened extra tags: {parser.tags}"
    tag, attrs = parser.tags[0]
    assert tag == "span"
    assert set(attrs) == {"class", "title"}, (
        f"the payload injected attributes {sorted(set(attrs) - {'class', 'title'})}: {html}"
    )
    # And the payload survives, intact, where it belongs: inside the value.
    assert attrs["title"] == 'Run 1: " onmouseover="alert(1)'


def test_the_page_no_longer_uses_the_text_node_serializer():
    """A regression pin on the specific implementation that was wrong.

    Behavioural coverage above is the real guard; this one names the exact shape
    so a future edit cannot quietly reintroduce it while the map stays defined
    and unused.
    """
    source = VIEWER.read_text(encoding="utf-8")
    assert "div.textContent = text;\n      return div.innerHTML;" not in source


def test_no_benchmark_metadata_field_reaches_the_html_string_unescaped():
    """A regression pin on the raw interpolations found alongside the main bug.

    Named individually, because each was a separate `html +=` that concatenated
    a benchmark.json value with no escaping at all.
    """
    source = VIEWER.read_text(encoding="utf-8")
    must_be_escaped = [
        "metadata.timestamp",
        "metadata.evals_run.join",
        "metadata.runs_per_configuration",
        "delta.pass_rate",
        "delta.tokens",
        "run.run_number",
        "configLabel",
    ]
    unescaped = []
    for line in source.splitlines():
        if "html +=" not in line:
            continue
        for field in must_be_escaped:
            if field not in line:
                continue
            # Every occurrence of the field on an `html +=` line must sit inside
            # an escapeHtml(...) call or a numeric-only helper.
            if not re.search(r"escapeHtml\([^)]*" + re.escape(field.split(".")[-1]), line) \
               and "deltaClass(" not in line:
                unescaped.append(line.strip())
    assert unescaped == [], "unescaped interpolations remain:\n" + "\n".join(unescaped)
