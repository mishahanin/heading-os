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

Usage:
    python scripts/turn-check.py                # human output, exit 1 on failure
    python scripts/turn-check.py --json         # machine output for the Stop hook
    python scripts/turn-check.py --no-cache     # ignore the pass cache
    python scripts/turn-check.py --timeout 60   # cap the test lane (default 120s)

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

from scripts.utils.colors import GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.session_scope import current_transcript, narrow  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
STATE_PATH = ROOT / ".claude" / "state" / "turn-check.json"

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

# Pytest's exit code for "no tests were collected". Ordinary here, because the
# test lane deselects the slow marker and a matched file can hold nothing else.
NO_TESTS_COLLECTED = 5

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
    """Run a git command in the engine tree. Any failure yields no paths."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def changed_python_files() -> list[Path]:
    """Uncommitted .py edits in the watched trees, as existing absolute paths.

    Working tree only, never `origin/main..HEAD`: a turn check is about what is
    on disk right now, and committed work has already passed the commit gates.
    """
    rel = set(_git(["diff", "--name-only", "HEAD"]))
    rel |= set(_git(["ls-files", "--others", "--exclude-standard"]))
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
    for r in sorted(set(_git(["diff", "--name-only", "HEAD"]))):
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
    except (OSError, json.JSONDecodeError):
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
            [sys.executable, "-c", probe],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
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


def _deselected(body: str) -> int:
    """How many tests pytest dropped for the marker expression.

    Read back from pytest's own summary rather than counted here, because this
    lane never imports the target files and so cannot see their markers. A line
    it cannot parse reports 0, which under-claims the exclusion instead of
    inventing one.
    """
    match = re.search(r"(\d+) deselected", body)
    return int(match.group(1)) if match else 0


def lane_tests(paths: list[Path], timeout: int) -> tuple[list[str], int, int, int]:
    """Run the matched tests, minus the ones marked slow.

    Returns (failures, test files run, contract files skipped, tests deselected).

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
        return [], 0, skipped, 0, 0
    args = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
        "-m", "not slow", "--no-header", "-x", *[str(t) for t in targets],
    ]
    try:
        out = subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return [
            f"the matched tests did not finish in {timeout}s "
            f"({len(targets)} file(s)); run them yourself or raise --timeout"
        ], len(targets), skipped, 0, 0
    except OSError as e:
        return [f"pytest could not run: {e}"], len(targets), skipped, 0, 0
    body = (out.stdout or "") + (out.stderr or "")
    dropped = _deselected(body)
    # Exit 5 is "no tests collected", and in NO form of it did a test fail.
    # Two causes reach it: the marker expression deselected everything (the
    # ordinary outcome for an all-slow file), or a matched file collected
    # nothing at all - a test file created empty at the start of a TDD slice,
    # or one holding only helpers. Requiring `dropped` conflated the two and
    # blocked the turn on the second, which is the exact false block this lane
    # exists to avoid. The second case is reported instead, because a matched
    # file that ran nothing is an exclusion, and a silent exclusion reads as
    # coverage.
    if out.returncode == NO_TESTS_COLLECTED:
        return [], len(targets), skipped, dropped, (0 if dropped else len(targets))
    if out.returncode != 0:
        tail = [ln for ln in body.strip().splitlines() if ln.strip()][-12:]
        return (["\n".join(tail) or f"pytest exited {out.returncode}"],
                len(targets), skipped, dropped, 0)
    return [], len(targets), skipped, dropped, 0


def run(timeout: int, use_cache: bool, transcript=None) -> dict:
    """Run the lanes and return a result dict. Never raises."""
    paths = changed_python_files()
    paths, foreign = narrow(paths, transcript)
    deleted = deleted_python_files()
    if not paths:
        reason = "no uncommitted Python edits"
        if foreign:
            reason = f"no uncommitted Python edits by this session ({foreign} by another)"
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
    if not failures:
        (failures, tests_run, skipped_contract, deselected_slow,
         collected_nothing) = lane_tests(paths, timeout)
        lane = "tests"

    if failures:
        return {"status": "fail", "lane": lane, "failures": failures,
                "files": len(paths), "tests_run": tests_run,
                "skipped_foreign": foreign,
                "skipped_contract": skipped_contract,
                "deselected_slow": deselected_slow,
                "collected_nothing": collected_nothing}

    write_state({"last_pass": fp, "files": len(paths), "tests_run": tests_run})
    return {"status": "pass", "files": len(paths), "tests_run": tests_run,
            "skipped_foreign": foreign, "skipped_contract": skipped_contract,
            "deselected_slow": deselected_slow,
            "collected_nothing": collected_nothing}


def _foreign_note(result: dict) -> str:
    """What the scope left out, named. Silence here is how a narrowed check
    starts reading as a complete one."""
    count = result.get("skipped_foreign") or 0
    if not count:
        return ""
    return f" {GRAY}[{count} changed file(s) written by another session, not checked]{RESET}"


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
    if not count:
        return ""
    return (f" {GRAY}[{count} slow test(s) not run here: "
            f"run `python scripts/run-tests.py` for those]{RESET}")


def _empty_note(result: dict) -> str:
    """The matched files that collected no test at all.

    Same reason as the three notes above, and one more: pytest answers exit 5
    for this, which the lane used to read as a failure and block the turn on.
    Not failing it must not mean saying nothing about it, or a `test_*.py` that
    holds no test reads as a passing lane."""
    count = result.get("collected_nothing") or 0
    if not count:
        return ""
    return (f" {GRAY}[{count} matched file(s) collected no tests: "
            f"nothing ran, and nothing failed]{RESET}")


def _notes(result: dict) -> str:
    return (_foreign_note(result) + _contract_note(result) + _slow_note(result)
            + _empty_note(result))


def render(result: dict) -> str:
    status = result["status"]
    if status in ("idle", "cached"):
        return f"{GRAY}turn-check: {result['reason']}{RESET}" + _notes(result)
    if status == "pass":
        return (f"{GREEN}turn-check: clean{RESET} "
                f"{GRAY}({result['files']} changed file(s), "
                f"{result['tests_run']} test file(s)){RESET}" + _notes(result))
    head = f"{RED}turn-check: {result['lane']} lane failed{RESET}" + _notes(result)
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
