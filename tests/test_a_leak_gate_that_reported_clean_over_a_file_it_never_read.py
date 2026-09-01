#!/usr/bin/env python3
"""The leak gate skipped an unreadable engine file and returned 0.

`scripts/leak-guard.py::check_paths` is a commit gate. It refuses a hardcoded
data-path literal in engine code. Its read was wrapped in `except OSError:
continue`, so a file it could not open was dropped from the walk and the
function returned 0, which is byte-for-byte what a clean commit looks like.

MEASURED 2026-09-01 on ONE file holding a real violation
(`P = "crm/contacts/x.md"`), the same bytes in three states:

    readable        -> 1 violation, BLOCKED
    mode 0o000      -> 0 violations, and nothing printed on any stream
    not valid UTF-8 -> RAISED UnicodeDecodeError

The first state is the control: it proves the gate can see this exact
violation. The second is the defect, and it is the shape SEC-007 exists to
refuse, a control whose failure is indistinguishable from its success. The
third is the decode class this tree keeps finding: `UnicodeDecodeError` is a
`ValueError`, not an `OSError`, so the handler could not catch it and the gate
died rather than refusing. A crash under pre-commit at least fails closed; the
silent skip did not fail at all.

Found by an AST sweep for handlers that DROP a record with no log line: an
`except` inside an accumulating loop whose body is a bare `continue` and whose
handler makes no logging call. The sweep returned 69 sites across `scripts/`
and `.claude/hooks/`, most of them legitimate, and was ranked by consequence
rather than fixed wholesale. Two security gates came out on top.

`scripts/secret-scanner.py:159` was the other, and it was WITHDRAWN after
reading it: that handler does `yield path` before its `continue`, so the record
is handed to `scan_files`, which reports it UNKNOWN and exits 2. The sweep saw
a `continue` with no logging call and could not see that `yield` was the report.

The fix keeps the gate's default answer silent and its refusals loud. It does
not widen what counts as a violation.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
import contextlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A literal that the gate really does refuse. Kept identical across every case
# below, so a state that returns 0 is returning 0 about THIS violation and not
# about some other file.
VIOLATION = 'PATH = "crm/contacts/x.md"\n'
CLEAN = "X = 1\n"
LONE_CONTINUATION = b"\xe9"


@pytest.fixture()
def gate():
    """Load leak-guard by path; its filename has a hyphen and cannot be imported."""
    spec = importlib.util.spec_from_file_location("leak_guard_under_test",
                                                  ROOT / "scripts" / "leak-guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def engine_file(tmp_path, monkeypatch):
    """A path the gate will treat as engine code.

    `check_paths` skips anything under `tests/`, anything under
    `scripts/archive/`, and anything whose routing destination is not `engine`.
    A file directly under `scripts/` satisfies all three, so the probe has to
    live there rather than in tmp_path. It is removed in teardown, and its name
    starts with an underscore so no collector picks it up.
    """
    probe = ROOT / "scripts" / "_leak_gate_probe.py"
    yield probe
    with contextlib.suppress(OSError):
        os.chmod(probe, 0o644)
    probe.unlink(missing_ok=True)


def test_the_gate_sees_the_violation_when_it_can_read_the_file(gate, engine_file):
    """The control. Without this, every other case measures nothing.

    A gate that returned 1 for all inputs would satisfy the refusal tests below
    while being useless, and a gate that could not see this literal at all would
    make the silent-skip case look correct.
    """
    engine_file.write_text(VIOLATION, encoding="utf-8")
    assert gate.check_paths([engine_file]) == 1


def test_the_gate_says_nothing_about_a_clean_file(gate, engine_file):
    """The other control, and the anchor against over-refusal."""
    engine_file.write_text(CLEAN, encoding="utf-8")
    assert gate.check_paths([engine_file]) == 0


def test_an_unreadable_engine_file_is_refused_not_skipped(gate, engine_file, capsys):
    """The defect. The same violating bytes, with the read taken away.

    Skipped for the whole class rather than the permission bit alone: a gate
    that cannot open a file has not checked it, whatever stopped it.
    """
    engine_file.write_text(VIOLATION, encoding="utf-8")
    os.chmod(engine_file, 0o000)
    if os.access(engine_file, os.R_OK):
        pytest.skip("this filesystem or user ignores the read bit, so the "
                    "unreadable state cannot be produced here")
    result = gate.check_paths([engine_file])
    out = capsys.readouterr().out
    assert result == 1, (
        "the gate returned 0 over an engine file it could not open. That is "
        "the same answer it gives for a clean file, so a commit carrying an "
        "unreadable file passes as checked.")
    assert "_leak_gate_probe.py" in out, (
        f"the refusal did not name the file that was not read: {out!r}")
    assert "could not read" in out


def test_a_non_utf8_engine_file_is_refused_not_crashed(gate, engine_file, capsys):
    """The decode half. UnicodeDecodeError is a ValueError, not an OSError."""
    engine_file.write_bytes(b"X = 1  # caf" + LONE_CONTINUATION + b"\n")
    result = gate.check_paths([engine_file])
    out = capsys.readouterr().out
    assert result == 1
    assert "UnicodeDecodeError" in out, (
        f"the refusal did not say why the file could not be read: {out!r}")


def test_a_valid_utf8_file_with_accents_is_still_read(gate, engine_file):
    """Anchor. The fix must not turn every non-ASCII byte into a refusal.

    A gate that refused anything above ASCII would pass both refusal tests
    above and block ordinary commits, which is how a gate gets switched off.
    """
    engine_file.write_text('X = "caf\u00e9 latt\u00e9"\n', encoding="utf-8")
    assert gate.check_paths([engine_file]) == 0


def test_an_unreadable_file_does_not_hide_a_readable_violation(gate, engine_file,
                                                               tmp_path, capsys):
    """Both branches must report, not the first one only.

    Written because the fix could have been `elif`, which would report the
    unreadable file and swallow the violation beside it, or report the
    violation and swallow the unreadable file. Either way the commit message
    would name one problem and hide the other.
    """
    second = ROOT / "scripts" / "_leak_gate_probe_two.py"
    second.write_text(VIOLATION, encoding="utf-8")
    engine_file.write_text(CLEAN, encoding="utf-8")
    os.chmod(engine_file, 0o000)
    try:
        if os.access(engine_file, os.R_OK):
            pytest.skip("the read bit is not honoured here")
        result = gate.check_paths([engine_file, second])
        out = capsys.readouterr().out
        assert result == 1
        assert "_leak_gate_probe_two.py" in out, (
            "the readable violation was swallowed by the unreadable-file branch")
        assert "_leak_gate_probe.py" in out, (
            "the unreadable file was swallowed by the violation branch")
    finally:
        second.unlink(missing_ok=True)


def test_the_handler_can_catch_a_decode_error(gate):
    """Asked of the source, as a second jaw on the behavioural cases.

    Cheap, and it fails on the exact edit that would reintroduce the crash even
    if someone also deleted the behavioural test above.
    """
    src = (ROOT / "scripts" / "leak-guard.py").read_text(encoding="utf-8")
    assert "except (OSError, UnicodeDecodeError)" in src, (
        "the read handler no longer catches UnicodeDecodeError, so one engine "
        "file that is not valid UTF-8 kills the gate instead of refusing")
