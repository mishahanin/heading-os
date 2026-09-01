#!/usr/bin/env python3
"""Four loops decoded a file with no try at all, so one bad byte ended the walk.

The sharper cousin of the `except OSError` class. An AST sweep on 2026-09-01
asked "which try-handlers cannot catch a decode error?" and fixed four modules.
It could not see a read that has NO handler in the first place, and a shard
auditor found `crm.scan_contacts` sitting in exactly that blind spot. Widening
the sweep to "decode points inside a walking loop that are in no try body"
returned 15 sites across `scripts/` and `.claude/hooks/`.

Ranked by whether the loop reads OPERATOR-written markdown, four were probed
against a corpus of one clean note, one carrying a lone 0xe9, and one carrying
REAL accented UTF-8. Measured before the fix:

    chronicle._load_personal_entries          RAISED   -> returns 2 of 3
    workspace-health.check_context_freshness  RAISED   -> reports all 3
    crm_migrate.scan_all_contacts             RAISED   -> returns 2 of 3
    offboard-exec.reassign_contacts           (below)  -> transfers the rest

`workspace-health` is the worst of the four to lose. It is a HEALTH CHECK that
runs before `/push-updates`, and it did not fail early: it reported on the first
file, then died on the second, so every later context file went unchecked and
the run produced no verdict at all. The traceback named a codec, a byte and an
offset, and no filename.

`offboard-exec` is the worst to get wrong. It runs while an executive's access
is being pulled. One unreadable card ended the loop, so the cards before it had
already been transferred and every card after it never was, with the operator
told nothing either way. It now reports the skipped names beside the transferred
count, in the same breath, because a transferred total printed alone reads as
"all of them".

Each fix skips the file and NAMES it. Silence is the separate defect: a dropped
record that nothing reports turns a count into a lower bound that reads as a
total.

`knowledge-health.scan_shared_notes` was on the ranked list and is NOT claimed
here. Its probe returned an empty list, which means the corpus never reached the
directory it reads, so the probe established nothing either way. Recording that
as unmeasured rather than as clean.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LONE_CONTINUATION = b"\xe9"
CLEAN = "---\ntitle: fine\nlast_verified: 2026-01-01\n---\n\nBody.\n"
# Real, valid UTF-8 above ASCII. The anchor against over-refusal: a fix that
# skipped every file with a high byte would satisfy every case below and
# quietly drop a third of the corpus.
ACCENTED = ("---\ntitle: caf\u00e9 latt\u00e9\nlast_verified: 2026-01-01\n"
            "---\n\nR\u00e9sum\u00e9.\n")
BROKEN = b"---\ntitle: broken\n---\n\nCaf" + LONE_CONTINUATION + b" note.\n"


def _load(rel: str, name: str):
    """Load a hyphenated script by path; it cannot be imported by name."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _three_files(directory: Path, stem: str = "") -> Path:
    """Clean, broken, accented. The broken one sorts in the MIDDLE.

    Deliberate: a reader that dies loses a file it had not reached yet as well
    as the one it was on, and all four of these sort their glob.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}a-clean.md").write_text(CLEAN, encoding="utf-8")
    (directory / f"{stem}b-broken.md").write_bytes(BROKEN)
    (directory / f"{stem}c-accented.md").write_text(ACCENTED, encoding="utf-8")
    return directory


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    """Point the data-root seam at a scratch tree for the whole test."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    return tmp_path


def test_the_chronicle_returns_the_notes_it_could_read(data_root, capsys):
    personal = data_root / "chronicle" / "personal"
    personal.mkdir(parents=True)
    (personal / "session-a.md").write_text(CLEAN, encoding="utf-8")
    (personal / "session-b.md").write_bytes(BROKEN)
    (personal / "session-c.md").write_text(ACCENTED, encoding="utf-8")

    entries = _load("scripts/chronicle.py", "chronicle_ut")._load_personal_entries()
    assert isinstance(entries, list), (
        "_load_personal_entries raised on one undecodable note instead of "
        "skipping it, so the whole personal chronicle came back as an exception")
    assert len(entries) == 2, (
        f"expected the clean note and the accented note, got {len(entries)}. "
        f"Two means the fix skipped exactly the broken file; one means it also "
        f"refused valid UTF-8 above ASCII.")
    err = capsys.readouterr().err
    assert "session-b.md" in err, (
        f"the dropped note was not named on any stream: {err!r}")


def test_the_health_check_reports_every_context_file(data_root, capsys):
    """It must not stop at the first file it cannot read.

    The count returned is the issue count, so the unreadable file has to raise
    it: a freshness check that could not open a file has not found it fresh.
    """
    _three_files(data_root / "context")
    health = _load("scripts/workspace-health.py", "workspace_health_ut")
    issues = health.check_context_freshness(90)
    out = capsys.readouterr().out

    assert isinstance(issues, int)
    assert "b-broken.md" in out, (
        f"the unreadable context file was never mentioned: {out!r}")
    assert "c-accented.md" in out, (
        "the walk stopped before reaching the file AFTER the broken one, which "
        "is the defect: every later context file went unchecked")
    assert issues >= 1, (
        "an unreadable context file counted as zero issues, so the health "
        "check reports clean over a file it could not open")


def test_the_crm_migration_scan_returns_the_cards_it_could_read(data_root):
    _three_files(data_root / "crm" / "contacts")
    migrate = _load("scripts/crm_migrate_to_entity_model.py", "crm_migrate_ut")
    result = migrate.scan_all_contacts()
    assert isinstance(result, tuple), (
        "scan_all_contacts raised on one undecodable card, so a MIGRATION would "
        "have run over no records at all rather than over the readable ones")
    records, unreadable = result[0], result[1]
    assert any("b-broken.md" in str(u) for u in unreadable), (
        f"the unreadable card was not reported: {unreadable!r}. A record "
        f"silently absent from the scan is a record silently absent from the "
        f"migrated set.")


def test_offboarding_transfers_the_cards_it_can_read_and_names_the_rest(
        data_root, tmp_path, monkeypatch, capsys):
    """The worst moment for this loop to die.

    Driven through the real function with only `_find_exec_contacts` replaced,
    because that is the one call that reaches a repository this test has no
    business creating. Everything after it, including the write, is the
    production path.
    """
    source = _three_files(tmp_path / "departing-exec-contacts")
    offboard = _load("scripts/offboard-exec.py", "offboard_ut")
    monkeypatch.setattr(offboard, "_find_exec_contacts",
                        lambda slug: (source, True))
    offboard.reassign_contacts("departing-exec", "owner-exec-a")
    out = capsys.readouterr().out

    destination = data_root / "crm" / "contacts"
    moved = sorted(p.name for p in destination.glob("*.md"))
    assert "a-clean.md" in moved and "c-accented.md" in moved, (
        f"offboarding lost readable contacts: transferred {moved}. The card "
        f"AFTER the broken one is the one that proves the loop continued.")
    assert "b-broken.md" in out, (
        f"a contact was NOT transferred and the operator was not told: {out!r}")
    assert "NOT transferred" in out, (
        "the skipped count was not stated beside the transferred count, so the "
        "transferred total reads as 'all of them'")


# ---------------------------------------------------------------------------
# The structural half. Cheap, and it fails on the exact edit that reintroduces
# the crash even if someone deleted a behavioural test above.
# ---------------------------------------------------------------------------

FIXED = [
    ("scripts/chronicle.py", "_load_personal_entries"),
    ("scripts/workspace-health.py", "check_context_freshness"),
    ("scripts/crm_migrate_to_entity_model.py", "scan_all_contacts"),
    ("scripts/offboard-exec.py", "reassign_contacts"),
]


@pytest.mark.parametrize("rel,func", FIXED)
def test_the_read_is_inside_a_handler_that_can_catch_a_decode_error(rel, func):
    """Asked of the AST. A grep matches the comment that explains the fix.

    Two things are checked together, because either alone can be satisfied by
    the wrong code: the function must contain a try, and every decode call in
    it must sit inside one whose handler can catch a `ValueError`.
    """
    path = ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == func), None)
    assert node is not None, f"{func} is no longer defined in {rel}"

    catches = set()
    guarded = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Try):
            continue
        for stmt in sub.body:
            for inner in ast.walk(stmt):
                guarded.add(id(inner))
        for handler in sub.handlers:
            if handler.type is None:
                catches.add("<bare>")
                continue
            parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
                     else [handler.type])
            for p in parts:
                if isinstance(p, ast.Name):
                    catches.add(p.id)
                elif isinstance(p, ast.Attribute):
                    catches.add(p.attr)

    reads = [c for c in ast.walk(node)
             if isinstance(c, ast.Call)
             and getattr(c.func, "attr", None) == "read_text"
             and not any(k.arg == "errors" for k in c.keywords)]
    assert reads, (
        f"{rel}::{func} no longer calls read_text; either it was rewritten or "
        f"this test is looking at the wrong shape, and either way it is "
        f"measuring nothing until that is resolved")
    unguarded = [c.lineno for c in reads if id(c) not in guarded]
    assert not unguarded, (
        f"{rel}::{func} decodes a file at line(s) {unguarded} with no try at "
        f"all. One file that is not valid UTF-8 raises out of the whole walk "
        f"again.")
    assert catches & {"UnicodeDecodeError", "ValueError", "UnicodeError"}, (
        f"{rel}::{func} catches {sorted(catches)}. None of those catch a "
        f"UnicodeDecodeError, which is a ValueError.")
