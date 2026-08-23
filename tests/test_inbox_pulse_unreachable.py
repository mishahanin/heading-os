"""An unreachable VM must not read as a quiet inbox.

`inbox-pulse-report.py` is an observability report about a remote daemon. Until
2026-08-23 an SSH failure returned None, `fetch_jsonl_for_date` folded that into
`[]`, and the report rendered a clean day and exited 0. The operator could not
tell "the VM is down" from "nobody wrote", and any automation reading the exit
code saw success -- the exact over-claim `.claude/rules/scope-claims.md` forbids.

ssh(1) exits 255 when the transport itself fails; a missing remote file exits
with `cat`'s own status. That is the discriminator.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "inbox_pulse_report", ROOT / "scripts" / "inbox-pulse-report.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["inbox_pulse_report"] = mod
_spec.loader.exec_module(mod)


class _Result:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def test_transport_failure_is_not_an_empty_day(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(255))
    assert mod.fetch_jsonl_for_date(date(2026, 8, 23)) is None, \
        "an unreachable VM must be distinguishable from a quiet day"


def test_a_missing_remote_log_is_a_genuine_empty_day(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(1))
    assert mod.fetch_jsonl_for_date(date(2026, 8, 23)) == []


def test_a_timeout_is_a_transport_failure(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=30)
    monkeypatch.setattr(subprocess, "run", _boom)
    assert mod.fetch_jsonl_for_date(date(2026, 8, 23)) is None


def test_a_good_day_still_parses(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _Result(0, '{"a": 1}\n\n{"b": 2}\n'))
    out = mod.fetch_jsonl_for_date(date(2026, 8, 23))
    assert out == [{"a": 1}, {"b": 2}]
