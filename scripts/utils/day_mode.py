#!/usr/bin/env python3
"""Day mode: pick the tests a day's changes can reach, and say why.

The full suite is 24700 tests in 1089 files and takes 405-525s on 16 cores, of
which 56s is collection that EVERY xdist worker pays. Selecting fewer files cuts
the collection as well as the run, which is why selection is worth more here
than parallelism. Day mode is the working-day gate: run what the day's changes
can reach. The full run moves to the night, where nothing is waiting on it.

Day mode is NEVER the default. It is a command the operator runs. The pre-push
gate, `scripts/run-tests.py`, is untouched by this module and still runs the
whole regression suite.

WHAT THIS CAN MISS, stated here because a selector whose bounds live only in a
report is a selector nobody can audit.

1.  DYNAMIC NAMES THIS MODULE CANNOT RECONSTRUCT. A test reaches a file by
    importing it, or by naming it in a string. Both are read from the AST. A
    test that builds a path or a module name from pieces no literal spells --
    `ROOT / parts[0] / (stem + ".py")` -- is invisible to the literal route, and
    if it also does not import the file, day mode will not select it. The
    `blind` subcommand reports every source file in that state and
    `tests/test_day_mode_blind_spot.py` fails when a new one appears.

2.  THE IMPORT GRAPH IS NOT A CALL GRAPH. A change to a file that is imported
    but whose changed function is never called by the selected tests is
    over-selected, not under-selected. Day mode errs toward running too much;
    that is the safe direction and it is deliberate.

3.  HUB MODULES AND CONFTEST INPUTS MAKE DAY MODE POINTLESS. Measured
    2026-09-04: a change to `scripts/utils/workspace.py` selects 1077 of 1077
    test files, because `tests/conftest.py` reaches it and a conftest is a proxy
    for its whole subtree. So does a change to any other module that conftest
    imports, or to any data file it reads. That is not a defect. It is the
    honest answer for a change that can break any test in the suite, and day
    mode prints the count so the operator can see it and run the full suite
    instead of pretending the selection saved something.

4.  THE MANDATORY CORE IS A FLOOR, NOT COVERAGE. The core is the set of tests
    that sweep the repository tree (below). They run on every day-mode
    invocation because any change alters their input and no edge names them.
    They check tree-wide invariants; they are not a substitute for the tests of
    the file you changed.

5.  DELETIONS ARE REPORTED, NOT RUN. A path in the change set that git no
    longer tracks is listed separately and never handed to pytest, because a
    deleted test file would fail the whole run at collection. A surviving test
    that NAMES the deleted path is still selected by the literal route and will
    fail, which is the intended behaviour. A test that reached the file only
    through an import of a module that no longer exists cannot be found at all,
    because the file is gone from the tree the facts are built from.

Routes, in the order they are reported:

    changed-test    the changed file is itself a test file, and still tracked
    conftest        a changed conftest.py selects its whole directory subtree
    conftest-input  a module a conftest imports, or a data file it names, does
                    the same. Pytest loads a conftest for every test beneath it
                    and never through an import statement, so nothing else in
                    this module can see that edge.
    import          reverse closure over the AST import graph
    literal         a test names the changed file in a string constant: its
                    path, a path suffix, its basename, or its dotted module
                    name. This is the route that covers files a test drives as
                    a SUBPROCESS, which is not an import and which no import
                    graph will ever model.
    subtree         a test globs ONE DIRECTORY and the changed file is what that
                    glob returns: `DOCS.glob("*.html")` reaches every page under
                    `docs/`. A directory full of files nothing imports and no
                    string names -- the docs pages, the systemd unit templates --
                    has no other route, and a sweep of the whole tree is not this
                    route but the core below.
    core            the derived mandatory core; always selected

WHY NOT `codegraph affected`. The index is real: 1592 files, and 8296 edges from
tests/ to scripts/. Three things decided against building on it, and the first
is the one that decides it on its own.

A WORKTREE HAS NO `.codegraph/` OF ITS OWN. Engine code is changed in a
worktree, never in the main clone, so a selector built on that index cannot run
in the place the work actually happens. That is an availability fact rather than
a quality judgement: no amount of later measurement can overturn it. The two
reasons below are supporting evidence, not the argument.

THE FILTER PARTITIONS THE CORPUS AND NO SINGLE VALUE RETURNS THE UNION. Measured
2026-09-04 in the main clone against `scripts/utils/supervise.py`:
`--filter "tests/**/*.py"` returns 122 files, every one of them in a
subdirectory; `--filter "tests/**"` returns 14, every one of them directly in
`tests/`; and no filter at all returns 0, printing "No test files affected".
The two globs do not overlap, so either one alone silently drops half the
answer, and `tests/**/*.py` drops `tests/test_supervise.py` itself. Repeating
the flag does not union them: `--filter A --filter B` returns B's set alone. A
comma list and a brace list each return zero without saying why, which is the
worst of the three failure modes. Anyone calling this command has to make two
calls and union the results, and should assert the union is a strict superset of
each half so the next change cannot quietly undo it.

THE EDGES ARE NOT ALL IMPORTS. The index resolves cross-file references by NAME,
so `scripts/utils/supervise.py` carries an `imports` edge pointing AT two test
files. Following it makes a reverse closure reach 730 of 1115 test files for a
module that 14 tests actually touch.

This module reads the checkout it lives in and needs no index at all.

Persistence: one SQLite fact cache under `.cache/day-mode/`, no daemon, no port.
MEASURED 2026-09-04 at load 3.3: parsing all 1551 Python files costs 6.6s cold
and the whole selection takes 0.31s warm, because the cache is keyed on content
hash and a normal day re-parses nothing.

WALL CLOCK, AND THE TARGET IT MISSES. MEASURED 2026-09-04 on a verified-idle
box, one-minute load under 4 and zero pytest processes before each run:

    the mandatory core alone     155 files    258.3s
    a supervise.py change        165 files    262.9s
    a _dispatch.py change        190 files    266.1s
    full suite (for comparison)  1079 files   405-525s

That is 1.6x to 2x, against a target of under 30 seconds. THE TARGET IS MISSED,
and the reason is not weak selection. Three layers of fixed cost were measured
separately and they account for all of it.

    xdist startup, on ANY invocation             ~16-19s
    the 334 tests of a typical selection         ~37s
    the mandatory core, on every invocation      258.3s

The core is 155 files but 5368 tests, because a tree sweep parametrizes over
every file in the repository: 22% of the suite's tests and roughly 60% of its
wall clock.

SELECTION IS NOT THE REMAINING PROBLEM, and the numbers say so plainly. The 10
files specific to a supervise.py change run in 54.8s on their own; the 35
specific to a _dispatch.py change run in 65.4s. Four times the tests for 19%
more wall clock is a fixed cost with a rounding error on top, so sharpening
selection further buys nothing.

BEWARE THE SUBTRACTION, which is the trap this module walked into first. The
MARGINAL cost of adding those 10 files to a run already under way is about 5s,
which is what 262.9 minus 258.3 measures. The cost of running them AS AN
INVOCATION is 54.8s. Day mode always asks the second question, because day mode
is always its own invocation. The subtraction answers a question nobody asked.

`-n auto` IS ALREADY RIGHT and the worker count must not be tuned by selection
size. Measured on the same 10 files: 53.5s at `-n 16`, 57.6s at `-n 8`, 62.6s at
`-n 4`, 86.6s at `-n 2`, and over 120s serially. Fewer workers is strictly
worse. The startup is per-worker but the sixteen pay it in PARALLEL, so its wall
cost stays roughly constant while the test work divides. The one case where
serial wins is a selection of one or two files (19.0s under `-n auto` against
2.8s serially, for a single 20-test file), and day mode never lands there
because its smallest possible selection is the 155-file core.

THE TWO LEVERS, neither of them taken here, both named so the choice is visible.
The core's CADENCE is the operator's decision and carries a real safety cost:
running it hourly rather than on every invocation would put a typical change
near a minute, at the price of a window in which a tree-wide invariant is
unchecked. The suite's STARTUP, sixteen workers each importing an 1877-line
conftest, is a separate piece of work on the suite rather than on this selector.
No flag is offered for either, because a flag is a decision half-made.

THE RECIPE, so the next quiet window is a replay and not a re-derivation. Check
`/proc/loadavg` AND `pgrep -af pytest` first: the load average lags, so a suite
that started thirty seconds ago is invisible in it. Require a one-minute load
under 4 and no pytest process outside this tree. Then, for each of the three
selections below, record the wall clock and the load before and after:

    # 1. the mandatory core alone, the floor under every run (155 files)
    python scripts/day-mode.py core | grep -oE 'tests/[^ ]+\\.py' > /tmp/sel
    time python -m pytest -q -n auto -m "not acceptance" $(cat /tmp/sel)

    # 2. a typical single-file change (165 files)
    python scripts/day-mode.py select --files scripts/utils/supervise.py --json \\
      | python -c "import json,sys; print('\\n'.join(json.load(sys.stdin)['tests']))" > /tmp/sel
    time python -m pytest -q -n auto -m "not acceptance" $(cat /tmp/sel)

    # 3. the hook change this design turns on (190 files)
    python scripts/day-mode.py select --files .claude/hooks/_dispatch.py --json \\
      | python -c "import json,sys; print('\\n'.join(json.load(sys.stdin)['tests']))" > /tmp/sel
    time python -m pytest -q -n auto -m "not acceptance" $(cat /tmp/sel)

Expected shape, and treat a departure from it as a result rather than a glitch:
all three well under the full suite, run 1 the floor and runs 2 and 3 close to
it, since the core is 155 of the 165 and 190. If run 1 is not far under the
full suite, the core is the wrong size and the selector's premise is wrong.
Extract the file list with `grep -oE 'tests/[^ ]+\\.py'` and not a leading-space
pattern: an earlier attempt kept the indentation, handed pytest paths with a
leading space, and exited 5 (no tests collected) while looking like a fast run.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Derived from THIS file, so a worktree resolves to the worktree. A constant, the
# common git directory or ${CLAUDE_PROJECT_DIR} would all point a YARD's selector
# at the main clone; `.claude/rules` calls that out by name as the way a guard
# ends up armed against the wrong tree.
ROOT = Path(__file__).resolve().parent.parent.parent

CACHE_REL = Path(".cache/day-mode/facts.db")

# Bump this whenever `Facts` gains, loses or changes the meaning of a field.
# `_open_cache` explains why a rename rather than a migration.
FACTS_TABLE = "facts_v2"

# Calls that mean "read the repository tree". A test that does this has the whole
# tree as its input, so ANY change alters what it sees and no import edge and no
# string literal names it. These tests are the mandatory core.
#
# The receiver matters as much as the name. An earlier draft matched the bare
# attribute and counted `ast.walk` (214 test files, most of the suite) as a repo
# sweep, which would have made the core 37% of the suite and day mode worthless.
# `_is_rootish` demands the receiver derive from a repo-root expression, which
# `ast` does not, and the core fell to 156 files.
SWEEP_CALLS = frozenset({"rglob", "glob", "iterdir", "walk", "scandir", "listdir"})

# The shared "files in this repository" helpers. A test importing one of these is
# sweeping the tree by definition, whatever it then calls.
REPO_HELPERS = frozenset(
    {
        "tracked_paths",
        "tracked_python_files",
        "read_sources",
        "not_ignored",
        "ignored_paths",
        "ignored_paths_or_none",
    }
)

_ROOTISH_NAMES = frozenset({"ROOT", "REPO", "REPO_ROOT", "WORKSPACE", "WORKSPACE_ROOT"})

# A trailing file extension: `.json`, `.yaml`, `.md`, `.py`, `.sh`. Anchored to
# the end so a dotted module name still falls through to the identifier test.
#
# TWELVE, and it used to be five. MEASURED 2026-09-05 over the 2375 tracked files
# of this repository: the longest extension on a non-dotfile basename is
# `.destinations` (12 characters after the dot), and the one that mattered is
# `.service` (7), which 21 files carry. At five, `bridge-daemon.service` was read
# as prose and no test could be attributed to it, while `sentinel.service` was
# accepted -- not by this pattern, but by the dotted-identifier test below, which
# a hyphen defeats and a plain word does not. That asymmetry is what made the bug
# look arbitrary from the outside: `.timer` passed and `.service` did not.
#
# A bound is still a bound. A thirteen-character extension landing in this tree
# tomorrow is invisible again, which is why `tests/test_a_selector_that_read_a_
# filename_as_prose.py` asserts the bound against the tree's own longest
# extension rather than against the number 12.
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,12}\Z")

# A whole DOTFILE NAME, which is a different shape from an extension and is why
# widening the bound alone would not have been enough. `.gitignore` has no
# extension: the dot opens the name rather than separating a suffix, so the
# question is not "how long may a suffix be" but "is this string a leading-dot
# filename". `.python-version` and `.worktreeinclude` carry hyphens and lengths
# that no extension rule should have to stretch to cover, and `.secrets.baseline`
# is both shapes at once.
_DOTFILE = re.compile(r"\A\.[A-Za-z][A-Za-z0-9._-]*\Z")


class DayModeError(RuntimeError):
    """Day mode could not answer. Never raised to mean "nothing to run"."""


# --------------------------------------------------------------------------
# Fact extraction
# --------------------------------------------------------------------------


@dataclass
class Facts:
    """What one Python file says about the files it reaches.

    `imports` are dotted module names as written. `literals` are string
    constants that could name a file or a module. `sweeps` is non-empty when the
    file reads the repository tree. `scoped` holds the SUBTREE sweeps: each a
    `(kind, directory, pattern)` triple naming a glob the file runs over one
    directory rather than over the whole tree.
    """

    imports: frozenset[str] = frozenset()
    literals: frozenset[str] = frozenset()
    sweeps: frozenset[str] = frozenset()
    scoped: frozenset[tuple[str, str, str]] = frozenset()


def _is_rootish(node: ast.AST) -> bool:
    """True when an expression looks like it derives from the repository root.

    `ROOT / "scripts"`, `Path(__file__).resolve().parents[2]`, `REPO_ROOT`. The
    test is deliberately loose in the over-selecting direction: a false positive
    puts one more test in the mandatory core, a false negative drops a tree
    sweep out of it.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            if sub.id == "__file__" or sub.id.upper() in _ROOTISH_NAMES:
                return True
            if "ROOT" in sub.id.upper():
                return True
        elif isinstance(sub, ast.Attribute) and sub.attr in {"parent", "parents"}:
            return True
    return False


def _looks_like_a_reference(text: str) -> bool:
    """Keep string constants that could name a file or a module, drop prose.

    Without this the cache holds every docstring in the repository -- this suite
    writes long ones -- and the literal route compares changed paths against
    English sentences. The filter is on shape, not on meaning: a separator, a
    leading-dot filename, an extension, or a dotted identifier chain.

    WHAT IS STILL DROPPED, and it is deliberate. A basename with neither a dot
    nor a separator -- `LICENSE`, `NOTICE`, `pre-push` -- is indistinguishable
    by shape from an ordinary word, and this repository tracks five such files.
    MEASURED 2026-09-05: accepting every hyphenated or ALL-CAPS bare word grew
    the literal set from 14453 to 21192, a 47% increase carried on every cached
    payload, and moved two of thirty-six files off the full suite. That is the
    wrong trade, and the two files still widen honestly rather than being routed
    by a rule that would also accept `utf-8` and `pre-commit` as filenames.
    """
    if not text or len(text) > 200 or "\n" in text or " " in text:
        return False
    if "/" in text:
        return True
    # A leading-dot filename, before the extension test, because the two ask
    # different questions and only this one accepts `.gitignore`.
    if _DOTFILE.match(text):
        return True
    # Any file extension, not just `.py`, and not just names that are legal
    # Python identifiers. MEASURED 2026-09-04: this used to accept `.py` or a
    # dotted identifier chain, which rejected `"tmp-leak-baseline.json"` --
    # hyphens are not identifier characters -- and that literal in
    # `tests/conftest.py` is the only edge to `config/tmp-leak-baseline.json`.
    # The five-commit replay caught it: commit 3055671 changed that baseline and
    # day mode did not select the one test that guards it.
    if _EXTENSION.search(text):
        return True
    # A dotted module path: `scripts.utils.paths`. Two segments minimum, so a
    # bare version string does not qualify.
    parts = text.split(".")
    return len(parts) >= 2 and all(p.isidentifier() for p in parts)


def _root_relative_dir(node: ast.AST, binds: dict[str, str]) -> str | None:
    """The repository-relative directory an expression names, or None.

    `""` is the repository root itself and is a DIFFERENT answer from None: the
    caller refuses it, because a sweep of the whole tree is the mandatory core's
    business and a route that claimed every file would leave nothing for the
    push gate to widen on.

    `binds` is what makes this worth writing. `_is_rootish` reads one expression
    and cannot follow a name to its assignment, so `DOCS = ROOT / "docs"` at
    module level followed by `DOCS.glob("*.html")` is invisible to it: the
    receiver is a bare `Name`, and `DOCS` neither is nor contains `ROOT`. That is
    the whole of cause B. MEASURED 2026-09-05: three of the four tests that read
    `docs/*.html` reach it exactly that way.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _root_relative_dir(node.left, binds)
        if base is None:
            return None
        right = node.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            return None
        piece = right.value.strip("/")
        if not piece:
            return base
        return f"{base}/{piece}" if base else piece
    if isinstance(node, ast.Name):
        if node.id in binds:
            return binds[node.id]
        return "" if _is_rootish(node) else None
    return "" if _is_rootish(node) else None


def _module_level_dirs(tree: ast.Module) -> dict[str, str]:
    """Module-level names bound to a repository-relative directory.

    Module level only, and single-target assignments only. A name rebound inside
    a function is not followed, because following it would mean a scope analysis
    for a gain this tree does not show: every scoped sweep measured on
    2026-09-05 reads a module-level constant.
    """
    binds: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        resolved = _root_relative_dir(node.value, binds)
        if resolved is not None:
            binds[target.id] = resolved
    return binds


def _scoped_sweep(node: ast.Call, binds: dict[str, str]) -> tuple[str, str, str] | None:
    """One `(kind, directory, pattern)` triple for a subtree glob, or None."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in {"glob", "rglob", "iterdir"}:
        return None
    directory = _root_relative_dir(func.value, binds)
    if not directory:
        # None (unresolvable) and "" (the whole tree) are both refused here.
        return None
    if func.attr == "iterdir":
        return ("glob", directory, "*")
    if not node.args:
        return None
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    return (func.attr, directory, first.value)


def swept_by(rel: str, kind: str, directory: str, pattern: str) -> bool:
    """Would that sweep have returned this repository-relative path?

    Segment counting, not `fnmatch` over the whole string, because `glob` and
    `rglob` differ exactly there: `docs/*.html` returns nothing from
    `docs/assets/`, while `rglob("*.html")` returns it from any depth. Matching
    the string alone would collapse the two and hand a non-recursive glob files
    it never saw.
    """
    prefix = directory + "/"
    if not rel.startswith(prefix):
        return False
    parts = rel[len(prefix):].split("/")
    wanted = pattern.strip("/").split("/")
    if kind == "glob":
        if len(parts) != len(wanted):
            return False
        tail = parts
    else:
        if len(parts) < len(wanted):
            return False
        tail = parts[len(parts) - len(wanted):]
    # `strict=True` cannot raise here: both branches above returned already
    # unless the lengths agree, and `tail` is sliced to `len(wanted)`. It is
    # written anyway so that a future edit to either branch fails loudly rather
    # than silently truncating the comparison to the shorter side.
    return all(
        fnmatch.fnmatchcase(part, want) for part, want in zip(tail, wanted, strict=True)
    )


def _resolve_relative(module: str, level: int, path: str) -> str:
    """Turn `from ..utils import x` inside `scripts/a/b.py` into `scripts.utils`.

    A relative import that walks above the repository root resolves to the empty
    string, which matches no module and is dropped by the caller.
    """
    package = path.split("/")[:-1]
    if level > len(package):
        return ""
    base = package[: len(package) - (level - 1)] if level > 1 else package
    return ".".join([*base, module] if module else base)


def extract(path: str, source: str) -> Facts:
    """Read one Python file's imports, reference-shaped literals and sweeps.

    A file that does not parse yields empty facts rather than raising. That is
    the one place this module degrades quietly, and it is bounded: an
    unparseable file cannot be imported by anything either, and the caller
    reports the count so a tree full of syntax errors does not look like a tree
    with nothing to run.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return Facts()

    imports: set[str] = set()
    literals: set[str] = set()
    sweeps: set[str] = set()
    scoped: set[tuple[str, str, str]] = set()
    binds = _module_level_dirs(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                resolved = _resolve_relative(node.module or "", node.level, path)
                if resolved:
                    imports.add(resolved)
                    for alias in node.names:
                        imports.add(f"{resolved}.{alias.name}")
            elif node.module:
                imports.add(node.module)
                for alias in node.names:
                    imports.add(f"{node.module}.{alias.name}")
            if "repo_files" in (node.module or ""):
                sweeps.add("repo_files")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _looks_like_a_reference(node.value):
                literals.add(node.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in SWEEP_CALLS and _is_rootish(func.value):
                    sweeps.add(f"{func.attr}@root")
                elif func.attr in REPO_HELPERS:
                    sweeps.add(func.attr)
            elif isinstance(func, ast.Name) and func.id in REPO_HELPERS:
                sweeps.add(func.id)
            found_scope = _scoped_sweep(node, binds)
            if found_scope is not None:
                scoped.add(found_scope)

    for name in imports:
        if "repo_files" in name:
            sweeps.add("repo_files")

    return Facts(
        frozenset(imports), frozenset(literals), frozenset(sweeps), frozenset(scoped)
    )


# --------------------------------------------------------------------------
# The tree, and the fact cache over it
# --------------------------------------------------------------------------


def tracked_files(root: Path) -> list[str]:
    """Every path git tracks, repo-relative, sorted.

    `git ls-files` and not a walk: a walk sees `.venv`, the worktrees and every
    ignored artifact, and `scripts/utils/repo_files.py` records what that cost
    the tree sweeps the last time someone tried it.
    """
    return sorted(_git_z(root, "ls-files", "-z"))


def _git_z(root: Path, *args: str) -> list[str]:
    """Run a NUL-separated git command and decode the paths byte-exactly.

    BYTES, not `text=True`. Universal-newline translation rewrites a CR inside a
    path to LF, so a file whose name carries one comes back as a name that is not
    on disk, and every route keyed on that path silently matches nothing. This
    repository has a guard for exactly that shape,
    `tests/test_a_reader_that_lost_a_byte_on_the_way_in.py`, and it caught this
    function on 2026-09-04 with `text=True` on a `-z` invocation. `surrogateescape`
    keeps a path that is not valid UTF-8 round-trippable rather than raising.
    """
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise DayModeError(f"git {' '.join(args)} failed in {root}: {message}")
    decoded = result.stdout.decode("utf-8", "surrogateescape")
    return [p for p in decoded.split("\0") if p]


def _open_cache(root: Path) -> sqlite3.Connection:
    """The fact cache, at the schema this module reads.

    THE TABLE NAME CARRIES THE SCHEMA VERSION, and that is the whole migration.
    A payload is keyed on the file's content hash, so a row written by an older
    version of `extract` matches for as long as the file is untouched: the day
    `scoped` was added, every file in the tree would have kept answering "no
    subtree sweeps" from a cache that was written before the question existed,
    and only an edit to a file would have fixed it. `.claude/rules` names that
    shape -- a cache keyed on unchanged input makes a stale answer permanent --
    and a rename is the cheapest thing that cannot get it wrong. The old table
    is dropped rather than left behind, because a cache nothing reads is a
    megabyte of confusion for whoever opens this database next.
    """
    cache = root / CACHE_REL
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache)
    conn.execute("DROP TABLE IF EXISTS facts")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {FACTS_TABLE} ("  # noqa: S608  # nosec B608 - FACTS_TABLE is a module constant, never input
        " path TEXT PRIMARY KEY, hash TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    return conn


def load_facts(
    root: Path, python_files: list[str], *, use_cache: bool = True
) -> tuple[dict[str, Facts], int]:
    """Facts for every Python file, re-parsing only what changed.

    Returns the facts and the number of files actually parsed, which is what
    tells the operator whether they paid the cold cost or the warm one.
    """
    sources: dict[str, str] = {}
    digests: dict[str, str] = {}
    for rel in python_files:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sources[rel] = text
        digests[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    facts: dict[str, Facts] = {}
    conn = _open_cache(root) if use_cache else None
    if conn is not None:
        for rel, cached_hash, payload in conn.execute(
            f"SELECT path, hash, payload FROM {FACTS_TABLE}"  # noqa: S608  # nosec B608 - module constant
        ):
            if digests.get(rel) == cached_hash:
                blob = json.loads(payload)
                facts[rel] = Facts(
                    frozenset(blob["imports"]),
                    frozenset(blob["literals"]),
                    frozenset(blob["sweeps"]),
                    frozenset(tuple(triple) for triple in blob["scoped"]),
                )

    parsed = 0
    fresh: list[tuple[str, str, str]] = []
    for rel, text in sources.items():
        if rel in facts:
            continue
        found = extract(rel, text)
        facts[rel] = found
        parsed += 1
        fresh.append(
            (
                rel,
                digests[rel],
                json.dumps(
                    {
                        "imports": sorted(found.imports),
                        "literals": sorted(found.literals),
                        "sweeps": sorted(found.sweeps),
                        "scoped": sorted(found.scoped),
                    }
                ),
            )
        )

    if conn is not None:
        with conn:
            conn.executemany(
                f"INSERT INTO {FACTS_TABLE}(path, hash, payload) VALUES (?, ?, ?)"  # noqa: S608  # nosec B608 - module constant
                " ON CONFLICT(path) DO UPDATE SET hash=excluded.hash,"
                " payload=excluded.payload",
                fresh,
            )
            # Compute the stale rows in Python and delete them by name. A
            # `NOT IN (?, ?, ...)` over every tracked file builds the SQL by
            # f-string and binds 1547 parameters, which is both a dynamic-SQL
            # finding and a run at SQLITE_MAX_VARIABLE_NUMBER.
            cached = {row[0] for row in conn.execute(f"SELECT path FROM {FACTS_TABLE}")}  # noqa: S608  # nosec B608
            stale = [(path,) for path in cached - set(sources)]
            conn.executemany(f"DELETE FROM {FACTS_TABLE} WHERE path = ?", stale)  # noqa: S608  # nosec B608
        conn.close()

    return facts, parsed


# --------------------------------------------------------------------------
# The index the routes run against
# --------------------------------------------------------------------------


def _module_names(rel: str) -> list[str]:
    """Every dotted name a file can be imported by, longest first.

    `scripts/utils/paths.py` is importable as `scripts.utils.paths` from the
    repository root, and as `utils.paths` from a `sys.path` entry pointing at
    `scripts/` -- about twenty test modules insert one. Suffixes of one segment
    are NOT registered: a bare `paths` would match any literal spelling that
    word, and the over-selection would be unbounded rather than merely generous.
    """
    if not rel.endswith(".py"):
        return []
    parts = rel[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return []
    return [".".join(parts[i:]) for i in range(len(parts) - 1)] or [".".join(parts)]


@dataclass
class Index:
    """The tree, its facts, and the reverse maps the routes read."""

    root: Path
    tracked: list[str]
    facts: dict[str, Facts]
    parsed: int = 0
    importers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    literal_users: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    core: dict[str, list[str]] = field(default_factory=dict)
    ambiguous_basenames: set[str] = field(default_factory=set)
    conftests: set[str] = field(default_factory=set)
    subtree_sweeps: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    @property
    def test_files(self) -> list[str]:
        return [p for p in self.tracked if is_test_file(p)]


def is_test_file(rel: str) -> bool:
    """Pytest's own rule from `pyproject.toml`: `tests/` and `test_*.py`."""
    return rel.startswith("tests/") and Path(rel).name.startswith("test_") and rel.endswith(".py")


def build_index(root: Path | None = None, *, use_cache: bool = True) -> Index:
    """Read the checkout once and build every map the routes need."""
    root = ROOT if root is None else root
    tracked = tracked_files(root)
    python_files = [p for p in tracked if p.endswith(".py")]
    facts, parsed = load_facts(root, python_files, use_cache=use_cache)
    index = Index(root=root, tracked=tracked, facts=facts, parsed=parsed)

    module_map: dict[str, set[str]] = defaultdict(set)
    for rel in python_files:
        for name in _module_names(rel):
            module_map[name].add(rel)

    basenames: dict[str, set[str]] = defaultdict(set)
    for rel in tracked:
        basenames[Path(rel).name].add(rel)
    index.ambiguous_basenames = {n for n, owners in basenames.items() if len(owners) > 1}

    for rel, found in facts.items():
        for name in found.imports:
            # `from scripts.utils import paths` records both `scripts.utils` and
            # `scripts.utils.paths`; only the second names a file, and the first
            # names a package whose `__init__.py` is a real file too. Both are
            # looked up, and a name owned by several files marks every one of
            # them -- over-selection, in the safe direction.
            for target in module_map.get(name, ()):
                if target != rel:
                    index.importers[target].add(rel)
        for literal in found.literals:
            for target in _literal_targets(literal, basenames, module_map):
                if target != rel:
                    index.literal_users[target].add(rel)

    for rel in index.test_files:
        sweeps = facts.get(rel, Facts()).sweeps
        if sweeps:
            index.core[rel] = sorted(sweeps)

    index.conftests = {rel for rel in tracked if Path(rel).name == "conftest.py"}

    # TEST FILES ONLY. A scoped sweep in a script is a fact about that script,
    # and a route has to end at something pytest can run.
    for rel in index.test_files:
        found_scoped = facts.get(rel, Facts()).scoped
        if found_scoped:
            index.subtree_sweeps[rel] = sorted(found_scoped)

    return index


def _literal_targets(
    literal: str, basenames: dict[str, set[str]], module_map: dict[str, set[str]]
) -> set[str]:
    """Which tracked files a single string constant could be naming.

    Three shapes, all of them seen in this suite:

        "scripts/sync-exchange.py"   a repo-relative path
        "sync-exchange.py"           a basename, joined onto ROOT at the call
        "scripts.sentinel"           a dotted module handed to import_module

    The basename shape is why this route works at all here: 662 sites in the
    suite build a path as `ROOT / "scripts" / "name.py"` because the scripts are
    hyphenated and cannot be imported. It is also the loosest shape, so a
    basename owned by more than one tracked file selects all of them and the
    caller reports the ambiguity rather than silently picking one.
    """
    targets: set[str] = set()
    cleaned = literal.strip("/")
    if cleaned in module_map:
        targets |= module_map[cleaned]
    name = cleaned.rsplit("/", 1)[-1]
    if name in basenames:
        if "/" in cleaned:
            # A path, or a path suffix: only the files it actually ends.
            suffix = "/" + cleaned
            targets |= {p for p in basenames[name] if p == cleaned or p.endswith(suffix)}
        else:
            targets |= basenames[name]
    return targets


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


@dataclass
class Selection:
    """What to run, why each file is in, and what could not be decided."""

    tests: list[str]
    routes: dict[str, list[str]]
    undecided: list[str]
    ambiguous: dict[str, list[str]]
    changed: list[str]
    unknown_changed: list[str]
    parsed: int
    total_tests: int

    def by_route(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for reasons in self.routes.values():
            for reason in reasons:
                counts[reason.split(":", 1)[0]] += 1
        return dict(sorted(counts.items()))


def select(index: Index, changed: list[str]) -> Selection:
    """The tests a change set can reach, plus the mandatory core.

    Deterministic: every set is sorted before it leaves this function, and no
    route depends on iteration order.
    """
    routes: dict[str, list[str]] = defaultdict(list)
    ambiguous: dict[str, list[str]] = {}
    unknown: list[str] = []
    undecided: list[str] = []
    tracked = set(index.tracked)

    for rel in sorted(index.core):
        routes[rel].append("core:" + ",".join(index.core[rel]))

    for rel in sorted(set(changed)):
        reached: set[str] = set()

        # `rel in tracked`, not just the name shape. A DELETED test file is in
        # the change set git reports and is not on disk, so selecting it hands
        # pytest a path that does not exist and the whole run dies at collection
        # with an error that names day mode rather than the deletion.
        if is_test_file(rel) and rel in tracked:
            routes[rel].append("changed-test")
            reached.add(rel)

        # Reverse closure over real AST imports. Cycles terminate on `seen`.
        seen = {rel}
        frontier = {rel}
        while frontier:
            nxt: set[str] = set()
            for node in frontier:
                nxt |= index.importers.get(node, set())
            nxt -= seen
            seen |= nxt
            frontier = nxt
        for test in sorted(t for t in seen if is_test_file(t) and t != rel):
            routes[test].append(f"import:{rel}")
            reached.add(test)

        for test in sorted(t for t in index.literal_users.get(rel, ()) if is_test_file(t)):
            routes[test].append(f"literal:{rel}")
            reached.add(test)

        # A SUBTREE SWEEP READS THE FILE. A test that globs `docs/*.html` opens
        # every one of them, so a changed page is genuinely covered by it, and
        # before this route existed no other route could say so: the pages are
        # not imported, and a test that enumerates a directory never spells any
        # single name in a string. MEASURED 2026-09-05: `docs/RULES-REFERENCE.html`
        # and 12 of the 16 hyphenated `*.service` unit templates reached no test
        # at all, so every push carrying one ran the whole 24,965-test suite.
        #
        # WHY THIS CANNOT SWALLOW THE WIDENING. `_scoped_sweep` refuses a sweep
        # whose directory resolves to the repository root, so the broadest edge
        # this route can build is one subdirectory wide. A file no test's glob
        # covers is still undecided and the gate still widens on it, which is
        # what `.secrets.baseline`, `LICENSE` and `docs/.nojekyll` do today.
        for test, sweeps in index.subtree_sweeps.items():
            if test == rel:
                continue
            if any(swept_by(rel, *sweep) for sweep in sweeps):
                routes[test].append(f"subtree:{rel}")
                reached.add(test)

        # A conftest is a PROXY FOR ITS SUBTREE. Pytest loads it for every test
        # underneath it and never through an import statement, so no edge runs
        # from a test file to the conftest that installs its fixtures. Anything
        # that reaches a conftest therefore reaches every test below it: the
        # conftest itself being changed, a module it imports, or a file it names.
        #
        # MEASURED 2026-09-04, and this rule exists because the replay caught its
        # absence. Two of five recent commits would have shipped with their own
        # regression tests unrun. a356b26 changed
        # `scripts/utils/overlay_write_guard.py`, which `tests/conftest.py`
        # imports to install a guard over the WHOLE suite; 14 of its 18 tests
        # went unselected. 3055671 changed `config/tmp-leak-baseline.json`, which
        # the same conftest reads; its one test went unselected. Both were
        # reported as "could not decide" rather than silently dropped, which is
        # how they were found, but reporting a gap is not closing one.
        for conftest in sorted(index.conftests):
            if conftest not in seen and conftest not in index.literal_users.get(rel, ()):
                continue
            why = "conftest" if conftest == rel else f"conftest-input:{rel}"
            prefix = "" if Path(conftest).parent == Path(".") else str(Path(conftest).parent) + "/"
            for test in index.test_files:
                if test.startswith(prefix):
                    routes[test].append(f"{why}:{conftest}")
                    reached.add(test)

        name = Path(rel).name
        if name in index.ambiguous_basenames and index.literal_users.get(rel):
            ambiguous[rel] = sorted(
                p for p in tracked if Path(p).name == name and p != rel
            )

        if rel not in tracked:
            # A path git does not track: a deleted file, or a rename's old side.
            # Its literal route still fired above if a test names it, which is
            # the behaviour a deletion needs. It is reported so the operator
            # sees that the tree the facts came from no longer holds it.
            unknown.append(rel)
        elif not reached:
            # No route reached a test. Not "nothing to run" and not an error:
            # the core still runs, and this file is named in the report so the
            # decision to trust the selection is the operator's, with the gap
            # visible.
            undecided.append(rel)

    tests = sorted(t for t in routes if is_test_file(t))
    return Selection(
        tests=tests,
        routes={t: sorted(set(routes[t])) for t in tests},
        undecided=sorted(set(undecided)),
        ambiguous=ambiguous,
        changed=sorted(set(changed)),
        unknown_changed=sorted(set(unknown)),
        parsed=index.parsed,
        total_tests=len(index.test_files),
    )


# --------------------------------------------------------------------------
# The blind spot
# --------------------------------------------------------------------------


def blind_files(index: Index) -> list[str]:
    """Source files a test mentions by name but no route can select.

    THE GUARD THIS MODULE EXISTS TO SURVIVE. `.claude/hooks/_dispatch.py` is the
    single PreToolUse entry point for twelve walls, and the code graph reports
    ZERO tests affected by it, because its 73 tests drive it as a SUBPROCESS and
    a subprocess is not an import edge. A selector that trusted an import graph
    alone would have let a change to every wall in this workspace through
    untested, and the run would have been green.

    The check is derived, not a list. A hand-maintained roster of "files the
    graph cannot see" falls behind silently, and the day it matters is the day
    nobody notices; this repository already records that shape. Here the
    question is asked of the tree every time: for each tracked non-test source
    file, does its basename appear anywhere in the test sources? If it does, a
    test knows about the file, and day mode MUST be able to select at least one
    test for it. A file no test mentions at all is untested, which is a coverage
    question and not this module's to answer.
    """
    blob = "\n".join(
        (index.root / rel).read_text(encoding="utf-8", errors="replace")
        for rel in index.test_files
        if (index.root / rel).exists()
    )
    blind: list[str] = []
    for rel in index.tracked:
        if not rel.endswith(".py") or rel.startswith("tests/"):
            continue
        if index.importers.get(rel) or index.literal_users.get(rel):
            continue
        # The RAW test source, not its string constants. "Mentioned" here has to
        # be the widest reading available, because this is the over-reporting
        # side of `.claude/rules/scope-claims.md`: a file wrongly flagged costs
        # one line in an exception list with a reason beside it, and a file
        # wrongly cleared is a change that ships with nothing run. The stem
        # rather than the basename, because the shape being hunted is
        # `ROOT / "cookbook" / f"{name}.py"` over a parametrize list of stems,
        # where the basename never appears anywhere.
        stem = Path(rel).stem
        if not _mentions(blob, stem):
            continue
        blind.append(rel)
    return sorted(blind)


def _mentions(blob: str, stem: str) -> bool:
    """Whole-word occurrence of `stem`, hyphens included as word characters.

    `\\b` alone splits on the hyphen, so `chart-slide` would match any test that
    says the word "chart". The lookarounds treat `-` as part of the word.
    """
    pattern = r"(?<![\w-])" + re.escape(stem) + r"(?![\w-])"
    return re.search(pattern, blob) is not None


# --------------------------------------------------------------------------
# Change set
# --------------------------------------------------------------------------


GREEN_MARKER = Path(".cache/day-mode/known-green")


def known_green(root: Path) -> str | None:
    """The revision the last full nightly passed on, or None.

    None is not "no changes". A caller that reads it as HEAD selects an empty
    change set and runs only the core, so every caller here must handle the
    absence in a visible line instead.
    """
    marker = root / GREEN_MARKER
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def record_green(root: Path, revision: str) -> None:
    """Record the revision the full suite just passed on. Atomic."""
    marker = root / GREEN_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(revision.strip() + "\n", encoding="utf-8")
    tmp.replace(marker)


def changed_files(root: Path, base: str | None) -> tuple[list[str], str]:
    """Files changed since `base`, plus the uncommitted and untracked ones.

    Returns the paths and a one-line description of where they came from, which
    the report prints: a selection is only auditable if the operator can see
    what it was computed against.
    """
    paths: set[str] = set()
    origin = []

    # `-z` on every one of these, and bytes rather than text. Without `-z` git
    # QUOTES any path with an unusual byte in it, so the caller gets
    # `"scripts/od\303\251.py"` -- quotes, escapes and all -- which matches no
    # tracked path and drops that file from the change set in silence.
    def git(*args: str) -> list[str]:
        return _git_z(root, *args)

    if base:
        paths.update(git("diff", "--name-only", "-z", base, "HEAD"))
        origin.append(f"committed since {base}")
    paths.update(git("diff", "--name-only", "-z", "HEAD"))
    paths.update(git("diff", "--name-only", "-z", "--cached"))
    paths.update(git("ls-files", "--others", "--exclude-standard", "-z"))
    origin.append("working tree and index")
    return sorted(paths), " + ".join(origin)
