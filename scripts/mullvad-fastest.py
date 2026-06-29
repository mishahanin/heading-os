#!/usr/bin/env python3
"""Find the fastest Mullvad WireGuard relay by ICMP latency.

Fetches the live relay list from Mullvad's public API, filters by
ownership/region, pings each relay in parallel, and prints the top N
sorted by median round-trip time. Full results are also written to JSON.

Usage
-----
    python scripts/mullvad-fastest.py
    python scripts/mullvad-fastest.py --region all --top 30
    python scripts/mullvad-fastest.py --countries ae,sa,tr,de,nl
    python scripts/mullvad-fastest.py --all-ownership --count 5

Caveat: if Mullvad is already connected, pings traverse the tunnel and
results reflect latency-through-current-exit, not true network latency.
The /mullvad skill handles this detection; direct callers should
disconnect first for unbiased measurements.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import platform
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_outputs_dir

MULLVAD_API = "https://api.mullvad.net/www/relays/wireguard/"

EU_ME_COUNTRIES = {
    "al", "at", "be", "ba", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr",
    "de", "gr", "hu", "is", "ie", "it", "lv", "lt", "lu", "mt", "md", "me",
    "nl", "mk", "no", "pl", "pt", "ro", "rs", "sk", "si", "es", "se", "ch",
    "ua", "gb",
    "ae", "bh", "il", "jo", "kw", "lb", "om", "qa", "sa", "tr", "eg",
}


def fetch_relays(timeout: int = 15) -> list[dict]:
    """Fetch the live WireGuard relay list from Mullvad."""
    req = urllib.request.Request(
        MULLVAD_API,
        headers={
            "User-Agent": "mullvad-fastest/1.1",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Mullvad API returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Mullvad API: {exc.reason}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected API payload type: {type(data).__name__}")
    return data


def ping_host(ip: str, count: int = 3, timeout_s: int = 2) -> list[float]:
    """Run system ping; return list of RTTs in ms (empty if all failed)."""
    if platform.system().lower() == "windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout_s * 1000), ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_s), ip]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=count * (timeout_s + 1) + 5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return []
    except (OSError, ValueError) as exc:
        print(f"{GRAY}  ping failed for {ip}: {exc}{RESET}", file=sys.stderr)
        return []
    return [float(m) for m in re.findall(r"time[=<]\s*([\d.]+)\s*ms", result.stdout)]


def benchmark(relay: dict, count: int) -> dict:
    """Ping a relay and return a result record."""
    ip = relay.get("ipv4_addr_in", "")
    rtts = ping_host(ip, count=count) if ip else []
    record = {
        "hostname": relay.get("hostname", ""),
        "ipv4": ip,
        "country_code": (relay.get("country_code") or "").lower(),
        "country": relay.get("country_name", ""),
        "city": relay.get("city_name", ""),
        "provider": relay.get("provider", ""),
        "owned": bool(relay.get("owned", False)),
        "active": bool(relay.get("active", True)),
        "bandwidth_gbps": relay.get("network_port_speed"),
        "daita": bool(relay.get("daita", False)),
        "samples": len(rtts),
    }
    if rtts:
        record["ping_min_ms"] = round(min(rtts), 2)
        record["ping_median_ms"] = round(statistics.median(rtts), 2)
        record["ping_max_ms"] = round(max(rtts), 2)
    else:
        record["ping_min_ms"] = None
        record["ping_median_ms"] = None
        record["ping_max_ms"] = None
    return record


def sort_key(record: dict) -> float:
    """Unreachable relays sort to the bottom."""
    med = record["ping_median_ms"]
    return float("inf") if med is None else med


def apply_filters(
    relays: list[dict],
    region: str,
    countries_override: str,
    owned_only: bool,
) -> list[dict]:
    """Filter by active status, ownership, and region."""
    out = [r for r in relays if r.get("active", True)]
    if owned_only:
        out = [r for r in out if r.get("owned", False)]
    if countries_override:
        allowed = {c.strip().lower() for c in countries_override.split(",") if c.strip()}
    elif region == "eu-me":
        allowed = EU_ME_COUNTRIES
    else:
        allowed = None
    if allowed is not None:
        out = [r for r in out if (r.get("country_code") or "").lower() in allowed]
    out = [r for r in out if r.get("ipv4_addr_in")]
    return out


def print_table(results: list[dict], top: int) -> None:
    header = (
        f"{'#':<4}{'Hostname':<22}{'City':<18}{'CC':<4}"
        f"{'Provider':<14}{'Gbps':<6}{'Median ms':>10}"
    )
    print()
    print(f"{BOLD}{header}{RESET}")
    print("-" * len(header))
    for i, r in enumerate(results[:top], start=1):
        med = r["ping_median_ms"]
        if med is None:
            med_str = f"{RED}     n/a{RESET}"
        elif i <= 3:
            med_str = f"{GREEN}{med:>8.2f}{RESET}"
        else:
            med_str = f"{med:>8.2f}"
        gbps = r.get("bandwidth_gbps")
        gbps_str = str(gbps) if gbps is not None else "?"
        rank_str = f"{CYAN}{i:<4}{RESET}" if i <= 3 else f"{i:<4}"
        print(
            f"{rank_str}"
            f"{r['hostname'][:21]:<22}"
            f"{(r['city'] or '')[:17]:<18}"
            f"{r['country_code'].upper():<4}"
            f"{(r['provider'] or '')[:13]:<14}"
            f"{gbps_str:<6}"
            f"{med_str:>10}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--region",
        default="eu-me",
        choices=["eu-me", "all"],
        help="Region pre-filter (default: eu-me = Europe + Middle East)",
    )
    parser.add_argument(
        "--countries",
        default="",
        help="Comma-separated ISO country codes; overrides --region",
    )
    parser.add_argument("--top", type=int, default=20, help="Rows to display (default: 20)")
    parser.add_argument("--workers", type=int, default=50, help="Parallel ping workers")
    parser.add_argument("--count", type=int, default=3, help="Pings per host (default: 3)")
    owner = parser.add_mutually_exclusive_group()
    owner.add_argument(
        "--owned-only",
        dest="owned_only",
        action="store_true",
        default=True,
        help="Only Mullvad-owned relays (default)",
    )
    owner.add_argument(
        "--all-ownership",
        dest="owned_only",
        action="store_false",
        help="Include rented relays",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Custom output JSON path (default: outputs/operations/mullvad_fastest_YYYY-MM-DD.json)",
    )
    args = parser.parse_args()

    print(f"{GRAY}Fetching relay list from {MULLVAD_API} ...{RESET}", file=sys.stderr)
    try:
        relays = fetch_relays()
    except RuntimeError as exc:
        print(f"{RED}Failed to fetch relay list: {exc}{RESET}", file=sys.stderr)
        return 1
    print(f"{GRAY}  Got {len(relays)} WireGuard relays total.{RESET}", file=sys.stderr)

    filtered = apply_filters(relays, args.region, args.countries, args.owned_only)
    print(f"{GRAY}  After filters: {len(filtered)} relays to test.{RESET}", file=sys.stderr)
    if not filtered:
        print(f"{YELLOW}No relays matched filters.{RESET}", file=sys.stderr)
        return 2

    print(
        f"{GRAY}Pinging {len(filtered)} hosts with {args.workers} workers "
        f"({args.count} pings each) ...{RESET}",
        file=sys.stderr,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(benchmark, r, args.count) for r in filtered]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    results.sort(key=sort_key)
    print_table(results, args.top)

    reachable = sum(1 for r in results if r["ping_median_ms"] is not None)
    fastest = results[0]
    print(
        f"\n{GREEN}{reachable}/{len(results)} relays responded. "
        f"Fastest: {fastest['hostname']} "
        f"({fastest['city']}, {fastest['country_code'].upper()}) "
        f"@ {fastest['ping_median_ms']} ms median{RESET}",
        file=sys.stderr,
    )

    if args.json_out:
        out_path = Path(args.json_out)
    else:
        out_dir = get_outputs_dir() / "operations"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"mullvad_fastest_{datetime.now().strftime('%Y-%m-%d')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "generated": datetime.now().isoformat(timespec="seconds"),
                "filter": {
                    "region": args.region,
                    "countries": args.countries,
                    "owned_only": args.owned_only,
                },
                "host_platform": platform.platform(),
                "total": len(results),
                "reachable": reachable,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{GRAY}Full results saved: {out_path}{RESET}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
