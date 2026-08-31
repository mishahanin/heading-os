"""The review server must not destroy accumulated feedback, and must not kill strangers.

Two defects in `.claude/skills/skill-creator/eval-viewer/generate_review.py`,
found by the 2026-08-31 review.

F5. `do_POST` saved the operator's review feedback with a plain
``self.feedback_path.write_text(...)``. `write_text` opens the destination with
mode ``w``, which TRUNCATES it before a single byte of the new content is
written. A crash, a full disk, or a kill in that window leaves `feedback.json`
empty or half-written, and the file holds every review the operator has typed
so far. The workspace convention is a same-directory tempfile then
``os.replace()``; `scripts/utils/atomic.py` ships the helper.

It is NOT importable here. Measured 2026-08-31: this script is run as
``python generate_review.py <workspace>``, which puts the `eval-viewer`
directory on ``sys.path[0]`` and no repository root anywhere on the path, so
``import scripts.utils.atomic`` raises ModuleNotFoundError under a bare
``python3``. The module docstring's "no dependencies beyond the Python stdlib"
promise is the same constraint stated from the other side. The pattern is
therefore reimplemented locally.

F11. `_kill_port` ran ``lsof -ti :3117`` and sent SIGTERM to every PID it
returned, unconditionally, before binding. It never checked that the process was
a previous instance of this viewer. On a developer machine port 3117 can be
anything. `main()` already degrades correctly on a busy port - it falls back to
an ephemeral one - so the kill bought nothing that was not already handled. The
fix REFUSES: it reports who holds the port and lets the existing fallback run.
Refusing beats a clever identification heuristic, because a heuristic that is
wrong once has already killed something.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / ".claude" / "skills" / "skill-creator" / "eval-viewer" / "generate_review.py"

_spec = importlib.util.spec_from_file_location("_gen_review_server_under_test", GENERATOR)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """This file drives an HTTP handler. It must never open a socket."""
    def _refuse(*args, **kwargs):
        raise AssertionError("a test in this file attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


# ---------------------------------------------------------------- F5


class _Recorder:
    """A stand-in for the socket side of BaseHTTPRequestHandler."""

    def __init__(self, body: bytes):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(body))}
        self.status = None

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, *args, **kwargs):
        pass

    def end_headers(self):
        pass


def _handler_for(feedback_path: Path, body: bytes):
    """Build a ReviewHandler without touching a socket.

    `BaseHTTPRequestHandler.__init__` runs the whole request cycle, so the
    handler is constructed unbound and given exactly the attributes `do_POST`
    reads.
    """
    handler = object.__new__(gen.ReviewHandler)
    rec = _Recorder(body)
    handler.path = "/api/feedback"
    handler.feedback_path = feedback_path
    handler.workspace = feedback_path.parent
    handler.skill_name = "example-skill"
    handler.previous = {}
    handler.benchmark_path = None
    handler.rfile = rec.rfile
    handler.wfile = rec.wfile
    handler.headers = rec.headers
    handler.send_response = rec.send_response
    handler.send_header = rec.send_header
    handler.end_headers = rec.end_headers
    return handler, rec


class _FailsOnWrite:
    """A file object that opens for real, then refuses to write.

    This is the crash window made deterministic. The underlying `io.open` has
    already run, so whatever the caller opened in mode ``w`` is now truncated on
    disk - exactly the state a process death mid-write leaves behind.
    """

    def __init__(self, fh):
        self._fh = fh

    def write(self, *args, **kwargs):
        raise OSError("simulated crash mid-write")

    def __enter__(self):
        self._fh.__enter__()
        return self

    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._fh, name)


@pytest.fixture
def arm_write_failure(monkeypatch):
    """Arm the crash simulation ON DEMAND, after the test has laid its fixture.

    Armed at fixture time it would also break the test's own setup write, which
    is how the first draft of this file failed for the wrong reason.
    """
    def _arm():
        real_open = io.open

        def fake_open(file, mode="r", *args, **kwargs):
            fh = real_open(file, mode, *args, **kwargs)
            if "w" in mode or "a" in mode or "+" in mode:
                return _FailsOnWrite(fh)
            return fh

        monkeypatch.setattr(io, "open", fake_open)

    return _arm


def test_a_crash_mid_write_does_not_destroy_existing_feedback(tmp_path, arm_write_failure):
    feedback = tmp_path / "feedback.json"
    original = json.dumps({"reviews": [{"run_id": "eval-1", "feedback": "hours of typing"}]}, indent=2) + "\n"
    feedback.write_text(original, encoding="utf-8")

    body = json.dumps({"reviews": [{"run_id": "eval-1", "feedback": "replacement"}]}).encode()
    handler, rec = _handler_for(feedback, body)
    arm_write_failure()
    handler.do_POST()

    assert feedback.read_text(encoding="utf-8") == original, (
        "a write that could not complete replaced the operator's accumulated feedback"
    )
    assert rec.status == 500, "a failed save must be reported, not swallowed"


def test_a_crash_mid_write_leaves_no_tmp_litter(tmp_path, arm_write_failure):
    feedback = tmp_path / "feedback.json"
    feedback.write_text('{"reviews": []}\n', encoding="utf-8")

    body = json.dumps({"reviews": [{"run_id": "eval-1", "feedback": "x"}]}).encode()
    handler, _ = _handler_for(feedback, body)
    arm_write_failure()
    handler.do_POST()

    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name != "feedback.json")
    assert leftovers == [], f"the aborted write left {leftovers} behind"


def test_a_successful_save_still_writes_the_new_content(tmp_path):
    """The accepted case. A write path with no success case is not a write path."""
    feedback = tmp_path / "feedback.json"
    feedback.write_text('{"reviews": []}\n', encoding="utf-8")

    payload = {"reviews": [{"run_id": "eval-1", "feedback": "looks right"}]}
    handler, rec = _handler_for(feedback, json.dumps(payload).encode())
    handler.do_POST()

    assert rec.status == 200
    assert json.loads(feedback.read_text(encoding="utf-8")) == payload
    assert sorted(p.name for p in tmp_path.iterdir()) == ["feedback.json"]


def test_a_malformed_payload_is_still_rejected_without_touching_the_file(tmp_path):
    feedback = tmp_path / "feedback.json"
    original = '{"reviews": [{"run_id": "eval-1", "feedback": "keep me"}]}\n'
    feedback.write_text(original, encoding="utf-8")

    handler, rec = _handler_for(feedback, b'{"not_reviews": 1}')
    handler.do_POST()

    assert rec.status == 500
    assert feedback.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------- F11


class _CompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


class _FakeSubprocess:
    """Stand-in for the `subprocess` MODULE inside the module under test.

    Never `monkeypatch.setattr(gen.subprocess, "run", ...)`: `gen.subprocess` IS
    the shared stdlib module object, so that rebinds `subprocess.run`
    interpreter-wide and any concurrently-collected test sees the fake. Swapping
    the module REFERENCE is contained to `gen`.
    """

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, run):
        self._run = run

    def run(self, *args, **kwargs):
        return self._run(*args, **kwargs)


class _OsShim:
    """The real `os`, with `kill` replaced by a recorder that never signals.

    Same containment argument: `gen.os` is the shared stdlib module, and
    `atomic_write_text` needs the rest of it (fdopen, replace, unlink) to keep
    working.
    """

    def __init__(self, on_kill):
        self._on_kill = on_kill

    def kill(self, pid, sig):
        return self._on_kill(pid, sig)

    def __getattr__(self, name):
        return getattr(os, name)


def test_the_port_check_never_signals_a_process(tmp_path, monkeypatch, capsys):
    """A fake lsof reports a stranger. Nothing may be signalled."""
    signalled = []

    monkeypatch.setattr(gen, "subprocess", _FakeSubprocess(lambda *a, **k: _CompletedProcess("4242\n4243\n")))
    monkeypatch.setattr(gen, "os", _OsShim(lambda pid, sig: signalled.append((pid, sig))))

    gen.check_port_holder(3117)

    assert signalled == [], f"the viewer signalled {signalled}"


def test_the_port_check_reports_who_holds_the_port(monkeypatch, capsys):
    monkeypatch.setattr(gen, "subprocess", _FakeSubprocess(lambda *a, **k: _CompletedProcess("4242\n")))
    monkeypatch.setattr(gen, "os", _OsShim(lambda pid, sig: pytest.fail("must not signal")))

    holders = gen.check_port_holder(3117)

    assert holders == [4242]
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "4242" in combined and "3117" in combined


def test_a_free_port_reports_no_holder_and_says_nothing(monkeypatch, capsys):
    monkeypatch.setattr(gen, "subprocess", _FakeSubprocess(lambda *a, **k: _CompletedProcess("")))
    monkeypatch.setattr(gen, "os", _OsShim(lambda pid, sig: pytest.fail("must not signal")))

    assert gen.check_port_holder(3117) == []
    out = capsys.readouterr()
    assert (out.out + out.err).strip() == ""


def test_a_missing_lsof_is_reported_and_not_fatal(monkeypatch, capsys):
    def _no_lsof(*a, **k):
        raise FileNotFoundError("lsof")

    monkeypatch.setattr(gen, "subprocess", _FakeSubprocess(_no_lsof))
    assert gen.check_port_holder(3117) == []
    assert "lsof" in (capsys.readouterr().err)


def test_the_module_no_longer_carries_a_killer(monkeypatch):
    """`_kill_port` is gone by name as well as by behaviour.

    Leaving the old function importable beside the new one is how a caller keeps
    reaching the dangerous half.
    """
    assert not hasattr(gen, "_kill_port")
