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
from scripts.utils.modem_drivers import ModemReadError, driver_for


def ledger_path():
    """Resolved at call time, never at import.

    `get_outputs_dir()` reads `HEADING_OS_DATA` on every call, so it follows
    the environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the data
    root still saved the ledger into the operator's real overlay.
    """
    return get_outputs_dir() / "operations/reference/modem-imei-ledger.json"


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


def resolve_device_offline(requested, cfg) -> str:
    """Return the device id from CONFIG ALONE. Never opens SSH.

    `resolve_device` above probes the router in BOTH of its branches -- even
    with an explicit `--device`, where the probe only fills in the "(modem: ...)"
    text of a display line. `generate` needs the device's TAC and the ledger and
    nothing else, yet it went through that path, so the command documented as
    "No SSH" stalled through two 15-second probe timeouts on an unreachable
    router before printing a number it had all along. The docstring stated the
    intent; the code was the wrong side of the disagreement.

    With no `--device` and exactly one configured device, that one is used --
    there is nothing to disambiguate. With several, it refuses and names them,
    because guessing here would silently mint an IMEI against the wrong TAC.
    """
    devices = cfg.get("devices", {})
    if requested:
        return requested
    named = sorted(devices)
    if len(named) == 1:
        return named[0]
    if not named:
        print(f"{RED}No devices configured in config/modem.json. "
              f"Re-run with --device xe300|e5800.{RESET}", file=sys.stderr)
        sys.exit(2)
    print(f"{RED}Several devices are configured ({', '.join(named)}) and "
          f"generate does not probe to choose between them. "
          f"Re-run with --device <name>.{RESET}", file=sys.stderr)
    sys.exit(2)


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
    led = mc.load_ledger(ledger_path())
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
    try:
        st = drv.read_status()
    except ModemReadError as exc:
        # A device that could not be read is not a device with nothing to
        # report. This printed the "Reading modem state" line, then nothing at
        # all, and exited 0 - because the driver's empty dict rendered exactly
        # like a healthy modem holding no IMEIs and no SIMs.
        print(f"{RED}Could not read the modem:{RESET} {exc}", file=sys.stderr)
        return 2
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
    # No `_device_ctx`: that resolves a HOST and builds an ssh-bound driver, and
    # this command touches neither. See `resolve_device_offline`.
    device = resolve_device_offline(getattr(args, "device", None), load_config())
    cfg = _require_cfg(device)
    led = mc.load_ledger(ledger_path())
    print(f"{CYAN}Device:{RESET} {BOLD}{device}{RESET} {GRAY}(from config; "
          f"generate does not contact the router){RESET}", file=sys.stderr)
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
    # A modem that cannot be READ must not be WRITTEN.
    #
    # `read_imei` used to answer "" for a dead channel, so this printed
    # "(unreadable)" and carried on to `send_egmr` against a device nobody had
    # reached. Since 2026-08-30 the E5800 driver raises `ModemReadError` on a
    # failed command, matching its ubus sibling, and the raise then escaped
    # `_apply_imei` as a traceback: measured with a driver raising
    # "No route to host", `_apply_imei` propagated it out of a file whose every
    # other exit is a code. `cmd_status` two hundred lines up already refuses
    # this way, with the same exit 2.
    #
    # Refusing is the substantive point, not the exit code. The old IMEI is
    # what this function files into `history` and into the never-repeat `used`
    # list; without it the ledger loses the record of what was replaced, and
    # the ledger is the only account of which IMEIs have been burned.
    try:
        old = drv.read_imei()
    except ModemReadError as exc:
        print(f"{RED}Refusing: could not read the current IMEI, so nothing is "
              f"written and the ledger keeps its record:{RESET} {exc}",
              file=sys.stderr)
        return 2
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
        mc.save_ledger(ledger_path(), led)
        return 1
    dled["current"] = {"imei": target, "applied_at": ts,
                       "luhn_valid": mc.luhn_valid(target), "verified": False}
    if target not in led.setdefault("used", []):
        led["used"].append(target)
    mc.save_ledger(ledger_path(), led)
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
    # The reboot is sent over the session it kills. A dropped session is NOT an
    # exception here -- `modem_ssh.ssh` runs `subprocess.run` without
    # `check=True`, so ssh's exit 255 comes back as a plain string. What does
    # raise is `subprocess.TimeoutExpired`, when the router drops TCP without a
    # FIN and ssh blocks past the 15s bound. That escaped uncaught and skipped
    # the wait below, so the command failed on the one router that rebooted
    # hardest. The reboot was very likely delivered either way, so report and
    # go on to wait for it.
    try:
        _ssh_for(host)("reboot", timeout=15)
    except Exception as exc:
        print(f"modem-tune: the reboot command did not return cleanly ({exc}); "
              f"waiting for the router anyway.", file=sys.stderr)
    back = _wait_for_router(drv, settle=240)
    print(f"{GREEN if back else YELLOW}Router "
          f"{'back online' if back else 'reboot issued (modem not yet readable)'}.{RESET}")
    return 0


def cmd_verify(args) -> int:
    device, _host, drv, led = _device_ctx(args)
    expect, live = args.expect, ""
    for _ in range(30):
        # Same guard as `_wait_for_router`, and for the same reason: this loop
        # runs in the post-reset window, when the AT bridge comes and goes. A
        # refused connection returns a string and retries fine, but a session
        # that hangs past the transport's `timeout` raises TimeoutExpired --
        # which escaped this loop as a traceback on attempt 1 of 30, throwing
        # away the other 29 and the ledger update they exist to reach.
        try:
            live = drv.read_imei()
        except Exception as exc:
            print(f"modem-tune: IMEI read attempt failed: {exc}", file=sys.stderr)
            live = ""
        if live == expect:
            break
        time.sleep(5)
    dled = mc.device_ledger(led, device, "")
    if live == expect:
        # `or {}`, not `get(k, {})`. `device_ledger` initialises a new device as
        # `{"tac": ..., "current": None, "history": []}`, so the key EXISTS with
        # the value None and the default is never used -- `.get("imei")` on it
        # raised AttributeError immediately after a SUCCESSFUL verify, on any
        # device with no applied IMEI yet. `_apply_imei` three lines up already
        # writes it the right way. Not in the audit report; found by the test
        # below.
        if (dled.get("current") or {}).get("imei") == expect:
            dled["current"]["verified"] = True
            mc.save_ledger(ledger_path(), led)
        print(f"{GREEN}Verified: live IMEI is {live}.{RESET}")
        return 0
    print(f"{RED}Mismatch: expected {expect}, modem reports {live or '(unreadable)'}.{RESET}",
          file=sys.stderr)
    return 1


def _wait_for_router(drv, settle: int) -> bool:
    """Block until the modem AT bridge answers again (after a full reboot).

    Returns True once a live IMEI is readable, False if `settle` seconds elapse
    first.

    What the guard below actually catches, corrected 2026-08-25 and again
    2026-08-30: `modem_ssh.ssh` runs `subprocess.run` without `check=True`, so
    a booting router's exit-255 stderr comes back as an ordinary string rather
    than an exception. The 2026-08-25 note concluded from that "a REFUSED
    connection does not raise", and half of that stopped being true on
    2026-08-30: the E5800 driver's `_at` now raises `ModemReadError` on a
    failed command, matching its ubus sibling, because returning "" made a dead
    channel indistinguishable from a modem holding no IMEI. So a refusal does
    reach here as an exception on that driver.

    The other raising cases are unchanged: a session that hangs past the
    transport's `timeout` (`subprocess.TimeoutExpired`), and whatever a
    driver's own parsing raises on a half-formed AT response. All of them are
    swallowed so the poll keeps retrying, which is right HERE and wrong in
    `_apply_imei`, where a read that failed means the write must not happen.
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
