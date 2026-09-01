#!/usr/bin/env python3
"""The symbol source: CodeGraph says WHERE, the source file says WHAT.

CodeGraph holds 9,546 functions, methods, classes and routes with exact locations
and 46,558 edges, and its only text search is FTS5 -- exact tokens. This module
turns those nodes into rows our embedder can index, so "the check that refuses an
ungated send" finds the code without knowing a single word in it.

The division of labour is the whole design, and it was forced by a measurement.
CodeGraph's `docstring` column reports 12.4% coverage; parsing the same tree with
`ast` reports **52.0%**. The gap is a parser defect: CodeGraph attributes the
section banner ABOVE a symbol (`# =====`) instead of the string inside it -- 582 of
its 1,180 "docstrings" are banners. So identity, location and edges come from
CodeGraph, and every character of TEXT comes from the file on disk.

Two more properties carry risk and are tested rather than assumed: the air gap must
hold for source files exactly as it does for commits, and a node whose file has
moved or shrunk since the index was built must be skipped, not embedded from a
wrong line range.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.symbol_source import (  # noqa: E402
    EMBEDDABLE_KINDS,
    MAX_BODY_CHARS,
    extract_docstring,
    iter_symbols,
)


def _graph(tmp_path: Path, rows: list[tuple]) -> Path:
    """A minimal stand-in for .codegraph/codegraph.db with just what we read."""
    db = tmp_path / "cg.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT, "
        "file_path TEXT, language TEXT, start_line INT, end_line INT, "
        "docstring TEXT, signature TEXT)"
    )
    conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _src(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# --- the docstring boundary ------------------------------------------------

def test_the_real_docstring_is_read_from_source_not_from_the_graph(tmp_path):
    """CodeGraph would hand back the banner. We must not use it."""
    _src(tmp_path, "m.py",
         "# ============================================================\n"
         "# Schema & Path Resolution\n"
         "# ============================================================\n"
         "class SchemaError(Exception):\n"
         '    """Raised when workspace identity is malformed."""\n')
    db = _graph(tmp_path, [("n1", "class", "SchemaError", "m.SchemaError", "m.py",
                            "python", 4, 5, "# ===== Schema & Path Resolution =====",
                            "class SchemaError(Exception)")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert "Raised when workspace identity is malformed." in row["body"]
    assert "=====" not in row["body"]


def test_the_docstring_is_added_when_the_slice_does_not_already_carry_it(tmp_path):
    """The test above cannot tell "read from source" from "not read at all".

    Its node range covers the docstring line, so the string is inside the raw
    slice whether or not `extract_docstring` ever ran. MEASURED 2026-09-01:
    replacing `doc = extract_docstring(text, name)` with `doc = None` left the
    whole file green. Here the range covers the signature line ONLY, so the
    docstring can reach `body` by one route and no other.
    """
    _src(tmp_path, "m.py",
         "def f():\n"
         '    """The only place this sentence lives."""\n'
         "    return 2\n")
    db = _graph(tmp_path, [("n1", "function", "f", "m.f", "m.py",
                            "python", 1, 1, "", "def f()")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert "The only place this sentence lives." in row["body"]
    assert row["has_docstring"] is True


def test_a_row_says_so_when_the_symbol_has_no_docstring(tmp_path):
    """The negative half of `has_docstring`, which nothing asserted either."""
    _src(tmp_path, "m.py", "def f():\n    return 2\n")
    db = _graph(tmp_path, [("n1", "function", "f", "m.f", "m.py",
                            "python", 1, 2, "", "def f()")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert row["has_docstring"] is False


def test_the_body_is_bounded(tmp_path):
    """`MAX_BODY_CHARS` exists so one long class cannot drown its own docstring.

    Dropping the slice left this file green: no fixture was long enough for the
    bound to have anything to do.
    """
    body_lines = "\n".join(f"    x{i} = {i}" for i in range(400))
    _src(tmp_path, "m.py", f"def big():\n{body_lines}\n")
    db = _graph(tmp_path, [("n1", "function", "big", "m.big", "m.py",
                            "python", 1, 401, "", "def big()")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert len(row["body"]) == MAX_BODY_CHARS


def test_a_symbol_with_no_docstring_still_yields_a_row(tmp_path):
    """48% of symbols have none. Their source is still the best text we have."""
    _src(tmp_path, "m.py", "def add(a, b):\n    return a + b\n")
    db = _graph(tmp_path, [("n1", "function", "add", "m.add", "m.py",
                            "python", 1, 2, "", "def add(a, b)")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert "return a + b" in row["body"]


def test_extract_docstring_finds_a_method_inside_a_class(tmp_path):
    src = ('class A:\n'
           '    def m(self):\n'
           '        """The method docstring."""\n'
           '        return 1\n')
    assert extract_docstring(src, "m") == "The method docstring."


def test_extract_docstring_returns_none_when_absent(tmp_path):
    assert extract_docstring("def m():\n    return 1\n", "m") is None


def test_a_file_that_does_not_parse_degrades_to_the_raw_slice(tmp_path):
    """A syntax error must not lose the symbol; the source is still indexable."""
    _src(tmp_path, "m.py", "def broken(:\n    pass\n")
    db = _graph(tmp_path, [("n1", "function", "broken", "m.broken", "m.py",
                            "python", 1, 2, "", "def broken(")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert "broken" in row["body"]


# --- the air gap -----------------------------------------------------------

def test_a_symbol_in_an_air_gapped_file_is_never_indexed(tmp_path):
    _src(tmp_path, "chronicle/personal/p.py", "def secret():\n    return 1\n")
    db = _graph(tmp_path, [("n1", "function", "secret", "p.secret",
                            "chronicle/personal/p.py", "python", 1, 2, "", "def secret()")])
    assert list(iter_symbols(db, tmp_path)) == []


def test_the_vault_prefix_is_denied_for_symbols_too(tmp_path):
    _src(tmp_path, "_secure/v.py", "def vault():\n    return 1\n")
    db = _graph(tmp_path, [("n1", "function", "vault", "v.vault",
                            "_secure/v.py", "python", 1, 2, "", "def vault()")])
    assert list(iter_symbols(db, tmp_path)) == []


def test_a_refusal_is_counted_back_to_the_caller(tmp_path):
    """The docstring's own promise: the refusal happens in here, so the caller
    cannot count it.

    A caller that reports "0 withheld" over content the air gap actually
    dropped is claiming coverage it does not have. Deleting the counter left
    this file green until 2026-09-01, so the promise was prose only. The
    control half is the second call: `denied` must stay absent when nothing was
    refused, or the assertion above passes on a counter that always fires.
    """
    _src(tmp_path, "chronicle/personal/p.py", "def secret():\n    return 1\n")
    _src(tmp_path, "m.py", "def open_one():\n    return 1\n")
    db = _graph(tmp_path, [
        ("n1", "function", "secret", "p.secret", "chronicle/personal/p.py",
         "python", 1, 2, "", "def secret()"),
        ("n2", "function", "open_one", "m.open_one", "m.py",
         "python", 1, 2, "", "def open_one()"),
    ])
    stats: dict = {}
    rows = list(iter_symbols(db, tmp_path, stats=stats))
    assert [r["title"] for r in rows] == ["m.open_one"]
    assert stats["denied"] == 1


def test_nothing_is_counted_when_nothing_was_refused(tmp_path):
    """The control for the counter above: it must not fire on a clean walk."""
    _src(tmp_path, "m.py", "def open_one():\n    return 1\n")
    db = _graph(tmp_path, [("n1", "function", "open_one", "m.open_one", "m.py",
                            "python", 1, 2, "", "def open_one()")])
    stats: dict = {}
    assert len(list(iter_symbols(db, tmp_path, stats=stats))) == 1
    assert stats.get("denied", 0) == 0


# --- staleness -------------------------------------------------------------

def test_a_node_whose_file_is_gone_is_skipped_not_guessed(tmp_path):
    db = _graph(tmp_path, [("n1", "function", "ghost", "g.ghost", "gone.py",
                            "python", 1, 2, "", "def ghost()")])
    assert list(iter_symbols(db, tmp_path)) == []


def test_a_node_pointing_past_the_end_of_its_file_is_skipped(tmp_path):
    """The index lags edits. Embedding a wrong slice is worse than a gap."""
    _src(tmp_path, "m.py", "def a():\n    return 1\n")
    db = _graph(tmp_path, [("n1", "function", "a", "m.a", "m.py",
                            "python", 900, 950, "", "def a()")])
    assert list(iter_symbols(db, tmp_path)) == []


def test_a_node_whose_start_fits_but_whose_end_does_not_is_skipped(tmp_path):
    """The exact shape the end-line check was added for, on 2026-08-26.

    The test above moves the START out of range too, so it is caught by either
    half of the condition. Dropping `start <= end <= len(lines)` left this file
    green: a node recorded as `m.py:1-40` whose file is now three lines passed
    the start test, the old `min(end, len(lines))` clamped the slice to the
    whole file, and the row went out labelled with a range that does not exist.
    """
    _src(tmp_path, "m.py", "def a():\n    return 1\n\n")
    db = _graph(tmp_path, [("n1", "function", "a", "m.a", "m.py",
                            "python", 1, 40, "", "def a()")])
    assert list(iter_symbols(db, tmp_path)) == []


def test_a_node_starting_below_line_one_is_skipped(tmp_path):
    """The other half of the same condition, and it is not decoration.

    `1 <= start` is what stops a zero or negative start reaching the slice.
    `lines[0 - 1:end]` is `lines[-1:end]`, which is a legal Python slice and
    almost always the WRONG lines, so the row would be labelled `m.py:0-2` over
    content from the end of the file.
    """
    _src(tmp_path, "m.py", "def a():\n    return 1\n")
    db = _graph(tmp_path, [("n1", "function", "a", "m.a", "m.py",
                            "python", 0, 2, "", "def a()")])
    assert list(iter_symbols(db, tmp_path)) == []


# --- selection and shape ---------------------------------------------------

def test_imports_and_variables_are_not_embedded(tmp_path):
    """A vector for `import json` retrieves nothing and dilutes the store."""
    _src(tmp_path, "m.py", "import json\nX = 1\ndef f():\n    return 2\n")
    db = _graph(tmp_path, [
        ("n1", "import", "json", "m.json", "m.py", "python", 1, 1, "", ""),
        ("n2", "variable", "X", "m.X", "m.py", "python", 2, 2, "", ""),
        ("n3", "function", "f", "m.f", "m.py", "python", 3, 4, "", "def f()"),
    ])
    names = [r["title"] for r in iter_symbols(db, tmp_path)]
    assert names == ["m.f"]
    assert "import" not in EMBEDDABLE_KINDS


def test_a_row_carries_the_join_key_back_to_codegraph(tmp_path):
    """The whole point: a vector hit must be explorable in the graph."""
    _src(tmp_path, "m.py", "def f():\n    return 2\n")
    db = _graph(tmp_path, [("n1", "function", "f", "m.f", "m.py",
                            "python", 1, 2, "", "def f()")])
    (row,) = list(iter_symbols(db, tmp_path))
    assert row["node_id"] == "n1"
    assert row["id"] == "symbol:n1"
    assert row["path"] == "m.py:1-2"
    assert row["title"] == "m.f"
    assert row["ntype"] == "function"
    assert row["mtime"] > 0


def test_a_missing_graph_raises_plainly(tmp_path):
    with pytest.raises(ValueError, match="CodeGraph index not found"):
        list(iter_symbols(tmp_path / "nope.db", tmp_path))
