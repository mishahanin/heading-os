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
        p = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return "\n".join(l for l in out.splitlines()
                         if "Permanently added" not in l and "Warning: " not in l).strip()
    finally:
        try:
            os.unlink(askpass)
        except OSError as exc:
            # The askpass helper holds the router password in cleartext. A failed
            # unlink leaves that credential on disk, which is precisely the case
            # that must never pass silently.
            print(f"{RED}modem-ssh: could not remove the transient askpass file "
                  f"{askpass}: {exc}. Delete it by hand.{RESET}", file=sys.stderr)
