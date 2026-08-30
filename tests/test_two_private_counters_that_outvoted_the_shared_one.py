r"""Two counters kept their own arithmetic after the workspace agreed on one.

`tests/test_one_question_five_counters_five_answers.py` folded three private
word counters into `scripts.utils.sanitize_text.word_count` and recorded, in its
own docstring, that two more were left alone on purpose. The operator reversed
that on 2026-08-30. This file pins the convergence and the measurement behind
it, so the next audit reads a number instead of re-opening the question.

MEASURED before the change, on one probe string:

    counter                                     count
    scripts/utils/sanitize_text.word_count         11   (canonical)
    scripts/ste-check.py:304                       12
    scripts/apply-wizard-answers.py:906            15

Two different mechanisms, both inflations, and neither one ever undercounts.

`ste-check` counted `\b[\w'-]+\b`. That regex reads `.` `,` `/` `=` `>` `{` `}`
as word boundaries, so a token a reader parses as one thing became several:
`e.g.,` scored 2, `1.0.0` scored 3, `outputs/operations/{version}.md` scored 7.

`apply-wizard-answers` counted `len(draft.split())`, which has no alnum filter
at all, so a bare `-` bullet, a `|` table rule and a `---` separator each scored
one word of prose.

The shared counter is `.split()` plus the alnum filter, which is exactly the
intersection of the two corrections.

THE THRESHOLD. Only one of the two numbers gates anything, and it is the
ste-check one: `check_sentence_length` compares it against `STEP_WORD_LIMIT`
(20) and `PROSE_WORD_LIMIT` (25), and the verdict runs CI and pre-commit.
Because the shared counter is never higher than the regex it replaced, the swap
can only LOOSEN that gate. Measured over the 108 gated files (14 documentation
pages plus 94 skill bodies), 8644 sentences:

    delta (old ste - shared)   sentences
    0                               8071
    1                                468
    2                                 75
    3                                 19
    4                                  5
    5                                  4
    6                                  1
    7                                  1

    sentences where shared > old ste      0
    sentences whose verdict flips         0
    `--all --quiet` / `--skills --quiet`  0 errors before, 0 errors after

So no verdict moved on the corpus as it stands, and the gate output is
byte-identical. The MARGIN moved: eight sentences sat exactly on their limit and
now sit one or two words under it, and a path-dense sentence can carry up to
seven more counted words before the limit refuses it. `test_the_gate_loosened_
at_the_boundary` below is that shift, written down at the exact word where it
happens, so it is a recorded decision and not a silent one.

The wizard number gates nothing. `_display_value` renders it into one dashboard
row as `[approved draft, ~N words]` and no caller reads it back.
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.sanitize_text import word_count  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ste = _load("ste_converged", "scripts/ste-check.py")
wz = _load("wizard_converged", "scripts/apply-wizard-answers.py")


# ==========================================================================
# The two definitions that were replaced, kept here so the probes below can
# be shown to discriminate. Never assert against a typed literal: the expected
# value is always read from the canonical `word_count`, so this file tracks the
# definition rather than freezing today's answer.
# ==========================================================================

def _old_ste(text):
    """`scripts/ste-check.py:304` before 2026-08-30."""
    return len(re.findall(r"\b[\w'-]+\b", text))


def _old_wizard(text):
    """`scripts/apply-wizard-answers.py:906` before 2026-08-30."""
    return len(text.split())


# The probe that produced 11 / 12 / 15.
SPREAD = "It's a well-known state-of-the-art system - see item 3. | --- | 50% of $347,850."


# ==========================================================================
# Red direction: the probe really does split the three counters apart
# ==========================================================================

def test_the_probe_separates_all_three_old_counters():
    """Without this, everything below could pass over an input nobody disagreed
    on. Three distinct answers, asserted as three distinct answers."""
    answers = {word_count(SPREAD), _old_ste(SPREAD), _old_wizard(SPREAD)}
    assert len(answers) == 3, f"the probe stopped discriminating: {answers}"
    assert _old_ste(SPREAD) > word_count(SPREAD)
    assert _old_wizard(SPREAD) > _old_ste(SPREAD)


def test_neither_old_counter_ever_read_lower_than_the_shared_one():
    """The direction of the whole change, and the reason the ste gate could
    only loosen. Every whitespace token holding an alnum yields at least one
    `[\\w'-]+` match, and `.split()` counts a superset of what the alnum filter
    keeps, so both old counters are floored by the shared one."""
    probes = [
        SPREAD,
        "outputs/operations/workspace/{version}_audit-overview.md",
        "Increment the PATCH version (e.g., 1.0.0 -> 1.0.1) before the release.",
        "- \n- \n- \nalpha beta",
        "| alpha | beta |\n| --- | --- |",
        "The gate refuses the push when the scan finds a secret.",
        "",
        "  \t\n ",
    ]
    for p in probes:
        assert _old_ste(p) >= word_count(p), p
        assert _old_wizard(p) >= word_count(p), p


# ==========================================================================
# Green direction: both live counters now answer with the canonical one
# ==========================================================================

def test_the_ste_checker_uses_the_shared_counter():
    assert ste.word_count is word_count


def test_the_wizard_uses_the_shared_counter():
    assert wz.word_count is word_count


def _word_count_bindings(rel):
    """Every place the module binds the name `word_count`, from the AST.

    A substring search is the wrong instrument: the first draft of this test
    grepped for `word_count = len(` and matched the COMMENT that records the
    line it was looking for. Ask the tree what the name is bound to, at module
    scope and inside every function.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    defs, assigns, imports = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "word_count":
            defs.append(node.lineno)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "word_count":
                    assigns.append(t.lineno)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "word_count":
                assigns.append(node.target.lineno)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if (a.asname or a.name) == "word_count":
                    imports.append(node.module)
    return defs, assigns, imports


def test_the_ste_checker_kept_no_private_copy():
    """A module can import the shared name and still define its own below it.

    `is word_count` above would pass on the import alone if a redefinition were
    added back at a line the import does not reach.
    """
    defs, assigns, imports = _word_count_bindings("scripts/ste-check.py")
    assert defs == [], f"a private def survived at line(s) {defs}"
    assert assigns == [], f"the name is rebound at line(s) {assigns}"
    assert imports == ["scripts.utils.sanitize_text"]


def test_the_wizard_kept_no_private_copy():
    """`word_count = len(draft.split())` was a LOCAL that also shadowed the
    name for the rest of its function. A rebinding is as much a second copy as
    a `def` is, and only the AST sees both."""
    defs, assigns, imports = _word_count_bindings("scripts/apply-wizard-answers.py")
    assert defs == [], f"a private def survived at line(s) {defs}"
    assert assigns == [], f"the name is rebound at line(s) {assigns}"
    assert imports == ["scripts.utils.sanitize_text"]


def test_all_three_now_answer_the_probe_identically():
    """The case that failed before the change: three counters, three answers."""
    assert ste.word_count(SPREAD) == word_count(SPREAD)
    assert wz.word_count(SPREAD) == word_count(SPREAD)


# ==========================================================================
# The wizard number reaches a label and stops there
# ==========================================================================

def test_the_wizard_label_no_longer_counts_bullets_as_prose():
    draft = "- \n- \n- \nalpha beta"
    assert _old_wizard(draft) > word_count(draft), "the probe stopped discriminating"
    got = wz._display_value({"type": "rich"}, {"status": "answered", "draft": draft})
    assert got == f"[approved draft, ~{word_count(draft)} words]"


def test_the_wizard_label_is_unchanged_on_ordinary_prose():
    """Not vacuous: the same call, on an input all three always agreed on."""
    draft = "The gate refuses the push when the scan finds a secret."
    assert _old_wizard(draft) == _old_ste(draft) == word_count(draft) > 0
    got = wz._display_value({"type": "rich"}, {"status": "answered", "draft": draft})
    assert got == f"[approved draft, ~{word_count(draft)} words]"


# ==========================================================================
# The threshold. This is the one place the swap changes a verdict.
# ==========================================================================

def _length_findings(text):
    """Run only the check the counter feeds, through the real parse path."""
    units = ste.parse_units(ste.strip_noise(text))
    return [f for f in ste.check_sentence_length(units)
            if f["type"] == "sentence_too_long"]


def _prose_of(n_words, tail=None):
    """A plain paragraph whose SHARED word count is exactly `n_words`.

    Built rather than typed, so it re-derives if the canonical definition ever
    changes. `tail`, when given, is one extra token appended before the period.
    """
    tail_words = word_count(tail) if tail else 0
    filler = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    body = [filler[i % len(filler)] for i in range(n_words - tail_words)]
    if tail:
        body.append(tail)
    text = " ".join(body) + "."
    assert word_count(text) == n_words, f"fixture built {word_count(text)}, wanted {n_words}"
    return text


def test_the_fixture_builder_produces_one_prose_unit_and_one_sentence():
    """Everything below is measured through `parse_units` and `split_sentences`.
    If the fixture ever splits into two, the length assertions become halves of
    the sentence they claim to measure and quietly stop testing the limit."""
    text = _prose_of(ste.PROSE_WORD_LIMIT, tail="outputs/reports/final.md")
    units = ste.parse_units(ste.strip_noise(text))
    assert len(units) == 1
    assert units[0]["kind"] == "prose"
    assert len(ste.split_sentences(units[0]["text"])) == 1


def test_the_gate_loosened_at_the_boundary():
    """The recorded shift, written at the exact word where it happens.

    A sentence of `PROSE_WORD_LIMIT` words, one of which is a bare file path.
    The old regex split that path into four and reported the sentence over the
    limit. The shared counter reads it as the one token a reader sees, so the
    sentence now sits exactly ON the limit and passes.
    """
    text = _prose_of(ste.PROSE_WORD_LIMIT, tail="outputs/reports/final.md")
    sentence = ste.split_sentences(ste.parse_units(ste.strip_noise(text))[0]["text"])[0]

    assert word_count(sentence) == ste.PROSE_WORD_LIMIT
    assert _old_ste(sentence) > ste.PROSE_WORD_LIMIT, "the probe stopped discriminating"

    assert _length_findings(text) == [], "the boundary sentence should now pass"


def test_the_gate_still_refuses_one_word_past_the_limit():
    """The loosening is bounded. A sentence the shared counter reads as over the
    limit is still an error, so this is a shifted gate and not a removed one."""
    text = _prose_of(ste.PROSE_WORD_LIMIT + 1)
    findings = _length_findings(text)
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert str(ste.PROSE_WORD_LIMIT + 1) in findings[0]["description"]


def test_the_step_limit_moved_the_same_way():
    """`check_sentence_length` reads two limits off the same counter. A fix that
    lands on one of them is half a fix."""
    body = _prose_of(ste.STEP_WORD_LIMIT, tail="outputs/reports/final.md")
    text = f"1. {body}"
    units = ste.parse_units(ste.strip_noise(text))
    assert len(units) == 1 and units[0]["kind"] == "step"

    sentence = ste.split_sentences(units[0]["text"])[0]
    assert word_count(sentence) == ste.STEP_WORD_LIMIT
    assert _old_ste(sentence) > ste.STEP_WORD_LIMIT, "the probe stopped discriminating"
    assert _length_findings(text) == []

    over = f"1. {_prose_of(ste.STEP_WORD_LIMIT + 1)}"
    assert len(_length_findings(over)) == 1


def test_a_sentence_of_plain_words_is_measured_the_same_as_before():
    """Not vacuous. On prose with no path, no abbreviation and no grouped
    number, the old regex and the shared counter always agreed, and the gate
    must behave identically on it in both directions."""
    at_limit = _prose_of(ste.PROSE_WORD_LIMIT)
    over_limit = _prose_of(ste.PROSE_WORD_LIMIT + 1)
    for text in (at_limit, over_limit):
        sentence = ste.split_sentences(ste.parse_units(ste.strip_noise(text))[0]["text"])[0]
        assert _old_ste(sentence) == word_count(sentence)
    assert _length_findings(at_limit) == []
    assert len(_length_findings(over_limit)) == 1


# ==========================================================================
# The corpus, which is what the gate actually runs on
# ==========================================================================

def test_the_gated_corpus_reports_no_length_error_under_the_shared_counter():
    """The measurement that made the swap safe to land: 0 errors before, 0
    after. Scoped to `sentence_too_long`, because the other eight checks do not
    read this counter and their findings are not this file's business.
    """
    for path in ste.resolve_scope() + ste.resolve_skill_scope():
        findings = _length_findings(path.read_text(encoding="utf-8"))
        assert findings == [], f"{path.relative_to(ROOT)}: {findings}"


def test_the_gated_corpus_is_not_empty():
    """A guard is green over an empty corpus. `resolve_scope` reads from the
    workspace root, so a runner that resolves it elsewhere would pass the test
    above by measuring nothing."""
    paths = ste.resolve_scope() + ste.resolve_skill_scope()
    assert len(paths) > 50, f"only {len(paths)} files resolved"
