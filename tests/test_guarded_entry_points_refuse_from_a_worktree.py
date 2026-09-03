"""Publishing and maintenance entry points refuse to run from a YARD.

Every script guarded here reaches something LIVE: the public engine remote, the
private data overlay, the executive fleet, or the operator's machine state. A
YARD is a throwaway checkout of the engine on its own branch, so a publish from
one sends an unreviewed branch to a public repository, and a maintenance pass
from one writes to the real overlay from a tree that will be deleted.

The firing direction is driven through the REAL entry point in a REAL worktree
and asserts the observable consequence: exit status 2, a message naming HELM,
and no stdout at all, so the script cannot have got going. The quiet direction
is deliberately NOT driven the same way -- running `push-all.py` from HELM to
prove it is permitted would push. It is proved once, generically, by
`tests/test_clone_guard.py::test_from_helm_the_guarded_body_runs`, and pinned
per script here by asking the AST that the call is present and where it sits.

The AST is asked rather than the text (a substring scan goes red the moment a
comment quotes the pattern to explain it, which teaches people to stop
explaining) and it is the reason the two exceptions below can be stated as
facts rather than as hopes.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Guarded as the first statement of main(). Reaching any of these from a YARD
# publishes, provisions, or writes to the live overlay.
GUARDED_FIRST = (
    "push-all.py",
    "safe-push.py",
    "publish-corporate.py",
    "publish-service.py",
    "create-data-repo.py",
    "build_data_repo.py",
    "build_engine_repo.py",
    "migrate-data.py",
    "aggregate-crm.py",
    "archive-transcripts.py",
    "dream-shadow.py",
    "offboard-exec.py",
)

# Guarded, but NOT as the first statement, each for a stated reason.
GUARDED_LATER = {
    "provision-exec.py":
        "argparse must handle -h first; an early exit restored a fixed defect "
        "where reading the interface printed a banner and no usage",
    "memory-index.py":
        "only the build subcommand writes under the data root; query and stats "
        "stay open from a YARD",
}

# Deliberately unguarded, and the absence is asserted so it cannot drift back.
UNGUARDED_BY_DECISION = {
    "emergency-revoke.py":
        "revokes nothing (disabled fail-closed), writes nothing, and its whole "
        "remaining value is printing an incident checklist that must work from "
        "anywhere. A guard would only make a 3am runbook fail in a worktree.",
}


def _script(name: str) -> Path:
    return ROOT / "scripts" / name


def _main_node(name: str) -> ast.FunctionDef:
    tree = ast.parse(_script(name).read_text(encoding="utf-8"))
    mains = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1, f"{name}: expected exactly one module-level main()"
    return mains[0]


def _guard_calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "require_main_clone"]


def _body_without_docstring(fn: ast.FunctionDef) -> list[ast.stmt]:
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return body


# ============================================================
# The corpus itself, floored outside every loop
# ============================================================

def test_the_guarded_corpus_is_the_size_it_was_measured_at():
    """MEASURED 2026-09-03: 12 first-statement, 2 later, 1 by-decision absent.

    Asserted outside the loops below so a corpus that shrank to nothing cannot
    satisfy them vacuously.
    """
    assert len(GUARDED_FIRST) == 12
    assert len(GUARDED_LATER) == 2
    assert len(UNGUARDED_BY_DECISION) == 1
    for name in (*GUARDED_FIRST, *GUARDED_LATER, *UNGUARDED_BY_DECISION):
        assert _script(name).is_file(), f"{name} is not in scripts/ any more"


# ============================================================
# Static: the call is present, and where it was put on purpose
# ============================================================

@pytest.mark.parametrize("name", GUARDED_FIRST)
def test_the_guard_is_the_first_statement_of_main(name):
    body = _body_without_docstring(_main_node(name))
    assert body, f"{name}: main() has no body"
    first = body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), (
        f"{name}: first statement of main() is {type(first).__name__}, "
        f"not the guard call")
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "require_main_clone"
    assert len(first.value.args) == 1
    assert isinstance(first.value.args[0], ast.Name)
    assert first.value.args[0].id == "__file__", (
        f"{name}: the guard must be passed the SCRIPT's own path. Passing "
        f"anything else reintroduces the hole where a YARD script launched by "
        f"absolute path from HELM answers 'main clone'.")


@pytest.mark.parametrize("name", sorted(GUARDED_LATER))
def test_the_late_guards_are_present_and_deliberate(name):
    calls = _guard_calls(_main_node(name))
    assert len(calls) == 1, f"{name}: expected one guard call in main()"
    assert isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "__file__"


def test_memory_index_guards_only_the_build_path():
    """query and stats must keep working from a YARD; build must not.

    The condition is asked of the resolved handler (`args.func is cmd_build`)
    rather than of a subcommand STRING, so renaming the subcommand cannot
    silently unguard the write path.
    """
    fn = _main_node("memory-index.py")
    guarded_ifs = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.If) and _guard_calls(n)
    ]
    assert len(guarded_ifs) == 1, (
        "memory-index.py: the guard must sit inside exactly one conditional, "
        "so the read subcommands stay open")
    test = guarded_ifs[0].test
    assert isinstance(test, ast.Compare)
    assert isinstance(test.ops[0], ast.Is)
    assert isinstance(test.comparators[0], ast.Name)
    assert test.comparators[0].id == "cmd_build"


@pytest.mark.parametrize("name", sorted(UNGUARDED_BY_DECISION))
def test_the_deliberate_exception_stays_unguarded(name):
    """If someone adds the guard here later, this fails and points at the why.

    The reason lives in `UNGUARDED_BY_DECISION` and in a comment at the top of
    the function, so the next author meets the argument rather than a mystery.
    """
    tree = ast.parse(_script(name).read_text(encoding="utf-8"))
    assert not _guard_calls(tree), (
        f"{name} is unguarded on purpose: {UNGUARDED_BY_DECISION[name]}")


# ============================================================
# Dynamic: the real entry point, in a real worktree
# ============================================================
#
# `armed_worktree` (tests/conftest.py) is a real worktree carrying this
# checkout's uncommitted state, so these cases exercise the guard as edited
# rather than the guard as committed.

@pytest.mark.parametrize("name", GUARDED_FIRST)
def test_running_it_from_a_worktree_exits_two_and_says_nothing_else(
    armed_worktree, name,
):
    """The real command, in a real worktree, doing nothing.

    Three separate observations, because exit status alone is satisfied by a
    crash: the status is 2 (not 1, which is what a traceback gives), stderr
    names HELM, and stdout is EMPTY, so the script printed no banner and cannot
    have reached its work.
    """
    result = subprocess.run(
        [sys.executable, str(armed_worktree / "scripts" / name)],
        cwd=str(armed_worktree), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 2, (
        f"{name}: exit {result.returncode}\nstdout={result.stdout}\n"
        f"stderr={result.stderr}")
    assert "HELM" in result.stderr
    assert result.stdout.strip() == "", f"{name} produced output before refusing"


def test_a_read_subcommand_of_memory_index_is_not_refused(armed_worktree):
    """The pair to the build guard: `stats` must still answer from a YARD.

    Asserted as "not refused by the clone guard" rather than "exits 0", because
    a worktree has no index to report on and the command may fail for its own
    reasons. What must not appear is this guard's refusal.
    """
    result = subprocess.run(
        [sys.executable, str(armed_worktree / "scripts" / "memory-index.py"),
         "stats"],
        cwd=str(armed_worktree), capture_output=True, text=True, timeout=180,
    )
    assert "runs from HELM" not in result.stderr, result.stderr


def test_the_build_subcommand_of_memory_index_is_refused(armed_worktree):
    result = subprocess.run(
        [sys.executable, str(armed_worktree / "scripts" / "memory-index.py"),
         "build"],
        cwd=str(armed_worktree), capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 2
    assert "HELM" in result.stderr


def test_provision_exec_still_prints_its_usage_from_a_worktree(armed_worktree):
    """The defect the late placement exists to avoid, asserted directly.

    A guard at the top of main() would exit 2 here with no usage, which is the
    behaviour a comment in that file records as already fixed once.
    """
    result = subprocess.run(
        [sys.executable, str(armed_worktree / "scripts" / "provision-exec.py"),
         "--help"],
        cwd=str(armed_worktree), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_emergency_revoke_still_reaches_its_checklist_from_a_worktree(
    armed_worktree,
):
    """The unguarded exception, proved by behaviour and not only by absence."""
    result = subprocess.run(
        [sys.executable, str(armed_worktree / "scripts" / "emergency-revoke.py"),
         "--help"],
        cwd=str(armed_worktree), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
