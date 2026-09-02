"""Four docstrings that described a stronger contract than their code delivers.

Found by the 2026-08-24 engine audit campaign, verified still present and fixed
2026-09-02. None of the four was a logic bug, and that is exactly why each one is
expensive: a sentence that over-claims is trusted, acted on, and quoted back
later as established fact. `.claude/rules/scope-claims.md` names this failure
mode for tool OUTPUT; these are the same defect one layer in.

  splice_region      said everything outside the markers is "preserved
                     byte-for-byte" while the module docstring twelve lines above
                     had already recorded that wording as false for any non-LF
                     file and narrowed itself to "line endings aside". The
                     narrowing never reached the function.

  unescape_pipes     called itself "the exact inverse" of `escape_pipes`. It is
                     not, on one shape: a pipe preceded by an ODD run of
                     backslashes. `escape_pipes` reads such a run as an existing
                     escape and passes it through, so the forward function is not
                     injective and nothing can invert it.

  skill-trigger-test said "1 strict-threshold breached or a skill left
                     unmeasured", stating the unmeasured case unconditionally,
                     eight lines below a promise that the default "always exit 0
                     on a completed run". `main` gates both on `--strict`, so one
                     of the two sentences had to be false.

  corpus_issues      defined a valid negative as a "hard negative naming the
                     neighbor skill they should route to" and then counted
                     `should_trigger is False`. Two off-topic trivia queries
                     satisfied the count, so the F-6.1 coverage gate passed a
                     corpus that cannot catch the routing hijack it exists for.

Every test below pairs the prose assertion with a behavioural one, so neither
side can drift alone. Whitespace is collapsed before matching, because a re-wrap
is not a change of claim.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GEN_PATH = ROOT / "scripts" / "generate-skill-router.py"
TRIGGER_PATH = ROOT / "scripts" / "skill-trigger-test.py"
METADATA_PATH = ROOT / "scripts" / "skill-metadata-check.py"


def _flat(text: str) -> str:
    """Whitespace-collapsed, so a re-wrap or a re-indent is not a new claim."""
    return " ".join(text.split())


def _claim(doc: str, opens_with: str) -> str:
    """The one PARAGRAPH of `doc` that opens with `opens_with`, flattened.

    Whole-docstring matching cannot be used on any of these, and finding out why
    is worth the helper. The house style retires a false sentence by QUOTING it
    beside the correction ("the line read X until 2026-09-02"), so the retired
    wording stays a substring of the file forever. A test asserting `X not in
    doc` then fails on the very fix it was written to hold - measured here on the
    first run, on both of the two docstrings that carry such a quote.

    Scoping to the paragraph that states the ACTIVE contract keeps the teeth:
    restoring the old wording puts it back in this paragraph, where it is seen,
    while leaving it in the history paragraph, where it belongs. Raises when no
    paragraph opens with the marker, so a reworded opener fails loudly rather
    than silently matching the empty string.
    """
    for block in (doc or "").split("\n\n"):
        flat = _flat(block)
        if flat.startswith(opens_with):
            return flat
    raise AssertionError(
        f"no paragraph opening with {opens_with!r}; the claim was reworded or "
        f"removed, which retires this pin and needs a human"
    )


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    return _load("router_docstring_gen", GEN_PATH)


# ============================================================
# 1. splice_region: "byte-for-byte" was retired once and left in place here
# ============================================================

def test_the_splice_docstring_no_longer_claims_bytes_it_does_not_preserve(gen):
    """The two docstrings in one file must not contradict each other.

    `read_text` / `write_text` apply universal-newline translation, so a CRLF
    router file is rewritten LF and the bytes outside the markers DO change. The
    module docstring says so in as many words.
    """
    fn = _flat(gen.splice_region.__doc__ or "")
    module = _flat(gen.__doc__ or "")

    assert "preserved byte-for-byte" not in fn, (
        "splice_region still claims byte-for-byte preservation, which its own "
        "module docstring records as false for any non-LF file"
    )
    assert "line endings aside" in fn.lower(), (
        "the narrowed claim must be stated, not merely have the false one removed"
    )
    assert "LINE ENDINGS ASIDE" in module, (
        "the module docstring lost the qualifier this function now defers to"
    )


def test_the_splice_docstring_states_the_duplicate_marker_refusal(gen):
    """The other half of the same docstring's contract.

    `splice_region` gained a raise on a doubled BEGIN or END marker; the
    docstring named only the missing-marker raise, so a caller reading it would
    not know a second failure mode existed.
    """
    fn = _flat(gen.splice_region.__doc__ or "")
    assert "more than once" in fn, fn
    text = gen.MARKER_BEGIN + "\nA\n" + gen.MARKER_BEGIN + "\nB\n" + gen.MARKER_END
    with pytest.raises(ValueError):
        gen.splice_region(text, "NEW")


# ============================================================
# 2. unescape_pipes: not an inverse, and the odd run is why
# ============================================================

def test_unescape_pipes_no_longer_calls_itself_the_exact_inverse(gen):
    """Prose and behaviour, together. Either alone can rot."""
    fn = _flat(gen.unescape_pipes.__doc__ or "")
    assert "The exact inverse" not in fn, (
        "unescape_pipes still claims to be the exact inverse of escape_pipes"
    )

    lossy = "C:\\|foo"
    assert gen.unescape_pipes(gen.escape_pipes(lossy)) == "C:|foo", (
        "the odd-backslash-run round trip changed; if it is now lossless the "
        "docstring's whole caveat is stale and must be rewritten with it"
    )


def test_the_escape_docstring_no_longer_credits_parity_with_the_odd_run(gen):
    """`escape_pipes` named `C:\\|foo` as the case parity repaired. It is the
    case parity does NOT repair: one backslash is an odd run, so the old
    lookbehind and the parity rule agree on it and both leave the pipe bare.
    Parity fixes the EVEN run."""
    fn = gen.escape_pipes.__doc__ or ""

    assert gen.escape_pipes("C:\\|foo") == "C:\\|foo", (
        "the odd run is now escaped, so the docstring's stated limit is stale"
    )
    assert gen.escape_pipes("C:\\\\|foo") == "C:\\\\\\|foo", (
        "the EVEN run is what parity repairs, and it stopped being repaired"
    )
    assert "does NOT repair" in fn, (
        "the docstring must say which run parity leaves alone, or the next "
        "reader re-derives the same wrong example"
    )


def test_the_round_trip_is_still_lossless_everywhere_else(gen):
    """The bound on the caveat. Exactly one shape is lossy; if the caveat grew
    to cover ordinary pipes, the pair would be broken rather than limited."""
    for value in ("a|b", "C:\\\\|foo", "no pipes here", "a|b|c"):
        assert gen.unescape_pipes(gen.escape_pipes(value)) == value, value


# ============================================================
# 3. skill-trigger-test: the unmeasured exit is strict-only
# ============================================================

def test_the_exit_code_line_bounds_the_unmeasured_case_to_strict():
    """The docstring stated it unconditionally and contradicted itself.

    Checked as text AND against the code: the only `return 1` in `main` must sit
    under a condition that reads `args.strict`, which is what makes the corrected
    sentence true.
    """
    source = TRIGGER_PATH.read_text(encoding="utf-8")
    claim = _claim(ast.get_docstring(ast.parse(source)) or "", "Exit codes:")

    assert "1 strict-threshold breached or a skill left unmeasured" not in claim, (
        "the exit-code line still says an unmeasured skill exits 1 regardless "
        "of --strict, which contradicts both the code and the advisory promise "
        "above it"
    )
    assert "UNDER --strict" in claim, claim

    tree = ast.parse(source)
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    guarded = []
    for node in ast.walk(main):
        if not isinstance(node, ast.If):
            continue
        if "strict" not in ast.unparse(node.test):
            continue
        guarded.extend(
            s for s in node.body
            if isinstance(s, ast.Return) and getattr(s.value, "value", None) == 1
        )
    assert guarded, (
        "no `return 1` in main() is guarded by a condition mentioning strict, "
        "so the corrected docstring now over-claims in the other direction"
    )


def test_the_advisory_promise_and_the_exit_code_line_agree():
    """The two sentences that could not both be true. Both must survive, and the
    contradiction must not."""
    doc = _flat(ast.get_docstring(ast.parse(
        TRIGGER_PATH.read_text(encoding="utf-8"))) or "")
    assert "always exit 0 on a completed run" in doc, (
        "the advisory promise was deleted rather than reconciled"
    )


# ============================================================
# 4. corpus_issues: a shape check, described as a hijack gate
# ============================================================

def test_the_corpus_docstring_no_longer_claims_a_hard_negative_check(tmp_path):
    """The gate must not read as coverage it does not have.

    Nothing in a `{query, should_trigger}` case records a neighbor skill, so the
    check cannot enforce what the old parenthetical described.
    """
    check = _load("skill_metadata_corpus", METADATA_PATH)
    doc = check.corpus_issues.__doc__ or ""
    claim = _claim(doc, "A JSON array")

    assert "hard negatives naming the neighbor skill they should route to" not in claim, (
        "corpus_issues still describes a hard-negative rule it never enforces"
    )
    assert "should_trigger` is false" in claim, (
        "the enforced rule must be stated as what it is - a count of "
        f"should_trigger values. Got: {claim}"
    )
    assert "SHAPE" in _claim(doc, "Validate"), (
        "the summary line must say what this is - a shape check"
    )

    # The behaviour the corrected docstring now claims, measured.
    corpus = tmp_path / "triggers.json"
    corpus.write_text(json.dumps(
        [{"query": f"do the thing {i}", "should_trigger": True} for i in range(4)]
        + [{"query": "what is the weather", "should_trigger": False},
           {"query": "how tall is a giraffe", "should_trigger": False}]
    ), encoding="utf-8")
    assert check.corpus_issues(corpus) == [], (
        "generic negatives naming no neighbor skill were refused, so the check "
        "is now stronger than its docstring says"
    )


def test_the_corpus_shape_rule_is_still_enforced(tmp_path):
    """The anchor. Softening a docstring must not be mistaken for softening the
    check: the counts it does enforce still have to fail a thin corpus."""
    check = _load("skill_metadata_corpus_2", METADATA_PATH)
    corpus = tmp_path / "triggers.json"
    corpus.write_text(json.dumps(
        [{"query": "a", "should_trigger": True},
         {"query": "b", "should_trigger": False}]
    ), encoding="utf-8")
    issues = check.corpus_issues(corpus)
    assert len(issues) == 3, issues
    assert any("cases <" in i for i in issues), issues
    assert any("positive <" in i for i in issues), issues
    assert any("negative <" in i for i in issues), issues
