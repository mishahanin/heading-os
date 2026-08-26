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
The probe is the point: a resolver that returns an address without checking it
would just move the silent breakage one layer down.

WHERE the preference comes from is `config/ollama-hosts.yaml`, gitignored and
machine-local, with one entry for `embed` and one for `generate`. It is a
separate file from the tracked configs because an address is a fact about one
computer: `auto:11434` names whatever answers at THIS machine's default gateway,
and a pin written into a tracked file refuses on every clone that is not this
laptop. No file at all is a valid setup and means the local daemon.

Two resolvers, because two jobs want opposite answers when nothing responds:

- `resolve_ollama_host` degrades to the local daemon. Right for GENERATION,
  where a slower model beats no model.
- `resolve_pinned_host` raises `OllamaHostUnavailable`. Right for EMBEDDING,
  where the operator pinned a machine on 2026-08-23 and a quiet substitution is
  the failure, not the mitigation. Both take a list as readily as a string, so
  one pin can name several ports on the same machine.

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

# Where THIS machine says its ollama lives. Gitignored, with a tracked
# `config/ollama-hosts.example.yaml` beside it that documents the shape and
# switches nothing on.
#
# It is a separate file because the pin is a fact about one laptop, not about
# the repository. It lived in `config/memory-index.yaml` for one day and that
# was a day too long: that file is tracked, `auto:11434` means "whatever answers
# at this clone's default gateway", and since a pin refuses instead of degrading,
# the shipped default made `memory-index build` fail on any clone that was not
# this WSL2 laptop -- while its own local ollama sat there working.
MACHINE_HOSTS_FILE = "config/ollama-hosts.yaml"

# One entry per job, because the two jobs want opposite answers when the pinned
# host is down: see `resolve_pinned_host` (embed, refuses) against
# `resolve_ollama_host` (generate, degrades).
MACHINE_HOST_ROLES = ("embed", "generate")

# Kept short on purpose: this runs before real work, in daemons that must not
# hang when the Windows side is simply not running.
DEFAULT_PROBE_TIMEOUT = 2.0


class OllamaHostUnavailable(RuntimeError):
    """No pinned ollama host answered, and degrading was not permitted."""


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
    # `isinstance`, not `in`. A 200 carrying a JSON SCALAR - `null`, a number, a
    # bool - parses fine and then `"version" in payload` raises TypeError, which
    # the clause above does not catch. The caller got a bare TypeError out of a
    # function whose entire contract is to answer True or False, so
    # `resolve_ollama_host` could not fall back to the local daemon and
    # `resolve_pinned_host` could not raise `OllamaHostUnavailable` naming what
    # it tried. Reproduced 2026-08-25 against a loopback server returning
    # `null`. `auto:<port>` invites exactly this: the WSL gateway address with
    # some other service on that port.
    return isinstance(payload, dict) and "version" in payload


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


def host_candidates(preferred) -> list[str]:
    """Every address a preference names, in order, WITHOUT probing any of them.

    `preferred` is a string or a list of strings. A list exists because one
    machine can serve on more than one port: the Windows Ollama desktop app
    binds its default 11434 unless it was launched with an explicit
    `OLLAMA_HOST`, and on 2026-08-23 an auto-update restart did exactly that
    while the pin still said 11436. Naming both ports makes that whole class of
    failure a non-event, and neither of them is the local daemon, so the pin
    still means one machine.

    Entries that cannot become an http(s) URL are dropped rather than raising:
    one typo must not disable the entries beside it. Duplicates collapse, so
    `["auto:11434", "http://<gateway>:11434"]` probes once.
    """
    values = preferred if isinstance(preferred, (list, tuple)) else [preferred]
    out: list[str] = []
    for value in values:
        url = candidate_url(value) if isinstance(value, str) else None
        if url and url not in out:
            out.append(url)
    return out


def machine_hosts(role: str, *, root=None) -> list[str]:
    """This machine's preference for `role`, as raw entries. [] when unset.

    Reads `config/ollama-hosts.yaml` (see `MACHINE_HOSTS_FILE`). An absent file
    means no accelerator here, which is the right default for a fresh clone, for
    CI, and for this laptop before its Windows side is running. Returns the
    entries UNRESOLVED so the caller picks its own resolver - refusing for
    embedding, degrading for generation.

    Args:
        role: one of `MACHINE_HOST_ROLES`.
        root: workspace root; resolved from the workspace when omitted.

    Raises:
        ValueError: unknown role. A typo must not read as "nothing configured".
    """
    from pathlib import Path

    import yaml

    from scripts.utils import yamlio

    if role not in MACHINE_HOST_ROLES:
        raise ValueError(
            f"unknown ollama host role {role!r}; expected one of {MACHINE_HOST_ROLES}"
        )

    if root is None:
        from scripts.utils.workspace import get_workspace_root

        root = get_workspace_root()

    path = Path(root) / MACHINE_HOSTS_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            payload = yamlio.safe_load(fh) or {}
    except FileNotFoundError:
        return []
    except (OSError, yaml.YAMLError) as exc:
        # Reported, never swallowed: a typo here silently unpins the machine,
        # and a silent unpin is the whole failure this arrangement exists to
        # prevent.
        sys.stderr.write(f"ollama: cannot read {MACHINE_HOSTS_FILE}: {exc}\n")
        return []

    if not isinstance(payload, dict):
        sys.stderr.write(f"ollama: {MACHINE_HOSTS_FILE} is not a mapping, ignoring it\n")
        return []

    value = payload.get(role)
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    # Non-strings are dropped rather than raising, for the same reason
    # `host_candidates` drops a bad URL: one bad row must not disable the good
    # ones beside it.
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def generation_host(
    *,
    root=None,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> str:
    """Where local GENERATION runs (`gemma3:4b`). A PIN: it refuses, never degrades.

    Precedence: `HEADING_OS_OLLAMA_HOST` (a one-off override) beats `generate:`
    in the machine file. Nothing set anywhere returns the local daemon UNPROBED,
    so a plain clone with one ollama works with no setup and pays no probe.

    Raises:
        OllamaHostUnavailable: a host is pinned and nothing it names answers.

    This refused to be a pin for about an hour on 2026-08-23, on the argument
    that a summary from the CPU copy of the same model is the same summary and
    the nightly run must produce a record. The operator closed the argument by
    removing its premise: there is no second copy. The ollama inside WSL is gone,
    every model this workspace uses lives on the Windows side, and a "fallback to
    the local daemon" now falls back to nothing at all. A refusal that names the
    dead host beats a connection error from a daemon that is not installed.

    The cost is stated rather than hidden: on a night the Windows side is asleep,
    the 03:00 chronicle build fails loudly and writes no record for that day.

    CALL THIS LAZILY, never at module scope: it probes.
    """
    override = os.environ.get("HEADING_OS_OLLAMA_HOST", "").strip()
    preference = override or machine_hosts("generate", root=root)
    if not preference:
        return LOCAL_HOST
    return resolve_pinned_host(preference, probe_timeout=probe_timeout)


def resolve_pinned_host(
    preferred,
    *,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> str:
    """The first pinned host that answers. Never degrades to the local daemon.

    Raises:
        OllamaHostUnavailable: nothing the preference names is reachable. The
            message lists every address tried, because "the embedder is down" is
            unactionable and "10.0.0.1:11434 and :11436 did not answer" is not.

    This is the embedding path. `resolve_ollama_host` below is the generation
    path and keeps the old degrade-to-local behaviour.
    """
    wanted = preferred.strip() if isinstance(preferred, str) else preferred
    if not wanted:
        # Nothing pinned. The local daemon is the default, and probing it here
        # would only turn "no accelerator configured" into a hard failure on
        # every machine that never had one - a public clone, or CI.
        return LOCAL_HOST

    candidates = host_candidates(wanted)
    if not candidates:
        raise OllamaHostUnavailable(
            f"no usable ollama host in the pin {preferred!r} "
            f"(an `auto:` pin needs a readable default gateway)"
        )
    if all(c == LOCAL_HOST for c in candidates):
        # Naming the local daemon is not a pin, it is the default written out.
        # Unprobed for the same reason as above; if it is down, the embed call
        # itself says so a moment later and says it better.
        return LOCAL_HOST

    for candidate in candidates:
        if probe(candidate, timeout=probe_timeout):
            return candidate
    raise OllamaHostUnavailable(
        "no pinned ollama host answered: " + ", ".join(candidates)
    )


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
    if isinstance(wanted, str):
        wanted = wanted.strip()
    if not wanted:
        return default.rstrip("/")

    candidates = host_candidates(wanted)
    if not candidates:
        reason = (
            "cannot read default gateway"
            if isinstance(wanted, str) and wanted.startswith("auto")
            else f"{wanted!r} is not an http(s) URL"
        )
        _warn(verbose, f"ollama: {reason}, using {default}")
        return default.rstrip("/")

    for candidate in candidates:
        if probe(candidate, timeout=probe_timeout):
            return candidate

    _warn(
        verbose,
        f"ollama: {', '.join(candidates)} unreachable, falling back to {default}",
    )
    return default.rstrip("/")


def _warn(verbose: bool, message: str) -> None:
    if verbose:
        sys.stderr.write(message + "\n")
