"""Shard 47: a guard that relaunched a process with no script to relaunch.

`scripts/utils/venv_guard.ensure_venv()` re-execs the running script under the
project `.venv` whenever another interpreter launched it. It built the new argv
as `[target, Path(sys.argv[0]).resolve(), *sys.argv[1:]]`.

`sys.argv[0]` is not always a script. Under `python -c "<code>"` it is the
literal string `"-c"`, and the code itself appears NOWHERE in argv. So the
guard resolved `-c` against the working directory and re-exec'd

    [.venv/bin/python, "<cwd>/-c"]

which dies with `can't open file '<cwd>/-c': [Errno 2] No such file or
directory`, having discarded the payload it was supposed to be protecting. The
REPL (`argv[0] == ""`) and `python -` (`argv[0] == "-"`) have the same shape.

MEASURED 2026-08-28. Running the suite from a git worktree with the engine's own
interpreter puts two DIFFERENT `.venv/bin` directories on the two sides of the
identity check, so `ensure_venv` did not return early and every subprocess-based
test that shells out with `[sys.executable, "-c", ...]` failed:

    25 failed, 14438 passed

Twenty-four in `tests/test_import_purity.py`, whose assertion message reads "is
not import-pure (a blocked optional dep is imported or sys.exit() runs at import
time)". Not one of those scripts was impure. A test that fails with a confident
diagnosis of something that is not happening is worse than one that fails with
nothing, because the next person spends the afternoon making pure code purer.

Nothing can recover a `-c` payload from argv, so the fix does not try. It
refuses to exec a path that is not a file, says on stderr that the relaunch did
not happen and why, and continues. That is this workspace's stated preference
under `.claude/rules/scope-claims.md`: name what you left out rather than let a
tool imply coverage it does not have. Silence would be the guard failing open
invisibly, which its own docstring calls worse than no guard at all.

Tests: this file. See also tests/test_venv_relaunch_guard.py, which owns the
identity comparison and the sentinel.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import venv_guard as _venv  # noqa: E402


def _fake_target(tmp_path: Path) -> Path:
    """A `.venv/bin/python` in a directory that is NOT the running one.

    The directory is what `interpreter_identity` compares, so this is what makes
    `ensure_venv` decide a relaunch is due.
    """
    binary = tmp_path / ".venv" / "bin"
    binary.mkdir(parents=True)
    link = binary / "python"
    link.symlink_to(Path(sys.executable).resolve())
    return link


def _armed(monkeypatch, tmp_path):
    """Put `ensure_venv` in the state where it would re-exec, and record execv.

    Returns the list execv appends to. `conftest.py` sets the sentinel at import
    for the whole pytest process and `ensure_venv` remembers it in module state,
    so clearing the environment alone does not disarm it.
    """
    calls = []
    monkeypatch.setattr(_venv, "venv_python", lambda: _fake_target(tmp_path))
    monkeypatch.setattr(_venv.os, "execv",
                        lambda path, argv: calls.append((path, list(argv))))
    monkeypatch.delenv(_venv._SENTINEL, raising=False)
    monkeypatch.setattr(_venv, "_SENTINEL_SEEN", False)
    return calls


# ==========================================================================
# 1 - the three argv[0] values that are not a script
# ==========================================================================

@pytest.mark.parametrize("argv0,label", [
    ("-c", "python -c"),
    ("", "the REPL"),
    ("-", "python - (stdin)"),
])
def test_a_process_with_no_script_is_not_re_execed(argv0, label, monkeypatch,
                                                   tmp_path, capsys):
    calls = _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", [argv0])

    _venv.ensure_venv()

    assert calls == [], f"{label} was re-exec'd with a path that is not a script"


@pytest.mark.parametrize("argv0", ["-c", "", "-"])
def test_the_refusal_is_stated_on_stderr(argv0, monkeypatch, tmp_path, capsys):
    """Returning silently would be the guard failing open with no trace, which
    is the outcome its own docstring calls worse than having no guard."""
    _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", [argv0])

    _venv.ensure_venv()

    err = capsys.readouterr().err
    assert "venv-guard" in err
    assert repr(argv0) in err, err
    assert sys.executable in err


def test_the_refusal_names_the_interpreter_it_wanted(monkeypatch, tmp_path, capsys):
    """An operator reading the line has to be able to act on it, which means
    knowing which interpreter the process should have been under."""
    target = _fake_target(tmp_path)
    monkeypatch.setattr(_venv, "venv_python", lambda: target)
    monkeypatch.setattr(_venv.os, "execv", lambda path, argv: None)
    monkeypatch.delenv(_venv._SENTINEL, raising=False)
    monkeypatch.setattr(_venv, "_SENTINEL_SEEN", False)
    monkeypatch.setattr(sys, "argv", ["-c"])

    _venv.ensure_venv()

    assert str(target) in capsys.readouterr().err


def test_stdin_is_refused_even_when_a_file_named_dash_exists(monkeypatch, tmp_path):
    """Why the literals are checked BEFORE the file test, not instead of it.

    `Path("-").resolve()` is `<cwd>/-`, and `is_file()` normally answers no, so
    the file test alone looks like it already covers stdin. It stops covering it
    in a directory that happens to contain a file called `-`: argv[0] then
    resolves to a real file, the guard re-execs it, and `python - < script.py`
    runs somebody else's file instead of the operator's stdin.

    A mutation that dropped `""` and `"-"` from the tuple survived every other
    test in this file, which is what asked the question.
    """
    calls = _armed(monkeypatch, tmp_path)
    decoy = tmp_path / "-"
    decoy.write_text("print('not yours')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["-"])

    _venv.ensure_venv()

    assert calls == [], "python - re-exec'd a file named '-' sitting in the cwd"


def test_the_empty_argv0_literal_is_defensive_only():
    """Stated rather than tested, because there is nothing left to test.

    `Path("").resolve()` is the working DIRECTORY, so `is_file()` refuses it on
    its own and no file can be planted to change that. The `""` entry in the
    tuple therefore adds no reachable behaviour; it is kept because it names the
    REPL case for a reader, beside the two entries that do carry weight. Written
    down so the next mutation run finds the answer instead of chasing an
    equivalent mutant.
    """
    assert Path("").resolve().is_dir()
    assert not Path("").resolve().is_file()


def test_an_argv0_naming_a_missing_file_is_also_refused(monkeypatch, tmp_path, capsys):
    """`-c` is the shape that bit, but the property is wider: never exec a path
    that is not there. A deleted or renamed script has the same failure."""
    calls = _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "gone.py")])

    _venv.ensure_venv()

    assert calls == []
    assert "names no script file" in capsys.readouterr().err


def test_an_argv0_naming_a_directory_is_refused(monkeypatch, tmp_path, capsys):
    """`.exists()` would accept a directory and exec it. The check is
    `is_file()` for that reason."""
    calls = _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", [str(tmp_path)])

    _venv.ensure_venv()

    assert calls == []


# ==========================================================================
# 2 - the ordinary case still relaunches
# ==========================================================================

def test_a_real_script_is_still_re_execed(monkeypatch, tmp_path):
    """The other half. A guard that refused everything would pass every test
    above and silently stop protecting any script in the workspace."""
    script = tmp_path / "run.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    calls = _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", [str(script), "--flag", "value"])

    _venv.ensure_venv()

    assert len(calls) == 1
    _path, argv = calls[0]
    assert argv[1] == str(script.resolve())
    assert argv[2:] == ["--flag", "value"], "the script's own arguments were lost"


def test_the_relaunch_still_sets_the_loop_sentinel(monkeypatch, tmp_path):
    script = tmp_path / "run.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", [str(script)])

    _venv.ensure_venv()

    assert _venv.os.environ.get(_venv._SENTINEL) == "1"
    monkeypatch.delenv(_venv._SENTINEL, raising=False)


def test_a_refusal_does_not_set_the_sentinel(monkeypatch, tmp_path):
    """The sentinel exists to stop an exec loop. No exec happened, so setting it
    would disarm the guard for the rest of this process for no reason."""
    _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["-c"])

    _venv.ensure_venv()

    assert _venv._SENTINEL not in _venv.os.environ


def test_a_relative_script_path_is_still_resolved(monkeypatch, tmp_path):
    """Resolving argv[0] is the part that was right: `python run.py` from
    another directory must relaunch with the absolute path."""
    script = tmp_path / "run.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    calls = _armed(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run.py"])

    _venv.ensure_venv()

    assert calls[0][1][1] == str(script.resolve())


# ==========================================================================
# 3 - end to end, against a real interpreter
# ==========================================================================

def test_a_dash_c_payload_survives_the_guard(tmp_path):
    """The whole defect in one subprocess: run `-c` code that calls the guard
    under a foreign venv, and check the code's own output comes back.

    Before the fix this printed nothing and exited 2 with `can't open file
    '<cwd>/-c'`. A unit test with a stubbed `execv` cannot show that, because
    the real `execv` is what replaced the process image.
    """
    foreign = tmp_path / ".venv" / "bin"
    foreign.mkdir(parents=True)
    (foreign / "python").symlink_to(Path(sys.executable).resolve())

    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts.utils import venv_guard\n"
        f"venv_guard.venv_python = lambda: __import__('pathlib').Path({str(foreign / 'python')!r})\n"
        "venv_guard.ensure_venv()\n"
        "print('PAYLOAD RAN')\n"
    )
    env = dict(_venv.os.environ)
    env.pop(_venv._SENTINEL, None)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, cwd=str(tmp_path), timeout=60, env=env)

    assert result.returncode == 0, result.stderr
    assert "PAYLOAD RAN" in result.stdout
    assert "can't open file" not in result.stderr
    assert "venv-guard" in result.stderr


def test_the_guard_is_silent_when_no_relaunch_was_due(tmp_path):
    """The warning prints only where a relaunch was actually owed, so it cannot
    become noise on every `-c` invocation in normal use."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts.utils import venv_guard\n"
        "venv_guard.venv_python = lambda: __import__('pathlib').Path(sys.executable)\n"
        "venv_guard.ensure_venv()\n"
        "print('PAYLOAD RAN')\n"
    )
    env = dict(_venv.os.environ)
    env.pop(_venv._SENTINEL, None)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, cwd=str(tmp_path), timeout=60, env=env)

    assert result.returncode == 0, result.stderr
    assert "PAYLOAD RAN" in result.stdout
    assert "venv-guard" not in result.stderr


def test_the_guard_still_returns_early_when_the_venv_is_absent(monkeypatch, tmp_path):
    """Unchanged behaviour, pinned because the new branch sits below it: a
    machine with no `.venv` must not start printing a warning per script."""
    calls = _armed(monkeypatch, tmp_path)
    monkeypatch.setattr(_venv, "venv_python", lambda: tmp_path / "nope" / "python")
    monkeypatch.setattr(sys, "argv", ["-c"])

    _venv.ensure_venv()

    assert calls == []
