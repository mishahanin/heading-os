#!/usr/bin/env python3
"""One SQLite store shape for every "has this already been proved?" cache.

Two caches in this workspace answer that question and neither may answer it
optimistically: `scripts/utils/test_cache.py` (which test FILES passed against a
corpus key) and `scripts/utils/content_scan_cache.py` (which engine files the
content-leak gate found clean against a scanner key). Their tables differ; the
part that must not differ is what happens when the store cannot be read.

That part is here, spelled once. A store that is corrupt, truncated, a directory
where a file should be, written by a build with a different schema, or simply
unopenable by this user, yields `None` from `_connect()` and records WHY on
`corrupt_reason`. Every caller reads `None` as "no verdict can be read", which
means everything runs. There is no branch that treats an unreadable store as an
empty one, because an empty store and an unreadable store differ in exactly the
direction that matters: the first says nothing is cached, the second says
nothing is KNOWN, and only one of those is safe to act on quietly.

Written as a base class 2026-09-05 because there were about to be two copies of
it. `VerdictStore._connect` already existed; the content-scan cache needed the
same twelve lines with a different `CREATE TABLE`, and a fix that lands in one
of N copies is this repository's dominant defect shape (35 commits of the
September campaign, `repo_files.git_index_paths` records the last one).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#: How long a writer waits for another process's lock before giving up. The
#: pre-commit gate and a pytest worker can reach the same store at once, and a
#: timeout is reported as "could not be read", which runs the scan rather than
#: skipping it. Five seconds is far longer than any write here takes.
BUSY_TIMEOUT_S = 5.0


class SqliteVerdictStore:
    """A machine-local SQLite store that fails closed on every doubt.

    Subclasses set `SCHEMA` (the `CREATE TABLE IF NOT EXISTS` script) and
    `SCHEMA_VERSION` (a string stamped into a `meta` row). A store written by a
    build with a different version is refused rather than migrated: a migration
    is a claim that the old rows still mean what they meant, and for a cache
    whose rows are security verdicts that claim has to be made deliberately, by
    bumping the version and letting every file be re-proved.
    """

    #: Subclasses override both.
    SCHEMA: str = ""
    SCHEMA_VERSION: str = "1"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.corrupt_reason: str | None = None

    def _connect(self) -> sqlite3.Connection | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
            conn.executescript(self.SCHEMA)
            conn.executescript(_META_SCHEMA)
            stored = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if stored is None:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (self.SCHEMA_VERSION,))
                conn.commit()
            elif stored[0] != self.SCHEMA_VERSION:
                conn.close()
                self.corrupt_reason = (
                    f"store schema is {stored[0]}, this build writes "
                    f"{self.SCHEMA_VERSION}")
                return None
            return conn
        except (sqlite3.DatabaseError, OSError) as exc:
            # Corrupt, truncated, a directory where the file should be, or a
            # file this user cannot open. Every one of them means no verdict can
            # be read, which means everything runs. Recorded rather than
            # swallowed so the caller can print the loud line.
            self.corrupt_reason = str(exc)
            return None


#: The version row every store carries. Kept beside the base rather than copied
#: into each subclass's schema, so a store cannot be created without one.
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
