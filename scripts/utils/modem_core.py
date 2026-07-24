#!/usr/bin/env python3
"""Device-independent logic for the modem-tune tool: IMEI math, AT parsing,
device classification, and per-device config/ledger migration + accessors.

Pure functions only — no SSH, no I/O side effects at import. Unit-tested.
"""

import json
import os
import re
from pathlib import Path

# ============================================================
# IMEI math
# ============================================================

def luhn_check_digit(body14: str) -> int:
    """Compute the Luhn check digit for the 14-digit IMEI body."""
    total = 0
    for i, ch in enumerate(reversed(body14)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def luhn_valid(imei: str) -> bool:
    return len(imei) == 15 and imei.isdigit() and luhn_check_digit(imei[:14]) == int(imei[14])


def make_imei(tac: str, serial6: str) -> str:
    body = tac + serial6
    return body + str(luhn_check_digit(body))


def generate_unique(tac: str, used: set, rng_seed: int) -> str:
    """Generate a valid IMEI for `tac`, absent from `used`. Deterministic given
    the seed (testable); real callers pass a time-derived seed."""
    for offset in range(1_000_000):
        serial = f"{(rng_seed + offset) % 1_000_000:06d}"
        imei = make_imei(tac, serial)
        if imei not in used:
            return imei
    raise RuntimeError("IMEI serial space exhausted against ledger (impossible in practice)")


# ============================================================
# AT parsing + device classification
# ============================================================

_IMEI_RE = re.compile(r"\b(\d{15})\b")


def parse_at_imei(payload: str) -> str:
    """Extract the first 15-digit IMEI from raw AT output (gl_modem text or the
    `data` field of a ubus modem.CPU.AT reply). Returns "" if none present."""
    m = _IMEI_RE.search(payload or "")
    return m.group(1) if m else ""


# model-substring -> device id. Ordered longest/most-specific first.
_MODEM_SIGNATURES = (
    ("RG650", "e5800"),
    ("EG25", "xe300"),
)


def classify_modem(model: str) -> "str | None":
    up = (model or "").upper()
    for needle, device in _MODEM_SIGNATURES:
        if needle in up:
            return device
    return None


def probe_hosts(hosts_by_device: dict, probe_fn) -> "tuple | None":
    """Find which configured router is live when devices sit on different IPs.

    `hosts_by_device` is {device_id: host}; `probe_fn(host)` returns that host's
    modem-model string, or "" if unreachable/unknown -- probe_fn owns its own
    error handling and must not raise. Returns `(classified_device, host)` for the
    first host whose model classifies to a known device, else None. The device is
    taken from the live model via `classify_modem`, not the dict key, so a
    mislabelled host self-corrects.
    """
    for _device, host in hosts_by_device.items():
        device = classify_modem(probe_fn(host))
        if device:
            return (device, host)
    return None


# ============================================================
# Config (per-device)
# ============================================================

def migrate_config(cfg: dict) -> dict:
    """Return cfg in the per-device shape. A legacy flat {tac,factory_imei} maps
    to devices.xe300 (gl_modem transport). Already-migrated input is returned
    unchanged."""
    if "devices" in cfg:
        return cfg
    return {"devices": {"xe300": {
        "transport": "gl_modem",
        "host": "192.168.8.1",
        "tac": cfg["tac"],
        "factory_imei": cfg["factory_imei"],
    }}}


def device_config(cfg: dict, device: str) -> dict:
    devices = cfg.get("devices", {})
    if device not in devices:
        raise KeyError(f"no config entry for device '{device}'")
    return devices[device]


# ============================================================
# Ledger (per-device, shared used[])
# ============================================================

def migrate_ledger(led: dict) -> dict:
    """Return led in the per-device shape. A legacy flat ledger
    {tac,current,history,used} moves current/history/tac under devices.xe300 and
    lifts used[] to the top. Any OTHER top-level keys (e.g. `_note`) are carried
    through unchanged. Already-migrated input is returned unchanged."""
    if "devices" in led:
        return led
    out = {k: v for k, v in led.items()
           if k not in ("tac", "current", "history", "used")}
    out["devices"] = {"xe300": {
        "tac": led.get("tac", ""),
        "current": led.get("current"),
        "history": led.get("history", []),
    }}
    out["used"] = led.get("used", [])
    return out


def device_ledger(led: dict, device: str, tac: str) -> dict:
    """Return devices[device], initialising an empty entry (with tac) if absent."""
    devices = led.setdefault("devices", {})
    if device not in devices:
        devices[device] = {"tac": tac, "current": None, "history": []}
    return devices[device]


def load_ledger(path: Path) -> dict:
    if Path(path).exists():
        return migrate_ledger(json.loads(Path(path).read_text(encoding="utf-8")))
    return {"devices": {}, "used": []}


def save_ledger(path: Path, led: dict) -> None:
    """Atomic write: serialise to a sibling .tmp, then os.replace()."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(led, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
