"""The same guard, applied to the read of a modem and not to the write of it.

Three findings, one family. `ModemReadError` exists to keep "the device could
not be read at all" apart from "read successfully, nothing to report", and each
of these is a place where the second was still standing in for the first.

**1. `modem-tune._apply_imei` had no handler on the WRITE.** The read above it
refuses an unreachable modem with exit 2, and then `drv.send_egmr(target)` ran
bare. `E5800Driver.send_egmr` calls `_at`, which has raised `ModemReadError` on
an unreadable reply since 2026-08-30, so a router that dropped between the read
and the write took the whole command out as a traceback. MEASURED 2026-09-01
with a driver whose `send_egmr` raises "No route to host": `ModemReadError`
propagated out of `_apply_imei`, out of a file whose every other exit is a code.

**2. `E5800Driver.read_imei` ignored `channel_status`.** That field is the
device's own statement about whether the AT exchange happened, and `send_egmr`
one method down already refuses to call a write successful without it. The read
mined the reply for digits regardless. MEASURED 2026-09-01 with
`{"data": "\\r\\n351756051523999\\r\\n\\r\\nERROR\\r\\n", "channel_status": false}`:
`read_imei()` answered `351756051523999`, and `_apply_imei` files whatever comes
back into the device history AND into the never-repeat `used` list, so an IMEI
the modem never confirmed is recorded as spent.

**3. `Xe300Driver` never raised at all.** The 2026-08-30 fix landed in the
E5800 driver and stopped one class short. `modem_ssh.ssh` runs `subprocess.run`
without `check=True`, so an unreachable router returns the ssh client's own
complaint as an ordinary string and `parse_at_imei` finds no digits in it.
MEASURED 2026-09-01 with "ssh: connect to host 192.0.2.1 port 22: No route to
host": `read_imei()` returned "", `read_status()` returned a well-formed dict
claiming slot 1 holds no IMEI, and `cmd_status` printed a Luhn verdict over it
and exited 0, while the same command against an unreachable E5800 exits 2.

Nothing here reaches a network or a router. Every transport is a stub, the
ledger is redirected to `tmp_path`, and the stub that matters records whether
`AT+EGMR` was ever sent.

Run: .venv/bin/python -m pytest
tests/test_a_modem_write_that_tracebacked_where_its_read_refused.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.modem_drivers import (  # noqa: E402
    E5800Driver,
    ModemReadError,
    Xe300Driver,
)


def _load():
    spec = importlib.util.spec_from_file_location(
        "modem_tune_write_probe", ROOT / "scripts" / "modem-tune.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["modem_tune_write_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


MT = _load()

DEVICE = "e5800"
CFG = {"tac": "35907011"}

# Two DIFFERENT Luhn-valid IMEIs, derived rather than typed. `_apply_imei`
# refuses a target that fails Luhn two lines before the read, and a target equal
# to the modem's current value makes every ledger assertion below satisfiable by
# the wrong append.
TARGET = "359070116384108"
CURRENT = "359070111111118"

# What a real failed ubus call returns, per the driver's own note.
FAILED_CALL = "Command failed: Not found"
SSH_DEAD = "ssh: connect to host 192.0.2.1 port 22: No route to host"


def test_the_two_imeis_are_valid_and_distinct():
    """Pins the arrangement for every ledger assertion in this file."""
    assert MT.mc.luhn_valid(TARGET)
    assert MT.mc.luhn_valid(CURRENT)
    assert TARGET != CURRENT


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """A ledger under tmp_path, so no test can write the operator's real one."""
    path = tmp_path / "modem-ledger.json"
    monkeypatch.setattr(MT, "ledger_path", lambda p=path: p)
    return {"used": [], "devices": {}}


# ============================================================
# 1 - the write half of the read guard
# ============================================================

class _DiesOnWrite:
    """Read succeeds, then the router goes away before the write lands."""

    def __init__(self, current: str = CURRENT) -> None:
        self.current = current
        self.reads = 0

    def read_imei(self):
        self.reads += 1
        return self.current

    def send_egmr(self, target):
        raise ModemReadError(
            f"ubus call did not return JSON; the reply was: {SSH_DEAD}")


class _Live:
    def __init__(self, current: str = CURRENT) -> None:
        self.current = current
        self.sends: list[str] = []

    def read_imei(self):
        return self.current

    def send_egmr(self, target):
        self.sends.append(target)
        return True, "OK"


def test_an_unreadable_write_returns_a_code_instead_of_a_traceback(ledger, capsys):
    """THE case. No pytest.raises: returning at all is the assertion."""
    rc = MT._apply_imei(DEVICE, _DiesOnWrite(), ledger, CFG, TARGET, False)

    assert rc == 1, "exit 1 is 'operation failed'; 2 would claim it never acted"
    err = capsys.readouterr().err
    assert "could not be read" in err
    assert "No route to host" in err


def test_the_unreadable_write_names_the_command_that_settles_it(ledger, capsys):
    """AT+EGMR may well have landed. A message that does not say so leaves the
    operator to guess, and the ledger already carries the old IMEI as spent."""
    MT._apply_imei(DEVICE, _DiesOnWrite(), ledger, CFG, TARGET, False)

    err = capsys.readouterr().err
    assert "verify" in err
    assert TARGET in err


def test_the_unreadable_write_keeps_the_replaced_imei_burned(ledger, capsys):
    """The old IMEI left circulation the moment AT+EGMR was attempted, and the
    ledger is the only account of that. Rolling it back would let a later run
    mint it again for a second device."""
    MT._apply_imei(DEVICE, _DiesOnWrite(), ledger, CFG, TARGET, False)
    capsys.readouterr()

    assert CURRENT in ledger["used"]
    assert MT.ledger_path().exists(), "the record of the attempt never reached disk"
    on_disk = json.loads(MT.ledger_path().read_text(encoding="utf-8"))
    assert CURRENT in on_disk["used"]


def test_the_unreadable_write_does_not_record_the_target_as_applied(ledger, capsys):
    """The other direction. Nothing confirmed the new IMEI, so `current` must
    not claim it: `verify` is what settles that, and a ledger that already said
    "applied" would make the verify a formality."""
    MT._apply_imei(DEVICE, _DiesOnWrite(), ledger, CFG, TARGET, False)
    capsys.readouterr()

    assert ledger["devices"][DEVICE].get("current") is None


def test_a_write_that_lands_still_returns_zero_and_records_both(ledger, capsys):
    """The anchor over all four above. A handler that caught everything and
    returned 1 would pass them and break the command."""
    drv = _Live()

    rc = MT._apply_imei(DEVICE, drv, ledger, CFG, TARGET, False)
    capsys.readouterr()

    assert rc == 0
    assert drv.sends == [TARGET]
    assert ledger["devices"][DEVICE]["current"]["imei"] == TARGET
    assert CURRENT in ledger["used"] and TARGET in ledger["used"]


def test_a_write_that_answers_without_ok_is_still_the_older_failure(ledger, capsys):
    """A modem that ANSWERED and refused is not a modem nobody reached, and the
    two must keep their separate messages."""
    class _Refuses(_Live):
        def send_egmr(self, target):
            self.sends.append(target)
            return False, '{"data": "ERROR", "channel_status": true}'

    rc = MT._apply_imei(DEVICE, _Refuses(), ledger, CFG, TARGET, False)

    assert rc == 1
    err = capsys.readouterr().err
    assert "AT+EGMR did not return OK" in err
    assert "could not be read" not in err


# ============================================================
# 2 - the E5800 read honours the device's own channel_status
# ============================================================

def _e5800(payload) -> E5800Driver:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return E5800Driver(lambda cmd, timeout=3: raw)


def test_a_reply_the_device_marked_down_is_not_mined_for_digits():
    """THE case, with the exact shape measured: digits present, channel down."""
    driver = _e5800({"data": "\r\n351756051523999\r\n\r\nERROR\r\n",
                     "channel_status": False})

    with pytest.raises(ModemReadError) as excinfo:
        driver.read_imei()
    assert "channel" in str(excinfo.value)


def test_an_empty_reply_the_device_marked_down_also_raises():
    driver = _e5800({"data": "", "channel_status": False})

    with pytest.raises(ModemReadError):
        driver.read_imei()


def test_a_reply_with_no_channel_status_key_is_still_read():
    """Absence is not a negative statement. A firmware that omits the field
    would otherwise have every one of its modems called unreachable."""
    driver = _e5800({"data": "\r\n351756051523999\r\n\r\nOK\r\n"})

    assert driver.read_imei() == "351756051523999"


def test_a_reply_the_device_marked_up_is_still_read():
    """The anchor for both above."""
    driver = _e5800({"data": "\r\n351756051523999\r\n\r\nOK\r\n",
                     "channel_status": True})

    assert driver.read_imei() == "351756051523999"


def test_a_reached_modem_holding_no_imei_still_answers_empty():
    """The outcome the raise must stay distinct from, on the E5800 side."""
    driver = _e5800({"data": "\r\nOK\r\n", "channel_status": True})

    assert driver.read_imei() == ""


def test_the_unreadable_e5800_read_is_refused_by_apply(ledger, capsys):
    """End to end: the driver raise and the CLI refusal are two hops, and the
    second is what stops the write."""
    driver = _e5800({"data": "", "channel_status": False})

    rc = MT._apply_imei(DEVICE, driver, ledger, CFG, TARGET, False)

    assert rc == 2
    assert "could not read the current IMEI" in capsys.readouterr().err
    assert ledger["used"] == []


# ============================================================
# 3 - the XE300 transport, which never raised at all
# ============================================================

def _xe300(reply: str) -> Xe300Driver:
    return Xe300Driver(lambda cmd, timeout=30: reply)


@pytest.mark.parametrize("reply", [SSH_DEAD, "", "   \n", FAILED_CALL,
                                   "sh: gl_modem: not found"],
                         ids=["ssh-refused", "empty", "blank", "failed-call",
                              "no-such-binary"])
def test_a_gl_modem_reply_with_no_result_code_raises(reply):
    with pytest.raises(ModemReadError) as excinfo:
        _xe300(reply).read_imei()
    assert "result code" in str(excinfo.value)


def test_the_raise_quotes_the_reply_so_the_operator_can_see_it():
    with pytest.raises(ModemReadError) as excinfo:
        _xe300(SSH_DEAD).read_imei()
    assert "No route to host" in str(excinfo.value)


def test_an_empty_reply_says_empty_rather_than_quoting_nothing():
    with pytest.raises(ModemReadError) as excinfo:
        _xe300("").read_imei()
    assert "(empty)" in str(excinfo.value)


def test_a_real_gl_modem_reply_still_yields_the_imei():
    """The anchor. This is the shape the driver has always parsed."""
    assert _xe300("\r\n351756051523999\r\nOK\r\n").read_imei() == "351756051523999"


def test_a_reached_xe300_holding_no_imei_still_answers_empty():
    """The distinction the raise exists to preserve, on this driver too."""
    assert _xe300("\r\nOK\r\n").read_imei() == ""


def test_a_modem_that_answered_with_an_error_is_a_read_not_a_failure():
    """`+CME ERROR` is a modem talking. Refusing it would call a SIM-less but
    perfectly reachable router unreachable, and `read_status` asks CPIN first."""
    assert _xe300("\r\n+CME ERROR: 10\r\n").read_imei() == ""


def test_a_status_read_over_a_dead_xe300_raises_rather_than_inventing_a_row():
    """`read_status` built `{"imeis": [{"slot": "1", "imei": ""}]}` out of a
    transport failure, and `cmd_status` then printed a Luhn verdict over it."""
    with pytest.raises(ModemReadError):
        _xe300(SSH_DEAD).read_status()


def test_a_status_read_over_a_live_xe300_still_returns_every_field():
    """The anchor for the one above."""
    replies = {
        "AT+GSN": "\r\n351756051523999\r\nOK\r\n",
        "AT+CPIN?": "\r\n+CPIN: READY\r\n\r\nOK\r\n",
        "AT+COPS?": '\r\n+COPS: 0,0,"Example Carrier",7\r\n\r\nOK\r\n',
        "AT+CSQ": "\r\n+CSQ: 22,99\r\n\r\nOK\r\n",
    }

    def ssh(cmd, timeout=30):
        for needle, reply in replies.items():
            if needle in cmd:
                return reply
        raise AssertionError(f"unexpected command: {cmd}")

    status = Xe300Driver(ssh).read_status()

    assert status["imeis"] == [{"slot": "1", "imei": "351756051523999"}]
    assert "READY" in status["cpin"]
    assert "Example Carrier" in status["cops"]


def test_the_unreadable_xe300_read_is_refused_by_apply(ledger, capsys):
    """The end-to-end consequence, and the reason the raise is worth having:
    the write is what must not happen."""
    sends: list[str] = []

    class _Recording(Xe300Driver):
        def send_egmr(self, imei):
            sends.append(imei)
            return True, "OK"

    driver = _Recording(lambda cmd, timeout=30: SSH_DEAD)

    rc = MT._apply_imei("xe300", driver, ledger, CFG, TARGET, False)

    assert rc == 2
    assert sends == [], "AT+EGMR reached a router nobody had read"
    assert "could not read the current IMEI" in capsys.readouterr().err
