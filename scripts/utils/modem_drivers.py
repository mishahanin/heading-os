#!/usr/bin/env python3
"""Transport drivers for the modem-tune tool.

Each driver owns ONLY how AT commands and status reach a given router; all IMEI
math, ledger, and generation live in modem_core. `ssh_fn` is injected so drivers
are unit-testable without a live router.

  Xe300Driver  -> gl_modem AT "<cmd>"      (Quectel EG25-G, USB)
  E5800Driver  -> ubus modem.CPU.AT        (Quectel RG650V-EU, embedded/MHI)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.modem_core import parse_at_imei
from scripts.utils.modem_ssh import shquote


class ModemDriver:
    device_id = ""

    def __init__(self, ssh_fn):
        self._ssh = ssh_fn

    def read_imei(self) -> str:
        raise NotImplementedError

    def read_status(self) -> dict:
        raise NotImplementedError

    def send_egmr(self, imei: str) -> tuple:
        raise NotImplementedError


class Xe300Driver(ModemDriver):
    device_id = "xe300"

    def _at(self, command: str, timeout: int = 30) -> str:
        return self._ssh(f"gl_modem AT {shquote(command)}", timeout)

    def read_imei(self) -> str:
        return parse_at_imei(self._at("AT+GSN"))

    def read_status(self) -> dict:
        return {
            "device": "xe300",
            "imeis": [{"slot": "1", "imei": self.read_imei()}],
            "cpin": self._at("AT+CPIN?"),
            "cops": self._at("AT+COPS?"),
            "csq": self._at("AT+CSQ"),
        }

    def send_egmr(self, imei: str) -> tuple:
        out = self._at(f'AT+EGMR=1,7,"{imei}"')
        return ("OK" in out, out)


class E5800Driver(ModemDriver):
    device_id = "e5800"
    BUS = "cpu"   # embedded Quectel RG650V-EU sits on the `cpu` (MHI) bus

    def _at(self, command: str, timeout: int = 3) -> dict:
        payload = json.dumps({"cmd": command, "timeout": timeout,
                              "source_flag": 0, "sub_id": 0})
        raw = self._ssh(f"ubus call modem.CPU.AT get_result_AT {shquote(payload)}", 15)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"data": raw, "channel_status": False}

    def read_imei(self) -> str:
        return parse_at_imei(self._at("AT+GSN").get("data", ""))

    def read_status(self) -> dict:
        info = self._modem_info()
        sims = self._ubus("cellular.sim", "status", {"bus": self.BUS})
        return {
            "device": "e5800",
            "model": info.get("name", ""),
            "imeis": info.get("imei", []),
            "sims": sims.get("sims", []),
        }

    def send_egmr(self, imei: str) -> tuple:
        resp = self._at(f'AT+EGMR=1,7,"{imei}"')
        ok = bool(resp.get("channel_status")) and "OK" in resp.get("data", "")
        return (ok, json.dumps(resp))

    # -- ubus helpers --
    def _ubus(self, service: str, method: str, args: dict = None) -> dict:
        payload = json.dumps(args or {})
        raw = self._ssh(f"ubus call {service} {method} {shquote(payload)}", 15)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _modem_info(self) -> dict:
        """Return the single-modem info dict, handling both ubus reply shapes.

        A bus-less `info '{}'` call wraps the result in `{"modems": [...]}`;
        the bus-scoped `info '{"bus":"cpu"}'` call this driver actually makes
        replies with a FLAT single-modem dict (top-level "name"/"imei", no
        "modems" wrapper) -- confirmed against a live GL-E5800.
        """
        data = self._ubus("cellular.modem", "info", {"bus": self.BUS})
        modems = data.get("modems")
        if isinstance(modems, list):
            return modems[0] if modems else {}
        return data if data.get("name") else {}


def driver_for(device: str, ssh_fn) -> ModemDriver:
    drivers = {"xe300": Xe300Driver, "e5800": E5800Driver}
    if device not in drivers:
        raise KeyError(f"unknown device '{device}'")
    return drivers[device](ssh_fn)
