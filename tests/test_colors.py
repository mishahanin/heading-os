"""`supports_ansi()` decides whether a caller emits escapes at all.

It was added on 2026-08-19 for one word in the checkpoint threshold menu, and it
has a branch this repository's runners never take: everything under `os.name !=
"nt"` short-circuits to True on the first line, so the Windows path is
unreachable on Linux and macOS. An unreachable branch with no test is a branch
that breaks silently for whoever runs the engine on Windows.

The check is deliberately NOT `isatty()`. Every caller that needs it writes to a
pipe - a Claude Code hook emits JSON on stdout and the TUI renders it - so
`isatty()` answers False on exactly the surface where colour does work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import colors  # noqa: E402


def test_posix_always_colours(monkeypatch):
    """The first line of the function, and the case every runner here takes."""
    monkeypatch.setattr(colors.os, "name", "posix")
    monkeypatch.delenv("TERM", raising=False)
    assert colors.supports_ansi() is True


@pytest.mark.parametrize(
    "var", ["WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"]
)
def test_a_modern_windows_terminal_colours(monkeypatch, var):
    """Windows Terminal, VS Code, ANSICON and ConEmu all handle VT100 and each
    announces itself with its own variable, so any one of them is sufficient."""
    monkeypatch.setattr(colors.os, "name", "nt")
    for name in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv(var, "1")
    assert colors.supports_ansi() is True


def test_bare_windows_console_does_not_colour(monkeypatch):
    """Classic cmd.exe: no terminal variable, no TERM. This is the case the whole
    function exists for, and the one that prints raw escapes when it is wrong."""
    monkeypatch.setattr(colors.os, "name", "nt")
    for name in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI", "TERM"):
        monkeypatch.delenv(name, raising=False)
    assert colors.supports_ansi() is False


def test_term_dumb_does_not_colour(monkeypatch):
    """A TERM is set, so the loop above finds nothing, and `dumb` is the one
    value that means the opposite of what a set TERM usually means."""
    monkeypatch.setattr(colors.os, "name", "nt")
    for name in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert colors.supports_ansi() is False


def test_an_empty_term_does_not_colour(monkeypatch):
    """`TERM=""` is set-but-useless. `bool(term)` has to carry this, because
    `term != "dumb"` alone would read an empty string as a capable terminal."""
    monkeypatch.setattr(colors.os, "name", "nt")
    for name in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "")
    assert colors.supports_ansi() is False


def test_a_named_term_colours(monkeypatch):
    """Claude Code's own TUI sets TERM to xterm-256color or similar."""
    monkeypatch.setattr(colors.os, "name", "nt")
    for name in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert colors.supports_ansi() is True


def test_the_constants_are_still_raw_escapes():
    """Callers concatenate these directly. A guard applied INSIDE the constants
    would silently disarm every existing caller that never asks the question."""
    assert colors.GREEN.startswith("\033[")
    assert colors.RESET == "\033[0m"
