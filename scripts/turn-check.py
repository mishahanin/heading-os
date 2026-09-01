#!/usr/bin/env python3
"""turn-check.py - the fast check a turn should not end without.

Answers one question about the uncommitted working tree: did the Python edits
made so far break anything a cheap check can see? It is deliberately NOT the
full suite. The full suite is `scripts/run-tests.py` and takes minutes; this
takes seconds, because something that runs at the end of every turn only helps
if nobody is tempted to skip it.

Whose edits. `git` knows a file changed, never who changed it, and this
workspace runs more than one session against one checkout. Pass
`--session-transcript` (the Stop hook passes the path Claude Code gives it) and
the changed set is narrowed to the files THIS session wrote, with the drop
count reported rather than swallowed. Without it the scope is the whole working
tree, which is right for a hand run from a terminal that belongs to no session.
Measured 2026-08-12: without this narrowing the hook blocked a turn over a
deliberately-red TDD test a parallel session had written a minute earlier.

Why it exists. On 2026-08-09 a one-line constant rename in
`scripts/wizard-verify-key.py` broke four tests, and that was discovered only
because a full suite happened to be run by hand later in the session. Nothing
was watching the end of a turn. Anthropic's own Claude Code guidance puts a
verification hook first for exactly this reason.

Three lanes, cheapest first, each bounded:

  compile  every changed .py through py_compile (milliseconds)
  import   every changed LIBRARY module imported in one subprocess. Restricted
           to `scripts/utils/`, `scripts/bridge_daemon/`, `scripts/inbox_pulse/`
           and `scripts/updaters/` on purpose: a top-level CLI script may call
           `ensure_venv()` at module scope and re-exec the interpreter, which is
           not something a hook should trigger.
  tests    the test files that name the changed modules, by stem. A changed
           `scripts/wizard-verify-key.py` maps to `tests/test_wizard_verify_key.py`
           (hyphens normalise to underscores, which is the mapping that would
           have caught the rename above). Files under `tests/contract/` are
           matched, then skipped and counted: a frozen contract is red between
           the approval commit and the implementation, on purpose. Tests marked
           `slow` are deselected and counted for the same class of reason: they
           sleep for real, they belong to the once-per-push suite, and a lane
           that takes a minute is a lane the operator learns to dread.

Every lane that spawns a child runs it under `PYTHON` below, never under
`sys.executable`. MEASURED 2026-08-31: run as the documented `python
scripts/turn-check.py`, `python` is `/usr/bin/python` 3.12 on this machine while
the project pins `.venv` 3.11, so the tests lane collected under the SYSTEM
interpreter and a Playwright test errored on a `~/.local` copy of the SDK that
the pinned environment does not use. The failure was the interpreter, not the
code, and the same run under `.venv/bin/python` was clean. `sys.executable` is
whatever launched this script; it is not the environment the suite is pinned to.

Usage:
    .venv/bin/python scripts/turn-check.py         # human output, exit 1 on failure
    .venv/bin/python scripts/turn-check.py --json  # machine output for the Stop hook
    .venv/bin/python scripts/turn-check.py --no-cache    # ignore the pass cache
    .venv/bin/python scripts/turn-check.py --timeout 60  # cap the test lane (default 120s)

Exit codes: 0 clean or nothing to check, 1 a lane failed, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.checkpoint_paths import state_root  # noqa: E402
from scripts.utils.colors import GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.session_scope import (current_transcript,  # noqa: E402
                                         narrow_with_scope)
from scripts.utils.venv_guard import venv_python  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# The interpreter every child lane runs under. Falls back to `sys.executable`
# only when the venv is genuinely absent (a fresh clone before `uv sync`), which
# is the one case where refusing would be worse than reporting under whatever is
# available. `.claude/hooks/turn-check.py` already resolves the same path for the
# automatic run; this makes the by-hand invocation behave identically.
_VENV = venv_python()
PYTHON = str(_VENV) if _VENV.exists() else sys.executable

ROOT = get_workspace_root()
STATE_PATH = state_root(ROOT) / "turn-check.json"

# Packages safe to import without side effects. A module outside these is still
# compiled and still gets its tests run; it is only spared the import probe.
IMPORT_SAFE_PREFIXES = (
    "scripts/utils/",
    "scripts/bridge_daemon/",
    "scripts/inbox_pulse/",
    "scripts/updaters/",
)

WATCHED_PREFIXES = ("scripts/", "tests/", ".claude/hooks/")

# A frozen Canopus contract is written RED at step 3 and stays red until the
# implementation lands at step 6. That is the method, not a defect. Running it at
# the end of every turn in between leaves the operator two bad choices: watch the
# hook block on a test whose failure IS the plan, or learn to ignore the hook.
# The second is worse and is the one that happens. Skipped by prefix, and counted
# out loud, because a narrowed check that prints like a complete one is the
# defect this script was already fixed for once (.claude/rules/scope-claims.md).
CONTRACT_PREFIX = "tests/contract/"

DEFAULT_TEST_TIMEOUT = 120

# Matched-file count at which the lane switches to `-n auto`.
#
# DERIVED from two measurements, not chosen. xdist costs about 7s to start its
# workers (a 2-test file: 0.04s serial, 7.41s parallel), and it repays that
# somewhere below 40 files (40 files: 25.95s serial, 18.90s parallel). 20 sits
# clear of the crossover in both directions, so an ordinary turn stays serial
# and exact while a fix campaign gets the parallelism it needs to finish inside
# the hook's budget at all.
PARALLEL_FILE_THRESHOLD = 20

# What `_deselected` returns when the count cannot be read at all.
#
# NOT zero. Under `-n auto` pytest stops printing "N deselected" in its summary
# entirely: measured on a fixture holding one fast and one slow test, the serial
# run says "1 passed, 1 deselected" and the parallel run says "1 passed". Zero
# would be a claim that nothing was excluded, and `.claude/rules/scope-claims.md`
# is explicit that silence about an exclusion reads as coverage. So the renderer
# gets a value it can tell apart from a real zero and says the count is unknown.
DESELECTED_UNKNOWN = -1

# Pytest's exit code for "no tests were collected". Ordinary here, because the
# test lane deselects the slow marker and a matched file can hold nothing else.
NO_TESTS_COLLECTED = 5

# xdist's own wording when two workers collected different suites. Matched as a
# literal on purpose: the retry it gates must fire for THIS cause and nothing
# else, so a substring loose enough to catch a second kind of failure would be
# a bug, not a convenience. See the retry in `lane_tests` for the measurement.
_COLLECTION_RACE = "Different tests were collected between"

# A module naming its own fast contract, for when the stem rule finds nothing.
# Repeatable, because six paths do not fit on one line.
#
# Anchored at column 0 with no leading whitespace allowed, so that an INDENTED
# example of the syntax - inside a docstring explaining the convention, which is
# exactly where one lives - is prose and not a declaration. Caught by
# `test_every_declaration_in_the_tree_points_at_a_real_file` the first time this
# module documented its own feature and thereby declared two tests that have
# never existed.
DECLARED_TESTS_RE = re.compile(r"^Tests:[ \t]*(.+)$", re.M)


def _rel(path: Path) -> str:
    """Repo-relative display path, falling back to the absolute one.

    `Path.relative_to` RAISES for anything outside the root, and this module
    promises never to raise: a check that crashes is worse than no check, since
    the hook then silently passes the turn.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _git(args: list[str]) -> list[str]:
    """Paths from a git command in the engine tree. Any failure yields no paths.

    Every caller passes `-z`, and it is not cosmetic. `core.quotePath` defaults
    to on, so both `diff --name-only` and `ls-files` C-quote a path holding any
    byte outside printable ASCII: a Cyrillic filename arrives as
    `"scripts/\\320\\261.py"`, which `ROOT / r` then resolves to a file that does
    not exist, and the edit is dropped without a word. This tool says it checks
    "the edits made in this turn", so a silent drop is a false coverage claim
    (`.claude/rules/scope-claims.md`). NUL separation also survives a path
    holding a newline, which `splitlines()` would cut in two.

    The flag is written at each call site rather than injected here, so the
    repo-wide guard in
    `tests/test_a_publisher_that_could_not_see_a_non_ascii_path.py` can read it.

    Paths are NOT stripped: with `-z` the bytes between separators are the exact
    name, and a leading or trailing space is part of it.

    Nor is the output read through subprocess text mode. Text mode turns on
    universal newlines and rewrites every CR byte to LF, with no `newline=` knob
    to switch it off, so `-z` fixes the quoting and leaves this untouched.
    MEASURED 2026-08-30: `docs/x\\r\\ny.md` and `docs/x\\ny.md` are two tracked
    files that `text=True` returns as one name. `ROOT / r` then resolves to
    nothing, `is_file()` is False, and the edit is dropped without a word - the
    same false coverage claim the paragraph above exists to prevent.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(ROOT), capture_output=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    decoded = out.stdout.decode("utf-8", "surrogateescape")
    return [path for path in decoded.split("\0") if path]


def changed_python_files() -> list[Path]:
    """Uncommitted .py edits in the watched trees, as existing absolute paths.

    Working tree only, never `origin/main..HEAD`: a turn check is about what is
    on disk right now, and committed work has already passed the commit gates.
    """
    rel = set(_git(["diff", "--name-only", "-z", "HEAD"]))
    rel |= set(_git(["ls-files", "-z", "--others", "--exclude-standard"]))
    out = []
    for r in sorted(rel):
        if not r.endswith(".py") or not r.startswith(WATCHED_PREFIXES):
            continue
        p = ROOT / r
        if p.is_file():
            out.append(p)
    return out


def deleted_python_files() -> list[str]:
    """Watched `.py` paths git reports as changed but that are gone from disk.

    Returned as repo-relative STRINGS, because there is no file to read. They
    feed the fingerprint only: a deletion is not something a compile or import
    lane can run against, but it absolutely changes what the surviving code
    does. Without it, a turn whose only change is `rm scripts/foo.py` left the
    changed-set hash byte-identical to the last pass, so the very turn that
    broke every importer of `foo` reported `cached`.
    """
    gone = []
    for r in sorted(set(_git(["diff", "--name-only", "-z", "HEAD"]))):
        if not r.endswith(".py") or not r.startswith(WATCHED_PREFIXES):
            continue
        if not (ROOT / r).is_file():
            gone.append(r)
    return gone


def fingerprint(paths: list[Path], deleted: "list[str] | tuple[str, ...]" = ()) -> str:
    """Content hash of the changed set, so an unchanged tree is checked once.

    Content, not mtime: a file rewritten with identical bytes is not a new
    thing to check, and an editor that touches mtime on save would otherwise
    re-run the whole lane set for nothing.

    `deleted` carries the paths that no longer exist. They have no bytes to
    hash, so their NAME goes in behind a marker; that is enough to make the
    hash move when a file disappears, which is the whole point.
    """
    h = hashlib.sha256()
    for p in paths:
        h.update(_rel(p).encode("utf-8"))
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    for r in sorted(deleted):
        h.update(r.encode("utf-8"))
        h.update(b"<deleted>")
    return h.hexdigest()


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    # UnicodeDecodeError is the gap between the two named here. The decode runs
    # inside `read_text`, so `json.loads` is never reached, and it is a
    # ValueError -- a sibling of `json.JSONDecodeError`, not a subclass of
    # OSError. A torn state file therefore took this whole lane down from
    # inside the Stop hook, where a raise is not a failed check but a failed
    # turn.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def write_state(data: dict) -> None:
    """Record the last passing fingerprint. A failed write costs nothing but a
    repeated check, so it is never allowed to fail the run."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        pass


def lane_compile(paths: list[Path]) -> list[str]:
    """Syntax. Cheapest possible signal, and it catches the whole class of edit
    that leaves a file unparseable."""
    import py_compile

    failures = []
    for p in paths:
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(p) + ".turncheck.pyc")
        except py_compile.PyCompileError as e:
            failures.append(f"{_rel(p)}: {e.msg.strip().splitlines()[-1]}")
        except OSError as e:
            failures.append(f"{_rel(p)}: {e}")
        finally:
            Path(str(p) + ".turncheck.pyc").unlink(missing_ok=True)
    return failures


def module_name(path: Path) -> str | None:
    """Dotted module name for an importable library path, else None."""
    rel = _rel(path).replace("\\", "/")
    if not rel.startswith(IMPORT_SAFE_PREFIXES) or rel.endswith("__init__.py"):
        return None
    return rel[: -len(".py")].replace("/", ".")


def lane_import(paths: list[Path]) -> list[str]:
    """Import every changed library module in ONE subprocess.

    One subprocess, not one per module: interpreter startup dominates, and a
    single failing import names itself in the traceback anyway.
    """
    modules = [m for m in (module_name(p) for p in paths) if m]
    if not modules:
        return []
    probe = "import importlib\n" + "\n".join(
        f"importlib.import_module({m!r})" for m in modules
    )
    try:
        out = subprocess.run(
            [PYTHON, "-c", probe],
            cwd=str(ROOT), capture_output=True, text=True,
            errors="replace", timeout=60,
        )
    except subprocess.TimeoutExpired:
        return [f"import probe timed out over {len(modules)} module(s)"]
    except OSError as e:
        return [f"import probe could not run: {e}"]
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()[-6:]
        return ["\n".join(tail) or f"import probe exited {out.returncode}"]
    return []


def _declared_paths(path: Path) -> list[str]:
    """The repo-relative test paths a module names in its own docstring.

    Unresolved and unfiltered - `declared_tests` keeps the ones that exist and
    `dangling_declarations` reports the ones that do not, because a caller that
    cannot tell those apart is how a renamed test file becomes silent zero
    coverage.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    for line in DECLARED_TESTS_RE.findall(text):
        for token in line.replace(",", " ").split():
            token = token.strip("`'\"")
            if token.startswith("tests/") and token.endswith(".py"):
                found.append(token)
    return found


def declared_tests(path: Path) -> list[Path]:
    """The declared test files that actually exist, resolved under the root."""
    return [ROOT / rel for rel in _declared_paths(path) if (ROOT / rel).is_file()]


def dangling_declarations(path: Path) -> list[str]:
    """Declared test paths with no file behind them."""
    return [rel for rel in _declared_paths(path) if not (ROOT / rel).is_file()]


def matching_tests(paths: list[Path]) -> list[Path]:
    """Test files that name the changed modules, plus changed test files, plus
    the ones a module declares for itself.

    Stem matching with hyphens normalised: `wizard-verify-key.py` and
    `test_wizard_verify_key.py` only line up once `-` becomes `_`, and that
    pair is the exact miss this script was written for.

    The stem rule finds tests NAMED after a module and nothing else, so a module
    whose tests are named after the behaviour they pin matches nothing at all.
    `scripts/checkpoint-paths.py` was that case: fifteen test files exercise it,
    the stem `checkpoint_paths` matched none, and editing it ran zero tests under
    a lane that printed `clean`. A module closes that by naming its own fast
    contract in its docstring:

        Tests: tests/test_a.py, tests/test_b.py

    Additive, and the author picks the members. Matching by content instead was
    measured and rejected - the fifteen files that merely mention
    `checkpoint-paths` cost 60.6s, which is the end-of-turn wait this lane exists
    to avoid. A declaration that points at a file which is not there is dropped
    here and reported by `tests/test_turn_check.py`, never silently honoured.
    """
    tests_dir = ROOT / "tests"
    if not tests_dir.is_dir():
        return []
    # A LIST per basename, not one path. Keyed by `p.name`, two test files with
    # the same name in different subdirectories collided and one was silently
    # dropped from ever being matched -- so a module whose only matching test
    # was the loser ran ZERO tests under a lane that printed `clean`, which is
    # the silent-zero-coverage failure this script exists to prevent. Measured
    # 2026-08-24: `test_fleet_health.py` and `test_state.py` each exist twice.
    all_tests: dict[str, list[Path]] = {}
    for tp in tests_dir.rglob("test_*.py"):
        all_tests.setdefault(tp.name, []).append(tp)
    picked: set[Path] = set()
    for p in paths:
        rel = _rel(p).replace("\\", "/")
        if rel.startswith("tests/"):
            picked.add(p)
            continue
        picked.update(declared_tests(p))
        stem = p.stem.replace("-", "_")
        for name, tps in all_tests.items():
            body = name[len("test_"): -len(".py")]
            if body == stem or body.startswith(stem + "_"):
                picked.update(tps)
    return sorted(picked)


def is_contract(path: Path) -> bool:
    """A file under the frozen-contract tree, whose red state is the plan."""
    return _rel(path).replace("\\", "/").startswith(CONTRACT_PREFIX)


def _deselected(body: str, parallel: bool = False) -> int:
    """How many tests pytest dropped for the marker expression.

    Read back from pytest's own summary rather than counted here, because this
    lane never imports the target files and so cannot see their markers. A line
    it cannot parse reports 0, which under-claims the exclusion instead of
    inventing one.
    """
    match = re.search(r"(\d+) deselected", body)
    if match:
        return int(match.group(1))
    if parallel:
        # xdist prints no deselection summary at all, so "no match" here means
        # "cannot tell", not "none". Returning 0 would print nothing and read as
        # full coverage over tests that really were dropped.
        return DESELECTED_UNKNOWN
    return 0


def _files_holding_no_test(targets: list[Path], timeout: int) -> int:
    """How many of the matched files hold no test AT ALL.

    Reached only when pytest answers exit 5, which says zero tests ran across
    the whole matched set and carries no per-file breakdown. Two different
    things produce it - a file whose only tests carry the `slow` marker this
    lane deselects, and a file with no test in it - and the lane used to guess
    between them with `0 if dropped else len(targets)`. That guess is wrong the
    moment both are present: MEASURED 2026-08-30 over one all-slow file beside
    one holding a bare helper, the lane reported zero empties while neither file
    ran anything, so the empty one went unnamed. A silent exclusion reads as
    coverage (`.claude/rules/scope-claims.md`).

    So the answer is resolved rather than guessed, by collecting the same files
    WITHOUT the marker filter and asking which of them yielded no node id. That
    is a second subprocess, and it is affordable precisely because it only runs
    on the path where nothing executed: collection, not execution.

    Any probe that does not come back clean returns `len(targets)`, widening to
    every matched file rather than reporting a zero nothing established -
    obligation 3 of the same rule.

    Node ids are relative to the rootdir PYTEST picks, which is the repo for a
    file under `tests/` and the containing directory for a probe somewhere else,
    so the id cannot be compared to a repo-relative path directly. Matching is
    by path suffix, and an id that could belong to two different targets makes
    the whole answer unknown rather than an arbitrary pick.
    """
    args = [
        PYTHON, "-m", "pytest", "-q", "-p", "no:randomly",
        "--collect-only", "--no-header", *[str(t) for t in targets],
    ]
    try:
        out = subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True,
            errors="replace", timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return len(targets)
    if out.returncode not in (0, NO_TESTS_COLLECTED):
        return len(targets)

    node_paths = {line.strip().split("::", 1)[0].replace("\\", "/")
                  for line in (out.stdout or "").splitlines()
                  if "::" in line}
    node_paths.discard("")
    holds_a_test = set()
    for node in node_paths:
        owners = [t for t in targets
                  if t.as_posix() == node or t.as_posix().endswith("/" + node)]
        if len(owners) != 1:
            return len(targets)
        holds_a_test.add(owners[0])
    return sum(1 for t in targets if t not in holds_a_test)


def lane_tests(paths: list[Path],
               timeout: int) -> tuple[list[str], int, int, int, int, int]:
    """Run the matched tests, minus the ones marked slow.

    Returns (failures, test files run, contract files skipped, tests deselected,
    files that collected nothing, files left unmeasured).

    The annotation said four values while the body returned five, which is the
    kind of stale contract nothing checks.

    A run that never finished reports `tests_run=0` and counts its files as
    unmeasured. Until 2026-08-29 both non-completion paths, the wall-clock
    timeout and the `OSError`, returned `tests_run=len(targets)` and a plain
    failure. MEASURED that day by forcing `TimeoutExpired`: the result carried
    `"tests_run": 2` when pytest had been killed and zero tests had run, and the
    Stop hook rendered "a failure here is almost always real" over it. A number
    for work that did not happen is worse than no number.

    `-m "not slow"` is the difference between a check that runs at the end of
    every turn and one the operator learns to dread. Measured 2026-08-22: an edit
    to `.claude/hooks/checkpoint-offer.py` matched a checkpoint/unattended set
    whose tests sleep for real - 122s over 273 tests, one of them 22.8s alone -
    and the Stop hook duly sat there for about a minute after every answer. Those
    tests are not wrong; they are timing tests, and a timing test that does not
    wait proves nothing. They belong to `scripts/run-tests.py`, which runs once
    per push and where a minute is affordable. The count comes back so the drop
    can be named rather than swallowed.
    """
    picked = matching_tests(paths)
    targets = [t for t in picked if not is_contract(t)]
    skipped = len(picked) - len(targets)
    if not targets:
        return [], 0, skipped, 0, 0, 0
    parallel = len(targets) >= PARALLEL_FILE_THRESHOLD
    args = [
        PYTHON, "-m", "pytest", "-q", "-p", "no:randomly",
        "-m", "not slow", "--no-header", "-x",
        # PARALLEL. This lane ran serially on a 16-core machine while its own
        # sibling `scripts/run-tests.py:78` already passed `-n auto`, and the
        # difference is what turned the Stop hook into a loop: on 2026-08-31 it
        # refused five turns running, each re-run with a longer cap came back
        # CLEAN, and the matched set grew 74 -> 75 -> 83 -> 89 -> 111 files under
        # a fleet of parallel agents while the check itself could never finish
        # inside the 80s the hook allows.
        #
        # MEASURED that day, on the real changed set and on a loaded machine:
        #   40 files   serial 25.95s   parallel 18.90s   (1.37x)
        #   78 files, 1932 tests       parallel 45.21s
        # Serial does not fit the hook's budget at campaign size; parallel does.
        # The speed-up is modest because worker start-up is a fixed cost, so the
        # budget was widened as well rather than resting on the flag alone.
        #
        # `-n auto` is safe for this suite by precedent, not by assumption:
        # `run-tests.py` has run the WHOLE suite that way for a long time, and
        # the ordering defects xdist can mask are separately guarded (the
        # root conftest arms its own isolation per test).
        #
        # ONLY above a threshold, and that is not tuning for its own sake.
        # MEASURED: a 2-test file costs 0.04s serial and 7.41s parallel, because
        # xdist pays about seven seconds to start its workers before it runs
        # anything. An ordinary turn touches a handful of files, so parallelising
        # unconditionally would make the common case a hundred times slower while
        # fixing only the campaign case.
        *(["-n", "auto"] if parallel else []),
        *[str(t) for t in targets],
    ]
    try:
        out = subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True,
            errors="replace", timeout=timeout
        )
        # ONE retry, on ONE error string, and only under `-n auto`.
        #
        # Each xdist worker collects the suite independently, a moment apart.
        # When a test FILE lands in `tests/` between gw0's collection and gw9's,
        # their sets differ and xdist aborts the whole lane with
        # "Different tests were collected between gw0 and gw9". Nothing failed;
        # the corpus moved under the collector.
        #
        # MEASURED 2026-09-01, while a fleet of agents was creating test files:
        #   serial collection, three runs back to back   20799, 20799, 20799
        #   parallel collection, minutes apart           20806, then 20808
        # Sixteen test files were written in the preceding thirty minutes, one
        # of them 45 seconds before a run. The serial number never moved, so the
        # cause is the race and not a conftest that generates tests at random.
        #
        # This matters beyond the wasted run. The hook tells the operator "a
        # failure here is almost always real", and a check that cries wolf
        # teaches its reader to skip it, which is the one failure mode a Stop
        # hook cannot survive.
        #
        # Retrying ONE string, not any failure, is what keeps a red meaningful.
        # A corpus that collects nondeterministically mismatches on BOTH
        # attempts and is still reported; the file-arrival race almost never
        # repeats, because the second collection starts after the write that
        # broke the first.
        #
        # That second cause is not hypothetical, and this comment named only the
        # race until a shard auditor found it the same day. A `gzip.compress()`
        # call at MODULE scope inside a `parametrize` decorator wrote the
        # current epoch second into byte 4 of its own fixture, so each worker
        # built a different test id and EVERY parallel run aborted. Fixed at
        # `tests/test_a_scan_that_called_trojan_source_clean.py` with `mtime=0`,
        # and guarded by `tests/test_a_fixture_that_changed_id_every_second.py`.
        #
        # The retry was right for both: it absorbs the race and reports the
        # nondeterminism. The lesson is about the DIAGNOSIS, not the fix. Two
        # causes produce one error string, the measurement here (serial stable,
        # parallel growing) established the first and said nothing about the
        # second, and a retry that had swallowed a repeat would have hidden a
        # broken gate for as long as anyone cared to look.
        if out.returncode != 0 and parallel and _COLLECTION_RACE in (
                (out.stdout or "") + (out.stderr or "")):
            out = subprocess.run(
                args, cwd=str(ROOT), capture_output=True, text=True,
                errors="replace", timeout=timeout
            )
    except subprocess.TimeoutExpired:
        return [
            f"the matched tests did not finish in {timeout}s "
            f"({len(targets)} file(s)); run them yourself or raise --timeout"
        ], 0, skipped, 0, 0, len(targets)
    except OSError as e:
        return [f"pytest could not run: {e}"], 0, skipped, 0, 0, len(targets)
    body = (out.stdout or "") + (out.stderr or "")
    dropped = _deselected(body, parallel=parallel)
    # Exit 5 is "no tests collected", and in NO form of it did a test fail.
    # Two causes reach it: the marker expression deselected everything (the
    # ordinary outcome for an all-slow file), or a matched file collected
    # nothing at all - a test file created empty at the start of a TDD slice,
    # or one holding only helpers. Requiring `dropped` conflated the two and
    # blocked the turn on the second, which is the exact false block this lane
    # exists to avoid. The second case is reported instead, because a matched
    # file that ran nothing is an exclusion, and a silent exclusion reads as
    # coverage.
    #
    # The two causes are NOT exclusive, which `0 if dropped else len(targets)`
    # assumed. A matched set holding one all-slow file and one file with no test
    # in it exits 5 with `dropped >= 1`, and that expression then reported zero
    # empties, so the file that ran nothing went completely unnamed. MEASURED
    # 2026-08-30 over exactly that pair. `_files_holding_no_test` resolves the
    # split by collecting without the marker filter instead of guessing from
    # `dropped`.
    if out.returncode == NO_TESTS_COLLECTED:
        return ([], len(targets), skipped, dropped,
                _files_holding_no_test(targets, timeout), 0)
    if out.returncode != 0:
        tail = [ln for ln in body.strip().splitlines() if ln.strip()][-12:]
        return (["\n".join(tail) or f"pytest exited {out.returncode}"],
                len(targets), skipped, dropped, 0, 0)
    return [], len(targets), skipped, dropped, 0, 0


def run(timeout: int, use_cache: bool, transcript=None) -> dict:
    """Run the lanes and return a result dict. Never raises."""
    paths = changed_python_files()
    # `scope_known` is False when the write set could not be established at all:
    # no transcript, an absent or unreadable one, or ANY ONE unreadable subagent
    # sidecar (a session with a hundred of them has a hundred ways to get here).
    # The narrowing then keeps every candidate and reports zero drops, which is
    # indistinguishable from a clean scope that dropped nothing, so the flag
    # travels in the result and the Stop hook names the widening out loud.
    # Without it, MEASURED 2026-08-31 over a malformed transcript, the hook told
    # the operator a break was in "the uncommitted Python edits in this turn"
    # with no exclusion line, over a file another session had written.
    paths, foreign, scope_known = narrow_with_scope(paths, transcript)
    deleted = deleted_python_files()
    if not paths:
        reason = "no uncommitted Python edits"
        if foreign:
            # Same disjunction as `_foreign_note`, and for the same reason: the
            # scope establishes "no write recorded by this session", which is
            # another session's file OR a Bash edit made here.
            reason = (f"no uncommitted Python edits by this session "
                      f"({foreign} by another session, or edited here "
                      f"through Bash)")
        if deleted:
            reason += f" ({len(deleted)} deleted, nothing left to run against)"
        return {"status": "idle", "reason": reason, "files": 0,
                "skipped_foreign": foreign, "deleted": len(deleted)}

    fp = fingerprint(paths, deleted)
    if use_cache and read_state().get("last_pass") == fp:
        return {"status": "cached", "reason": "unchanged since the last pass",
                "files": len(paths), "skipped_foreign": foreign}

    failures = lane_compile(paths)
    lane = "compile"
    if not failures:
        failures, lane = lane_import(paths), "import"
    tests_run = 0
    skipped_contract = 0
    deselected_slow = 0
    collected_nothing = 0
    unmeasured = 0
    if not failures:
        (failures, tests_run, skipped_contract, deselected_slow,
         collected_nothing, unmeasured) = lane_tests(paths, timeout)
        lane = "tests"

    if failures:
        return {"status": "fail", "lane": lane, "failures": failures,
                "files": len(paths), "tests_run": tests_run,
                "skipped_foreign": foreign,
                "scope_unknown": not scope_known,
                "skipped_contract": skipped_contract,
                "deselected_slow": deselected_slow,
                "collected_nothing": collected_nothing,
                "unmeasured": unmeasured}

    write_state({"last_pass": fp, "files": len(paths), "tests_run": tests_run})
    return {"status": "pass", "files": len(paths), "tests_run": tests_run,
            "skipped_foreign": foreign, "scope_unknown": not scope_known,
            "skipped_contract": skipped_contract,
            "deselected_slow": deselected_slow,
            "collected_nothing": collected_nothing,
            "unmeasured": unmeasured}


def _foreign_note(result: dict) -> str:
    """What the scope left out, named. Silence here is how a narrowed check
    starts reading as a complete one.

    The sentence is a disjunction because that is all the method establishes.
    `session_scope.files_written` reads this session's transcript AND its
    subagent sidecars for `Write`/`Edit`/`MultiEdit`/`NotebookEdit` calls, so a
    dropped file is one carrying no such call: another session's work, OR an
    edit this session made through `Bash`, which records a command and never a
    path. Until 2026-08-30 this said flatly "written by another session" while
    the scope read only the parent transcript, and that day it labelled 37 files
    written by this session's own subagents as a stranger's."""
    count = result.get("skipped_foreign") or 0
    if not count:
        return ""
    return (f" {GRAY}[{count} changed file(s) written by another session, or "
            f"edited here through Bash, not checked]{RESET}")


def _contract_note(result: dict) -> str:
    """The frozen contracts this run declined to judge. Same reason as above:
    the operator has to be able to see that the number of tests run is smaller
    than the number of tests matched."""
    count = result.get("skipped_contract") or 0
    if not count:
        return ""
    return (f" {GRAY}[{count} frozen-contract file(s) not run: red by design "
            f"until the slice implements them]{RESET}")


def _slow_note(result: dict) -> str:
    """The timing tests this lane hands to the full suite. Named for the same
    reason as the two notes above: an exclusion nobody can see reads as
    coverage."""
    count = result.get("deselected_slow") or 0
    if count == DESELECTED_UNKNOWN:
        # The parallel lane. Saying nothing here would be the exact defect this
        # note exists to prevent, one level up: the exclusion is still happening,
        # only the COUNT is unreadable, so name the state instead of the number.
        return (f" {GRAY}[slow test(s) deselected here, count unavailable "
                f"under the parallel lane: run `python scripts/run-tests.py` "
                f"for those]{RESET}")
    if not count:
        return ""
    return (f" {GRAY}[{count} slow test(s) not run here: "
            f"run `python scripts/run-tests.py` for those]{RESET}")


def _empty_note(result: dict) -> str:
    """The matched files that collected no test at all.

    Same reason as the three notes above, and one more: pytest answers exit 5
    for this, which the lane used to read as a failure and block the turn on.
    Not failing it must not mean saying nothing about it, or a `test_*.py` that
    holds no test reads as a passing lane.

    The count comes from `_files_holding_no_test`, so it names files with no
    test in them and not files whose tests were merely deselected; `_slow_note`
    reports those separately."""
    count = result.get("collected_nothing") or 0
    if not count:
        return ""
    return (f" {GRAY}[{count} matched file(s) collected no tests: "
            f"nothing ran, and nothing failed]{RESET}")


def _unmeasured_note(result: dict) -> str:
    """The files the lane never got an answer about.

    A run killed by the wall clock, or one where pytest could not start, judged
    nothing. Counting those files as run was a claim about work that did not
    happen, which `.claude/rules/scope-claims.md` calls the defect that gets
    trusted and quoted back later."""
    count = result.get("unmeasured") or 0
    if not count:
        return ""
    return (f" {GRAY}[{count} matched file(s) left unmeasured: the lane did not "
            f"finish, so nothing about them is known]{RESET}")


def _notes(result: dict) -> str:
    return (_foreign_note(result) + _contract_note(result) + _slow_note(result)
            + _empty_note(result) + _unmeasured_note(result))


def render(result: dict) -> str:
    status = result["status"]
    if status in ("idle", "cached"):
        return f"{GRAY}turn-check: {result['reason']}{RESET}" + _notes(result)
    if status == "pass":
        return (f"{GREEN}turn-check: clean{RESET} "
                f"{GRAY}({result['files']} changed file(s), "
                f"{result['tests_run']} test file(s)){RESET}" + _notes(result))
    # "failed" is a verdict about the code. A lane that never finished reached
    # no verdict, so it does not get that word.
    verb = "did not finish" if result.get("unmeasured") else "failed"
    head = f"{RED}turn-check: {result['lane']} lane {verb}{RESET}" + _notes(result)
    return head + "\n" + "\n".join(result["failures"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fast check over uncommitted Python edits."
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit the result as JSON instead of prose.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Re-run even if this exact tree already passed.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TEST_TIMEOUT,
                        help=f"Cap the test lane, seconds (default {DEFAULT_TEST_TIMEOUT}).")
    parser.add_argument("--session-transcript", default=None,
                        help="Session transcript; narrows the check to files this "
                             "session wrote. Omitted means the whole working tree.")
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        print(f"{YELLOW}--timeout must be positive{RESET}", file=sys.stderr)
        return 2

    result = run(timeout=args.timeout, use_cache=not args.no_cache,
                 transcript=args.session_transcript or current_transcript())
    print(json.dumps(result, ensure_ascii=False) if args.json else render(result))
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
