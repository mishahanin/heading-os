#!/usr/bin/env python3
"""
firecrawl.py -- Web scraping for JS-rendered pages, batch URLs, site crawling,
structured extraction, and search+scrape via the Firecrawl API.

Usage:
  python scripts/firecrawl.py scrape "https://example.com"
  python scripts/firecrawl.py scrape "https://example.com" --format html
  python scripts/firecrawl.py batch "https://a.com,https://b.com,https://c.com"
  python scripts/firecrawl.py batch urls.txt
  python scripts/firecrawl.py crawl "https://docs.example.com" --limit 25
  python scripts/firecrawl.py crawl "https://competitor.com" --limit 10 --include "/product|/pricing"
  python scripts/firecrawl.py map "https://example.com"
  python scripts/firecrawl.py search "DPI market trends 2026" --limit 5
  python scripts/firecrawl.py extract "https://example.com/pricing" --prompt "Extract pricing tiers"
  python scripts/firecrawl.py extract "https://example.com/team" --schema '{"type":"object",...}'

Commands:
  scrape   - Scrape a single URL (1 credit)
  batch    - Scrape multiple URLs in parallel (1 credit/page)
  crawl    - Crawl a site discovering and scraping pages (1 credit/page)
  map      - Discover all URLs on a site without scraping (1 credit)
  search   - Search the web and return full page content (1 credit/result)
  extract  - Extract structured data from URLs using AI (credits vary)

Environment:
  Reads FIRECRAWL_API_KEY from .env file in workspace root or environment variable.
  Free tier: 500 credits.

Cache:
  Results cached to avoid re-spending credits: outputs/browser/firecrawl-cache/
  under the data overlay, or .cache/firecrawl/ under the workspace root when
  there is no overlay. `clear-cache` prints the path it acted on.
  Default TTLs: scrape 24h, batch 24h, crawl 48h, extract 72h, search 6h,
  map 168h. All six, matching DEFAULT_TTLS; the list reads as exhaustive and
  used to name five, leaving an operator tuning cache behaviour from this
  header with no documented TTL for batch.
  Use --no-cache to bypass, and the `clear-cache` COMMAND to wipe. There is no
  `--clear-cache` flag: argparse answers `unrecognized arguments: --clear-cache`
  and prints the usage line. Two lines above, this same header already spelled
  it as a command, so the header contradicted itself.

Tests: tests/test_a_cache_key_that_forgot_what_was_asked_for.py
       tests/test_a_bundle_that_never_said_the_keys_were_live.py
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.api import load_api_key
from scripts.utils.workspace import (
    data_overlay_present,
    get_data_root,
    get_outputs_dir,
    get_reference_dir,
    private_cache_dir,
)

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(SCRIPT_DIR)


def cache_dir() -> Path:
    """Where scraped pages are cached.

    With a data overlay this is unchanged, `<overlay>/outputs/browser/
    firecrawl-cache`, which the data repository's own gitignore already covers.
    Keeping the path is deliberate: moving it would orphan whatever is cached
    there now, and a cache the `clear-cache` command can no longer see is worse
    than an untidy path.

    WITHOUT an overlay it used to resolve to `<engine>/examples/outputs/browser/
    firecrawl-cache`, inside the bundled demo tree. `engine_guard.py` treats
    that tree as a closed manifest, so anything untracked under it is a data
    artifact. MEASURED 2026-08-28 on a clone with no overlay: one cached scrape
    put a file there, no gitignore rule covered it (`outputs/browser/
    firecrawl-cache/` is root-anchored and does not match a path under
    `examples/`), and `scan_engine_repo` flagged it. The pre-commit wall runs on
    every commit and the push wall on every push, so from that moment both
    refuse, over a directory nothing told the operator about.

    A FUNCTION, not the module constant it replaces. The constant called
    `get_outputs_dir()` at import time, so `import scripts.firecrawl` raised
    DataRootError on a stale HEADING_OS_DATA before argparse ever ran, and
    `--help` answered with a traceback. That is a by-product of the move, not a
    claim about the workspace: 31 scripts still bind such a constant at import,
    and the loud raise itself is deliberate. Only the timing changed here.
    """
    if data_overlay_present():
        return get_outputs_dir() / "browser" / "firecrawl-cache"
    return private_cache_dir("firecrawl")


DEFAULT_TTLS = {
    "scrape": 24,
    "batch": 24,
    "crawl": 48,
    "extract": 72,
    "search": 6,
    "map": 168,
}


# ============================================================
# State Management / Cache
# ============================================================
def find_search_domains_file():
    """Where `search-domains.md` actually is, or None.

    `config/routing-map.yaml` routes this file `private`, so on the operator's
    machine it lives in the DATA overlay. This function used to look ONLY at
    `WORKSPACE_ROOT / "reference"`, which is the ENGINE tree — the file was not
    there, `os.path.exists` was False, and `load_blocked_domains()` returned an
    empty list. Measured 2026-08-24 on the operator's own workspace: zero
    domains loaded, so the blocked-domain filter had been a complete no-op.
    The DATA path is tried first and the engine path second, so a public clone
    shipping a generic list still works.
    """
    for candidate in (get_data_root() / "reference" / "search-domains.md",
                      get_reference_dir() / "search-domains.md"):
        if candidate.is_file():
            return candidate
    return None


def load_blocked_domains(verbose=True):
    """Parse blocked domains from reference/search-domains.md.

    Says so on stderr when it loads nothing. A filter that silently degrades to
    a no-op reports the same clean output as a filter that is working, and this
    one is a content-quality and sourcing control.
    """
    domains_file = find_search_domains_file()
    if domains_file is None:
        if verbose:
            print("[warn] search-domains.md not found in the data overlay or the "
                  "engine reference/ dir; NO domains are being blocked",
                  file=sys.stderr)
        return []

    # The read is guarded, and the guard is the same degradation the branch
    # above takes. `search-domains.md` is an OPERATOR-authored file in a
    # bilingual RU/EN workspace, so a copy saved by an editor in cp1251 is an
    # ordinary accident rather than a hostile one, and `read_text`/`open` in
    # text mode raises `UnicodeDecodeError` on it. Nothing caught that: the
    # docstring says this function "says so on stderr when it loads nothing",
    # and instead the whole command died with a traceback from inside a
    # content-quality control. `ValueError` is the class of the decode failure;
    # `OSError` covers the file vanishing between the `is_file()` above and the
    # open here.
    try:
        text = domains_file.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        if verbose:
            print(f"[warn] {domains_file} could not be read ({type(exc).__name__}); "
                  f"NO domains are being blocked", file=sys.stderr)
        return []

    blocked = []
    in_blocked = False
    for line in text.splitlines(keepends=True):
        if "## Blocked Domains" in line:
            in_blocked = True
            continue
        if in_blocked and line.startswith("##"):
            break
        if not in_blocked:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A leading `-` used to skip the line outright, on the assumption
        # that a bullet is prose. A Markdown bullet list is the ordinary
        # way to write a domain list, so the bullet is stripped and the
        # rest parsed like any other line.
        if stripped.startswith(("- ", "* ", "-\t")):
            stripped = stripped[1:].strip()
        elif stripped == "-":
            continue
        for domain in stripped.split(","):
            d = domain.strip().strip("`*_")
            # A domain, not a sentence. The prose paragraph under the
            # heading ("Content farms, stub-only paywalled sites, and
            # low-signal aggregators. Apply as `blocked_domains`...") has
            # dots in it too.
            if d and "." in d and " " not in d:
                blocked.append(d)
    if not blocked and verbose:
        print(f"[warn] {domains_file} has a Blocked Domains section that parsed to "
              f"ZERO domains; nothing is being blocked", file=sys.stderr)
    return blocked


def is_blocked_url(url, blocked):
    """Is `url` on the blocked list, matched on the HOST, not the whole string?

    `any(bd in url for bd in blocked)` matched anywhere in the URL, so blocking
    `abc.com` also blocked `abc.com.au`, `notabc.com`, and any URL carrying
    `abc.com` in a query parameter. An entry may still name a path prefix
    (`linkedin.com/pulse` is in the live list), so both forms are handled: the
    host part must match as a whole label sequence, and the path part must be
    a real prefix of the path.
    """
    try:
        parsed = urlparse(url if "//" in url else f"//{url}", scheme="https")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if not host:
        return False
    for entry in blocked:
        entry = entry.strip().lower().rstrip("/")
        if not entry:
            continue
        bd_host, _, bd_path = entry.partition("/")
        if host != bd_host and not host.endswith("." + bd_host):
            continue
        if bd_path and not (path.lstrip("/") + "/").startswith(bd_path + "/"):
            continue
        return True
    return False


def get_cache_key(identifier, command):
    """Generate SHA256 cache key from identifier and command."""
    raw = f"{command}:{identifier}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scrape_formats(output_format, screenshot=False):
    """The formats list a scrape request actually asks Firecrawl for.

    One function, used by both the request and the cache key, because those two
    must never disagree. `--format json` and `--format markdown` request the
    same thing and legitimately share a cache entry; `--format html` and
    `--screenshot` do not.
    """
    formats = ["markdown", "html"] if output_format == "html" else ["markdown"]
    if screenshot:
        formats.append("screenshot")
    return formats


def scrape_cache_key(url, formats):
    """Cache key for one scraped URL, keyed on WHAT WAS ASKED FOR.

    The key was `get_cache_key(url, "scrape")` - the URL and nothing else. So a
    plain `scrape URL` cached a markdown-only document, and a later
    `scrape URL --screenshot` served that entry back: the screenshot was never
    fetched, `format_output` found markdown and printed it, and nothing said the
    screenshot was missing. The same hole swallowed `--format html`, which fell
    back to the cached markdown.

    Every other command already keys on its variable inputs - crawl on
    limit/include/exclude, search on limit, extract on prompt/schema. Scrape and
    batch were the two that did not.

    Sorted, so a formats list built in a different order still hits.
    """
    return get_cache_key(f"{url}|formats={','.join(sorted(formats))}", "scrape")


def check_cache(key, ttl_hours):
    """Return cached content or None."""
    cache_file = str(cache_dir() / f"{key}.json")
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (ValueError, OSError):
        # `ValueError`, not `json.JSONDecodeError`. The decode happens in the
        # text wrapper while `json.load` reads it, so a cache file holding a
        # non-UTF-8 byte raises `UnicodeDecodeError` - a SIBLING of
        # JSONDecodeError under ValueError, not a subclass. MEASURED 2026-09-01:
        # one such file took the whole command down instead of taking the
        # documented "return None and fetch it again" branch. A cache entry is
        # the most disposable thing in this script and the least worth crashing
        # over, and a torn write is how it becomes undecodable.
        return None

    ts = cached.get("timestamp", 0)
    age_hours = (time.time() - ts) / 3600
    if age_hours > ttl_hours:
        return None

    print(f"[cache hit] {age_hours:.1f}h old (TTL: {ttl_hours}h)", file=sys.stderr)
    return cached.get("content")


def write_cache(key, data, command, identifier, credits_used=0, ttl_hours=24):
    """Write result to cache with metadata."""
    cdir = cache_dir()
    os.makedirs(cdir, exist_ok=True)
    cache_file = str(cdir / f"{key}.json")
    payload = {
        "url": identifier,
        "command": command,
        "timestamp": time.time(),
        "ttl_hours": ttl_hours,
        "credits_used": credits_used,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "content": data,
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ============================================================
# API Client / Firecrawl Wrapper
# ============================================================
def _retry(max_attempts=3, backoff_base=2):
    """Decorator: retry on transient failures with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # No `last_error` accumulator: it only ever fed the `raise
            # last_error` below the loop, which nothing could reach.
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    err_str = str(e).lower()
                    # Only retry on transient/network errors
                    if any(kw in err_str for kw in ("timeout", "connection", "rate limit", "429", "500", "502", "503", "504")):
                        if attempt < max_attempts:
                            wait = backoff_base ** attempt
                            print(f"[retry] Attempt {attempt}/{max_attempts} failed: {e}. Retrying in {wait}s...", file=sys.stderr)
                            time.sleep(wait)
                            continue
                    # Reached on a non-transient error OR on the last attempt of
                    # a transient one. The old comment claimed only the first,
                    # and a `raise last_error` sat below the loop where nothing
                    # could reach it: every path above returns, continues, or
                    # raises here.
                    raise
            raise AssertionError("unreachable: the loop above always exits")  # pragma: no cover
        return wrapper
    return decorator


def get_client(timeout_ms=30000):
    """Initialize and return Firecrawl client."""
    # Avoid self-import: temporarily remove scripts/ from sys.path
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    path_backup = sys.path[:]
    sys.path = [p for p in sys.path if os.path.abspath(p) != scripts_dir]
    # Also clear any cached self-import
    cached = sys.modules.pop("firecrawl", None)
    try:
        import firecrawl as firecrawl_pkg
        FirecrawlClass = firecrawl_pkg.Firecrawl
    finally:
        sys.path = path_backup
        if cached is not None:
            sys.modules["firecrawl"] = cached

    api_key = load_api_key("FIRECRAWL_API_KEY")
    return FirecrawlClass(api_key=api_key, timeout=timeout_ms / 1000)


# ============================================================
# Output Formatting / Document Conversion
# ============================================================
def format_output(content, output_format="markdown"):
    """Format content for output."""
    if isinstance(content, dict):
        if output_format == "json":
            return json.dumps(content, indent=2, ensure_ascii=False)
        # Extract markdown or html from document dict
        if output_format == "html" and "html" in content:
            return content["html"]
        return content.get("markdown", content.get("html", json.dumps(content, indent=2)))
    if isinstance(content, str):
        return content
    return str(content)


# Each aggregate command renders its OWN wrapper dict, and both the fresh path
# and the cache-hit path call the same renderer. They used to differ: the fresh
# path built markdown inline while the cache-hit path handed the wrapper to
# `format_output`, which found no "markdown" key and dumped raw JSON. The same
# command therefore printed markdown once and JSON on the next run inside the
# TTL, and anything parsing the output broke depending on cache state.

def render_crawl(crawl_data, output_format):
    """Render a crawl wrapper in the format that was ASKED FOR.

    `output_format` was read for "json" and ignored otherwise, so `--format
    html` preferred markdown on every page. `cmd_crawl` does request html from
    Firecrawl for that flag, so the html was fetched, paid for, and thrown away
    here. `format_output` - which the scrape and batch paths use - has honoured
    "html" all along; this renderer was the odd one out.
    """
    if output_format == "json":
        return json.dumps(crawl_data, indent=2, ensure_ascii=False)
    parts = []
    for doc in crawl_data.get("documents", []):
        meta = doc.get("metadata") or {}
        page_url = meta.get("source_url") or meta.get("url") or "unknown"
        if output_format == "html":
            content = doc.get("html") or doc.get("markdown") or ""
        else:
            content = doc.get("markdown") or doc.get("html") or ""
        parts.append(f"--- {page_url} ---\n{content}")
    return "\n\n".join(parts)


def render_map(map_data, output_format):
    if output_format == "json":
        return json.dumps(map_data, indent=2, ensure_ascii=False)
    links = map_data.get("links", [])
    parts = [f"# Site Map: {map_data.get('url', '')}", f"Found {len(links)} URLs\n"]
    for link in links:
        line = link.get("url", "")
        title = link.get("title")
        if title:
            line += f"  -- {title}"
        parts.append(line)
    return "\n".join(parts)


def render_search(search_data, output_format):
    if output_format == "json":
        return json.dumps(search_data, indent=2, ensure_ascii=False)
    parts = [f"# Search: {search_data.get('query', '')}\n"]
    for r in search_data.get("results", []):
        meta = r.get("metadata") or {}
        url = r.get("url") or meta.get("source_url") or "unknown"
        title = r.get("title") or meta.get("title") or ""
        markdown = r.get("markdown") or r.get("description") or ""
        parts.append(f"## {title}\n**URL:** {url}\n\n{markdown}")
    return "\n\n---\n\n".join(parts)


def document_to_dict(doc):
    """Convert a Firecrawl Document to a serializable dict."""
    if hasattr(doc, "model_dump"):
        return doc.model_dump(exclude_none=True)
    if hasattr(doc, "dict"):
        return doc.dict(exclude_none=True)
    if isinstance(doc, dict):
        return doc
    return {"content": str(doc)}


# ============================================================
# Scrape Command
# ============================================================
def cmd_scrape(args):
    """Scrape a single URL."""
    url = args.target
    ttl = args.cache_ttl if args.cache_ttl is not None else DEFAULT_TTLS["scrape"]
    formats_list = scrape_formats(args.format, args.screenshot)
    cache_key = scrape_cache_key(url, formats_list)

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            output = format_output(cached, args.format)
            return write_output(output, args.output)

    client = get_client(args.timeout)

    kwargs = {"formats": formats_list}

    @_retry()
    def _do_scrape():
        return client.scrape(url, **kwargs)
    result = _do_scrape()
    doc = document_to_dict(result)

    credits = 1
    print(f"[{credits} credit] Fresh scrape: {url}", file=sys.stderr)

    if not args.no_cache:
        write_cache(cache_key, doc, "scrape", url, credits, ttl)

    output = format_output(doc, args.format)
    return write_output(output, args.output)


# ============================================================
# Batch Command
# ============================================================
def cmd_batch(args):
    """Scrape multiple URLs in parallel."""
    raw = args.target

    # Parse URLs: comma-separated string or file path
    if os.path.isfile(raw):
        with open(raw, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and line.strip().startswith("http")]
    else:
        urls = [u.strip() for u in raw.split(",") if u.strip()]

    if not urls:
        print("Error: No valid URLs provided.", file=sys.stderr)
        sys.exit(1)

    ttl = args.cache_ttl if args.cache_ttl is not None else DEFAULT_TTLS["batch"]
    # The SAME key function scrape uses, over the same formats list, so a batch
    # entry and a single scrape of the same URL still share when they asked for
    # the same thing - and stop sharing when they did not. batch has no
    # --screenshot flag.
    formats_list = scrape_formats(args.format)

    # Check cache for each URL
    results = {}
    urls_to_fetch = []
    for url in urls:
        cache_key = scrape_cache_key(url, formats_list)
        if not args.no_cache:
            cached = check_cache(cache_key, ttl)
            if cached is not None:
                results[url] = cached
                continue
        urls_to_fetch.append(url)

    if urls_to_fetch:
        client = get_client(args.timeout)

        @_retry()
        def _do_batch():
            return client.batch_scrape(urls_to_fetch, formats=formats_list)
        batch_result = _do_batch()

        # batch_scrape returns a BatchScrapeJob with .data list
        docs = []
        shape_ok = True
        if hasattr(batch_result, "data"):
            docs = batch_result.data
        elif isinstance(batch_result, list):
            docs = batch_result
        else:
            # Same defect as the crawl path: an unrecognised shape printed
            # `--- <url> ---\n{}` for every requested URL and exited 0. The
            # stderr line below was the whole of that "fix" - stdout still
            # carried the garbage the comment called the defect, and the
            # success credit line below still claimed N URLs were scraped.
            shape_ok = False
            print(f"[error] batch_scrape returned an unexpected shape "
                  f"({type(batch_result).__name__} with no .data); "
                  f"{len(urls_to_fetch)} URL(s) produced nothing", file=sys.stderr)

        if shape_ok:
            credits = len(urls_to_fetch)
            print(f"[{credits} credits] Batch scraped {len(urls_to_fetch)} URLs",
                  file=sys.stderr)

        # Firecrawl answers a batch in request order, so POSITION is the mapping
        # from a document back to the URL the operator asked for. Keying on the
        # document's own `metadata.source_url` broke every redirecting host:
        # `https://example.com` came back as `https://www.example.com/`, the
        # output loop below looked up the REQUESTED url, found nothing, and
        # printed the literal `{}` for a page that had been fetched and paid
        # for. The cache write inherited the same wrong key, so a later `scrape`
        # of the requested URL missed too.
        aligned = len(docs) == len(urls_to_fetch)
        if docs and not aligned:
            print(f"[warn] batch returned {len(docs)} document(s) for "
                  f"{len(urls_to_fetch)} requested URL(s); position cannot be "
                  f"trusted, falling back to the URL each document reports",
                  file=sys.stderr)
        for i, doc in enumerate(docs):
            doc_dict = document_to_dict(doc)
            if aligned:
                url = urls_to_fetch[i]
            else:
                # `.get("metadata", {})` substitutes only when the KEY IS
                # ABSENT. `document_to_dict` passes a raw dict through
                # untouched, so a `"metadata": null` reached `None.get(...)`
                # and killed the whole batch after the credits were spent.
                # Every other renderer in this file already uses `or {}`.
                meta = doc_dict.get("metadata") or {}
                url = meta.get("source_url") or meta.get("url") or f"url_{i}"
            results[url] = doc_dict
            if not args.no_cache:
                write_cache(scrape_cache_key(url, formats_list), doc_dict,
                            "scrape", url, 1, ttl)

    # Output all results
    output_parts = []
    missing = []
    for url in urls:
        doc = results.get(url)
        if doc is None:
            # NOT `{}`. `format_output({})` renders the literal two characters
            # "{}", which reads downstream as a document rather than as an
            # absence.
            missing.append(url)
            content = "(no document was returned for this URL)"
        else:
            content = format_output(doc, args.format)
        output_parts.append(f"--- {url} ---\n{content}")

    write_output("\n\n".join(output_parts), args.output)
    if missing:
        print(f"[error] {len(missing)} of {len(urls)} URL(s) produced no document: "
              f"{', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


# ============================================================
# Crawl Command
# ============================================================
def cmd_crawl(args):
    """Crawl a website, discovering and scraping pages."""
    url = args.target
    limit = args.limit if args.limit is not None else 25
    ttl = args.cache_ttl if args.cache_ttl is not None else DEFAULT_TTLS["crawl"]
    # The formats list is built BEFORE the key, and is part of it. `--format
    # html` changes `scrape_options.formats`, so it changes what comes back -
    # and the key did not carry it. A plain `crawl URL` cached markdown-only
    # documents and a `crawl URL --format html` for the next 48 hours was served
    # that entry, printed `[cache hit]`, and never fetched any html.
    formats_list = ["markdown", "html"] if args.format == "html" else ["markdown"]
    cache_key = get_cache_key(
        f"{url}|limit={limit}|inc={args.include}|exc={args.exclude}"
        f"|formats={','.join(sorted(formats_list))}", "crawl")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            return write_output(render_crawl(cached, args.format), args.output)

    client = get_client(args.timeout)

    kwargs = {"limit": limit}
    if args.include:
        kwargs["include_paths"] = args.include.split("|")
    if args.exclude:
        kwargs["exclude_paths"] = args.exclude.split("|")

    kwargs["scrape_options"] = {"formats": formats_list}

    @_retry()
    def _do_crawl():
        return client.crawl(url, **kwargs)
    result = _do_crawl()

    # CrawlJob has .data (list of Documents) and .credits_used
    credits = 0
    if not hasattr(result, "data"):
        # An unrecognised return shape used to leave `docs` empty with no
        # message, and the empty result was then CACHED for 48 hours -- so one
        # SDK change made the command answer "0 pages" for two days.
        #
        # Not caching it was half the fix. The command still printed
        # "Crawled 0 pages", wrote a zero-page document, and RETURNED 0, which
        # is byte-for-byte what a site with nothing on it looks like to a
        # caller. `cmd_batch` already exits 1 for its own version of this, and
        # this is the same class: nothing was measured, so nothing may be
        # rendered as a result.
        print(f"[error] crawl returned an unexpected shape ({type(result).__name__} "
              f"with no .data); nothing was crawled, so no result is written "
              f"and nothing is cached", file=sys.stderr)
        sys.exit(1)
    docs = [document_to_dict(d) for d in result.data]
    if hasattr(result, "credits_used"):
        credits = result.credits_used

    crawl_data = {
        "url": url,
        "pages_found": len(docs),
        "credits_used": credits,
        "documents": docs,
    }

    print(f"[{credits} credits] Crawled {len(docs)} pages from {url}", file=sys.stderr)

    # No `and shape_ok`: the shape failure exited above, so a second guard here
    # would be unreachable, and two guards that make each other dead code is how
    # a mutation survives with nobody able to say which one was load-bearing.
    if not args.no_cache:
        write_cache(cache_key, crawl_data, "crawl", url, credits, ttl)

    return write_output(render_crawl(crawl_data, args.format), args.output)


# ============================================================
# Map Command
# ============================================================
def cmd_map(args):
    """Discover all URLs on a site."""
    url = args.target
    ttl = args.cache_ttl if args.cache_ttl is not None else DEFAULT_TTLS["map"]
    # Keyed on the limit as well as the URL. `--limit` is passed to the API and
    # changes how many links come back, and the key was the URL alone: `map URL
    # --limit 5` cached five links, and `map URL --limit 500` was served those
    # five for the next SEVEN days. The 168h TTL makes map the worst place in
    # this file to lose a key component.
    cache_key = get_cache_key(f"{url}|limit={args.limit}", "map")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            return write_output(render_map(cached, args.format), args.output)

    client = get_client(args.timeout)

    kwargs = {}
    if args.limit is not None:
        kwargs["limit"] = args.limit

    @_retry()
    def _do_map():
        return client.map(url, **kwargs)
    result = _do_map()

    # MapData has .links (list of SearchResult with url, title, description)
    links = []
    # The same guard cmd_crawl carries, for the same reason and with a worse
    # blast radius: an SDK that renames `.links` leaves this list empty, and the
    # empty payload used to be cached for 168 hours under "[1 credit] Mapped 0
    # URLs". One release note would have made this command answer "0 URLs" for a
    # week while charging for it.
    if not hasattr(result, "links"):
        # Exits, for the reason spelled out in `cmd_crawl`. Rendering
        # "[1 credit] Mapped 0 URLs" and a "Found 0 URLs" site map at exit 0 is
        # indistinguishable from a site that genuinely has no links.
        print(f"[error] map returned an unexpected shape ({type(result).__name__} "
              f"with no .links); nothing was mapped, so no result is written "
              f"and nothing is cached", file=sys.stderr)
        sys.exit(1)
    for link in result.links:
        if hasattr(link, "url"):
            links.append({"url": link.url, "title": getattr(link, "title", None), "description": getattr(link, "description", None)})
        elif isinstance(link, str):
            links.append({"url": link})
        elif isinstance(link, dict):
            links.append(link)
        else:
            print(f"[warn] map: skipping a link of unexpected type "
                  f"{type(link).__name__}; links_found undercounts",
                  file=sys.stderr)

    map_data = {"url": url, "links_found": len(links), "links": links}
    print(f"[1 credit] Mapped {len(links)} URLs from {url}", file=sys.stderr)

    # No second shape guard: see the note in `cmd_crawl`.
    if not args.no_cache:
        write_cache(cache_key, map_data, "map", url, 1, ttl)

    return write_output(render_map(map_data, args.format), args.output)


# ============================================================
# Search Command
# ============================================================
def cmd_search(args):
    """Search the web and return full page content."""
    query = args.target
    limit = args.limit if args.limit is not None else 5
    ttl = args.cache_ttl if args.cache_ttl is not None else DEFAULT_TTLS["search"]
    cache_key = get_cache_key(f"{query}|limit={limit}", "search")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            return write_output(render_search(cached, args.format), args.output)

    client = get_client(args.timeout)

    @_retry()
    def _do_search():
        return client.search(query, limit=limit)
    result = _do_search()

    # SearchData has .web (list of SearchResultWeb or Document)
    results_list = []
    # Shape-guarded like crawl and map. An empty `.web` is a legitimate
    # zero-result search and still caches; a MISSING `.web` is the SDK having
    # moved, and that answer was being cached for six hours as though the web
    # held nothing on the query.
    if not hasattr(result, "web"):
        # Exits, for the reason spelled out in `cmd_crawl`. An empty `.web` is a
        # legitimate zero-result search and still renders and caches below; a
        # MISSING `.web` is the SDK having moved, and rendering
        # `results_count: 0` for it at exit 0 tells the caller the web holds
        # nothing on the query.
        print(f"[error] search returned an unexpected shape ({type(result).__name__} "
              f"with no .web); nothing was searched, so no result is written "
              f"and nothing is cached", file=sys.stderr)
        sys.exit(1)
    if result.web:
        for item in result.web:
            results_list.append(document_to_dict(item))

    # Firecrawl charges per RESULT IT RETURNED. Counting after the local
    # blocked-domain filter under-reported real spend against the 500-credit
    # tier, which is the number the budget is tracked against.
    credits = len(results_list)

    # Filter out blocked domains
    blocked = load_blocked_domains()
    if blocked:
        filtered = []
        for r in results_list:
            meta = r.get("metadata") or {}
            url = r.get("url") or meta.get("source_url") or ""
            if not url:
                # A result with no URL used to pass unconditionally, because
                # `any(bd in "")` is False. The filter cannot judge it, so it
                # is dropped and said so -- a filter says what it could not
                # check rather than waving it through.
                print("[filtered] result carries no URL; cannot check it "
                      "against the blocked list", file=sys.stderr)
                continue
            if is_blocked_url(url, blocked):
                print(f"[filtered] Blocked domain: {url}", file=sys.stderr)
                continue
            filtered.append(r)
        results_list = filtered

    search_data = {
        "query": query,
        "results_count": len(results_list),
        "credits_used": credits,
        "results": results_list,
    }

    print(f"[{credits} credits] Search returned {len(results_list)} results", file=sys.stderr)

    # No second shape guard: see the note in `cmd_crawl`.
    if not args.no_cache:
        write_cache(cache_key, search_data, "search", query, credits, ttl)

    return write_output(render_search(search_data, args.format), args.output)


# ============================================================
# Extract Command
# ============================================================
def cmd_extract(args):
    """Extract structured data from URLs."""
    url = args.target
    ttl = args.cache_ttl if args.cache_ttl is not None else DEFAULT_TTLS["extract"]
    prompt_str = args.prompt or ""
    schema_str = args.schema or ""
    cache_key = get_cache_key(f"{url}|prompt={prompt_str}|schema={schema_str}", "extract")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            output = format_output(cached, "json")
            return write_output(output, args.output)

    client = get_client(args.timeout)

    kwargs = {"urls": [url]}
    if prompt_str:
        kwargs["prompt"] = prompt_str
    if schema_str:
        try:
            kwargs["schema"] = json.loads(schema_str)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON schema: {schema_str}", file=sys.stderr)
            sys.exit(1)

    @_retry()
    def _do_extract():
        return client.extract(**kwargs)
    result = _do_extract()

    # Extract returns structured data
    if hasattr(result, "model_dump"):
        extract_data = result.model_dump(exclude_none=True)
    elif hasattr(result, "dict"):
        extract_data = result.dict(exclude_none=True)
    elif isinstance(result, dict):
        extract_data = result
    else:
        extract_data = {"data": str(result)}

    # The extract API does not report its cost here, and the docstring at the
    # top of this file says "credits vary". The cache entry recorded `0` for it
    # and the console printed a `[credits used]` label with no number beside it,
    # so the one command whose spend is variable was the one command that
    # reported spending nothing. An unmeasured cost is stated as unknown; a
    # guessed figure inside a spend record is worse than a stated gap.
    print(f"[credits: unknown - the extract API does not report them] "
          f"Extracted structured data from {url}", file=sys.stderr)

    if not args.no_cache:
        write_cache(cache_key, extract_data, "extract", url, None, ttl)

    output = json.dumps(extract_data, indent=2, ensure_ascii=False)
    return write_output(output, args.output)


# ============================================================
# Output / Cache Utilities
# ============================================================
def write_output(content, output_path=None):
    """Write content to file or stdout."""
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Output written to {output_path}", file=sys.stderr)
    else:
        print(content)


def cmd_clear_cache(args):
    """Clear the firecrawl cache."""
    # Both messages name the directory. The cache moves with the data overlay,
    # so "Cache directory does not exist" without a path leaves the operator
    # unable to tell an empty cache from a cache somewhere they did not look.
    cdir = cache_dir()
    if not os.path.exists(cdir):
        print(f"Cache directory does not exist: {cdir}", file=sys.stderr)
        return

    count = 0
    for f in os.listdir(cdir):
        if f.endswith(".json"):
            os.remove(cdir / f)
            count += 1
    print(f"Cleared {count} cached entries from {cdir}.", file=sys.stderr)


# ============================================================
# Main / CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Firecrawl web scraping - JS-rendered pages, batch URLs, site crawling, structured extraction"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Common arguments added to each subparser
    def add_common_args(sub):
        sub.add_argument("target", help="URL, comma-separated URLs, file path, or search query")
        sub.add_argument("--format", "-f", default="markdown", choices=["markdown", "html", "json"],
                         help="Output format (default: markdown)")
        sub.add_argument("--no-cache", action="store_true", help="Skip cache, fetch fresh")
        sub.add_argument("--cache-ttl", type=int, help="Cache TTL in hours (overrides default)")
        sub.add_argument("-o", "--output", help="Write output to file instead of stdout")
        sub.add_argument("--timeout", type=int, default=30000, help="Request timeout in ms (default: 30000)")
        sub.add_argument("-q", "--quiet", action="store_true", help="Content only, suppress metadata on stderr")

    # scrape
    p_scrape = subparsers.add_parser("scrape", help="Scrape a single URL")
    add_common_args(p_scrape)
    p_scrape.add_argument("--screenshot", action="store_true", help="Include page screenshot")

    # batch
    p_batch = subparsers.add_parser("batch", help="Scrape multiple URLs in parallel")
    add_common_args(p_batch)

    # crawl
    p_crawl = subparsers.add_parser("crawl", help="Crawl a website")
    add_common_args(p_crawl)
    p_crawl.add_argument("--include", help="URL path patterns to include (pipe-separated regex)")
    p_crawl.add_argument("--exclude", help="URL path patterns to exclude (pipe-separated regex)")
    p_crawl.add_argument("--limit", type=int, default=25, help="Max pages to crawl (default: 25)")

    # map
    p_map = subparsers.add_parser("map", help="Discover all URLs on a site")
    add_common_args(p_map)
    p_map.add_argument("--limit", type=int, help="Max URLs to return")

    # search
    p_search = subparsers.add_parser("search", help="Search the web with full page content")
    add_common_args(p_search)
    p_search.add_argument("--limit", type=int, default=5, help="Max results (default: 5)")

    # extract
    p_extract = subparsers.add_parser("extract", help="Extract structured data from URLs")
    add_common_args(p_extract)
    p_extract.add_argument("--prompt", "-p", help="Natural language extraction prompt")
    p_extract.add_argument("--schema", "-s", help="JSON schema for structured output")

    # clear-cache
    p_clear = subparsers.add_parser("clear-cache", help="Clear the firecrawl cache")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "clear-cache":
        return cmd_clear_cache(args)

    commands = {
        "scrape": cmd_scrape,
        "batch": cmd_batch,
        "crawl": cmd_crawl,
        "map": cmd_map,
        "search": cmd_search,
        "extract": cmd_extract,
    }

    # Suppress stderr metadata if quiet. ExitStack closes the /dev/null handle and
    # restores sys.stderr on exit (the old bare assignment leaked the handle, F-L8).
    with contextlib.ExitStack() as stack:
        if hasattr(args, "quiet") and args.quiet:
            devnull = stack.enter_context(open(os.devnull, "w"))
            stack.enter_context(contextlib.redirect_stderr(devnull))

        cmd_func = commands.get(args.command)
        if cmd_func:
            cmd_func(args)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
