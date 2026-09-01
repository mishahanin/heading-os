#!/usr/bin/env python3
"""Four processes write one state file. `locked_state` makes each one indivisible.

`write_json_atomic` makes every WRITE indivisible. That is a different guarantee
from making a read and the write that follows it indivisible, and the checkpoint
mechanism needs the second one: the statusline reads the whole state dict at the
top of a render and writes the whole dict back at the bottom, while the Stop
hook, the PostCompact hook and `scripts/checkpoint-paths.py` all write the same
file from other processes.

Measured 2026-08-20: the statusline's exposed span is 0.814 ms median, 3.686 ms
worst, and 1 of 60 forced-overlap trials lost a concurrent
`--unattended on` - the operator's switch went nowhere while the CLI printed
`unattended=on`.

The tests below reproduce the loss deterministically by widening that span with
a sleep inside the block, rather than by racing real processes and hoping. A
race test that only fails sometimes is a race test nobody trusts.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


def test_a_concurrent_writer_is_not_lost(tmp_path):
    """The defect, reproduced and then closed.

    Writer A reads, holds for 100 ms, then writes its own key. Writer B sets a
    different key inside that window. Without the lock B's key is gone, because
    A writes back the dict it read before B existed.
    """
    path = tmp_path / "checkpoint-x.json"
    CP.write_json_atomic(path, {"seed": True})
    errors = []

    def slow_writer():
        try:
            with CP.locked_state(path):
                time.sleep(0.10)
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            errors.append(exc)

    def fast_writer():
        try:
            time.sleep(0.02)
            with CP.locked_state(path) as state:
                state["session_unattended"] = True
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=slow_writer), threading.Thread(target=fast_writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final.get("session_unattended") is True, (
        "the concurrent write was lost: the slow writer wrote back a dict it "
        "read before the fast one ran"
    )
    assert final.get("seed") is True, "the lock dropped a key neither writer touched"


def test_the_unlocked_shape_really_does_lose_it(tmp_path):
    """Pins the premise. If a plain read-modify-write no longer loses the key,
    the test above is proving nothing and should be re-derived."""
    path = tmp_path / "checkpoint-y.json"
    CP.write_json_atomic(path, {"seed": True})

    def slow_unlocked():
        state = CP.read_json(path)
        time.sleep(0.10)
        CP.write_json_atomic(path, state)

    def fast_unlocked():
        time.sleep(0.02)
        state = CP.read_json(path)
        state["session_unattended"] = True
        CP.write_json_atomic(path, state)

    threads = [threading.Thread(target=slow_unlocked), threading.Thread(target=fast_unlocked)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    final = json.loads(path.read_text(encoding="utf-8"))
    assert final.get("session_unattended") is None, (
        "the unlocked shape kept the key, so this file's premise no longer holds"
    )


def test_an_exception_inside_the_block_writes_nothing(tmp_path):
    path = tmp_path / "checkpoint-z.json"
    CP.write_json_atomic(path, {"keep": 1})
    with pytest.raises(RuntimeError), CP.locked_state(path) as state:
        state["keep"] = 2
        raise RuntimeError("boom")
    assert json.loads(path.read_text(encoding="utf-8")) == {"keep": 1}


def test_it_creates_the_file_when_absent(tmp_path):
    path = tmp_path / "deeper" / "checkpoint-new.json"
    with CP.locked_state(path) as state:
        state["created"] = True
    assert json.loads(path.read_text(encoding="utf-8")) == {"created": True}


def test_it_proceeds_rather_than_hanging_when_the_lock_is_held(tmp_path, capsys):
    """A hook that blocks is worse than a hook that races: the Stop hook has a
    90-second budget and the statusline runs every turn."""
    path = tmp_path / "checkpoint-busy.json"
    CP.write_json_atomic(path, {})
    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with CP.locked_state(path):
            holder_ready.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    assert holder_ready.wait(timeout=5)

    started = time.monotonic()
    with CP.locked_state(path, wait=0.2) as state:
        state["proceeded"] = True
    elapsed = time.monotonic() - started

    release.set()
    t.join(timeout=10)

    assert elapsed < 2.0, f"the writer blocked for {elapsed:.1f}s instead of degrading"
    assert "writing unlocked" in capsys.readouterr().err, (
        "a degraded write must say so; a silent one reads like a locked write"
    )


def test_the_lock_file_is_a_sidecar_not_the_state_file(tmp_path):
    """Locking the file a writer is about to `os.replace` would lock an inode
    that stops being the file."""
    path = tmp_path / "checkpoint-side.json"
    with CP.locked_state(path) as state:
        state["x"] = 1
    assert (tmp_path / "checkpoint-side.json.lock").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"x": 1}


# ============================================================
# Every writer of the state file must go through the lock
# ============================================================

WRITERS = (
    ".claude/hooks/checkpoint-statusline.py",
    ".claude/hooks/checkpoint-offer.py",
    ".claude/hooks/checkpoint-save.py",
    "scripts/checkpoint-paths.py",
)


def _atomic_writer_calls(source: str) -> list[int]:
    """Line numbers of every CALL to `write_json_atomic` in `source`.

    An AST question rather than a substring one, because the substring version
    only ever saw the offender its author was already imagining. It matched two
    hand-picked spellings of the ARGUMENT - `"state_path" in line`, written
    twice by a copy-paste that was plainly meant to be a second spelling, and
    `", state)" in line`.

    MEASURED 2026-09-01. A real, unlocked read-modify-write inserted into
    `.claude/hooks/checkpoint-statusline.py`, one of the four names in WRITERS::

        target = state_path
        payload_state = CP.read_json(target)
        CP.write_json_atomic(target, payload_state)

    left this file green at 14 passed. The variable is simply not called
    `state_path`, and the second argument is not called `state`.

    A DEFINITION is not a call, and drawing that line is what the old floor
    could not do. The ONLY line in the four writers that reached the offender
    check was `def write_json_atomic(path: Path, data: dict) -> None:` in
    checkpoint-save.py, so `inspected >= 1` was satisfied by a def while zero
    real calls were ever examined - a guard green over an empty corpus, with a
    floor that said otherwise. (That definition is itself dead: a repo-wide
    search on 2026-09-01 found no caller of it in any file, test or hook. It is
    reported rather than removed, per the workspace's dead-code convention.)
    """
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "write_json_atomic":
            found.append(node.lineno)
    return sorted(found)


def test_the_atomic_writer_detector_flags_a_real_bypass():
    """The negative case. Nothing ever made the old guard refuse.

    Three inputs, each on a boundary the detector has to get right: the bypass
    spelled with names the substring check missed, a definition (not a call),
    and the correct shape going through the lock.
    """
    planted = (
        "def render():\n"
        "    target = state_path\n"
        "    payload_state = CP.read_json(target)\n"
        "    CP.write_json_atomic(target, payload_state)\n"
    )
    assert _atomic_writer_calls(planted) == [4], (
        "the detector cannot see a bypass written with any other variable name"
    )
    assert _atomic_writer_calls(
        "def write_json_atomic(path, data):\n    pass\n"
    ) == [], "a definition was counted as a call"
    assert _atomic_writer_calls(
        "with CP.locked_state(p) as s:\n    s['k'] = 1\n"
    ) == [], "the correct shape was flagged"
    # A bare call, not only an attribute one.
    assert _atomic_writer_calls("write_json_atomic(p, d)\n") == [1]


def test_no_writer_calls_the_atomic_writer_on_a_state_path_directly():
    """`write_json_atomic` is the primitive `locked_state` builds on. Calling it
    from one of these four files skips the lock and reinstates the race.

    A source guard rather than a race test on purpose: the loss needs two
    processes overlapping inside a sub-millisecond window, so a behavioural test
    for it either injects a sleep into production code or fails one run in sixty.
    The unit tests above prove the lock works; this proves the writers use it.

    The rule is "no direct call", not "no direct call on a path that looks like
    the state file", and the widening is deliberate. The state JSON is the only
    thing these four write with this primitive, so the narrower rule bought
    nothing and cost the detector its ability to see an offender under a
    different name. If a legitimate non-state use ever appears here, this test
    fails and the author states the reason - which is what a guard is for.
    """
    offenders = []
    parsed = 0
    for rel in WRITERS:
        path = ROOT / rel
        source = path.read_text(encoding="utf-8")
        # The floor, on the thing the walk actually consumes. A file that read
        # empty - moved, renamed, replaced by a stub - would contribute no
        # offenders and read as a pass.
        assert len(source) > 2000, (
            f"{rel} is {len(source)} bytes; it is not the writer this guard "
            "means to inspect"
        )
        parsed += 1
        offenders.extend(f"{rel}:{lineno}" for lineno in _atomic_writer_calls(source))
    assert parsed == len(WRITERS) >= 4, (
        f"parsed {parsed} of {len(WRITERS)} writers"
    )
    assert not offenders, (
        "these writers bypass CP.locked_state and can lose a concurrent "
        "write:\n  " + "\n  ".join(offenders)
    )


def test_the_statusline_does_not_lose_a_concurrent_write(tmp_path, monkeypatch):
    """A key another process stamped mid-render survives the render's own write.

    This is the INVARIANT. The test that stood here until 2026-08-31 was three
    substring greps over the hook's source, requiring `state_at_read`,
    `locked_state(state_path) as fresh` and `fresh.update(changes)`. Those name
    ONE implementation of the invariant, the diff-and-merge, and a grep for an
    implementation fails when the implementation is replaced by a stronger one.

    That is what happened. The diff existed because the render READ outside the
    lock and wrote inside it, so it could only protect the write; the DECISION
    still ran on the pre-lock copy, and a bucket stamped in that window made the
    render re-offer a checkpoint already consumed. Read, decide and write now
    share one hold of `CP.locked_state`, which leaves no window for a diff to
    protect against, so the diff was removed with the window.

    Asked of behaviour, so any future shape that keeps the invariant passes and
    any that loses it fails. `state_at_read` was a name; not losing the write is
    the promise.
    """
    import importlib.util

    hook_path = ROOT / ".claude" / "hooks" / "checkpoint-statusline.py"
    spec = importlib.util.spec_from_file_location("statusline_lock_probe", hook_path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    project = tmp_path / "project"
    project.mkdir()
    state_path = hook.CP.state_path(project, "probe-session")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"session_unattended": False}), encoding="utf-8")

    # The competing write lands between any read the hook takes OUTSIDE the lock
    # and the read the lock covers. If the hook decides or writes on a pre-lock
    # copy, this key is gone afterwards.
    real_locked_state = hook.CP.locked_state
    injected = {"count": 0}

    @contextlib.contextmanager
    def locked_state_with_a_racer(path, *args, **kwargs):
        if injected["count"] == 0:
            injected["count"] += 1
            current = json.loads(path.read_text(encoding="utf-8"))
            current["session_unattended"] = True
            current["stamped_by_the_other_process"] = "survive-me"
            path.write_text(json.dumps(current), encoding="utf-8")
        with real_locked_state(path, *args, **kwargs) as state:
            yield state

    monkeypatch.setattr(hook.CP, "locked_state", locked_state_with_a_racer)
    monkeypatch.setattr(hook.CP, "project_root", lambda payload: project)
    monkeypatch.setattr(hook.CP, "session_slug", lambda payload: "probe-session")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"context_window": {"used_percentage": 40.0}})),
    )

    assert hook.main() == 0
    assert injected["count"] == 1, (
        "the racer never fired, so this test measured nothing")

    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after.get("stamped_by_the_other_process") == "survive-me", (
        "the render overwrote a key another process stamped mid-render. Read, "
        "decide and write must share one hold of CP.locked_state.")
    assert after.get("session_unattended") is True, (
        "the operator's switch was reinstated to its pre-render value, which is "
        "the exact loss measured on 2026-08-20")
    # The other direction: the render must still record its own reading, or a
    # hook that wrote nothing at all would satisfy the two assertions above.
    assert after.get("used_percentage") == 40.0, (
        "the render preserved the racer's keys by writing nothing of its own")


def test_the_writer_list_is_not_empty():
    """A list that names nothing checks nothing."""
    assert len(WRITERS) >= 4
    for rel in WRITERS:
        assert (ROOT / rel).is_file(), f"{rel} moved; update this guard"


# ============================================================
# The shared .latest pointer PAIR is two files, written as one
# ============================================================

def test_the_shared_pointer_pair_cannot_be_torn(tmp_path):
    """`summary.md` and `prompt.md` must always name the same archive.

    They are two separate text files written back to back. Two sessions
    compacting at once can interleave between them and leave `summary.md`
    naming session A's archive while `prompt.md` names session B's - a state
    neither session ever held, and the one a resumed session reads.

    The tear is reproduced by widening the gap with a sleep, not by racing and
    hoping. `file_lock` closes it.
    """
    base = tmp_path / ".latest"
    base.mkdir()
    summary, prompt = base / "summary.md", base / "prompt.md"
    lock = base / ".pointers.lock"

    def writer(tag: str, gap: float, delay: float):
        time.sleep(delay)
        with CP.file_lock(lock):
            summary.write_text(f"summary-{tag}", encoding="utf-8")
            time.sleep(gap)
            prompt.write_text(f"prompt-{tag}", encoding="utf-8")

    threads = [
        threading.Thread(target=writer, args=("A", 0.10, 0.0)),
        threading.Thread(target=writer, args=("B", 0.0, 0.02)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    got = (summary.read_text(encoding="utf-8").split("-")[1],
           prompt.read_text(encoding="utf-8").split("-")[1])
    assert got[0] == got[1], (
        f"the pair is torn: summary names {got[0]}, prompt names {got[1]}"
    )


def test_the_unlocked_pair_really_does_tear(tmp_path):
    """Pins the premise of the test above."""
    base = tmp_path / ".latest"
    base.mkdir()
    summary, prompt = base / "summary.md", base / "prompt.md"

    def writer(tag: str, gap: float, delay: float):
        time.sleep(delay)
        summary.write_text(f"summary-{tag}", encoding="utf-8")
        time.sleep(gap)
        prompt.write_text(f"prompt-{tag}", encoding="utf-8")

    threads = [
        threading.Thread(target=writer, args=("A", 0.10, 0.0)),
        threading.Thread(target=writer, args=("B", 0.0, 0.02)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    got = (summary.read_text(encoding="utf-8").split("-")[1],
           prompt.read_text(encoding="utf-8").split("-")[1])
    assert got[0] != got[1], (
        "the unlocked pair stayed consistent, so this file's premise no longer holds"
    )


def test_the_save_hook_writes_the_shared_pair_under_a_lock():
    """A source guard, for the same reason as the state one: the tear needs two
    sessions overlapping and cannot be driven end to end deterministically."""
    text = (ROOT / ".claude" / "hooks" / "checkpoint-save.py").read_text(encoding="utf-8")
    assert "CP.file_lock(" in text, (
        "the .latest pointer pair is written without the shared lock"
    )


# ============================================================
# The state file a compaction resets must be the one everyone reads
# ============================================================

def test_the_state_path_is_the_canonical_one_on_both_branches():
    """The artifact slug and the state slug answer different questions.

    The archive FILENAME is a tracked path, so it uses the redacted id on the
    success branch and the literal `unredacted` when redaction failed - a
    poisoned string must not ride a filename into the data repo.

    The STATE path is not tracked (`.claude/state/` is gitignored) and is read by
    the statusline, the Stop hook and the CLI, all of which key off
    `safe_slug(raw session id)` unconditionally. Keying it off the artifact slug
    meant a quarantined compaction reset `checkpoint-unredacted.json` and left
    the real session's hysteresis untouched and its compaction unrecorded.
    """
    text = (ROOT / ".claude" / "hooks" / "checkpoint-save.py").read_text(encoding="utf-8")
    assert 'f"checkpoint-{CP.session_slug(payload)}.json"' in text, (
        "the state path is not derived from the canonical session slug"
    )
    assert 'f"checkpoint-{session_slug}.json"' not in text, (
        "the state path is still keyed off the artifact slug, which is the "
        "literal 'unredacted' whenever redaction fails"
    )
    # And the artifact name must NOT have been switched to the raw slug by the
    # same edit - that is the tracked path the quarantine protects.
    assert 'archive_name = f"{stamp}_handoff_compact-{trigger_slug}_{session_slug}.md"' in text, (
        "the archive name no longer uses the quarantine-safe slug"
    )


# ============================================================
# An instrument that names a key nobody writes reports nothing
# ============================================================

def test_the_compaction_watcher_watches_keys_that_are_actually_written():
    """`scripts/dev/compact-watch.py` watched `compact_request_at` for its first
    hour. The writer spells it `compact_requested_at`, so the log said no request
    had been made through a run where one had fired at 07:41:02 and been
    recorded. A misspelled key is indistinguishable from a quiet mechanism, which
    is the same failure shape as a gate that reports green while doing nothing.
    """
    import re

    watcher = (ROOT / "scripts" / "dev" / "compact-watch.py").read_text(encoding="utf-8")
    block = watcher[watcher.index("WATCHED = ("):]
    block = block[:block.index("\n)")]
    watched = set(re.findall(r'"([a-z_]+)"', block))
    assert len(watched) >= 15, "the watch list shrank; re-derive this guard"

    writers = ""
    for rel in (".claude/hooks/checkpoint-offer.py",
                ".claude/hooks/checkpoint-save.py",
                ".claude/hooks/checkpoint-statusline.py",
                "scripts/utils/checkpoint_paths.py"):
        writers += (ROOT / rel).read_text(encoding="utf-8")

    orphans = sorted(k for k in watched
                     if f'"{k}"' not in writers and f"{k}=" not in writers)
    assert not orphans, (
        f"the watcher names {len(orphans)} key(s) no writer produces, so it "
        f"would report their absence as silence: {orphans}"
    )
