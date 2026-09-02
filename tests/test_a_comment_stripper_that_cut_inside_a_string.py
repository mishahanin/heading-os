#!/usr/bin/env python3
"""The stripper twelve source sweeps trusted, and the hole it left open.

Every one of them removed comments with `ln.split("#", 1)[0]`, which does not
remove comments: it truncates at the first `#` on the line wherever it sits. A
`#` inside a string literal cuts the line there, and everything after it leaves
the text the assertion then searches.

Two consequences, both silent. A NEGATIVE assertion ("this call does not appear
in the source") passes over a line that still holds the call. A COUNT assertion
counts over mangled source. The first is the dangerous one: an absence
assertion that can be defeated without failing lets the regression it guards
come back with the suite green.

`tests/code_only.py` asks `tokenize`, which is the tokenizer the interpreter's
own front end uses, so it knows which `#` opens a comment. This file is the
control on it, and the first six cases are the ones that defeated the old
stripper.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.code_only import (  # noqa: E402
    SourceNotTokenizable,
    code_lines,
    code_of,
    strip_comments,
    strip_comments_and_strings,
)


def _truncating(source: str) -> str:
    """The stripper this module replaced, kept so the hole stays demonstrable.

    A fix whose "before" is only described in prose stops being checkable the
    moment someone doubts the description.
    """
    return "\n".join(ln.split("#", 1)[0] for ln in source.splitlines())


# ============================================================
# 1 - a `#` inside a string, in every literal form Python has
# ============================================================

# (label, source line, the text a negative assertion would look for)
HASH_IN_A_STRING = [
    ("single-quoted", "log('pass #2'); shutil.move(src, dst)\n", "shutil.move("),
    ("double-quoted", 'log("pass #2"); shutil.move(src, dst)\n', "shutil.move("),
    ("triple-quoted", 'log("""pass #2"""); shutil.move(src, dst)\n', "shutil.move("),
    ("f-string", 'log(f"pass #{n}"); shutil.move(src, dst)\n', "shutil.move("),
    ("raw string", 'log(r"^#\\d+"); shutil.move(src, dst)\n', "shutil.move("),
    ("dict value", 'H = {"X-Run": "run#1", "owner": "ada-lovelace"}\n', "ada-lovelace"),
]


@pytest.mark.parametrize("label,source,needle",
                         HASH_IN_A_STRING, ids=[c[0] for c in HASH_IN_A_STRING])
def test_a_hash_inside_a_string_no_longer_hides_the_rest_of_the_line(
        label, source, needle):
    assert needle in source, "the case is stale: the needle is not in the source"
    assert needle not in _truncating(source), (
        f"the {label} case no longer defeats the old stripper, so it is not "
        f"testing what it was written for")
    assert needle in strip_comments(source), (
        f"a `#` in a {label} literal still swallows the rest of the line")


def test_the_whole_line_survives_a_hash_in_a_string_byte_for_byte():
    """Not just the needle: nothing on the line is dropped."""
    source = 'log("pass #2"); shutil.move(src, dst)\n'
    assert strip_comments(source) == 'log("pass #2"); shutil.move(src, dst)'
    assert _truncating(source) == 'log("pass '


# ============================================================
# 2 - and comments are still removed, which is the point of the call
# ============================================================

def test_a_trailing_comment_is_removed_and_the_code_before_it_is_kept():
    source = "x = 1  # raise last_error\n"
    assert strip_comments(source) == "x = 1  "
    assert "raise last_error" not in strip_comments(source)


def test_a_full_line_comment_leaves_its_indentation_and_nothing_else():
    """The blank-but-present line is what keeps `"\\n    main()\\n"` searchable:
    a stripper that DELETED comment lines would splice unrelated lines together
    and invent adjacencies that are not in the file."""
    source = "def f():\n    # main()\n    return 1\n"
    assert strip_comments(source) == "def f():\n    \n    return 1"


def test_a_hash_that_opens_a_comment_after_a_string_is_still_a_comment():
    """Both on one line, in that order. The tokenizer has to get past the
    string to find the real comment."""
    source = 'TAG = "run#1"  # shutil.move(src, dst)\n'
    out = strip_comments(source)
    assert out == 'TAG = "run#1"  '
    assert "shutil.move(" not in out


def test_a_hash_inside_a_multiline_string_does_not_end_the_string():
    source = 'DOC = """\nheading #1\n"""\nx = 2  # gone\n'
    out = strip_comments(source)
    assert "heading #1" in out
    assert "gone" not in out


def test_a_shebang_is_a_comment_like_any_other():
    assert strip_comments("#!/usr/bin/env python3\nx = 1\n") == "\nx = 1"


# ============================================================
# 3 - line alignment, because two callers index by line number
# ============================================================

def test_one_output_line_per_input_line():
    source = "a = 1\n# two\nb = 2  # three\n\nc = 3\n"
    lines = code_lines(source)
    assert len(lines) == len(source.splitlines())
    assert lines == ["a = 1", "", "b = 2  ", "", "c = 3"]


def test_the_line_number_of_a_hit_is_the_line_number_in_the_file():
    source = 'x = "#"\ny = 2\ntarget()\n'
    lines = code_lines(source)
    assert [i for i, ln in enumerate(lines, start=1) if "target()" in ln] == [3]


# ============================================================
# 4 - it degrades by raising, never by returning the input
# ============================================================

@pytest.mark.parametrize("label,source", [
    ("unterminated string", 'x = "open\n'),
    ("truncated mid-call", "def f(\n"),
    ("stray indent", "    x = 1\n"),
    ("unbalanced bracket", "x = [1, 2\n"),
])
def test_source_that_does_not_tokenize_raises_rather_than_passing_through(
        label, source):
    """The failure mode the raise exists for: a caller that got its input back
    unchanged would run its negative assertion over text that still carries the
    comment, and pass for the wrong reason. Silence here is the same hole in a
    different place."""
    with pytest.raises(SourceNotTokenizable):
        strip_comments(source, where=label)


def test_the_failure_names_the_file_it_could_not_read(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text('x = "unterminated\n', encoding="utf-8")
    with pytest.raises(SourceNotTokenizable) as excinfo:
        code_of(bad)
    assert str(bad) in str(excinfo.value)


def test_the_stripper_is_not_the_identity_function():
    """The control on the control. If `strip_comments` returned its argument,
    every parametrized case above would still pass except the trailing-comment
    ones, and every sweep in the suite would be a whole-file substring search
    again."""
    source = "x = 1  # a comment\n"
    assert strip_comments(source) != source


# ============================================================
# 5 - the string-blanking form, used where prose must not answer a code search
# ============================================================

def test_strings_are_blanked_to_spaces_and_offsets_are_kept():
    source = 'root = Path(__file__).parent\nname = "parent.parent.parent"\n'
    out = strip_comments_and_strings(source)
    assert "parent.parent.parent" not in out
    assert "root = Path(__file__).parent" in out
    assert ([len(ln) for ln in out.splitlines()]
            == [len(ln) for ln in source.splitlines()])


def test_a_bytes_literal_is_blanked_too():
    """`ast.Constant` string spans, which this replaced, walked straight past a
    bytes literal and left its contents answering the search."""
    out = strip_comments_and_strings('marker = b"parents[2]"\n')
    assert "parents[2]" not in out


def test_a_docstring_is_blanked_and_the_code_under_it_is_not():
    source = ('def f():\n'
              '    """Walks to parents[2] by hand."""\n'
              '    return Path(__file__).parents[2]\n')
    out = strip_comments_and_strings(source)
    assert out.count("parents[2]") == 1
    assert "return Path(__file__).parents[2]" in out
