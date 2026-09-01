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

import ast
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


# --- the FIELDS inside the payload, not only the payload's own shape ---------
#
# Everything above varies the top-level payload. It cannot see the defect one
# layer down: a payload that IS a dict, carrying a field that is not the type the
# hook assumes. `.get(key, default)` is not a type check - the default fires only
# on an ABSENT key, and a present-but-wrong value passes straight through.
#
# `.claude/hooks/session-start.py` died exactly there on 2026-09-01
# (`input_data.get("cwd", os.getcwd())` handed a `null` to `Path()`), and every
# alert that hook computes was lost while the session opened looking normal.
# `tests/test_a_session_start_that_died_on_a_field_it_did_not_check.py` holds
# that one hook and that one field. This is the sweep it does not reach.
#
# Driving all 17 stdin hooks against 13 harness field names and 6 JSON shapes,
# 1547 runs, 2026-09-01, found two more and no others:
#
#     checkpoint-inject.py  source     3 / True  AttributeError: no attribute 'strip'
#     data-path-redirect.py tool_name  [] / {}   TypeError: unhashable type
#
# Both are the same shape as the session-start one and both sit in a file that
# had ALREADY written the identical guard for its neighbour: checkpoint-inject
# guards the payload container and not the field in it, and data-path-redirect
# type-checks `tool_input` on the line directly above the `tool_name` that
# raised. A fix that landed in one of its copies.

# What the harness fills in. A key read from a config dict or a JSON record is
# not an externally supplied payload field, so the derivation below is
# intersected with this set rather than testing every string key in the file.
_HARNESS_FIELDS = frozenset({
    "cwd", "session_id", "transcript_path", "hook_event_name", "tool_name",
    "tool_input", "tool_response", "prompt", "source", "trigger",
    "stop_hook_active", "permission_mode", "message",
})

# `None` and `""` are omitted deliberately: both are falsy, so `or`-defaulting
# and `.get`-defaulting already absorb them, and the probe above confirmed no
# hook fails on either. These four are the shapes that actually crashed. `True`
# is kept beside `3` even though `bool` subclasses `int`, because a hook may
# branch on truthiness and reach a different line.
BAD_FIELD_VALUES = [3, [], {}, True]


def _string_keys(src: str, path: Path) -> set[str]:
    """Every constant string key the source reads, asked of the AST.

    AST rather than a regex: `payload.get("source")` and `payload["source"]` are
    both reads, a regex over one is blind to the other, and neither is visible
    through a line wrap.
    """
    tree = ast.parse(src, filename=str(path))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
        if (isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return keys


def _helper_modules(src: str, path: Path) -> list[Path]:
    """The repo-local `scripts.utils` modules this hook imports.

    Load-bearing, not thoroughness. `checkpoint-save.py` reads NO harness field
    by name; it hands the whole payload to `scripts/utils/checkpoint_paths.py`,
    which reads `session_id` and `cwd`. Deriving from the hook file alone would
    have declared that hook - the one whose docstring calls its loss "the one
    loss nobody can undo" - to have no fields worth testing.
    """
    tree = ast.parse(src, filename=str(path))
    mods: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("scripts.utils"):
            continue
        base = ROOT / Path(node.module.replace(".", "/"))
        for candidate in [base.with_suffix(".py")] + [
                base / f"{alias.name}.py" for alias in node.names]:
            if candidate.is_file():
                mods.append(candidate)
    return mods


def _fields_read_by(hook: Path) -> list[str]:
    src = hook.read_text(encoding="utf-8")
    keys = _string_keys(src, hook)
    for helper in _helper_modules(src, hook):
        keys |= _string_keys(helper.read_text(encoding="utf-8"), helper)
    return sorted(keys & _HARNESS_FIELDS)


def _field_cases() -> list[tuple[Path, str, object]]:
    return [(hook, field, value)
            for hook in _stdin_hooks()
            for field in _fields_read_by(hook)
            for value in BAD_FIELD_VALUES]


def _well_formed(root: Path, scratch: Path) -> dict:
    """A payload every field of which is the type the harness really sends.

    This has to be RIGHT, not merely present. A field is only exercised if the
    hook reaches the line that reads it, and a hook that returns at its first
    guard clause because `transcript_path` names nothing measures nothing at all.
    """
    transcript = scratch / "transcript.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"}}\n',
        encoding="utf-8")
    target = scratch / "a-written-file.md"
    target.write_text("# scratch\n", encoding="utf-8")
    return {
        "cwd": str(root),
        "session_id": "sweep-session",
        "transcript_path": str(transcript),
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "# scratch\n"},
        "tool_response": {},
        "prompt": "a prompt long enough to clear the recall length heuristic",
        "source": "startup",
        "trigger": "manual",
        "stop_hook_active": False,
        "permission_mode": "default",
        "message": "",
    }


@pytest.mark.slow
@pytest.mark.parametrize(
    ("hook", "field", "value"), _field_cases(),
    ids=lambda v: v.name if isinstance(v, Path) else f"{v!r}")
def test_a_field_the_hook_reads_may_hold_any_json_value(hook, field, value, tmp_path):
    """The payload is a dict and well formed; ONE field holds another JSON type.

    A hook may refuse such a field, ignore it, or default it. It may not die on
    it: the work every one of these hooks exists to do is below the line that
    reads the field.
    """
    import json as _json
    env, _ = _scratch_env(tmp_path)
    payload = _well_formed(ROOT, tmp_path)
    payload[field] = value
    proc = subprocess.run(
        [sys.executable, str(hook), *_argv_for(hook)],
        input=_json.dumps(payload), capture_output=True, text=True,
        timeout=240, env=env,
    )
    _assert_no_crash(hook, proc, f"{field}={value!r}")


@pytest.mark.slow
@pytest.mark.parametrize("hook", _stdin_hooks(), ids=lambda p: p.name)
def test_the_well_formed_payload_is_itself_accepted(hook, tmp_path):
    """The control. Without it the sweep above proves nothing.

    A base payload that every hook rejects at its first guard clause would make
    every case green while reaching no field at all. This asserts the untouched
    payload runs clean, so a green case above means the hook got past the door.
    """
    import json as _json
    env, _ = _scratch_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(hook), *_argv_for(hook)],
        input=_json.dumps(_well_formed(ROOT, tmp_path)),
        capture_output=True, text=True, timeout=240, env=env,
    )
    _assert_no_crash(hook, proc, "the well-formed control payload")


def test_the_field_derivation_found_a_real_matrix():
    """A derivation that returns nothing turns the sweep green over zero work.

    Floored on the MEASURED count, not on `>= 1`. The 2026-09-01 sweep derived
    44 (hook, field) pairs across the 17 stdin hooks; a change that halves that
    is a derivation that has stopped seeing reads, not a tree that got simpler.
    """
    pairs = {(hook.name, field)
             for hook in _stdin_hooks() for field in _fields_read_by(hook)}
    assert len(pairs) >= 40, f"only derived {len(pairs)} (hook, field) pairs: {sorted(pairs)}"

    # The three hooks whose field defects were measured, each named with the
    # field that crashed it. If the derivation stops seeing one of these, the
    # sweep silently stops covering the regression it was written for.
    for hook_name, field in (("session-start.py", "cwd"),
                             ("checkpoint-inject.py", "source"),
                             ("data-path-redirect.py", "tool_name")):
        if (HOOKS / hook_name).exists():
            assert (hook_name, field) in pairs, (
                f"{hook_name} reads {field!r} and the derivation missed it")

    # checkpoint-save reads its fields only through scripts/utils/checkpoint_paths,
    # so this pins the helper-following in _helper_modules rather than trusting it.
    if (HOOKS / "checkpoint-save.py").exists():
        assert ("checkpoint-save.py", "session_id") in pairs, (
            "the derivation stopped following hooks into their helper modules")


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
