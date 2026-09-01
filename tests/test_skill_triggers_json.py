"""Every trigger corpus on disk is valid, against ONE definition of valid.

Was F-M13. Rewritten 2026-08-23 after the audit found two definitions of "valid
triggers.json" in the same suite:

- this file demanded `>= 6` positives AND `>= 6` negatives, for four hardcoded
  skills (`odin`, `email-draft`, `thread`, `linkedin-series`);
- the F-6.1 coverage gate in `scripts/skill-metadata-check.py` accepts `>= 6`
  cases with `>= 4` positive and `>= 2` negative, and `tests/test_skill_trigger_
  coverage.py` pins a 4/2 corpus as COVERED.

The gate is the shipped policy: it runs unconditionally in CI and pre-commit and
covers every auto-routable skill. Measured on 2026-08-23, 70 skills carry a
corpus and most sit at 5 positive / 3 negative - below the old 6/6 bar and above
the gate's. So the 6/6 number was not the policy; it was a stricter bar that four
skills happened to meet and no process enforced.

Two things changed. The thresholds are now IMPORTED from the gate rather than
retyped, so the two can no longer drift. And the skill set is derived from disk
instead of hardcoded, so all 70 corpora are checked - the old list covered four
and let the other sixty-six through with no structural check at all.

Existence is not asserted here: the coverage gate already fails an auto-routable
skill with no corpus, and `test_skill_trigger_coverage.py` pins that behaviour.
This file checks the CONTENT of every corpus that exists.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
SKILLS = ENGINE / ".claude" / "skills"

sys.path.insert(0, str(ENGINE))
from tests.repo_files import read_sources  # noqa: E402

# The single source of truth, loaded by path (the filename has a hyphen).
_spec = importlib.util.spec_from_file_location(
    "_skill_metadata_check", ENGINE / "scripts" / "skill-metadata-check.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

MIN_CASES = gate.TRIGGERS_MIN_CASES
MIN_POS = gate.TRIGGERS_MIN_POS
MIN_NEG = gate.TRIGGERS_MIN_NEG

# A bare single-level glob, deliberately, rather than `tests.repo_files.
# tracked_paths`. The usual reason for that routing is an agent worktree under
# `.claude/worktrees/` doubling a corpus and making every floor meaningless, and
# this glob cannot reach one: it is rooted at `.claude/skills` and matches one
# directory level. What it CAN reach that a git-filtered walk might not is a
# corpus written for a new skill and not yet staged, which is the corpus most
# likely to be malformed. MEASURED 2026-09-01: the two walks return the same 70
# files on this tree, so the choice costs nothing today; it is stated so the next
# reader does not "fix" it into the narrower one. The floor below is the guard.
CORPORA = sorted(SKILLS.glob("*/triggers.json"))

# The four structural cases below parametrize over `(path, raw)` rather than the
# path, so the corpus is read ONCE here, at collection. Four cases per skill each
# opening the same file put four copies of the walk/read race in this module: the
# glob above runs when the decorators are evaluated and the reads ran at
# execution, minutes later under `-n auto`, so a corpus written for a new skill
# and moved moments later would raise FileNotFoundError from inside four guards
# at once. Reading here removes the window instead of narrowing it four times.
#
# The TEXT is what is handed on, not the parsed object: `test_the_corpus_is_a_
# json_array` exists to report malformed JSON as a test FAILURE naming the skill,
# and parsing at collection would turn that into a collection error instead.
#
# Two siblings still fail on a corpus that really went away --
# `test_the_corpora_glob_finds_something` floors the glob above 50, and
# `test_the_gate_and_this_file_agree_on_every_corpus` retries then fails naming
# the file -- so nothing here can report clean over a set that shrank.
_CORPORA_VANISHED: list[Path] = []
CORPUS_SOURCES = list(read_sources(CORPORA, _CORPORA_VANISHED))
IDS = [p.parent.name for p, _ in CORPUS_SOURCES]


def test_the_corpora_glob_finds_something():
    """A glob that resolves to nothing makes every parametrized test vacuous."""
    assert len(CORPORA) > 50, f"only found {len(CORPORA)} corpora"


def test_the_thresholds_come_from_the_gate_not_from_here():
    """Pins the single-source property itself, not the numbers."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert "gate.TRIGGERS_MIN_CASES" in source
    assert "gate.TRIGGERS_MIN_POS" in source
    assert "gate.TRIGGERS_MIN_NEG" in source
    # And the gate really defines them, so a rename cannot pass silently.
    assert isinstance(MIN_CASES, int) and MIN_CASES > 0
    assert isinstance(MIN_POS, int) and MIN_POS > 0
    assert isinstance(MIN_NEG, int) and MIN_NEG > 0


@pytest.mark.parametrize("path,raw", CORPUS_SOURCES, ids=IDS)
def test_the_corpus_is_a_json_array(path: Path, raw: str):
    data = json.loads(raw)
    assert isinstance(data, list), f"{path.parent.name}/triggers.json must be an array"


@pytest.mark.parametrize("path,raw", CORPUS_SOURCES, ids=IDS)
def test_every_case_has_the_required_fields(path: Path, raw: str):
    data = json.loads(raw)
    name = path.parent.name
    for i, entry in enumerate(data):
        assert isinstance(entry, dict), f"{name} entry[{i}] is not an object"
        assert "query" in entry, f"{name} entry[{i}] missing 'query'"
        assert "should_trigger" in entry, f"{name} entry[{i}] missing 'should_trigger'"
        assert isinstance(entry["query"], str), f"{name} entry[{i}]['query'] not a string"
        assert entry["query"].strip(), f"{name} entry[{i}]['query'] is empty"
        assert isinstance(entry["should_trigger"], bool), \
            f"{name} entry[{i}]['should_trigger'] not a bool"


@pytest.mark.parametrize("path,raw", CORPUS_SOURCES, ids=IDS)
def test_the_corpus_meets_the_gate_thresholds(path: Path, raw: str):
    data = json.loads(raw)
    name = path.parent.name
    positives = [e for e in data if e.get("should_trigger") is True]
    negatives = [e for e in data if e.get("should_trigger") is False]
    assert len(data) >= MIN_CASES, \
        f"{name}: {len(data)} cases < {MIN_CASES} required"
    assert len(positives) >= MIN_POS, \
        f"{name}: {len(positives)} positive < {MIN_POS} required"
    assert len(negatives) >= MIN_NEG, \
        f"{name}: {len(negatives)} negative < {MIN_NEG} required"


@pytest.mark.parametrize("path,raw", CORPUS_SOURCES, ids=IDS)
def test_no_duplicate_queries_within_a_corpus(path: Path, raw: str):
    """A corpus padded with repeats meets the count and tests nothing new."""
    data = json.loads(raw)
    seen: dict[str, int] = {}
    for entry in data:
        key = entry["query"].strip().lower()
        seen[key] = seen.get(key, 0) + 1
    dupes = {q: n for q, n in seen.items() if n > 1}
    assert dupes == {}, f"{path.parent.name}: repeated queries {dupes}"


def test_the_gate_and_this_file_agree_on_every_corpus():
    """Cross-check: no corpus may pass one contract and fail the other.

    That divergence is the defect this rewrite closes, so it is asserted
    directly rather than inferred from the two using the same constants.
    """
    disagreements = []
    for path in CORPORA:
        # The walk that built CORPORA and this read are two moments. Skipping a
        # corpus that went missing in between would leave `disagreements` empty
        # and print "no corpus may pass one contract and fail the other" over a
        # set that quietly shrank - a narrowed check reported as a complete one.
        # Retry once for the race, then fail naming the corpus.
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError as gone:
                raise AssertionError(
                    f"{path} vanished between the walk and the read, so this "
                    f"cross-check would report agreement over a corpus it never "
                    f"compared") from gone
        data = json.loads(raw)
        positives = sum(1 for e in data if e.get("should_trigger") is True)
        negatives = sum(1 for e in data if e.get("should_trigger") is False)
        here_ok = (len(data) >= MIN_CASES and positives >= MIN_POS
                   and negatives >= MIN_NEG)
        gate_ok = gate.is_valid_corpus(path)
        if here_ok != gate_ok:
            disagreements.append(
                (path.parent.name, here_ok, gate_ok, gate.corpus_issues(path))
            )
    assert disagreements == [], disagreements


def test_the_cross_check_can_actually_disagree(tmp_path: Path):
    """A cross-check that both sides always answer the same way proves nothing."""
    thin = tmp_path / "triggers.json"
    thin.write_text(json.dumps(
        [{"query": f"q{i}", "should_trigger": i < 3} for i in range(4)]
    ), encoding="utf-8")
    assert gate.is_valid_corpus(thin) is False
    assert gate.corpus_issues(thin), "the gate reported no issue on a 3/1 corpus"
