"""A `--basetemp` inside the checkout turned every test's scratch into the repo.

MEASURED 2026-09-08 in the operator's live clone. An unattended agent ran, from
the engine root:

    pytest tests/ -q -x -n auto --basetemp=.tmp/night-repair/pytest-bt

The path is RELATIVE, so every `tmp_path` in that run resolved INSIDE the engine
work tree. A test that scaffolds a data overlay reached `git_init_commit` in
`scripts/create-data-repo.py`, whose idempotency probe asks `git rev-parse
--is-inside-work-tree` -- "is this path inside ANY repository" -- and reads the
answer as "is this the repository I already initialised". For a target under the
engine it answers yes, `git init` is skipped, and `git config`, `git add -A` and
`git commit` then ran against THE ENGINE. The result was commit `ed7cee1` on
`main`: six files, two unrelated sessions' work, under a message describing
neither.

WHAT LIMITED THE DAMAGE WAS LUCK, and it is the reason this defect is rated where
it is. The scaffolded fake overlay landed under `.tmp/`, which is gitignored, so
it contributed nothing to the commit. A basetemp anywhere else in the tree would
have committed a fabricated data overlay -- a `.gitignore`, a README,
`crm/contacts/`, `context/` -- into the PUBLIC engine repository. Being under a
gitignored path is not protection; it decides only whether the fabricated files
are also committed.

Four tests can reach `git_init_commit` with a target that has no `.git` of its
own, and all four were safe only while basetemp lay outside the clone. The repair
is one refusal at session start rather than four repairs: it covers every future
site as well, and it turns a mistake that is merely NOTICEABLE into one that is
IMPOSSIBLE, because the run never reaches collection.

Before the fix, `pytest tests/test_data_root.py --collect-only
--basetemp=.tmp/<x>` exited 0 and reported 21 tests collected, with every
`tmp_path` rooted in the work tree.

Guards: `declared_basetemp` and `_refuse_a_basetemp_inside_the_checkout` in
`tests/conftest.py`.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parent.parent

# A module that collects fast and is not itself about temp directories, so the
# child run's verdict is about the guard and nothing else.
PROBE_TARGET = "tests/test_data_root.py"

# Measured 2026-09-08: `pytest tests/test_data_root.py --collect-only -q` reports
# this many. A floor, not an equality, so the child having to collect one test
# more does not fail this file; zero would mean the child collected nothing and
# the "still runs" cases would pass vacuously.
COLLECTED_FLOOR = 10


def _run_pytest(*args: str) -> subprocess.CompletedProcess:
    """A child pytest, from the engine root, with this suite's conftest live."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", PROBE_TARGET, "--collect-only", "-q",
         "-p", "no:cacheprovider", *args],
        cwd=str(ENGINE_ROOT), capture_output=True, text=True, timeout=300,
    )


def _collected(result: subprocess.CompletedProcess) -> int:
    """How many tests the child reported, or 0 when it reported none.

    Parsed from the summary line rather than by position: `-q --collect-only`
    prints "N tests collected in Xs", and a run that collected nothing prints no
    such line at all. Returning 0 there is what makes the floor below a real
    assertion instead of a crash that happens to look like one.
    """
    found = re.search(r"(\d+) tests? collected", result.stdout)
    return int(found.group(1)) if found else 0


@pytest.fixture
def scratch_inside_the_clone(request):
    """A path inside the work tree that this test owns, removed afterwards.

    Under `.tmp/`, which is gitignored, for the same reason the incident's own
    basetemp was: it is the one place a stray directory in this checkout cannot
    become a commit. That is a courtesy to the tree, NOT the property under
    test -- the guard must refuse regardless, which is what
    `test_the_message_says_gitignored_is_not_protection` pins.
    """
    import shutil
    import uuid

    path = ENGINE_ROOT / ".tmp" / f"basetemp-guard-{uuid.uuid4().hex[:12]}"
    request.addfinalizer(lambda: shutil.rmtree(path, ignore_errors=True))
    return path


# ---------------------------------------------------------------------------
# The refusal, driven through the real entry point
# ---------------------------------------------------------------------------


def test_a_relative_basetemp_inside_the_checkout_stops_the_session(
        scratch_inside_the_clone):
    """The incident's own shape: a relative path, resolved against the root."""
    relative = scratch_inside_the_clone.relative_to(ENGINE_ROOT)
    result = _run_pytest(f"--basetemp={relative}")

    assert result.returncode == 4, (
        f"expected pytest's USAGE_ERROR (4), got {result.returncode}. "
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}")
    assert "REFUSING to run" in result.stderr, result.stderr[-2000:]
    assert str(scratch_inside_the_clone) in result.stderr, result.stderr[-2000:]
    # Nothing collected: the refusal is at session start, not per test.
    assert "tests collected" not in result.stdout, result.stdout[-2000:]


def test_an_absolute_basetemp_inside_the_checkout_stops_the_session(
        scratch_inside_the_clone):
    """Resolving, not string-matching the flag: the absolute form refuses too."""
    result = _run_pytest(f"--basetemp={scratch_inside_the_clone}")
    assert result.returncode == 4, result.stdout[-2000:] + result.stderr[-2000:]
    assert "REFUSING to run" in result.stderr


def test_the_refusal_does_not_create_the_directory_it_refuses(
        scratch_inside_the_clone):
    """The guard reads the option; it must never ask the factory.

    `TempPathFactory.getbasetemp()` is not a reader. On an explicit
    `--basetemp` it does `rm_rf(path)` and then `mkdir(mode=0o700)`, so a guard
    that reaches for it DESTROYS and recreates the very directory it is about
    to refuse about. MEASURED 2026-09-08: the first draft of the guard called
    it and left an empty `.tmp/guard-probe/` in the work tree on every refusal.

    With a path that already holds a file, the damage is visible rather than
    cosmetic, so that is what is asserted.
    """
    scratch_inside_the_clone.mkdir(parents=True)
    canary = scratch_inside_the_clone / "not-scratch.txt"
    canary.write_text("a file the operator put here\n", encoding="utf-8")

    result = _run_pytest(f"--basetemp={scratch_inside_the_clone}")

    assert result.returncode == 4, result.stderr[-2000:]
    assert canary.exists(), (
        "the refusal deleted the directory it refused about; the guard is "
        "calling getbasetemp() instead of reading config.option.basetemp")
    assert canary.read_text(encoding="utf-8") == "a file the operator put here\n"


def test_the_message_names_the_path_and_what_it_would_have_allowed():
    """A refusal nobody can act on sends the reader back to the code."""
    from tests.conftest import _refuse_a_basetemp_inside_the_checkout

    class _Config:
        option = type("O", (), {"basetemp": str(ENGINE_ROOT / ".tmp" / "bt")})()

    with pytest.raises(pytest.UsageError) as caught:
        _refuse_a_basetemp_inside_the_checkout(_Config())
    message = str(caught.value)

    assert str(ENGINE_ROOT / ".tmp" / "bt") in message
    assert str(ENGINE_ROOT) in message
    for phrase in ("git add -A", "git commit", "--basetemp"):
        assert phrase in message, phrase


def test_the_message_says_gitignored_is_not_protection():
    """`.tmp/` being ignored is what limited the incident, not what stopped it.

    Split from the test above deliberately. The path and the consequence are
    the operator's instruction; this sentence is the one that stops the next
    reader concluding "it was under .tmp/, so it was fine" and re-arming the
    same run.
    """
    from tests.conftest import _refuse_a_basetemp_inside_the_checkout

    class _Config:
        option = type("O", (), {"basetemp": str(ENGINE_ROOT / ".tmp" / "bt")})()

    with pytest.raises(pytest.UsageError) as caught:
        _refuse_a_basetemp_inside_the_checkout(_Config())
    message = str(caught.value)
    assert "NOT protection" in message
    assert ".tmp" in message


# ---------------------------------------------------------------------------
# The other direction: everything legitimate still runs
# ---------------------------------------------------------------------------


def test_an_explicit_basetemp_outside_the_checkout_still_runs(tmp_path):
    """The operator passes one deliberately (`/tmp/hos-night-bt`)."""
    result = _run_pytest(f"--basetemp={tmp_path / 'bt'}")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "REFUSING" not in result.stderr
    assert _collected(result) >= COLLECTED_FLOOR, result.stdout[-2000:]


def test_the_default_basetemp_still_runs():
    """No flag at all: the system temp directory, which is where it belongs."""
    result = _run_pytest()
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "REFUSING" not in result.stderr
    assert _collected(result) >= COLLECTED_FLOOR, result.stdout[-2000:]


def test_an_xdist_worker_subdirectory_of_an_outside_basetemp_is_allowed():
    """Under `-n auto` a worker's basetemp is a `popen-gwN` one level deeper.

    A guard that only recognised the controller's own path would refuse every
    worker of a legitimate run, which is the same outage as refusing the run.
    """
    import tempfile

    from tests.conftest import _refuse_a_basetemp_inside_the_checkout

    # Built from the system temp directory rather than written as `/tmp/...`:
    # the literal is a portability assumption and ruff's S108 flags it.
    outside = Path(tempfile.gettempdir()) / "hos-night-bt" / "popen-gw3"

    class _Config:
        option = type("O", (), {"basetemp": str(outside)})()

    _refuse_a_basetemp_inside_the_checkout(_Config())      # must not raise


def test_a_worker_subdirectory_inside_the_checkout_is_still_refused():
    """The mirror of the case above, so `popen-gwN` is not an escape hatch."""
    from tests.conftest import _refuse_a_basetemp_inside_the_checkout

    class _Config:
        option = type(
            "O", (), {"basetemp": str(ENGINE_ROOT / ".tmp" / "bt" / "popen-gw3")})()

    with pytest.raises(pytest.UsageError):
        _refuse_a_basetemp_inside_the_checkout(_Config())


def test_a_sibling_directory_sharing_the_prefix_is_not_inside():
    """Containment, never a string prefix.

    `<clone>-2` and `<clone>.bak` both start with the checkout's path and
    neither is in it. A `startswith` guard refuses a neighbouring worktree's
    perfectly good basetemp and reports it as this checkout.
    """
    from tests.conftest import _refuse_a_basetemp_inside_the_checkout

    for sibling in (f"{ENGINE_ROOT}-2/bt", f"{ENGINE_ROOT}.bak/bt"):
        class _Config:
            option = type("O", (), {"basetemp": sibling})()

        _refuse_a_basetemp_inside_the_checkout(_Config())  # must not raise


# ---------------------------------------------------------------------------
# The reader the guard is built on
# ---------------------------------------------------------------------------


def test_the_declared_basetemp_is_read_from_the_option_not_the_factory():
    """A config whose factory would explode still yields an answer.

    The strongest available statement that the option is the source: if
    `declared_basetemp` reached for `_tmp_path_factory` at all, this raises.
    """
    import tempfile

    from tests.conftest import declared_basetemp

    outside = Path(tempfile.gettempdir()) / "somewhere" / "bt"

    class _Exploding:
        def getbasetemp(self):
            raise AssertionError("declared_basetemp asked the factory")

    class _Config:
        option = type("O", (), {"basetemp": str(outside)})()
        _tmp_path_factory = _Exploding()

    assert declared_basetemp(_Config()) == outside


def test_with_no_basetemp_the_system_temp_directory_is_the_answer(monkeypatch):
    """No flag: pytest builds one under the shared temp directory.

    Returned rather than special-cased to None, so a `TMPDIR` pointed inside a
    checkout is caught by the same comparison as an explicit flag would be.
    """
    import tempfile

    from tests.conftest import declared_basetemp

    class _Config:
        option = type("O", (), {"basetemp": None})()

    assert declared_basetemp(_Config()) == Path(tempfile.gettempdir()).resolve()

    monkeypatch.setattr(tempfile, "tempdir", str(ENGINE_ROOT / ".tmp"))
    assert declared_basetemp(_Config()) == (ENGINE_ROOT / ".tmp").resolve()


def test_a_tmpdir_inside_the_checkout_is_refused_with_no_flag_at_all(monkeypatch):
    """The case with no `--basetemp` to read: the environment supplied it."""
    import tempfile

    from tests.conftest import _refuse_a_basetemp_inside_the_checkout

    monkeypatch.setattr(tempfile, "tempdir", str(ENGINE_ROOT / ".tmp"))

    class _Config:
        option = type("O", (), {"basetemp": None})()

    with pytest.raises(pytest.UsageError):
        _refuse_a_basetemp_inside_the_checkout(_Config())


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


def test_the_refusal_is_the_first_statement_of_session_start():
    """Asked of the AST, so a re-order is caught and a comment is not evidence.

    Ordering is the whole of it. `pytest_sessionstart` also arms the socket
    guard, snapshots the temp tree and arms the overlay guard; any of those
    running first against a poisoned tree is work done in the work tree before
    anything refused.
    """
    import ast

    source = (ENGINE_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    hook = next(
        (node for node in tree.body
         if isinstance(node, ast.FunctionDef)
         and node.name == "pytest_sessionstart"),
        None)
    assert hook is not None, "pytest_sessionstart is gone from tests/conftest.py"

    statements = [s for s in hook.body if not isinstance(s, ast.Global)]
    first = statements[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), (
        f"the first statement of pytest_sessionstart is {ast.dump(first)[:120]}")
    assert getattr(first.value.func, "id", None) == \
        "_refuse_a_basetemp_inside_the_checkout", ast.dump(first.value)[:200]
