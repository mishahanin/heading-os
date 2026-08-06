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
