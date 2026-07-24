#!/usr/bin/env python3
"""
modem-tune.py -- IMEI reconfiguration engine for GL.iNet travel routers.

Drives the router's cellular modem over SSH -- gl_modem AT bridge on the
GL-XE300 (Quectel EG25-G), ubus modem.CPU.AT on the GL-E5800 (Quectel
RG650V-EU) -- to read, generate, and change the reported IMEI. Generated
IMEIs are valid values for the configured device class (TAC + 6-digit serial
+ Luhn check) and are deduplicated against a shared ledger so a value is
never reused. Per-device identity (transport, host, TAC, factory IMEI) is
read from config (config/modem.json; engine ships scripts/modem.example.json).

The device is auto-detected from the live modem unless --device is given
explicitly.

Subcommands (the /modem-tune skill calls these in order, with a human
confirmation gate before reset):

  detect              Identify the connected device. Read-only.
  status              Read live IMEI(s) + SIM/registration/signal. Read-only.
  generate            Propose one fresh unique IMEI. No SSH, no ledger write.
  apply --imei X      Record old IMEI to ledger history, send AT+EGMR, expect OK.
  verify --expect X   Read live IMEI, confirm it matches X, mark ledger verified.
  reset               Full router reboot.
  revert              Apply the factory IMEI (from config/modem.json).

Every subcommand accepts --device {xe300,e5800}; omit it to auto-detect.

Credentials come from .env (gitignored): MODEM_HOST, MODEM_USER, MODEM_SSH_PASSWORD.

Usage:
  python3 scripts/modem-tune.py detect
  python3 scripts/modem-tune.py status
  python3 scripts/modem-tune.py generate --device e5800
  python3 scripts/modem-tune.py apply --imei <15-digit-imei>
  python3 scripts/modem-tune.py reset
  python3 scripts/modem-tune.py verify --expect <15-digit-imei>
  python3 scripts/modem-tune.py revert
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_default_tz, get_outputs_dir, resolve_config_with_example
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils import modem_core as mc
from scripts.utils.modem_ssh import ssh
from scripts.utils.modem_drivers import driver_for

LEDGER_PATH = get_outputs_dir() / "operations/reference/modem-imei-ledger.json"


def load_config() -> dict:
    raw = json.loads(resolve_config_with_example(
        "modem.json", Path(__file__).resolve().parent / "modem.example.json"
    ).read_text(encoding="utf-8"))
    return mc.migrate_config(raw)


def now_iso() -> str:
    from datetime import datetime
    return datetime.now(get_default_tz()).replace(microsecond=0).isoformat()


def _ssh_for(host: str):
    """Return an ssh callable bound to a specific host, so a device on its own
    LAN IP is reached correctly (routers can sit on different subnets)."""
    return lambda cmd, timeout=30: ssh(cmd, timeout, host=host)


def _probe_model(host: str) -> str:
    """Best-effort live modem-model string at `host`: try ubus (E5800) then
    gl_modem (XE300). Returns "" (never raises) if unreachable or unknown, so
    probe_hosts can treat that as a skip; transport failures are logged."""
    sfn = _ssh_for(host)
    try:
        info = sfn("ubus call cellular.modem info '{\"bus\":\"cpu\"}' 2>/dev/null", 15)
        m = re.search(r'"name"\s*:\s*"([^"]+)"', info)
        if m and m.group(1):
            return m.group(1)
    except Exception as exc:
        print(f"modem-tune: ubus probe at {host} failed: {exc}", file=sys.stderr)
    try:
        return sfn("gl_modem AT 'AT+CGMM' 2>/dev/null", 15)
    except Exception as exc:
        print(f"modem-tune: gl_modem probe at {host} failed: {exc}", file=sys.stderr)
        return ""


def _host_for(cfg: dict, device: str) -> str:
    """Per-device host from config, falling back to MODEM_HOST env then the
    GL.iNet factory-default LAN IP."""
    entry = cfg.get("devices", {}).get(device, {})
    return entry.get("host") or os.environ.get("MODEM_HOST") or "192.168.8.1"


def resolve_device(requested, cfg):
    """Return (device_id, host). An explicit --device uses that device's
    configured host; otherwise probe each configured host and classify the live
    modem so multi-router setups on different IPs resolve automatically."""
    devices = cfg.get("devices", {})
    if requested:
        host = _host_for(cfg, requested)
        shown = _probe_model(host).strip()[:40] or "unreachable"
        print(f"{CYAN}Detected device:{RESET} {BOLD}{requested}{RESET} "
              f"at {host} (modem: {shown})")
        return requested, host
    hosts_by_device = {d: v["host"] for d, v in devices.items() if v.get("host")}
    if hosts_by_device:
        found = mc.probe_hosts(hosts_by_device, _probe_model)
        if not found:
            tried = ", ".join(sorted(set(hosts_by_device.values())))
            print(f"{RED}Could not identify any configured router (tried: {tried}). "
                  f"Re-run with --device xe300|e5800.{RESET}", file=sys.stderr)
            sys.exit(2)
        device, host = found
        print(f"{CYAN}Detected device:{RESET} {BOLD}{device}{RESET} at {host}")
        return device, host
    # No configured hosts (bare example / legacy config): single env host.
    host = os.environ.get("MODEM_HOST") or "192.168.8.1"
    device = mc.classify_modem(_probe_model(host))
    if not device:
        print(f"{RED}Could not identify modem at {host}. "
              f"Re-run with --device xe300|e5800.{RESET}", file=sys.stderr)
        sys.exit(2)
    print(f"{CYAN}Detected device:{RESET} {BOLD}{device}{RESET} at {host}")
    return device, host


# ============================================================
# Subcommands
# ============================================================

def _device_ctx(args):
    """Resolve device + host + driver + ledger. Config IS loaded (to pick the
    device's host); read-only commands still work with no config *entry* for the
    device -- the host then falls back to MODEM_HOST env. The driver's ssh is
    bound to the resolved host."""
    cfg = load_config()
    device, host = resolve_device(getattr(args, "device", None), cfg)
    drv = driver_for(device, _ssh_for(host))
    led = mc.load_ledger(LEDGER_PATH)
    return device, host, drv, led


def _require_cfg(device: str) -> dict:
    """Config entry for a mutating command; clean exit(2) if the device is
    unconfigured (rather than an uncaught KeyError)."""
    try:
        return mc.device_config(load_config(), device)
    except KeyError:
        print(f"{RED}No config entry for device '{device}' in config/modem.json. "
              f"Add it before generate/apply/revert.{RESET}", file=sys.stderr)
        sys.exit(2)


def cmd_detect(args) -> int:
    resolve_device(getattr(args, "device", None), load_config())
    return 0


def cmd_status(args) -> int:
    device, _host, drv, led = _device_ctx(args)
    print(f"{CYAN}Reading modem state ({device})...{RESET}")
    st = drv.read_status()
    for entry in st.get("imeis", []):
        imei = entry.get("imei", "")
        badge = f"{GREEN}valid{RESET}" if mc.luhn_valid(imei) else f"{RED}INVALID{RESET}"
        print(f"{BOLD}IMEI slot {entry.get('slot','?')}:{RESET} {imei}   Luhn: {badge}")
    if "sims" in st:
        for sim in st.get("sims", []):
            print(f"{BOLD}SIM {sim.get('slot','?')}:{RESET} {sim.get('carrier','?')}")
    elif st.get("cpin") is not None:
        # XE300 has no ubus SIM listing; read_status instead returns the raw
        # +CPIN/+COPS/+CSQ AT replies -- surface them the way the pre-refactor
        # single-device cmd_status did.
        cpin, cops, csq = st.get("cpin", ""), st.get("cops", ""), st.get("csq", "")
        sim = "READY" if "READY" in cpin else cpin.replace("\n", " ").strip()
        print(f"{BOLD}SIM:{RESET}  {sim}")
        op = re.search(r'\+COPS:[^\n]*', cops)
        print(f"{BOLD}Net:{RESET}  {op.group(0) if op else cops.strip()}")
        sig = re.search(r'\+CSQ:[^\n]*', csq)
        print(f"{BOLD}CSQ:{RESET}  {sig.group(0) if sig else csq.strip()}")
    dled = mc.device_ledger(led, device, "")   # tac irrelevant for a read-only view
    if dled.get("current"):
        print(f"{GRAY}Ledger current: {dled['current'].get('imei')} "
              f"(verified={dled['current'].get('verified')}){RESET}")
    return 0


def cmd_generate(args) -> int:
    device, _host, drv, led = _device_ctx(args)
    cfg = _require_cfg(device)
    used = set(led.get("used", []))
    seed = int(time.time() * 1000) % 1_000_000
    imei = mc.generate_unique(cfg["tac"], used, seed)
    print(imei)
    print(f"{GRAY}TAC {cfg['tac']} ({device}), Luhn valid, unique vs {len(used)} "
          f"ledger entries.{RESET}", file=sys.stderr)
    return 0


def _apply_imei(device, drv, led, cfg, target: str, allow_used: bool) -> int:
    dled = mc.device_ledger(led, device, cfg["tac"])
    if not mc.luhn_valid(target):
        print(f"{RED}Refusing: {target} is not a valid Luhn IMEI.{RESET}", file=sys.stderr)
        return 2
    if not allow_used and target in set(led.get("used", [])):
        print(f"{RED}Refusing: {target} already in ledger (never-repeat).{RESET}", file=sys.stderr)
        return 2
    old = drv.read_imei()
    print(f"{CYAN}Old IMEI:{RESET} {old or '(unreadable)'}\n{CYAN}New IMEI:{RESET} {target}")
    ts = now_iso()
    if old:
        prev = dled.get("current") or {}
        dled.setdefault("history", []).append(
            {"imei": old, "applied_at": prev.get("applied_at"), "replaced_at": ts})
        if old not in led.setdefault("used", []):
            led["used"].append(old)
    ok, raw = drv.send_egmr(target)
    if not ok:
        print(f"{RED}AT+EGMR did not return OK:{RESET}\n{raw}", file=sys.stderr)
        mc.save_ledger(LEDGER_PATH, led)
        return 1
    dled["current"] = {"imei": target, "applied_at": ts,
                       "luhn_valid": mc.luhn_valid(target), "verified": False}
    if target not in led.setdefault("used", []):
        led["used"].append(target)
    mc.save_ledger(LEDGER_PATH, led)
    print(f"{GREEN}AT+EGMR OK.{RESET} IMEI staged. Reset required.")
    return 0


def cmd_apply(args) -> int:
    device, _host, drv, led = _device_ctx(args)
    cfg = _require_cfg(device)
    return _apply_imei(device, drv, led, cfg, args.imei, allow_used=False)


def cmd_revert(args) -> int:
    device, _host, drv, led = _device_ctx(args)
    cfg = _require_cfg(device)
    print(f"{YELLOW}Reverting {device} to factory IMEI {cfg['factory_imei']}...{RESET}")
    return _apply_imei(device, drv, led, cfg, cfg["factory_imei"], allow_used=True)


def cmd_reset(args) -> int:
    device, host, drv, _ = _device_ctx(args)
    print(f"{YELLOW}Full router reboot ({device}); modem-ready can take 2-3 min...{RESET}")
    _ssh_for(host)("reboot", timeout=15)
    back = _wait_for_router(drv, settle=240)
    print(f"{GREEN if back else YELLOW}Router "
          f"{'back online' if back else 'reboot issued (modem not yet readable)'}.{RESET}")
    return 0


def cmd_verify(args) -> int:
    device, _host, drv, led = _device_ctx(args)
    expect, live = args.expect, ""
    for _ in range(30):
        live = drv.read_imei()
        if live == expect:
            break
        time.sleep(5)
    dled = mc.device_ledger(led, device, "")
    if live == expect:
        if dled.get("current", {}).get("imei") == expect:
            dled["current"]["verified"] = True
            mc.save_ledger(LEDGER_PATH, led)
        print(f"{GREEN}Verified: live IMEI is {live}.{RESET}")
        return 0
    print(f"{RED}Mismatch: expected {expect}, modem reports {live or '(unreadable)'}.{RESET}",
          file=sys.stderr)
    return 1


def _wait_for_router(drv, settle: int) -> bool:
    """Block until the modem AT bridge answers again (after a full reboot).

    Returns True once a live IMEI is readable, False if `settle` seconds elapse
    first. SSH is refused while the router is still booting; that raises and is
    swallowed so the poll keeps retrying.
    """
    deadline = time.time() + settle
    time.sleep(35)
    while time.time() < deadline:
        try:
            if drv.read_imei():
                return True
        except Exception as exc:
            print(f"modem-tune: IMEI poll attempt failed: {exc}", file=sys.stderr)
        time.sleep(5)
    return False


# ============================================================
# CLI
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="GL.iNet IMEI reconfiguration engine (XE300 + E5800).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_device(p):
        p.add_argument("--device", choices=["xe300", "e5800"], default=None,
                       help="target device (auto-detected from the live modem if omitted)")

    for name, help_ in [("detect", "identify the connected device"),
                        ("status", "read live IMEI(s) + SIM (read-only)"),
                        ("generate", "propose a fresh unique IMEI (no SSH)"),
                        ("reset", "full router reboot"),
                        ("revert", "apply the factory IMEI")]:
        add_device(sub.add_parser(name, help=help_))

    ap_apply = sub.add_parser("apply", help="record old IMEI + send AT+EGMR")
    add_device(ap_apply); ap_apply.add_argument("--imei", required=True)

    ap_verify = sub.add_parser("verify", help="confirm live IMEI matches --expect")
    add_device(ap_verify); ap_verify.add_argument("--expect", required=True)

    args = ap.parse_args()
    return {"detect": cmd_detect, "status": cmd_status, "generate": cmd_generate,
            "apply": cmd_apply, "reset": cmd_reset, "verify": cmd_verify,
            "revert": cmd_revert}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
