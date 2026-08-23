#!/usr/bin/env python3
"""Authenticated OSINT API calls for /osint-advanced, without a shell.

Replaces the raw `curl` lines the reference files used to prescribe. Three
things were wrong with those, all found by the 2026-08-23 audit:

1. **The sanctions call could not run at all.** `streams-deep-osint.md` told the
   assistant to reach `POST https://api.opensanctions.org/match/default` with a
   JSON body and an `Authorization` header *via WebFetch*. WebFetch issues a GET
   and controls no headers. The stream is MANDATORY and the skill's NEVER-list
   forbids "a sanctions CLEAR without actually querying the databases", so the
   documented primary path produced exactly the outcome the skill bans.
2. **`curl` is not granted.** `allowed-tools` is
   `WebSearch, WebFetch, Read, Bash(python3:*)`. Every other API line was a
   `curl` invocation, so each one stalls on a permission prompt or fails.
3. **The API key was interpolated into a shell command line.** The pattern was
   `curl -H "x-apikey: $(python3 -c '...load_api_key...')"`, which puts a live
   credential into the process table and into the session transcript. Here the
   key is read in-process and never leaves it.

stdlib only (`urllib.request`) - no new dependency, TLS verification on.

Usage:
    python3 .claude/skills/osint-advanced/scripts/osint_api.py sanctions --name "Jane Roe"
    python3 .claude/skills/osint-advanced/scripts/osint_api.py sanctions --name "Acme Ltd" --schema Company
    python3 .claude/skills/osint-advanced/scripts/osint_api.py hunter --domain example.com
    python3 .claude/skills/osint-advanced/scripts/osint_api.py hunter --domain example.com --first Jane --last Roe
    python3 .claude/skills/osint-advanced/scripts/osint_api.py hibp --account name@example.com
    python3 .claude/skills/osint-advanced/scripts/osint_api.py hibp --account example.com --kind breacheddomain
    python3 .claude/skills/osint-advanced/scripts/osint_api.py virustotal --domain example.com
    python3 .claude/skills/osint-advanced/scripts/osint_api.py dehashed --query "email:name@example.com"

Every subcommand prints one JSON object on stdout:

    {"source": "...", "ok": true,  "status": 200, "data": {...}}
    {"source": "...", "ok": false, "status": 401, "error": "..."}

`ok: false` is a REPORTABLE state, not a silent zero: a stream that could not
run must be named in the report as not-run, never rendered as CLEAR. Exit code
is 0 on a successful call, 2 on an API or credential failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The workspace root, six levels up: scripts/ -> osint-advanced/ -> skills/ ->
# .claude/ -> <workspace>.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts.utils.api import load_api_key  # noqa: E402

USER_AGENT = "31C-OSINT"
TIMEOUT = 30


def _request(
    source: str,
    url: str,
    headers: dict[str, str],
    body: dict | None = None,
) -> tuple[dict, int]:
    """Issue one request and return (payload, exit_code). Never raises."""
    data = None
    headers = {"user-agent": USER_AGENT, **headers}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"

    # Assert the scheme rather than suppressing the warning about it. Every
    # caller passes an https endpoint from a fixed table, but a `file:` or
    # `ftp:` URL reaching urlopen reads a local path and reports it as an API
    # response, so the guard holds regardless of where the URL came from.
    if not url.startswith(("https://", "http://")):
        return ({"source": source, "ok": False, "status": None,
                 "error": f"refusing a non-HTTP URL: {url!r}"}, 2)
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - scheme guarded above
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - same guard
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        # HTTPError before URLError: it is a subclass.
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return (
            {"source": source, "ok": False, "status": exc.code,
             "error": f"HTTP {exc.code}: {detail or exc.reason}"},
            2,
        )
    except urllib.error.URLError as exc:
        return (
            {"source": source, "ok": False, "status": None,
             "error": f"network error: {exc.reason}"},
            2,
        )
    except TimeoutError:
        return (
            {"source": source, "ok": False, "status": None,
             "error": f"timed out after {TIMEOUT}s"},
            2,
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return (
            {"source": source, "ok": False, "status": status,
             "error": f"response was not JSON: {raw[:300]}"},
            2,
        )
    return ({"source": source, "ok": True, "status": status, "data": parsed}, 0)


def _key(source: str, name: str) -> tuple[str | None, dict | None]:
    """Load a credential, returning a reportable payload instead of raising."""
    try:
        return load_api_key(name), None
    except Exception as exc:  # load_api_key raises on a missing key
        return None, {
            "source": source, "ok": False, "status": None,
            "error": f"{name} is not set: {exc}",
        }


# ---------------------------------------------------------------- sanctions

def cmd_sanctions(args) -> tuple[dict, int]:
    key, failure = _key("opensanctions", "OPENSANCTIONS_API_KEY")
    if failure:
        return failure, 2
    body = {
        "queries": {
            "q1": {
                "schema": args.schema,
                "properties": {"name": [args.name]},
            }
        }
    }
    return _request(
        "opensanctions",
        "https://api.opensanctions.org/match/default",
        {"Authorization": f"ApiKey {key}"},
        body=body,
    )


# ------------------------------------------------------------------- hunter

def cmd_hunter(args) -> tuple[dict, int]:
    key, failure = _key("hunter", "HUNTER_API_KEY")
    if failure:
        return failure, 2
    if args.first or args.last:
        params = {"domain": args.domain, "first_name": args.first or "",
                  "last_name": args.last or "", "api_key": key}
        path = "email-finder"
    else:
        params = {"domain": args.domain, "api_key": key}
        path = "domain-search"
    url = f"https://api.hunter.io/v2/{path}?" + urllib.parse.urlencode(params)
    return _request("hunter", url, {})


# --------------------------------------------------------------------- hibp

HIBP_KINDS = ("breachedaccount", "pasteaccount", "breacheddomain",
              "stealerlogsbyemail")


def cmd_hibp(args) -> tuple[dict, int]:
    key, failure = _key("hibp", "HIBP_API_KEY")
    if failure:
        return failure, 2
    account = urllib.parse.quote(args.account, safe="")
    url = f"https://haveibeenpwned.com/api/v3/{args.kind}/{account}"
    if args.kind == "breachedaccount":
        url += "?truncateResponse=false"
    return _request("hibp", url, {"hibp-api-key": key})


# -------------------------------------------------------------- virustotal

def cmd_virustotal(args) -> tuple[dict, int]:
    key, failure = _key("virustotal", "VIRUSTOTAL_API_KEY")
    if failure:
        return failure, 2
    if args.domain:
        url = f"https://www.virustotal.com/api/v3/domains/{urllib.parse.quote(args.domain, safe='')}"
    elif args.ip:
        url = f"https://www.virustotal.com/api/v3/ip_addresses/{urllib.parse.quote(args.ip, safe='')}"
    else:
        url = ("https://www.virustotal.com/api/v3/search?"
               + urllib.parse.urlencode({"query": args.search}))
    return _request("virustotal", url, {"x-apikey": key})


# ----------------------------------------------------------------- dehashed

def cmd_dehashed(args) -> tuple[dict, int]:
    key, failure = _key("dehashed", "DEHASHED_API_KEY")
    if failure:
        return failure, 2
    body = {"query": args.query, "size": args.size, "page": args.page,
            "de_dupe": True}
    return _request(
        "dehashed",
        "https://api.dehashed.com/v2/search",
        {"Dehashed-Api-Key": key},
        body=body,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticated OSINT API calls for /osint-advanced.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sanctions", help="OpenSanctions /match/default (POST)")
    p.add_argument("--name", required=True)
    p.add_argument("--schema", default="Person",
                   choices=["Person", "Company", "Organization", "LegalEntity"])
    p.set_defaults(func=cmd_sanctions)

    p = sub.add_parser("hunter", help="Hunter.io domain-search / email-finder")
    p.add_argument("--domain", required=True)
    p.add_argument("--first")
    p.add_argument("--last")
    p.set_defaults(func=cmd_hunter)

    p = sub.add_parser("hibp", help="Have I Been Pwned v3")
    p.add_argument("--account", required=True)
    p.add_argument("--kind", default="breachedaccount", choices=HIBP_KINDS)
    p.set_defaults(func=cmd_hibp)

    p = sub.add_parser("virustotal", help="VirusTotal v3")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--domain")
    group.add_argument("--ip")
    group.add_argument("--search")
    p.set_defaults(func=cmd_virustotal)

    p = sub.add_parser("dehashed", help="DeHashed v2 search (POST)")
    p.add_argument("--query", required=True)
    p.add_argument("--size", type=int, default=100)
    p.add_argument("--page", type=int, default=1)
    p.set_defaults(func=cmd_dehashed)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload, code = args.func(args)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
