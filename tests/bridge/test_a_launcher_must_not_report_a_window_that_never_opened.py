"""The terminal launcher: a false invariant, a false success, three schemas.

Found by the 2026-08-23 engine audit, shard `scripts-02-p3`, and every case
below was reproduced against this tree on 2026-08-24 before it was fixed.

**The false invariant.** The module said its initial prompt was "constrained to
ASCII-safe chars without double quotes or shell metacharacters so it embeds
cleanly inside the cmd.exe `/k \"...\"` parameter". Three values are
interpolated into that string and the sentence was true of none of them:
`subject` went through a sanitizer that passed `% ^ ! & | < > ( ) $` and every
non-ASCII printable, and the operator's name and voice-reference path went
through nothing at all. Measured: an `operator.yaml` name of
``A" & whoami > C:\\pwn.txt & "`` put the breakout quote into the built command
verbatim, and a subject of ``see %USERPROFILE% now`` survived to be expanded by
cmd.exe, which expands inside quoted regions. The fix bans the metacharacters
and applies the sanitizer to all three, so the sentence is now true. It
deliberately does NOT ban non-ASCII: the subject comes from an email, this
operator reads Russian and Hebrew, and mojibake under a legacy codepage is a
display problem while a dropped subject is a lost one.

**The false success.** `spawn_or_focus` ran the tmux session create as
`Popen(...).wait(timeout=10)` on one line. The exit code was never read, so a
failed `tmux new-session` still returned ``launched: True`` and the caller then
attached to a session that does not exist. `TimeoutExpired` was neither caught
nor documented, and the Popen handle was discarded on the same line, so a slow
tmux both crashed the caller and leaked. Note what fixing this did to the
existing endpoint tests: they had been passing with an unconfigured MagicMock
whose exit code was never an integer, because nothing looked.

**Three schemas.** The Linux branch returned `attached` and `attach_hint`; the
macOS and Windows branches returned neither, so any caller reading either key
raised KeyError on two of three platforms. `attached` was also a claim the
function had not established - the attach is fire-and-forget by design - and it
suppressed the fallback hint whenever it claimed success, leaving a user who
was told a window opened with no way to reach the session.

One finding from this shard is recorded as UNVERIFIED, not fixed: the claim that
`x-terminal-emulator` is tried before `gnome-terminal` and handed a `-e` its
real binary rejects. Debian ships `gnome-terminal.wrapper` as that alternative
precisely to translate `-e`, and there is no GNOME desktop here to measure on.
Reordering the candidate list on an untested claim can only break the common
case, so the order stands.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon import terminal as T  # noqa: E402

# A stand-in operator. Real identities live in the DATA overlay; the engine
# gets 007.
_BOND = {"name": "James Bond", "voice_reference": "reference/agent-007-voice.md"}


def _operator(monkeypatch, **over) -> None:
    import scripts.utils.operator_identity as oi
    monkeypatch.setattr(oi, "get_operator", lambda: {**_BOND, **over})


def _inner(**ctx) -> str:
    """The string cmd.exe parses. `context` needs a valid conv_id to get a prompt."""
    return T.build_wt_command(
        "bond", "t", r"C:\w", "email-respond", None,
        {"conv_id": "abc123", **ctx},
    )[-1]


# --- the prompt cannot break out of the quoted region ------------------------

def test_an_operator_name_holding_a_quote_cannot_break_out(monkeypatch):
    _operator(monkeypatch, name='A" & whoami > C:\\pwn.txt & "')
    inner = _inner()
    assert "whoami" in inner, "the fixture no longer carries the payload"
    assert inner.count('"') == 2, (
        "the operator name closed the `cmd /k \"...\"` region; everything after "
        f"the extra quote runs as a cmd.exe command:\n  {inner}"
    )


def test_a_voice_reference_holding_a_quote_cannot_break_out(monkeypatch):
    _operator(monkeypatch, voice_reference='ref.md" & calc.exe & "')
    assert _inner().count('"') == 2


def test_an_operator_name_that_sanitizes_to_nothing_still_reads_as_a_sentence(monkeypatch):
    """Stripping must not produce `a reply in 's voice ()`."""
    _operator(monkeypatch, name="%%%", voice_reference="&&&")
    inner = _inner()
    assert "the operator's voice" in inner, inner
    assert "the voice reference" in inner, inner


# --- the sanitizer bans what cmd.exe actually acts on ------------------------

@pytest.mark.parametrize("ch", list('"%^!&|<>()$`'))
def test_the_sanitizer_drops_every_shell_metacharacter(ch):
    assert ch not in T._safe_for_shell_arg(f"a{ch}b")


def test_a_percent_in_a_subject_is_not_left_for_cmd_to_expand(monkeypatch):
    """`%VAR%` is substituted INSIDE the quoted region, so a variable whose
    value carries a quote reopens the breakout through a sanitized field."""
    _operator(monkeypatch)
    inner = _inner(subject="see %USERPROFILE% now")
    assert "%" not in inner, inner


def test_a_cyrillic_subject_survives_on_purpose():
    """Deliberate carve-out, not an oversight. Tightening this to ASCII drops
    the subject of every Russian and Hebrew email, which is a real regression
    for this operator; mojibake is only a display problem."""
    out = T._safe_for_shell_arg("Договор на 2026 год")
    assert "Договор" in out and "2026" in out


def test_the_sanitizer_still_keeps_ordinary_prose():
    """Anchor: a ban list that eats everything passes every test above."""
    out = T._safe_for_shell_arg("Re: PandaDoc reminder - reference/voice.md, v2")
    assert out == "Re: PandaDoc reminder - reference/voice.md, v2"


# --- tmux failure is reported, never dressed as a launch ---------------------

class _FakeProc:
    """Stands in for the tmux child. `communicate`, because the launcher now
    captures tmux's stderr so a failure can quote the real cause instead of
    guessing at one."""

    def __init__(self, rc=0, hang=False, stderr=b""):
        self._rc, self._hang, self.killed = rc, hang, False
        self._stderr = stderr
        self.returncode = None

    def communicate(self, timeout=None):
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired(cmd="tmux", timeout=timeout or 0)
        self.returncode = self._rc
        return b"", self._stderr

    def wait(self, timeout=None):
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired(cmd="tmux", timeout=timeout or 0)
        self.returncode = self._rc
        return self._rc

    def kill(self):
        self.killed = True


def _no_live_session(monkeypatch):
    """Pin the has-session probe. Without this the test shells out to the REAL
    tmux, so a developer with a `31c-bond` session open would take the
    already-exists path and never reach the code under test."""
    monkeypatch.setattr(T, "_tmux_has_session", lambda session: False)


def _posix(monkeypatch, proc):
    monkeypatch.setattr(T.sys, "platform", "linux")
    monkeypatch.setattr(T.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: proc)


def test_a_failed_tmux_new_session_is_not_reported_as_launched(monkeypatch):
    _posix(monkeypatch, _FakeProc(rc=1))
    with pytest.raises(T.TerminalUnavailable, match="exited 1"):
        T.spawn_or_focus("bond", "t", Path("/nonexistent-dir"), "osint", None)


def test_a_hanging_tmux_raises_the_documented_exception_and_kills_the_child(monkeypatch):
    """`TimeoutExpired` was undocumented AND the handle was discarded on the
    same line, so there was nothing left to kill."""
    proc = _FakeProc(hang=True)
    _posix(monkeypatch, proc)
    with pytest.raises(T.TerminalUnavailable, match="did not finish"):
        T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)  # noqa: S108
    assert proc.killed, "the tmux child was leaked with no handle to kill it"


def test_a_successful_tmux_still_launches(monkeypatch):
    """Anchor: the two guards above must not have turned every launch into a
    failure."""
    _posix(monkeypatch, _FakeProc(rc=0))
    assert T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)["launched"] is True  # noqa: S108


# --- one schema on every platform -------------------------------------------

_KEYS = {"launched", "attach_attempted", "attach_hint", "command"}


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_every_platform_returns_the_same_keys(monkeypatch, platform):
    monkeypatch.setattr(T.sys, "platform", platform)
    monkeypatch.setattr(T.shutil, "which", lambda name: f"/usr/bin/{name}")
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: _FakeProc(rc=0))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert set(T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)) == _KEYS  # noqa: S108


def test_the_fallback_hint_survives_a_successful_attach(monkeypatch):
    """The old code returned the hint ONLY when it believed the attach failed.
    Since it could not observe the attach at all, a denied macOS automation
    prompt left the user told a window had opened and given no way in."""
    monkeypatch.setattr(T.sys, "platform", "darwin")
    monkeypatch.setattr(T.shutil, "which", lambda name: f"/usr/bin/{name}")
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: _FakeProc(rc=0))
    out = T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)  # noqa: S108
    assert out["attach_attempted"] is True
    assert out["attach_hint"] == "tmux attach -t 31c-bond"


# --- the GUI branch, on purpose rather than by accident ----------------------
#
# `terminal = find_linux_terminal() if _is_linux_gui_session() else None` reads
# DISPLAY and WAYLAND_DISPLAY off the ambient environment. Until 2026-08-27 no
# test set either, so the True side ran only on a workstation that happened to
# have a graphical session and never on CI. Branch coverage measured that day:
# with a display, lines 93-97 and 492 of terminal.py are covered; with
# `env -u DISPLAY -u WAYLAND_DISPLAY` they are not, and the suite passes both
# ways. `find_linux_terminal` had no direct caller in any test at all.

def test_this_directory_runs_headless_unless_a_test_says_otherwise():
    """Pins the autouse fixture in `tests/bridge/conftest.py`.

    Nothing else can. Removing that fixture changes no assertion in this
    directory - measured 2026-08-27, the mutation survived every test - because
    the suite passes with a display and without one. What it changes is WHICH
    LINES RUN: with DISPLAY set, `find_linux_terminal()` and the attach Popen
    execute on this workstation and never on CI, so two machines were running
    two different programs and reporting the same green.

    A coverage difference is invisible to assertions, so it needs a test that
    asks the question directly.
    """
    assert not T._is_linux_gui_session(), (
        "a bridge test is seeing this host's graphical session. The autouse "
        "`_no_graphical_session` fixture in tests/bridge/conftest.py is gone or "
        "no longer applies, and the launch path now takes a different branch "
        "here than it does on CI."
    )


def _linux_gui(monkeypatch, proc, *, terminal="/usr/bin/gnome-terminal"):
    """A Linux host with a display. `terminal=None` means no emulator installed.

    `tmux` keeps resolving in that case, deliberately: `assert_tmux_available()`
    runs first and raises `TerminalUnavailable` when `which("tmux")` is None, so
    a blanket None never reaches the branch this helper exists to reach.
    """
    monkeypatch.setattr(T.sys, "platform", "linux")
    monkeypatch.setattr(
        T.shutil, "which",
        lambda name: "/usr/bin/tmux" if name == "tmux" else terminal)
    monkeypatch.setenv("DISPLAY", ":0")
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: proc)


def test_a_graphical_session_gets_a_gui_attach(monkeypatch):
    calls = []
    proc = _FakeProc(rc=0)

    def _record(argv, *a, **kw):
        calls.append(list(argv))
        return proc

    monkeypatch.setattr(T.sys, "platform", "linux")
    monkeypatch.setattr(T.shutil, "which", lambda name: "/usr/bin/gnome-terminal")
    monkeypatch.setenv("DISPLAY", ":0")
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen", _record)

    out = T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)  # noqa: S108
    assert out["attach_attempted"] is True
    assert len(calls) == 2, f"expected tmux create then GUI attach, got {calls}"
    assert calls[0][:2] == ["tmux", "new-session"], calls[0]
    assert "attach" in calls[1], calls[1]


def test_a_wayland_session_counts_as_graphical(monkeypatch):
    """DISPLAY is not the only signal, and a Wayland-only desktop has no DISPLAY."""
    proc = _FakeProc(rc=0)
    monkeypatch.setattr(T.sys, "platform", "linux")
    monkeypatch.setattr(T.shutil, "which", lambda name: "/usr/bin/kitty")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: proc)
    assert T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)["attach_attempted"] is True  # noqa: S108


def test_a_headless_session_gets_no_gui_attach(monkeypatch):
    """The CI shape. Same code, no display, so no second process."""
    calls = []
    proc = _FakeProc(rc=0)
    monkeypatch.setattr(T.sys, "platform", "linux")
    monkeypatch.setattr(T.shutil, "which", lambda name: "/usr/bin/gnome-terminal")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _no_live_session(monkeypatch)
    monkeypatch.setattr(T.subprocess, "Popen",
                        lambda argv, *a, **kw: (calls.append(list(argv)), proc)[1])

    out = T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)  # noqa: S108
    assert out["launched"] is True
    assert out["attach_attempted"] is False
    assert len(calls) == 1, f"a headless host spawned a GUI attach: {calls}"


def test_a_graphical_session_with_no_terminal_installed_does_not_attach(monkeypatch):
    """A display is not a terminal. `find_linux_terminal` returning None is the
    other way the attach is skipped, and it was the branch `95->93` that branch
    coverage reported as never taken."""
    proc = _FakeProc(rc=0)
    _linux_gui(monkeypatch, proc, terminal=None)
    out = T.spawn_or_focus("bond", "t", Path("/tmp"), "osint", None)  # noqa: S108
    assert out["launched"] is True
    assert out["attach_attempted"] is False


def test_find_linux_terminal_returns_the_first_candidate_present(monkeypatch):
    """The precedence tuple IS policy, so the lookup that reads it needs a caller."""
    seen = []

    def _which(name):
        seen.append(name)
        return "/usr/bin/xterm" if name == "xterm" else None

    monkeypatch.setattr(T.shutil, "which", _which)
    assert T.find_linux_terminal() == "/usr/bin/xterm"
    assert seen == list(T._LINUX_TERMINAL_CANDIDATES), (
        "the lookup did not walk the candidates in their declared order"
    )


def test_find_linux_terminal_answers_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(T.shutil, "which", lambda name: None)
    assert T.find_linux_terminal() is None


def test_no_branch_claims_a_window_appeared():
    """The key is named for what the method establishes: a command was spawned.
    `attached` asserted an outcome nothing observed."""
    src = (ROOT / "scripts" / "bridge_daemon" / "terminal.py").read_text(encoding="utf-8")
    assert '"attached"' not in src, "the outcome-asserting key is back"


# --- defense in depth reaches the path that claimed it -----------------------

def test_the_linux_attach_builder_validates_its_own_slug():
    """The module header claims validation covers "any future internal caller".
    This exported builder was the one path where that was false."""
    with pytest.raises(ValueError, match="invalid user_slug"):
        T.build_linux_attach_command("/usr/bin/xterm", "bond; rm -rf /")


def test_the_linux_attach_builder_still_builds_for_a_valid_slug():
    cmd = T.build_linux_attach_command("/usr/bin/xterm", "bond")
    assert cmd == ["/usr/bin/xterm", "-e", "tmux", "attach", "-t", "31c-bond"]
