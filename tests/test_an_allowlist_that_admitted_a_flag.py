"""Shard 02-p2: an allowlist that let a flag through into argument position, a
cap measured on the wrong string, a status command that could throw, and a
header list that inverted the order it described.

* ``terminal._SESSION_ID_RE`` sits under a comment promising "the
  most-restrictive pattern that still accepts every legitimate value". A
  session id never begins with ``-``, and the pattern allowed it.
  ``{session_id}`` is interpolated bare straight after ``claude --resume``, so
  ``--dangerously-skip-permissions`` - thirty characters, every one of them in
  the allowed class - passed validation and arrived on the command line as a
  FLAG rather than as a resume target. Every other interpolated token in those
  strings is prefixed, env-assignment-bound, base64 or metacharacter-stripped;
  this was the one uncontrolled argument-position value, and the dashboard's
  /launch path supplies it.

* ``_encode_context`` said it caps the payload at 8 KB and measured the
  base64 string instead. Base64 inflates by 4/3, so a 7 KB context was inside
  the documented limit, outside the real one, and dropped with no error.

* ``browser.cmd_status`` read the lock file unguarded, while the two other
  readers of the same file in the same module both catch OSError. A health
  command must report, not throw.

* ``terminal``'s module header listed the Linux emulators in an order that
  inverted ``_LINUX_TERMINAL_CANDIDATES`` - which IS the precedence, since
  ``find_linux_terminal`` returns the first match.

Run: python3 -m pytest tests/test_an_allowlist_that_admitted_a_flag.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon import terminal  # noqa: E402


# ============================================================
# The session id that could be a flag
# ============================================================

@pytest.mark.parametrize("bad", [
    "--dangerously-skip-permissions",   # the reported reproduction
    "-h",
    "--help",
    "-",
    "--resume",
    "-x",
    "_leading-underscore",              # also not a session id
])
def test_a_flag_shaped_session_id_is_refused(bad):
    assert not terminal._SESSION_ID_RE.match(bad), bad


@pytest.mark.parametrize("builder", ["build_tmux_command", "build_wt_command"])
def test_both_builders_refuse_it(builder):
    """The validation is what protects the argument position, on both paths."""
    fn = getattr(terminal, builder)
    with pytest.raises(ValueError):
        fn("alice", "t", "/srv/workspace", "noop", "--dangerously-skip-permissions")


def test_a_real_session_id_still_builds():
    cmd = terminal.build_tmux_command(
        "alice", "t", "/srv/workspace", "noop", "2ab651eb-8513-4af5-95e5-d6dbea356c60")
    joined = " ".join(cmd)
    assert "claude --resume 2ab651eb-8513-4af5-95e5-d6dbea356c60" in joined


@pytest.mark.parametrize("good", [
    "2ab651eb-8513-4af5-95e5-d6dbea356c60",
    "a",
    "0",
    "A1_b-2",
    "9" * 64,
])
def test_every_legitimate_shape_is_still_accepted(good):
    """A tightened allowlist that refuses real values is a different defect."""
    assert terminal._SESSION_ID_RE.match(good), good


def test_the_length_ceiling_is_unchanged():
    assert not terminal._SESSION_ID_RE.match("a" * 65)
    assert terminal._SESSION_ID_RE.match("a" * 64)


def test_no_session_id_is_still_allowed():
    """`session_id=None` means "no resume target", and must stay valid."""
    cmd = terminal.build_tmux_command("alice", "t", "/srv/workspace", "noop", None)
    assert "--resume" not in " ".join(cmd)


# ============================================================
# The cap that measured the wrong string
# ============================================================

def test_the_cap_is_measured_on_the_encoded_string():
    """The command line carries the encoded blob, so that is what the limit is."""
    payload = {"k": "x" * 7000}
    encoded = terminal._encode_context(payload)
    raw_len = len(json.dumps(payload, default=str).encode("utf-8"))
    assert raw_len < 8192, "the payload is inside the OLD documented limit"
    assert encoded is None, "and outside the real one, which is what ships"


def test_a_context_that_fits_the_encoded_cap_is_returned():
    payload = {"k": "x" * 4000}
    encoded = terminal._encode_context(payload)
    assert encoded is not None
    assert len(encoded) <= 8192
    assert json.loads(base64.b64decode(encoded))["k"] == "x" * 4000


def test_an_empty_context_is_still_none():
    assert terminal._encode_context(None) is None
    assert terminal._encode_context({}) is None


def test_the_docstring_names_the_string_it_measures():
    doc = terminal._encode_context.__doc__
    assert "ENCODED string" in doc
    # The correction quotes the sentence it replaced, so pin the order.
    assert doc.index("ENCODED string") < doc.index('said "caps payload at 8 KB"')


# ============================================================
# The status command that could throw
# ============================================================

def _stub_probes(browser, monkeypatch):
    """Silence the network probes; this section is about the lock read."""
    monkeypatch.setattr(browser, "_lock_state", dict)
    monkeypatch.setattr(browser, "is_running", lambda _b: False)
    monkeypatch.setattr(browser, "_port_listening", lambda _p: False)
    monkeypatch.setattr(browser, "_cdp_ready", lambda _p: False)


class _Args:
    port = None
    browser = "brave"


def test_status_reports_an_unreadable_lock_instead_of_raising(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """`_lock_state` and `stop_comet` both guard this read; status did not."""
    import scripts.browser as browser

    lock = tmp_path / "browser-cdp.json"
    lock.write_text("{}", encoding="utf-8")
    _stub_probes(browser, monkeypatch)
    monkeypatch.setattr(browser, "_active_lock_file", lambda: lock)

    real_read = Path.read_text

    def _boom(self, *a, **kw):
        if self == lock:
            raise PermissionError("permission denied")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)

    code = browser.cmd_status(_Args())
    out = capsys.readouterr().out
    assert code in (0, 2)
    assert "unreadable" in out


def test_status_still_prints_a_readable_lock(tmp_path, monkeypatch, capsys):
    import scripts.browser as browser

    lock = tmp_path / "browser-cdp.json"
    lock.write_text('{"pid": 1234}', encoding="utf-8")
    _stub_probes(browser, monkeypatch)
    monkeypatch.setattr(browser, "_active_lock_file", lambda: lock)

    browser.cmd_status(_Args())
    assert '"pid": 1234' in capsys.readouterr().out


# ============================================================
# The header list that inverted the precedence
# ============================================================

def test_the_header_lists_the_emulators_in_detection_order():
    """`find_linux_terminal` returns the first match, so the tuple IS policy."""
    header = terminal.__doc__
    positions = [header.index(name) for name in terminal._LINUX_TERMINAL_CANDIDATES]
    assert positions == sorted(positions), (
        "the header names the candidates in an order the code does not use"
    )


def test_the_wrapper_is_still_first_in_the_tuple():
    """The deliberate choice the tuple's own comment explains."""
    assert terminal._LINUX_TERMINAL_CANDIDATES[0] == "x-terminal-emulator"
    assert terminal._LINUX_TERMINAL_CANDIDATES[-1] == "xterm"
