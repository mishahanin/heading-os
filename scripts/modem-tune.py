#!/usr/bin/env python3
"""
modem-tune.py -- IMEI reconfiguration engine for a GL.iNet GL-XE300 travel router.

Drives the Quectel EG25-G modem over SSH (gl_modem AT bridge) to read, generate,
and change the reported IMEI. Generated IMEIs are valid values for the configured
device class (TAC + 6-digit serial + Luhn check) and are deduplicated against a
ledger so a value is never reused. The device's TAC + factory IMEI are read from
config (config/modem.json; engine ships scripts/modem.example.json).

Subcommands (the /modem-tune skill calls these in order, with a human confirmation
gate before reset):

  status              Read live IMEI + SIM/registration/signal. Read-only.
  generate            Propose one fresh unique IMEI. No SSH, no ledger write.
  apply --imei X      Record old IMEI to ledger history, send AT+EGMR, expect OK.
  verify --expect X   Read live IMEI, confirm it matches X, mark ledger verified.
  reset [--modem]     Full router reboot by default; --modem does AT+CFUN=1,1 only.
  revert              Apply the factory IMEI (from config/modem.json).

Credentials come from .env (gitignored): MODEM_HOST, MODEM_USER, MODEM_SSH_PASSWORD.

Usage:
  python3 scripts/modem-tune.py status
  python3 scripts/modem-tune.py generate
  python3 scripts/modem-tune.py apply --imei <15-digit-imei>
  python3 scripts/modem-tune.py reset
  python3 scripts/modem-tune.py verify --expect <15-digit-imei>
  python3 scripts/modem-tune.py revert
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_default_tz, get_default_tz_name, get_outputs_dir, load_env, resolve_config_with_example
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET

# ============================================================
# Configuration
# ============================================================

# Device identity (TAC + factory IMEI) is per-instance DATA: lives in the data
# overlay at <data-root>/config/modem.json; the engine ships scripts/modem.example.json.
_MODEM_CFG = json.loads(resolve_config_with_example(
    "modem.json", Path(__file__).resolve().parent / "modem.example.json"
).read_text(encoding="utf-8"))
TAC = _MODEM_CFG["tac"]                 # Type Allocation Code (device class)
FACTORY_IMEI = _MODEM_CFG["factory_imei"]  # device's original IMEI (revert target)

LEDGER_PATH = get_outputs_dir() / "operations/reference/modem-imei-ledger.json"

# 15-digit IMEI matcher used to pull the value out of gl_modem AT output.
IMEI_RE = re.compile(r"\b(\d{15})\b")


def now_iso() -> str:
    return datetime.now(get_default_tz()).replace(microsecond=0).isoformat()


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


def make_imei(serial6: str) -> str:
    body = TAC + serial6
    return body + str(luhn_check_digit(body))


def generate_unique(used: set, rng_seed: int) -> str:
    """Generate a valid IMEI for the configured TAC, absent from `used`.

    Deterministic given the seed so the function is testable; the caller passes a
    time-derived seed for real runs. Walks serials forward from the seed until an
    unused value is found (the 1e6 serial space dwarfs the ledger, so this is O(1)
    in practice).
    """
    for offset in range(1_000_000):
        serial = f"{(rng_seed + offset) % 1_000_000:06d}"
        imei = make_imei(serial)
        if imei not in used:
            return imei
    raise RuntimeError("IMEI serial space exhausted against ledger (impossible in practice)")


# ============================================================
# Ledger
# ============================================================

def load_ledger() -> dict:
    import json
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    return {"tac": TAC, "current": None, "history": [], "used": []}


def save_ledger(led: dict) -> None:
    import json
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(led, indent=2) + "\n", encoding="utf-8")


# ============================================================
# SSH execution
# ============================================================

def _credentials() -> tuple:
    load_env()
    host = os.environ.get("MODEM_HOST", "192.168.8.1")
    user = os.environ.get("MODEM_USER", "root")
    pw = os.environ.get("MODEM_SSH_PASSWORD")
    if not pw:
        print(f"{RED}MODEM_SSH_PASSWORD not set in .env -- cannot authenticate.{RESET}",
              file=sys.stderr)
        sys.exit(2)
    return host, user, pw


def ssh(remote_cmd: str, timeout: int = 30) -> str:
    """Run a command on the router over SSH using the SSH_ASKPASS mechanism.

    The WSL host has neither sshpass nor non-interactive sudo, so the password is
    fed through a transient askpass helper (never written to a tracked file).
    Returns combined stdout+stderr with the host-key warning line stripped.
    """
    host, user, pw = _credentials()
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(f"#!/bin/bash\nprintf '%s' {_shquote(pw)}\n")
        askpass = fh.name
    os.chmod(askpass, 0o700)
    try:
        env = dict(os.environ,
                   SSH_ASKPASS=askpass, SSH_ASKPASS_REQUIRE="force", DISPLAY=":0")
        # This is a trusted LAN router we control and reboot frequently; each
        # reboot can regenerate its dropbear host key. Pinning the key would make
        # the rotation workflow fail on every reboot, so host-key checking is off
        # and known_hosts is discarded.
        cmd = [
            "setsid", "-w", "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=password",
            "-o", "NumberOfPasswordPrompts=1",
            "-o", f"ConnectTimeout={min(timeout, 20)}",
            f"{user}@{host}", remote_cmd,
        ]
        p = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return "\n".join(l for l in out.splitlines()
                         if "Permanently added" not in l and "Warning: " not in l).strip()
    finally:
        try:
            os.unlink(askpass)
        except OSError:
            pass


def _shquote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def at(command: str, timeout: int = 30) -> str:
    """Send one AT command via the router's gl_modem bridge."""
    return ssh(f"gl_modem AT {_shquote(command)}", timeout=timeout)


def read_live_imei() -> str:
    out = at("AT+GSN")
    m = IMEI_RE.search(out)
    return m.group(1) if m else ""


# ============================================================
# Subcommands
# ============================================================

def cmd_status(_args) -> int:
    print(f"{CYAN}Reading modem state...{RESET}")
    imei = read_live_imei()
    cpin = at('AT+CPIN?')
    cops = at('AT+COPS?')
    csq = at('AT+CSQ')
    valid = luhn_valid(imei)
    badge = f"{GREEN}valid{RESET}" if valid else f"{RED}INVALID (manual-entry typo?){RESET}"
    print(f"\n{BOLD}IMEI:{RESET} {imei}   Luhn: {badge}")
    sim = "READY" if "READY" in cpin else cpin.replace("\n", " ").strip()
    print(f"{BOLD}SIM:{RESET}  {sim}")
    op = re.search(r'\+COPS:[^\n]*', cops)
    print(f"{BOLD}Net:{RESET}  {op.group(0) if op else cops.strip()}")
    sig = re.search(r'\+CSQ:[^\n]*', csq)
    print(f"{BOLD}CSQ:{RESET}  {sig.group(0) if sig else csq.strip()}")
    led = load_ledger()
    if led.get("current"):
        print(f"{GRAY}Ledger current: {led['current'].get('imei')} "
              f"(verified={led['current'].get('verified')}){RESET}")
    return 0


def cmd_generate(_args) -> int:
    led = load_ledger()
    used = set(led.get("used", []))
    seed = int(time.time() * 1000) % 1_000_000
    imei = generate_unique(used, seed)
    print(imei)
    print(f"{GRAY}TAC {TAC} (iPhone 13 Pro Max), Luhn valid, unique vs "
          f"{len(used)} ledger entries.{RESET}", file=sys.stderr)
    return 0


def cmd_apply(args) -> int:
    new = args.imei
    if not luhn_valid(new):
        print(f"{RED}Refusing: {new} is not a valid 15-digit Luhn IMEI.{RESET}", file=sys.stderr)
        return 2
    led = load_ledger()
    if new in set(led.get("used", [])):
        print(f"{RED}Refusing: {new} already exists in the ledger (never-repeat rule).{RESET}",
              file=sys.stderr)
        return 2

    old = read_live_imei()
    print(f"{CYAN}Old IMEI:{RESET} {old or '(unreadable)'}")
    print(f"{CYAN}New IMEI:{RESET} {new}")

    # Record the outgoing IMEI BEFORE mutating the modem.
    ts = now_iso()
    if old:
        prev = led.get("current") or {}
        led.setdefault("history", []).append({
            "imei": old,
            "applied_at": prev.get("applied_at"),
            "replaced_at": ts,
        })
        led.setdefault("used", [])
        if old not in led["used"]:
            led["used"].append(old)

    out = at(f'AT+EGMR=1,7,"{new}"')
    if "OK" not in out:
        print(f"{RED}AT+EGMR did not return OK:{RESET}\n{out}", file=sys.stderr)
        save_ledger(led)   # persist the history entry we already recorded
        return 1

    led["current"] = {"imei": new, "applied_at": ts,
                      "luhn_valid": True, "verified": False}
    led.setdefault("used", [])
    if new not in led["used"]:
        led["used"].append(new)
    save_ledger(led)
    print(f"{GREEN}AT+EGMR OK.{RESET} IMEI staged. Reset required for it to take effect.")
    return 0


def cmd_reset(args) -> int:
    # Full router reboot is the default: AT+CFUN=1,1 proved unreliable on this
    # GL-XE300 (it re-enumerates the USB ports and the modem rarely picks up the
    # new IMEI), so the modem-only path is opt-in via --modem.
    if args.modem:
        print(f"{YELLOW}Modem reset (AT+CFUN=1,1) -- rarely takes on this device...{RESET}")
        at("AT+CFUN=1,1", timeout=20)
        time.sleep(45)
        print(f"{GREEN}Reset issued.{RESET}")
    else:
        print(f"{YELLOW}Full router reboot (cold boot to modem-ready can take 2-3 min)...{RESET}")
        ssh("reboot", timeout=15)
        # GL-XE300 cold boot + modem re-registration regularly exceeds 2 min; wait
        # generously so the modem AT bridge is back before the caller verifies.
        back = _wait_for_router(settle=240)
        print(f"{GREEN if back else YELLOW}Router "
              f"{'back online' if back else 'reboot issued (modem not yet readable)'}.{RESET}")
    return 0


def cmd_verify(args) -> int:
    expect = args.expect
    live = ""
    # Poll up to ~2.5 min: after a reset the modem AT bridge can stay unreadable
    # while the modem re-registers, even once SSH itself is back.
    for attempt in range(30):
        live = read_live_imei()
        if live == expect:
            break
        time.sleep(5)
    led = load_ledger()
    if live == expect:
        if led.get("current", {}).get("imei") == expect:
            led["current"]["verified"] = True
            save_ledger(led)
        print(f"{GREEN}Verified: live IMEI is {live}.{RESET}")
        return 0
    print(f"{RED}Mismatch: expected {expect}, modem reports {live or '(unreadable)'}.{RESET}",
          file=sys.stderr)
    return 1


def cmd_revert(_args) -> int:
    print(f"{YELLOW}Reverting to factory IMEI {FACTORY_IMEI}...{RESET}")
    # Factory IMEI is intentionally allowed even though it sits in used[]:
    # bypass the dedup guard by applying directly.
    led = load_ledger()
    old = read_live_imei()
    ts = now_iso()
    if old:
        prev = led.get("current") or {}
        led.setdefault("history", []).append(
            {"imei": old, "applied_at": prev.get("applied_at"), "replaced_at": ts})
        led.setdefault("used", [])
        if old not in led["used"]:
            led["used"].append(old)
    out = at(f'AT+EGMR=1,7,"{FACTORY_IMEI}"')
    if "OK" not in out:
        print(f"{RED}AT+EGMR did not return OK:{RESET}\n{out}", file=sys.stderr)
        save_ledger(led)
        return 1
    led["current"] = {"imei": FACTORY_IMEI, "applied_at": ts,
                      "luhn_valid": luhn_valid(FACTORY_IMEI), "verified": False}
    save_ledger(led)
    print(f"{GREEN}Factory IMEI staged.{RESET} Reset required.")
    return 0


def _wait_for_router(settle: int) -> bool:
    """Block until the modem AT bridge answers again (after a full reboot).

    Returns True once a live IMEI is readable, False if `settle` seconds elapse
    first. SSH is refused while the router is still booting; that raises and is
    swallowed so the poll keeps retrying.
    """
    deadline = time.time() + settle
    time.sleep(35)
    while time.time() < deadline:
        try:
            if read_live_imei():
                return True
        except Exception as exc:
            print(f"modem-tune: IMEI poll attempt failed: {exc}", file=sys.stderr)
        time.sleep(5)
    return False


# ============================================================
# CLI
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="GL.iNet GL-XE300 IMEI reconfiguration engine.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="read live IMEI + SIM/net/signal (read-only)")
    sub.add_parser("generate", help="propose a fresh unique IMEI (no SSH)")

    ap_apply = sub.add_parser("apply", help="record old IMEI + send AT+EGMR")
    ap_apply.add_argument("--imei", required=True)

    ap_reset = sub.add_parser("reset", help="full router reboot (default); --modem for AT+CFUN only")
    ap_reset.add_argument("--modem", action="store_true",
                          help="modem-only AT+CFUN=1,1 reset instead of a full reboot")

    ap_verify = sub.add_parser("verify", help="confirm live IMEI matches --expect")
    ap_verify.add_argument("--expect", required=True)

    sub.add_parser("revert", help="apply the factory IMEI")

    args = ap.parse_args()
    return {
        "status": cmd_status, "generate": cmd_generate, "apply": cmd_apply,
        "reset": cmd_reset, "verify": cmd_verify, "revert": cmd_revert,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
