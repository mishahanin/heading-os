#!/usr/bin/env python3
"""One timestamp in one fixture took the whole parallel test gate down.

`tests/test_a_scan_that_called_trojan_source_clean.py` built a gzip body inside
a `@pytest.mark.parametrize` decorator. A decorator argument is evaluated at
MODULE IMPORT, once per process, and `gzip.compress` writes the current epoch
second into its header at byte offset 4. Pytest derives a parametrize id from
those bytes.

Under `-n auto` each xdist worker imports the module a moment apart, so the
workers built DIFFERENT ids for that one case and xdist aborted the entire run:

    ERROR gw9 - Different tests were collected between gw0 and gw9.

MEASURED 2026-09-01, collecting that one file in two separate processes 1.2s
apart and hashing the matching id lines:

    before   71aa8fbe...   8f5988ea...     two processes, two different ids
    after    bb5f94d1...   bb5f94d1...     identical

and directly: `gzip.compress(x) != gzip.compress(x)` across 1.1s, first
differing byte at offset 4; with `mtime=0` the two are byte-identical.

Two things make this worth a guard rather than a one-line diff.

The error MISLEADS. It names a collection mismatch, which reads as a
nondeterministic conftest or a corpus being written underneath the collector,
and both of those are real causes that produce the identical message. The
coordinator diagnosed the second one, measured it correctly, fixed the symptom
with a retry, and never suspected there was also a first one. A shard auditor
found it hours later.

And it is INVISIBLE serially. `pytest` on one process collects one id and is
perfectly green, so the whole class only appears under parallelism, which is
exactly where nobody reads the collection output.

What this file does NOT claim: it cannot catch every nondeterministic id. A
decorator argument built from `time.time()`, a `uuid4()`, a set iteration order
or a temp path would all do the same and are not detectable by this shape. The
general invariant is "collecting twice yields the same ids", which is a slow
whole-suite check; this is the cheap, specific jaw for the one shape that
actually bit, and it says so rather than implying more.
"""
from __future__ import annotations

import ast
import gzip
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

SUBJECT = ROOT / "tests" / "test_a_scan_that_called_trojan_source_clean.py"


def test_gzip_really_does_embed_the_clock(tmp_path):
    """Bind the rule to the library, not to a claim about the library.

    Without this, every assertion below rests on a sentence in a docstring. If
    a future CPython stopped writing the timestamp, this test would fail and
    tell the reader the guard is now unnecessary, which is the right outcome.
    """
    payload = b"caf\xe9 not utf-8"
    first = gzip.compress(payload)
    time.sleep(1.1)
    second = gzip.compress(payload)

    assert first != second, (
        "gzip.compress no longer varies with the clock, so the defect this "
        "file guards cannot occur and the guard should be retired")
    assert first[4:8] != second[4:8], (
        "the bytes differ somewhere other than the mtime field, so the "
        "measurement recorded in this file's docstring is wrong")
    assert gzip.compress(payload, mtime=0) == gzip.compress(payload, mtime=0), (
        "mtime=0 is no longer deterministic, so the fix does not fix it")


def _decorator_calls(tree: ast.AST):
    """Every Call inside a decorator, which is what runs at import time."""
    for node in ast.walk(tree):
        for deco in getattr(node, "decorator_list", []):
            yield from (c for c in ast.walk(deco) if isinstance(c, ast.Call))


def test_no_decorator_builds_a_gzip_body_without_pinning_the_clock():
    """The structural jaw, over the WHOLE test tree, not one file.

    Scoped to decorators on purpose. The same call inside a function body runs
    at test time and its bytes never reach an id, so flagging those would be a
    false positive that teaches the reader to ignore this test. Two such calls
    exist in this tree today and are correct.
    """
    offenders = []
    scanned = 0
    # `tracked_paths`, not a bare glob. A hand-written sweep of the repo root
    # also walks whatever an agent left under `.claude/worktrees/`, which
    # doubles the corpus and makes the floor below meaningless. The rule is
    # enforced by `tests/test_a_walker_that_never_asked_git.py`, which caught
    # this file's first draft doing exactly that.
    for path in sorted(tracked_paths(["tests/**/*.py"])):
        scanned += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            offenders.append(f"{path.relative_to(ROOT)}: unreadable ({exc})")
            continue
        for call in _decorator_calls(tree):
            if getattr(call.func, "attr", None) != "compress":
                continue
            if not any(k.arg == "mtime" for k in call.keywords):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{call.lineno} gzip.compress in a "
                    f"decorator with no mtime=")

    assert scanned >= 200, (
        f"only {scanned} test file(s) scanned; this guard is green over an "
        f"empty corpus and measures nothing until that is resolved")
    assert not offenders, (
        "a decorator argument carries a clock-dependent gzip body:\n  "
        + "\n  ".join(offenders)
        + "\nEach xdist worker will build a different parametrize id and the "
          "whole parallel run aborts with 'Different tests were collected'.")


def test_the_detector_can_actually_fire(tmp_path):
    """The negative case. A guard with no case ON the line is not a guard.

    Written against a synthetic file rather than the tree, so it proves the
    predicate fires without needing a real offender to exist.
    """
    offending = tmp_path / "test_offender.py"
    offending.write_text(
        "import gzip, pytest\n"
        "@pytest.mark.parametrize('b', [gzip.compress(b'x')])\n"
        "def test_x(b): pass\n", encoding="utf-8")
    clean = tmp_path / "test_clean.py"
    clean.write_text(
        "import gzip, pytest\n"
        "@pytest.mark.parametrize('b', [gzip.compress(b'x', mtime=0)])\n"
        "def test_x(b): pass\n"
        "def test_y():\n"
        "    return gzip.compress(b'x')\n", encoding="utf-8")

    def flagged(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return [c.lineno for c in _decorator_calls(tree)
                if getattr(c.func, "attr", None) == "compress"
                and not any(k.arg == "mtime" for k in c.keywords)]

    assert flagged(offending) == [2], (
        "the detector did not flag a decorator gzip body with no mtime, so the "
        "green result above means nothing")
    assert flagged(clean) == [], (
        "the detector flagged either a pinned decorator call or a call inside "
        "a function body; the second is correct code and flagging it would "
        "train the reader to ignore this test")


@pytest.mark.slow
def test_collecting_the_subject_twice_yields_the_same_ids():
    """The behavioural jaw. Asks pytest, not the AST.

    This is the property that actually matters, and it would catch a
    clock-dependent id built by any means, not only by gzip. Marked slow
    because it spawns two collections a second apart; the AST jaw above runs in
    the fast lane and covers the one shape that bit.
    """
    def ids() -> list[str]:
        """Only the id lines.

        The first draft of this compared the whole stdout and failed on
        pytest's own "collected in 0.40s" versus "0.47s" footer, which is a
        test measuring the wall clock rather than the thing it is named for.
        Keeping only lines that carry the `::` of a node id is what makes this
        an assertion about collection.
        """
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(SUBJECT),
             "--collect-only", "-q", "--no-header", "-p", "no:randomly"],
            cwd=str(ROOT), capture_output=True, text=True, errors="replace",
            timeout=300)
        lines = [ln.strip() for ln in proc.stdout.splitlines() if "::" in ln]
        # The exit status is REPORTED, never asserted on. This child is rooted
        # at the real repository, and the root conftest's `pytest_sessionfinish`
        # sets `session.exitstatus = 1` whenever the operator's live overlay
        # gains a file during the run. Nothing in this test causes that, but a
        # concurrent agent or a checkpoint hook does, and the first draft of
        # this helper asserted `returncode == 0` and would have gone red for a
        # reason that has nothing to do with parametrize ids. The jaw that
        # actually matters is the one below: ids were parsed at all.
        assert lines, (
            f"no node ids were parsed out of the collection output, so a "
            f"comparison of two empty lists would pass over anything. "
            f"Child exit status was {proc.returncode}.\n{proc.stdout}")
        return lines

    first = ids()
    time.sleep(1.2)
    second = ids()

    assert first == second, (
        "collecting the same file twice, 1.2s apart, produced different test "
        "ids. Under -n auto the workers disagree and xdist aborts the run. "
        f"Differing: {sorted(set(first) ^ set(second))}")
