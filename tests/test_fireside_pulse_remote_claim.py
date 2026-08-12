"""A failed SSH probe must not be reported as the daemon being unreachable.

`scripts/fireside-pulse.py` reaches the service-host daemon over SSH. When that
call fails it used to print "UNREACHABLE", which is a claim about the daemon
that the method never established: an outbound :22 block on the operator's own
network produces exactly the same failure as a dead VM. Measured 2026-08-12
from an Azertelecom exit, where github.com:22 and gitlab.com:22 were equally
unreachable while the daemon's webhook port answered normally.

Governed by .claude/rules/scope-claims.md.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PULSE = ROOT / "scripts" / "fireside-pulse.py"


@pytest.fixture(scope="module")
def pulse():
    # The module rebinds sys.stdout to a UTF-8 TextIOWrapper at import time,
    # which would close pytest's capture buffer. Point it at devnull for the
    # duration of the import, then hand stdout back.
    sys.path.insert(0, str(ROOT))
    saved = sys.stdout
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    try:
        spec = importlib.util.spec_from_file_location("fireside_pulse", PULSE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved
    return mod


def _run(pulse, monkeypatch, capsys, *, listening):
    monkeypatch.setattr(pulse, "_query_service_host", lambda *a, **k: None)
    monkeypatch.setattr(pulse, "_webhook_listening", lambda *a, **k: listening)
    pulse._print_remote_status("service-host")
    return capsys.readouterr().out


def test_ssh_failure_never_asserts_the_daemon_is_unreachable(pulse, monkeypatch, capsys):
    """No verdict on the daemon may be printed on SSH failure alone."""
    for listening in (True, False, None):
        out = _run(pulse, monkeypatch, capsys, listening=listening)
        assert "UNREACHABLE" not in out, f"bare UNREACHABLE verdict at listening={listening}: {out}"
        assert "SSH probe failed" in out, out


def test_listening_webhook_is_reported_as_daemon_listening(pulse, monkeypatch, capsys):
    out = _run(pulse, monkeypatch, capsys, listening=True)
    assert "UNKNOWN" in out
    assert "daemon is listening" in out
    # Obligation 2: name the thing the operator should check instead.
    assert ":22" in out


def test_dead_webhook_widens_to_host_may_be_down(pulse, monkeypatch, capsys):
    out = _run(pulse, monkeypatch, capsys, listening=False)
    assert "UNKNOWN" in out
    assert "host may be down" in out


def test_unresolvable_host_reports_unknown_not_healthy(pulse, monkeypatch, capsys):
    """Obligation 3: absent evidence widens to UNKNOWN, never to silence."""
    out = _run(pulse, monkeypatch, capsys, listening=None)
    assert "UNKNOWN" in out
    assert "could not be resolved" in out


def test_webhook_probe_returns_none_when_alias_does_not_resolve(pulse, monkeypatch):
    class _Proc:
        stdout = "user root\n"

    monkeypatch.setattr(pulse.subprocess, "run", lambda *a, **k: _Proc())
    assert pulse._webhook_listening("nowhere", 8443) is None


def test_webhook_probe_reports_false_on_refused_connection(pulse, monkeypatch):
    class _Proc:
        stdout = "hostname 203.0.113.7\nport 22\n"

    def _boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(pulse.subprocess, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(pulse.socket, "create_connection", _boom)
    assert pulse._webhook_listening("host", 8443) is False
