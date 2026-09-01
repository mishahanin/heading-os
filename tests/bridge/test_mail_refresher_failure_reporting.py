"""A failed mail fetch must say why, and must not claim the data is fresh.

Measured 2026-08-12: the bridge log carried two lines reading
`bridge.email: producer exited 2; stderr=` and nothing else. Exit 2 is the
producer's `exchange_unreachable` path, which writes a JSON diagnostic
carrying the detail and a pointer to the WSL/CGNAT thread -- to STDOUT. The
refresher logged only stderr, so the one useful sentence was captured and
discarded, leaving an operator with a bare exit code.

The same call then bumped `inbox` unconditionally, advancing the dashboard's
data_time, so an inbox that had not been fetched read as refreshed seconds ago.
Governed by .claude/rules/scope-claims.md.

**Three of the four ways it says why had no test.** `_failure_detail` prefers the
producer's structured stdout payload, then falls back to stderr, then to raw
stdout, then to a fixed sentence. Only the first branch was covered: every test
here handed it `UNREACHABLE`, a JSON object carrying `error`. MEASURED
2026-08-31 by collapsing the other three (`mail.py:97-101`) to `return ""`::

    $ .venv/bin/python -m pytest tests/bridge -q
    1312 passed, 1 skipped

Byte-identical to the unmutated baseline. The `stderr` branch is the one that
matters most, because logging stderr alone IS the 2026-08-12 behaviour this file
was written to prevent regressing to: collapse it and an operator is back to a
bare exit code. The four branch tests below, plus the two truncation bounds, are
the cases that fail without them.
"""
from __future__ import annotations

import json
import logging
import subprocess

import pytest

from scripts.bridge_daemon import state as state_mod
from scripts.bridge_daemon.refreshers import mail

UNREACHABLE = json.dumps({
    "error": "exchange_unreachable",
    "detail": "TransportError: connection timed out",
    "hint": "WSL->Exchange (mail.31c.io) on CGNAT not routed",
})


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def st():
    return state_mod.State()


def _run(monkeypatch, tmp_path, st, result):
    # The producer is placed under the root that is PASSED IN, not patched over
    # a module constant. `refresh()` resolves it from its `workspace_root`
    # argument as of 2026-08-24; before that the argument was decoration and the
    # module constant was the only lookup, so a refresh against one root could
    # run a script out of another tree.
    script = tmp_path / "scripts" / "email-intelligence.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(mail.subprocess, "run", lambda *a, **k: result)
    mail.refresh(tmp_path, st)


def test_the_producers_stdout_diagnostic_reaches_the_log(monkeypatch, tmp_path, st, caplog):
    with caplog.at_level(logging.WARNING):
        _run(monkeypatch, tmp_path, st, _Result(2, stdout=UNREACHABLE))
    text = caplog.text
    assert "exchange_unreachable" in text, f"reason discarded: {text}"
    assert "CGNAT" in text or "connection timed out" in text, text


def test_a_failed_fetch_does_not_advance_the_freshness_clock(monkeypatch, tmp_path, st):
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    version_before = st.version("inbox")

    _run(monkeypatch, tmp_path, st, _Result(2, stdout=UNREACHABLE))

    assert st.data_time("inbox") == fresh_before, "stale inbox reported as freshly fetched"
    assert st.version("inbox") > version_before, "ETag must still advance so the UI re-reads"


def test_a_successful_fetch_does_advance_the_freshness_clock(monkeypatch, tmp_path, st):
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    _run(monkeypatch, tmp_path, st, _Result(0, stdout="{}"))
    assert st.data_time("inbox") != fresh_before


def test_a_timeout_is_also_reported_as_not_fresh(monkeypatch, tmp_path, st, caplog):
    """The timeout branch, actually reached.

    This test used to patch `mail.PRODUCER_SCRIPT` and plant its stub at
    `tmp_path/producer.py`, neither of which `refresh()` looks at: it resolves
    `producer_script(workspace_root)`, which is
    `tmp_path/scripts/email-intelligence.py`. So the missing-producer branch ran,
    `_timeout` was never called, and the assertion held because BOTH branches
    leave the clock alone. Measured 2026-08-27: this test alone covered 33% of
    the module and never executed lines 126-154, which include the
    `except subprocess.TimeoutExpired` handler it is named for. Deleting that
    handler would not have failed it.

    The log line is the positive signal that the branch ran, so the
    missing-producer path can no longer satisfy this test.
    """
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    calls = []

    def _timeout(*a, **k):
        calls.append(a)
        raise subprocess.TimeoutExpired(cmd="producer", timeout=300)

    script = tmp_path / "scripts" / "email-intelligence.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(mail.subprocess, "run", _timeout)
    with caplog.at_level(logging.WARNING):
        mail.refresh(tmp_path, st)

    assert calls, "the producer was never invoked; the missing-script branch ran"
    assert "producer timed out after" in caplog.text, caplog.text
    assert st.data_time("inbox") == fresh_before


def test_a_missing_producer_script_is_not_fresh_either(tmp_path, st, caplog):
    """The absence is real, not patched: tmp_path has no scripts/ directory."""
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    assert not (tmp_path / "scripts" / "email-intelligence.py").exists()
    with caplog.at_level(logging.WARNING):
        mail.refresh(tmp_path, st)
    assert "producer script missing" in caplog.text, caplog.text
    assert st.data_time("inbox") == fresh_before


def test_bump_defaults_to_fresh_for_every_existing_caller(st):
    """26 call sites pass no keyword; none of them may change behaviour."""
    st.bump("tasks")
    assert st.data_time("tasks") is not None


def test_the_producer_is_resolved_under_the_root_it_was_handed(monkeypatch, tmp_path, st, caplog):
    """A root with no producer must be reported as missing, whatever this
    module's own tree holds. The engine clone running the daemon always has
    `scripts/email-intelligence.py`, so a module-constant lookup found it and
    ran it -- against the OTHER root's cwd -- instead of skipping."""
    called = []
    monkeypatch.setattr(mail.subprocess, "run",
                        lambda *a, **k: called.append(a) or _Result(0))
    with caplog.at_level(logging.WARNING):
        mail.refresh(tmp_path, st)          # tmp_path has no scripts/ tree
    assert not called, "ran a producer that does not exist under the given root"
    assert "producer script missing" in caplog.text
    assert str(tmp_path) in caplog.text


def test_producer_script_defaults_to_this_modules_engine_root():
    """The old constant still resolves, for importers that never pass a root."""
    assert mail.producer_script().name == "email-intelligence.py"
    assert mail.producer_script() == mail.PRODUCER_SCRIPT


# --- the other three ways `_failure_detail` says why ---------------------------
#
# Unit tests on the function, plus one end-to-end through `refresh()` so the
# stderr text is proved to reach the log and not merely to be returned. Every
# test above this line fed it the structured-stdout branch.


def test_stderr_is_reported_when_the_producer_prints_no_json(st):
    """The 2026-08-12 branch, and the one worth most.

    Logging stderr alone is what this file exists to prevent regressing to, so
    the fallback that still reads stderr has to be pinned: collapse it and an
    operator is back to `producer exited 2;` with nothing after the semicolon.
    """
    detail = mail._failure_detail(
        _Result(2, stdout="", stderr="Traceback: TransportError at line 41"))
    assert detail == "stderr=Traceback: TransportError at line 41", detail


def test_stdout_is_reported_when_it_is_not_json_and_stderr_is_empty(st):
    """A producer that dies mid-print leaves prose on stdout and nothing on
    stderr. That prose is the only evidence there is, so it must be carried."""
    detail = mail._failure_detail(
        _Result(2, stdout="connecting to mail host ... killed", stderr=""))
    assert detail == "stdout=connecting to mail host ... killed", detail


def test_a_json_object_without_an_error_key_falls_through_to_stderr(st):
    """The structured branch requires `error`, not merely valid JSON.

    `{"messages": 0}` parses to a dict and is falsy at `payload.get("error")`,
    so the useful text is the stderr beside it. Without this case the
    `payload.get("error")` condition could become a bare `isinstance` check and
    the detail would read `error=None`.
    """
    detail = mail._failure_detail(
        _Result(2, stdout='{"messages": 0}', stderr="permission denied"))
    assert detail == "stderr=permission denied", detail


def test_silence_on_both_streams_is_named_rather_than_left_blank(st):
    """An empty detail reads as "no reason exists"; this says which.

    A producer killed by SIGKILL prints nothing at all, and the difference
    between "the reason was discarded" and "there was no reason" is the whole
    subject of this file.
    """
    assert mail._failure_detail(_Result(137, stdout="", stderr="")) == (
        "no output on either stream")
    # Whitespace-only is the same case: `.strip()` must reduce it to silence.
    assert mail._failure_detail(_Result(137, stdout="  \n", stderr="\t")) == (
        "no output on either stream")


def test_the_stderr_fallback_reaches_the_log_through_refresh(monkeypatch, tmp_path,
                                                             st, caplog):
    """End to end: a stderr-only failure is logged, and the clock is not bumped.

    The unit tests above assert the return value. This one proves `refresh()`
    actually puts it in the log line beside the exit code, which is the fact an
    operator reads.
    """
    with caplog.at_level(logging.WARNING):
        _run(monkeypatch, tmp_path, st,
             _Result(2, stdout="", stderr="TransportError: no route to host"))
    assert "producer exited 2" in caplog.text, caplog.text
    assert "TransportError: no route to host" in caplog.text, (
        f"the only available reason was discarded: {caplog.text}")
    assert st.data_time("inbox") is None, "a failed fetch advanced the clock"


def test_a_long_stderr_is_truncated_at_five_hundred_characters(st):
    """The case ON the bound. A producer can dump a megabyte of traceback into
    the daemon log, so the cap matters; without a case at the edge it could
    become 50, or vanish, unnoticed."""
    detail = mail._failure_detail(_Result(2, stdout="", stderr="e" * 900))
    assert detail == "stderr=" + "e" * 500, len(detail)
    # Exactly at the cap, nothing is dropped.
    assert mail._failure_detail(_Result(2, stdout="", stderr="e" * 500)) == (
        "stderr=" + "e" * 500)


def test_a_long_structured_detail_is_truncated_at_two_hundred_characters(st):
    """The structured branch has its own, tighter bound per field."""
    payload = json.dumps({"error": "exchange_unreachable", "detail": "d" * 400,
                          "hint": "h" * 10})
    detail = mail._failure_detail(_Result(2, stdout=payload))
    assert "detail=" + "d" * 200 in detail, detail
    assert "d" * 201 not in detail, "the 200-character bound did not hold"
    assert "hint=" + "h" * 10 in detail, detail
