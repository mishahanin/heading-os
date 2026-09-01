#!/usr/bin/env python3
"""SSH transport for the modem-tune tool (password auth via transient askpass).

Shared by the XE300 and E5800 drivers. The WSL host has neither sshpass nor
non-interactive sudo, so the password is fed through a transient SSH_ASKPASS
helper, never written to a tracked file.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import load_env
from scripts.utils.colors import RED, RESET


def shquote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def credentials() -> tuple:
    load_env()
    host = os.environ.get("MODEM_HOST", "192.168.8.1")
    user = os.environ.get("MODEM_USER", "root")
    pw = os.environ.get("MODEM_SSH_PASSWORD")
    if not pw:
        print(f"{RED}MODEM_SSH_PASSWORD not set in .env -- cannot authenticate.{RESET}",
              file=sys.stderr)
        sys.exit(2)
    return host, user, pw


def ssh(remote_cmd: str, timeout: int = 30, host: str = None) -> str:
    """Run a command on the router over SSH using the SSH_ASKPASS mechanism.

    `host` overrides the credentials/env default when the caller targets a
    specific device (routers can sit on different LAN IPs). When None, falls back
    to MODEM_HOST from .env. Returns combined stdout+stderr with the host-key
    warning lines stripped.
    """
    default_host, user, pw = credentials()
    host = host or default_host
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(f"#!/bin/bash\nprintf '%s' {shquote(pw)}\n")
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
        # `text=True` alone decodes with the host locale and raises
        # UnicodeDecodeError on the first byte that does not fit. The raise
        # happens INSIDE `subprocess.run`, so it never reaches the drivers'
        # `except (json.JSONDecodeError, TypeError)` and comes out of
        # `modem-tune` as a traceback - in a module whose whole design is to
        # refuse by name rather than crash. The bytes here are a router's AT
        # output, which carries a carrier name in the modem's own charset; the
        # value being read out of it is a 15-digit IMEI and a final result code,
        # neither of which a replacement character can corrupt. Ask for UTF-8
        # explicitly rather than the locale, for the same reason the engine leak
        # wall does: the host's encoding is not a property of the router.
        p = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        # The noise filter belongs to the ssh CLIENT, which writes on stderr.
        # It used to run over stdout+stderr concatenated, so any line of genuine
        # router output carrying "Warning: " or "Permanently added" was deleted
        # before the drivers ever parsed it, with nothing recording the drop.
        # Concatenating first also glued the last stdout line to the first
        # stderr line when stdout did not end in a newline, putting real output
        # inside a line the filter could then judge. Filtering stderr alone, and
        # keeping stdout verbatim, removes both.
        out_lines = (p.stdout or "").splitlines()
        err_lines = [l for l in (p.stderr or "").splitlines()
                     if "Permanently added" not in l and "Warning: " not in l]
        return "\n".join(out_lines + err_lines).strip()
    finally:
        try:
            os.unlink(askpass)
        except OSError as exc:
            # The askpass helper holds the router password in cleartext. A failed
            # unlink leaves that credential on disk, which is precisely the case
            # that must never pass silently.
            print(f"{RED}modem-ssh: could not remove the transient askpass file "
                  f"{askpass}: {exc}. Delete it by hand.{RESET}", file=sys.stderr)
