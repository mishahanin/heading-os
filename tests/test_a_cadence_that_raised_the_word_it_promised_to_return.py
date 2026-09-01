#!/usr/bin/env python3
"""`marker_state` enumerates "unreadable" and raised on the unreadable case.

`scripts/odin-cadence.py` decides whether the operator gets nudged to run an
Odin collect pass, and the sentence it produces goes verbatim to Telegram. Four
of its readers did `exists()` then `read_text()` with no `try` at all.

The decode half was closed earlier by `errors="replace"`, so a bad BYTE could no
longer raise. The OSError half was not, and it is the sharper one here because
of what one of these functions promises. `marker_state`'s first docstring line
is the enumeration:

    "absent" | "ok" | "unreadable" for `.last-collect`

MEASURED 2026-09-01 with the marker file at mode 000:

    read_marker           RAISED PermissionError  -> (None, None)
    marker_state          RAISED PermissionError  -> "unreadable"
    read_reflect_marker   RAISED PermissionError  -> None
    count_threads         RAISED on any thread    -> skips it and NAMES it

A function that enumerates its return values in its own first line, and then
raises on one of the three, is the broken-promise shape this campaign is built
around. It is worse than an ordinary crash: the caller reads the contract, sees
that "unreadable" is handled, and writes no handler.

Degradation direction, stated because it is a choice and not an accident. Every
one of these degrades toward COUNTING MORE and REVIEWING MORE, never toward
silence: an unreadable collect marker falls through to `EPOCH_FLOOR`, which
counts every entry, and an unreadable reflect marker makes the cluster signal
treat material as UNREVIEWED. Being wrong in that direction costs the operator
a nudge they did not need. The other direction costs them a nudge they did.

`count_threads` is the walker, and it NAMES what it skipped. A dropped thread
that nothing reports turns the count into a lower bound the caller reads as a
total, and this count is what the Telegram message quotes.

Found by a shard auditor which measured it and deliberately did NOT fix it,
because another agent was editing the file at that moment. Picked up here once
the file had been quiet for eight hours. Recording that, because "reported and
left" is the step where a finding usually disappears.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CADENCE = ROOT / "scripts" / "odin-cadence.py"

skip_if_root = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="mode 000 does not block root, so an unreadable file cannot be "
           "staged on this account")


@pytest.fixture()
def cadence(tmp_path, monkeypatch) -> types.ModuleType:
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    spec = importlib.util.spec_from_file_location("odin_cadence_ut", CADENCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unreadable(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("2026-01-01\n", encoding="utf-8")
    os.chmod(p, 0o000)
    return p


# ---------------------------------------------------------------------------
# The promise, and the three readers
# ---------------------------------------------------------------------------

@skip_if_root
def test_marker_state_returns_the_word_it_enumerates(cadence, tmp_path):
    """The headline. The contract names this exact case and it raised."""
    _unreadable(tmp_path, cadence.MARKER)

    assert cadence.marker_state(tmp_path) == "unreadable", (
        "marker_state did not answer 'unreadable' for a marker it could not "
        "read, which is the one value its own first docstring line promises "
        "for this input")


@skip_if_root
def test_the_collect_marker_reader_degrades_toward_counting_everything(
        cadence, tmp_path):
    """`(None, None)` is not merely 'no crash'; it selects a safe floor.

    The caller reads None as "no marker", falls through to `EPOCH_FLOOR`, and
    counts every entry. Asserting the exact shape rather than just absence of
    an exception is what pins that direction.
    """
    _unreadable(tmp_path, cadence.MARKER)

    assert cadence.read_marker(tmp_path) == (None, None), (
        "read_marker did not degrade to (None, None), so an unreadable marker "
        "no longer falls through to the count-everything floor")


@skip_if_root
def test_the_reflect_marker_reader_degrades_toward_unreviewed(
        cadence, tmp_path):
    _unreadable(tmp_path, cadence.REFLECT_MARKER)

    assert cadence.read_reflect_marker(tmp_path) is None, (
        "read_reflect_marker raised instead of returning None, so an "
        "unreadable reflect marker takes the cadence run down rather than "
        "making the cluster signal treat material as unreviewed")


# ---------------------------------------------------------------------------
# The three states must stay THREE states
# ---------------------------------------------------------------------------

def test_absent_and_ok_are_still_distinct_from_unreadable(cadence, tmp_path):
    """The anchor against over-refusal, and it is the important one.

    A "fix" that returned "unreadable" unconditionally would satisfy every
    case above while making the nudge say a marker is broken on every healthy
    install. All three values are asserted together, in one test, because what
    matters is that they DIFFER.
    """
    marker = tmp_path / cadence.MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)

    assert cadence.marker_state(tmp_path) == "absent"
    marker.write_text("2026-01-01\n", encoding="utf-8")
    assert cadence.marker_state(tmp_path) == "ok"
    marker.write_text("not a date at all\n", encoding="utf-8")
    assert cadence.marker_state(tmp_path) == "unreadable", (
        "a corrupt-but-readable marker stopped reporting unreadable, which is "
        "the case this function was originally written for")


def test_a_healthy_marker_still_reports_its_date(cadence, tmp_path):
    marker = tmp_path / cadence.MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("2026-01-01\n", encoding="utf-8")

    stamp, days = cadence.read_marker(tmp_path)
    assert stamp == "2026-01-01", f"a healthy marker was discarded: {stamp!r}"
    assert isinstance(days, int) and days >= 0


# ---------------------------------------------------------------------------
# The walker names what it lost
# ---------------------------------------------------------------------------

@skip_if_root
def test_an_unreadable_thread_is_skipped_and_named(cadence, tmp_path, capsys):
    """A count quoted to the operator must not silently be a lower bound."""
    base = tmp_path / "threads" / "business"
    base.mkdir(parents=True)
    good = ("---\ntype: business\nclassification: ceo-only\n---\n\n"
            "- 2026-06-01 something happened\n")
    (base / "a-readable.md").write_text(good, encoding="utf-8")
    (base / "c-also-readable.md").write_text(good, encoding="utf-8")
    blocked = base / "b-blocked.md"
    blocked.write_text(good, encoding="utf-8")
    os.chmod(blocked, 0o000)

    try:
        count = cadence.count_threads(tmp_path, "2026-01-01")
    finally:
        os.chmod(blocked, 0o644)
    err = capsys.readouterr().err

    assert isinstance(count, int), (
        "count_threads raised on one unreadable thread, so the whole cadence "
        "count died on a file it did not need")
    assert "b-blocked.md" in err, (
        f"the skipped thread was not named on any stream, so the count it "
        f"returned reads as a total when it is a lower bound: {err!r}")
    assert "c-also-readable.md" not in err, (
        "the walk complained about a thread it could read, which means the "
        "skip is firing too widely")


def test_a_healthy_thread_tree_is_counted_in_silence(cadence, tmp_path, capsys):
    """Clean-path anchor. A warning on every healthy run is noise."""
    base = tmp_path / "threads" / "business"
    base.mkdir(parents=True)
    (base / "a.md").write_text(
        "---\ntype: business\nclassification: ceo-only\n---\n\n"
        "- 2026-06-01 something happened\n", encoding="utf-8")

    cadence.count_threads(tmp_path, "2026-01-01")

    assert capsys.readouterr().err == "", (
        "a healthy thread tree produced a warning")


# ---------------------------------------------------------------------------
# Structural: all four, so a fifth reader added later inherits the rule
# ---------------------------------------------------------------------------

# The four the shard auditor named, plus the three its list missed and the
# floor below found. `count_viraid` was ALREADY correct; it is listed because
# this file's floor asks about every reader in the module, not only the broken
# ones, and an exemption that is not written down is an exemption nobody can
# check.
GUARDED = ["read_marker", "marker_state", "read_reflect_marker",
           "count_threads", "count_crm", "count_viraid",
           "analyze_reflect_clusters"]


@pytest.mark.parametrize("func", GUARDED)
def test_the_read_sits_under_a_handler_that_can_catch_an_oserror(func):
    """Asked of the AST. A grep matches the comment explaining the fix.

    Two things together, because either alone is satisfied by the wrong code:
    the function must contain a try, and every read in it must sit inside one
    whose handler can catch an `OSError`.
    """
    tree = ast.parse(CADENCE.read_text(encoding="utf-8"), filename=str(CADENCE))
    node = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == func), None)
    assert node is not None, f"{func} is no longer defined in odin-cadence.py"

    guarded: set[int] = set()
    catches: set[str] = set()
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
             and getattr(c.func, "attr", None) == "read_text"]
    assert reads, (
        f"{func} no longer calls read_text, so this test is looking at the "
        f"wrong shape and measures nothing until that is resolved")

    unguarded = [c.lineno for c in reads if id(c) not in guarded]
    assert not unguarded, (
        f"{func} reads a file at line(s) {unguarded} with no try at all")
    assert catches & {"OSError", "EnvironmentError", "Exception", "<bare>"}, (
        f"{func} catches {sorted(catches)}, none of which catch an OSError")


def test_every_reader_in_the_module_is_covered_by_this_file():
    """A floor, so a fifth reader cannot arrive unguarded and unnoticed.

    Derived from the module rather than from the list above: a new function
    doing `exists()` then `read_text()` fails here until someone decides
    whether it belongs in GUARDED.
    """
    tree = ast.parse(CADENCE.read_text(encoding="utf-8"), filename=str(CADENCE))
    readers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(isinstance(c, ast.Call)
               and getattr(c.func, "attr", None) == "read_text"
               for c in ast.walk(node)):
            readers.add(node.name)

    assert len(readers) >= 4, (
        f"only {sorted(readers)} read a file in odin-cadence.py; this guard "
        f"is measuring less than it did when it was written")
    missing = readers - set(GUARDED)
    assert not missing, (
        f"{sorted(missing)} read a file and are not covered by this file. "
        f"Add them to GUARDED once you have decided which way each should "
        f"degrade, rather than widening the list to make this pass.")
