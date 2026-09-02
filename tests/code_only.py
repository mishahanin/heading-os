#!/usr/bin/env python3
"""Token-aware comment removal for the source sweeps in `tests/`.

Twelve sweeps in this suite asserted over a source file with the comments
"stripped" by `ln.split("#", 1)[0]`. That is not comment removal. It truncates
at a `#` ANYWHERE on the line, including one inside a string literal, so:

    subprocess.run(cmd, shell=True)   # kept, no comment on the line
    LABEL = "tag #1"; subprocess.run(cmd, shell=True)

The second line survives `split("#", 1)[0]` as `LABEL = "tag ` and every
negative assertion over it ("`shell=True` does not appear") passes while the
call sits in the file. An absence assertion that can be defeated without
failing is worse than no assertion: the regression it guards comes back with
the suite green. The count assertions built on the same slice were counting
over mangled source.

`tokenize` knows exactly which `#` opens a comment and which sits inside a
string, an f-string, or a raw string, because it is the tokenizer the
interpreter's own front end uses. Every sweep routes through here, so there is
one implementation to fix rather than twelve.

Degrading honestly is half the point. Source that does not tokenize (a syntax
error, a file truncated mid-literal) raises `SourceNotTokenizable` naming the
file. A stripper that quietly hands back the untouched text on failure re-opens
exactly the hole this module closes, because the caller's negative assertion
then runs over a line that still carries its comment and passes for the wrong
reason.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

__all__ = [
    "SourceNotTokenizable",
    "code_lines",
    "code_of",
    "strip_comments",
    "strip_comments_and_strings",
]


class SourceNotTokenizable(ValueError):
    """`source` is not tokenizable Python, so its comments cannot be found."""


# 3.12 splits an f-string into FSTRING_START / MIDDLE / END; 3.11 and earlier
# emit one STRING token for the whole literal. Ask for the names rather than
# pinning a version, so this file behaves the same on both.
def _string_token_types() -> frozenset[int]:
    types = {tokenize.STRING}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        value = getattr(tokenize, name, None)
        if value is not None:
            types.add(value)
    return frozenset(types)


_STRING_TYPES = _string_token_types()


def _tokens(source: str, where: str) -> list[tokenize.TokenInfo]:
    """Every token in `source`, or `SourceNotTokenizable` naming `where`.

    Parsed first, and that is not belt-and-braces. MEASURED on CPython 3.11.15:
    `tokenize.generate_tokens` does NOT raise on `x = "open` (it emits an
    ERRORTOKEN for the quote and tokenizes the rest of the line as code) and
    does not raise on a stray indent either, because it is a lexer and not a
    parser. On a file truncated mid-literal that is the worst possible
    behaviour: the `#` inside the broken string is then read as opening a real
    comment, the tail of the line is cut, and the caller's negative assertion
    passes over source the interpreter would refuse to load. `ast.parse`
    answers "is this Python" properly; `tokenize` then answers "where are the
    comments".

    `generate_tokens` is also lazy and raises part-way through iteration, so
    the list() has to happen inside the try.
    """
    try:
        ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise SourceNotTokenizable(
            f"{where}: not parseable Python, so its comments cannot be located "
            f"({type(exc).__name__}: {exc})") from exc
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError) as exc:
        raise SourceNotTokenizable(
            f"{where}: not tokenizable Python, so its comments cannot be "
            f"located ({type(exc).__name__}: {exc})") from exc


def _blank(lines: list[str], start: tuple[int, int], end: tuple[int, int]) -> None:
    """Overwrite the span with spaces, in place, preserving every offset.

    Length-preserving on purpose: the comment cuts are applied afterwards from
    columns measured against the ORIGINAL source, and a blank that shortened a
    line would move them.
    """
    (start_row, start_col), (end_row, end_col) = start, end
    for i in range(start_row - 1, min(end_row, len(lines))):
        line = lines[i]
        lo = start_col if i == start_row - 1 else 0
        hi = end_col if i == end_row - 1 else len(line)
        lo, hi = min(lo, len(line)), min(hi, len(line))
        if hi > lo:
            lines[i] = line[:lo] + " " * (hi - lo) + line[hi:]


def code_lines(source: str, *, where: str = "<source>") -> list[str]:
    """`source` split into lines, each with its comment removed.

    One entry per input line, so a caller that reports `enumerate(..., 1)` as a
    line number still reports the right one. The text before the `#` is kept
    byte for byte, trailing whitespace included, which is what the truncating
    strippers this replaces produced on a line that genuinely ended in a
    comment.
    """
    lines = source.splitlines()
    for tok in _tokens(source, where):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            if row - 1 < len(lines):
                lines[row - 1] = lines[row - 1][:col]
    return lines


def strip_comments(source: str, *, where: str = "<source>") -> str:
    """`source` with every comment removed and every string literal intact."""
    return "\n".join(code_lines(source, where=where))


def code_of(path: Path) -> str:
    """`strip_comments` over a file, with the path in any failure message."""
    return strip_comments(path.read_text(encoding="utf-8"), where=str(path))


def strip_comments_and_strings(source: str, *, where: str = "<source>") -> str:
    """`source` with every comment removed and every string blanked to spaces.

    For sweeps whose whole finding is that prose satisfied a search over code:
    what is left is what the interpreter would execute. Blanking rather than
    deleting keeps line and column offsets, so a hit still reports where it is.
    """
    lines = source.splitlines()
    cuts: list[tuple[int, int]] = []
    for tok in _tokens(source, where):
        if tok.type == tokenize.COMMENT:
            cuts.append(tok.start)
        elif tok.type in _STRING_TYPES:
            _blank(lines, tok.start, tok.end)
    for row, col in cuts:
        if row - 1 < len(lines):
            lines[row - 1] = lines[row - 1][:col]
    return "\n".join(lines)
