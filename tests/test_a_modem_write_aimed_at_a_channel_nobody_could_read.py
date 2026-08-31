"""`_apply_imei` wrote to a modem whose current IMEI it had failed to read.

`scripts/modem-tune.py` stages a new IMEI on a travel router. Before writing it
reads the CURRENT one, and that value is what the function files into the
device's `history` and into the never-repeat `used` list. The ledger is the only
account of which IMEIs have been burned.

`read_imei` used to answer `""` for a dead channel, so the function printed
`Old IMEI: (unreadable)` and carried on to `AT+EGMR` against a device nobody had
reached. On 2026-08-30 the E5800 driver's `_at` started raising `ModemReadError`
on a failed command, matching its ubus sibling, because `""` made a dead channel
indistinguishable from a modem holding no IMEI. That raise then escaped
`_apply_imei` untouched.

MEASURED 2026-08-30 with a driver raising "No route to host": `_apply_imei`
propagated `ModemReadError` to its caller, out of a file whose every other exit
is a code (`Exit codes: 0 ok, 1 operation failed, 2 refused`). `cmd_status` two
hundred lines up already refuses an unreadable modem the same way, with the same
exit 2.

Nothing here touches a real modem. Every driver below is a stub, and the one
that matters raises if `send_egmr` is ever reached.

Run: python3 -m pytest tests/test_a_modem_write_aimed_at_a_channel_nobody_could_read.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.modem_drivers import ModemReadError  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location(
        "modem_tune_apply_probe", ROOT / "scripts" / "modem-tune.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["modem_tune_apply_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


MT = _load()

DEVICE = "xe300"
CFG = {"tac": "35907011"}


def _valid_imei() -> str:
    """A Luhn-valid IMEI, derived rather than typed.

    A hand-typed one that fails Luhn is refused two lines before the read, which
    would make every test below pass for the wrong reason.
    """
    base = "35907011638410"
    for check in "0123456789":
        if MT.mc.luhn_valid(base + check):
            return base + check
    raise AssertionError("no check digit completes this TAC; the helper is wrong")


TARGET = _valid_imei()


class _Dead:
    """A transport that cannot be reached."""

    def __init__(self) -> None:
        self.sends: list[str] = []

    def read_imei(self):
        raise ModemReadError(
            "ssh: connect to host 192.0.2.1 port 22: No route to host")

    def send_egmr(self, target):
        self.sends.append(target)
        return True, "OK"


class _Live:
    """A reachable modem holding a current IMEI."""

    def __init__(self, current: str = "359070116384108") -> None:
        self.current = current
        self.sends: list[str] = []

    def read_imei(self):
        return self.current

    def send_egmr(self, target):
        self.sends.append(target)
        return True, "OK"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """A ledger under tmp_path, so no test can write the operator's real one."""
    path = tmp_path / "modem-ledger.json"
    monkeypatch.setattr(MT, "ledger_path", lambda p=path: p)
    return {"used": [], "devices": {}}


def test_the_arrangement_is_not_refused_before_the_read(ledger):
    """Pins the fixture. Every assertion below would hold vacuously if the
    target IMEI failed the Luhn check two lines earlier."""
    assert MT.mc.luhn_valid(TARGET)
    assert TARGET not in ledger["used"]


def test_an_unreadable_modem_is_refused_with_a_code(ledger, capsys):
    """THE case."""
    drv = _Dead()

    rc = MT._apply_imei(DEVICE, drv, ledger, CFG, TARGET, False)

    assert rc == 2
    err = capsys.readouterr().err
    assert "could not read the current IMEI" in err
    assert "No route to host" in err


def test_nothing_is_written_to_a_modem_that_could_not_be_read(ledger):
    """The substantive half. A refusal that still sent would be no refusal."""
    drv = _Dead()

    MT._apply_imei(DEVICE, drv, ledger, CFG, TARGET, False)

    assert drv.sends == [], "AT+EGMR reached a modem nobody had read"


def test_the_ledger_is_not_spent_by_a_refused_write(ledger):
    """The never-repeat list is the only account of which IMEIs are burned.
    A refused attempt must not spend one, and must not reach the disk.

    `ledger["devices"]` is NOT asserted empty: `device_ledger` runs before the
    read and seeds an empty entry in memory. That entry records nothing, and the
    refusal returns before `save_ledger`, so the file is what proves it.
    """
    MT._apply_imei(DEVICE, _Dead(), ledger, CFG, TARGET, False)

    assert ledger["used"] == []
    assert ledger["devices"][DEVICE].get("current") is None
    assert ledger["devices"][DEVICE].get("history") == []
    assert not MT.ledger_path().exists(), "a refused write still saved the ledger"


def test_a_readable_modem_is_still_written_and_recorded(ledger):
    """The negative control, and it carries the whole file. A guard that refused
    every modem would pass all three tests above and break the command."""
    drv = _Live(current="359070116384108")

    rc = MT._apply_imei(DEVICE, drv, ledger, CFG, TARGET, False)

    assert rc == 0
    assert drv.sends == [TARGET]
    assert TARGET in ledger["used"]
    assert "359070116384108" in ledger["used"], "the replaced IMEI was not burned"
    history = ledger["devices"][DEVICE]["history"]
    assert history and history[-1]["imei"] == "359070116384108"


def test_a_modem_with_no_current_imei_is_still_written(ledger):
    """An empty read is NOT an unreadable one, and the two must stay apart.
    A brand-new modem reports no IMEI and is a legitimate target."""
    drv = _Live(current="")

    rc = MT._apply_imei(DEVICE, drv, ledger, CFG, TARGET, False)

    assert rc == 0
    assert drv.sends == [TARGET]
    assert ledger["devices"][DEVICE].get("history", []) == [], \
        "an absent old IMEI was filed into history as though it were real"
