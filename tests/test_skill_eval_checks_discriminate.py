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
import re
import sys
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
    results = runner.run_checks(dodge, case["checks"])
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
    results = runner.run_checks(correct, case["checks"])
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


def _read_or_fail(path: Path) -> str:
    """Read a benchmark the three checks below claim to have read ALL of.

    The walk and the read are two moments and a file can disappear between
    them. For a scan that is a skip, because an absent file violates nothing.
    Not here: each of the three assertions is about EVERY benchmark - one
    declares its label, one matches it against the data, one proves at least
    one real baseline exists - so a quietly dropped file turns "all of them
    declare it" into a claim about a corpus nobody named. One retry closes the
    race window; a file that is still gone fails by name.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise AssertionError(
                f"{path} vanished between the walk and the read and was still "
                f"gone one retry later; these checks answer about every "
                f"benchmark, so they cannot answer with one missing."
            ) from exc


def test_every_benchmark_declares_whether_its_baseline_is_a_self_seed():
    missing = [p.parent.parent.name for p in _benchmarks()
               if "baseline_is_self_seed" not in json.loads(_read_or_fail(p))]
    assert missing == [], missing


def test_the_label_matches_the_data():
    """A label nothing verifies would rot into a second lie."""
    wrong = []
    for path in _benchmarks():
        data = json.loads(_read_or_fail(path))
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
            if not json.loads(_read_or_fail(p))["baseline_is_self_seed"]]
    assert real, "no benchmark has ever been compared against a real baseline"


# The writer, run rather than grepped.
#
# This was three `in source` assertions over `scripts/run-skill-eval.py`, and a
# grep is not a test of a behaviour: a comment satisfies it. MEASURED 2026-09-01
# by changing the assignment to `existing["baseline"]["source"] = "x"` while
# leaving the searched literal in a trailing comment on the same line. The label
# was dead - a fresh seed came out `baseline_is_self_seed: false`, which is the
# exact reading the whole second half of this file exists to prevent - and the
# suite stayed GREEN, here and across every other file naming run-skill-eval.
# Pinning the label to a constant `False` survived the same way. The one
# neighbour with a real harness
# (`test_a_broken_fixture_that_billed_itself_as_an_api_error.py`) asserts
# `baseline_is_self_seed is False` on the promoted and non-object paths; nothing
# asserted the TRUE side, and nothing asserted the stdout warning at all.


@pytest.fixture
def _runner():
    spec = importlib.util.spec_from_file_location("run_skill_eval", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_skill_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _first_run(tmp_path, _runner, monkeypatch):
    """One graded run against a scratch skills tree, with no API call.

    Returns the benchmark.json path. Nothing here touches the real catalog:
    `SKILLS_DIR` is the seam the rest of this suite redirects.
    """
    root = tmp_path / ".claude" / "skills"
    (root / "q-branch" / "evals" / "cases").mkdir(parents=True)
    (root / "q-branch" / "SKILL.md").write_text(
        "---\nname: q-branch\nmodel: haiku\n---\nBody of the skill.\n",
        encoding="utf-8")
    (root / "q-branch" / "evals" / "cases" / "case-1.json").write_text(
        json.dumps({"id": "case-1", "input": "brief me", "checks": {"min_words": 2}}),
        encoding="utf-8")
    monkeypatch.setattr(_runner, "SKILLS_DIR", root)
    monkeypatch.setattr(_runner, "call_skill", lambda system, user, model: (
        "gadget briefing text here",
        {"input_tokens": 1, "output_tokens": 1,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}, 0.1))
    monkeypatch.setattr(sys, "argv", ["run-skill-eval.py", "--skill", "q-branch"])
    return root / "q-branch" / "evals" / "benchmark.json"


def test_a_first_run_seeds_the_baseline_and_says_so_in_the_file(_runner, _first_run):
    """The TRUE side of the label, which nothing bound."""
    assert _runner.main() == 0
    written = json.loads(_first_run.read_text(encoding="utf-8"))
    assert written["baseline"]["source"] == "seeded-from-first-run"
    assert written["baseline_is_self_seed"] is True
    # And it really is the self-seed the label describes: identical but for the
    # marker the writer adds.
    seeded = dict(written["baseline"])
    assert seeded.pop("source") == "seeded-from-first-run"
    assert seeded == written["last_run"]


def test_a_first_run_announces_the_self_seed_on_stdout(_runner, _first_run, capsys):
    """The other half of the fix: the operator reading the terminal is told."""
    _runner.main()
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "baseline is a self-seed" in out
    assert "delta detects nothing" in out


def test_a_second_run_against_the_seeded_baseline_stays_labelled(_runner, _first_run):
    """The label is recomputed each write, so it must not decay after run one."""
    assert _runner.main() == 0
    assert _runner.main() == 0
    written = json.loads(_first_run.read_text(encoding="utf-8"))
    assert written["baseline_is_self_seed"] is True, (
        "the second run compared against a self-seed and stopped saying so")
