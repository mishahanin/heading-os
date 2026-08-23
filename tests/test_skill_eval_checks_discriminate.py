"""Eval checks must be able to fail, and a self-seeded baseline must say so.

Found by the 2026-08-23 audit, in two halves.

**Half one: the checks.** `run_checks` matched a term with plain
`term.lower() in output.lower()`. `brain-audit/case-3-boundaries` asserted
`must_mention: ["no"]`, and "no" is a substring of "not", "know", "cannot",
"note", "another". Any answer meeting the 30-word floor passed it - including
one that got the boundaries exactly backwards. Twenty-two further terms of four
characters or fewer sit in the corpus behind the same weakness. Matching is now
word-bounded, with the boundary added only on an edge that is a word character
so the twelve punctuation- and emoji-edged terms still work.

**Half two: the baseline.** Twelve of sixteen `benchmark.json` files carried a
`baseline` byte-identical to `last_run`, timestamps included. That is not
fabrication - `run_one_skill` seeds the baseline from the first run when none
exists, which is the right bootstrap - but nothing in the file said so, and a
structurally-zero delta reads exactly like "no regression detected". The seed is
now labelled in the file and announced on stdout.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "run-skill-eval.py"
SKILLS = ROOT / ".claude" / "skills"

_spec = importlib.util.spec_from_file_location("_run_skill_eval_under_test", RUNNER)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


# ------------------------------------------------------- word-bounded matching

def test_a_short_term_no_longer_matches_inside_a_longer_word():
    """The exact defect: "no" passing on "not", "know", "cannot"."""
    for output in ("It does not persist.", "I know the answer.",
                   "It cannot run.", "Take note of this.", "Another thing."):
        assert not runner.any_match(output, "no"), output


def test_the_same_short_term_still_matches_the_real_word():
    assert runner.any_match("No, it does not.", "no")
    assert runner.any_match("The answer is no.", "no")


@pytest.mark.parametrize("term,hit,miss", [
    ("add", "please add a contact", "the address book"),
    ("log", "log the call", "the login page"),
    ("new", "marp new deck", "renew the licence"),
    ("from", "build from markdown", "fromage"),
])
def test_the_other_short_terms_became_real_checks(term, hit, miss):
    assert runner.any_match(hit, term)
    assert not runner.any_match(miss, term)


@pytest.mark.parametrize("term", [
    "$350,000", ".workspace-identity.json", ".jsonl", "Hi there!",
    "\U0001F680", "31C", "pdf", "HIGH", "VIIA", "75",
])
def test_punctuation_and_emoji_edged_terms_still_match(term):
    """A naive \\b on both ends would silently stop matching these twelve."""
    assert runner.any_match(f"prefix {term} suffix", term), term


def test_a_term_list_means_any_of_these():
    """Word boundaries are strict about inflection; a list restores the OR."""
    assert runner.any_match("no persistence at all",
                            ["persist", "persistence", "persisting"])
    assert runner.any_match("it does not persist",
                            ["persist", "persistence", "persisting"])
    assert not runner.any_match("it writes nothing",
                                ["persist", "persistence", "persisting"])


def test_a_bare_string_still_behaves_as_before():
    assert runner.any_match("uses the CRM", "CRM")
    assert not runner.any_match("uses the CRM", "pipeline")


def test_must_not_mention_uses_the_same_matcher():
    results = runner.run_checks(
        "It does not run continuously.",
        {"must_not_mention": ["continuous"]},
        SKILLS / "brain-audit",
    )
    # "continuous" is not the word "continuously" under boundary matching.
    assert results[0]["passed"] is True


# ------------------------------------------------------------ the fixed case

def test_the_vacuous_brain_audit_case_is_gone():
    case = json.loads(
        (SKILLS / "brain-audit" / "evals" / "cases" / "case-3-boundaries.json")
        .read_text(encoding="utf-8")
    )
    assert "no" not in case["checks"]["must_mention"]
    assert "daemon runs continuously" not in case["checks"]["must_not_mention"]


def test_the_fixed_case_fails_an_answer_that_dodges_the_question():
    case = json.loads(
        (SKILLS / "brain-audit" / "evals" / "cases" / "case-3-boundaries.json")
        .read_text(encoding="utf-8")
    )
    dodge = ("The skill produces a footer summarising the newest source dates "
             "and modality coverage for the entity you name, and it composes "
             "into the synthesis skills that call it at the end of their work.")
    results = runner.run_checks(dodge, case["checks"], SKILLS / "brain-audit")
    assert [r for r in results if not r["passed"]], (
        "the reworked case still passes an answer that never addresses the "
        "three boundaries it asks about"
    )


def test_the_fixed_case_passes_a_correct_answer():
    case = json.loads(
        (SKILLS / "brain-audit" / "evals" / "cases" / "case-3-boundaries.json")
        .read_text(encoding="utf-8")
    )
    correct = ("No to all three. The skill does not persist anything between "
               "invocations, there is no daemon behind it, and it never scans "
               "the whole workspace: it reads the source set it is handed and "
               "the canonical locations for the named entity, then emits its "
               "footer and stops.")
    results = runner.run_checks(correct, case["checks"], SKILLS / "brain-audit")
    failures = [r["check"] for r in results if not r["passed"]]
    assert failures == [], failures


def test_the_case_names_what_it_cannot_check():
    """Coverage is not correctness, and the file has to say which it is."""
    case = json.loads(
        (SKILLS / "brain-audit" / "evals" / "cases" / "case-3-boundaries.json")
        .read_text(encoding="utf-8")
    )
    assert "POLARITY" in case["description"]


# ------------------------------------------------------------- the baseline

def _benchmarks() -> list[Path]:
    return sorted(SKILLS.glob("*/evals/benchmark.json"))


def test_every_benchmark_declares_whether_its_baseline_is_a_self_seed():
    missing = [p.parent.parent.name for p in _benchmarks()
               if "baseline_is_self_seed" not in
               json.loads(p.read_text(encoding="utf-8"))]
    assert missing == [], missing


def test_the_label_matches_the_data():
    """A label nothing verifies would rot into a second lie."""
    wrong = []
    for path in _benchmarks():
        data = json.loads(path.read_text(encoding="utf-8"))
        baseline = dict(data["baseline"])
        baseline.pop("source", None)
        identical = (json.dumps(baseline, sort_keys=True)
                     == json.dumps(data["last_run"], sort_keys=True))
        if identical != data["baseline_is_self_seed"]:
            wrong.append(path.parent.parent.name)
    assert wrong == [], wrong


def test_some_benchmarks_carry_a_real_baseline():
    """If every one were a self-seed, the label would be carrying no signal."""
    real = [p.parent.parent.name for p in _benchmarks()
            if not json.loads(p.read_text(encoding="utf-8"))["baseline_is_self_seed"]]
    assert real, "no benchmark has ever been compared against a real baseline"


def test_the_writer_labels_a_fresh_seed(tmp_path: Path):
    source = RUNNER.read_text(encoding="utf-8")
    assert 'existing["baseline"]["source"] = "seeded-from-first-run"' in source
    assert '"baseline_is_self_seed"' in source
    assert "baseline is a self-seed" in source
