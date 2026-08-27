#!/usr/bin/env python3
"""Code symbols as index rows: CodeGraph says WHERE, the file on disk says WHAT.

`.codegraph/codegraph.db` holds every function, method, class and route in the
engine with exact line ranges, plus 46,558 edges. Its only text search is FTS5 --
exact tokens -- so "the check that refuses an ungated send" finds nothing unless
you already know a word in the file. This module embeds the code itself, and every
row carries its CodeGraph `node_id` so a vector hit feeds straight back into
`codegraph explore` for callers and blast radius. The two halves compose: search
finds the entry point, the graph explains the consequences.

Design spec: `docs/superpowers/specs/2026-08-21-semantic-index-commits-and-symbols-design.md`
Contract: `tests/test_symbol_source.py`

**Text comes from the file, never from CodeGraph's `docstring` column.** That
column reports 12.4% coverage; parsing the same tree with `ast` reports 52.0%. The
gap is a parser defect -- CodeGraph attributes the section banner ABOVE a symbol
(`# =====`) instead of the string inside it, and 582 of its 1,180 "docstrings" are
banners. `SchemaError` at `scripts/apply-wizard-answers.py:33` carries a real
docstring on the next line and CodeGraph returns the banner three lines above. So
CodeGraph supplies identity, location and edges, which is what it is good at, and
the source supplies text. Not a complaint about the tool -- a boundary.

**Staleness is a skip, never a guess.** The index lags edits by about a second, and
a file can shrink between index and build. A node whose range no longer fits its
file is dropped rather than embedded from whatever now sits at those lines: a
confident vector pointing at the wrong code is worse than a missing one.
"""
from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from scripts.utils.air_gap import is_denied
from scripts.utils.sqlite_uri import read_only_uri

# Imports, variables and constants are excluded: a vector for `import json`
# retrieves nothing and dilutes every neighbour it sits beside.
EMBEDDABLE_KINDS = ("function", "method", "class", "route")

# Bound the text per symbol. bge-m3 truncates long inputs anyway, and a
# thousand-line class body would drown its own docstring in boilerplate.
MAX_BODY_CHARS = 2000


def extract_docstring(source: str, name: str) -> str | None:
    """The real docstring of `name`, read from parsed source. None if absent.

    Walks the whole tree rather than only module level, so a method inside a
    class is found. On a name collision the first match wins; the caller already
    has the line range to disambiguate if it ever matters.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == name):
            doc = ast.get_docstring(node)
            return doc.strip() if doc and doc.strip() else None
    return None


def iter_symbols(
    graph_db: Path,
    root: Path,
    *,
    deny_prefixes: tuple = (),
    deny_segments: tuple = (),
    stats: dict | None = None,
) -> Iterator[dict]:
    """Yield one dict per embeddable, readable, non-denied symbol.

    `root` is the repository the CodeGraph paths are relative to. Files are read
    once and cached for the whole walk, because a module with 80 functions would
    otherwise be read 80 times.

    `stats` is how the air gap reports back, as in `commit_source.iter_commits`:
    the refusal happens in here, so a caller counting its own denials counts
    none of these and prints a total that reads as "nothing was withheld".
    """
    graph_db = Path(graph_db)
    if not graph_db.exists():
        raise ValueError(f"CodeGraph index not found: {graph_db}")

    conn = sqlite3.connect(read_only_uri(graph_db), uri=True)
    try:
        # `json_each` rather than a generated `IN (?,?,?)` list: the placeholder
        # count is the only thing an f-string was building here, and building ANY
        # part of a statement with an f-string is a pattern the security lint
        # flags on sight (ruff S608) -- correctly, because the next author copies
        # the shape and interpolates a value. This has no dynamic SQL at all.
        rows = conn.execute(
            "SELECT id, kind, name, qualified_name, file_path, start_line, end_line, "
            "COALESCE(signature,'') FROM nodes "
            "WHERE kind IN (SELECT value FROM json_each(?)) "
            "ORDER BY file_path, start_line",
            (json.dumps(list(EMBEDDABLE_KINDS)),),
        ).fetchall()
    finally:
        conn.close()

    cache: dict[str, tuple[str, list[str], float] | None] = {}

    for node_id, kind, name, qname, rel, start, end, signature in rows:
        if is_denied(rel, deny_prefixes, deny_segments):
            if stats is not None:
                stats["denied"] = stats.get("denied", 0) + 1
            continue            # NEVER read denied content

        if rel not in cache:
            fpath = root / rel
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                cache[rel] = (text, text.splitlines(), fpath.stat().st_mtime)
            except OSError:
                cache[rel] = None       # moved, deleted, or unreadable
        entry = cache[rel]
        if entry is None:
            continue
        text, lines, mtime = entry

        # The index lags the tree. A range past the end means the file changed
        # under us; skip rather than embed whatever now lives at those lines.
        #
        # `end` was not checked, only `start`. A node recorded as m.py:1-40 whose
        # file is now 3 lines passed the start test, `min(end, len(lines))`
        # silently clamped the slice to the whole file, and the record was
        # yielded with `path` reading "m.py:1-40". So the embedding carried a
        # whole unrelated file under a label naming a range that does not exist,
        # which is exactly the "wrong slice" this comment says is worse than a
        # gap. Measured 2026-08-26 on a scratch graph.
        #
        # Cost of the strict check, measured against the live
        # `.codegraph/codegraph.db` the same day: of 27 777 nodes with an end
        # line, 1 067 overshoot their file, every one of them by exactly 1 and
        # every one of them `kind='file'`. `EMBEDDABLE_KINDS` excludes `file`,
        # so the SQL above never returns one and this skip drops nothing that
        # is embedded today.
        if not (1 <= start <= len(lines)) or not (start <= end <= len(lines)):
            continue
        slice_ = "\n".join(lines[start - 1:end])

        doc = extract_docstring(text, name)
        parts = [signature.strip() or f"{kind} {name}"]
        if doc:
            parts.append(doc)
        parts.append(slice_)
        body = "\n".join(p for p in parts if p)[:MAX_BODY_CHARS]

        yield {
            "node_id": node_id,
            "id": f"symbol:{node_id}",
            "path": f"{rel}:{start}-{end}",
            "title": qname or name,
            "ntype": kind,
            "mtime": float(mtime),
            "body": body,
            "embed_text": f"{qname or name}\n{body}".strip(),
            "has_docstring": bool(doc),
        }
