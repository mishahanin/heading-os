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


# --------------------------------------------------------------------------
# Default-deny over every interpolation, not a list of the fields already known
# to be wrong.
#
# The first version of the check below named seven fields and asked whether each
# one sat inside `escapeHtml(...)`. That is a hand-maintained security list, and
# it behaves the way hand-maintained security lists behave: an EIGHTH field that
# nobody added is invisible, and the test stays green while the page injects.
#
# Measured 2026-09-01, with that list in place and green: `renderBenchmark`'s
# per-eval breakdown row spliced `r.passed`, `r.total` and `r.errors` into the
# HTML string with no escaping at all. All three are run content -
# `aggregate_benchmark.py` reads them out of grading.json with `.get(key, 0)`,
# and a default fires only on an ABSENT key, so a present string passes through -
# and all three land in `container.innerHTML`. `r.passed` set to
# `<img src=x onerror="alert(1)">` produced a live tag in the assembled row.
# The same three field names were ALREADY escaped in `renderGrades`, so this was
# the `exp.evidence` fix landing in one of the two functions that splice run data.
#
# So the question is inverted here. Every operand concatenated into an `html +=`
# statement must be a string LITERAL, an `escapeHtml(...)` call, or an entry in
# `SAFE_UNESCAPED` carrying the reason it cannot inject. A new interpolation is a
# failure until its author writes that reason down.
# --------------------------------------------------------------------------

#: Operand text -> why it cannot carry attacker-controlled markup.
SAFE_UNESCAPED = {
    # Local class/icon choices: a ternary over string literals in this file.
    "badgeClass": "ternary over 'grade-pass' / '' / 'grade-fail' literals",
    "statusClass": "ternary over 'pass' / 'fail' literals",
    "statusIcon": "ternary over two \\u escapes",
    "rowClass": "ternary over 'benchmark-row-with' / '-without' literals",
    "prClass": "ternary over two benchmark-delta-* literals and ''",
    "avgPrClass": "ternary over two benchmark-delta-* literals and ''",
    "cls": "ternary over two benchmark-delta-* literals",
    "icon": "ternary over two \\u escapes",
    # Helpers that return arithmetic or a literal class name.
    "passRate": "Math.round(n * 100) + '%' -- arithmetic, NaN% at worst",
    "fmtStat(a.pass_rate, true)": "fmtStat returns .toFixed() output or an em dash",
    "fmtStat(b.pass_rate, true)": "fmtStat returns .toFixed() output or an em dash",
    "fmtStat(a.time_seconds, false)": "fmtStat returns .toFixed() output or an em dash",
    "fmtStat(b.time_seconds, false)": "fmtStat returns .toFixed() output or an em dash",
    "fmtStat(a.tokens, false)": "fmtStat returns .toFixed() output or an em dash",
    "fmtStat(b.tokens, false)": "fmtStat returns .toFixed() output or an em dash",
    "deltaClass(delta.pass_rate)": "returns one of two benchmark-delta-* literals or ''",
    "deltaClass(delta.time_seconds)": "returns one of two benchmark-delta-* literals or ''",
    "deltaClass(delta.tokens)": "returns one of two benchmark-delta-* literals or ''",
    # Arithmetic on run content. A string operand coerces to NaN, never to markup.
    "((r.pass_rate || 0) * 100).toFixed(0)": "multiplication coerces; NaN at worst",
    "(avgRate * 100).toFixed(0)": "multiplication coerces; NaN at worst",
    # `.toFixed` on run content. Stated limit: a non-numeric value here raises a
    # TypeError and the benchmark view fails to render. That is a robustness
    # defect, not an injection: no markup reaches the string on that path.
    '(r.time_seconds != null ? r.time_seconds.toFixed(1) : "—")':
        "toFixed on a number; a string operand throws rather than injecting",
    '(times.length ? (times.reduce((a, b) => a + b, 0) / times.length).toFixed(1) : "—")':
        "division then toFixed; a string operand throws rather than injecting",
}

_JS_LITERAL = re.compile(r"""^(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")$""", re.S)


def _split_concatenation(expression: str) -> list[str]:
    """Split a JS `+` concatenation into operands, quote- and depth-aware.

    A naive `split("+")` breaks `(a, b) => a + b` and every `+` inside a string.
    """
    out, buf, quote, depth, index = [], [], None, 0, 0
    while index < len(expression):
        char = expression[index]
        if quote:
            buf.append(char)
            if char == "\\" and index + 1 < len(expression):
                buf.append(expression[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"`":
            quote = char
            buf.append(char)
            index += 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "+" and depth == 0:
            out.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    out.append("".join(buf))
    return [operand.strip() for operand in out if operand.strip()]


def _html_operands() -> list[tuple[int, str]]:
    """Every operand spliced into an `html +=` statement, with its line number."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(VIEWER.read_text(encoding="utf-8").splitlines(), 1):
        if "html +=" not in line:
            continue
        statement = re.search(r"html\s*\+=\s*(.*?);\s*$", line)
        assert statement, (
            f"{VIEWER.name}:{number} appends to `html` in a shape this test cannot "
            f"parse (a statement spanning lines?), so it would go unchecked: {line.strip()}"
        )
        found.extend((number, operand) for operand in _split_concatenation(statement.group(1)))
    return found


def test_the_operand_walk_actually_reaches_the_page():
    """A walk that finds nothing would make the default-deny check vacuous."""
    operands = _html_operands()
    assert len(operands) > 80, f"only {len(operands)} operands parsed out of {VIEWER}"
    assert sum(1 for _, o in operands if o.startswith("escapeHtml(")) > 20, operands


def test_every_interpolation_is_escaped_or_declared_safe():
    """Default-deny. A new unescaped operand fails until its reason is written down."""
    offenders = []
    for number, operand in _html_operands():
        if _JS_LITERAL.match(operand):
            continue
        if operand.startswith("escapeHtml("):
            continue
        if operand in SAFE_UNESCAPED:
            continue
        offenders.append(f"{VIEWER.name}:{number}: {operand}")
    assert offenders == [], (
        "operands reach the page's HTML string without escapeHtml() and without an "
        "entry in SAFE_UNESCAPED saying why they cannot inject:\n  "
        + "\n  ".join(offenders)
    )


def test_the_safe_list_cannot_accumulate_entries_guarding_nothing():
    """A registry entry whose operand is gone is a claim about code that left."""
    live = {operand for _, operand in _html_operands()}
    stale = sorted(set(SAFE_UNESCAPED) - live)
    assert stale == [], f"SAFE_UNESCAPED entries with no matching operand: {stale}"


def test_the_default_deny_would_catch_a_new_raw_field(tmp_path: Path):
    """Positive control, on the exact three operands that were live on 2026-09-01.

    Without it, an operand walk that silently stopped splitting would report an
    empty offender list and read as a pass.
    """
    line = ('              html += \'<td class="\' + prClass + \'">\' + '
            '((r.pass_rate || 0) * 100).toFixed(0) + "% (" + (r.passed || 0) + '
            '"/" + (r.total || 0) + ")</td>";')
    operands = list(_split_concatenation(
        re.search(r"html\s*\+=\s*(.*?);\s*$", line).group(1)))
    raw = [o for o in operands
           if not _JS_LITERAL.match(o) and not o.startswith("escapeHtml(")
           and o not in SAFE_UNESCAPED]
    assert raw == ["(r.passed || 0)", "(r.total || 0)"], raw


@requires_node
def test_run_counts_cannot_inject_a_tag_into_the_breakdown_row():
    """The behavioural half: the exact payload, through the page's real escaper."""
    snippet = "\n".join([
        _extract_const("HTML_ESCAPES"),
        _extract("escapeHtml"),
        'const r = { pass_rate: 1.0, passed: \'<img src=x onerror="alert(1)">\','
        " total: 3, errors: 0 };",
        'const prClass = "benchmark-delta-positive";',
        'const html = \'<td class="\' + prClass + \'">\' + ((r.pass_rate || 0) * 100).toFixed(0)'
        ' + "% (" + escapeHtml(r.passed || 0) + "/" + escapeHtml(r.total || 0) + ")</td>";',
        "console.log(JSON.stringify(html));",
    ])
    html = json.loads(_run_js(snippet).strip())

    class _Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)

    parser = _Parser()
    parser.feed(html)
    assert parser.tags == ["td"], f"run counts opened extra tags {parser.tags}: {html}"
    assert "onerror" in html, "the text must survive; only the markup is neutralised"
