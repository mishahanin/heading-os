"""Tests for R16 H1 -- publish-corporate.py --bump-build BUILD.json increment.

The bump is additive (a new mode; default --preview/--copy/--verify unchanged).
Loads the kebab-case script via importlib and points its CORPORATE_ROOT at a
temp dir so the real corporate repo is never touched.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))


def _load():
    spec = importlib.util.spec_from_file_location(
        "publish_corp_mod", WORKSPACE / "scripts" / "publish-corporate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def M(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "CORPORATE_ROOT", tmp_path)
    # The publisher is stamped through the operator seam; pin it deterministically
    # so the assertion holds regardless of the ambient operator identity
    # (established host vs data-less CI clone).
    monkeypatch.setattr(mod, "operator_slug", lambda: "misha-hanin")
    return mod


def _build(mod):
    return json.loads((mod.CORPORATE_ROOT / "BUILD.json").read_text(encoding="utf-8"))


def test_patch_bump(M):
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.56.0", "build": 89}), encoding="utf-8")
    assert M.bump_build(summary="content tweak") == 0
    b = _build(M)
    assert b["build"] == 90
    assert b["version"] == "1.56.1"          # PATCH
    assert b["summary"] == "content tweak"
    assert b["publisher"] == "misha-hanin"
    assert "timestamp" in b


def test_structural_bump_is_minor(M):
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.56.0", "build": 89}), encoding="utf-8")
    assert M.bump_build(structural=True) == 0
    b = _build(M)
    assert b["build"] == 90
    assert b["version"] == "1.57.0"          # MINOR


def test_preserves_history(M):
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.0.0", "build": 5,
                    "history": [{"event": "force-promote", "build": 4}]}),
        encoding="utf-8")
    M.bump_build()
    b = _build(M)
    assert b["build"] == 6
    assert b["history"] == [{"event": "force-promote", "build": 4}]


def test_initialises_when_absent(M):
    assert M.bump_build() == 0
    b = _build(M)
    assert b["build"] == 1
    assert b["version"] == "0.0.1"


# ============================================================
# Three properties the four tests above did not measure
#
# MEASURED 2026-09-01 by mutation, against this file and three neighbours
# (test_build_number_reporting_is_honest.py, test_a_typo_that_published_the_
# ceos_outputs.py, test_atomic_scripts.py). Each of these edits left the whole
# set green:
#
#   the `files_changed` pop disabled      -> the previous build's count is
#                                            republished under the new number
#   tmp + replace made a direct write     -> a crash mid-write truncates the
#                                            file the fleet compares against
#   the corrupt-file handler removed      -> unreachable anyway (see below)
# ============================================================

def test_a_previous_builds_file_count_is_never_republished(M):
    """`payload = dict(cur)` carries every key forward, which is what lets a new
    BUILD.json keep an unrelated key. `files_changed` is the one key where that
    is wrong: it is a measurement of ONE publish, and `--bump-build` is mutually
    exclusive with `--copy`, so a bump never counts anything.

    Carrying it forward is worse than the zero the code used to stamp: a zero is
    obviously nothing, while last build's 47 reads as this build's 47.
    """
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.0.0", "build": 5, "files_changed": 47,
                    "history": [{"event": "force-promote", "build": 4}]}),
        encoding="utf-8")
    assert M.bump_build(summary="a bump that counted nothing") == 0
    b = _build(M)
    assert "files_changed" not in b, (
        f"build {b['build']} republished the previous build's count: {b}")
    # and the carry-forward the pop must not have broken
    assert b["history"] == [{"event": "force-promote", "build": 4}]


def test_an_explicit_file_count_is_written(M):
    """The other side of the pop, so it cannot be satisfied by never writing the
    key at all."""
    assert M.bump_build(files_changed=3) == 0
    assert _build(M)["files_changed"] == 3


def test_a_failed_replace_leaves_the_previous_build_intact(M, monkeypatch):
    """Atomicity, asserted as a side effect rather than as a spelling.

    The write goes to `BUILD.json.tmp` and is moved onto `BUILD.json` with
    `Path.replace`, so a process killed mid-write leaves the old file whole. A
    direct `build_path.write_text` passes every other test in this file and
    fails here: with `replace` made to raise, the atomic form has not touched
    `BUILD.json` yet, while the direct form has already overwritten it AND never
    calls `replace`, so it returns success over a file that was rewritten by a
    run this test asked to fail.
    """
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.56.0", "build": 89}), encoding="utf-8")
    before = (M.CORPORATE_ROOT / "BUILD.json").read_text(encoding="utf-8")

    def boom(self, target):
        raise OSError("simulated interruption between the write and the move")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated interruption"):
        M.bump_build(summary="interrupted")
    assert (M.CORPORATE_ROOT / "BUILD.json").read_text(encoding="utf-8") == before, (
        "BUILD.json was written in place; an interrupted bump truncates the "
        "number the whole fleet compares against")


@pytest.mark.parametrize("payload,label", [
    (b'{"version": "1.0.0", "build": 5,', "truncated JSON"),
    (b'{"version": "1.0.0", "build": 5, "s": "caf\xe9"}', "a byte that is not UTF-8"),
    (b'["not", "an", "object"]', "valid JSON that is not an object"),
])
def test_an_unreadable_build_file_refuses_rather_than_restarting_the_counter(
        M, capsys, payload, label):
    """ABSENT and CORRUPT are different answers.

    `except json.JSONDecodeError: cur = {}` gave them the same one, so a damaged
    file restarted the counter at build 1 / version 0.0.1 with a GREEN exit.
    `scripts/check-build.py` compares this number against the copy each exec
    holds, so that reset does not quietly lose an audit trail: it tells every
    executive in the fleet they are ahead of the publisher.

    The non-UTF-8 case is the one the old handler could not even see.
    `UnicodeDecodeError` is a ValueError and a sibling of `json.JSONDecodeError`,
    raised inside `read_text` before any parsing, so it escaped the try entirely.
    MEASURED 2026-09-01: `bump_build` raised UnicodeDecodeError out of the
    function whose author had written a handler for exactly this case.
    """
    build = M.CORPORATE_ROOT / "BUILD.json"
    build.write_bytes(payload)

    assert M.bump_build(summary="over a corrupt file") == 1, label
    assert "REFUSING TO BUMP" in capsys.readouterr().out
    assert build.read_bytes() == payload, (
        f"the refusal still rewrote BUILD.json ({label})")
