#!/usr/bin/env python3
"""You could not INSTALL a daemon from a YARD. You could START one.

`CLAUDE.md` forbade "daemon install, restart or uninstall" from a worktree, and
exactly three files carried those words in their names --
`install-daemon-service.sh`, `restart-daemon-service.sh`,
`uninstall-daemon-service.sh`. Those three were guarded. Nothing else was. The
prohibition had been implemented as a literal match against a list of verbs, and
"start" was not on the list.

MEASURED 2026-09-03: a second Exchange mail daemon (PID 3598127) ran for twelve
hours out of a worktree, beside the operator's real one in HELM. Nothing spawned
it by hand. `/prime` -> `scripts/prime-health-parallel.py` -> the sync-exchange
pulse read liveness from `.sync-exchange/daemon.pid` INSIDE the checkout; a
fresh worktree has no such file, so the HELM systemd unit was invisible to the
check that then "helpfully" started another.

THE RULE IS NOW A CATEGORY, operator directive 2026-09-03: from a YARD, take no
action after which a running process on this machine appears, disappears, or
changes behaviour. The line runs through EXECUTION, not through editing --
writing the source of a daemon in a worktree is ordinary engine work and is what
a YARD is for.

THIS FILE HOLDS TWO THINGS, and the second is why it exists rather than being
seventeen more rows in the older registry:

1. A NAMED REGISTRY, so a guard cannot be quietly deleted.
2. A DISCOVERY NET derived from the tree, so a daemon entry point ADDED
   tomorrow is caught without anyone remembering to add a row. A hand-kept list
   of daemons is the same defect as a hand-kept list of verbs, one level up.

Run: python3 -m pytest tests/test_a_prohibition_written_as_a_list_of_verbs.py
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.repo_files import tracked_paths

ROOT = Path(__file__).resolve().parent.parent
GUARD = "require_main_clone"


# ============================================================
# 1. The named registry
# ============================================================

# Long-running processes, and everything that starts, stops, installs,
# supervises or health-checks one. Guarded as the first statement of `main()`.
DAEMON_ENTRY_POINTS = (
    # the daemons themselves
    "bridge-daemon.py",
    "sync-exchange-daemon.py",
    "fireside-bot-daemon.py",
    "sentinel.py",
    "inbox_pulse/daemon.py",
    # the spawners: where the duplicate is born
    "sync-exchange-pulse.py",
    "fireside-pulse.py",
    "ollama-guard.py",
    "ops-radar.py",
    # supervision and health, guarded too -- the rule is categorical, not
    # proportional to risk, so a reader that only looks is still guarded
    "daemon-watchdog.py",
    "daemon-fleet-health.py",
    "setup-daemon-healthchecks.py",
    "setup-fireside-healthchecks.py",
    # installers and unit management
    "install-bridge-service-mac.py",
    "update-manager.py",
    "setup.py",
)

# Guarded, but NOT as the first statement of main(), each for a stated reason.
GUARDED_LATER = {
    "fireside-bot.py":
        "only the `poll` subcommand is a daemon entry point -- it sits in "
        "`while True: get_updates(timeout=25)`. The other subcommands are "
        "ordinary CLI work and stay open from a worktree, the same shape as "
        "the `memory-index build` guard.",
}

# Shell entry points, guarded by scripts/lib/require-main-clone.sh.
GUARDED_SHELL = (
    "restart-bridge-daemon.sh",
    "uninstall-bridge-service.sh",
)


def _script(name: str) -> Path:
    return ROOT / "scripts" / name


def _source(name: str) -> str:
    return _script(name).read_text(encoding="utf-8")


def _main_node(name: str) -> ast.FunctionDef:
    mains = [n for n in ast.parse(_source(name)).body
             if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1, f"{name}: expected exactly one module-level main()"
    return mains[0]


def _guard_calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == GUARD]


def _body_without_docstring(fn: ast.FunctionDef) -> list[ast.stmt]:
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return body


def test_the_registry_is_the_size_it_was_measured_at():
    """A floor outside every loop below. MEASURED 2026-09-03: 16 + 1 + 2."""
    assert len(DAEMON_ENTRY_POINTS) == 16
    assert len(GUARDED_LATER) == 1
    assert len(GUARDED_SHELL) == 2
    for name in (*DAEMON_ENTRY_POINTS, *GUARDED_LATER, *GUARDED_SHELL):
        assert _script(name).is_file(), f"{name} is no longer in scripts/"


@pytest.mark.parametrize("name", DAEMON_ENTRY_POINTS)
def test_the_guard_is_the_first_statement_of_main(name):
    body = _body_without_docstring(_main_node(name))
    assert body, f"{name}: main() has no body"

    # `setup.py` puts two stdlib-only lines first on purpose; see its comment.
    # Anything else must guard before it does anything at all.
    head = body[:3] if name == "setup.py" else body[:1]
    calls = [c for stmt in head for c in _guard_calls(stmt)]
    assert len(calls) == 1, (
        f"{name}: the guard is not among the first statements of main(). "
        f"A daemon entry point that does ANY work before refusing has already "
        f"read a PID file, opened a socket, or decided to spawn.")
    assert isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "__file__", (
        f"{name}: the guard must be passed the SCRIPT's own path, or a YARD "
        f"script launched by absolute path from a HELM shell answers "
        f"'main clone'.")


@pytest.mark.parametrize("name", sorted(GUARDED_LATER))
def test_the_late_guards_are_present_and_deliberate(name):
    calls = _guard_calls(ast.parse(_source(name)))
    assert len(calls) == 1, f"{name}: expected exactly one guard call"
    assert calls[0].args[0].id == "__file__"


@pytest.mark.parametrize("name", GUARDED_SHELL)
def test_the_shell_entry_points_source_the_bash_guard(name):
    text = _source(name)
    assert "lib/require-main-clone.sh" in text, f"{name}: guard not sourced"
    assert f"\n{GUARD}\n" in text, f"{name}: guard sourced but never called"


def test_the_windows_restarter_carries_the_same_predicate():
    """PowerShell, so neither of the other two guards is reachable.

    Checked by reading rather than by running: there is no PowerShell on this
    machine. That is a real limit and is stated rather than hidden.
    """
    text = _source("restart-bridge-daemon.ps1")
    assert "PathType Leaf" in text and "exit 2" in text, (
        "restart-bridge-daemon.ps1 no longer refuses from a worktree. The "
        "predicate is that <root>/.git is a FILE in a worktree and a DIRECTORY "
        "in the main clone.")


def test_the_retired_windows_launcher_still_starts_nothing():
    """`launch-all-daemons.bat` is on the operator's list and carries no guard.

    It cannot: a `.bat` cannot source the bash helper. It does not need one,
    because it was retired in 2026-05 and its whole body is `exit /b 0`. That
    is asserted here rather than assumed, so the day someone puts a command
    back into it, this fails and asks for a guard first.
    """
    text = _source("launch-all-daemons.bat")
    body = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().upper().startswith(("REM", "@ECHO"))]
    assert body == ["exit /b 0"], (
        f"launch-all-daemons.bat now does something: {body}. It is a daemon "
        f"launcher, so it needs a clone guard before it does it.")


# ============================================================
# 2. The discovery net
# ============================================================
#
# A hand-kept list of daemons is the same defect as a hand-kept list of verbs.
# This asks the AST for the STRUCTURAL marks of a daemon and requires each hit
# to be guarded or explicitly excused.

SCHEDULERS = {"AsyncIOScheduler", "BackgroundScheduler", "BlockingScheduler"}
SERVICE_TOOLS = {"systemctl", "launchctl", "schtasks", "loginctl"}

# Structurally daemon-shaped, deliberately unguarded, each with its reason.
NOT_AN_ENTRY_POINT = {
    "scripts/bridge_daemon/scheduler.py":
        "a library inside the bridge daemon's own package. It has no main() "
        "and no __main__; the process that imports it is bridge-daemon.py, "
        "which is guarded.",
    "scripts/updaters/cliproxyapi_update.py":
        "a library reached only through update-manager.py, which is guarded. "
        "It restarts a unit, so if it ever grows its own entry point it needs "
        "the guard on that entry point.",
    "scripts/utils/supervise.py":
        "a library. Its only caller today is scripts/utils/git_push.py, "
        "reached from safe-push.py, which is guarded at its own main().",
    "scripts/marp_render.py":
        "spawns `marp --watch --server`, a local slide-preview server. It is "
        "not a HEADING OS daemon: the operator's category is a unit or a PID "
        "file the fleet reads, and this has neither -- no state outside the "
        "deck being previewed. A guard here would break `/marp` in a YARD, "
        "which is ordinary engine work and exactly what a YARD is for. NAMED "
        "to the operator on 2026-09-03 rather than gated silently; he ruled "
        "on 2026-09-04 that it stays unguarded, for that reason.",
}


def _daemon_signals(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.endswith(".pid"):
            found.add("pidfile")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else "")
        if name in SCHEDULERS:
            found.add("scheduler")
        if name in ("Popen", "run", "call", "check_call"):
            if any(kw.arg in ("start_new_session", "creationflags")
                   for kw in node.keywords):
                found.add("detached-spawn")
            for arg in node.args:
                if isinstance(arg, ast.List):
                    for element in arg.elts:
                        if isinstance(element, ast.Constant) \
                                and isinstance(element.value, str) \
                                and element.value.rsplit("/", 1)[-1] in SERVICE_TOOLS:
                            found.add("service-tool")
        # Both spellings. `uvicorn.run(...)` is the short one; the bridge daemon
        # builds a `uvicorn.Server` and calls `server.run(sockets=[...])`, whose
        # receiver is a local name, so only the CONSTRUCTION is visible here.
        if name == "Server" or (
                isinstance(func, ast.Attribute) and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "uvicorn"):
            found.add("uvicorn")
    return found


def _discovered() -> dict[str, set[str]]:
    # `tracked_paths`, not `glob`: a bare walk of the repo root also counts the
    # agent worktrees under `.claude/worktrees/`, doubling the corpus and
    # reporting another branch's files as this one's.
    hits = {}
    for path in sorted(tracked_paths(("scripts/**/*.py",), ROOT)):
        signals = _daemon_signals(path)
        if signals:
            hits[str(path.relative_to(ROOT))] = signals
    return hits


def test_the_net_catches_what_it_was_calibrated_on():
    """A floor, and a calibration anchor.

    MEASURED 2026-09-03: 13 files carry a daemon signal. A net that matches
    nothing passes every assertion below while checking nothing, and that is
    exactly how the verb list passed review.
    """
    hits = _discovered()
    assert len(hits) >= 13, f"the net now finds only {len(hits)}: {sorted(hits)}"
    for expected in ("scripts/sync-exchange-daemon.py", "scripts/sentinel.py",
                     "scripts/sync-exchange-pulse.py", "scripts/fireside-pulse.py",
                     "scripts/bridge-daemon.py"):
        assert expected in hits, f"{expected} stopped looking like a daemon"


def test_every_discovered_daemon_is_guarded_or_excused():
    """The whole point. A daemon entry point added tomorrow fails here.

    It fails with the reason and with the two ways forward, so the next author
    meets the decision rather than a mystery.
    """
    unexplained = {}
    for relative, signals in _discovered().items():
        if relative in NOT_AN_ENTRY_POINT:
            continue
        if GUARD in (ROOT / relative).read_text(encoding="utf-8"):
            continue
        unexplained[relative] = sorted(signals)

    assert not unexplained, (
        f"these files are shaped like a daemon entry point and carry no clone "
        f"guard: {unexplained}\n"
        f"Daemons run in HELM only (CLAUDE.md, HELM and YARD). Either add "
        f"`require_main_clone(__file__)` as the first statement of main(), or "
        f"add the path to NOT_AN_ENTRY_POINT in this file with the reason it "
        f"is not one.")


def test_the_excuse_list_holds_no_entry_that_guards_nothing():
    """An excuse for a file that no longer matches is an excuse nobody reads.

    It also catches the opposite drift: a file that got the guard later, whose
    excuse now contradicts the code.
    """
    # The floor, outside the loop. An emptied excuse list would satisfy every
    # assertion below while checking nothing. Four entries on 2026-09-03.
    assert len(NOT_AN_ENTRY_POINT) == 4, sorted(NOT_AN_ENTRY_POINT)
    hits = _discovered()
    for relative, reason in NOT_AN_ENTRY_POINT.items():
        path = ROOT / relative
        assert path.is_file(), f"{relative} is excused but does not exist"
        assert relative in hits, (
            f"{relative} is excused from a net that no longer catches it")
        assert GUARD not in path.read_text(encoding="utf-8"), (
            f"{relative} now carries the guard, so its excuse is stale: {reason}")
        assert len(reason) > 60, f"{relative}: the reason is too thin to act on"


# ============================================================
# 3. Driven, in a real worktree
# ============================================================
#
# The refusing direction is driven through the real entry point. The PERMITTED
# direction is asked of the AST above and is deliberately NOT driven.
#
# That is not caution in the abstract. On 2026-09-03, while writing this file,
# `bash scripts/restart-bridge-daemon.sh --help` was run from a main clone to
# prove the guard let it through. `--help` never reached the delegate: the
# script restarted the bridge daemon, which the operator had deliberately
# stopped and disabled. Proving a guard PERMITS something means performing the
# thing it permits.
#
# `--help` is used below rather than a bare invocation so that if the guard is
# ever removed, this test prints usage and fails on the exit code -- instead of
# starting the operator's daemons.

DRIVEN = ("sync-exchange-daemon.py", "sync-exchange-pulse.py",
          "fireside-pulse.py", "daemon-fleet-health.py", "bridge-daemon.py",
          "sentinel.py", "ollama-guard.py")


@pytest.mark.parametrize("name", DRIVEN)
def test_running_it_from_a_worktree_exits_two_and_names_helm(
        armed_worktree, name):
    result = subprocess.run(
        [sys.executable, str(armed_worktree / "scripts" / name), "--help"],
        cwd=str(armed_worktree), capture_output=True, text=True, timeout=180)

    assert result.returncode == 2, (
        f"{name} exited {result.returncode} from a worktree, not 2\n"
        f"{result.stdout[-800:]}{result.stderr[-800:]}")
    assert "HELM" in result.stderr, result.stderr[-800:]
    assert result.stdout.strip() == "", (
        f"{name} printed on stdout before refusing, so it got going: "
        f"{result.stdout[-400:]}")


def test_the_driven_set_is_not_empty_and_is_a_subset_of_the_registry():
    """A floor on the parametrisation, and a check that it did not drift into
    naming a script the registry does not guard."""
    assert len(DRIVEN) == 7
    for name in DRIVEN:
        assert name in DAEMON_ENTRY_POINTS, f"{name} is driven but not registered"


def test_nothing_here_drives_a_script_that_changes_a_live_unit():
    """A live daemon as a side effect of a test run is a defect, not a cost.

    The same class as a mutation that writes the real tree: the run leaves the
    machine in a state nobody asked for, and on 2026-09-03 that is exactly what
    happened -- `restart-bridge-daemon.sh --help` was run from a main clone to
    prove the guard let it through, `--help` never reached the delegate, and a
    daemon the operator had deliberately stopped came up. Operator ruling,
    2026-09-04: a test looks at the REFUSAL and at the ARGUMENTS, never at a
    process that really started.

    So the lifecycle scripts are asserted about by READING only, and this pins
    it: nothing whose name says it installs, restarts or launches may enter the
    driven set, whose members are all invoked from a worktree where they refuse.
    """
    lifecycle = ("restart", "install", "uninstall", "launch", "setup")
    for name in DRIVEN:
        assert not any(word in name for word in lifecycle), (
            f"{name} manages a unit and is in the driven set. If its guard is "
            f"ever removed, this file starts or stops a daemon on the "
            f"operator's machine as a side effect of running the tests.")

    # And no test here may ask for a main clone to run something in. Asked of
    # the AST -- a parameter list -- because a substring scan of this file
    # matches the sentence explaining the rule, which is how the first version
    # of this assertion failed against itself.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    borrowers = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and any(a.arg == "armed_main_clone" for a in n.args.args)]
    assert not borrowers, (
        f"{borrowers} take `armed_main_clone`. Proving a guard PERMITS "
        f"something means performing the thing it permits; the permitted "
        f"direction is asked of the AST above, never driven.")
