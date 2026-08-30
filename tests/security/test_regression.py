#!/usr/bin/env python3
"""The guard that no security regression test is ever quietly removed.

This file used to be an aggregator: seventeen `from tests.security.test_SEC_...
import *` lines, so that one command ran the whole SEC set. It carried two
defects, and the second is the reason the shape is gone.

* The list was hand-maintained and had fallen behind. It named 14 of the 17
  files on disk; SEC-017, SEC-018 and SEC-019 were added later and never
  appended. Nothing went red, because pytest collects each file by its own
  name anyway - but the control `docs/THREAT-MODEL.md` pointed a reader at,
  `pytest tests/security/test_regression.py`, ran 175 tests and silently
  skipped three findings.

* Completing the list made the second defect visible. Every test it imported
  was ALREADY collected from its own file, so the directory collected each of
  them twice: 721 nodes for 398 distinct tests. That inflated number is what
  README.md, ROADMAP.md and docs/index.html publish as "N security tests",
  derived by `scripts/dev/check-readme-numbers.py` from exactly this
  collection. The repository was advertising 721 checks and running 398.
  Measured 2026-08-26; the inflation was not new, only smaller before.

So the imports are gone and nothing replaced them, because nothing had to: the
CI `security-tests` job runs `tests/security/` as a directory, which is the
real permanent enforcement and always was. `docs/THREAT-MODEL.md` now names
that command.

What DOES need a guard is the rule the old docstring stated and no code
enforced: tests are NEVER removed from this suite. `KNOWN_SEC_IDS` below is an
only-grows floor. A file that vanishes, INCLUDING one renamed out of the
`test_SEC_*.py` glob, fails `test_no_sec_regression_file_has_disappeared`,
because `_sec_files()` stops finding it and its id then shows up as removed. A
file that survives the glob but is emptied of its tests fails
`test_every_sec_file_is_collectable`. Either of those is how a removal would
otherwise look like nothing at all.

This paragraph used to credit `test_every_sec_file_is_collectable` with
catching renames. It cannot see a renamed file at all: it only ever receives
names that already matched the glob, which is also why the
`not name.startswith("test_")` branch inside `_uncollectable` is unreachable
from it and exists purely as unit-test surface.

Numbering note, so the next reader is not misled: these ids are the ENGINE's
own sequence and they do NOT line up with the private SEC registry past 016.
That registry's SEC-017 is "push protection unavailable on private repos",
while this directory's SEC-017 is the dispatch check branches, and its 018/019
have no registry entry. SEC-008 and SEC-009 in the registry are process
findings (dependency pinning, SAST enforcement) closed by CI configuration
rather than by a test, which is why no file here carries those numbers.
"""
import ast
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Every SEC finding that has a regression file in this directory. THIS LIST
# ONLY GROWS. Deleting an entry is deleting the evidence that a test was
# removed, so a change that shrinks it is the change this file exists to stop.
#
# 008 and 009 are absent on purpose: see the numbering note in the docstring.
KNOWN_SEC_IDS = frozenset({
    "001", "002", "003", "004", "005", "006", "007",
    "010", "011", "012", "013", "014", "015", "016",
    "017", "018", "019",
})

_SEC_FILE_GLOB = "test_SEC_*.py"
_SEC_FILE_RE = re.compile(r"^test_SEC_(\d+)_\w+\.py$")


def _sec_files() -> dict:
    """id -> path, for every SEC regression file on disk."""
    found = {}
    for path in sorted(HERE.glob(_SEC_FILE_GLOB)):
        match = _SEC_FILE_RE.match(path.name)
        if match:
            found[match.group(1)] = path
    return found


def _unreadable_names(names) -> list:
    """Glob hits the id regex rejects, which `_sec_files` drops on the floor.

    The glob is WIDER than the regex. `test_SEC_020.py` passes the glob and
    fails the regex (no `_slug` after the digits), so `_sec_files` skipped it
    in silence: its id never entered the found set, `_unregistered` never saw
    it, `_removed` never missed it, and all three guards stayed green over a
    real SEC regression file that nothing in here was counting. That is this
    file's own purpose, pointed the other way.

    Nothing widens the regex, because the `NNN_slug` convention is the thing
    being enforced. What changes is that a disagreement between the two is now
    reported instead of discarded.
    """
    return sorted(n for n in names if not _SEC_FILE_RE.match(n))


# The three comparisons live as free functions so each can be driven with input
# that FAILS. Inline, none of them could be: the corpus is correct today, so an
# assertion that compared nothing at all would pass exactly the same way. The
# mutation harness proved that on the first version of this file, where
# replacing the comparison with a hardcoded empty result survived untouched.

def _removed(known, on_disk) -> list:
    return sorted(set(known) - set(on_disk))


def _removal_report(known, on_disk) -> str:
    """The failure message, or "" when nothing is wrong.

    The empty-corpus case is separate from the removed-files case on purpose,
    and it is a function rather than a bare `assert` above the comparison for
    the usual reason: as an assert it could not be driven, and a mutation that
    deleted it survived. An empty glob would otherwise be reported as "all 17
    files removed", which sends the reader to restore files that never left.
    """
    if not on_disk:
        return "no SEC regression files found at all; the directory moved"
    gone = _removed(known, on_disk)
    if gone:
        return (f"SEC regression file(s) removed: {gone}. Security tests are "
                f"never deleted. Restore them, or bring the removal to the "
                f"operator.")
    return ""


def _unregistered(known, on_disk) -> list:
    return sorted(set(on_disk) - set(known))


def _collectable_items(body: str) -> int:
    """How many tests pytest would actually collect from this source.

    This was a regex, `^\\s*(?:async def|def) test_\\w+|^class Test\\w+`, and
    the second alternation counted the mere PRESENCE of a `Test*` class as
    collectable. pytest runs zero tests out of `class TestThing: pass`, and
    zero out of a `Test*` class that defines `__init__`. So a SEC file whose
    tests were deleted and replaced with an empty `Test` shell passed
    `test_every_sec_file_is_collectable` while contributing nothing, which is
    one of the two removal disguises this file names. `_uncollectable`'s own
    docstring ("pytest would run nothing out of this file") was the correct
    side; the regex was wrong, and the pinned assertion below cemented it.

    Counting through the AST answers the question the docstring asks. A file
    that does not parse also collects nothing, and says so loudly at
    collection time, so it counts as zero here rather than raising.

    `python_files = ["test_*.py"]`, `python_functions = ["test_*"]` and the
    default `python_classes = ["Test*"]` in pyproject.toml are the rules being
    modelled.
    """
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return 0
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            count += node.name.startswith("test_")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            members = [m for m in node.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if any(m.name == "__init__" for m in members):
                continue  # pytest refuses to collect a class with a constructor
            count += sum(m.name.startswith("test_") for m in members)
    return count


def _uncollectable(name: str, body: str) -> bool:
    """True when pytest would run nothing out of this file."""
    return not name.startswith("test_") or _collectable_items(body) == 0


def test_no_sec_regression_file_has_disappeared():
    """The whole point of the file. A removed test must fail the suite."""
    assert _removal_report(KNOWN_SEC_IDS, set(_sec_files())) == ""


def test_a_new_sec_file_joins_the_known_set():
    """Otherwise the floor stops rising and stops protecting anything new."""
    extra = _unregistered(KNOWN_SEC_IDS, set(_sec_files()))

    assert not extra, f"add these ids to KNOWN_SEC_IDS in {Path(__file__).name}: {extra}"


def test_every_sec_file_is_collectable():
    """Present on disk is not the same as running."""
    dead = [path.name for path in sorted(_sec_files().values())
            if _uncollectable(path.name, path.read_text(encoding="utf-8"))]

    assert not dead, f"SEC file(s) pytest will run nothing out of: {dead}"


def test_the_removal_check_can_actually_fail():
    assert _removed({"001", "002"}, {"001", "002"}) == []
    assert _removed({"001", "002"}, {"001"}) == ["002"]


def test_the_two_failure_modes_read_differently():
    """A vanished directory must not be reported as seventeen deletions."""
    assert _removal_report({"001", "002"}, {"001", "002"}) == ""
    assert "removed: ['002']" in _removal_report({"001", "002"}, {"001"})
    assert "the directory moved" in _removal_report({"001", "002"}, set())


def test_the_new_file_check_can_actually_fail():
    assert _unregistered({"001"}, {"001"}) == []
    assert _unregistered({"001"}, {"001", "020"}) == ["020"]


def test_the_collectability_check_can_actually_fail():
    """Each way a file can sit here and contribute nothing."""
    live = "def test_a_thing():\n    assert True\n"

    assert not _uncollectable("test_SEC_001_x.py", live)
    assert not _uncollectable("test_SEC_001_x.py", "async def test_a_thing():\n    pass\n")
    # A Test class that actually holds a test.
    assert not _uncollectable(
        "test_SEC_001_x.py", "class TestThing:\n    def test_a_thing(self):\n        pass\n")
    # Renamed out of the collection pattern.
    assert _uncollectable("SEC_001_x.py", live)
    # Every test function renamed away.
    assert _uncollectable("test_SEC_001_x.py", "def check_a_thing():\n    pass\n")
    # Helpers only.
    assert _uncollectable("test_SEC_001_x.py", "def _test_helper():\n    pass\n")
    # An empty Test shell. This line used to assert the OPPOSITE, which pinned
    # the defect: pytest collects nothing from it.
    assert _uncollectable("test_SEC_001_x.py", "class TestThing:\n    pass\n")
    # A Test class with a constructor: pytest warns and collects nothing.
    assert _uncollectable(
        "test_SEC_001_x.py",
        "class TestThing:\n    def __init__(self):\n        pass\n"
        "    def test_a_thing(self):\n        pass\n")
    # A file that does not parse runs nothing either.
    assert _uncollectable("test_SEC_001_x.py", "def test_a_thing(:\n")


def test_no_sec_file_is_invisible_to_the_id_regex():
    """A glob hit the regex rejects is counted by nothing. Say so."""
    names = [p.name for p in sorted(HERE.glob(_SEC_FILE_GLOB))]
    assert names, "no SEC regression files found at all; the directory moved"
    unreadable = _unreadable_names(names)

    assert not unreadable, (
        f"SEC file(s) matched {_SEC_FILE_GLOB} but not the NNN_slug convention, "
        f"so _sec_files() ignores them and no guard here counts them: "
        f"{unreadable}. Rename to test_SEC_NNN_<slug>.py.")


def test_the_invisible_file_check_can_actually_fail():
    assert _unreadable_names(["test_SEC_001_x.py"]) == []
    assert _unreadable_names(["test_SEC_020.py"]) == ["test_SEC_020.py"]
    assert _unreadable_names(
        ["test_SEC_001_x.py", "test_SEC_020.py"]) == ["test_SEC_020.py"]
