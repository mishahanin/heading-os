"""A cache keyed on content hash keeps answering after the question changes.

`load_facts` stores one row per Python file keyed on the sha256 of its bytes, so
an unchanged file is never re-parsed. That is what makes day mode 0.31s warm
against 6.6s cold, and it is also a trap: when `Facts` gained a `scoped` field on
2026-09-05, every row already in the database matched its file's hash and would
have kept answering "no subtree sweeps" forever. Only editing a file would have
fixed it, so the tree would have healed one file at a time, invisibly, over
months. `.claude/rules` names the shape: a cache keyed on unchanged input makes a
stale answer permanent.

The fix is the table NAME carrying the schema version, `facts_v2`. A rename
cannot half-apply the way a migration can, and the old table is dropped so a
reader of this database is never looking at two answers to the same question.

MEASURED 2026-09-05 before the rename: writing the pre-`scoped` payload shape
into `facts` and then calling `load_facts` returned rows with no `scoped` field
for every unmodified file in the tree.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.day_mode import CACHE_REL, FACTS_TABLE, load_facts  # noqa: E402

LEGACY_ROW = {
    "imports": ["scripts.utils.paths"],
    "literals": ["docs/index.html"],
    "sweeps": [],
}

SOURCE = (
    "from pathlib import Path\n"
    "ROOT = Path(__file__).resolve().parent.parent\n"
    "DOCS = ROOT / 'docs'\n"
    "def test_pages():\n"
    "    assert list(DOCS.glob('*.html'))\n"
)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pages.py").write_text(SOURCE, encoding="utf-8")
    return tmp_path


def test_a_legacy_row_is_never_read_as_an_answer(tmp_path):
    """The regression. A pre-schema payload must not satisfy a modern read."""
    root = _tree(tmp_path)
    cache = root / CACHE_REL
    cache.parent.mkdir(parents=True, exist_ok=True)
    text = (root / "tests" / "test_pages.py").read_bytes()
    import hashlib

    digest = hashlib.sha256(text).hexdigest()
    conn = sqlite3.connect(cache)
    conn.execute(
        "CREATE TABLE facts (path TEXT PRIMARY KEY, hash TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO facts VALUES (?, ?, ?)",
        ("tests/test_pages.py", digest, json.dumps(LEGACY_ROW)),
    )
    conn.commit()
    conn.close()

    facts, parsed = load_facts(root, ["tests/test_pages.py"])
    assert parsed == 1, "the legacy row was accepted and the file was not re-parsed"
    assert facts["tests/test_pages.py"].scoped == frozenset({("glob", "docs", "*.html")})


def test_the_legacy_table_is_dropped_rather_than_left_behind(tmp_path):
    root = _tree(tmp_path)
    cache = root / CACHE_REL
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache)
    conn.execute("CREATE TABLE facts (path TEXT PRIMARY KEY, hash TEXT, payload TEXT)")
    conn.commit()
    conn.close()

    load_facts(root, ["tests/test_pages.py"])

    conn = sqlite3.connect(cache)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "facts" not in tables
    assert FACTS_TABLE in tables


def test_the_warm_path_still_avoids_re_parsing(tmp_path):
    """The direction that pays for the cache: a second call parses nothing.

    A schema bump that quietly disabled the cache would leave every day-mode run
    and every narrowed push paying the 6.6s cold parse, and nothing else in the
    suite would notice.
    """
    root = _tree(tmp_path)
    _, first = load_facts(root, ["tests/test_pages.py"])
    _, second = load_facts(root, ["tests/test_pages.py"])
    assert first == 1
    assert second == 0

    got, third = load_facts(root, ["tests/test_pages.py"])
    assert third == 0
    assert got["tests/test_pages.py"].scoped == frozenset({("glob", "docs", "*.html")})


def test_an_edit_still_invalidates_the_row(tmp_path):
    root = _tree(tmp_path)
    load_facts(root, ["tests/test_pages.py"])
    (root / "tests" / "test_pages.py").write_text(
        SOURCE.replace("*.html", "*.md"), encoding="utf-8"
    )
    got, parsed = load_facts(root, ["tests/test_pages.py"])
    assert parsed == 1
    assert got["tests/test_pages.py"].scoped == frozenset({("glob", "docs", "*.md")})
