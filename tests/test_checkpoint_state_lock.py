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


def test_no_writer_calls_the_atomic_writer_on_a_state_path_directly():
    """`write_json_atomic` is the primitive `locked_state` builds on. Calling it
    on the state file directly skips the lock and reinstates the race.

    A source guard rather than a race test on purpose: the loss needs two
    processes overlapping inside a sub-millisecond window, so a behavioural test
    for it either injects a sleep into production code or fails one run in sixty.
    The unit tests above prove the lock works; this proves the writers use it.
    """
    offenders = []
    for rel in WRITERS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "write_json_atomic(" not in line or line.lstrip().startswith("#"):
                continue
            # The call is only a problem when its target is the state file.
            if "state_path" in line or "state_path" in line or ", state)" in line:
                offenders.append(f"{rel}:{lineno} {line.strip()}")
    assert not offenders, (
        "these writers bypass CP.locked_state and can lose a concurrent "
        "write:\n  " + "\n  ".join(offenders)
    )


def test_the_statusline_writes_only_what_it_changed():
    """It reads at the top of a render and writes at the bottom. Writing the
    dict it READ replaces anything another process wrote in between - which is
    exactly how an `--unattended on` was lost."""
    text = (ROOT / ".claude" / "hooks" / "checkpoint-statusline.py").read_text(encoding="utf-8")
    assert "state_at_read" in text, "the render no longer records its starting state"
    assert "with CP.locked_state(state_path) as fresh:" in text, (
        "the render's write is not under the shared lock"
    )
    assert "fresh.update(changes)" in text, (
        "the render is not applying a diff; a wholesale write reinstates the race"
    )


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
