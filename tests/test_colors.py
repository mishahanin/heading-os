"""`supports_ansi()` decides whether a caller emits escapes at all.

It was added on 2026-08-19 for one word in the checkpoint threshold menu, and it
has a branch this repository's runners never take: the Windows arm sits behind
`os.name == "nt"`, so it is unreachable on Linux and macOS. An unreachable branch
with no test is a branch that breaks silently for whoever runs the engine on
Windows.

The check is deliberately NOT `isatty()`. Every caller that needs it writes to a
pipe - a Claude Code hook emits JSON on stdout and the TUI renders it - so
`isatty()` answers False on exactly the surface where colour does work.

Two defects in THIS file were measured on 2026-09-01 and are fixed below.

**The Windows cases used to rebind the interpreter's `os.name`.** Every one of
them ran `monkeypatch.setattr(colors.os, "name", "nt")`, and `colors.os` IS the
stdlib `os` module, so that statement set `os.name = "nt"` process-wide for the
duration of the test. `pathlib.Path.__new__` reads `os.name` to choose between
`PosixPath` and `WindowsPath`, so while any of those tests was running, EVERY
`Path(...)` anywhere in the process raised `NotImplementedError: cannot
instantiate 'WindowsPath' on your system`. Measured directly, and measured again
through pytest: mutating `supports_ansi`'s last line to `!= "dumb"` made two of
these tests fail, and pytest's own `repr_failure` built a `Path(os.getcwd())`
while the patch was still live - reporting runs before monkeypatch teardown - so
the session died with an INTERNALERROR, printed no `FAILED` line and no `failed`
count, and a mutation harness reading the summary scored the regression as
SURVIVED. A real Windows regression would have been reported the same way.
`_windows()` below rebinds the module REFERENCE inside `colors` instead, which
leaves the stdlib untouched; `test_the_windows_cases_leave_pathlib_alone` is the
guard that keeps it that way.

**`TERM=dumb` on POSIX had no test.** The check moved to the top of the function
on 2026-08-30 precisely so it applies off Windows, where `TERM=dumb` is the one
place it actually occurs (cron, an Emacs shell buffer, a minimal CI container,
`env -i`). Moving it back inside the `nt` arm - the exact regression the fix
undoes - passed all six cases that were here. `test_posix_honours_term_dumb`
fails on that mutation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import colors  # noqa: E402

_WINDOWS_TERMINAL_VARS = ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI")


class _OsWithName:
    """The real `os`, with `name` overridden and nothing else touched.

    Proxying rather than listing what `supports_ansi` reads today: a stub that
    carried only `name` and `environ` would raise the day the function starts
    reading a third attribute, and a stub that carried a COPY of `environ` would
    silently ignore `monkeypatch.setenv`. `__getattr__` fires only for names not
    found on the instance or the class, so everything but `name` is the genuine
    module object.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def __getattr__(self, attr):
        return getattr(os, attr)


def _windows(monkeypatch) -> None:
    """Put `colors` on the Windows branch without moving the interpreter there."""
    monkeypatch.setattr(colors, "os", _OsWithName("nt"))
    for var in _WINDOWS_TERMINAL_VARS:
        monkeypatch.delenv(var, raising=False)


def _posix(monkeypatch) -> None:
    monkeypatch.setattr(colors, "os", _OsWithName("posix"))


def test_posix_always_colours(monkeypatch):
    """The first line of the function, and the case every runner here takes."""
    _posix(monkeypatch)
    monkeypatch.delenv("TERM", raising=False)
    assert colors.supports_ansi() is True


def test_posix_honours_term_dumb(monkeypatch):
    """The 2026-08-30 fix, which nothing pinned until 2026-09-01.

    `TERM=dumb` is read ahead of the platform test on purpose. It is close to
    meaningless on Windows, where `TERM` is almost never set, and it is the one
    place the variable actually appears on POSIX: cron, an Emacs shell buffer, a
    minimal CI container, `env -i`. Before the fix a caller gating on this
    emitted raw escape sequences into a log file. Moving the check back inside
    the `nt` arm passes every other case in this file.
    """
    _posix(monkeypatch)
    monkeypatch.setenv("TERM", "dumb")
    assert colors.supports_ansi() is False


def test_posix_with_an_ordinary_term_still_colours(monkeypatch):
    """Anti-vacuity for the case above: `dumb` must be the only POSIX refusal."""
    _posix(monkeypatch)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert colors.supports_ansi() is True


def test_the_windows_cases_leave_pathlib_alone(monkeypatch):
    """The guard for this file's own fixture, not for `colors`.

    `os.name` is global state that `pathlib` reads on every `Path()` call. If
    `_windows` ever goes back to setting it, this test names that directly
    instead of letting it surface as an INTERNALERROR in some unrelated failure
    three months from now.
    """
    _windows(monkeypatch)
    monkeypatch.setenv("TERM", "")

    assert colors.supports_ansi() is False, "the Windows branch was not reached"
    assert os.name != "nt", "the interpreter's os.name was rebound process-wide"
    assert type(Path(os.getcwd())).__name__ == "PosixPath"


@pytest.mark.parametrize("var", _WINDOWS_TERMINAL_VARS)
def test_a_modern_windows_terminal_colours(monkeypatch, var):
    """Windows Terminal, VS Code, ANSICON and ConEmu all handle VT100 and each
    announces itself with its own variable, so any one of them is sufficient."""
    _windows(monkeypatch)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv(var, "1")
    assert colors.supports_ansi() is True


def test_bare_windows_console_does_not_colour(monkeypatch):
    """Classic cmd.exe: no terminal variable, no TERM. This is the case the whole
    function exists for, and the one that prints raw escapes when it is wrong."""
    _windows(monkeypatch)
    monkeypatch.delenv("TERM", raising=False)
    assert colors.supports_ansi() is False


def test_term_dumb_does_not_colour(monkeypatch):
    """A TERM is set, so the loop above finds nothing, and `dumb` is the one
    value that means the opposite of what a set TERM usually means."""
    _windows(monkeypatch)
    monkeypatch.setenv("TERM", "dumb")
    assert colors.supports_ansi() is False


def test_an_empty_term_does_not_colour(monkeypatch):
    """`TERM=""` is set-but-useless. `bool(term)` has to carry this, because
    `term != "dumb"` alone would read an empty string as a capable terminal."""
    _windows(monkeypatch)
    monkeypatch.setenv("TERM", "")
    assert colors.supports_ansi() is False


def test_a_named_term_colours(monkeypatch):
    """Claude Code's own TUI sets TERM to xterm-256color or similar."""
    _windows(monkeypatch)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert colors.supports_ansi() is True


def test_the_constants_are_still_raw_escapes():
    """Callers concatenate these directly. A guard applied INSIDE the constants
    would silently disarm every existing caller that never asks the question."""
    assert colors.GREEN.startswith("\033[")
    assert colors.RESET == "\033[0m"
