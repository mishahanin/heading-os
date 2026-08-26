"""A `finally` clause must not replace the exception it is cleaning up after.

Found by the 2026-08-23 engine audit and reproduced against the pinned
APScheduler (3.11.3, `pyproject.toml`).

`start_daemon` binds `sched = build_scheduler(...)` well before `sched.start()`.
Three things run in between -- `sched.add_job(_run_llm_fit_report, ...)`,
`_register_spine_jobs(...)` and `write_heartbeat(...)` -- and any of them can
raise. Control then reaches:

    finally:
        if sched is not None:
            sched.shutdown(wait=False)

and on a scheduler that never started, `shutdown()` raises
`SchedulerNotRunningError`. Measured:

    >>> BackgroundScheduler().shutdown(wait=False)
    apscheduler.schedulers.SchedulerNotRunningError: Scheduler is not running

That new exception propagates from the `finally` and REPLACES the original. The
operator, or systemd, is told the scheduler was not running -- true, and
useless. The heartbeat failure, the malformed job, the unwritable state
directory: gone.

The watchdog observer had the same shape one line down. `observer.stop()`
raising skipped `observer.join()` AND replaced the original exception, so a
cleanup failure could both leak a thread and hide why the daemon died.

The rule this encodes: cleanup reports its own failures and re-raises nothing.
A daemon whose failure telemetry is wrong precisely when startup fails has no
usable telemetry at all.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DAEMON = ROOT / "scripts" / "bridge-daemon.py"


def test_the_pinned_scheduler_really_raises():
    """Anchor the premise against the version actually pinned. If APScheduler
    ever makes this a no-op, the guard below is harmless but its reason moved."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.schedulers import SchedulerNotRunningError
    with pytest.raises(SchedulerNotRunningError):
        BackgroundScheduler().shutdown(wait=False)


def _finally_body() -> str:
    """The source of `start_daemon`'s finally clause, via the AST."""
    tree = ast.parse(DAEMON.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "start_daemon":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Try) and sub.finalbody:
                    return "\n".join(ast.unparse(s) for s in sub.finalbody)
    raise AssertionError("start_daemon has no try/finally any more")


def test_the_finally_clause_still_exists():
    body = _finally_body()
    assert "shutdown" in body, "the scheduler is no longer shut down on exit"


def test_every_cleanup_step_is_guarded():
    """Each statement that can raise sits inside its own handler, so one
    failing step neither hides the original error nor skips the next step."""
    body = _finally_body()
    tree = ast.parse(body)
    unguarded = []
    guarded = 0
    for node in tree.body:
        if isinstance(node, ast.Try):
            guarded += 1
            continue
        # An `if x is not None:` wrapper counts only when its own body is a Try.
        if isinstance(node, ast.If) and all(isinstance(s, ast.Try) for s in node.body):
            guarded += 1
            continue
        unguarded.append(ast.unparse(node).splitlines()[0])
    # An empty offender list proves nothing on its own: both `continue` arms
    # above drop an item, and the passing state is "everything was dropped".
    # So count what the guards positively recognised. Measured 4 on 2026-08-26
    # (3 `if x is not None:` wrappers plus 1 bare try), floored at 2 so
    # retiring one cleanup step does not fail this test. If the `ast.If` arm's
    # predicate stops matching (or `_finally_body` starts returning a shrunken
    # clause), this count collapses and the guard is watching nothing.
    assert guarded >= 2, f"only {guarded} cleanup statements were inspected"
    assert not unguarded, (
        "these cleanup statements can raise out of the finally clause and "
        "replace the exception that caused the shutdown:\n  "
        + "\n  ".join(unguarded)
    )


def test_a_cleanup_failure_is_reported_not_swallowed():
    """Guarded must not mean silent: a shutdown that fails is still a fact."""
    body = _finally_body()
    assert body.count("logging.") >= 1, (
        "the cleanup guards swallow their errors without a word; a leaked "
        "scheduler thread or observer should leave a trace"
    )


def _load_daemon():
    spec = importlib.util.spec_from_file_location("bridge_daemon_cleanup", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_original_exception_survives_a_failing_cleanup():
    """Behavioural proof on the same shape, independent of the source scan.

    Rebuilds the finally clause's contract in miniature: a body that raises the
    REAL error, cleanup that also raises, and the requirement that the caller
    sees the first one.
    """
    D = _load_daemon()

    class Boom(Exception):
        pass

    class FailingScheduler:
        def shutdown(self, wait=False):
            raise RuntimeError("scheduler was never running")

    class FailingObserver:
        def stop(self):
            raise RuntimeError("observer already gone")

        def join(self):
            raise RuntimeError("observer join also fails")

    class FailingListener:
        """The listening socket the daemon binds before handing it to uvicorn.

        Added 2026-08-24 with the bind-first port fix. Every cleanup step gets
        an object that raises, so a new step added without its own guard shows
        up here as a masked failure rather than in production.
        """

        def close(self):
            raise OSError("socket already closed by uvicorn")

    # The AST-unparsed body, so a blank line inside a comment block cannot
    # truncate it the way a text slice did.
    code = _finally_body()
    namespace = {
        "sched": FailingScheduler(),
        "observer": FailingObserver(),
        "listener": FailingListener(),
        "logging": D.logging,
        "port": 31415,
        # A directory with no port file: the cleanup must not care.
        "WORKSPACE_ROOT": Path("/nonexistent-workspace-for-this-probe"),
    }
    try:
        try:
            raise Boom("the real startup failure")
        finally:
            exec(compile(code, "<finally>", "exec"), namespace)  # noqa: S102
    except Boom:
        pass                    # the original survived: correct
    except Exception as e:      # noqa: BLE001
        pytest.fail(f"cleanup replaced the real failure with {type(e).__name__}: {e}")


# --- the port file does not outlive a failed start ---------------------------

def test_the_cleanup_removes_the_port_file():
    """`.daemon-state/port` is written BEFORE uvicorn binds -- the scheduler
    start, the pulse prime and build_app all run in between. A start that fails
    after that write left a file naming a port nothing listens on, and
    `--status` reads a present file as a live daemon while reading absence as
    "not running". Found by the 2026-08-23 audit as a TOCTOU note; this closes
    the half that misreports state, not the two-daemons race."""
    body = _finally_body()
    assert "port_file" in body and "unlink" in body, (
        "the finally clause no longer removes the port file:\n" + body
    )


def test_the_removal_only_touches_this_daemon_own_port():
    """A second daemon may have claimed the file in the meantime."""
    body = _finally_body()
    assert "read_text().strip() == str(port)" in body, (
        "the cleanup unlinks the port file without checking it still holds THIS "
        "process's port, so a restart race would delete a live daemon's file"
    )


def test_port_is_bound_before_the_try_so_the_cleanup_can_read_it():
    """A NameError inside a finally clause is the masking bug all over again."""
    src = DAEMON.read_text(encoding="utf-8")
    start = src.index("def start_daemon(")
    head = src[start:src.index("    try:", start)]
    assert "port = None" in head, head
