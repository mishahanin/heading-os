#!/usr/bin/env python3
"""Half of "corrupt" reached the corporate wall. The other half fell open.

`.claude/hooks/_dispatch.py::check_protect_corporate` stops an executive editing
shared corporate content that the next sync would overwrite. It decides from
`.workspace-identity.json`, and the paragraph in that function already records
the defect it was written to close: a PRESENT identity file the hook could not
parse used to return None, which means allow, so one corrupt byte switched the
wall off for the whole session and the edit was silently overwritten later.

That fix caught `json.JSONDecodeError` and missed `UnicodeDecodeError`.
`UnicodeDecodeError` is a `ValueError` and a SIBLING of `json.JSONDecodeError`,
not a subclass, because the decode fails inside `read_text` before `json.loads`
is ever called.

MEASURED 2026-09-01 by driving the real hook as a subprocess with a real stdin
payload, one identity file in four states, the same Write into `corporate/`:

    valid, role exec  -> allowed    allowed     (unchanged, the normal path)
    valid, role ceo   -> allowed    allowed     (unchanged)
    not JSON          -> BLOCKED    BLOCKED     (unchanged, already correct)
    not UTF-8         -> ALLOWED    BLOCKED     (the defect, and the fix)

Only the last row moved. The third row is the control: without it, a hook that
blocked everything would satisfy the fix and this file would not notice.

Driven through the process boundary on purpose. An in-process call would not
establish that the hook, as the harness actually launches it, reaches this
branch and emits a decision on stdout.

The wider lesson, and the reason this file exists rather than a one-line diff:
fixing a handler means asking which OTHER inputs reach it, not only the one that
prompted the fix. The same shape was found three times in one day, in
`scripts/turn-check.py` (a sentinel rendered in words by the producer and raw by
the consumer), in `scripts/workspace-health.py` (one read fixed, four siblings
in the same file left), and here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"

# A lone 0xe9 inside otherwise well-formed JSON. Valid latin-1, invalid UTF-8.
NOT_UTF8 = b'{"role": "exec", "slug": "some-exec", "note": "caf\xe9"}'
NOT_JSON = "{ this is not json at all"

RUNNER = ("import sys, runpy; sys.argv = [sys.argv[1]]; "
          "runpy.run_path(sys.argv[0], run_name='__main__')")


def _run_hook(workspace: Path) -> tuple[int, str]:
    """Drive the hook exactly as the harness does: subprocess, JSON on stdin."""
    target = workspace / "corporate" / "shared-note.md"
    payload = {
        "tool_name": "Write",
        "cwd": str(workspace),
        "tool_input": {"file_path": str(target), "content": "an edit"},
    }
    proc = subprocess.run(
        [sys.executable, "-c", RUNNER, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
        errors="replace", cwd=str(workspace), timeout=120,
    )
    return proc.returncode, proc.stdout


def _blocked(stdout: str) -> bool:
    lowered = stdout.lower()
    return "deny" in lowered or "block" in lowered


@pytest.fixture()
def exec_workspace(tmp_path):
    """An executive clone with a corporate layer and a shared file in it."""
    target = tmp_path / "corporate" / "shared-note.md"
    target.parent.mkdir(parents=True)
    target.write_text("shared content\n", encoding="utf-8")
    return tmp_path


def test_an_identity_file_that_is_not_utf8_blocks_the_write(exec_workspace):
    """The defect. One byte must not switch the wall off.

    Before 2026-09-01 this returned no decision at all: the hook printed the
    decode error to stderr and the write went through.
    """
    (exec_workspace / ".workspace-identity.json").write_bytes(NOT_UTF8)
    code, out = _run_hook(exec_workspace)
    assert code == 0, f"the hook exited {code} instead of emitting a decision"
    assert _blocked(out), (
        "an identity file that is not valid UTF-8 let the write through. That "
        "is the corporate wall switched off by one byte, which is the exact "
        f"outcome the function's own comment says was fixed. stdout: {out!r}")


def test_an_identity_file_that_is_not_json_still_blocks(exec_workspace):
    """The control, and a regression guard on the half that already worked.

    Without this, a change that broke the JSON path while fixing the decode
    path would pass the test above and leave the wall half open again.
    """
    (exec_workspace / ".workspace-identity.json").write_text(
        NOT_JSON, encoding="utf-8")
    code, out = _run_hook(exec_workspace)
    assert code == 0
    assert _blocked(out), (
        f"the corrupt-JSON case stopped blocking: {out!r}")


@pytest.mark.parametrize("role", ["exec", "ceo"])
def test_a_readable_identity_behaves_as_it_did_before(exec_workspace, role):
    """Anchor against over-refusal, in both roles.

    A hook that blocked every write would satisfy both tests above while making
    the workspace unusable, and that is how a guard gets switched off for real.
    These two rows were measured before and after the fix and did not move; the
    test asserts only that they still do not, never what the answer should be
    for a given role, because the role policy is not what this file is about.
    """
    (exec_workspace / ".workspace-identity.json").write_text(
        json.dumps({"role": role, "slug": "some-exec"}), encoding="utf-8")
    code, out = _run_hook(exec_workspace)
    assert code == 0
    assert not _blocked(out), (
        f"a readable identity file with role {role!r} is now blocked, so the "
        f"decode fix widened into a blanket refusal: {out!r}")


def test_the_handler_names_the_decode_error(exec_workspace):
    """A second jaw on the source, cheap, and it fails on the exact edit.

    Scoped to the handler line rather than the file, so the comment explaining
    the fix cannot satisfy it.
    """
    src = HOOK.read_text(encoding="utf-8")
    assert "except (OSError, UnicodeDecodeError, json.JSONDecodeError)" in src, (
        "the identity-file handler no longer names UnicodeDecodeError, so an "
        "identity file that is not valid UTF-8 falls past it again and the "
        "corporate wall opens")
