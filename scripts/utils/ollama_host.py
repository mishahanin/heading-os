"""Resolve which ollama instance to talk to, with a fallback to the local one.

Why this exists. On a WSL2 workspace the fast ollama may live on the Windows
side (its GPU is reachable there, not inside WSL). Reaching it means addressing
the host across the NAT boundary, and that gateway address is NOT stable: WSL
picks a new one on restart. A gateway hardcoded in config therefore works until
the first reboot and then silently breaks every background timer that uses it.

So callers name an INTENT, not an address:

    resolve_ollama_host("auto:11436")   # -> "http://172.30.48.1:11436"
    resolve_ollama_host()               # -> "http://localhost:11434"

`auto:<port>` resolves the current default gateway at call time and probes it.
Anything unreachable falls back to the local daemon, because slow indexing beats
no indexing. The probe is the point: a resolver that returns an address without
checking it would just move the silent breakage one layer down.

Sovereignty note: both candidates are on this machine. `auto` crosses the WSL
NAT boundary to the Windows side of the SAME laptop; it never reaches a network
host, and this module has no way to express one - only a gateway or a literal
the operator wrote themselves.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

LOCAL_HOST = "http://localhost:11434"
PROC_ROUTE = "/proc/net/route"

# Kept short on purpose: this runs before real work, in daemons that must not
# hang when the Windows side is simply not running.
DEFAULT_PROBE_TIMEOUT = 2.0


def read_default_gateway(route_path: str = PROC_ROUTE) -> str | None:
    """Return the IPv4 default gateway, or None when it cannot be determined.

    Parses /proc/net/route rather than shelling out to `ip route`, so there is
    no subprocess and nothing to quote. The Gateway column is a little-endian
    hex word; the default route is the row whose Destination is all zeroes.
    """
    try:
        with open(route_path, encoding="utf-8") as fh:
            rows = fh.read().splitlines()
    except OSError:
        return None

    for line in rows[1:]:                      # first line is the header
        fields = line.split()
        if len(fields) < 3:
            continue
        destination, gateway = fields[1], fields[2]
        if destination != "00000000":
            continue
        try:
            packed = struct.pack("<L", int(gateway, 16))
        except (ValueError, struct.error):
            continue
        return socket.inet_ntoa(packed)
    return None


def is_http_url(value: str) -> bool:
    """True for an http/https URL with a host part.

    The candidate reaches here from an environment variable or a config file,
    and `urlopen` honours whatever scheme it is handed - `file:///etc/passwd`
    would be opened and read. Only http(s) can ever be an ollama endpoint, so
    everything else is rejected before it reaches the opener.
    """
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def probe(host: str, timeout: float = DEFAULT_PROBE_TIMEOUT) -> bool:
    """True when an ollama instance answers /api/version at `host`."""
    if not is_http_url(host):
        return False
    url = f"{host.rstrip('/')}/api/version"
    try:
        # Scheme is checked immediately above, so the opener only ever sees
        # http/https here.
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False
    return "version" in payload


def candidate_url(preferred: str | None) -> str | None:
    """Turn a preference into the address it names, WITHOUT probing it.

    Returns None when there is no usable preference: an empty value, an `auto`
    whose gateway cannot be read, or anything that is not an http(s) URL.

    Split out of `resolve_ollama_host` so a caller can tell "no accelerated host
    is configured" from "one is configured and is down". The resolver folds both
    into the same local fallback, which is right for the callers that only want
    a working endpoint and useless for a monitor - that fold is exactly how a
    16-hour outage of the Windows-side daemon went unreported on 2026-08-21.
    """
    wanted = (preferred or "").strip()
    if not wanted:
        return None

    if wanted.startswith("auto"):
        _, _, port = wanted.partition(":")
        port = port.strip() or "11434"
        gateway = read_default_gateway()
        if gateway is None:
            return None
        return f"http://{gateway}:{port}"

    candidate = wanted.rstrip("/")
    return candidate if is_http_url(candidate) else None


def resolve_ollama_host(
    preferred: str | None = None,
    *,
    env_var: str = "HEADING_OS_OLLAMA_HOST",
    default: str = LOCAL_HOST,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    verbose: bool = True,
) -> str:
    """Pick an ollama base URL: the wanted one if it answers, else `default`.

    Args:
        preferred: `auto:<port>` to resolve the current gateway, a literal base
            URL, or None to read `env_var` (and fall back to `default`).
        env_var: environment variable consulted when `preferred` is None.
        default: what to use when nothing else is reachable. Never probed - it
            is the fallback, and probing it would only add a delay to the path
            that is already the last resort.
        probe_timeout: seconds to wait for /api/version.
        verbose: write one line to stderr when a preference is dropped, so a
            silent downgrade cannot masquerade as a fast run.

    Returns:
        A base URL with no trailing slash.
    """
    wanted = preferred if preferred is not None else os.environ.get(env_var, "")
    wanted = (wanted or "").strip()
    if not wanted:
        return default.rstrip("/")

    candidate = candidate_url(wanted)
    if candidate is None:
        reason = (
            "cannot read default gateway" if wanted.startswith("auto")
            else f"{wanted.rstrip('/')!r} is not an http(s) URL"
        )
        _warn(verbose, f"ollama: {reason}, using {default}")
        return default.rstrip("/")

    if probe(candidate, timeout=probe_timeout):
        return candidate

    _warn(verbose, f"ollama: {candidate} unreachable, falling back to {default}")
    return default.rstrip("/")


def _warn(verbose: bool, message: str) -> None:
    if verbose:
        sys.stderr.write(message + "\n")
