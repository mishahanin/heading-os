import os
import sys
import subprocess

from scripts.utils import modem_ssh
from scripts.utils.modem_ssh import shquote


def test_shquote_wraps_and_escapes_single_quotes():
    assert shquote("AT+GSN") == "'AT+GSN'"
    assert shquote("it's") == "'it'\"'\"'s'"


def test_the_host_argument_overrides_the_credentials_default(monkeypatch):
    """`host or default_host` decides which router is reconfigured.

    Every test here stubbed `subprocess.run` with a lambda that ignored `a[0]`
    and returned a fixed stdout, which was then the value asserted equal. So the
    command was never read: collapsing line 44 to `host = default_host` left the
    modem tests green while `resolve_device` probed one box and `send_egmr`
    wrote an IMEI to whichever router `.env` happened to name.
    """
    seen = []

    def _run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "359...\n", "")

    monkeypatch.setattr(modem_ssh, "credentials",
                        lambda: ("192.0.2.10", "root", "synthetic-not-a-real-password"))
    monkeypatch.setattr(modem_ssh.subprocess, "run", _run)

    assert modem_ssh.ssh("AT+GSN") == "359..."
    assert modem_ssh.ssh("AT+GSN", host="192.0.2.99") == "359..."

    targets = [c[-2] for c in seen]
    assert targets == ["root@192.0.2.10", "root@192.0.2.99"], (
        f"the host argument did not reach the command: {targets}")
    assert seen[0][-1] == "AT+GSN", "the remote command is not last"
    for cmd in seen:
        assert "StrictHostKeyChecking=no" in cmd, (
            "the documented host-key stance is gone from the command")


def test_a_router_reply_that_is_not_utf8_is_read_rather_than_raised(monkeypatch):
    """A real child process, not a stub that raises.

    `text=True` with no `encoding=` decodes with the HOST locale, and the raise
    lands inside `subprocess.run` - above every handler the drivers have, so it
    leaves `modem-tune` as a traceback rather than as the named refusal this
    module is built around. A stub that raises `UnicodeDecodeError` would only
    measure the handler; spawning a child that really emits the byte measures
    the fix. MEASURED 2026-09-01 with `\\xff` in a `+COPS` carrier name: under
    `text=True` alone the call raised, and `AT+GSN`'s digits went with it.
    """
    monkeypatch.setattr(modem_ssh, "credentials",
                        lambda: ("192.0.2.10", "root", "synthetic-not-a-real-password"))
    real_run = subprocess.run
    emit = (
        "import sys\n"
        "sys.stdout.buffer.write(b'+COPS: 0,0,\"Synth\\xffCarrier\",7\\n')\n"
        "sys.stdout.buffer.write(b'356741100000016\\nOK\\n')\n"
    )

    def _child(cmd, **kwargs):
        return real_run([sys.executable, "-c", emit], **kwargs)

    monkeypatch.setattr(modem_ssh.subprocess, "run", _child)

    out = modem_ssh.ssh("AT+COPS?")

    assert "356741100000016" in out, "the IMEI was lost with the undecodable byte"
    assert "OK" in out, "the final result code was lost, so a driver would refuse"
    assert "�" in out, "the bad byte was not replaced; something else decoded it"


def test_a_failed_askpass_removal_is_reported_not_swallowed(monkeypatch, capsys):
    """The askpass helper carries the router password in cleartext.

    The removal sits in a `finally`, where a swallowed OSError leaves that
    credential on disk with nobody told. This is the one case the workspace's
    exception rule exists for, so the handler reports and names the survivor.
    """
    real_unlink = os.unlink
    survivors = []

    def _refuse(path):
        survivors.append(path)
        raise OSError("device or resource busy")

    monkeypatch.setattr(modem_ssh, "credentials",
                        lambda: ("192.0.2.10", "root", "synthetic-not-a-real-password"))
    monkeypatch.setattr(modem_ssh.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "359...\n", ""))
    monkeypatch.setattr(os, "unlink", _refuse)

    try:
        assert modem_ssh.ssh("AT+GSN") == "359..."
    finally:
        for path in survivors:
            real_unlink(path)

    err = capsys.readouterr().err
    assert survivors, "the removal is still attempted"
    assert "could not remove the transient askpass file" in err
    assert survivors[0] in err, "the operator is told which file survived"
