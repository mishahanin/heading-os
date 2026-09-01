#!/usr/bin/env python3
"""A tree sweep gathers its paths, then reads them. Files move in between.

Seven sweeps under `tests/` walked the repository, collected a path list, and
read each entry with a bare `path.read_text(...)`. In a workspace where several
agents work against one checkout, a file can be created and deleted inside that
window.

MEASURED 2026-08-30: an agent's scratch file,
`tests/test_turn_check_empty_masked_real_fixture.py`, existed when
`tests/test_subprocess_interpreter_guard.py` gathered its corpus and was gone
when it read it. The suite failed with

    FAILED tests/test_subprocess_interpreter_guard.py::
        test_no_bare_python_interpreter_in_spawned_commands
    FileNotFoundError: .../tests/test_turn_check_empty_masked_real_fixture.py

A crash inside the guard, presented as though the guard had caught something.
Nothing was violated.

Silently swallowing the miss is the other half of the same defect: the sweep
would then hold its verdict over a corpus that shrank underneath it and say
nothing, which `.claude/rules/scope-claims.md` forbids. So `read_sources` skips
the vanished path, WARNS naming it, and hands the caller a list to put in its
own message.

The direction that matters just as much is the second one: a file that is
genuinely there and genuinely violating must still fail the guard. A fix that
turns a crash into a pass is worse than the crash.

Run:
    .venv/bin/python -m pytest \\
        tests/test_a_guard_that_crashed_on_a_file_that_vanished_mid_walk.py \\
        -q --no-header -p no:randomly
"""
from __future__ import annotations

import ast
import functools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

SPAWNER = "import subprocess\nsubprocess.run(['python3', 'x.py'])\n"
CLEAN = "import subprocess\nimport sys\nsubprocess.run([sys.executable, 'x.py'])\n"


def _corpus(tmp_path: Path, **files: str) -> list[Path]:
    """Write a corpus and return the gathered path list, as a walk would."""
    made = []
    for name, body in files.items():
        p = tmp_path / f"{name}.py"
        p.write_text(body, encoding="utf-8")
        made.append(p)
    return sorted(made)


# ============================================================
# The measured failure
# ============================================================


def test_a_path_that_vanishes_between_the_walk_and_the_read_does_not_crash(tmp_path):
    """The exact race: gathered while present, read after deletion."""
    a, b, c = _corpus(tmp_path, a=CLEAN, b=CLEAN, c=CLEAN)
    walked = [a, b, c]

    b.unlink()  # a parallel agent removes its scratch file

    with pytest.warns(UserWarning):
        got = [p for p, _ in read_sources(walked)]

    assert got == [a, c], "the survivors must still be read"


def test_the_vanished_path_is_reported_not_silently_dropped(tmp_path):
    """A sweep whose corpus shrank in silence claims coverage it does not have."""
    a, b = _corpus(tmp_path, a=CLEAN, b=CLEAN)
    b.unlink()

    vanished: list[Path] = []
    with pytest.warns(UserWarning, match="vanished between the walk and the read"):
        list(read_sources([a, b], vanished))

    assert vanished == [b], "the caller must be able to name what it did not read"


def test_the_warning_names_the_file(tmp_path):
    """"One path was skipped" is not a report. The operator needs the name."""
    a, b = _corpus(tmp_path, a=CLEAN, b=CLEAN)
    b.unlink()

    with pytest.warns(UserWarning) as caught:
        list(read_sources([a, b]))

    assert str(b) in str(caught[0].message)


def test_a_complete_walk_warns_about_nothing(tmp_path):
    """The ordinary case must stay silent, or the warning becomes noise nobody
    reads - and a warning nobody reads is the silence this test forbids."""
    walked = _corpus(tmp_path, a=CLEAN, b=CLEAN)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = [p for p, _ in read_sources(walked)]

    assert got == walked


def test_every_surviving_file_is_read_in_full(tmp_path):
    """Skipping must not truncate: the text handed over is the file's own."""
    a, b = _corpus(tmp_path, a=SPAWNER, b=CLEAN)
    ghost = tmp_path / "ghost.py"
    ghost.write_text(CLEAN, encoding="utf-8")
    walked = sorted([a, b, ghost])
    ghost.unlink()

    with pytest.warns(UserWarning):
        pairs = dict(read_sources(walked))

    assert pairs[a] == SPAWNER
    assert pairs[b] == CLEAN


# ============================================================
# The other direction: the guard must still catch a real violation
# ============================================================


def test_a_present_violating_file_is_still_caught(tmp_path):
    """A fix that turns a crash into a pass is worse than the crash.

    Same composition the guard runs: read the corpus, parse it, detect. The
    violating file is present the whole time and must be reported.
    """
    guard = _load_guard()
    a, b = _corpus(tmp_path, a=SPAWNER, b=CLEAN)

    violations = []
    for path, source in read_sources([a, b]):
        for lineno, name in guard._bare_interpreter_calls(ast.parse(source)):
            violations.append((path.name, lineno, name))

    assert violations == [("a.py", 2, "python3")]


def test_a_violation_survives_a_vanished_neighbour(tmp_path):
    """The skip must not swallow the finding sitting next to it."""
    guard = _load_guard()
    a, b = _corpus(tmp_path, a=SPAWNER, b=CLEAN)
    b.unlink()

    violations = []
    with pytest.warns(UserWarning):
        for path, source in read_sources([a, b]):
            for lineno, name in guard._bare_interpreter_calls(ast.parse(source)):
                violations.append((path.name, lineno, name))

    assert violations == [("a.py", 2, "python3")]


def _load_guard():
    """The real guard module, imported by name."""
    import importlib

    return importlib.import_module("tests.test_subprocess_interpreter_guard")


# ============================================================
# Not over-caught: a real fault about a file that IS there still raises
# ============================================================


def test_a_directory_handed_in_where_a_file_was_expected_still_raises(tmp_path):
    """`except FileNotFoundError`, never `except OSError`. A path that exists and
    cannot be read is a genuine fault and must not be filed under "vanished"."""
    directory = tmp_path / "adir"
    directory.mkdir()

    with pytest.raises(IsADirectoryError):
        list(read_sources([directory]))


def test_a_decoding_failure_still_raises(tmp_path):
    """Strict by default: a file whose bytes are not UTF-8 is a real finding
    about a file that is present, not a file that went away."""
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28\n")

    with pytest.raises(UnicodeDecodeError):
        list(read_sources([bad]))


def test_errors_replace_is_available_for_the_sweep_that_wants_it(tmp_path):
    """One sweep reads SKILL.md with `errors="replace"`; the helper must keep
    that behaviour rather than silently tighten it."""
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"head \xc3\x28 tail\n")

    pairs = dict(read_sources([bad], errors="replace"))
    assert "head" in pairs[bad] and "tail" in pairs[bad]


# ============================================================
# The sweeps that had the hole, asked of the AST
# ============================================================
#
# WHAT WAS HERE UNTIL 2026-09-01, AND WHY IT COULD NOT WORK.
#
# A tuple of seven file names, each checked with `"read_sources(" in source`.
# Two independent failures, and the comment above it described an AST pass that
# had been run by hand once and never encoded:
#
#   1. A HAND-MAINTAINED LIST detects reversion in seven known files and in
#      nothing else. Measured 2026-09-01: 145 unprotected sites were live across
#      89 files while this guard was green, including
#      `tests/test_a_wall_that_answered_about_the_spelling.py`, which had been
#      converted hours earlier and was never added to the list. A list a human
#      maintains beside the thing it describes is the half that falls behind.
#   2. A SUBSTRING OVER THE WHOLE FILE is satisfied by a comment, by a
#      docstring, or by one adopted loop in a module whose other three loops
#      still hand-roll `read_text`. It asks whether the words are present, not
#      whether the read is protected.
#
# The replacement asks the structure. For every module under `tests/`,
# `scripts/` and `.claude/`, a read of a name BOUND BY A LOOP over a
# repository-wide walk must sit inside a `try` whose handler names OSError or
# FileNotFoundError. Three indirections are followed, because between them they
# account for most of the live hits and each one is a trivial evasion otherwise:
# a walk assigned to a local, a helper function that returns a walk, and a
# comprehension over either.
#
# WHAT THIS GUARD DOES NOT ESTABLISH, stated rather than left to be discovered:
#
# * `except OSError: continue` counts as compliant here. It survives the race,
#   which is what this guard is about. It is ALSO the silent-narrowing shape
#   `read_sources` exists to warn about - a sweep holding its verdict over a
#   corpus that shrank in silence - and that is a real defect with a different
#   fix. Accepting it here is deliberate, so this guard lands blocking the crash
#   class instead of stalling on a second argument.
# * A walk rooted at a fixture-owned directory is excluded only when the
#   expression mentions a name containing "tmp". A fixture directory under some
#   other name is reported. That is the over-reporting direction
#   `.claude/rules/scope-claims.md` asks for.
# * It sees `read_text`, `read_bytes` and `open`. A read reached some other way
#   is invisible to it.

_WALKERS = {"tracked_paths", "tracked_python_files", "not_ignored"}
_COMPLIANT_WALKERS = {"read_sources"}   # the fix; a loop over it is safe by construction
_GLOBS = {"glob", "rglob", "iterdir"}
_READS = {"read_text", "read_bytes"}
_PASSTHROUGH = {"sorted", "list", "set", "reversed", "enumerate", "tuple"}
_SAFE_EXC = {"OSError", "FileNotFoundError", "EnvironmentError", "IOError", "Exception"}
_SCANNED_TREES = ("tests", "scripts", ".claude")


def _mentions_tmp(node: ast.AST) -> bool:
    """A corpus the test itself built and owns. Nothing else can delete it."""
    return any(isinstance(n, ast.Name) and "tmp" in n.id.lower()
               for n in ast.walk(node))


def _is_walk_call(node: ast.AST, walker_funcs: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and (func.id in _WALKERS or func.id in walker_funcs):
        return True
    if isinstance(func, ast.Attribute):
        if func.attr in _WALKERS:
            return True
        if func.attr in _GLOBS and not _mentions_tmp(func.value):
            return True
    return False


def _derives_from_walk(node: ast.AST, walk_names: set[str],
                       walker_funcs: set[str]) -> bool:
    """Is this iterable expression a repository-wide walk?"""
    if _mentions_tmp(node):
        return False
    if isinstance(node, ast.Name):
        return node.id in walk_names
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _COMPLIANT_WALKERS:
            return False
        if isinstance(func, ast.Name) and func.id in _PASSTHROUGH:
            return any(_derives_from_walk(a, walk_names, walker_funcs)
                       for a in node.args)
        return _is_walk_call(node, walker_funcs)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return any(_derives_from_walk(g.iter, walk_names, walker_funcs)
                   for g in node.generators)
    if isinstance(node, ast.BinOp):
        return (_derives_from_walk(node.left, walk_names, walker_funcs)
                or _derives_from_walk(node.right, walk_names, walker_funcs))
    return False


def _walker_returning_funcs(tree: ast.AST, walk_names: set[str]) -> set[str]:
    """Functions whose body returns a walk. The third indirection, and the one
    that hid roughly half the live hits behind a `_corpus()` helper."""
    found: set[str] = set()
    changed = True
    while changed:                      # a helper may call another helper
        changed = False
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name in found:
                continue
            local = set(walk_names)
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and _derives_from_walk(node.value, local, found)):
                    local.add(node.targets[0].id)
            for node in ast.walk(fn):
                if (isinstance(node, ast.Return) and node.value is not None
                        and _derives_from_walk(node.value, local, found)):
                    found.add(fn.name)
                    changed = True
                    break
    return found


def _protected(call: ast.AST, stop: ast.AST) -> bool:
    """Does an enclosing `try` inside this loop catch the vanished file?"""
    node = call
    while node is not None and node is not stop:
        parent = getattr(node, "_race_parent", None)
        if isinstance(parent, ast.Try) and node in parent.body:
            for handler in parent.handlers:
                if handler.type is None:        # bare except: crude, but catches it
                    return True
                names = {n.id for n in ast.walk(handler.type)
                         if isinstance(n, ast.Name)}
                if names & _SAFE_EXC:
                    return True
        node = parent
    return False


def _parametrized_walk(fn, walk_names: set[str], walker_funcs: set[str]):
    """The argument names a `parametrize` decorator binds to walked paths.

    Returns `(names, values_expression)`, or `(set(), None)` when this function
    is not parametrised over a repository walk.

    Only the FIRST parameter of a multi-name `parametrize("a,b", ...)` is
    treated as the path, because the values are tuples and this pass does not
    unpack them. That under-reports rather than over-reports, and saying so here
    is the point: a read of `b` in such a test is invisible to this guard.
    """
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call) or len(dec.args) < 2:
            continue
        func = dec.func
        if not (isinstance(func, ast.Attribute) and func.attr == "parametrize"):
            continue
        spec, values = dec.args[0], dec.args[1]
        if not (isinstance(spec, ast.Constant) and isinstance(spec.value, str)):
            continue
        if not _derives_from_walk(values, walk_names, walker_funcs):
            continue
        parts = [p.strip() for p in spec.value.split(",") if p.strip()]
        if parts:
            return {parts[0]}, values
    return set(), None


def unprotected_reads(source: str) -> list[tuple[int, str]]:
    """Every read of a walked path that no `except OSError` covers.

    Public because both the guard below and its two synthetic detector fixtures
    drive this one function. A detector proven on fixtures and then reimplemented
    for the live scan proves nothing about the live scan.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._race_parent = node

    walk_names: set[str] = set()
    walker_funcs = _walker_returning_funcs(tree, walk_names)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and _derives_from_walk(node.value, walk_names, walker_funcs)):
            walk_names.add(node.targets[0].id)
    walker_funcs |= _walker_returning_funcs(tree, walk_names)

    findings: set[tuple[int, str]] = set()
    for loop in ast.walk(tree):
        if isinstance(loop, (ast.For, ast.AsyncFor)):
            iterable, target, body = loop.iter, loop.target, loop.body
        elif isinstance(loop, (ast.ListComp, ast.SetComp,
                               ast.GeneratorExp, ast.DictComp)):
            iterable, target, body = (loop.generators[0].iter,
                                      loop.generators[0].target, [loop])
        elif isinstance(loop, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # `@pytest.mark.parametrize("page", _pages())` binds a walked path
            # to an ARGUMENT, and the read happens later in the body. Same race
            # with a WIDER window: the walk runs at collection time and the read
            # runs at execution time, minutes apart under `-n auto`, where a
            # `for` loop's window is microseconds.
            #
            # Added 2026-09-01 after a converting agent reported two live sites
            # this scan could not see, because it only ever looked at loops.
            # A detector that misses a shape reports zero for it forever, which
            # is the "green over an empty corpus" failure one level up.
            names, values = _parametrized_walk(loop, walk_names, walker_funcs)
            if not names:
                continue
            iterable, target, body = values, None, loop.body
            bound = names
        else:
            continue
        if target is not None:
            if not _derives_from_walk(iterable, walk_names, walker_funcs):
                continue
            bound = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        for stmt in body:
            for call in ast.walk(stmt):
                if not isinstance(call, ast.Call):
                    continue
                func, hit = call.func, None
                if (isinstance(func, ast.Attribute)
                        and func.attr in _READS | {"open"}
                        and isinstance(func.value, ast.Name)
                        and func.value.id in bound):
                    hit = f"{func.value.id}.{func.attr}"
                elif (isinstance(func, ast.Name) and func.id == "open"
                      and call.args and isinstance(call.args[0], ast.Name)
                      and call.args[0].id in bound):
                    hit = f"open({call.args[0].id})"
                if hit is not None and not _protected(call, loop):
                    findings.add((call.lineno, hit))
    return sorted(findings)


# THERE IS NO BASELINE. The threshold is zero, and that is deliberate.
#
# This guard shipped on 2026-09-01 beside a shrink-only record of the 145 sites
# then live across 89 files, so it could block new ones while the existing ones
# came down. They all came down the same day, the record emptied, and the record
# plus its regenerator were deleted rather than left at zero.
#
# Leaving an empty allowlist in place is how the next violation gets waved
# through: the file exists, adding one line to it looks like maintenance rather
# than like a regression, and the reviewer sees a JSON edit instead of a guard
# being switched off. A bare `== 0` cannot be edited into permission.


@functools.lru_cache(maxsize=1)
def _live_sites() -> dict[str, list[tuple[int, str]]]:
    """Cached: the walk parses ~1400 modules and two tests below both need it.

    Measured 2026-09-01 at roughly 20s a call, so calling it twice put 40s on
    every suite run to answer the same question twice.
    """
    from scripts.utils.repo_files import tracked_paths

    sites: dict[str, list[tuple[int, str]]] = {}
    for tree in _SCANNED_TREES:
        for path, source in read_sources(tracked_paths((f"{tree}/**/*.py",), ROOT),
                                         errors="replace"):
            hits = unprotected_reads(source)
            if hits:
                sites[path.relative_to(ROOT).as_posix()] = hits
    return sites


def test_no_walk_then_read_is_left_unprotected():
    """The headline. A read of a walked path must survive the file vanishing.

    Zero, tree-wide. Measured 2026-09-01: 145 sites across 89 files at the start
    of the day, 0 at the end.
    """
    offenders = []
    for rel, hits in sorted(_live_sites().items()):
        lines = ", ".join(f"line {n} ({what})" for n, what in hits)
        offenders.append(f"{rel}: {len(hits)} unprotected read(s) -- {lines}")

    assert not offenders, (
        "A read of a path bound by a repository walk is not covered by an "
        "`except OSError`. A file created and deleted between the walk and the "
        "read makes this guard raise FileNotFoundError and report a violation "
        "where nothing was violated -- and the pre-push gate produces exactly "
        "that on its own, since `scripts/run-tests.py` runs `-n auto` without "
        "deselecting `slow`.\n\n"
        "Route the read through `read_sources` from `scripts.utils.repo_files` "
        "when the sweep hunts offenders; a file that is gone cannot violate "
        "anything. When the sweep computes a COUNT, a CHECKSUM or a "
        "completeness claim, retry once and then FAIL naming the file, because "
        "a silent skip there makes the answer wrong rather than narrower.\n  "
        + "\n  ".join(offenders))


# ============================================================
# Anti-vacuity: the detector must detect, and the walk must reach files
# ============================================================

# Measured 2026-09-01: tests 987, scripts 386, .claude 57. The floors sit under
# those with room for ordinary churn. PER TREE and never over the union, because
# a union floor is satisfied while one source contributes zero - `tests/` alone
# clears any total these three could share.
_CORPUS_FLOORS = {"tests": 800, "scripts": 300, ".claude": 40}


@pytest.mark.parametrize("tree,floor", sorted(_CORPUS_FLOORS.items()))
def test_the_scan_still_reaches_the_tree(tree, floor):
    """A guard is green over an empty corpus. Ask what it actually walked."""
    from scripts.utils.repo_files import tracked_paths

    found = len(tracked_paths((f"{tree}/**/*.py",), ROOT))
    assert found >= floor, (
        f"the walk over {tree}/ collapsed to {found} file(s), under the floor "
        f"of {floor}. Every assertion above is passing over almost nothing.")


_FIXTURE_BARE = '''
from scripts.utils.repo_files import tracked_paths
def scan():
    for path in tracked_paths(("scripts/**/*.py",)):
        text = path.read_text(encoding="utf-8")
        print(text)
'''

_FIXTURE_GUARDED = '''
from scripts.utils.repo_files import tracked_paths
def scan():
    for path in tracked_paths(("scripts/**/*.py",)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        print(text)
'''

_FIXTURE_VIA_LOCAL = '''
from scripts.utils.repo_files import tracked_python_files
def scan():
    paths = tracked_python_files(("scripts",))
    for p in paths:
        print(p.read_bytes())
'''

_FIXTURE_VIA_HELPER = '''
from scripts.utils.repo_files import tracked_paths
def _corpus():
    return tracked_paths(("tests/**/*.py",))
def scan():
    for p in _corpus():
        print(p.read_text(encoding="utf-8"))
'''

_FIXTURE_COMPLIANT_HELPER = '''
from scripts.utils.repo_files import tracked_paths, read_sources
def scan():
    for path, text in read_sources(tracked_paths(("scripts/**/*.py",))):
        print(text)
'''

_FIXTURE_FIXTURE_OWNED = '''
def test_something(tmp_path):
    for p in sorted(tmp_path.rglob("*.py")):
        print(p.read_text(encoding="utf-8"))
'''

# The parametrize shape. The walk runs at COLLECTION time and the read runs at
# EXECUTION time, so its window is minutes wide under `-n auto` rather than
# microseconds. This scan was blind to it until 2026-09-01 and reported zero.
_FIXTURE_PARAM_BARE = '''
import pytest
from scripts.utils.repo_files import tracked_paths
def _pages():
    return tracked_paths(("docs/**/*.html",))
@pytest.mark.parametrize("page", _pages())
def test_x(page):
    assert "a" in page.read_text(encoding="utf-8")
'''

_FIXTURE_PARAM_GUARDED = '''
import pytest
from scripts.utils.repo_files import tracked_paths
def _pages():
    return tracked_paths(("docs/**/*.html",))
@pytest.mark.parametrize("page", _pages())
def test_x(page):
    try:
        t = page.read_text(encoding="utf-8")
    except OSError:
        pytest.skip("gone")
    assert "a" in t
'''

# A literal parameter list is not a walk. Without this the shape above could be
# "supported" by reporting every parametrised test that opens anything.
_FIXTURE_PARAM_LITERAL = '''
import pytest
@pytest.mark.parametrize("name", ["a", "b"])
def test_x(name, tmp_path):
    assert (tmp_path / name).read_text() == ""
'''


@pytest.mark.parametrize("label,source", [
    ("bare read", _FIXTURE_BARE),
    ("walk assigned to a local", _FIXTURE_VIA_LOCAL),
    ("walk returned by a helper", _FIXTURE_VIA_HELPER),
    ("parametrize over a walk", _FIXTURE_PARAM_BARE),
])
def test_the_detector_reports_a_violation_it_should(label, source):
    """Without this, repointing the walk at nothing leaves the guard green."""
    assert unprotected_reads(source), f"the detector missed: {label}"


@pytest.mark.parametrize("label,source", [
    ("read inside except OSError", _FIXTURE_GUARDED),
    ("read through read_sources", _FIXTURE_COMPLIANT_HELPER),
    ("walk the fixture owns (tmp_path)", _FIXTURE_FIXTURE_OWNED),
    ("parametrize read inside except OSError", _FIXTURE_PARAM_GUARDED),
    ("parametrize over a literal list", _FIXTURE_PARAM_LITERAL),
])
def test_the_detector_stays_quiet_when_it_should(label, source):
    """The other jaw. A detector that reports everything is not a detector."""
    assert not unprotected_reads(source), f"the detector false-positived on: {label}"


def test_the_helper_lives_in_one_place():
    """`tests/repo_files` is a re-export. A second implementation under `tests/`
    is the copy that stops being fixed, which is why the walker moved out."""
    import tests.repo_files as shim

    assert shim.read_sources.__module__ == "scripts.utils.repo_files"
