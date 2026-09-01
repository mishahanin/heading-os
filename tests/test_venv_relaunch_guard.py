"""The suite's re-exec guard, measured rather than asserted about itself.

About twenty scripts call `ensure_venv()` at module scope and about twenty test
modules load a script by path. Under any interpreter that is not
`.venv/bin/python`, that call `os.execv`s the whole pytest process, which
inherits pytest's capture as file descriptor 1 and 2: every byte of the
relaunched run lands in a temp file nobody reads, so the session prints ZERO
bytes while exiting 0 on a passing set and 1 on a failing one. A run that prints
nothing is indistinguishable from one that never happened.

Until wire 2.2 the guard was three per-module copies of one line, each with a
test asserting the process-global variable it set. That shape could not hold:
deleting the line from one module left that module's own test passing, because
another module had already set the same variable. Worse, it was self-erasing --
a NEW unguarded module re-execs at collection, `ensure_venv` sets the sentinel
before `os.execv`, and in the silent relaunched run all three tests pass.

So the guard moved to tests/conftest.py, which is collected before any test
module, and the tests here replace the three that could not fail. Measured with
the conftest line removed: the child run below printed zero bytes and exited 0.
"""
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils import venv_guard as _venv

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# Fallback only, for a file the parser cannot read. This regex WAS the whole
# check, under a comment claiming it matched the sentinel "however it spells the
# name". It matched two spellings of the ASSIGNMENT and one of the name.
# MEASURED 2026-09-01 with a scratch module carrying
#   from scripts.utils.venv_guard import _SENTINEL
#   os.environ.setdefault(_SENTINEL, "1")
# in `tests/`: the stray scan stayed green, 6 passed. An imported bare name is
# the obvious way anyone would write that line, and it was the one spelling the
# regex could not see - so the guard against cross-satisfying per-module copies
# had a hole shaped exactly like the copies it exists to refuse.
_SETS_THE_SENTINEL = re.compile(
    r"""os\.environ(?:\.setdefault)?[\[(]\s*(?:_venv\._SENTINEL|["']"""
    + _venv._SENTINEL
    + r"""["'])"""
)

# A write into the process environment, whatever it is spelled through.
_ENV_WRITERS = ("environ.setdefault", "environ.update", "os.putenv", "putenv")


def _names_the_sentinel(node) -> bool:
    """Does this expression name the guard's sentinel, by literal or by binding?

    Either the literal string, or any identifier ending in `_SENTINEL` - which
    covers `_venv._SENTINEL`, a bare imported `_SENTINEL`, and an alias of it.
    Deliberately generous: a false positive here is a stray reported for review,
    a false negative is a silent second copy of the guard.
    """
    text = ast.unparse(node)
    return _venv._SENTINEL in text or re.search(r"\b\w*_SENTINEL\b", text) is not None


def sets_the_sentinel(source: str) -> bool:
    """True when this module SETS the sentinel. An AST question, not a grep.

    Three reasons it is not a regex. A comment or a docstring quoting the line
    cannot satisfy it, which the regex could. An assignment written any of the
    ways Python allows it (`os.environ[X] = `, `environ[X] = `,
    `os.environ.setdefault(X, ...)`, `os.environ.update({X: ...})`,
    `os.putenv(X, ...)`) is one question rather than one alternation branch per
    spelling. And the key may be a bare imported name rather than an attribute,
    which is the case the regex missed entirely.

    READING the variable is not setting it, so a Load-context subscript and a
    `.get`/`.pop`/`delenv` call are all ignored - this module reads it several
    times below and must not flag itself.
    """
    # Prefilter, and it is exact rather than approximate. `_names_the_sentinel`
    # returns True only when the unparsed key contains the sentinel literal or
    # an identifier ending in `_SENTINEL`; `ast.unparse` reproduces those
    # characters, so a file holding neither substring cannot make it answer True
    # and needs no parse. Selectivity on the live tree, measured 2026-09-01: 10
    # of 972 test modules survive it. Without it the stray scan cost 15.75s
    # against 0.30s for the regex it replaced, parsing every module under
    # `tests/` to look at ten of them; with it, 0.55s.
    if _venv._SENTINEL not in source and "_SENTINEL" not in source:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A file the parser cannot read is not a file this scan can judge, so
        # fall back to the old substring question rather than answering False.
        return bool(_SETS_THE_SENTINEL.search(source))

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (isinstance(target, ast.Subscript)
                    and ast.unparse(target.value).endswith("environ")
                    and _names_the_sentinel(target.slice)):
                return True
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if any(func.endswith(writer) for writer in _ENV_WRITERS) and any(
                    _names_the_sentinel(arg) for arg in node.args):
                return True
    return False


def test_the_guard_is_set_once_by_the_root_conftest():
    """Where it is, stated as a test, because "somewhere in the suite" was the bug.

    A per-module copy satisfies every other module's guard test, so the only
    property worth pinning is that the ONE file collected before every test
    module carries it.
    """
    assert sets_the_sentinel((TESTS / "conftest.py").read_text(encoding="utf-8"))


SPELLINGS = [
    'import os\nos.environ["%s"] = "1"' % _venv._SENTINEL,
    "import os\nfrom scripts.utils import venv_guard as _venv\n"
    'os.environ[_venv._SENTINEL] = "1"',
    "import os\nfrom scripts.utils.venv_guard import _SENTINEL\n"
    'os.environ.setdefault(_SENTINEL, "1")',
    "import os\nfrom scripts.utils.venv_guard import _SENTINEL as GUARD_SENTINEL\n"
    'os.environ.update({GUARD_SENTINEL: "1"})',
    "from os import environ\nfrom scripts.utils.venv_guard import _SENTINEL\n"
    'environ[_SENTINEL] = "1"',
    "import os\nfrom scripts.utils.venv_guard import _SENTINEL\n"
    'os.putenv(_SENTINEL, "1")',
]

NOT_SETTING = [
    "import os\nfrom scripts.utils.venv_guard import _SENTINEL\n"
    "value = os.environ.get(_SENTINEL)",
    "import os\nfrom scripts.utils.venv_guard import _SENTINEL\n"
    "os.environ.pop(_SENTINEL, None)",
    '# os.environ["%s"] = "1"  # a comment, not a copy' % _venv._SENTINEL,
    '"""os.environ[_SENTINEL] = "1" in a docstring."""',
    "import os\nos.environ[\"SOMETHING_ELSE\"] = \"1\"",
]


@pytest.mark.parametrize("source", SPELLINGS, ids=range(len(SPELLINGS)))
def test_every_way_of_setting_the_sentinel_is_seen(source):
    """The positive control the stray scan never had.

    `strays == []` is green over a corpus the detector cannot read, so the
    detector needs its own evidence. Case 2 is the measured miss: the old regex
    answered False for it.
    """
    assert sets_the_sentinel(source), source


@pytest.mark.parametrize("source", NOT_SETTING, ids=range(len(NOT_SETTING)))
def test_reading_the_sentinel_is_not_setting_it(source):
    """The true negative. A detector that answered True for everything would
    satisfy the test above and fail the whole suite on this file, which reads
    the sentinel four times."""
    assert not sets_the_sentinel(source), source


def test_the_old_regex_could_not_see_the_imported_spelling():
    """The measurement, kept as a test so the reason for the AST walk survives.

    If this ever fails the regex has been widened, and the fallback path in
    `sets_the_sentinel` is no longer the narrow thing this comment says it is.
    """
    imported = SPELLINGS[2]
    assert not _SETS_THE_SENTINEL.search(imported)
    assert sets_the_sentinel(imported)


def _tracked(rel: str) -> bool:
    """Is this path in git's index? Used to judge a file that vanished mid-scan."""
    proc = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel],
                          cwd=str(ROOT), capture_output=True, text=True, check=False)
    return proc.returncode == 0


def test_no_test_module_carries_its_own_copy_of_the_guard():
    """The cross-satisfying copies, refused as a class rather than one by one.

    A module that sets the sentinel for itself is indistinguishable in its own
    output from one that inherited it, which is exactly how three copies came to
    cover for each other. The root conftest is the only place it belongs.

    THE MID-SCAN DISAPPEARANCE, and why it is tolerated rather than ignored.
    This scan walks the LIVE tests directory while the rest of the suite runs
    beside it under xdist, and one test in that suite writes a real file into
    that directory and deletes it again:
    `tests/test_turn_check.py::test_the_test_lane_deselects_slow_marked_tests`
    creates `tests/test_turn_check_slow_fixture.py`, because `turn-check.py`
    only picks up test files whose path is under `tests/`, so a `tmp_path`
    fixture would not exercise the lane at all.

    Land between that write and that unlink and `rglob` yields a path whose
    `read_text` then raises FileNotFoundError. It happened twice on 2026-08-22
    and could not be reproduced in 17 clean runs plus a dedicated 8-iteration
    hunt; it was recorded as unexplained in the audit verdict and diagnosed on
    2026-08-23 from a full traceback, which named the file.

    A file that disappears while the suite runs was never part of the
    repository, so it cannot be a checked-in stray copy of the guard — but that
    is asserted, not assumed: `git ls-files` has to agree the path is untracked
    before it is dropped. A TRACKED file that vanishes is a real finding and
    still fails.
    """
    paths = sorted(TESTS.rglob("test_*.py"))
    # An empty stray list is green over zero files, so a renamed tests
    # directory or a changed suffix would switch this guard off in silence.
    # Measured 2026-08-26: 642 test modules under tests/. The floor sits well
    # below that so ordinary deletion never trips it, and well above zero so a
    # collapsed scan does.
    assert len(paths) >= 380, f"the scan collapsed to {len(paths)} files"
    strays = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        try:
            body = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            assert not _tracked(rel), (
                f"{rel} is tracked by git but disappeared mid-scan"
            )
            continue
        if sets_the_sentinel(body):
            strays.append(rel)
    assert strays == []


def test_a_vanished_untracked_file_is_dropped_but_a_tracked_one_is_not():
    """Both halves of the mid-scan tolerance, without waiting for the race.

    `read_text` is made to raise FileNotFoundError for one path at a time. An
    untracked path must be dropped; a tracked path really did disappear from a
    checkout and must still fail.

    THE GHOST NEEDS ITS OWN NAME. This test first used
    `tests/test_turn_check_slow_fixture.py`, the same path the lane test
    writes, and its `unlink(missing_ok=True)` then deleted that test's fixture
    out from under it whenever the two landed on different xdist workers at the
    same moment. `test_the_test_lane_deselects_slow_marked_tests` failed on its
    own cleanup with FileNotFoundError, once in five full-suite runs on
    2026-08-23. A fix for a race that introduced a second race; the name below
    is unique to this test and must stay that way.
    """
    real_read = Path.read_text
    ghost = TESTS / "test_venv_guard_vanish_probe.py"   # untracked, ours alone
    tracked = TESTS / "conftest.py"                      # not matched by rglob
    victim = {"path": ghost}

    def _read(self, *a, **kw):
        if self == victim["path"]:
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_read(self, *a, **kw)

    assert not _tracked(ghost.relative_to(ROOT).as_posix()), (
        "the probe path is tracked now; pick another untracked example"
    )
    assert _tracked(tracked.relative_to(ROOT).as_posix())
    # No other test may write this path, or the two will delete each other's
    # scratch file under xdist. That is exactly how this test broke the lane
    # test on 2026-08-23.
    # This scan needs the SAME mid-scan tolerance the test exists to verify,
    # and until 2026-08-25 it did not have it. `errors="replace"` covers a
    # decode failure, never a missing file, so this loop walked the live tests
    # directory with a bare read while the lane test wrote and deleted
    # `tests/test_turn_check_slow_fixture.py` beside it. It lost that race in a
    # full-suite run and failed with FileNotFoundError - the very race
    # documented at length two functions above. A path that vanishes mid-scan
    # was never checked in, so it cannot own the ghost name.
    owners = []
    for candidate in sorted(TESTS.rglob("test_*.py")):
        if candidate.name == Path(__file__).name:
            continue
        try:
            body = candidate.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        if ghost.name in body:
            owners.append(candidate.name)
    assert owners == [], f"{ghost.name} is also written by {owners}"

    monkeypatch = pytest.MonkeyPatch()
    try:
        # The untracked ghost: present to rglob, absent to read. Must pass.
        ghost.write_text("# transient\n", encoding="utf-8")
        monkeypatch.setattr(Path, "read_text", _read)
        try:
            test_no_test_module_carries_its_own_copy_of_the_guard()
        finally:
            monkeypatch.undo()
            ghost.unlink(missing_ok=True)

        # A TRACKED test file that vanishes is a real finding.
        #
        # Pick the first TRACKED file, not the first file. This used to take
        # `sorted(...)[0]` and assert it was tracked, which quietly assumed the
        # alphabetically-first test module is always committed. On 2026-08-24 a
        # new, not-yet-committed `tests/bridge/test_a_...py` sorted ahead of
        # everything and failed the assertion -- a green suite turning red on a
        # file that had nothing to do with this guard. Writing a new test should
        # never break an unrelated one.
        victim["path"] = next(
            p for p in sorted(TESTS.rglob("test_*.py"))
            if _tracked(p.relative_to(ROOT).as_posix())
        )
        monkeypatch.setattr(Path, "read_text", _read)
        with pytest.raises(AssertionError, match="tracked by git"):
            test_no_test_module_carries_its_own_copy_of_the_guard()
    finally:
        monkeypatch.undo()
        ghost.unlink(missing_ok=True)


def _candidate_interpreters() -> list:
    """Interpreters that might not be this venv's, discovered rather than named.

    Two sources, neither a literal path: the interpreter this venv was BUILT
    from (`sys.base_prefix`), and the first `python3` on a PATH with the venv's
    own bin directory removed. Which of them differs from the venv interpreter
    is a property of the machine, so both are tried.
    """
    found = [Path(sys.base_prefix) / "bin" / "python3"]
    venv_bin = str(_venv.venv_python().parent)
    path = os.pathsep.join(
        part for part in os.environ.get("PATH", "").split(os.pathsep)
        if part and Path(part) != Path(venv_bin)
    )
    on_path = shutil.which("python3", path=path)
    if on_path:
        found.append(Path(on_path))
    return found


def _foreign_interpreter() -> Path:
    """An interpreter whose re-exec target really differs, or skip.

    `ensure_venv` compares RESOLVED paths, so a candidate that resolves to the
    same file as `.venv/bin/python` cannot reproduce the defect however it is
    spelled: it re-execs nothing. A skip here says the measurement could not run
    on this machine, which is the honest answer and not a pass.
    """
    target = _venv.venv_python().resolve()
    for candidate in _candidate_interpreters():
        if not candidate.is_file() or candidate.resolve() == target:
            continue
        probe = subprocess.run([str(candidate), "-c", "import pytest"],
                               capture_output=True, text=True, timeout=60,
                               check=False)
        if probe.returncode == 0:
            return candidate
    pytest.skip("no interpreter outside the venv, carrying pytest, to measure with")


def test_a_run_under_a_foreign_interpreter_still_prints_its_output():
    """The bite: the defect itself, reproduced end to end and then absent.

    tests/test_push_all_gate.py loads a script that calls `ensure_venv()` at
    module scope, so collecting it under an interpreter that is not the venv's is
    exactly the situation the guard exists for. The child's sentinel is stripped
    from the environment so the guard has to come from the collected
    tests/conftest.py and from nowhere else.

    The assertion is VISIBILITY, not success. The child may well fail: a script
    imported under the system interpreter can miss a pinned dependency, and the
    freeze gate speaks its own state at session start. What it may never do is
    produce a run with no output at all, which is what the re-exec produced --
    both file descriptors point at pytest's capture files, so neither stream
    reaches this process.
    """
    interpreter = _foreign_interpreter()
    env = {key: value for key, value in os.environ.items() if key != _venv._SENTINEL}

    proc = subprocess.run(
        [str(interpreter), "-m", "pytest", "tests/test_push_all_gate.py",
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
        check=False,
    )

    assert (proc.stdout + proc.stderr).strip(), (
        "the child run printed nothing at all, which is what an ensure_venv "
        "re-exec inside pytest's capture looks like"
    )


def test_a_venv_symlinked_to_the_invoking_interpreter_still_re_execs(
    tmp_path, monkeypatch
):
    """A stdlib `python -m venv` layout must not read as "already there".

    Regression for a fail-open measured 2026-08-05. `ensure_venv` compared
    `Path(sys.executable).resolve()` against `venv_python().resolve()`. A venv
    built by the stdlib `python -m venv` -- documented in the setup notes as a
    supported path -- symlinks `.venv/bin/python` to the very system interpreter
    an operator types, so both sides collapsed onto one real file, the function
    returned, and the suite ran under the system interpreter with none of the
    pinned dependencies. The docstring above it promised the opposite.

    Real symlinks, never a monkeypatched comparison: the defect lived entirely in
    what `resolve()` does to a link, so a test that stubbed resolution would have
    passed against the broken code. The re-exec itself is stubbed, because
    `os.execv` replaces this process and there is nothing left to assert in.
    """
    base = Path(sys.executable).resolve()
    fake_venv = tmp_path / ".venv" / "bin"
    fake_venv.mkdir(parents=True)
    link = fake_venv / "python"
    link.symlink_to(base)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text(
        f"home = {base.parent}\n", encoding="utf-8")

    calls = []
    monkeypatch.setattr(_venv, "venv_python", lambda: link)
    monkeypatch.setattr(_venv.os, "execv", lambda path, argv: calls.append(path))
    monkeypatch.delenv(_venv._SENTINEL, raising=False)
    # The module remembers whether it ever saw the sentinel, because it now
    # POPS the variable so a relaunch cannot disable the guard for every
    # descendant process. Clearing the env alone is no longer enough inside
    # one pytest process, where conftest sets it at import.
    monkeypatch.setattr(_venv, "_SENTINEL_SEEN", False)
    monkeypatch.setattr(sys, "executable", str(base))
    # A script for the guard to relaunch. This case is about the IDENTITY
    # comparison, so it used to borrow the runner's `sys.argv[0]`: a real path
    # under a plain `pytest`, the literal "-c" inside an xdist worker, because
    # execnet spawns workers that way. `ensure_venv` now refuses to exec a path
    # that is not a file, so the borrowed argv decided the outcome of a test
    # that is not about argv.
    entry = tmp_path / "entry.py"
    entry.write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(entry)])

    _venv.ensure_venv()

    assert calls == [str(link)], (
        "ensure_venv treated a venv symlinked to the invoking interpreter as "
        "the same environment and skipped the re-exec, so the suite would run "
        "without the pinned dependency set"
    )


def test_the_invoking_interpreter_itself_does_not_re_exec(tmp_path, monkeypatch):
    """The other half: no loop when it really IS the same interpreter.

    Without this, the test above is satisfied by an `ensure_venv` that re-execs
    unconditionally -- which the sentinel would stop from looping forever and
    which would still double the startup cost of every script in the workspace.
    """
    fake_venv = tmp_path / ".venv" / "bin"
    fake_venv.mkdir(parents=True)
    link = fake_venv / "python"
    link.symlink_to(Path(sys.executable).resolve())

    calls = []
    monkeypatch.setattr(_venv, "venv_python", lambda: link)
    monkeypatch.setattr(_venv.os, "execv", lambda path, argv: calls.append(path))
    monkeypatch.delenv(_venv._SENTINEL, raising=False)
    # The module remembers whether it ever saw the sentinel, because it now
    # POPS the variable so a relaunch cannot disable the guard for every
    # descendant process. Clearing the env alone is no longer enough inside
    # one pytest process, where conftest sets it at import.
    monkeypatch.setattr(_venv, "_SENTINEL_SEEN", False)
    monkeypatch.setattr(sys, "executable", str(link))

    _venv.ensure_venv()

    assert calls == []
