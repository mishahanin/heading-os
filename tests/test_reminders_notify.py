import importlib.util
from datetime import date
from pathlib import Path

from scripts.utils import reminders_store as rs

ROOT = Path(__file__).resolve().parent.parent


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location(
        "reminders_notify", ROOT / "scripts" / "reminders-notify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_send_due_marks_fired_only_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    a = rs.add({"kind": "once", "when": "2026-07-26", "message": "prep"})
    b = rs.add({"kind": "once", "when": "2026-07-26", "message": "fail"})
    disp = _load_dispatcher()

    def send_fn(msg):
        return "fail" not in msg  # b's send "fails"

    sent = disp.send_due(date(2026, 7, 27), send_fn)
    assert a["id"] in sent and b["id"] not in sent
    recs = {r["id"]: r for r in rs.load()}
    assert recs[a["id"]]["status"] == "fired"   # succeeded -> fired
    assert recs[b["id"]]["status"] == "active"  # failed -> still due, retried next tick


def test_send_due_raising_send_does_not_abort_loop(tmp_path, monkeypatch):
    """Verify raising send_fn for one record does not abort processing others."""
    monkeypatch.setattr(rs, "store_path", lambda: tmp_path / "reminders.json")
    a = rs.add({"kind": "once", "when": "2026-07-26", "message": "first"})
    b = rs.add({"kind": "once", "when": "2026-07-26", "message": "second"})
    disp = _load_dispatcher()

    def send_fn(msg):
        if "first" in msg:
            raise RuntimeError("first record send failed")
        return True  # second succeeds

    sent = disp.send_due(date(2026, 7, 27), send_fn)
    recs = {r["id"]: r for r in rs.load()}

    # Second record should be processed despite first raising
    assert b["id"] in sent, "second record should be in sent ids"
    assert recs[b["id"]]["status"] == "fired", "second record should be marked fired"

    # First record should not be in sent, status stays active for retry
    assert a["id"] not in sent, "first record should not be in sent ids"
    assert recs[a["id"]]["status"] == "active", "first record should stay active"
