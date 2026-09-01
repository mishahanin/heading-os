"""The guard refused writes it was never present to refuse.

`scripts/utils/overlay_write_guard.py` wraps eleven write primitives and raises
on a write to the operator's private overlay. Every test of it passed. It still
let a real 18,857-byte operator workbook be overwritten on 2026-08-31, because
it lived in `tests/conftest.py`: pytest imports a conftest and nothing else does,
so a scratch probe run as a plain `.venv/bin/python` ran with no guard at all.
The probe called an entry point blind, `openpyxl` saved, and nothing anywhere
said a word. It was found forty minutes later, by accident.

Being correct and being present are different properties, and this file is about
the second one. Four things it pins:

1. The arming file exists in THIS venv. It is a `.pth` in site-packages, which
   `.gitignore` covers and `uv sync` rebuilds, so it disappears silently. This
   test is the thing that says so out loud.
2. RECORD mode allows the write and logs it. The mode exists because arming
   every process cannot start by refusing: the overlay is where the operator's
   work legitimately lands, and the writers that belong there are numerous. The
   refusal rule is to be DERIVED from the log, never hand-written, because a
   hand-maintained list of legitimate writers is the defect this workspace keeps
   re-finding.
3. Arming twice wraps once. Reachable the moment a `.pth` arms at interpreter
   startup and `pytest_sessionstart` then arms again. Two layers matter because
   `restore()` unwinds exactly one, so the inner layer would outlive `disarm()`.
4. The refusal message does not claim a test wrote. It used to open "a test tried
   to write", true of every caller that could reach it while the guard lived in a
   conftest, and false now. A complaint naming the wrong kind of culprit has
   already cost this repository one investigation.

Nothing here writes the operator's overlay. Refusal happens BEFORE the write, so
the guard is proven by making it refuse a real path, and RECORD mode is measured
against a `tmp_path` standing in for an overlay.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD_SRC = ROOT / "scripts" / "utils" / "overlay_write_guard.py"
INSTALLER = ROOT / "scripts" / "overlay-guard-install.py"


@pytest.fixture
def fresh():
    """A FRESH copy of the guard, so arming it cannot disturb the live session.

    These tests install and remove wrappers. Doing that to the module
    `tests/conftest.py` armed would take the operator's real overlay out of
    guard for the length of the test, which is precisely the state this file
    exists to make impossible.
    """
    spec = importlib.util.spec_from_file_location("overlay_guard_presence_copy", GUARD_SRC)
    module = importlib.util.module_from_spec(spec)
    sys.modules["overlay_guard_presence_copy"] = module
    spec.loader.exec_module(module)
    yield module
    module.disarm()
    del sys.modules["overlay_guard_presence_copy"]


def _aim(module, root):
    """Point a fresh guard at `root` and nothing else."""
    module._watched_roots = lambda: {module._LIVE_OVERLAY_LABEL: root}


# ============================================================
# 1. The arming file is present in this venv
# ============================================================

def test_the_venv_carries_the_file_that_arms_the_guard_outside_pytest():
    """The one assertion that would have failed on 2026-08-31.

    `.venv/` is gitignored and `uv sync` rebuilds site-packages, so this file is
    removed by an ordinary maintenance command with no output about it. Without
    this test the guard would be off and the suite would stay green, which is
    exactly the state that cost a workbook.

    A clone that has never run the installer fails here too, and that is
    intended: the remedy is one command, printed in the message.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "_editable_impl_heading_os_engine.pth").exists():
        pytest.skip("not the engine's editable venv, so its site-packages is not ours to assert")

    pth = site / "zz_heading_os_overlay_guard.pth"
    assert pth.exists(), (
        f"{pth} is absent, so the overlay write guard arms under pytest and in "
        f"no other process. A plain `.venv/bin/python` can overwrite the "
        f"operator's private data and nothing will refuse it. Fix: "
        f".venv/bin/python scripts/overlay-guard-install.py --install")

    line = pth.read_text(encoding="utf-8").strip()
    assert line.startswith("import "), (
        "site.py executes a .pth line only when it begins with `import`; this "
        "one does not, so the file is present and inert")
    assert "overlay_write_guard" in line, (
        f"{pth} exists but does not arm this guard: {line[:120]}")


def test_the_installer_reports_absence_rather_than_assuming_it():
    """`--check` is what a human runs after `uv sync`, so it must be honest."""
    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "HEADING_OS_OVERLAY_GUARD": ""},
    )
    site = Path(sysconfig.get_paths()["purelib"])
    present = (site / "zz_heading_os_overlay_guard.pth").exists()
    assert (result.returncode == 0) is present, (
        f"--check exited {result.returncode} while the file "
        f"{'exists' if present else 'does not exist'}: {result.stdout}{result.stderr}")


# ============================================================
# 2. The .pth arms a plain interpreter, and only when asked
# ============================================================

def _startup_probe(env_value):
    """Ask a CHILD interpreter whether the guard armed itself at startup.

    A child, because the question is about what `site.py` does before any user
    code runs, and this process is long past that point.
    """
    env = dict(os.environ)
    if env_value is None:
        env.pop("HEADING_OS_OVERLAY_GUARD", None)
    else:
        env["HEADING_OS_OVERLAY_GUARD"] = env_value
    # A scratch data root, so the child cannot reach the operator's overlay even
    # if every assertion here is wrong. It has to EXIST: `paths.env_data_root()`
    # refuses a missing directory rather than falling back, which is correct of
    # it, and pointing at one here is what exposed the swallowed-exception
    # defect above rather than testing the thing this helper is for.
    scratch = ROOT / ".tmp" / "guard-child-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    env["HEADING_OS_DATA"] = str(scratch)
    code = (
        "import sys, json;"
        # The private name, NOT `scripts.utils.overlay_write_guard`: the .pth
        # loads the guard BY PATH so it never binds the `scripts` package name.
        "g = sys.modules.get('_heading_os_overlay_guard');"
        "print(json.dumps({'armed': g is not None,"
        " 'mode': getattr(g, '_MODE', None),"
        " 'scripts_bound': 'scripts' in sys.modules,"
        " 'wrapped': __builtins__.open.__name__}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )
    assert result.returncode == 0, (
        f"a .pth that runs at interpreter startup broke the interpreter: "
        f"{result.stderr[-400:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_an_unset_variable_arms_guard_mode():
    """Default-ON, since 2026-08-31, and the default has to be provable.

    It was default-OFF while the rule was still being derived: the `.pth` did
    nothing unless the variable asked. An always-on control that needs a variable
    set is not always on, so once the census showed all 300 candidate writers are
    tracked, the default became GUARD.

    The escape is the test below, and it matters more than this one: a line that
    `site.py` runs for every process in the venv must have a state in which it
    cannot be blamed for anything.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "zz_heading_os_overlay_guard.pth").exists():
        pytest.skip("the arming file is absent; the test above is the one that reports that")
    seen = _startup_probe(None)
    assert seen["armed"] is True, "the guard is not on by default"
    assert seen["mode"] == "guard"
    assert seen["wrapped"] == "guarded_open"


def test_arming_does_not_decide_what_the_scripts_package_means():
    """MEASURED 2026-08-31: 62 tests went red the first time GUARD armed by default.

    The `.pth` did `from scripts.utils import overlay_write_guard`, which binds
    the name `scripts` in `sys.modules` to the ENGINE's package during
    `site.py`, before any user code runs. After that, `python -m
    scripts.anything` from a directory with its own `scripts/` package resolves
    against the engine instead. Every skill under `.claude/skills/` that ships a
    `scripts/` package broke, with `No module named scripts.improve_description`.

    The fix is to load the guard BY PATH under a private module name. A line
    that `site.py` runs in every process must not change what a top-level
    package name means for the rest of the interpreter, and this is the widest
    kind of damage an always-on startup hook can do: silent, global, and nothing
    to do with the thing it was added for.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "zz_heading_os_overlay_guard.pth").exists():
        pytest.skip("the arming file is absent; another test reports that")

    seen = _startup_probe(None)
    assert seen["armed"] is True, "nothing armed, so this proves nothing"
    assert seen["scripts_bound"] is False, (
        "arming bound the `scripts` package at interpreter startup; every "
        "`python -m scripts.x` run from a directory with its own scripts/ "
        "package now resolves against the engine")

    line = (site / "zz_heading_os_overlay_guard.pth").read_text(encoding="utf-8")
    assert "from scripts" not in line and "import scripts" not in line, (
        f"the .pth imports the scripts package by name: {line[:160]}")

    # And the same question asked end to end, on the shape that actually broke.
    skill = ROOT / ".claude" / "skills" / "skill-creator"
    if not (skill / "scripts").is_dir():
        pytest.skip("no skill-local scripts package to run the real check against")
    result = subprocess.run(
        [sys.executable, "-c", "import scripts, sys; print(scripts.__file__)"],
        capture_output=True, text=True, cwd=str(skill),
        env={k: v for k, v in os.environ.items() if k != "HEADING_OS_OVERLAY_GUARD"},
    )
    assert result.returncode == 0, result.stderr[-400:]
    resolved = Path(result.stdout.strip()).resolve()
    assert skill in resolved.parents, (
        f"`import scripts` from {skill} resolved to {resolved}, which is not the "
        f"skill's own package; arming stole the name")


def test_arming_does_not_take_a_capability_away_from_the_platform():
    """MEASURED 2026-08-31: wrapping `os.open` changed which algorithm shutil runs.

    `os.supports_dir_fd`, `os.supports_fd` and `os.supports_follow_symlinks` are
    sets of FUNCTION OBJECTS, and the standard library tests membership by
    IDENTITY to pick an implementation. `shutil` does it once at import:

        _use_fd_functions = ({os.open, os.stat, os.unlink, os.rmdir}
                             <= os.supports_dir_fd and ...)

    A wrapper is a different object, so replacing `os.open` removed it from every
    such set and any module imported after the wrap saw a platform that had lost
    a capability it has. Measured that day: `shutil._use_fd_functions` was True
    unguarded and False guarded, `shutil.rmtree` fell back to its legacy walk,
    and `test_an_unreadable_directory_is_removed_not_raised_over` failed with
    `OSError: Directory not empty` while passing with the guard off.

    This is the widest kind of damage the guard can do, and the reason it is
    tested from a CHILD process: the parent's `shutil` was imported before the
    wrappers went on, so the parent cannot see the defect at all.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "zz_heading_os_overlay_guard.pth").exists():
        pytest.skip("the arming file is absent; another test reports that")

    code = (
        "import os, shutil, json, sys;"
        "g = sys.modules.get('_heading_os_overlay_guard');"
        "print(json.dumps({"
        " 'armed': g is not None,"
        " 'wrapped': os.open.__name__,"
        " 'use_fd': shutil._use_fd_functions,"
        " 'open_dir_fd': os.open in os.supports_dir_fd,"
        " 'unlink_dir_fd': os.unlink in os.supports_dir_fd,"
        " 'rmdir_dir_fd': os.rmdir in os.supports_dir_fd,"
        "}))"
    )

    def ask(mode):
        env = dict(os.environ)
        if mode is None:
            env.pop("HEADING_OS_OVERLAY_GUARD", None)
        else:
            env["HEADING_OS_OVERLAY_GUARD"] = mode
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, text=True,
                                cwd=str(ROOT), env=env)
        assert result.returncode == 0, result.stderr[-400:]
        return json.loads(result.stdout.strip().splitlines()[-1])

    guarded, unguarded = ask(None), ask("off")
    assert guarded["armed"] is True and unguarded["armed"] is False
    assert guarded["wrapped"] == "guarded_os_open"

    # The comparison IS the test. A hardcoded `True` would pass on a platform
    # that never had the capability, which is exactly the false green this kind
    # of check invites.
    assert guarded["use_fd"] == unguarded["use_fd"], (
        f"arming changed shutil's algorithm: _use_fd_functions is "
        f"{guarded['use_fd']} guarded and {unguarded['use_fd']} unguarded")
    for key in ("open_dir_fd", "unlink_dir_fd", "rmdir_dir_fd"):
        assert guarded[key] == unguarded[key], (
            f"arming changed os.supports_dir_fd membership for {key}: "
            f"{guarded[key]} guarded, {unguarded[key]} unguarded")


def test_the_off_value_leaves_the_interpreter_completely_untouched():
    """The escape hatch, measured from outside the process.

    Not merely "allows writes": the module must not even be imported, or the
    escape costs interpreter startup time on every invocation and stops being an
    escape.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "zz_heading_os_overlay_guard.pth").exists():
        pytest.skip("the arming file is absent; the test above is the one that reports that")
    seen = _startup_probe("off")
    assert seen["armed"] is False, "OFF still imported and armed the guard"
    assert seen["wrapped"] == "open", "OFF left builtins.open wrapped"


@pytest.mark.parametrize("mode", ["record", "refuse"])
def test_a_plain_interpreter_arms_the_guard_when_the_variable_asks(mode):
    """The hole that let the workbook be overwritten, measured shut.

    No pytest in the child, no conftest, no import of anything by hand: a bare
    `python -c` finds the guard already installed over `builtins.open`.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "zz_heading_os_overlay_guard.pth").exists():
        pytest.skip("the arming file is absent; the test above is the one that reports that")
    seen = _startup_probe(mode)
    assert seen["armed"] is True, "the .pth did not arm the guard"
    assert seen["mode"] == mode
    assert seen["wrapped"] == "guarded_open", (
        f"the guard armed but `builtins.open` is still {seen['wrapped']}, so "
        f"nothing is actually intercepted")


def test_a_broken_data_root_variable_does_not_switch_the_guard_off():
    """MEASURED 2026-08-31, found by this file's own test failing.

    `HEADING_OS_DATA` pointing at a directory that does not exist makes
    `paths.env_data_root()` raise `DataRootError`, which is neither `OSError`
    nor `ImportError`, the two `_overlay_root()` used to catch. It escaped
    `_watch_snapshot()`, escaped `arm()`, and was swallowed by the `.pth` line's
    `except Exception: pass`. The guard did not arm. Nothing said so.

    A mistyped path in one variable disarming the whole guard is the same shape
    as the defect the structural resolver was written to fix, arriving through a
    different door. The STRUCTURAL root cannot be moved by any variable, so the
    correct behaviour is that it stays watched.
    """
    env = dict(os.environ)
    env["HEADING_OS_OVERLAY_GUARD"] = "refuse"
    env["HEADING_OS_DATA"] = str(ROOT / ".tmp" / "a-directory-that-does-not-exist")
    assert not Path(env["HEADING_OS_DATA"]).exists(), "this test needs a missing path"

    code = (
        "import sys, json;"
        "g = sys.modules.get('_heading_os_overlay_guard');"
        "print(json.dumps({'armed': g is not None,"
        " 'prefixes': list(getattr(g, '_OVERLAY_PREFIXES', ())),"
        " 'wrapped': __builtins__.open.__name__}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )
    assert result.returncode == 0, f"the interpreter did not start: {result.stderr[-400:]}"
    seen = json.loads(result.stdout.strip().splitlines()[-1])

    site = Path(sysconfig.get_paths()["purelib"])
    if not (site / "zz_heading_os_overlay_guard.pth").exists():
        pytest.skip("the arming file is absent; another test reports that")

    assert seen["wrapped"] == "guarded_open", (
        "a broken HEADING_OS_DATA left builtins.open unwrapped, so one mistyped "
        "variable disarms the guard for the whole process and prints nothing")
    assert seen["prefixes"], "the guard armed with no prefix, so it refuses nothing"
    assert any(".heading-os-data" in p for p in seen["prefixes"]), (
        f"the structural root is not among the watched prefixes {seen['prefixes']}; "
        f"the environment moved the guard off the operator's real data")


def test_an_unrecognised_value_does_not_read_as_permission(fresh):
    """A typo must not be the thing that stops a guard refusing.

    `HEADING_OS_OVERLAY_GUARD=recrod` resolving to RECORD would allow every
    write and log none of them, which is strictly worse than either real mode.
    """
    assert fresh.resolve_mode("recrod") == fresh.MODE_REFUSE
    assert fresh.resolve_mode("") == fresh.MODE_REFUSE
    assert fresh.resolve_mode(None) in (fresh.MODE_REFUSE, fresh.MODE_RECORD)
    assert fresh.resolve_mode(fresh.MODE_RECORD) == fresh.MODE_RECORD
    assert fresh.resolve_mode(fresh.MODE_REFUSE) == fresh.MODE_REFUSE


# ============================================================
# 3. RECORD allows the write and logs who made it
# ============================================================

def test_record_mode_allows_the_write_and_names_the_caller(fresh, tmp_path, monkeypatch):
    """The measurement half. It has to let the write happen, or it measures the
    guard rather than the workspace."""
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    log = tmp_path / "record.jsonl"
    _aim(fresh, overlay)
    monkeypatch.setattr(fresh, "record_log_path", lambda: log)

    fresh.arm(fresh.MODE_RECORD)

    def a_named_function_of_the_test():
        (overlay / "allowed.txt").write_text("landed")

    a_named_function_of_the_test()
    (overlay / "a-new-directory").mkdir()
    fresh.disarm()

    assert (overlay / "allowed.txt").read_text() == "landed", (
        "RECORD refused the write; then it is REFUSE under another name")
    assert (overlay / "a-new-directory").is_dir()

    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 2, f"expected one row per write, got {rows}"

    verbs = {row["verb"] for row in rows}
    assert "write" in verbs and "create a directory in" in verbs

    written = next(row for row in rows if row["verb"] == "write")
    assert written["path"].endswith("allowed.txt")
    assert written["caller"] is not None, (
        "no workspace frame was identified, so the log cannot say who wrote")
    assert "a_named_function_of_the_test" in written["caller"], (
        f"the caller field named {written['caller']} instead of the function "
        f"that made the write; a log that names pathlib.py measures nothing")
    assert written["pid"] == os.getpid()


def test_record_mode_writes_its_log_outside_the_overlay(fresh):
    """A sink inside the watched tree would report itself, forever."""
    log = fresh.record_log_path()
    for root in fresh._watched_roots().values():
        assert not str(log).startswith(str(root)), (
            f"the record log {log} is inside the watched root {root}, so every "
            f"entry would be a write that produces the next entry")


def test_the_record_sink_does_not_write_through_the_guarded_primitive(fresh, tmp_path, monkeypatch):
    """Recursion, asked as a question about behaviour rather than about code.

    The sink is built inside `_install_overlay_write_guard()` so it can close
    over the UNWRAPPED `open`. If a refactor ever has it reach the wrapped one,
    a single write into the overlay recurses. Driven here by counting: one write
    produces exactly one row, not a stack overflow and not a growing file.
    """
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    log = tmp_path / "record.jsonl"
    _aim(fresh, overlay)
    monkeypatch.setattr(fresh, "record_log_path", lambda: log)
    fresh.arm(fresh.MODE_RECORD)
    (overlay / "one.txt").write_text("x")
    fresh.disarm()
    assert len(log.read_text().splitlines()) == 1


# ============================================================
# 3b. GUARD mode: the derived rule
# ============================================================
#
# The rule is "refuse a write whose innermost workspace frame is a file git does
# not track". DERIVED, not chosen: `scripts/overlay-writer-census.py` swept 1898
# Python files across BOTH repos on 2026-08-31 and found 300 that can write the
# overlay. All 300 are tracked. The write that destroyed a real operator workbook
# came from `.tmp/frozen/behaviour.py`, which `.gitignore` covers.

def test_guard_mode_refuses_a_caller_git_does_not_track(fresh, tmp_path, monkeypatch):
    """The destructive shape. The caller lives in a file git cannot track."""
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    monkeypatch.setattr(fresh, "record_log_path", lambda: tmp_path / "record.jsonl")
    _aim(fresh, overlay)
    # An empty tracked set is a real answer meaning "git tracks nothing here",
    # NOT the None that means "could not ask". The distinction decides the mode.
    monkeypatch.setattr(fresh, "tracked_workspace_files", lambda refresh=False: frozenset())
    fresh.arm(fresh.MODE_GUARD)

    with pytest.raises(fresh.OverlayWriteRefused) as caught:
        (overlay / "destroyed.txt").write_text("x")
    fresh.disarm()

    message = str(caught.value)
    assert "not tracked by git" in message, (
        f"the refusal did not give the actual reason: {message}")
    assert not (overlay / "destroyed.txt").exists()


def test_guard_mode_allows_a_caller_git_tracks(fresh, tmp_path, monkeypatch):
    """The legitimate shape, and the half that decides whether the rule is usable.

    A guard that refuses the operator's own committed tools is a guard that gets
    switched off, after which nothing guards anything.
    """
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    log = tmp_path / "record.jsonl"
    monkeypatch.setattr(fresh, "record_log_path", lambda: log)
    _aim(fresh, overlay)
    # THIS test file is the caller, so declaring it tracked is what the real
    # `git ls-files` would say about any committed workspace file.
    monkeypatch.setattr(
        fresh, "tracked_workspace_files",
        lambda refresh=False: frozenset({str(Path(__file__).resolve())}))
    fresh.arm(fresh.MODE_GUARD)

    (overlay / "allowed.txt").write_text("landed")
    fresh.disarm()

    assert (overlay / "allowed.txt").read_text() == "landed", (
        "GUARD refused a tracked caller; the rule blocks the operator's own tools")
    assert log.exists() and log.read_text().strip(), (
        "the allowed write was not logged, so an always-on allow decision is "
        "unreviewable afterwards")


def test_guard_mode_allows_when_git_cannot_be_asked(fresh, tmp_path, monkeypatch):
    """`None` means unknown, and unknown must not refuse.

    No git on PATH, a detached worktree, a timeout: all arrive as None. Refusing
    on unknown would make every workspace tool fail the moment git hiccups.
    """
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    monkeypatch.setattr(fresh, "record_log_path", lambda: tmp_path / "record.jsonl")
    _aim(fresh, overlay)
    monkeypatch.setattr(fresh, "tracked_workspace_files", lambda refresh=False: None)
    fresh.arm(fresh.MODE_GUARD)
    (overlay / "allowed.txt").write_text("x")
    fresh.disarm()
    assert (overlay / "allowed.txt").exists()


def test_no_repo_answering_is_none_and_never_an_empty_set(fresh, monkeypatch):
    """The branch that PRODUCES None, driven instead of stood in for.

    `test_guard_mode_allows_when_git_cannot_be_asked` above patches
    `tracked_workspace_files` itself, so it proves what GUARD does with a None
    it was handed and says nothing about whether the function ever returns one.
    MEASURED 2026-09-01: mutating `if not any_answer: return None` to
    `return frozenset()` left all 149 tests across the eight overlay-guard files
    green.

    An empty frozenset is a real answer meaning "git tracks nothing here", and
    `_caller_is_tracked` reads it as such: every caller is untracked, so GUARD
    refuses EVERY overlay write. GUARD arms in every interpreter in this venv,
    so a machine with no git on PATH, a detached worktree, or one `git ls-files`
    timeout would turn the guard into a total block on the operator's own data
    with no way to tell it from a real refusal. The module's own comment calls
    the distinction load-bearing; nothing measured it.
    """
    monkeypatch.setattr(fresh, "_TRACKED", None)
    monkeypatch.setattr(fresh, "_tracked_in", lambda repo: None)

    assert fresh.tracked_workspace_files(refresh=True) is None, (
        "neither repo answered and the function reported an empty tracked set, "
        "which GUARD reads as 'nothing is tracked, refuse everything'")


def test_one_repo_answering_is_a_partial_union_not_a_refusal(fresh, monkeypatch):
    """The other side of the same branch, which the docstring states explicitly.

    "A partial answer (one repo readable, the other not) is returned as the
    partial union rather than None, because refusing to answer would disarm the
    half that did work." Without this, returning None whenever ANY repo failed
    would satisfy the test above.
    """
    engine = Path(fresh.__file__).resolve().parents[2]
    answers = {engine: frozenset({"/x/a.py"})}
    monkeypatch.setattr(fresh, "_TRACKED", None)
    monkeypatch.setattr(fresh, "_tracked_in", lambda repo: answers.get(repo))

    assert fresh.tracked_workspace_files(refresh=True) == frozenset({"/x/a.py"})


def test_a_caller_whose_filename_holds_a_colon_is_read_whole(fresh, monkeypatch):
    """`rsplit(":", 2)`, which the docstring gives a reason for and nothing pinned.

    A frame string is `path:line:function`, and a POSIX filename may contain a
    colon while a line number and a function name may not. MEASURED 2026-09-01:
    mutating `caller.rsplit(":", 2)[0]` to `caller.split(":", 1)[0]` left all
    149 tests green, because every caller path in every fixture is
    colon-free and the two spellings agree on those.

    They disagree the moment a path has one, and the disagreement refuses the
    operator's own tracked file as untracked.
    """
    tracked = "/w/od:un/tool.py"
    monkeypatch.setattr(fresh, "tracked_workspace_files",
                        lambda refresh=False: frozenset({tracked}))

    assert fresh._caller_is_tracked(f"{tracked}:42:write_report") is True, (
        "the colon in the directory name split the path short, so a tracked "
        "caller was reported as untracked and GUARD would refuse its write")
    assert fresh._caller_is_tracked("/w/od:un/other.py:42:f") is False, (
        "anchor: a genuinely untracked colon path must still be untracked")


def test_the_tracked_set_asks_both_repos(fresh):
    """The hole found on 2026-08-31 before this was two repos.

    The private DATA overlay is a SEPARATE git repository, and it tracks Python
    that legitimately writes the overlay: `admin/provision/provision_exec.py`
    among others. Asking only the engine reported the operator's own committed
    provisioning tools as untracked, which would have blocked them.
    """
    tracked = fresh.tracked_workspace_files()
    if tracked is None:
        pytest.skip("git could not be asked in this environment")

    engine = Path(fresh.__file__).resolve().parents[2]
    assert any(p.startswith(str(engine)) for p in tracked), "no engine paths at all"

    overlay = fresh._structural_overlay_root()
    if overlay is None or not (overlay / ".git").exists():
        pytest.skip("no sibling DATA repository on this clone")
    assert any(p.startswith(str(overlay)) for p in tracked), (
        f"nothing under {overlay} is in the tracked set, so every committed tool "
        f"in the operator's DATA repo would be refused as untracked")


def test_off_mode_installs_nothing_at_all(fresh, tmp_path, monkeypatch):
    """The escape hatch, and it has to cost nothing.

    GUARD is on by default, so an operator wrongly refused needs one command
    past it. If that command still wrapped every `open()` and merely allowed,
    the escape would be a slowdown rather than an exit, and the next step would
    be deleting the arming file, which removes the guard permanently.
    """
    import builtins

    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    _aim(fresh, overlay)
    pristine = builtins.open
    fresh.arm(fresh.MODE_OFF)
    assert builtins.open is pristine, "OFF wrapped the primitives anyway"
    assert fresh._RESTORE_WRITE_GUARD is None
    (overlay / "allowed.txt").write_text("x")
    assert (overlay / "allowed.txt").exists()


def test_guard_mode_skips_the_snapshot_and_the_others_take_it(fresh, tmp_path, monkeypatch):
    """Measured 2026-08-31: the snapshot is 0.53 s against 0.01 s for a bare
    interpreter, and GUARD arms on every python invocation in the workspace.

    The snapshot also has no consumer in GUARD mode: it exists to be diffed at
    the end of a bounded run, and an arbitrary script has no such moment.
    """
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    monkeypatch.setattr(fresh, "record_log_path", lambda: tmp_path / "record.jsonl")
    _aim(fresh, overlay)

    taken = []
    real_snapshot = fresh._watch_snapshot
    monkeypatch.setattr(fresh, "_watch_snapshot",
                        lambda: taken.append(1) or real_snapshot())

    fresh.arm(fresh.MODE_GUARD)
    assert taken == [], "GUARD mode paid for a snapshot nothing will read"
    fresh.disarm()

    fresh.arm(fresh.MODE_REFUSE)
    assert len(taken) == 1, "REFUSE mode skipped the snapshot the session diff needs"
    fresh.disarm()


def test_the_process_wide_entry_point_defaults_to_guard(fresh, monkeypatch):
    """What the `.pth` calls, and its default is the whole point of the change.

    An always-on control that needs a variable set is not always on.
    """
    monkeypatch.delenv(fresh.ENV_MODE, raising=False)
    assert fresh.resolve_mode(default=fresh.MODE_GUARD) == fresh.MODE_GUARD
    assert fresh.resolve_mode() == fresh.MODE_REFUSE, (
        "a direct arm() with no argument must stay REFUSE; only the .pth defaults "
        "to GUARD, because an explicit arm() in code is almost always a harness")
    monkeypatch.setenv(fresh.ENV_MODE, "off")
    assert fresh.resolve_mode(default=fresh.MODE_GUARD) == fresh.MODE_OFF
    monkeypatch.setenv(fresh.ENV_MODE, "nonsense")
    assert fresh.resolve_mode(default=fresh.MODE_GUARD) == fresh.MODE_GUARD, (
        "a typo resolved to something other than the default; a mistyped "
        "variable must never be what softens the guard")


# ============================================================
# 4. Arming twice wraps once
# ============================================================

def test_arming_twice_installs_one_layer_of_wrappers(fresh, tmp_path, monkeypatch):
    """Two layers survive one `disarm()`, which is a guard that will not switch off.

    `restore()` puts back the primitives ONE layer captured. Wrap twice and the
    inner layer keeps refusing after the code that installed it believes it is
    gone. Measured through `builtins.open` before, between and after.
    """
    import builtins

    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    monkeypatch.setattr(fresh, "record_log_path", lambda: tmp_path / "record.jsonl")
    _aim(fresh, overlay)

    pristine = builtins.open
    fresh.arm(fresh.MODE_RECORD)
    once = builtins.open
    assert once is not pristine, "the first arm() wrapped nothing"

    fresh.arm(fresh.MODE_REFUSE)
    assert builtins.open is once, (
        "the second arm() wrapped again; disarm() unwinds one layer, so the "
        "inner one would outlive it")
    assert fresh._MODE == fresh.MODE_REFUSE, (
        "the second arm() must still change the mode even though it does not "
        "wrap again, or a caller cannot tighten a guard already armed loosely")

    fresh.disarm()
    assert builtins.open is pristine, (
        "disarm() left a wrapper behind, so this process is guarded by code "
        "nothing can now remove")


# ============================================================
# 5. The refusal names what it knows, not what it guesses
# ============================================================

def test_the_refusal_does_not_claim_a_test_wrote_when_no_test_did(fresh, tmp_path, monkeypatch):
    """`.claude/rules/scope-claims.md`, applied to this guard's own message."""
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    _aim(fresh, overlay)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    fresh.arm(fresh.MODE_REFUSE)

    with pytest.raises(fresh.OverlayWriteRefused) as caught:
        (overlay / "nope.txt").write_text("x")
    fresh.disarm()

    message = str(caught.value)
    assert "a test tried" not in message, (
        f"the guard asserted a test made this write, and the interpreter knows "
        f"no such thing: {message}")
    assert "not a test run" in message
    assert "nope.txt" in message
    assert not (overlay / "nope.txt").exists(), (
        "the refusal happened after the write, so it refused nothing")


def test_the_refusal_names_the_test_when_there_is_one(fresh, tmp_path, monkeypatch):
    """The other direction, so the test above is not passing on a broken message."""
    overlay = tmp_path / "pretend-overlay"
    overlay.mkdir()
    _aim(fresh, overlay)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
    fresh.arm(fresh.MODE_REFUSE)

    with pytest.raises(fresh.OverlayWriteRefused) as caught:
        (overlay / "nope.txt").write_text("x")
    fresh.disarm()

    message = str(caught.value)
    assert "tests/test_x.py::test_y" in message
    assert "HEADING_OS_DATA" in message
    assert "not a test run" not in message
