#!/usr/bin/env python3
"""Do not re-prove a file the content-leak gate has already proved clean.

THE COST THIS EXISTS FOR. MEASURED 2026-09-05 in this checkout, 2330
engine-routed text files, 861 denylist tokens: `content-guard.py --all` spends
essentially all of its wall clock inside `Denylist.scan_text` -- 17.8 s in the
whole-text prefilter and 80.5 s in the per-line regex over the 278 files the
prefilter cannot rule out (on a loaded box; the shape, not the absolute number,
is what matters). Enumeration is 1.1 s, reading every file 0.2 s, building the
denylist 0.1 s. So the gate's price is per-file scanning, and the file it scans
is almost always the file it scanned last time, byte for byte.

`tests/test_a_gate_that_shipped_what_it_never_read.py::
test_the_whole_engine_surface_passes` pays that price on every pre-push run. Its
assertion is the RIGHT one and is not narrowed here: the gate's job is a
property of the WHOLE tree, and a delta-only test would go green over a tree
that had been dirty since before anyone was looking. What is wrong is only that
the verdict is recomputed from scratch when it is a pure function of inputs that
mostly did not change.

WHAT IS CACHED, AND WHY IT CANNOT BE WRONG
------------------------------------------

One row per file, and the row says: *these exact bytes, under this exact scanner,
produced zero findings.* The verdict function is `Denylist.scan_text(text)`,
whose only inputs are the text and the compiled token set. So the key is:

  1. the file's CONTENT digest (`repo_files.content_digest`), never its mtime
     or its size;
  2. `scanner_key()` below -- a digest of the scanning CODE and of the resolved
     denylist. If either moves, EVERY file is re-scanned.

The denylist half is a digest of the token set the harvest actually produced,
not a list of the files the harvest reads. That is deliberate and it is the
whole reason this can be trusted: the tokens are harvested from the private DATA
overlay (CRM contacts, the exec roster, the fireside roster, the operator's
curated list) and narrowed by `config/ordinary-english.txt` and by `STOPWORDS`.
A hand-kept list of "things that invalidate the cache" would have to name every
one of those and stay right forever, and a hand-maintained security list falls
behind in silence -- which is exactly what `STOPWORDS`' own comment ("this is
the ONLY such collision in the tree") did on 2026-09-04. Digesting the OUTPUT of
the harvest covers every input to it, including the ones nobody has added yet.

ONLY CLEAN VERDICTS ARE STORED, and that is a security property rather than an
optimisation. `content_denylist`'s first design constraint is that the denylist
IS PII and is never persisted into the engine. A findings row would carry the
matched token, which is the real-entity value itself, into a file inside the
engine checkout. So a file with findings gets NO row: it is re-scanned every
time and reports its findings freshly, and the store holds paths and digests and
nothing else. `scanner_key` carries a one-way digest of the token set, never the
tokens.

WHAT IS NOT CACHED, stated rather than left to be discovered:

* Which files are scanned. Selection (`repo_carried_paths`, `engine_text_files`,
  the routing map, the binary-suffix list) is recomputed on every run and is
  never read from this store. A file that newly enters the scan set has no row
  and is scanned; a file that leaves it is not scanned at all. So a routing
  change cannot produce a false clean through this cache, and routing is
  therefore not in the key.
* A file that could not be read. Unreadable is a refusal, not a verdict, and no
  row is written for it.

FAIL CLOSED, ALWAYS
-------------------

Every uncertainty re-scans. No row, an unreadable or corrupt store, a store
written under a different schema, a scanner key that could not be computed, a
digest that could not be taken: each means SCAN. There is no branch that skips
on doubt, and `tests/test_a_leak_scan_that_reproved_a_tree_it_had_already_
proved.py` drives each of those states rather than a docstring asserting them.

STORAGE
-------

One SQLite file, `.cache/content-verdicts.db`, joining `.cache/test-verdicts.db`
and `.memory-index/index.db` as an embedded store per
`.claude/rules/persistence.md`. No daemon, no port, no server database. It is
gitignored, which it must be for two independent reasons: it is machine-local
and meaningless in another checkout, and a TRACKED store would sit inside the
very corpus the gate scans.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.repo_files import content_digest  # noqa: E402
from scripts.utils.verdict_store import SqliteVerdictStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
STORE_PATH = ROOT / ".cache" / "content-verdicts.db"
SCHEMA_VERSION = "1"

#: The scanner key must be built from a real import closure. A bug that
#: collapsed it to one or two files would still produce a stable-looking hex
#: string, and the cache would then survive an edit to a module it never
#: hashed. MEASURED 2026-09-05: the gate's imports bring 14 in-repo modules with
#: them at the point the key is taken. The floor is set well below that so
#: ordinary refactoring does not trip it, and well above the collapse.
MODULE_FLOOR = 5

#: How many scanner-key generations the store keeps. See `CleanScanStore.record`
#: for why this is not one.
GENERATIONS_KEPT = 3


class ScannerKeyUnavailable(Exception):
    """The scanner could not be fingerprinted. Nothing may be reused."""


def _repo_module_files(modules, root: Path) -> list[Path]:
    """The source files of every loaded module that lives inside ``root``.

    DERIVED from the interpreter's own record of what it imported, never from a
    list in this file. The brief that asked for this cache named
    `content_denylist.py`, `content-guard.py`, `config/routing-map.yaml` and
    `config/ordinary-english.txt`; a list is what falls behind the moment the
    gate grows a helper, so the question asked here is "what did this process
    actually load from the repository", which answers itself.

    The entry point is included without being named: a script run as
    `python scripts/content-guard.py` is `sys.modules["__main__"]` with its
    `__file__` set to that path.

    THE LIMIT, stated rather than left to be found: this sees what has been
    imported BY THE TIME IT IS CALLED. A repository module imported lazily
    afterwards is not in the key. Callers take the key after building the
    denylist (which is what triggers the lazy imports) and before scanning, and
    `scan_text` imports nothing.

    THE SECOND LIMIT, and it is a real one rather than a formality. Third-party
    and stdlib modules are NOT hashed: hashing site-packages on every run would
    cost more than the scan this saves. A `pyyaml` upgrade that changed how the
    curated denylist parses is covered anyway, because it moves the harvested
    TOKEN SET and the token set is in the key. What is left uncovered is the
    scan itself, which uses only `re`, so `sys.version` goes into the key below
    and an interpreter change re-scans everything. A different `re` inside the
    same interpreter build is not covered by anything here.
    """
    root = root.resolve()
    venv = root / ".venv"
    files: set[Path] = set()
    for module in list(modules.values()):
        name = getattr(module, "__file__", None)
        if not name:
            continue
        try:
            path = Path(name).resolve()
        except (OSError, ValueError):
            # A `__file__` that cannot even be resolved to a path is a module
            # whose bytes this cannot hash, which is a doubt, which raises.
            raise ScannerKeyUnavailable(
                f"unresolvable module path: {name!r}") from None
        if not path.is_relative_to(root) or path.is_relative_to(venv):
            continue
        files.add(path)
    # A NOTE ON `<stdin>`. Run through a heredoc or `python -c`, `__main__` has
    # a `__file__` of `<stdin>` or `<string>`, which resolves against the cwd
    # into a path inside the repository that no reader can open, and
    # `scanner_key` then refuses and the caller scans everything. That is
    # correct rather than unfortunate: a loaded module whose source cannot be
    # read is code this cannot fingerprint, and the alternative -- skipping it
    # -- is a key that ignores a file it could not check. `content-guard.py` run
    # as a script has a real `__file__`, so the gate is unaffected; a REPL is
    # loud and slow instead of quiet and wrong.
    return sorted(files)


def scanner_key(denylist, *, root: Path | None = None, modules=None) -> str:
    """A digest of everything that decides what counts as a violation.

    Two halves, and a change to either invalidates every row in the store:

    * the CODE -- the source bytes of every repository module this process has
      loaded, derived from `sys.modules`;
    * the DENYLIST -- the resolved `token -> category` map, its `degraded` flag,
      and what the ordinary-English floor withheld.

    The denylist half is hashed as a whole (`sha256` over the sorted pairs), so
    the key is one-way: it can say "the token set is not the one it was", and it
    cannot say what is in it. That matters because this value is written to a
    file inside the engine checkout.

    Raises `ScannerKeyUnavailable` on any doubt: an unreadable module source, a
    module path that will not resolve, or a closure so small it cannot be the
    real one. The caller disables the cache and scans everything.

    ``root`` defaults to THIS MODULE's own checkout and callers must leave it
    alone outside tests. It is deliberately not `get_workspace_root()`: a
    `WORKSPACE_ROOT` in a copied `.env` repoints that resolver at another tree
    (CLAUDE.md, "Guards must be armed inside the task"), and a code fingerprint
    that moved with it would be a fingerprint of whatever checkout the
    environment named rather than of the code that is about to run.
    `content_denylist._ORDINARY_ENGLISH_PATH` is resolved the same way and for
    the same reason.
    """
    repo = ROOT if root is None else Path(root)
    module_files = _repo_module_files(
        sys.modules if modules is None else modules, repo)
    if len(module_files) < MODULE_FLOOR:
        # A floor under the corpus, per development-standards obligation 7. A
        # key built over an empty or near-empty closure would be stable and
        # meaningless, and the cache would then outlive the code it fingerprints.
        raise ScannerKeyUnavailable(
            f"only {len(module_files)} repository module(s) were loaded when the "
            f"scanner key was taken; the closure cannot be the real one")

    digest = hashlib.sha256()
    digest.update(f"v{SCHEMA_VERSION}\0code\0".encode())
    # The interpreter, because the scan is a `re` match and `re` ships with it.
    digest.update(sys.version.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    for path in module_files:
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise ScannerKeyUnavailable(f"cannot hash {path}: {exc}") from exc
        try:
            rel = path.relative_to(repo.resolve()).as_posix()
        except ValueError:  # pragma: no cover - filtered above
            rel = path.as_posix()
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(content_digest(blob).encode("ascii"))
        digest.update(b"\0")

    digest.update(b"denylist\0")
    digest.update(b"degraded\0" if getattr(denylist, "degraded", True) else b"ok\0")
    for token, category in sorted(getattr(denylist, "tokens", {}).items()):
        digest.update(token.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(str(category).encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    # The withheld set is part of the verdict function too: a word the floor
    # stops withholding becomes a token again, and every file must be re-read
    # against it. It is derived from `tokens` plus the vocabulary artifact, so
    # in practice a change here also changes `tokens`; it is hashed anyway
    # rather than argued about.
    digest.update(b"withheld\0")
    for token in sorted(getattr(denylist, "withheld", {})):
        digest.update(token.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clean (
    scanner_key TEXT NOT NULL,
    path        TEXT NOT NULL,
    digest      TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (scanner_key, path, digest)
);
"""


class CleanScanStore(SqliteVerdictStore):
    """Which files the content gate found clean, against which scanner key.

    A row exists only for a file that scanned CLEAN. There is no findings row
    (it would carry a real-entity token to disk) and no "unknown" row, because
    the only question asked of this store is "may this scan be reused", and
    every answer other than a present row is no.
    """

    SCHEMA = _SCHEMA
    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(STORE_PATH if path is None else Path(path))

    def clean_digests(self, key: str) -> dict[str, str] | None:
        """`path -> digest` for every file recorded clean at ``key``.

        `None`, not `{}`, when the store could not be read. The two are the
        whole difference between "nothing is cached" and "nothing is known", and
        a caller that reads the second as the first is still correct HERE only
        because both scan everything. It is returned distinctly anyway so the
        caller can say which happened, per `.claude/rules/scope-claims.md`.
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            rows = conn.execute(
                "SELECT path, digest FROM clean WHERE scanner_key = ?",
                (key,)).fetchall()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return None
        finally:
            conn.close()
        return {row[0]: row[1] for row in rows}

    def record(self, key: str, entries) -> bool:
        """Record `(path, digest)` pairs as clean. False when the write failed.

        Rows are kept for the `GENERATIONS_KEPT` most recently written scanner
        keys and older generations are dropped in the same transaction. Keeping
        only the current key was tried first and measured worse in the case that
        actually happens: editing a scanner module and reverting it costs a full
        49 s re-scan TWICE, because the revert restores a key whose rows were
        just deleted. Three generations is roughly a megabyte and makes the
        revert free. Keeping every key for ever would grow without bound.
        """
        conn = self._connect()
        if conn is None:
            return False
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO clean "
                "(scanner_key, path, digest, recorded_at) VALUES (?, ?, ?, ?)",
                [(key, str(path), str(digest), stamp) for path, digest in entries])
            conn.execute(
                "DELETE FROM clean WHERE scanner_key NOT IN ("
                "  SELECT scanner_key FROM clean GROUP BY scanner_key"
                "  ORDER BY MAX(recorded_at) DESC, MAX(rowid) DESC LIMIT ?)",
                (GENERATIONS_KEPT,))
            conn.commit()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return False
        finally:
            conn.close()
        return True

    def rows(self) -> int:
        conn = self._connect()
        if conn is None:
            return 0
        try:
            return conn.execute("SELECT COUNT(*) FROM clean").fetchone()[0]
        except sqlite3.DatabaseError:
            return 0
        finally:
            conn.close()

    def clear(self) -> bool:
        conn = self._connect()
        if conn is None:
            return False
        try:
            conn.execute("DELETE FROM clean")
            conn.commit()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return False
        finally:
            conn.close()
        return True


class ScanCache:
    """The gate's view of the store: ask, then record. Never skips on doubt.

    `open()` returns a cache that is either ARMED (a key was computed and the
    store was readable) or DISABLED, and a disabled cache answers `False` to
    every `is_clean`, which scans everything. The caller prints `warnings` so a
    disabled cache is visible rather than merely slow.
    """

    def __init__(self, key: str | None, known: dict[str, str] | None,
                 store: CleanScanStore | None, warnings: list[str]) -> None:
        self.key = key
        self._known = known
        self._store = store
        self.warnings = warnings
        self.reused = 0
        self._fresh: list[tuple[str, str]] = []

    @property
    def armed(self) -> bool:
        return self.key is not None and self._known is not None

    def drain_warnings(self) -> list[str]:
        """The warnings raised since the last drain. Emptied by reading.

        A caller prints these; printing them twice would read as two separate
        failures of the same store.
        """
        out, self.warnings = self.warnings, []
        return out

    @classmethod
    def disabled(cls) -> "ScanCache":
        """A cache that answers no to everything. What `--no-cache` builds."""
        return cls(None, None, None, [])

    @classmethod
    def open(cls, denylist, *, store_root: Path | None = None,
             store: CleanScanStore | None = None,
             code_root: Path | None = None, modules=None) -> "ScanCache":
        """Arm the cache, or return a disabled one that scans everything.

        ``store_root`` is where the store FILE lives and is the caller's
        workspace root, so a sandboxed run writes its rows inside the sandbox.
        ``code_root`` and ``modules`` exist for the tests that drive a scanner
        change; production leaves both alone, and the key is then taken over
        this module's own checkout (see `scanner_key`).
        """
        warnings: list[str] = []
        if store is None:
            store = (CleanScanStore() if store_root is None
                     else CleanScanStore(Path(store_root) / ".cache"
                                         / STORE_PATH.name))
        try:
            key = scanner_key(denylist, root=code_root, modules=modules)
        except ScannerKeyUnavailable as exc:
            warnings.append(
                f"verdict cache DISABLED: the scanner key could not be computed "
                f"({exc}). Every file is scanned.")
            return cls(None, None, None, warnings)
        known = store.clean_digests(key)
        if known is None:
            warnings.append(
                f"verdict cache DISABLED: the store at {store.path} could not be "
                f"read ({store.corrupt_reason}). Every file is scanned.")
            return cls(None, None, None, warnings)
        return cls(key, known, store, warnings)

    def is_clean(self, rel: str, digest: str) -> bool:
        """Has THIS path, with THESE bytes, already been proved clean here?

        Both halves are required. A path whose recorded digest differs is a
        different file with the same name, and it is scanned.
        """
        if not self.armed:
            return False
        hit = self._known.get(rel) == digest
        if hit:
            self.reused += 1
        return hit

    def note_clean(self, rel: str, digest: str) -> None:
        """A file that was scanned in this run and produced no findings."""
        if self.armed:
            self._fresh.append((rel, digest))

    def flush(self) -> None:
        """Write this run's clean verdicts. A failed write is not fatal.

        A store that cannot be written leaves the next run with fewer rows,
        which is slower and never wrong, so it is reported and not raised.
        """
        if not self.armed or not self._fresh:
            return
        # Carry the rows already known good forward: `record` drops every other
        # key, and a run that scanned only a subset (`--files`) must not delete
        # the rest of its own key's rows. Insertion is idempotent.
        if not self._store.record(self.key, self._fresh):
            self.warnings.append(
                f"verdict cache: {len(self._fresh)} clean verdict(s) could not be "
                f"stored ({self._store.corrupt_reason}); the next run re-scans them.")
        self._fresh = []
