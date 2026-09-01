#!/usr/bin/env python3
"""Tests for the executable-line counter (scripts/dev/exec_lines.py).

The counter is the measuring instrument the line-budget steps of the Canopus
plan are evaluated against, so its definition of "executable" is pinned here:
physical lines, minus blank lines, minus comment-only lines, minus the line
ranges of docstring statements. The two subtle behaviours — a `#` inside a
string literal is not a comment, and a multi-line string used as a VALUE is not
a docstring — get their own cases because a naive regex counter gets both wrong
and the numbers would stop being commensurable with the prior rounds.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dev.exec_lines import count_lines, exec_lines, main

ROOT = Path(__file__).resolve().parents[1]
COUNTER = ROOT / "scripts" / "dev" / "exec_lines.py"

# 10 physical lines: a 3-line docstring, 2 comment-only lines, 2 blank lines,
# and exactly 3 executable statements.
FIXTURE = '''"""Fixture docstring, first line.

Third line closes the docstring."""
# a comment-only line
import os

VALUE = 1
# another comment-only line

print(VALUE, os.name)
'''


def test_fixture_counts_three_executable_lines(tmp_path):
    path = tmp_path / "fixture.py"
    path.write_text(FIXTURE, encoding="utf-8")
    assert exec_lines(path) == 3
    assert count_lines(FIXTURE) == (3, 10)


def test_hash_inside_string_literal_is_not_a_comment():
    source = 'URL = "https://example.invalid/page#anchor"\nLABEL = "# not a comment"\n'
    assert count_lines(source) == (2, 2)


def test_multiline_string_as_value_is_not_a_docstring():
    source = 'TEXT = """line one\nline two\nline three"""\n'
    assert count_lines(source) == (3, 3)


def test_docstrings_in_class_and_function_are_excluded():
    source = (
        "class Thing:\n"
        '    """Class docstring."""\n'
        "\n"
        "    def method(self):\n"
        '        """Method docstring,\n'
        '        two lines."""\n'
        "        return 1\n"
    )
    # Executable: class Thing, def method, return 1.
    assert count_lines(source) == (3, 7)


def test_bare_string_that_is_not_the_first_statement_still_counts():
    source = "X = 1\n'''not a docstring, it is the second statement'''\n"
    assert count_lines(source) == (2, 2)


# The eight characters `str.splitlines()` cuts on and Python's tokenizer does
# not. Written as code points, never literally: `.claude/rules/hidden-chars.md`
# and the shard brief both forbid a U+2028 or U+2029 in a source file, and the
# first two would break this very file's own line numbering.
SPLITLINES_ONLY = {
    "U+2028 LINE SEPARATOR": chr(0x2028),
    "U+2029 PARAGRAPH SEPARATOR": chr(0x2029),
    "U+0085 NEXT LINE": chr(0x0085),
    "U+000B LINE TABULATION": chr(0x000B),
    "U+000C FORM FEED": chr(0x000C),
    "U+001C FILE SEPARATOR": chr(0x001C),
    "U+001D GROUP SEPARATOR": chr(0x001D),
    "U+001E RECORD SEPARATOR": chr(0x001E),
}


@pytest.mark.parametrize("label", sorted(SPLITLINES_ONLY))
def test_a_separator_inside_a_string_does_not_add_a_line(label):
    """The counter splits on "\\n" alone, and the reason is in its own comment.

    `str.splitlines()` breaks on all eight characters above; the Python
    tokenizer treats none of them as a line terminator. Counting with it
    inflated BOTH numbers and, worse, shifted every line number after the first
    occurrence out of step with the ones `tokenize` and `ast` report, so a
    blank-line or docstring exclusion landed on the wrong line.

    The counter is the instrument the Canopus line-budget steps are measured
    against, so a fix nothing binds is a number that can silently stop being
    commensurable with the prior rounds. Reverting `split("\\n")` to
    `splitlines()` left the other nine cases in this file green; measured
    2026-09-01.
    """
    ch = SPLITLINES_ONLY[label]
    source = f'x = "a{ch}b"\n'
    assert count_lines(source) == (1, 1), label


def test_a_separator_does_not_shift_the_docstring_exclusion(tmp_path):
    """The consequential half. An inflated count is a wrong number; a shifted
    line number excludes the wrong line, which is silent."""
    ch = SPLITLINES_ONLY["U+2028 LINE SEPARATOR"]
    source = (
        f'HEADER = "a{ch}b"\n'
        "def f():\n"
        '    """A docstring on the line the shift would move off."""\n'
        "    return 1\n"
    )
    # Executable: HEADER, def f, return 1. The docstring line is the exclusion.
    assert count_lines(source) == (3, 4)


def test_a_crlf_source_is_counted_as_one_line_per_record(tmp_path):
    """The normalisation the split depends on. Source arriving from stdin has
    had no universal-newline translation."""
    assert count_lines("x = 1\r\ny = 2\r\n") == (2, 2)
    assert count_lines("x = 1\ry = 2\r") == (2, 2)


def test_cli_prints_totals_for_a_file(tmp_path, capsys):
    path = tmp_path / "fixture.py"
    path.write_text(FIXTURE, encoding="utf-8")
    assert main([str(path)]) == 0
    assert "total_exec=3 total_physical=10" in capsys.readouterr().out


def test_cli_reads_stdin_on_dash():
    proc = subprocess.run(
        [sys.executable, str(COUNTER), "-"],
        input=FIXTURE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "total_exec=3 total_physical=10" in proc.stdout


@pytest.mark.parametrize("bad", ["missing.py", "broken.py"])
def test_cli_exits_non_zero_on_unreadable_or_unparseable(tmp_path, bad, capsys):
    target = tmp_path / bad
    if bad == "broken.py":
        target.write_text("def (:\n", encoding="utf-8")
    assert main([str(target)]) == 1
    assert str(target) in capsys.readouterr().err
