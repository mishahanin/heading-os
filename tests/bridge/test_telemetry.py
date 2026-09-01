"""Telemetry: the JSONL append, its lock, and its disk-full behaviour.

`test_concurrent_writes_all_land_intact` used to end its docstring with "Proves
the per-instance lock serializes writes correctly", and it proved nothing of the
kind. Measured 2026-08-31 by deleting the lock outright:

    -        with self._lock:
    +        if True:  # MUTATION: lock removed

    tests/bridge/test_telemetry.py                 ->  5 passed  (x5 runs)
    tests/bridge tests/inbox_pulse tests/contract  ->  1706 passed, 1 skipped

Byte-identical to the baseline. The reason is not the threads: it is that a
single buffered `f.write(line)` of a ~90-byte record on an O_APPEND handle
reaches the kernel as one atomic `write(2)`, so the operating system was doing
the serialising and the lock was never load-bearing in the measurement. The old
test observed a correct OUTCOME and attributed it to a mechanism it never
varied, which is the scope claim `.claude/rules/scope-claims.md` is about.

`test_a_torn_record_cannot_happen_while_the_lock_is_held` below makes the write
non-atomic on purpose - two flushed appends with a yield between them - so an
unserialised writer CAN interleave. Under the lock it still cannot, because the
lock spans the whole open-write-close. That is the property the class claims,
and it is now the property being measured.
"""
import json
import threading
import time
from pathlib import Path

from scripts.bridge_daemon.telemetry import Telemetry


def test_writes_jsonl_line(workspace_root):
    """Telemetry.event() appends one JSONL line per call with ts, event, kwargs."""
    t = Telemetry(workspace_root)
    t.event("page_view", page="pulse")
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    lines = f.read_text().strip().split("\n")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "page_view"
    assert rec["page"] == "pulse"
    assert "ts" in rec


def test_concurrent_writes_all_land_intact(workspace_root):
    """50 threads writing simultaneously all produce valid JSONL lines.

    An end-to-end sanity check, and NOT evidence about the lock: with a single
    buffered write per record the kernel serialises these on its own, and this
    test stays green with the lock deleted (measured, see the module docstring).
    The sibling below is the one that measures the lock.
    """
    from concurrent.futures import ThreadPoolExecutor
    t = Telemetry(workspace_root)
    n = 50
    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(lambda i: t.event("page_view", page=f"p{i}"), range(n)))
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    lines = f.read_text().strip().split("\n")
    assert len(lines) == n
    # Every line must parse cleanly (no torn writes)
    pages = sorted(json.loads(l)["page"] for l in lines)
    assert pages == sorted(f"p{i}" for i in range(n))


class _SplitWriter:
    """A file handle that appends each record in two flushed halves.

    The gap is what an unserialised writer can be interrupted in. Nothing here
    is exotic: a longer record, a larger `usage.jsonl`, or a filesystem whose
    write path is not one syscall produces the same window in production. The
    split just makes it reachable in a second rather than under load.
    """

    def __init__(self, fh, gate: threading.Event):
        self._fh = fh
        self._gate = gate

    def write(self, text: str) -> None:
        half = max(1, len(text) // 2)
        self._fh.write(text[:half])
        self._fh.flush()
        self._gate.set()      # tell the other threads a record is half-written
        time.sleep(0.005)     # release the GIL inside the record
        self._fh.write(text[half:])
        self._fh.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()
        return False


def test_a_torn_record_cannot_happen_while_the_lock_is_held(workspace_root,
                                                            monkeypatch):
    """The lock, measured rather than asserted about.

    Twelve threads each append one record, and every append is deliberately two
    separate flushed writes with a yield between them. Without `self._lock` the
    halves interleave and the file stops being JSONL. With it, the whole
    open-write-close is one critical section and interleaving is impossible, so
    this is deterministic rather than a race that usually goes the right way.
    """
    real_open = Path.open
    gate = threading.Event()

    def _split_open(self, *a, **kw):
        fh = real_open(self, *a, **kw)
        if str(self).endswith("usage.jsonl"):
            return _SplitWriter(fh, gate)
        return fh

    monkeypatch.setattr(Path, "open", _split_open)

    t = Telemetry(workspace_root)
    n = 12
    threads = [threading.Thread(target=t.event, args=("page_view",),
                                kwargs={"page": f"p{i:02d}"}) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    monkeypatch.undo()

    assert gate.is_set(), (
        "no record was ever written in halves, so the interleaving window this "
        "test needs was never open and the result says nothing about the lock")

    f = workspace_root / ".daemon-state" / "usage.jsonl"
    raw = f.read_text(encoding="utf-8")
    lines = [ln for ln in raw.split("\n") if ln]
    torn = [ln for ln in lines if not _parses(ln)]
    assert not torn, (
        f"{len(torn)} of {len(lines)} records are not valid JSON, so two "
        f"threads wrote into one line: {torn[:3]}")
    assert sorted(json.loads(ln)["page"] for ln in lines) == \
        sorted(f"p{i:02d}" for i in range(n)), raw


def _parses(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


# Disk-full hardening (was a Phase 2 TODO in telemetry.py docstring; resolved
# 2026-05-20). event() must not raise on OSError; the failing telemetry write
# is preferable to a 500 propagating to the user-visible action that triggered
# it. The Phase J error tracker picks up the WARNING from logging.


def test_event_swallows_oserror_and_logs_warning(workspace_root, monkeypatch, caplog):
    """OSError from the underlying write must not propagate."""
    from pathlib import Path as _Path
    t = Telemetry(workspace_root)

    real_open = _Path.open
    def _fail_open(self, *a, **kw):
        if str(self).endswith("usage.jsonl"):
            raise OSError("No space left on device")
        return real_open(self, *a, **kw)
    monkeypatch.setattr(_Path, "open", _fail_open)

    with caplog.at_level("WARNING"):
        # Must not raise
        t.event("page_view", page="pulse", duration_s=1)
    assert any("telemetry write failed" in r.message for r in caplog.records)

    # The write was suppressed, and that is asserted rather than guarded.
    # `if f.exists(): assert f.read_text() == ""` could never run: `_fail_open`
    # raises for every path ending in `usage.jsonl`, so the file cannot be
    # created inside this test, and the branch was dead from the day it was
    # written. The sibling below covers the case where the file DOES exist.
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    assert not f.exists(), "a failed write still created the file"


def test_a_failed_write_leaves_an_existing_log_byte_identical(workspace_root,
                                                              monkeypatch,
                                                              caplog):
    """The half the dead branch was reaching for, with the file actually there.

    Seed it BEFORE the patch, because `_fail_open` refuses every later open.
    """
    from pathlib import Path as _Path
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    seeded = '{"event": "already here"}\n'
    f.write_text(seeded, encoding="utf-8")

    t = Telemetry(workspace_root)
    real_open = _Path.open

    def _fail_open(self, *a, **kw):
        if str(self).endswith("usage.jsonl"):
            raise OSError("No space left on device")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(_Path, "open", _fail_open)
    with caplog.at_level("WARNING"):
        t.event("page_view", page="pulse", duration_s=1)

    monkeypatch.undo()  # restore Path.open so the file can be read back
    assert f.read_text(encoding="utf-8") == seeded, (
        "a failed telemetry write truncated or appended to the existing log"
    )
    assert any("telemetry write failed" in r.message for r in caplog.records)


def test_event_warning_includes_event_name(workspace_root, monkeypatch, caplog):
    """The warning must name the failing event so the CEO can correlate the
    log line with the action that triggered it."""
    from pathlib import Path as _Path
    t = Telemetry(workspace_root)

    real_open = _Path.open
    def _fail_open(self, *a, **kw):
        if str(self).endswith("usage.jsonl"):
            raise OSError("read-only filesystem")
        return real_open(self, *a, **kw)
    monkeypatch.setattr(_Path, "open", _fail_open)

    with caplog.at_level("WARNING"):
        t.event("launch", action="email-respond")
    assert any("event=launch" in r.message for r in caplog.records)
