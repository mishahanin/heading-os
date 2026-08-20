#!/usr/bin/env python3
"""The operator's instruction resumes a paused stretch when he sends it.

`clear_unattended_window` is documented as "the operator's next instruction
clears it", and `--done` prints the same promise. Until 2026-08-20 the clearing
happened at the next STOP - the END of the turn his instruction opened - so for
that whole turn the state still carried the pause and the status bar rendered
`unattended paused` while the assistant worked under a stretch his message had
already resumed. He reported it twice, holding a screenshot the second time.

`.claude/hooks/unattended-resume.py` moves the clear to prompt submission, which
is both the promised moment and the accurate one: the window belongs to the
operator's turn, and the turn begins when he presses Enter.

The Stop hook keeps its own `prompt_id` comparison. Not redundancy to delete -
this hook is not registered in every clone, and a stretch resumable only from
here would strand itself wherever it is missing.
"""
from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOK = ROOT / ".claude" / "hooks" / "unattended-resume.py"
SESSION = "8c765efb-81ea-4397-a80c-620d7b9fc4c3"
SLUG = "8c765efb-81ea-4397-a80c-620d7b9f"

PAUSED = {
    "session_unattended": True,
    "session_auto": True,
    "unattended_done_at": "2026-08-20T09:00:00+00:00",
    "unattended_done_note": "the plan is finished",
    "unattended_continuations": 4,
    "used_percentage": 41.0,
}


def _run(tmp: Path, prompt: str, state: dict | None = None):
    if state is not None:
        (tmp / ".claude" / "state").mkdir(parents=True, exist_ok=True)
        (tmp / ".claude" / "state" / f"checkpoint-{SLUG}.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(HOOK)],
        input=json.dumps({
            "session_id": SESSION, "prompt": prompt, "cwd": str(tmp),
            "hook_event_name": "UserPromptSubmit",
        }, ensure_ascii=False),
        capture_output=True, text=True, cwd=str(tmp), timeout=60,
    )
    after = tmp / ".claude" / "state" / f"checkpoint-{SLUG}.json"
    written = json.loads(after.read_text(encoding="utf-8")) if after.exists() else {}
    return proc, written


def _assert_silent(proc, why: str) -> None:
    """Silence is empty stdout, never "no error". A hook that crashed and one
    that correctly said nothing both exit without printing a decision."""
    assert proc.returncode == 0, f"{why}: exited {proc.returncode}\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, f"{why}: crashed\n{proc.stderr}"
    assert proc.stdout == "", f"{why}: spoke into the operator's prompt:\n{proc.stdout}"


def test_an_instruction_clears_the_pause(tmp_path):
    proc, state = _run(tmp_path, "Мирим, продолжай", dict(PAUSED))
    _assert_silent(proc, "clearing a pause")
    assert not state.get("unattended_done_at"), (
        "the pause survived the operator's instruction, so the bar keeps "
        "reading `unattended paused` through the turn it opened"
    )
    assert not state.get("unattended_continuations"), "the ceiling did not reset"


def test_the_switch_is_never_lowered(tmp_path):
    """The switch is the operator's. Clearing a WINDOW must not touch the MODE."""
    proc, state = _run(tmp_path, "keep going", dict(PAUSED))
    _assert_silent(proc, "clearing a pause")
    assert state.get("session_unattended") is True
    assert state.get("session_auto") is True


def test_our_own_compact_submission_does_not_open_a_window(tmp_path):
    """The Stop hook drives compaction by queueing the literal `/compact`, and
    the harness delivers it here as an ordinary prompt. Counting it as the
    operator speaking would reset the continuation counter at every compaction
    and retire the ceiling - the one bound with no backstop behind it."""
    proc, state = _run(tmp_path, "/compact", dict(PAUSED))
    _assert_silent(proc, "seeing our own submission")
    assert state.get("unattended_done_at") == PAUSED["unattended_done_at"]
    assert state.get("unattended_continuations") == 4


def test_a_session_with_no_pause_writes_nothing(tmp_path):
    """The common path. Every prompt in every session runs this hook, so the
    no-op case must not touch the disk."""
    running = {"session_unattended": True, "unattended_continuations": 2}
    proc, state = _run(tmp_path, "hello", dict(running))
    _assert_silent(proc, "a running stretch")
    assert state == running, f"the hook rewrote a state it had nothing to change: {state}"


def test_a_session_with_no_state_file_is_silent(tmp_path):
    (tmp_path / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    proc, state = _run(tmp_path, "hello")
    _assert_silent(proc, "no state file")
    assert state == {}, "the hook created a state file for a session that has none"


def test_a_malformed_payload_is_silent(tmp_path):
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(HOOK)], input="not json{",
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )
    _assert_silent(proc, "malformed payload")


def _user_prompt_commands(settings_file: Path) -> list[str]:
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    return [
        entry.get("command", "")
        for group in settings["hooks"].get("UserPromptSubmit", [])
        for entry in group["hooks"]
    ]


def test_the_hook_is_registered_in_every_shipped_template():
    """A hook that exists and is not registered does nothing at all, and looks
    exactly like one that works.

    The TEMPLATES are what this asserts, not the live file. `settings.local.json`
    is gitignored and machine-local: registering only there passes on the machine
    that made the change and ships the hook dead to every clone, which is the
    failure mode `docs/HOOKS-REFERENCE.md` warns about in its own closing note.
    """
    for platform in ("linux", "macos", "windows"):
        path = ROOT / ".claude" / f"settings.local.{platform}.json"
        assert any("unattended-resume.py" in c for c in _user_prompt_commands(path)), (
            f"unattended-resume.py is missing from the {platform} template, so a "
            f"fresh clone would resume a paused stretch only at the next Stop"
        )


def test_the_hook_is_registered_here():
    """The live file, checked separately and skipped when absent, because a
    machine-local file cannot speak for the repository."""
    import pytest

    live = ROOT / ".claude" / "settings.local.json"
    if not live.is_file():
        pytest.skip("no live settings.local.json in this checkout")
    assert any("unattended-resume.py" in c for c in _user_prompt_commands(live)), (
        "the templates carry the hook but this machine's live settings do not; "
        "merge the UserPromptSubmit block per docs/HOOKS-REFERENCE.md"
    )
