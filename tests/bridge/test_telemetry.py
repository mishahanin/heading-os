import json
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
    Proves the per-instance lock serializes writes correctly."""
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
    # File contents unchanged (write was suppressed)
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    if f.exists():
        assert f.read_text() == ""


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
