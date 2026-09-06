#!/usr/bin/env python3
"""The release gate learned `merge` and still could not hear `слить`.

MEASURED 2026-09-06, against the version this test's fix replaced. The operator
typed

    сливай фикс w5M, удаляй ярд

and the wall refused the commit, quoting that sentence back as its own evidence.
`prompt_authorises("сливай фикс w5M, удаляй ярд", "commit")` returned False.

That is the SECOND turn of one screw. On the same day `merge`, `мерж` and `мёрж`
were added after the operator typed `merge` twice and was refused twice. The
Russian half of the same verb was left out, so the vocabulary covered one of the
two words the operator actually uses for one action, and the refusal was purely
lexical: the intent was unambiguous and the gate had no way to express it.

Both directions, and the failing half fails against the previous version:

* `сливай` and its siblings authorise a COMMIT (all returned False before);
* they do NOT authorise a PUSH, exactly as `merge` does not: a merge is local;
* `не сливай` refuses, so the permission can be withdrawn in the sentence that
  grants it;
* `слишком` does NOT authorise anything, which is the negative anchor that tells
  this rule from a naive `сли` prefix match;
* a brief carrying `_BRIEF_MARKER` still refuses, so the new words did not open
  a door for another Claude session.

Run: .venv/bin/python -m pytest \\
     tests/test_a_gate_that_could_not_hear_the_russian_word_for_merge.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"

#: The root's spellings this change added, as they appear in `_COMMIT_WORDS`.
#: A floor, so the family cannot quietly shrink to nothing and leave every
#: assertion below passing over an empty set.
RUSSIAN_MERGE_ROOTS = ("слить", "слив", "слей", "сольё", "солью")


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("dispatch_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# The sentence the operator actually typed
# ============================================================

@pytest.mark.parametrize("prompt", [
    "сливай фикс w5M, удаляй ярд",   # verbatim, 2026-09-06
    "слей ветку в main",
    "слить ветку и удалить ярд",
    "давай сольём это в main",
    "я солью ветку сам, просто зафиксируй",
    "Сливай It",                     # case is folded before matching
])
def test_a_russian_merge_request_authorises_the_commit_it_needs(gate, prompt):
    assert gate.prompt_authorises(prompt, "commit") is True, (
        f"{prompt!r} did not authorise a commit, so the operator is again told "
        f"to type a word other than the one they meant")


def test_a_russian_merge_request_does_not_authorise_a_push(gate):
    """A merge is local in either language."""
    for prompt in ("сливай фикс w5M, удаляй ярд", "слей ветку в main",
                   "давай сольём это в main"):
        assert gate.prompt_authorises(prompt, "push") is False, (
            f"{prompt!r} authorised a PUSH; asking for a merge is not asking "
            f"for the work to leave the machine")


# ============================================================
# The refusing direction
# ============================================================

@pytest.mark.parametrize("prompt", [
    "не сливай пока",
    "не слей это в main",
    "не слить, сначала покажи диф",
    "без слива, просто прогони тесты",
    "не сольём это сегодня",
    "не солью, подождём HELM",
])
def test_a_withdrawn_russian_merge_refuses(gate, prompt):
    assert gate.prompt_authorises(prompt, "commit") is False, (
        f"{prompt!r} authorised a commit; a permission word with no negation "
        f"beside it cannot be taken back in the sentence that grants it")


def test_every_russian_merge_word_is_covered_by_a_negation(gate):
    """The pairing, asked of the lists rather than of one example each.

    MEASURED 2026-09-06: 5 permissions in this family, 6 negations.
    """
    present = [w for w in RUSSIAN_MERGE_ROOTS if w in gate._COMMIT_WORDS]
    assert len(present) == len(RUSSIAN_MERGE_ROOTS), (
        f"the Russian merge vocabulary shrank ({present}); a family that can "
        f"empty out makes every assertion in this file pass over nothing")
    for word in present:
        assert any(word in negation for negation in gate._COMMIT_NEGATIONS), (
            f"{word!r} can be granted but never withdrawn, so `не {word}...` "
            f"authorises what it forbids")


# ============================================================
# The negative anchor: a root, not a prefix
# ============================================================

@pytest.mark.parametrize("prompt", [
    "это слишком сложно, покажи диф",       # `сли` + `шком`
    "слишком много тестов упало",
    "пришли мне список файлов",
    "что осталось открытым?",
    "",
])
def test_a_word_that_merely_starts_like_it_authorises_nothing(gate, prompt):
    """What tells this rule from a bare three-letter prefix match.

    `слишком` is the word that makes `сли` unusable, and it is common in this
    operator's prompts. A corpus that cannot distinguish the two rules cannot
    tell whether the narrower one was ever implemented.
    """
    assert gate.prompt_authorises(prompt, "commit") is False, prompt
    assert gate.prompt_authorises(prompt, "push") is False, prompt


# ============================================================
# The other direction of the edit
# ============================================================

def test_the_existing_vocabulary_is_unchanged(gate):
    """Nothing that worked stopped working."""
    for prompt in ("commit", "коммит всё", "зафиксируй это", "merge",
                   "смержить ветку в main"):
        assert gate.prompt_authorises(prompt, "commit") is True, prompt
    for prompt in ("push", "сделай пуш", "залей на github"):
        assert gate.prompt_authorises(prompt, "push") is True, prompt
    assert gate.prompt_authorises("не коммить", "commit") is False
    assert gate.prompt_authorises("не мержи", "commit") is False
    assert gate.prompt_authorises("не пушь", "push") is False


def test_a_brief_asking_for_a_merge_in_russian_still_authorises_nothing(gate):
    """The marker is checked before any word is looked at, and must stay so."""
    brief = f"{gate._BRIEF_MARKER}\n\nСливай ветку в main и удаляй ярд."
    assert gate.prompt_authorises(brief, "commit") is False, (
        "a marked brief authorised a commit through the new vocabulary, so "
        "these words reopened the hole the marker closes")
    assert gate.prompt_authorises(brief, "push") is False


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
