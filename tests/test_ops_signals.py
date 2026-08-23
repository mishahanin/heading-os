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
    """Fail the test when `cond` is false. The name is the failure message.

    This used to `return bool(cond)`. Every caller accumulated the result into
    an `ok` flag and closed with `return ok`, which is how these files ran
    before they were renamed `test_*.py`: as standalone scripts, under a
    `main()` that read the return value.

    Under pytest a test that RETURNS False still PASSES. Pytest only emits
    `PytestReturnNotNoneWarning` and moves on. So the rename made the runner
    redundant and the conditions blind at the same time, and nothing said so.
    Measured 2026-08-20 across the three files that shared this helper: 25 test
    functions, 78 conditions, none able to fail the suite.

    An assert is the whole fix. The `main()` runner went with it, because its
    only job was to read a return value that no longer exists.
    """
    assert cond, name


def test_backup():
    # clean: nothing uncommitted, nothing ahead -> not due, ok
    s = ops.classify_backup(0, 0.0, 0)
    _check("backup clean -> not due", not s["due"] and s["severity"] == "ok")
    # uncommitted but fresh (under 24h) and not ahead -> not due
    s = ops.classify_backup(3, 5.0, 0)
    _check("backup fresh uncommitted -> not due", not s["due"])
    # uncommitted just past 24h -> due, warn
    s = ops.classify_backup(1, 24.0, 0)
    _check("backup 24h boundary -> due warn", s["due"] and s["severity"] == "warn")
    # ahead with no uncommitted -> due
    s = ops.classify_backup(0, 0.0, 2)
    _check("backup unpushed commits -> due", s["due"])
    # 48h -> high
    s = ops.classify_backup(1, 48.0, 0)
    _check("backup 48h -> high", s["severity"] == "high")
    # 72h -> critical (crunch floor)
    s = ops.classify_backup(1, 72.0, 0)
    _check("backup 72h -> critical", s["severity"] == "critical")
    _check("backup tier B", s["tier"] == "B")


def test_weekly_review():
    s = ops.classify_weekly_review(None)
    _check("review never -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_weekly_review(6)
    _check("review 6d -> not due", not s["due"])
    s = ops.classify_weekly_review(7)
    _check("review 7d boundary -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_weekly_review(14)
    _check("review 14d -> high", s["severity"] == "high")


def test_weekly_review_fs():
    with tempfile.TemporaryDirectory() as td:
        outputs = Path(td)
        # no dir -> never
        s = ops.weekly_review_state(outputs)
        _check("review fs absent -> never/due", s["due"] and s["value"] == "never")
        # write a file aged 10 days
        rd = outputs / "operations" / "reviews"
        rd.mkdir(parents=True)
        f = rd / "2026-06-16_weekly-review.md"
        f.write_text("x", encoding="utf-8")
        old = time.time() - 10 * 86400
        import os
        os.utime(f, (old, old))
        s = ops.weekly_review_state(outputs)
        _check("review fs 10d -> due, days>=10", s["due"] and isinstance(s["value"], int) and s["value"] >= 10)


def test_cold_sweep():
    s = ops.classify_cold_sweep(4)
    _check("cold-sweep 4 -> not due", not s["due"])
    s = ops.classify_cold_sweep(5)
    _check("cold-sweep 5 boundary -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_cold_sweep(12)
    _check("cold-sweep 12 -> high", s["severity"] == "high")


def test_publish():
    s = ops.classify_publish(0)
    _check("publish 0 -> not due", not s["due"])
    s = ops.classify_publish(1)
    _check("publish 1 -> due warn", s["due"] and s["severity"] == "warn")


def test_ollama():
    s = ops.classify_ollama(True, True)
    _check("ollama up+model -> not due", not s["due"] and s["severity"] == "ok")
    s = ops.classify_ollama(True, False)
    _check("ollama up, no model -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_ollama(False, False)
    _check("ollama down -> due high", s["due"] and s["severity"] == "high")
    _check("ollama tier A", s["tier"] == "A")
    # live probe against a dead port must be deterministically unreachable
    s = ops.ollama_state(host="http://127.0.0.1:1", timeout=1)
    _check("ollama dead port -> unreachable due", s["due"] and not s["value"]["reachable"])


def test_ollama_accel():
    """The accelerated host is a SECOND daemon, and `ollama_state` cannot see it.

    Measured 2026-08-21: the Windows-side GPU daemon crash-looped for 16 hours
    while `ollama_state` reported green the whole time, because it probes one
    address and that one was healthy. Every caller degraded silently to the CPU
    daemon. This signal is the eye on the other address.
    """
    s = ops.classify_ollama_accel(False, False)
    _check("accel not configured -> not due", not s["due"] and s["severity"] == "ok")
    s = ops.classify_ollama_accel(True, True)
    _check("accel up -> not due", not s["due"] and s["severity"] == "ok")
    # `high`, not `warn`, since 2026-08-23: embedding is PINNED to this host, so
    # it being down no longer means "slower on the local daemon", it means
    # nothing embeds at all. A warn here would rank an outage below a nudge.
    s = ops.classify_ollama_accel(True, False)
    _check("accel configured but down -> due high", s["due"] and s["severity"] == "high")
    # Up, but the embed model was never pulled there: the host answers and still
    # cannot embed. Reachability alone would call this green.
    s = ops.classify_ollama_accel(True, True, model_present=False)
    _check("accel up but model missing -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_ollama_accel(True, True, model_present=None)
    _check("accel model unknown -> not due", not s["due"])
    _check("accel tier B", s["tier"] == "B")
    _check("accel key", s["key"] == "ollama_accel")
    # Tier B is the point, not a detail: a Tier-A signal stays invisible until
    # auto-heal has failed twice, and nothing on this side of the WSL boundary
    # can restart a daemon on the other side of it.
    _check("accel summary counts-only", "\n" not in s["summary"] and s["summary"].strip())


def test_ollama_accel_state_fs():
    with tempfile.TemporaryDirectory() as td:
        engine = Path(td)
        cfg_dir = engine / "config"
        cfg_dir.mkdir(parents=True)

        # No config file at all -> nothing is configured, nothing is due. This
        # is the public-clone case: most operators have one daemon.
        s = ops.ollama_accel_state(engine)
        _check("accel no config -> not configured", not s["due"]
               and not s["value"]["configured"])

        # A config naming only the local daemon is not an accelerated host.
        (cfg_dir / "memory-index.yaml").write_text(
            'model: bge-m3\nhost: "http://localhost:11434"\n', encoding="utf-8")
        s = ops.ollama_accel_state(engine)
        _check("accel local host -> not configured", not s["due"]
               and not s["value"]["configured"])

        # A configured host that answers nothing is the failure this exists for.
        (cfg_dir / "memory-index.yaml").write_text(
            'model: bge-m3\nhost: "http://127.0.0.1:1"\n', encoding="utf-8")
        s = ops.ollama_accel_state(engine, timeout=1)
        _check("accel dead port -> due", s["due"] and s["value"]["configured"]
               and not s["value"]["reachable"])


def test_index():
    s = ops.classify_index(None, False)
    _check("index absent -> due high", s["due"] and s["value"] == "absent")
    s = ops.classify_index(0, False)
    _check("index fresh -> not due", not s["due"])
    s = ops.classify_index(1, True)
    _check("index sources newer -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_index(2, False)
    _check("index 2d stale boundary -> due", s["due"])


def test_index_fs():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        engine = base / "engine"
        data = base / "data"
        (engine / ".claude" / "rules").mkdir(parents=True)
        (data / "knowledge").mkdir(parents=True)
        # no index.db -> never built
        s = ops.index_freshness_state(engine, data)
        _check("index fs absent -> never built", s["due"] and s["value"] == "absent")
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
        _check("index fs sources newer -> due", s["due"] and s["value"]["sources_newer"])


def test_odin():
    s = ops.classify_odin({"nudge": False, "unharvested_total": 0, "reflect_clusters": 0})
    _check("odin no nudge -> not due", not s["due"])
    s = ops.classify_odin({"nudge": True, "unharvested_total": 8, "reflect_clusters": 1, "stale_clusters": 0})
    _check("odin nudge -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_odin({"nudge": True, "unharvested_total": 3, "reflect_clusters": 2, "stale_clusters": 1})
    _check("odin stale cluster -> high", s["severity"] == "high")


def test_queue():
    s = ops.classify_queue(0, 0)
    _check("queue empty -> not due", not s["due"] and s["severity"] == "ok")
    s = ops.classify_queue(1, 0)
    _check("queue 1 ready -> due warn", s["due"] and s["severity"] == "warn")
    s = ops.classify_queue(0, 1)
    _check("queue 1 failed -> due high", s["due"] and s["severity"] == "high")
    s = ops.classify_queue(2, 1)
    _check("queue ready+failed -> high, summary names both",
                 s["severity"] == "high" and "2 draft" in s["summary"] and "1 failed" in s["summary"])
    _check("queue tier B", ops.classify_queue(1, 0)["tier"] == "B")


def test_queue_fs():
    with tempfile.TemporaryDirectory() as td:
        data = Path(td)
        s = ops.queue_state(data)
        _check("queue fs absent -> not due", not s["due"])
        qdir = data / "outputs" / "operations" / "action-queue"
        qdir.mkdir(parents=True)
        (qdir / "queue.json").write_text('{"actions": ['
            '{"status":"pending","draft_status":"ready_for_review"},'
            '{"status":"send_failed"},'
            '{"status":"pending","draft_status":"needs_draft"}]}', encoding="utf-8")
        s = ops.queue_state(data)
        _check("queue fs counts ready+failed",
                     s["value"]["ready"] == 1 and s["value"]["failed"] == 1 and s["due"])


def test_summaries_counts_only():
    """No signal summary should embed anything but counts/ages (no content)."""
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
    _check("summaries single-line", all("\n" not in s for s in samples))
    _check("summaries non-empty", all(s.strip() for s in samples))


def test_cold_sweep_survives_an_unexpected_crm_health_shape(tmp_path, monkeypatch):
    """The monitor may not die on the shape of what it monitors.

    Found by the 2026-08-23 audit. `cold_sweep_state` iterates the parsed JSON
    with `c.get("health")`, guarded only against OSError, SubprocessError and
    JSONDecodeError. A dict, a list of strings, or a bare null all parse fine
    and then raise AttributeError or TypeError, which is not in that set — so
    the whole ops-radar run dies, and the docstring's promise ("degrades to
    red_count=0 ... a missing CRM is not a cold-sweep emergency") is false for
    every case except an absent file.

    `publish_state`, twenty lines below in the same file, already writes
    `if isinstance(data, dict)`. The shape risk was known and handled once.
    """
    import subprocess
    from collections import namedtuple

    script = tmp_path / "scripts" / "crm-health.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    Proc = namedtuple("Proc", "returncode stdout stderr")

    def _run_returning(payload):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: Proc(0, payload, ""))
        return ops.cold_sweep_state(tmp_path)

    # The shape it really emits today: a list of contact dicts.
    healthy = _run_returning('[{"health": "red"}, {"health": "green"}]')
    _check("a real payload still counts reds", healthy["value"] == 1)

    for payload, label in [
        ('{"contacts": []}', "a dict"),
        ('["alice", "bob"]', "a list of strings"),
        ("null", "a bare null"),
        ("42", "a bare number"),
    ]:
        signal = _run_returning(payload)
        _check(f"{label} did not kill the radar", signal["value"] == 0)
        _check(f"{label} is not reported as due", not signal["due"])
