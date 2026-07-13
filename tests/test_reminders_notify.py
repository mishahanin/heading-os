import importlib.util
from datetime import date
from pathlib import Path

import pytest

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
