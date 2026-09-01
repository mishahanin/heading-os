"""Unit tests for the mark-read finalizer (Exchange read-state write-back).

Three of this file's claims were not measured until 2026-08-31.

**The validation cases were a straw man.** `test_mark_conversation_read_rejects_bad_conv_id`
never called `_stub_script`, so `workspace_root/scripts/email-intelligence.py`
did not exist and `mark_conversation_read` returned
`{"ok": False, "error": "email-intelligence.py not found"}` for every input it
was handed. `ok is False` was therefore true for an unrelated reason, and both
guards it exists to pin could be deleted outright. MEASURED by removing
`mark_read.py:28-31` (the `conv_id is required` and `conv_id too long`
branches) and running the directory::

    $ .venv/bin/python -m pytest tests/bridge -q
    1312 passed, 1 skipped

Identical to the unmutated baseline. Nothing anywhere refused a blank or a
600-character conversation id. The cases below now stub the producer, patch
`subprocess.run` to a recorder that would report `ok: True`, and assert BOTH
that the child never ran and which error came back, so each guard has a case
that fails without it.

**The `except OSError` branch had no case at all.** The docstring promises
`{ok: False, error: ...}` "on any failure", and a bad interpreter path, ENOEXEC
or EMFILE arrives as OSError from `subprocess.run`. MEASURED by deleting
`mark_read.py:48-49`: `1312 passed, 1 skipped` again. Without a handler that
exception leaves the finalizer and 500s `POST /inbox/dismiss`.

**The result scan's direction was unmeasured.** `reversed(...)` is documented as
"scan from the last line back so any incidental output before it is ignored",
and no test ever supplied more than one line of stdout. MEASURED by dropping
`reversed()`: `1312 passed, 1 skipped`. A producer that prints any JSON object
before its result line would then have that first object returned to the
endpoint as the outcome.
"""
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.bridge_daemon.finalizers.mark_read import mark_conversation_read


def _stub_script(workspace_root):
    """Create a stub email-intelligence.py so the existence check passes."""
    d = workspace_root / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "email-intelligence.py").write_text("# stub", encoding="utf-8")


def test_mark_conversation_read_parses_result(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", return_value=SimpleNamespace(
            stdout='{"ok": true, "conv_id": "c1", "messages_changed": 3}',
            returncode=0)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is True
    assert r["messages_changed"] == 3


@pytest.mark.parametrize("mark_read, flag", [
    (True, "--mark-read"),
    (False, "--mark-unread"),
])
def test_the_flag_matches_the_direction_asked_for(tmp_path, mark_read, flag):
    """The one thing this finalizer computes, and nothing asserted it.

    Every other test here patches `subprocess.run` away and then asserts the
    JSON the stub itself supplied, so the whole file measured its own setup.
    `flag = "--mark-read" if mark_read else "--mark-unread"` is the only real
    line, and `mark_read=False` was never passed: inverting the ternary left the
    bridge tests green while `POST /inbox/undo-dismiss` marked a conversation
    READ instead of unread, so the mail stayed hidden in Outlook with no undo.
    """
    _stub_script(tmp_path)
    seen = []

    def _run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout='{"ok": true, "messages_changed": 1}',
                               returncode=0)

    with patch("subprocess.run", _run):
        mark_conversation_read(tmp_path, " c1 ", mark_read=mark_read)

    assert len(seen) == 1, f"expected one child run, got {len(seen)}"
    argv = seen[0]
    assert flag in argv, f"{flag} missing from {argv}"
    other = "--mark-unread" if flag == "--mark-read" else "--mark-read"
    assert other not in argv, f"both directions were passed: {argv}"
    assert argv[-1] == "c1", "the conversation id is not stripped and last"
    assert argv[1].endswith("email-intelligence.py"), (
        f"the finalizer ran something else: {argv}")


def test_mark_conversation_read_surfaces_producer_error(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", return_value=SimpleNamespace(
            stdout='{"ok": false, "error": "Exchange write failed: boom"}',
            returncode=1)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "boom" in r["error"]


def test_mark_conversation_read_handles_no_output(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", return_value=SimpleNamespace(stdout="", returncode=1)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "no result" in r["error"]


def test_mark_conversation_read_timeout(tmp_path):
    _stub_script(tmp_path)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "timed out" in r["error"]


def test_mark_conversation_read_missing_script(tmp_path):
    """No scripts/email-intelligence.py on disk -> graceful error."""
    r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "not found" in r["error"]


@pytest.mark.parametrize("conv_id, expected_error", [
    ("", "conv_id is required"),
    ("   ", "conv_id is required"),
    ("\t\n", "conv_id is required"),
    (None, "conv_id is required"),
    (12345, "conv_id is required"),
    ("x" * 501, "conv_id too long"),
    ("x" * 600, "conv_id too long"),
])
def test_a_rejected_conv_id_is_rejected_by_the_guard_that_names_it(
        tmp_path, conv_id, expected_error):
    """Each guard gets a case that fails without it.

    The producer EXISTS here and `subprocess.run` is a recorder that answers
    `ok: True`, so the only way to get `ok: False` is for validation to refuse
    before the child is reached. Under the old file neither was true: the
    script was absent, so "not found" answered every input and both guards were
    free-floating. Asserting the error TEXT is what ties a case to its branch -
    a blank id that came back "too long" would be a guard firing for the wrong
    reason, and that would now fail.
    """
    _stub_script(tmp_path)
    seen = []

    def _run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout='{"ok": true, "messages_changed": 1}',
                               returncode=0)

    with patch("subprocess.run", _run):
        r = mark_conversation_read(tmp_path, conv_id, mark_read=True)

    assert not seen, f"a refused conv_id still reached the producer: {seen}"
    assert r["ok"] is False, r
    assert r["error"] == expected_error, r


def test_the_length_bound_admits_the_longest_legal_id(tmp_path):
    """The case ON the line, so the bound cannot drift inward unnoticed.

    500 is legal and 501 is not. Without this, `len(conv_id) > 500` could
    become `>= 500`, or 400, or 5, and every rejection case above would still
    pass.
    """
    _stub_script(tmp_path)
    seen = []

    def _run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout='{"ok": true, "messages_changed": 1}',
                               returncode=0)

    legal = "x" * 500
    with patch("subprocess.run", _run):
        r = mark_conversation_read(tmp_path, legal, mark_read=True)

    assert r["ok"] is True, r
    assert len(seen) == 1, f"the longest legal id was refused: {seen}"
    assert seen[0][-1] == legal


def test_an_oserror_from_the_child_becomes_a_result_not_a_traceback(tmp_path):
    """The `except OSError` branch, which had no case at all.

    `subprocess.run` raises OSError for a missing or non-executable
    interpreter, ENOEXEC, and EMFILE. The finalizer's docstring promises a
    result dict "on any failure"; without the handler the exception leaves the
    function and 500s `POST /inbox/dismiss`, so the dashboard's Done action
    fails with no explanation instead of reporting one.
    """
    _stub_script(tmp_path)
    with patch("subprocess.run", side_effect=OSError("Exec format error")):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["ok"] is False
    assert "subprocess failed" in r["error"], r
    assert "Exec format error" in r["error"], r


def test_the_result_is_read_from_the_last_json_line_not_the_first(tmp_path):
    """`reversed()` is the documented contract and nothing measured it.

    Every other test here supplies exactly one line of stdout, which scores the
    same forwards and backwards. The producer is a script that may log before it
    reports, so a JSON object printed ahead of the result must not be mistaken
    for the result.
    """
    _stub_script(tmp_path)
    noisy = (
        '{"stage": "connecting", "ok": true, "messages_changed": 999}\n'
        'not json at all\n'
        '{"ok": true, "conv_id": "c1", "messages_changed": 4}\n'
    )
    with patch("subprocess.run", return_value=SimpleNamespace(
            stdout=noisy, returncode=0)):
        r = mark_conversation_read(tmp_path, "c1", mark_read=True)
    assert r["messages_changed"] == 4, (
        f"the scan returned a line printed BEFORE the result: {r}")
    assert "stage" not in r, r
