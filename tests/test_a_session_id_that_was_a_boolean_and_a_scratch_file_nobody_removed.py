#!/usr/bin/env python3
"""Two writer-side defects found by reading `.claude/state/` rather than code.

Neither was found by a test. They were found by listing the operator's live
state directory on 2026-09-01 and asking what every file in it was doing there.
Both are the same family: an operation that goes wrong leaves a permanent trace
in a directory that is read on every turn, and nothing removes it.

## 1. `checkpoint-True.json` and `checkpoint-3.json`

`session_id()` read `str(candidate).strip()` over whatever the payload held. A
payload of `{"session_id": true}` therefore became the session id `"True"`, and
`{"session_id": 3}` became `"3"`.

Neither is a session, and both are WORSE than falling back. The fallback is a
sentinel that `session_id_is_known` reports as unknown, so callers say they do
not know. `"True"` is reported as a KNOWN id, and every session that ever sends
that value shares one state file, so one session's compaction threshold and
unattended switch silently become another session's.

The evidence was on disk. An audit probe drove the hooks with malformed
payloads that afternoon, and the live `.claude/state/` was left holding:

    checkpoint-True.json    600 bytes, last_compact_at 2026-09-01T09:45:17
    checkpoint-3.json       597 bytes, last_compact_at 2026-09-01T09:45:17

beside the operator's own session state. This is the workspace's most repeated
defect shape: a `.get(key, default)` is not a type check, because the default
fires only on an ABSENT key and a present-but-wrong value passes through.

The pair had to be fixed together. `session_id` and `session_id_is_known` each
decide "is there an id here?", and narrowing only one would have made them
disagree on precisely the inputs that matter: `session_id` falling back to the
shared bucket while `session_id_is_known` reported the id known, so a caller
would name one session's handoff over a bucket every id-less session shares.

## 2. Eight zero-byte `dispatch-rate.json.<pid>.tmp`

`_save_rate_state` in `.claude/hooks/_dispatch.py` stages to a per-process temp
file and `os.replace`s it. Its handler printed the failure and returned, never
removing the staging file. When the filesystem hit 100% that day with eight hook
processes mid-save, `.claude/state/` was left holding eight zero-byte staging
files, all stamped 12:05.

Nothing would ever have removed them. The pid is IN the name, so no later run
reuses that path deliberately, and this directory is read on every Write and
every Edit. That is unbounded growth in a hot path, from an error handler that
reported the failure it had just made permanent.

## What was measured before the fix

Both files were put back to their previous revision for the length of one run:

    7 failed, 19 passed in 1.17s

with the staging-file case reporting the orphan by name:

    a failed save left ['dispatch-rate.json.1212961.tmp'] behind

`test_the_two_functions_that_decide_this_agree` is in the 19 that passed, and it
is supposed to be. It asks whether the pair AGREES, and before the fix they
agreed on being wrong together. It is a ratchet against a future fix landing in
one of the two, not a binding of this one, and it says so at its own docstring
rather than being counted as coverage it does not provide.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


# ------------------------------------------------------------- the session id

# Every JSON type a payload field can hold that is NOT a string. `True` and `3`
# are first because those two are the ones found on disk.
NOT_A_SESSION_ID = [True, 3, False, 0, [], {}, ["a"], {"id": "x"}, 1.5, None]


@pytest.mark.parametrize("value", NOT_A_SESSION_ID)
def test_a_non_string_session_id_falls_back_instead_of_becoming_one(
        value, monkeypatch):
    """`str(True)` is not an id. Falling back is the designed behaviour."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    got = CP.session_id({"session_id": value})

    assert got == CP.FALLBACK_SESSION_ID, (
        f"a session_id of {value!r} produced the id {got!r}. Every session "
        f"sending that value would share one state file, so one session's "
        f"compaction threshold and unattended switch become another's.")


@pytest.mark.parametrize("value", NOT_A_SESSION_ID)
def test_the_two_functions_that_decide_this_agree(value, monkeypatch):
    """A narrowing that lands in one of the pair is worse than neither.

    `session_id` falling back while `session_id_is_known` says the id is known
    means a caller prints a sentence naming one session's handoff over a bucket
    that every id-less session shares.

    **This test passed before the fix too, and that is correct.** It asks
    whether the pair agrees, and before the fix they agreed on being wrong
    together. It binds nothing about the current behaviour: it is a ratchet
    against a LATER fix landing in one of the two, which is this workspace's
    single most repeated defect shape. Do not read its green as evidence that
    the type narrowing is present; the parametrized test above is what asks
    that.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    payload = {"session_id": value}

    fell_back = CP.session_id(payload) == CP.FALLBACK_SESSION_ID
    claims_known = CP.session_id_is_known(payload)

    assert fell_back is not claims_known, (
        f"for {value!r}: session_id fell back = {fell_back}, "
        f"session_id_is_known = {claims_known}. These two must never disagree.")


def test_a_real_string_id_is_still_used(monkeypatch):
    """The positive anchor. Without it every test above is satisfied by a
    function that returns the sentinel unconditionally."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    assert CP.session_id({"session_id": "2ab651eb-8513"}) == "2ab651eb-8513"
    assert CP.session_id_is_known({"session_id": "2ab651eb-8513"}) is True
    # Whitespace-only is not an id either, and never was.
    assert CP.session_id({"session_id": "   "}) == CP.FALLBACK_SESSION_ID


def test_the_environment_leg_is_narrowed_too(monkeypatch):
    """`os.environ` can only hold strings, so this leg cannot carry a boolean.

    Stated rather than left implicit: the guard above is about the PAYLOAD, and
    a reader who assumed both legs were equally exposed would go looking for a
    defect that cannot exist. What the environment leg CAN hold is an empty or
    whitespace-only value, and that is what is checked here.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "   ")
    assert CP.session_id({}) == CP.FALLBACK_SESSION_ID
    assert CP.session_id_is_known({}) is False

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "from-the-env")
    assert CP.session_id({}) == "from-the-env"


def test_no_state_file_is_named_for_a_non_string_id(monkeypatch, tmp_path):
    """End to end, through the path builder, which is where the file appears."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    path = CP.state_path(tmp_path, CP.session_slug({"session_id": True}))

    assert path.name == "checkpoint-session.json", (
        f"the payload produced {path.name}. A file called checkpoint-True.json "
        f"was found in the operator's live state directory on 2026-09-01.")


# ------------------------------------------------------- the staging file

def test_a_failed_rate_state_save_removes_its_staging_file(tmp_path,
                                                           monkeypatch):
    """The headline for the second defect.

    Driven through a real `_save_rate_state` with the write made to fail the way
    a full disk makes it fail. Before the fix the handler printed and returned,
    and the zero-byte staging file stayed on disk for good.
    """
    dispatch = _load_dispatch(monkeypatch, tmp_path)
    state_file = tmp_path / "dispatch-rate.json"
    monkeypatch.setattr(dispatch, "RATE_LIMIT_STATE_FILE", state_file)

    def full_disk(self, *args, **kwargs):
        # Create the file first, exactly as a real write does, THEN fail. A stub
        # that fails before creating anything would find no orphan and pass
        # against the unfixed code.
        Path(self).write_bytes(b"")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", full_disk)

    dispatch._save_rate_state({"date": "2026-09-01", "count": 1, "recent": []})

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, (
        f"a failed save left {leftovers} behind. The pid is in the name, so no "
        f"later run reuses that path, and this directory is read on every "
        f"Write and Edit.")


def test_a_successful_rate_state_save_leaves_nothing_behind(tmp_path,
                                                            monkeypatch):
    """The positive anchor: the happy path still writes, and still tidies up."""
    dispatch = _load_dispatch(monkeypatch, tmp_path)
    state_file = tmp_path / "dispatch-rate.json"
    monkeypatch.setattr(dispatch, "RATE_LIMIT_STATE_FILE", state_file)

    dispatch._save_rate_state({"date": "2026-09-01", "count": 7, "recent": []})

    assert json.loads(state_file.read_text(encoding="utf-8"))["count"] == 7
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_the_staging_name_still_carries_the_pid(tmp_path, monkeypatch):
    """The property the cleanup rests on, and the reason the orphans persisted.

    Two hook processes racing on one fixed `.json.tmp` interleaved their writes
    and promoted torn JSON, which is why the pid went into the name. That fix is
    what made an orphan permanent, so this test exists to stop a later reader
    removing the pid as "the cause" of the litter. The pid stays; the cleanup is
    what was missing.
    """
    source = (ROOT / ".claude" / "hooks" / "_dispatch.py").read_text(
        encoding="utf-8")
    assert 'f".json.{os.getpid()}.tmp"' in source, (
        "the staging name no longer carries the pid, so two concurrent hook "
        "processes share one staging path again")


def _load_dispatch(monkeypatch, tmp_path):
    """Import `.claude/hooks/_dispatch.py` under a private module name.

    Loaded by PATH rather than by package name: the file is not importable as a
    module (`_dispatch` under a dotted `.claude` directory), and binding a
    package name for it would be the startup-hook defect this workspace already
    records.
    """
    import importlib.util
    path = ROOT / ".claude" / "hooks" / "_dispatch.py"
    spec = importlib.util.spec_from_file_location("_coord_dispatch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
