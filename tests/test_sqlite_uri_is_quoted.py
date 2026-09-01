"""SQLite `file:` URIs must be built by quoting, never by f-string paste.

Six call sites wrote the same thing:

    sqlite3.connect(f"file:{path}?mode=ro", uri=True)

An f-string pastes a filesystem path into a URI with no quoting. A `?` in the
path starts the query string early, so the filename is truncated and the rest is
read as connection parameters; `#` does the same via the fragment; a space is
not legal in a URI; a Windows path carries a colon and backslashes the grammar
does not accept.

Found by the 2026-08-23 audit on `.claude/hooks/memory-inject.py`, where the
failure is silent: the connect error is caught and the hook calls `_emit("")`,
so a data root under a directory with `?` in its name turns memory injection off
forever with no diagnostic. Sweeping the tree found five more.

`scripts/utils/sqlite_uri.read_only_uri()` is now the one way. This file pins
both halves: the helper quotes correctly, and no caller has gone back to the
f-string.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.sqlite_uri import read_only_uri  # noqa: E402

# The shape that was wrong, in any file under these trees, in any of the four
# ways Python spells an f-string. The pattern read `f"file:\{` until 2026-09-01,
# which is the double-quoted spelling alone: the same line written with single
# quotes, or in a triple-quoted f-string, walked straight through a detector
# whose whole job is to refuse it. `ruff format` prefers double quotes, so the
# blind spot needed only one hand-written line to become a live gap. Measured
# when widened: still zero offenders in either tree, so nothing was hiding
# behind it today.
_PASTE = re.compile(r"""f(?:'''|\"\"\"|['"])file:\{[^}]+\}""")
_SCAN_DIRS = [ROOT / "scripts", ROOT / ".claude" / "hooks"]
_SKIP = {"__pycache__", ".venv"}
# A floor PER TREE, not over the union. Measured 2026-09-01: `scripts/` holds
# 386 files and `.claude/hooks/` holds 17, so the union floor of 250 below was
# carried by `scripts/` alone and the hooks tree could have contributed nothing.
# `.claude/hooks/memory-inject.py` is where this defect was FOUND, so that is
# precisely the tree that must not fall out of the walk unnoticed.
_MIN_PER_DIR = {"scripts": 250, "hooks": 10}


@pytest.mark.parametrize("name", [
    "plain.db",
    "has?question.db",
    "has#hash.db",
    "has space.db",
    "has%percent.db",
    "has'quote.db",
    # A directory or file name that is not ASCII is ordinary, and `as_uri()`
    # percent-encodes its UTF-8 bytes. Nothing here had a non-ASCII case.
    "имя-файла.db",
    "café ☕.db",
])
def test_a_real_database_opens_through_the_helper(tmp_path, name):
    """Not a string comparison: create the file, open it, read from it."""
    db = tmp_path / name
    with sqlite3.connect(str(db)) as setup:
        setup.execute("CREATE TABLE t (v TEXT)")
        setup.execute("INSERT INTO t VALUES ('ok')")
    conn = sqlite3.connect(read_only_uri(db), uri=True)
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_the_old_shape_really_fails_on_a_question_mark(tmp_path):
    """Pins the defect, so the helper is not judged on its own say-so."""
    db = tmp_path / "has?question.db"
    with sqlite3.connect(str(db)) as setup:
        setup.execute("CREATE TABLE t (v TEXT)")
    with pytest.raises(sqlite3.Error):
        sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("SELECT 1 FROM t")


def test_the_uri_is_actually_read_only(tmp_path):
    """`mode=ro` must survive the rewrite; losing it would make every caller
    silently writable."""
    db = tmp_path / "ro.db"
    with sqlite3.connect(str(db)) as setup:
        setup.execute("CREATE TABLE t (v TEXT)")
    conn = sqlite3.connect(read_only_uri(db), uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES ('nope')")
    finally:
        conn.close()


def test_a_relative_path_is_made_absolute():
    """`Path.as_uri()` raises on a relative path. Callers pass what they have."""
    uri = read_only_uri("relative/x.db")
    assert uri.startswith("file:///") and uri.endswith("?mode=ro")


def test_no_caller_pastes_a_path_into_a_file_uri():
    offenders = []
    inspected = 0
    per_dir = {}
    for base in _SCAN_DIRS:
        seen = 0
        for path in base.rglob("*.py"):
            if any(part in _SKIP for part in path.parts):
                continue
            inspected += 1
            seen += 1
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                if _PASTE.search(line) and "sqlite_uri" not in str(path):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
        per_dir[base.name] = seen
    # 388 files reached the read on 2026-08-26. If the `_SKIP` part-match drifted
    # true for every path (a directory name every file sits under lands in the
    # set), no file would be read and `offenders` would be empty for free.
    assert inspected >= 250, f"only inspected {inspected} files"
    assert per_dir.keys() == _MIN_PER_DIR.keys(), (
        f"the scanned trees changed to {sorted(per_dir)}; give each one a floor"
    )
    short = {d: n for d, n in per_dir.items() if n < _MIN_PER_DIR[d]}
    assert not short, (
        f"these trees contributed fewer files than their floor: {short} "
        f"(floors {_MIN_PER_DIR})"
    )
    assert offenders == [], (
        f"these build a SQLite file: URI by pasting an unquoted path: "
        f"{offenders}. Use scripts.utils.sqlite_uri.read_only_uri()."
    )


@pytest.mark.parametrize("line", [
    'conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)',
    "conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)",
    'conn = sqlite3.connect(f"""file:{db}?mode=ro""", uri=True)',
    "uri = f'file:{self._path}?mode=ro'",
])
def test_the_scanner_recognises_every_spelling_of_the_defect(line):
    """One quoting style is not the defect; pasting a path into a URI is.

    The single-quoted line is the realistic near-miss, not an invented one: it
    is the same statement a hand edit produces before a formatter touches it,
    and the pattern that stood here until 2026-09-01 did not see it.
    """
    assert _PASTE.search(line)


@pytest.mark.parametrize("line", [
    "conn = sqlite3.connect(read_only_uri(db), uri=True)",
    'conn = sqlite3.connect(read_only_uri(db), uri=True)  # f"file:" was here',
    'print(f"file: {name}")',
    'raise ValueError(f"no such file: {path}")',
])
def test_the_scanner_spares_the_fixed_shape_and_its_near_misses(line):
    """A detector that fires on ordinary prose gets switched off by whoever
    inherits it, so the negatives are the near misses rather than a straw man:
    the helper call, a comment quoting the old shape, and two ordinary messages
    that contain the words "file:" and a brace."""
    assert not _PASTE.search(line)


def test_the_scanner_walk_is_not_vacuous():
    scanned = sum(1 for base in _SCAN_DIRS for _ in base.rglob("*.py"))
    assert scanned > 50, f"only scanned {scanned} files"


@pytest.mark.parametrize("module,attr", [
    ("scripts.utils.firefox_cookies", None),
    ("scripts.utils.chromium_cookies", None),
    ("scripts.utils.symbol_source", None),
])
def test_the_rewritten_modules_still_import(module, attr):
    """A bulk rewrite that inserts an import in the wrong place breaks at import
    time, not at the call site."""
    __import__(module)
