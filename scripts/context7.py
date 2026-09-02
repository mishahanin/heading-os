#!/usr/bin/env python3
"""
Context7 - Fetch up-to-date library documentation for LLM context.

Usage:
    python scripts/context7.py "library" "query"
    python scripts/context7.py react "how to use hooks"
    python scripts/context7.py nextjs "app router middleware"
    python scripts/context7.py python-pptx "add slides"

Options:
    --json          Output structured JSON instead of text
    --list          Only list matching libraries, don't fetch docs
    --version VER   Pin to a specific library version
    --limit N       Max tokens to return (default: no limit)

Environment:
    CONTEXT7_API_KEY  Optional. Get a free key at https://context7.com/dashboard
                      Works without a key but with lower rate limits.
"""

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add workspace root to path for shared utils
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

from scripts.utils.api import load_api_key

BASE_URL = "https://context7.com/api"

# Common name variations the Context7 search API handles differently
SEARCH_ALIASES = {
    "nextjs": "next.js",
    "expressjs": "express",
    "vuejs": "vue",
    "angularjs": "angular",
    "tailwindcss": "tailwind css",
    "postgresql": "postgres",
    "mongodb": "mongoose",
    "tensorflow": "tensorflow",
    "pytorch": "torch",
    "scikit-learn": "sklearn",
}

# Map HTTP status codes to user-friendly messages
STATUS_MESSAGES = {
    202: "Library is still being processed. Try again in a minute.",
    301: "Library has been moved. Check the library ID.",
    400: "Bad request. Check library name and query.",
    401: "Invalid API key. Check CONTEXT7_API_KEY in .env.",
    403: "Access denied.",
    404: "Library not found.",
    422: "Library is too large or has no code snippets.",
    503: "Context7 search service is temporarily unavailable. Try again shortly.",
}


def get_headers():
    """Build request headers with optional API key."""
    headers = {}
    api_key = load_api_key("CONTEXT7_API_KEY", required=False)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def handle_response(resp, context=""):
    """Handle HTTP response, exit cleanly on errors."""
    if resp.status_code == 429:
        # RFC 7231 lets Retry-After be an HTTP-date, not only a second count:
        # `Retry-After: Wed, 21 Oct 2026 07:28:00 GMT` sent `int()` straight to
        # an uncaught ValueError, so the clean "Rate limited" exit turned into a
        # traceback on the one path that exists to be clean. The default 5 only
        # ever applied when the header was ABSENT.
        raw = resp.headers.get("Retry-After", "5")
        try:
            wait = f"{int(raw)}s"
        except (TypeError, ValueError):
            wait = f"whatever the server means by {raw!r}"
        print(f"Rate limited. Retry after {wait}.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code != 200:
        msg = STATUS_MESSAGES.get(resp.status_code, f"HTTP {resp.status_code} error.")
        # Try to get error detail from response body
        try:
            body = resp.json()
            detail = body.get("message", "")
            if detail:
                msg = f"{msg} {detail}"
        except ValueError as e:
            print(f"[debug] response body is not JSON: {e}", file=sys.stderr)
        prefix = f"{context}: " if context else ""
        print(f"{prefix}{msg}", file=sys.stderr)
        sys.exit(1)


def json_body(resp, context=""):
    """The parsed body of a 200 response, or a clean exit 1.

    `handle_response` only runs when the status is NOT 200, so it never sees
    this: a 200 carrying a non-JSON body - a proxy error page, a captive
    portal, a truncated upstream - reached a bare `resp.json()` and left the
    CLI as an uncaught `requests.exceptions.JSONDecodeError` traceback.
    MEASURED 2026-09-02 with a stub returning `200 OK` and the body
    `<html>Bad Gateway</html>`: both `fetch_docs_json` and `search_libraries`
    raised. `handle_response` already anticipates exactly this shape for ERROR
    bodies, with its own `except ValueError` debug branch; the success path had
    no such guard.

    Both call sites go through here rather than one, because the defect the
    audit named in `fetch_docs_json` was a verbatim copy of the line in
    `search_libraries`, and a fix that lands in one of two copies is the shape
    this file keeps rediscovering.
    """
    try:
        return resp.json()
    except ValueError as exc:
        prefix = f"{context}: " if context else ""
        print(f"{prefix}Context7 returned HTTP {resp.status_code} with a body that "
              f"is not JSON ({exc}). The upstream or a proxy in front of it is "
              f"misbehaving; try again shortly.", file=sys.stderr)
        sys.exit(1)


def search_libraries(library_name, query="documentation"):
    """Search for libraries matching the given name."""
    try:
        resp = requests.get(
            f"{BASE_URL}/v2/libs/search",
            headers=get_headers(),
            params={"libraryName": library_name, "query": query},
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        print("Connection error. Check your internet connection.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("Request timed out. Try again.", file=sys.stderr)
        sys.exit(1)
    handle_response(resp, "Search")
    body = json_body(resp, "Search")
    return body.get("results", []) if isinstance(body, dict) else []


def fetch_docs_text(library_id, query, limit=None):
    """Fetch documentation as plain text (ideal for LLM context)."""
    params = {"libraryId": library_id, "query": query, "type": "txt"}
    if limit:
        params["tokens"] = limit
    try:
        resp = requests.get(
            f"{BASE_URL}/v2/context",
            headers=get_headers(),
            params=params,
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        print("Connection error. Check your internet connection.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("Request timed out. The library may be too large. Try --limit.", file=sys.stderr)
        sys.exit(1)
    handle_response(resp, f"Fetch '{library_id}'")
    return resp.text


def fetch_docs_json(library_id, query, limit=None):
    """Fetch documentation as structured JSON."""
    params = {"libraryId": library_id, "query": query, "type": "json"}
    if limit:
        params["tokens"] = limit
    try:
        resp = requests.get(
            f"{BASE_URL}/v2/context",
            headers=get_headers(),
            params=params,
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        print("Connection error. Check your internet connection.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("Request timed out. The library may be too large. Try --limit.", file=sys.stderr)
        sys.exit(1)
    handle_response(resp, f"Fetch '{library_id}'")
    return json_body(resp, f"Fetch '{library_id}'")


def pick_best_match(results, library_name):
    """Pick the best matching library from search results.

    Prefers exact title matches and higher trust scores over arbitrary API ordering.
    """
    name_lower = library_name.lower().replace("-", "").replace(".", "")

    # Score each result: exact title match > partial ID match > first result
    scored = []
    for r in results:
        score = 0
        title = r.get("title", "").lower().replace("-", "").replace(".", "")
        rid = r.get("id", "").lower().replace("-", "").replace(".", "")

        # Exact title match
        if title == name_lower:
            score += 100
        # Title starts with search term
        elif title.startswith(name_lower):
            score += 50
        # ID contains the search term as a path segment
        if f"/{name_lower}" in rid or rid.endswith(f"/{name_lower}"):
            score += 30

        # Trust score bonus
        trust = r.get("trustScore", 0)
        if isinstance(trust, (int, float)):
            score += trust

        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def main():
    parser = argparse.ArgumentParser(
        description="Fetch up-to-date library docs from Context7",
        epilog="Examples:\n"
               "  python scripts/context7.py react hooks\n"
               "  python scripts/context7.py nextjs \"app router\" --version v15\n"
               "  python scripts/context7.py python-pptx \"add slides\" --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("library", help="Library name to search (e.g., react, nextjs, fastapi)")
    parser.add_argument("query", nargs="?", default="documentation", help="What you want to know (default: documentation)")
    parser.add_argument("--json", action="store_true", help="Output structured JSON instead of text")
    parser.add_argument("--list", action="store_true", help="Only list matching libraries")
    parser.add_argument("--version", type=str, help="Pin to a specific library version")
    parser.add_argument("--limit", type=int, help="Max tokens to return")
    args = parser.parse_args()

    # Step 1: Search for the library (apply known aliases for better results)
    search_name = SEARCH_ALIASES.get(args.library.lower(), args.library)
    results = search_libraries(search_name, args.query)
    if not results:
        print(f"No libraries found matching '{args.library}'", file=sys.stderr)
        sys.exit(1)

    # If --list, just show matches and exit
    if args.list:
        if args.json:
            # --list used to ignore --json and print prose, so any machine caller
            # got an unparseable page. The versions array is the field callers
            # actually want; emitting it is what makes a currency check possible.
            print(json.dumps({"results": results}, indent=2))
            return
        print(f"Found {len(results)} matching libraries:\n")
        for r in results:
            trust = r.get("trustScore", "?")
            snippets = r.get("totalSnippets", "?")
            versions = r.get("versions", [])
            ver_str = f"  Versions: {', '.join(versions[:5])}" if versions else ""
            print(f"  {r['id']}  -  {r.get('title', '?')}")
            print(f"    {r.get('description', '')[:100]}")
            print(f"    Trust: {trust}/10  |  Snippets: {snippets}{ver_str}")
            print()
        return

    # Pick the best match (not just the first result)
    best = pick_best_match(results, args.library)
    lib_id = best["id"]

    # Apply version if specified
    if args.version:
        ver = args.version if args.version.startswith("v") else f"v{args.version}"
        available = best.get("versions", [])
        # Find matching version
        # `startswith` at a version-segment boundary, and nothing looser. The
        # `or ver in v` clause that used to be here made `--version 1` match
        # `v15.2.0`, because "v1" IS a substring of "v15.2.0" — so whichever of
        # the two appeared first in `available` won, and asking for major
        # version 1 silently returned v15 docs. `startswith` alone is not
        # enough either: "v1" still prefixes "v15.2.0". The next character has
        # to end the segment.
        match = None
        for v in available:
            if v == ver:
                match = v
                break
            if v.startswith(ver) and not v[len(ver):len(ver) + 1].isdigit():
                match = v
                break
        if match:
            lib_id = f"{lib_id}/{match}"
        else:
            lib_id = f"{lib_id}/{ver}"
            if available:
                print(f"Warning: version '{ver}' not in known versions: {', '.join(available[:5])}", file=sys.stderr)

    print(f"Library: {best.get('title', '?')} ({lib_id})", file=sys.stderr)
    print(f"Query: {args.query}", file=sys.stderr)
    print("---", file=sys.stderr)

    # Step 2: Fetch docs
    if args.json:
        data = fetch_docs_json(lib_id, args.query, args.limit)
        print(json.dumps(data, indent=2))
    else:
        text = fetch_docs_text(lib_id, args.query, args.limit)
        print(text)


if __name__ == "__main__":
    main()
