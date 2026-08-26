"""No hook may crash on a payload that is valid JSON and not an object.

Every hook here reads its payload as `json.load(sys.stdin)` and then calls
`.get` on the result. `[]`, `"x"`, `3` and `null` are all valid JSON. None of
them has `.get`, so each raises an uncaught `AttributeError` and the hook dies
with a traceback.

`.claude/hooks/checkpoint-inject.py` found and fixed this on 2026-08-20, with
the measurement in its own comment. The fix stopped there. The 2026-08-23 audit
found three more by reading; sweeping every stdin hook against all four shapes
found TEN:

    bridge-hook, checkpoint-offer, checkpoint-save, memory-reconcile,
    post-write-sanitize, prompt-guard, session-start, sync-docs, turn-check,
    unattended-resume

`checkpoint-save` was the worst of them. It runs after the session's context has
been discarded, which its own docstring calls "the one loss nobody can undo", and
it exited 1 having written no archive, no quarantine, no pointer and no
systemMessage.

That is why this is a SWEEP and not ten individual tests. The defect is not any
one hook; it is that a hook can be added without anyone remembering the shape.
A new hook that reads stdin is picked up here automatically and fails until it
is guarded.

What "survives" means here is narrow and deliberate: no traceback. A hook may
still exit non-zero, and several correctly do, because a missing `session_id`
is a real refusal. The line is between deciding and crashing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"

# Valid JSON, no `.get`. `null` is included because `json.load` returns None for
# it, and `None.get` fails the same way with a different exception type.
MALFORMED = ['[]', '"x"', '3', 'null', '[{"tool_name": "Bash"}]']

# Any read of stdin, not just the inline `json.load(sys.stdin)` shape.
# checkpoint-inject.py does `raw = sys.stdin.read()` on one line and parses on
# the next, so the narrower pattern missed the ONE hook that had already fixed
# this defect — a detector blind to the reference implementation would be blind
# to the next hook written the same way.
_READS_STDIN = re.compile(r"sys\.stdin\b")


def _stdin_hooks() -> list[Path]:
    return sorted(p for p in HOOKS.glob("*.py")
                  if _READS_STDIN.search(p.read_text(encoding="utf-8")))


def _argv_for(hook: Path) -> list[str]:
    """bridge-hook dispatches on argv[1]; without one it prints usage and never
    reaches the payload, which would make this sweep pass on nothing."""
    if hook.name == "bridge-hook.py":
        return ["session-start"]
    return []


def _assert_no_crash(hook: Path, proc, what: str) -> None:
    """Every way the run can be a crash, not only the one that prints a traceback.

    "Traceback" alone was the whole check, and it cannot see two real failures:
    a hook killed by a signal writes nothing to stderr, and an interpreter that
    cannot open the file at all prints `can't open file` with no traceback under
    it. Both leave the negative assertion satisfied. A hook exiting non-zero is
    NOT checked here on purpose - a blocking hook returns 2 by design, so a
    return-code equality would fail the well-behaved ones.
    """
    tail = proc.stderr[-1500:]
    assert proc.returncode >= 0, (
        f"{hook.name} was killed by signal {-proc.returncode} on {what}: {tail}")
    assert "can't open file" not in proc.stderr, (
        f"{hook.name} never started on {what}: {tail}")
    assert "Traceback" not in proc.stderr, (
        f"{hook.name} crashed on {what}:\n{tail}")


def _scratch_env(tmp_path):
    """Child env with the data root pointed at scratch.

    Every hook here is launched as a child process, and a child resolves where
    it writes through `get_data_root()`, which reads HEADING_OS_DATA. Without
    this, `checkpoint-save.py` wrote a REAL handoff into the operator's overlay
    on every parametrised case: five per run of this file, and 1107 archives
    named `..._handoff_compact-unknown_session.md` had accumulated there by
    2026-08-27. The shared `.latest/` pointer pair, which `/next` reads, was
    pointing at one of them.

    A per-test cleanup was the old answer and it only ever covered one test.
    Redirecting the root covers every hook in the sweep, including the ones
    nobody has written yet.
    """
    overlay = tmp_path / "data-root"
    overlay.mkdir(exist_ok=True)
    return dict(os.environ, HEADING_OS_DATA=str(overlay)), overlay


@pytest.mark.parametrize("hook", _stdin_hooks(), ids=lambda p: p.name)
@pytest.mark.parametrize("payload", MALFORMED)
def test_a_non_object_payload_does_not_crash_the_hook(hook, payload, tmp_path):
    env, _ = _scratch_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(hook), *_argv_for(hook)],
        input=payload, capture_output=True, text=True, timeout=120, env=env,
    )
    _assert_no_crash(hook, proc, f"the payload {payload}")


@pytest.mark.parametrize("hook", _stdin_hooks(), ids=lambda p: p.name)
def test_an_empty_payload_does_not_crash_the_hook(hook, tmp_path):
    """The neighbouring shape: nothing on stdin at all."""
    env, _ = _scratch_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(hook), *_argv_for(hook)],
        input="", capture_output=True, text=True, timeout=120, env=env,
    )
    _assert_no_crash(hook, proc, "an empty payload")


def test_the_sweep_actually_found_the_hooks():
    """A regex that matches nothing turns this whole file green on zero work."""
    found = _stdin_hooks()
    assert len(found) >= 12, f"only found {[p.name for p in found]}"
    names = {p.name for p in found}
    # The four the audit named, plus the one that had already been fixed and is
    # the reference for the rest.
    for expected in ("checkpoint-save.py", "session-start.py",
                     "post-write-sanitize.py", "bridge-hook.py",
                     "checkpoint-inject.py"):
        assert expected in names or not (HOOKS / expected).exists(), (
            f"{expected} reads stdin but the detector missed it"
        )


def test_checkpoint_save_still_writes_its_handoff_on_a_bad_payload(tmp_path):
    """Not crashing is not enough for this one. Its entire reason to exist is
    that the handoff reaches disk; degrading to silence would satisfy the sweep
    above while losing exactly what the file protects.

    The write is now checked ON DISK, in a scratch overlay. It used to be
    checked by looking for the word `systemMessage` in stdout and then deleting
    whatever file the message named - a cleanup that ran in the operator's real
    archive, that returned early on two paths without deleting anything, and
    that said nothing about whether the file existed in the first place.
    """
    env, overlay = _scratch_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "checkpoint-save.py")],
        input="[]", capture_output=True, text=True, timeout=120, env=env,
    )
    assert "Traceback" not in proc.stderr
    assert "systemMessage" in proc.stdout, (
        "checkpoint-save produced no systemMessage on a malformed payload, so "
        f"the operator has no sign the handoff was saved: {proc.stdout!r}"
    )

    import json as _json
    message = _json.loads(proc.stdout).get("systemMessage", "")
    match = re.search(r"(outputs/operations/handoff-archive/\S+\.md)", message)
    assert match, f"the message names no archive path: {message!r}"
    written = overlay / match.group(1)
    assert written.is_file(), (
        f"the hook announced {match.group(1)} and wrote nothing there. Either "
        f"the announcement is false, or the file went outside {overlay}."
    )
    assert written.read_text(encoding="utf-8").strip(), "the archive is empty"
