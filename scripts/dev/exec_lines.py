#!/usr/bin/env python3
"""Executable-line counter for Python source.

Usage:
    python scripts/dev/exec_lines.py scripts/utils/atomic.py
    python scripts/dev/exec_lines.py -            # read source from stdin
    from scripts.dev.exec_lines import exec_lines  # snake_case: imported by tests

An executable line is a physical line that is not blank, not a comment-only
line (detected with `tokenize`, so a `#` inside a string literal is not a
comment), and not part of a docstring statement (detected with `ast`, so a
multi-line string used as a VALUE is not excluded). This is the definition the
five prior measurement rounds used; changing it makes the numbers
incommensurable with them.
"""
import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def count_lines(source: str) -> tuple[int, int]:
    """Return (executable_lines, physical_lines) for *source*.

    Raises SyntaxError if *source* is not parseable Python.
    """
    lines = source.splitlines()
    excluded = {n for n, text in enumerate(lines, 1) if not text.strip()}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and not tok.line[: tok.start[1]].strip():
                excluded.add(tok.start[0])
    except (tokenize.TokenError, IndentationError) as exc:
        raise SyntaxError(f"tokenize failed: {exc}") from exc
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, _DOCSTRING_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if isinstance(first.value.value, str):
            excluded.update(range(first.lineno, first.end_lineno + 1))
    return len(lines) - len(excluded), len(lines)


def exec_lines(path: Path) -> int:
    """Return the executable-line count of the Python file at *path*."""
    return count_lines(Path(path).read_text(encoding="utf-8"))[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count executable lines of Python source.")
    parser.add_argument("paths", nargs="+", help="source files; '-' reads stdin")
    args = parser.parse_args(argv)
    status = 0
    for name in args.paths:
        try:
            source = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
            executable, physical = count_lines(source)
        except (OSError, SyntaxError, ValueError) as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = 1
            continue
        print(f"{name} total_exec={executable} total_physical={physical}")
    return status


if __name__ == "__main__":
    sys.exit(main())
