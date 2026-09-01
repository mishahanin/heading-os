"""A Healthchecks deadman may only ever be pinged by the daemon it watches.

The check `steward-email-triage` reports whether the inbox-pulse poll loop on
the Steward host is alive. On 2026-08-17 that loop wedged and stayed wedged for
33 hours, and the check did NOT stay red: it flapped, going green for 15 minutes
at a time. The green came from this repository. `_main_loop` calls
`hc_ping("STEWARD_HC_EMAIL_TRIAGE")` at the end of a clean cycle, eleven tests in
tests/inbox_pulse/test_daemon.py drive that loop to completion, and any earlier
test that called the real `load_env()` leaves the production ping URL in
os.environ for the rest of the session. Measured 2026-08-18: one run of that one
file sent 14 real success pings to the live check.

A monitor that a test run can turn green is worse than no monitor, because it is
believed. These two tests hold the containment in tests/conftest.py.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import healthchecks  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from tests.repo_files import tracked_python_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_PING_HOST = "hc-ping.com"


def _live_ping_urls() -> list[str]:
    return sorted(k for k, v in os.environ.items() if _PING_HOST in v)


def _pingable_env_names() -> list[str]:
    """Every env name this repository can actually ping, read from the tree.

    Derived rather than listed. The second test below used to name three by
    hand while the code carried seven, and the four it left out are the four
    fireside checks - `FIRESIDE_HC_SPEAKER_DMS`, `FIRESIDE_HC_SUNDAY_PREVIEW`,
    `FIRESIDE_HC_DAYOF_REMINDERS`, `FIRESIDE_HC_HELMSMAN_BRIEF` - each of which
    a test in this suite drives to completion exactly the way the one that
    caused the incident did. A hand list of the checks a monitor-falsification
    guard covers is a list that falls behind the day a daemon adds a job.

    Matched on the CALL, through the AST: a `Name` call to `hc_ping` (the alias
    every daemon imports it under) or an `Attribute` call to
    `healthchecks.ping`, with one string literal argument.
    """
    names: set[str] = set()
    # Through the shared git-aware walker, not a hand `rglob`. A worktree under
    # `.claude/worktrees/` is a second checkout of this same repository, so a
    # hand walk of `.claude` reads every ping call site twice and can pick up a
    # call site from another BRANCH. `tests/test_a_walker_that_never_asked_git.py`
    # is the rule; this is one more sweep obeying it.
    for py in tracked_python_files(("scripts", ".claude")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) != 1:
                continue
            func = node.func
            is_ping = (
                (isinstance(func, ast.Name) and func.id == "hc_ping")
                or (isinstance(func, ast.Attribute) and func.attr == "ping"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "healthchecks")
            )
            arg = node.args[0]
            if is_ping and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
    return sorted(names)


def test_load_env_cannot_reintroduce_a_production_ping_url():
    """The real load_env() must not hand a live deadman URL to the suite.

    This is the exact path that produced the false greens: load_env() uses
    os.environ.setdefault, so blanking the names at conftest import (rather than
    deleting them) is what makes the containment survive a later load_env().
    On a clone with no .env there is nothing to reintroduce and this passes
    trivially -- it is the operator workspace, where .env is real, that it
    guards.
    """
    load_env()
    leaked = _live_ping_urls()
    assert not leaked, (
        f"production Healthchecks ping URL(s) live in the test environment: {leaked}. "
        "A test that reaches hc_ping would mark a wedged daemon healthy."
    )


def test_ping_is_a_no_op_for_every_check_this_repository_can_ping(monkeypatch):
    """With the URL blanked, ping() takes its documented missing-var path.

    Two things this used to leave out, and each on its own let a real breach
    through.

    It named three checks by hand while the tree pings seven, so the four
    fireside deadmen were unasserted. The list is derived now.

    And it asked only for the RETURN VALUE. `ping()` answers False on a missing
    variable, on a timeout, on a refused connection and on any unexpected
    exception, so `is False` was equally true of a check that was contained and
    of one that was LIVE while the network happened to be down - and on a
    working network the test would have discovered the breach by committing it,
    sending the very false green it exists to prevent. What is asserted here is
    the side effect: the transport is replaced by a recorder, and it must not be
    reached at all.
    """
    load_env()

    reached: list[str] = []

    class _Transport:
        # The real exception namespace, so `ping()`'s own except clauses still
        # resolve. A stub without it turns the assertion below into an
        # AttributeError raised while handling it, which is a red run for the
        # wrong reason.
        exceptions = healthchecks.requests.exceptions

        @staticmethod
        def get(url, **kwargs):
            reached.append(url)
            raise AssertionError(
                f"ping() opened a connection to {url!r} from inside the test "
                f"suite; a run can now mark a wedged daemon healthy")

    # Bound onto the healthchecks module's own name, never onto `sys.modules`,
    # so no later test in this worker inherits a rebound `requests`.
    monkeypatch.setattr(healthchecks, "requests", _Transport)

    names = _pingable_env_names()
    # Floor: an AST walk that finds nothing would pass this over an empty set,
    # which is the same silent all-clear the containment exists to prevent.
    # Measured 7 on 2026-09-01 (two steward, five fireside).
    assert len(names) >= 6, (
        f"only {names} ping call sites found under scripts/ and .claude/; the "
        f"AST walk has stopped reaching them")

    for env_key in names:
        assert healthchecks.ping(env_key) is False, (
            f"{env_key} still resolves to a pingable URL inside the test suite"
        )
    assert reached == [], f"ping() reached the network for {reached}"
