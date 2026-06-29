"""Dead-letter writer + backoff tests (R14, plan Step 3).

Covers the dead-letter primitive in isolation - no daemon, no network, no real
``outputs/`` path. Every call passes an explicit ``workspace_root=tmp_path`` so
the live workspace tree is never touched.

  - record() writes a classified entry keyed by trace_id;
  - list_entries()/load() round-trip the written entry;
  - purge() honours the age cutoff (old removed, fresh kept);
  - backoff_schedule() is monotonic in the base term, capped at cap, and the
    jitter stays within [0, computed ceiling].

Run: python3 -m pytest tests/test_dead_letter.py
"""
import json
import os
import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import dead_letter


def test_record_writes_classified_entry_keyed_by_trace_id(tmp_path):
    path = dead_letter.record(
        trace_id="abc123",
        kind="email_send",
        payload={"to": "x@y.com", "subject": "hello"},
        classification="permanent",
        error="empty recipient",
        workspace_root=tmp_path,
    )
    assert path is not None
    assert path.name == "abc123__email_send.json"
    assert path.parent == tmp_path / "outputs" / "operations" / "dead-letter"

    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["trace_id"] == "abc123"
    assert entry["kind"] == "email_send"
    assert entry["classification"] == "permanent"
    assert entry["error"] == "empty recipient"
    assert entry["payload"] == {"to": "x@y.com", "subject": "hello"}
    assert "recorded_at" in entry


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode only")
def test_record_writes_owner_only_mode(tmp_path):
    path = dead_letter.record(
        trace_id="mode1",
        kind="email_send",
        payload={"to": "x@y.com"},
        classification="permanent",
        error="bad",
        workspace_root=tmp_path,
    )
    assert path is not None
    assert (path.stat().st_mode & 0o777) == 0o600


def test_unknown_classification_defaults_permanent(tmp_path):
    path = dead_letter.record(
        trace_id="cls1",
        kind="email_send",
        payload={},
        classification="bogus",
        error="x",
        workspace_root=tmp_path,
    )
    assert path is not None
    assert dead_letter.load(path)["classification"] == "permanent"


def test_list_and_load_round_trip(tmp_path):
    p1 = dead_letter.record(
        trace_id="t1", kind="email_send", payload={"n": 1},
        classification="transient", error="timeout", workspace_root=tmp_path,
    )
    p2 = dead_letter.record(
        trace_id="t2", kind="email_send", payload={"n": 2},
        classification="permanent", error="bad addr", workspace_root=tmp_path,
    )
    entries = dead_letter.list_entries(workspace_root=tmp_path)
    assert {p1, p2} == set(entries)

    loaded = {dead_letter.load(p)["trace_id"]: dead_letter.load(p) for p in entries}
    assert loaded["t1"]["payload"] == {"n": 1}
    assert loaded["t1"]["classification"] == "transient"
    assert loaded["t2"]["payload"] == {"n": 2}
    assert loaded["t2"]["classification"] == "permanent"


def test_purge_honours_age_cutoff(tmp_path):
    old = dead_letter.record(
        trace_id="old", kind="email_send", payload={},
        classification="permanent", error="x", workspace_root=tmp_path,
    )
    fresh = dead_letter.record(
        trace_id="fresh", kind="email_send", payload={},
        classification="permanent", error="x", workspace_root=tmp_path,
    )
    # Backdate the old entry to 100 days ago.
    hundred_days_ago = time.time() - 100 * 86400
    os.utime(old, (hundred_days_ago, hundred_days_ago))

    removed = dead_letter.purge(older_than_days=90, workspace_root=tmp_path)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_purge_keeps_everything_when_none_older(tmp_path):
    dead_letter.record(
        trace_id="recent", kind="email_send", payload={},
        classification="permanent", error="x", workspace_root=tmp_path,
    )
    removed = dead_letter.purge(older_than_days=1, workspace_root=tmp_path)
    assert removed == 0
    assert len(dead_letter.list_entries(workspace_root=tmp_path)) == 1


def test_backoff_ceiling_monotonic_and_capped():
    # The computed ceiling is base * factor**attempt, capped. Drive the rng to
    # its max (uniform returns the upper bound) to read the ceiling directly.
    class _MaxRng:
        def uniform(self, lo, hi):
            return hi

    rng = _MaxRng()
    base, factor, cap = 60.0, 2.0, 1800.0
    ceilings = [
        dead_letter.backoff_schedule(a, base=base, factor=factor, cap=cap, rng=rng)
        for a in range(0, 12)
    ]
    # Monotonic non-decreasing.
    assert all(b >= a for a, b in zip(ceilings, ceilings[1:]))
    # Capped.
    assert max(ceilings) == cap
    # Early attempts follow base * factor**attempt before the cap bites.
    assert ceilings[0] == 60.0
    assert ceilings[1] == 120.0
    assert ceilings[2] == 240.0


def test_backoff_jitter_within_bounds_and_deterministic():
    base, factor, cap = 60.0, 2.0, 1800.0
    for attempt in range(0, 10):
        ceiling = min(cap, base * (factor ** attempt))
        rng = random.Random(42)  # noqa: S311 - deterministic test jitter, not crypto
        delay = dead_letter.backoff_schedule(
            attempt, base=base, factor=factor, cap=cap, rng=rng
        )
        assert 0.0 <= delay <= ceiling
        # Deterministic: same seed + attempt -> same delay.
        rng2 = random.Random(42)  # noqa: S311 - deterministic test jitter, not crypto
        delay2 = dead_letter.backoff_schedule(
            attempt, base=base, factor=factor, cap=cap, rng=rng2
        )
        assert delay == delay2


def test_backoff_negative_attempt_floored():
    rng = random.Random(1)  # noqa: S311 - deterministic test jitter, not crypto
    # attempt < 0 is treated as 0; delay must stay within the base ceiling.
    delay = dead_letter.backoff_schedule(-5, base=60.0, factor=2.0, cap=1800.0, rng=rng)
    assert 0.0 <= delay <= 60.0
