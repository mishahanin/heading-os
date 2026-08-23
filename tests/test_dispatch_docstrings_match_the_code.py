"""A guard's docstring must state the coverage the guard actually has.

`.claude/rules/scope-claims.md` exists because a tool that over-claims is
trusted, acted on, and quoted back later as established fact. Two docstrings in
the PreToolUse dispatcher were doing exactly that. Found by the 2026-08-23 audit
and confirmed by reading the constants beside them:

  * `check_protect_docs` said "The 8 shared documentation files in docs/ are
    auto-synced". `SYNCED_FILES` holds 6, and `.claude/hooks/sync-docs.py` says
    6 in its own module docstring. The wall was described as covering two files
    it does not.

  * `check_tool_budget` said "Three identical calls in a row ... advisory only".
    `TOOL_REPEAT_THRESHOLD` is 4, raised from 3 on 2026-08-20 by a change that
    left the prose behind. The detector at the bottom of the same function reads
    the constant, so the code and its own docstring disagreed about when it
    fires.

Neither is a logic bug, which is exactly why both survived. This test reads the
number out of the docstring and compares it with the value the code uses, so
the next threshold change fails here instead of quietly desynchronising the
description from the behaviour.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"
SYNC_DOCS = ROOT / ".claude" / "hooks" / "sync-docs.py"

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10}


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(DISPATCH.read_text(encoding="utf-8"))


def _docstring(tree: ast.Module, func: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            doc = ast.get_docstring(node)
            assert doc, f"{func} has no docstring"
            return doc
    raise AssertionError(f"{func} not found in {DISPATCH}")


def _constant(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    raise AssertionError(f"{name} not found in {DISPATCH}")


# --- check_protect_docs -------------------------------------------------------

def test_the_synced_file_count_in_the_docstring_is_the_real_count(tree):
    node = _constant(tree, "SYNCED_FILES")
    assert isinstance(node, ast.Set), "SYNCED_FILES is no longer a set literal"
    real = len(node.elts)
    doc = _docstring(tree, "check_protect_docs")
    stated = re.search(r"The (\d+|\w+) shared documentation files", doc)
    assert stated, f"check_protect_docs no longer states a count: {doc!r}"
    token = stated.group(1)
    value = int(token) if token.isdigit() else _WORDS.get(token.lower())
    assert value == real, (
        f"check_protect_docs claims {token} synced files; SYNCED_FILES holds {real}"
    )


def test_the_two_hooks_agree_on_how_many_files_are_synced(tree):
    """The wall and the syncer describe one list. They had drifted apart, and
    the syncer was the one telling the truth."""
    real = len(_constant(tree, "SYNCED_FILES").elts)
    text = SYNC_DOCS.read_text(encoding="utf-8")
    stated = re.search(r"Only syncs the (\d+) shared documentation files", text)
    assert stated, "sync-docs.py no longer states its count"
    assert int(stated.group(1)) == real, (
        f"sync-docs.py says {stated.group(1)}, _dispatch.py's SYNCED_FILES holds {real}"
    )
    # And the syncer's own SYNC_FILES set must be the same size as the wall's.
    sync_set = re.search(r"SYNC_FILES = \{(.*?)\}", text, re.S)
    assert sync_set
    assert len([x for x in sync_set.group(1).split(",") if x.strip()]) == real


# --- check_tool_budget --------------------------------------------------------

def test_the_repeat_threshold_in_the_docstring_is_the_constant(tree):
    node = _constant(tree, "TOOL_REPEAT_THRESHOLD")
    assert isinstance(node, ast.Constant), "TOOL_REPEAT_THRESHOLD is not a literal"
    real = node.value
    doc = _docstring(tree, "check_tool_budget")

    # The sentence that describes the repeat rule, from the phrase back to the
    # previous sentence break and forward to the next one.
    hit = re.search(r"identical calls", doc)
    assert hit, f"check_tool_budget no longer describes the repeat rule: {doc!r}"
    start = max(doc.rfind(". ", 0, hit.start()) + 1, 0)
    end = doc.find(". ", hit.end())
    sentence = doc[start:end if end != -1 else len(doc)]

    stale_words = [w for w, n in _WORDS.items()
                   if n != real and re.search(rf"\b{w}\b", sentence, re.I)]
    assert not stale_words, (
        f"the repeat sentence spells a threshold of {stale_words}; "
        f"TOOL_REPEAT_THRESHOLD is {real}. Sentence: {sentence!r}"
    )

    # Digits too. Naming the constant is good practice but it is not a value,
    # so a "which is 4" left behind after a bump still misleads. The first
    # version of this test accepted the constant's NAME as proof and passed a
    # mutation from 4 to 6 with "which is 4" still in the prose.
    digits = {int(d) for d in re.findall(r"\b(\d+)\b", sentence)}
    wrong = sorted(d for d in digits if d != real)
    assert not wrong, (
        f"the repeat sentence states {wrong}; TOOL_REPEAT_THRESHOLD is {real}. "
        f"Sentence: {sentence!r}"
    )
    assert digits or "TOOL_REPEAT_THRESHOLD" in sentence, (
        f"the docstring states no threshold at all; the constant is {real}"
    )


def test_the_detector_still_reads_the_constant(tree):
    """If the comparison stopped using TOOL_REPEAT_THRESHOLD, the test above
    would be checking the docstring against an unused number."""
    src = DISPATCH.read_text(encoding="utf-8")
    body = src[src.index("def check_tool_budget"):]
    body = body[:body.index("\ndef ", 1)]
    assert "TOOL_REPEAT_THRESHOLD" in body, (
        "check_tool_budget no longer references its own threshold constant"
    )
