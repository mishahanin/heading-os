"""`--scan` reports the word count, because a rule requires it and nothing computed it.

`.claude/rules/hidden-chars.md` makes every deliverable carry the line
"Word count: X. Hidden characters: clean." Until 2026-08-23 the sanitizer
reported only the second half, so the X was supplied by whoever wrote the line
— an estimate presented inside a validation statement, which is the over-claim
`.claude/rules/scope-claims.md` exists to stop.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "sanitize-text.py"

_spec = importlib.util.spec_from_file_location("sanitize_text", _SCRIPT)
st = importlib.util.module_from_spec(_spec)
sys.modules["sanitize_text"] = st
_spec.loader.exec_module(st)


@pytest.mark.parametrize("text,expected", [
    # Two inputs, so a hardcoded number cannot satisfy both. `one two three`
    # was the only case until 2026-09-01, and it is a value at which every
    # plausible wrong implementation agrees: replacing the interpolation with
    # the literal `3`, and replacing it with `len(text.split())`, BOTH survived
    # a mutation run against this file and its six neighbours.
    ("one two three", 3),
    # Chosen so the shared definition and a bare `.split()` disagree: the bullet,
    # the pipe and the lone em-dash are separators a human does not count, so
    # `len(text.split())` answers 8 here and `word_count` answers 4. That is the
    # exact disagreement `scripts/utils/sanitize_text.word_count` exists to
    # settle, and the CLI has to be wired to the settled one.
    ("- alpha | beta — gamma delta", 4),
])
def test_scan_prints_a_word_count(text, expected):
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--scan", "--text", text],
        capture_output=True, text=True, timeout=60)
    assert f"Word count: {expected}" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_the_cli_reports_the_shared_definition_and_not_a_naive_split():
    """The wiring, stated as the disagreement rather than as a number.

    Pinned separately from the parametrized case above so the reason survives a
    later edit to the fixture: the whole point of moving `word_count` into
    `scripts/utils/sanitize_text.py` was that five counters in this workspace
    answered 11 / 12 / 15 / 15 / 17 on one sentence. A CLI that prints its own
    arithmetic re-opens that, silently, inside a validation line.
    """
    text = "- alpha | beta — gamma delta"
    assert st._word_count(text) != len(text.split()), "fixture no longer discriminates"

    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--scan", "--text", text],
        capture_output=True, text=True, timeout=60)

    assert f"Word count: {st._word_count(text)}" in (r.stdout + r.stderr)
    assert f"Word count: {len(text.split())}" not in (r.stdout + r.stderr)


def test_punctuation_only_tokens_do_not_count():
    """A markdown bullet, a table rule and an em-dash are not words."""
    assert st._word_count("- alpha | beta — gamma") == 3


def test_numbers_count_and_empty_is_zero():
    assert st._word_count("42 apples") == 2
    assert st._word_count("") == 0
    assert st._word_count("|  --- | --- |") == 0


def test_the_count_is_close_to_wc_w_on_prose():
    """Sanity against the system tool: prose-only, so the two should agree."""
    prose = "The quick brown fox jumps over the lazy dog. It does so twice.\n" * 20
    assert st._word_count(prose) == len(prose.split())
