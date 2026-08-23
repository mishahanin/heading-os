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

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "sanitize-text.py"

_spec = importlib.util.spec_from_file_location("sanitize_text", _SCRIPT)
st = importlib.util.module_from_spec(_spec)
sys.modules["sanitize_text"] = st
_spec.loader.exec_module(st)


def test_scan_prints_a_word_count():
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--scan", "--text", "one two three"],
        capture_output=True, text=True, timeout=60)
    assert "Word count: 3" in (r.stdout + r.stderr)


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
