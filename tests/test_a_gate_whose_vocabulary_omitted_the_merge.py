#!/usr/bin/env python3
"""The release gate could not hear the one verb the cycle is built on.

MEASURED 2026-09-06. The operator typed `merge`. The gate refused, echoing the
prompt back as its own evidence:

    RELEASE GATE: the operator did not ask for a commit in this turn.
      [operator-prompt] 'merge'

He typed it a second time and it refused again. `_COMMIT_WORDS` held
`commit`, `коммит`, `закоммить`, `зафиксируй`; `_PUSH_WORDS` held eleven
entries; neither list held a merge word in either language.

Why that is a defect and not a missing convenience: `CLAUDE.md` names the merge
as one of exactly four things HELM does, beside the push, `/backup` and the
daemons, and the whole YARD cycle ends with it. The wall guarding that cycle
could not express its own middle step, so the operator had to be told which
other word to type instead. A wall that teaches people to substitute a word they
did not mean is the same failure the per-action split of 2026-09-03 was written
to remove, one level up.

Both directions, and the failing half fails against the previous version:

* `merge` authorises a COMMIT (returned False before this change);
* `merge` does NOT authorise a PUSH, because a merge is local;
* `не мержи` refuses, so the permission can be withdrawn in the sentence that
  grants it;
* a brief carrying `_BRIEF_MARKER` still refuses, whatever it says, so the new
  words did not open a door for another session.

Run: .venv/bin/python -m pytest \\
     tests/test_a_gate_whose_vocabulary_omitted_the_merge.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("dispatch_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# The word the operator actually typed
# ============================================================

@pytest.mark.parametrize("prompt", [
    "merge",
    "merge and delete all yards",
    "коммить и мерж",
    "смержить ветку в main",
    "смёржить ветку в main",       # the same word, spelled with ё
    "Merge It",                    # case is folded before matching
])
def test_a_merge_request_authorises_the_commit_it_needs(gate, prompt):
    assert gate.prompt_authorises(prompt, "commit") is True, (
        f"{prompt!r} did not authorise a commit, so the operator is again told "
        f"to type a word other than the one they meant")


def test_a_merge_request_does_not_authorise_a_push(gate):
    """A merge is local. Nothing about it asks for the work to leave the machine."""
    for prompt in ("merge", "смёржить ветку в main", "merge and delete all yards"):
        assert gate.prompt_authorises(prompt, "push") is False, (
            f"{prompt!r} authorised a PUSH; asking for a merge is not asking to "
            f"publish")


# ============================================================
# The refusing direction
# ============================================================

@pytest.mark.parametrize("prompt", [
    "не мержи пока",
    "не мёржи пока",
    "без мержа, просто покажи диф",
    "don't merge yet",
    "do not merge",
    "no merge today",
])
def test_a_withdrawn_merge_refuses(gate, prompt):
    assert gate.prompt_authorises(prompt, "commit") is False, (
        f"{prompt!r} authorised a commit; a permission word with no negation "
        f"beside it cannot be taken back in the sentence that grants it")


def test_the_existing_commit_words_are_unchanged(gate):
    """The other direction of the edit: nothing that worked stopped working."""
    for prompt in ("commit", "коммит всё", "закоммить и всё", "зафиксируй это"):
        assert gate.prompt_authorises(prompt, "commit") is True, prompt
    for prompt in ("push", "сделай пуш", "залей на github"):
        assert gate.prompt_authorises(prompt, "push") is True, prompt
    assert gate.prompt_authorises("не коммить", "commit") is False
    assert gate.prompt_authorises("не пушь", "push") is False


def test_an_unrelated_prompt_still_authorises_nothing(gate):
    """A guard that permits everything is not a guard."""
    for prompt in ("что осталось открытым?", "почини тесты", "покажи статус", ""):
        assert gate.prompt_authorises(prompt, "commit") is False, prompt
        assert gate.prompt_authorises(prompt, "push") is False, prompt


# ============================================================
# The new words must not open a door for another session
# ============================================================

def test_a_brief_asking_for_a_merge_still_authorises_nothing(gate):
    """The marker is checked before any word is looked at, and must stay so."""
    brief = f"{gate._BRIEF_MARKER}\n\nMirim, смёржить ветку и запушить."
    assert gate.prompt_authorises(brief, "commit") is False, (
        "a marked brief authorised a commit through the merge vocabulary, so "
        "the words added here reopened the hole the marker closes")
    assert gate.prompt_authorises(brief, "push") is False


def test_every_merge_word_is_covered_by_a_negation(gate):
    """A floor, so the lists cannot drift apart as either one grows.

    MEASURED 2026-09-06: 3 merge permissions, 8 merge negations.
    """
    merge_words = [w for w in gate._COMMIT_WORDS
                   if "мерж" in w or "мёрж" in w or "merge" in w]
    assert len(merge_words) == 3, (
        f"the merge vocabulary changed size ({merge_words}); confirm each new "
        f"spelling can also be refused, then update this floor")
    for word in merge_words:
        assert any(word in neg for neg in gate._COMMIT_NEGATIONS), (
            f"{word!r} can be granted but never withdrawn")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
