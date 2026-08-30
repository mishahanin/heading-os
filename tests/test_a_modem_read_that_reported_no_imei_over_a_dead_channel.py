"""A modem read that reported "no IMEI" over a channel nobody reached, and an ssh filter that ate real output.

**`modem_drivers.E5800Driver._at`.** An unparseable ubus reply was converted into
a successful-looking `{"data": <the error text>, "channel_status": False}`.
`read_imei` then ran `parse_at_imei` over that error string and returned `""` -
which is exactly what a modem that WAS reached and had no IMEI to report returns.
`ModemReadError`'s own docstring names this failure ("Distinct from 'read
successfully, nothing to report'"), and `_ubus` was rewritten to raise on it. The
AT path was left open, so the same silent channel survived one method over.

**`modem_ssh.ssh`.** The host-key noise filter ran over stdout and stderr
concatenated, so any line of GENUINE router output containing "Warning: " or
"Permanently added" was deleted before a driver ever parsed it, with nothing
recording the drop. Concatenating before splitting also glued the last stdout
line to the first stderr line when stdout did not end in a newline, feeding real
output into the filter's judgement. The ssh client writes its chatter on stderr;
the filter belongs there and nowhere else.

The router in these tests is a stub `ssh_fn`, so nothing here reaches a network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import modem_ssh  # noqa: E402
from scripts.utils.modem_drivers import (  # noqa: E402
    E5800Driver,
    ModemReadError,
)

# The string a failed ubus call really returns, per the module's own note.
FAILED_CALL = "Command failed: Not found"
GOOD_IMEI = '{"data": "\\r\\n351756051523999\\r\\n\\r\\nOK\\r\\n", "channel_status": true}'


# ============================================================
# A dead AT channel raises instead of answering ""
# ============================================================

def test_an_unparseable_at_reply_raises_rather_than_reading_as_no_imei():
    driver = E5800Driver(lambda cmd, timeout: FAILED_CALL)
    with pytest.raises(ModemReadError) as excinfo:
        driver.read_imei()
    assert "Command failed" in str(excinfo.value)


def test_an_empty_at_reply_raises_too():
    driver = E5800Driver(lambda cmd, timeout: "")
    with pytest.raises(ModemReadError):
        driver.read_imei()


def test_a_json_scalar_reply_raises_rather_than_being_indexed():
    driver = E5800Driver(lambda cmd, timeout: "null")
    with pytest.raises(ModemReadError) as excinfo:
        driver.read_imei()
    assert "not an object" in str(excinfo.value)


def test_a_real_reply_still_yields_the_imei():
    driver = E5800Driver(lambda cmd, timeout: GOOD_IMEI)
    assert driver.read_imei() == "351756051523999"


def test_a_reached_modem_with_no_imei_still_answers_empty_not_an_error():
    """The outcome the raise must stay distinct from."""
    driver = E5800Driver(
        lambda cmd, timeout: '{"data": "\\r\\nOK\\r\\n", "channel_status": true}')
    assert driver.read_imei() == ""


def test_send_egmr_also_refuses_a_dead_channel():
    driver = E5800Driver(lambda cmd, timeout: FAILED_CALL)
    with pytest.raises(ModemReadError):
        driver.send_egmr("351756051523999")


# ============================================================
# The ssh noise filter touches stderr only
# ============================================================

class _Completed:
    def __init__(self, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0


@pytest.fixture
def stub_ssh(monkeypatch):
    """Replace credentials and the subprocess so nothing leaves this process."""
    monkeypatch.setattr(modem_ssh, "credentials",
                        lambda: ("10.0.0.1", "root", "not-a-real-password"))
    captured: dict = {}

    def install(stdout, stderr):
        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Completed(stdout, stderr)
        monkeypatch.setattr(modem_ssh.subprocess, "run", fake_run)
        return captured

    return install


def test_a_stdout_line_saying_warning_is_not_deleted(stub_ssh):
    stub_ssh("Warning: low signal\nrssi -101\n", "")
    out = modem_ssh.ssh("cellular.modem info")
    assert out.splitlines() == ["Warning: low signal", "rssi -101"]


def test_a_stdout_line_saying_permanently_added_is_not_deleted(stub_ssh):
    stub_ssh("Permanently added to the ledger\n", "")
    assert modem_ssh.ssh("anything") == "Permanently added to the ledger"


def test_the_client_chatter_on_stderr_is_still_stripped(stub_ssh):
    stub_ssh("rssi -101\n",
             "Warning: Permanently added '10.0.0.1' to the list of known hosts.\n")
    assert modem_ssh.ssh("anything") == "rssi -101"


def test_a_real_stderr_line_survives_the_filter(stub_ssh):
    stub_ssh("", "ubus: connection failed\n")
    assert modem_ssh.ssh("anything") == "ubus: connection failed"


def test_stdout_without_a_final_newline_does_not_merge_into_stderr(stub_ssh):
    stub_ssh("351756051523999", "Warning: Permanently added host key.\n")
    assert modem_ssh.ssh("anything") == "351756051523999"


def test_the_transport_never_shells_out_with_a_string(stub_ssh):
    captured = stub_ssh("ok\n", "")
    modem_ssh.ssh("cellular.modem info")
    assert isinstance(captured["cmd"], list)
    assert "ssh" in captured["cmd"]
