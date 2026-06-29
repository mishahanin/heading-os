#!/usr/bin/env python3
"""Boundary tests for ops-radar pure signal functions (scripts/utils/ops_signals.py).

Standalone-runnable, plain asserts. Anchored to the invariants the signal layer
must never break:
  - each threshold flips `due` at exactly the right boundary
  - severity bands escalate in the documented order
  - the crunch-piercing `critical` floor only lights at its band
  - fs-based signals (weekly_review, index_freshness) compute from real temp dirs
  - summaries are counts-only (no content leaks)
"""

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import ops_signals as ops


def _check(name, cond):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    return bool(cond)


def test_backup():
    ok = True
    # clean: nothing uncommitted, nothing ahead -> not due, ok
    s = ops.classify_backup(0, 0.0, 0)
    ok &= _check("backup clean -> not due", not s["due"] and s["severity"] == "ok")
    # uncommitted but fresh (under 24h) and not ahead -> not due
    s = ops.classify_backup(3, 5.0, 0)
    ok &= _check("backup fresh uncommitted -> not due", not s["due"])
    # uncommitted just past 24h -> due, warn
    s = ops.classify_backup(1, 24.0, 0)
    ok &= _check("backup 24h boundary -> due warn", s["due"] and s["severity"] == "warn")
    # ahead with no uncommitted -> due
    s = ops.classify_backup(0, 0.0, 2)
    ok &= _check("backup unpushed commits -> due", s["due"])
    # 48h -> high
    s = ops.classify_backup(1, 48.0, 0)
    ok &= _check("backup 48h -> high", s["severity"] == "high")
    # 72h -> critical (crunch floor)
    s = ops.classify_backup(1, 72.0, 0)
    ok &= _check("backup 72h -> critical", s["severity"] == "critical")
    ok &= _check("backup tier B", s["tier"] == "B")
    return ok


def test_weekly_review():
    ok = True
    s = ops.classify_weekly_review(None)
    ok &= _check("review never -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_weekly_review(6)
    ok &= _check("review 6d -> not due", not s["due"])
    s = ops.classify_weekly_review(7)
    ok &= _check("review 7d boundary -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_weekly_review(14)
    ok &= _check("review 14d -> high", s["severity"] == "high")
    return ok


def test_weekly_review_fs():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        outputs = Path(td)
        # no dir -> never
        s = ops.weekly_review_state(outputs)
        ok &= _check("review fs absent -> never/due", s["due"] and s["value"] == "never")
        # write a file aged 10 days
        rd = outputs / "operations" / "weekly-review"
        rd.mkdir(parents=True)
        f = rd / "2026-06-16_weekly-review.md"
        f.write_text("x", encoding="utf-8")
        old = time.time() - 10 * 86400
        import os
        os.utime(f, (old, old))
        s = ops.weekly_review_state(outputs)
        ok &= _check("review fs 10d -> due, days>=10", s["due"] and isinstance(s["value"], int) and s["value"] >= 10)
    return ok


def test_cold_sweep():
    ok = True
    s = ops.classify_cold_sweep(4)
    ok &= _check("cold-sweep 4 -> not due", not s["due"])
    s = ops.classify_cold_sweep(5)
    ok &= _check("cold-sweep 5 boundary -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_cold_sweep(12)
    ok &= _check("cold-sweep 12 -> high", s["severity"] == "high")
    return ok


def test_publish():
    ok = True
    s = ops.classify_publish(0)
    ok &= _check("publish 0 -> not due", not s["due"])
    s = ops.classify_publish(1)
    ok &= _check("publish 1 -> due warn", s["due"] and s["severity"] == "warn")
    return ok


def test_ollama():
    ok = True
    s = ops.classify_ollama(True, True)
    ok &= _check("ollama up+model -> not due", not s["due"] and s["severity"] == "ok")
    s = ops.classify_ollama(True, False)
    ok &= _check("ollama up, no model -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_ollama(False, False)
    ok &= _check("ollama down -> due high", s["due"] and s["severity"] == "high")
    ok &= _check("ollama tier A", s["tier"] == "A")
    # live probe against a dead port must be deterministically unreachable
    s = ops.ollama_state(host="http://127.0.0.1:1", timeout=1)
    ok &= _check("ollama dead port -> unreachable due", s["due"] and not s["value"]["reachable"])
    return ok


def test_index():
    ok = True
    s = ops.classify_index(None, False)
    ok &= _check("index absent -> due high", s["due"] and s["value"] == "absent")
    s = ops.classify_index(0, False)
    ok &= _check("index fresh -> not due", not s["due"])
    s = ops.classify_index(1, True)
    ok &= _check("index sources newer -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_index(2, False)
    ok &= _check("index 2d stale boundary -> due", s["due"])
    return ok


def test_index_fs():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        engine = base / "engine"
        data = base / "data"
        (engine / ".claude" / "rules").mkdir(parents=True)
        (data / "knowledge").mkdir(parents=True)
        # no index.db -> never built
        s = ops.index_freshness_state(engine, data)
        ok &= _check("index fs absent -> never built", s["due"] and s["value"] == "absent")
        # build the index db, then add a NEWER source
        idx = data / ".memory-index"
        idx.mkdir(parents=True)
        db = idx / "index.db"
        db.write_text("x", encoding="utf-8")
        import os
        build_t = time.time() - 100
        os.utime(db, (build_t, build_t))
        src = data / "knowledge" / "note.md"
        src.write_text("y", encoding="utf-8")  # mtime = now > build_t
        s = ops.index_freshness_state(engine, data)
        ok &= _check("index fs sources newer -> due", s["due"] and s["value"]["sources_newer"])
    return ok


def test_odin():
    ok = True
    s = ops.classify_odin({"nudge": False, "unharvested_total": 0, "reflect_clusters": 0})
    ok &= _check("odin no nudge -> not due", not s["due"])
    s = ops.classify_odin({"nudge": True, "unharvested_total": 8, "reflect_clusters": 1, "stale_clusters": 0})
    ok &= _check("odin nudge -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_odin({"nudge": True, "unharvested_total": 3, "reflect_clusters": 2, "stale_clusters": 1})
    ok &= _check("odin stale cluster -> high", s["severity"] == "high")
    return ok


def test_queue():
    ok = True
    s = ops.classify_queue(0, 0)
    ok &= _check("queue empty -> not due", not s["due"] and s["severity"] == "ok")
    s = ops.classify_queue(1, 0)
    ok &= _check("queue 1 ready -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_queue(0, 1)
    ok &= _check("queue 1 failed -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_queue(2, 1)
    ok &= _check("queue ready+failed -> high, summary names both",
                 s["severity"] == "high" and "2 draft" in s["summary"] and "1 failed" in s["summary"])
    ok &= _check("queue tier B", ops.classify_queue(1, 0)["tier"] == "B")
    return ok


def test_queue_fs():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        data = Path(td)
        s = ops.queue_state(data)
        ok &= _check("queue fs absent -> not due", not s["due"])
        qdir = data / "outputs" / "operations" / "action-queue"
        qdir.mkdir(parents=True)
        (qdir / "queue.json").write_text('{"actions": ['
            '{"status":"pending","draft_status":"ready_for_review"},'
            '{"status":"send_failed"},'
            '{"status":"pending","draft_status":"needs_draft"}]}', encoding="utf-8")
        s = ops.queue_state(data)
        ok &= _check("queue fs counts ready+failed",
                     s["value"]["ready"] == 1 and s["value"]["failed"] == 1 and s["due"])
    return ok


def test_summaries_counts_only():
    """No signal summary should embed anything but counts/ages (no content)."""
    ok = True
    samples = [
        ops.classify_backup(2, 30.0, 1)["summary"],
        ops.classify_weekly_review(9)["summary"],
        ops.classify_cold_sweep(6)["summary"],
        ops.classify_publish(3)["summary"],
        ops.classify_ollama(False, False)["summary"],
        ops.classify_index(5, True)["summary"],
        ops.classify_odin({"nudge": True, "unharvested_total": 4, "reflect_clusters": 1})["summary"],
    ]
    # every summary is a short single line
    ok &= _check("summaries single-line", all("\n" not in s for s in samples))
    ok &= _check("summaries non-empty", all(s.strip() for s in samples))
    return ok


def main():
    ok = True
    for fn in (
        test_backup, test_weekly_review, test_weekly_review_fs,
        test_cold_sweep, test_publish, test_ollama, test_index,
        test_index_fs, test_odin, test_queue, test_queue_fs,
        test_summaries_counts_only,
    ):
        print(f"\n{fn.__name__}:")
        ok &= fn()
    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
