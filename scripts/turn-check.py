#!/usr/bin/env python3
"""turn-check.py - the fast check a turn should not end without.

Answers one question about the uncommitted working tree: did the Python edits
made so far break anything a cheap check can see? It is deliberately NOT the
full suite. The full suite is `scripts/run-tests.py` and takes minutes; this
takes seconds, because something that runs at the end of every turn only helps
if nobody is tempted to skip it.

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
           have caught the rename above).

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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
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

DEFAULT_TEST_TIMEOUT = 120


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


def fingerprint(paths: list[Path]) -> str:
    """Content hash of the changed set, so an unchanged tree is checked once.

    Content, not mtime: a file rewritten with identical bytes is not a new
    thing to check, and an editor that touches mtime on save would otherwise
    re-run the whole lane set for nothing.
    """
    h = hashlib.sha256()
    for p in paths:
        h.update(_rel(p).encode("utf-8"))
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
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


def matching_tests(paths: list[Path]) -> list[Path]:
    """Test files that name the changed modules, plus changed test files.

    Stem matching with hyphens normalised: `wizard-verify-key.py` and
    `test_wizard_verify_key.py` only line up once `-` becomes `_`, and that
    pair is the exact miss this script was written for.
    """
    tests_dir = ROOT / "tests"
    if not tests_dir.is_dir():
        return []
    all_tests = {p.name: p for p in tests_dir.rglob("test_*.py")}
    picked: set[Path] = set()
    for p in paths:
        rel = _rel(p).replace("\\", "/")
        if rel.startswith("tests/"):
            picked.add(p)
            continue
        stem = p.stem.replace("-", "_")
        for name, tp in all_tests.items():
            body = name[len("test_"): -len(".py")]
            if body == stem or body.startswith(stem + "_"):
                picked.add(tp)
    return sorted(picked)


def lane_tests(paths: list[Path], timeout: int) -> tuple[list[str], int]:
    """Run the matched tests. Returns (failures, number of test files run)."""
    targets = matching_tests(paths)
    if not targets:
        return [], 0
    args = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
        "--no-header", "-x", *[str(t) for t in targets],
    ]
    try:
        out = subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return [
            f"the matched tests did not finish in {timeout}s "
            f"({len(targets)} file(s)); run them yourself or raise --timeout"
        ], len(targets)
    except OSError as e:
        return [f"pytest could not run: {e}"], len(targets)
    if out.returncode != 0:
        body = (out.stdout or "") + (out.stderr or "")
        tail = [ln for ln in body.strip().splitlines() if ln.strip()][-12:]
        return ["\n".join(tail) or f"pytest exited {out.returncode}"], len(targets)
    return [], len(targets)


def run(timeout: int, use_cache: bool) -> dict:
    """Run the lanes and return a result dict. Never raises."""
    paths = changed_python_files()
    if not paths:
        return {"status": "idle", "reason": "no uncommitted Python edits", "files": 0}

    fp = fingerprint(paths)
    if use_cache and read_state().get("last_pass") == fp:
        return {"status": "cached", "reason": "unchanged since the last pass",
                "files": len(paths)}

    failures = lane_compile(paths)
    lane = "compile"
    if not failures:
        failures, lane = lane_import(paths), "import"
    tests_run = 0
    if not failures:
        failures, tests_run = lane_tests(paths, timeout)
        lane = "tests"

    if failures:
        return {"status": "fail", "lane": lane, "failures": failures,
                "files": len(paths), "tests_run": tests_run}

    write_state({"last_pass": fp, "files": len(paths), "tests_run": tests_run})
    return {"status": "pass", "files": len(paths), "tests_run": tests_run}


def render(result: dict) -> str:
    status = result["status"]
    if status in ("idle", "cached"):
        return f"{GRAY}turn-check: {result['reason']}{RESET}"
    if status == "pass":
        return (f"{GREEN}turn-check: clean{RESET} "
                f"{GRAY}({result['files']} changed file(s), "
                f"{result['tests_run']} test file(s)){RESET}")
    head = f"{RED}turn-check: {result['lane']} lane failed{RESET}"
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
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        print(f"{YELLOW}--timeout must be positive{RESET}", file=sys.stderr)
        return 2

    result = run(timeout=args.timeout, use_cache=not args.no_cache)
    print(json.dumps(result, ensure_ascii=False) if args.json else render(result))
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
