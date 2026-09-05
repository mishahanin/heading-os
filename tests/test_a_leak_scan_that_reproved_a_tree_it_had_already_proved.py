#!/usr/bin/env python3
"""The content-leak gate re-proved, on every push, a tree it had already proved.

MEASURED 2026-09-05 in HELM: `tests/test_a_gate_that_shipped_what_it_never_read.
py::test_the_whole_engine_surface_passes` took 44.3 s, the next most expensive
test in that file 0.17 s, and the other thirty hundredths. All of it is
`Denylist.scan_text` over 2330 engine files, and almost every one of those files
is byte-for-byte the file the previous run scanned.

`scripts/utils/content_scan_cache.py` reuses a per-file verdict, and the ONE
outcome that must be impossible is a gate made cheaper by checking less. So the
tests below are written against the two directions rather than against the
speed-up:

REUSE IS EXACT. A verdict is reused only when the file's CONTENT digest and the
scanner key both match. A file whose bytes changed is scanned, and if it now
carries a real-entity token the gate blocks. A scanner key that moved -- the
scanning code, the denylist token set, the degraded flag, the interpreter -- is
a different key, and every row under the old one is invisible.

FAIL CLOSED. No row, an unreadable store, a store from another schema, a key
that could not be computed, a closure too small to be the real one: each of
those scans. The cases below drive each state; none of them is a docstring
saying so.

NOTHING PRIVATE IS PERSISTED. `content_denylist`'s first design constraint is
that the denylist is PII and is never written into the engine. Only CLEAN
verdicts get a row, so a matched token never reaches disk, and
`test_the_store_holds_no_denylist_token_and_no_matched_text` reads the store's
raw bytes back to check it.

Run: python3 -m pytest tests/test_a_leak_scan_that_reproved_a_tree_it_had_already_proved.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import Denylist  # noqa: E402
from scripts.utils.content_scan_cache import (  # noqa: E402
    GENERATIONS_KEPT, MODULE_FLOOR, CleanScanStore, ScanCache,
    ScannerKeyUnavailable, scanner_key,
)

GUARD = ROOT / "scripts" / "content-guard.py"

#: The invented company the sandbox overlay is built around. Fictional by
#: construction: the engine carries no real entity, so a gate test must invent
#: the thing it is driven with.
ENTITY = "Spectre Holdings"


# ============================================================
# Fixtures: a synthetic engine tree and a synthetic DATA overlay
# ============================================================

@pytest.fixture()
def sandbox(tmp_path):
    """A throwaway engine + overlay, the same shape the sibling gate test uses.

    Nothing here touches the real repository or the operator's overlay. The
    verdict store lands at `<engine>/.cache/content-verdicts.db`, inside
    `tmp_path`, because the gate resolves the STORE from the workspace root
    while resolving the SCANNER KEY from the code's own checkout.
    """
    engine = tmp_path / "engine"
    (engine / ".claude").mkdir(parents=True)
    (engine / "CLAUDE.md").write_text("# marker\n", encoding="utf-8")
    (engine / "config").mkdir()
    shutil.copy2(ROOT / "config" / "routing-map.yaml",
                 engine / "config" / "routing-map.yaml")
    (engine / "docs").mkdir()

    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "content-denylist.yaml").write_text(
        f"companies:\n  - {ENTITY}\n", encoding="utf-8")

    env = dict(os.environ, WORKSPACE_ROOT=str(engine), HEADING_OS_DATA=str(data))
    return engine, data, env


def _run(sandbox, *rels, extra=()):
    engine, data, env = sandbox
    return subprocess.run(
        [sys.executable, str(GUARD), *extra, "--files", *rels,
         "--data-root", str(data)],
        capture_output=True, text=True, timeout=300, check=False,
        cwd=str(engine), env=env)


def _store_path(sandbox) -> Path:
    engine, _data, _env = sandbox
    return engine / ".cache" / "content-verdicts.db"


def _denylist(tokens: dict[str, str], *, degraded: bool = False) -> Denylist:
    dl = Denylist()
    dl.tokens = dict(tokens)
    dl.degraded = degraded
    dl._compile()
    return dl


# ============================================================
# Through the real entry point: reuse is exact
# ============================================================

def test_the_sandbox_really_arms_the_gate(sandbox):
    """The experiment before the measurement.

    A denylist that harvested nothing makes the gate print "skipped" and exit 0,
    and every case below would then pass while measuring the skip.
    """
    engine, _data, _env = sandbox
    (engine / "docs" / "ok.md").write_text("Ordinary prose.\n", encoding="utf-8")

    proc = _run(sandbox, "docs/ok.md")

    assert "skipped" not in proc.stdout, proc.stdout
    assert "1 denylist tokens" in proc.stdout, proc.stdout


def test_a_second_run_over_an_unchanged_file_reuses_the_first_verdict(sandbox):
    engine, _data, _env = sandbox
    (engine / "docs" / "ok.md").write_text("Ordinary prose.\n", encoding="utf-8")

    first = _run(sandbox, "docs/ok.md")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "unchanged since a clean scan" not in first.stdout, (
        "the first run had nothing to reuse; if it says it did, the store was "
        "not empty and nothing below measures a cold scan")

    second = _run(sandbox, "docs/ok.md")

    assert second.returncode == 0, second.stdout + second.stderr
    assert "1 of 1 unchanged since a clean scan" in second.stdout, second.stdout


def test_the_reuse_survives_a_touch_that_changes_no_byte(sandbox):
    """Keyed on CONTENT. An mtime moves when a file is touched and not edited."""
    engine, _data, _env = sandbox
    target = engine / "docs" / "ok.md"
    target.write_text("Ordinary prose.\n", encoding="utf-8")
    _run(sandbox, "docs/ok.md")

    os.utime(target, (0, 0))
    proc = _run(sandbox, "docs/ok.md")

    assert "1 of 1 unchanged since a clean scan" in proc.stdout, proc.stdout


def test_a_file_edited_after_a_clean_verdict_is_scanned_again_and_blocked(sandbox):
    """The case a cache gets wrong, driven end to end.

    Same path, clean once, then a real-entity token written into it. A cache
    keyed on anything but content -- a path, an mtime, a "we already did this
    file" set -- reports clean here and ships the leak.
    """
    engine, _data, _env = sandbox
    target = engine / "docs" / "ok.md"
    target.write_text("Ordinary prose.\n", encoding="utf-8")
    assert _run(sandbox, "docs/ok.md").returncode == 0

    target.write_text(f"A note about {ENTITY} and its quarter.\n", encoding="utf-8")
    proc = _run(sandbox, "docs/ok.md")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BLOCKED" in proc.stdout
    assert "docs/ok.md:1" in proc.stdout


def test_a_leaking_file_is_never_recorded_clean(sandbox):
    """A findings row would carry the token to disk, so there is none.

    It also has to keep blocking: a gate that refuses once and then reuses a
    verdict it never recorded would pass the file on the second run.
    """
    engine, _data, _env = sandbox
    (engine / "docs" / "leak.md").write_text(
        f"A note about {ENTITY}.\n", encoding="utf-8")

    first = _run(sandbox, "docs/leak.md")
    second = _run(sandbox, "docs/leak.md")

    assert first.returncode == 1, first.stdout + first.stderr
    assert second.returncode == 1, second.stdout + second.stderr
    assert "BLOCKED" in second.stdout


def test_an_unreadable_file_is_refused_on_every_run(sandbox):
    """Unverified is not clean, and it does not become clean by repetition."""
    engine, _data, _env = sandbox
    (engine / "docs" / "probe.md").write_bytes(b"caf\xe9\n")

    first = _run(sandbox, "docs/probe.md")
    second = _run(sandbox, "docs/probe.md")

    assert first.returncode == 1, first.stdout + first.stderr
    assert second.returncode == 1, second.stdout + second.stderr
    assert "REFUSED" in second.stderr


def test_no_cache_scans_every_file_again(sandbox):
    engine, _data, _env = sandbox
    (engine / "docs" / "ok.md").write_text("Ordinary prose.\n", encoding="utf-8")
    _run(sandbox, "docs/ok.md")

    proc = _run(sandbox, "docs/ok.md", extra=("--no-cache",))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unchanged since a clean scan" not in proc.stdout, proc.stdout


def test_the_clean_line_says_how_much_of_it_was_reused(sandbox):
    """A verdict is a claim about what was compared, so the split is printed.

    `.claude/rules/scope-claims.md`: a narrowed check that prints like a
    complete one is the defect, and silence about the narrowing is what makes
    it one.
    """
    engine, _data, _env = sandbox
    for name in ("a.md", "b.md", "c.md"):
        (engine / "docs" / name).write_text(f"Prose {name}.\n", encoding="utf-8")
    rels = [f"docs/{n}" for n in ("a.md", "b.md", "c.md")]
    _run(sandbox, *rels)
    (engine / "docs" / "b.md").write_text("Prose b, edited.\n", encoding="utf-8")

    proc = _run(sandbox, *rels)

    assert "2 of 3 unchanged since a clean scan" in proc.stdout, proc.stdout
    assert "1 re-read" in proc.stdout, proc.stdout


def test_the_store_holds_no_denylist_token_and_no_matched_text(sandbox):
    """The denylist is PII and is never persisted into the engine.

    Both a clean file and a leaking one are scanned first, so the store has seen
    the token in this process; the assertion is that no byte of it landed.
    """
    engine, _data, _env = sandbox
    (engine / "docs" / "ok.md").write_text("Ordinary prose.\n", encoding="utf-8")
    (engine / "docs" / "leak.md").write_text(
        f"A note about {ENTITY}.\n", encoding="utf-8")
    _run(sandbox, "docs/ok.md", "docs/leak.md")

    store = _store_path(sandbox)
    assert store.is_file(), "nothing was stored, so this asserts nothing"
    raw = store.read_bytes().lower()
    for word in ENTITY.lower().split():
        assert word.encode() not in raw, f"the store carries {word!r}"


# ============================================================
# The scanner half of the key
# ============================================================

@pytest.fixture()
def fake_code(tmp_path):
    """A scratch 'code checkout' whose module set the tests can edit.

    `scanner_key` derives its closure from `sys.modules`, so a test that wants
    to change the SCANNER without touching the real repository injects its own
    module objects pointing at files it owns.
    """
    root = tmp_path / "code"
    (root / "scripts" / "utils").mkdir(parents=True)
    modules = {}
    for index in range(MODULE_FLOOR + 2):
        path = root / "scripts" / "utils" / f"mod{index}.py"
        path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        module = types.ModuleType(f"mod{index}")
        module.__file__ = str(path)
        modules[f"mod{index}"] = module
    return root, modules


def test_a_scanner_code_change_moves_the_key(fake_code):
    """The files did not change; the code did. Everything must re-scan."""
    root, modules = fake_code
    dl = _denylist({"acme corp": "curated:companies"})
    before = scanner_key(dl, root=root, modules=modules)

    edited = root / "scripts" / "utils" / "mod0.py"
    edited.write_text("VALUE = 0  # one comment\n", encoding="utf-8")
    after = scanner_key(dl, root=root, modules=modules)

    assert before != after


def test_a_scanner_change_makes_every_recorded_verdict_invisible(fake_code, tmp_path):
    """The end of that sentence, at the boundary the gate actually uses.

    Ten files recorded clean, then one byte of one scanner module changes, and
    the count reused drops from ten to zero with nothing else touched.
    """
    root, modules = fake_code
    dl = _denylist({"acme corp": "curated:companies"})
    store = CleanScanStore(tmp_path / "store.db")
    files = [(f"docs/{i}.md", f"digest-{i}") for i in range(10)]

    first = ScanCache.open(dl, store=store, code_root=root, modules=modules)
    assert first.armed
    for rel, digest in files:
        assert not first.is_clean(rel, digest)
        first.note_clean(rel, digest)
    first.flush()

    warm = ScanCache.open(dl, store=store, code_root=root, modules=modules)
    assert sum(warm.is_clean(rel, d) for rel, d in files) == 10, (
        "the store did not come back warm, so the cold count below proves "
        "nothing")

    (root / "scripts" / "utils" / "mod3.py").write_text(
        "VALUE = 3  # edited\n", encoding="utf-8")
    cold = ScanCache.open(dl, store=store, code_root=root, modules=modules)

    assert cold.armed, "the key still computes; it is simply a different key"
    assert sum(cold.is_clean(rel, d) for rel, d in files) == 0


def test_a_denylist_token_added_moves_the_key(fake_code):
    """A new CRM contact is a new token, and a new token is a new question."""
    root, modules = fake_code
    before = scanner_key(_denylist({"acme corp": "curated:companies"}),
                         root=root, modules=modules)
    after = scanner_key(
        _denylist({"acme corp": "curated:companies", "rivex": "crm-org"}),
        root=root, modules=modules)

    assert before != after


def test_a_tokens_category_changing_moves_the_key(fake_code):
    """The category is what the gate REPORTS, so it is part of the verdict."""
    root, modules = fake_code
    before = scanner_key(_denylist({"rivex": "crm-org"}), root=root, modules=modules)
    after = scanner_key(_denylist({"rivex": "curated:companies"}),
                        root=root, modules=modules)

    assert before != after


def test_a_degraded_harvest_moves_the_key(fake_code):
    """A partial harvest is a different scanner from a complete one."""
    root, modules = fake_code
    tokens = {"acme corp": "curated:companies"}
    whole = scanner_key(_denylist(tokens), root=root, modules=modules)
    partial = scanner_key(_denylist(tokens, degraded=True), root=root,
                          modules=modules)

    assert whole != partial


def test_an_identical_scanner_gives_an_identical_key(fake_code):
    """The other direction. A key that never repeats is a cache that never hits."""
    root, modules = fake_code
    dl = _denylist({"acme corp": "curated:companies"})

    assert (scanner_key(dl, root=root, modules=modules)
            == scanner_key(dl, root=root, modules=modules))


def test_a_collapsed_module_closure_refuses_rather_than_keying_on_nothing(tmp_path):
    """A floor under the corpus, per development-standards obligation 7.

    A closure that came back empty would still hash to a stable-looking string,
    and the cache would then survive every edit to code it never read.
    """
    with pytest.raises(ScannerKeyUnavailable):
        scanner_key(_denylist({"acme corp": "curated:companies"}),
                    root=tmp_path, modules={})


def test_an_unreadable_module_source_refuses(fake_code):
    """A module this cannot hash is a doubt, and a doubt scans."""
    root, modules = fake_code
    (root / "scripts" / "utils" / "mod0.py").unlink()

    with pytest.raises(ScannerKeyUnavailable):
        scanner_key(_denylist({"acme corp": "curated:companies"}),
                    root=root, modules=modules)


def test_the_live_gate_fingerprints_the_module_that_decides_a_violation():
    """A floor over the REAL closure, not a synthetic one.

    `scan_text` lives in `content_denylist`, so if that file is not in the
    closure the key cannot notice the scanner changing. Asserted by importing
    what the gate imports and reading the closure back out of `sys.modules`,
    which is where the gate reads it from too.
    """
    import scripts.utils.content_denylist  # noqa: F401
    from scripts.utils.content_scan_cache import _repo_module_files

    loaded = _repo_module_files(sys.modules, ROOT)
    names = {p.relative_to(ROOT).as_posix() for p in loaded}

    assert "scripts/utils/content_denylist.py" in names
    assert "scripts/utils/content_scan_cache.py" in names
    assert len(names) >= MODULE_FLOOR, sorted(names)


def test_the_closure_excludes_the_virtualenv(fake_code):
    """Hashing site-packages on every run would cost more than the scan saved."""
    root, modules = fake_code
    venv_file = root / ".venv" / "lib" / "third_party.py"
    venv_file.parent.mkdir(parents=True)
    venv_file.write_text("VALUE = 1\n", encoding="utf-8")
    stranger = types.ModuleType("third_party")
    stranger.__file__ = str(venv_file)
    modules = dict(modules, third_party=stranger)
    dl = _denylist({"acme corp": "curated:companies"})
    before = scanner_key(dl, root=root, modules=modules)

    venv_file.write_text("VALUE = 2\n", encoding="utf-8")

    assert scanner_key(dl, root=root, modules=modules) == before


# ============================================================
# The store, and every way of being unsure about it
# ============================================================

def test_a_missing_row_is_not_a_clean_verdict(tmp_path, fake_code):
    root, modules = fake_code
    cache = ScanCache.open(_denylist({"rivex": "crm-org"}),
                           store=CleanScanStore(tmp_path / "s.db"),
                           code_root=root, modules=modules)

    assert cache.armed
    assert not cache.is_clean("docs/never-seen.md", "digest")


def test_a_row_whose_digest_differs_is_not_a_clean_verdict(tmp_path, fake_code):
    root, modules = fake_code
    store = CleanScanStore(tmp_path / "s.db")
    dl = _denylist({"rivex": "crm-org"})
    warm = ScanCache.open(dl, store=store, code_root=root, modules=modules)
    warm.note_clean("docs/a.md", "digest-one")
    warm.flush()

    reopened = ScanCache.open(dl, store=store, code_root=root, modules=modules)

    assert reopened.is_clean("docs/a.md", "digest-one")
    assert not reopened.is_clean("docs/a.md", "digest-two")


def test_a_corrupt_store_disables_the_cache_and_says_so(tmp_path, fake_code):
    root, modules = fake_code
    path = tmp_path / "s.db"
    path.write_bytes(b"this is not a sqlite database" * 40)

    cache = ScanCache.open(_denylist({"rivex": "crm-org"}),
                           store=CleanScanStore(path), code_root=root,
                           modules=modules)

    assert not cache.armed
    assert not cache.is_clean("docs/a.md", "digest")
    assert any("DISABLED" in w for w in cache.warnings), cache.warnings


def test_a_store_written_under_another_schema_is_refused(tmp_path, fake_code):
    """A migration is a claim that old rows still mean what they meant.

    For a store whose rows are security verdicts, that claim is made by bumping
    the version and re-proving every file, never by reading them anyway.
    """
    root, modules = fake_code
    path = tmp_path / "s.db"
    conn = sqlite3.connect(path)
    conn.executescript(CleanScanStore.SCHEMA)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta (key, value) VALUES ('schema_version', '0');")
    conn.commit()
    conn.close()

    cache = ScanCache.open(_denylist({"rivex": "crm-org"}),
                           store=CleanScanStore(path), code_root=root,
                           modules=modules)

    assert not cache.armed
    assert not cache.is_clean("docs/a.md", "digest")


def test_a_key_that_cannot_be_computed_disables_the_cache(tmp_path):
    cache = ScanCache.open(_denylist({"rivex": "crm-org"}),
                           store=CleanScanStore(tmp_path / "s.db"),
                           code_root=tmp_path / "nowhere", modules={})

    assert not cache.armed
    assert not cache.is_clean("docs/a.md", "digest")
    assert any("scanner key" in w for w in cache.warnings), cache.warnings


def test_a_failed_write_is_reported_and_not_fatal(tmp_path, fake_code):
    """A store that cannot be written leaves the next run slower, never wrong."""
    root, modules = fake_code
    blocked = tmp_path / "blocked.db"
    blocked.mkdir()  # a directory where the store file should be
    store = CleanScanStore(blocked)
    cache = ScanCache.open(_denylist({"rivex": "crm-org"}), store=store,
                           code_root=root, modules=modules)
    assert not cache.armed

    cache.note_clean("docs/a.md", "digest")
    cache.flush()  # must not raise

    assert not cache.is_clean("docs/a.md", "digest")


def test_generations_beyond_the_kept_window_are_dropped(tmp_path):
    """Bounded growth, and the revert case that made it more than one.

    Editing a scanner module and reverting it is an ordinary afternoon, and
    keeping only the current key made that cost two full re-scans.
    """
    store = CleanScanStore(tmp_path / "s.db")
    for generation in range(GENERATIONS_KEPT + 2):
        assert store.record(f"key-{generation}",
                            [(f"docs/{i}.md", f"d{i}") for i in range(4)])

    assert store.rows() == GENERATIONS_KEPT * 4
    assert store.clean_digests("key-0") == {}
    assert len(store.clean_digests(f"key-{GENERATIONS_KEPT + 1}")) == 4


def test_an_unreadable_store_is_told_apart_from_an_empty_one(tmp_path):
    """`None` and `{}` are different answers and must stay different.

    Both scan everything today, so a caller confusing them is still correct by
    accident. It is returned distinctly so the caller can say WHICH happened,
    per `.claude/rules/scope-claims.md`.
    """
    empty = CleanScanStore(tmp_path / "empty.db")
    corrupt = CleanScanStore(tmp_path / "corrupt.db")
    corrupt.path.write_bytes(b"not a database" * 40)

    assert empty.clean_digests("key") == {}
    assert corrupt.clean_digests("key") is None
    assert corrupt.corrupt_reason
