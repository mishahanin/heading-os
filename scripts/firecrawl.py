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
  Results cached at outputs/browser/firecrawl-cache/ to avoid re-spending credits.
  Default TTLs: scrape 24h, crawl 48h, extract 72h, search 6h, map 168h.
  Use --no-cache to bypass, --clear-cache to wipe.
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

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.api import load_api_key
from scripts.utils.workspace import get_outputs_dir

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = str(get_outputs_dir() / "browser" / "firecrawl-cache")

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
def load_blocked_domains():
    """Parse blocked domains from reference/search-domains.md."""
    domains_file = str(Path(WORKSPACE_ROOT) / "reference" / "search-domains.md")
    if not os.path.exists(domains_file):
        return []

    blocked = []
    in_blocked = False
    with open(domains_file, "r", encoding="utf-8") as f:
        for line in f:
            if "## Blocked Domains" in line:
                in_blocked = True
                continue
            if in_blocked and line.startswith("##"):
                break
            if in_blocked and line.strip() and not line.startswith("-") and not line.startswith("#"):
                # Parse comma-separated domains
                for domain in line.strip().split(","):
                    d = domain.strip()
                    if d and "." in d:
                        blocked.append(d)
    return blocked


def get_cache_key(identifier, command):
    """Generate SHA256 cache key from identifier and command."""
    raw = f"{command}:{identifier}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_cache(key, ttl_hours):
    """Return cached content or None."""
    cache_file = str(Path(CACHE_DIR) / f"{key}.json")
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    ts = cached.get("timestamp", 0)
    age_hours = (time.time() - ts) / 3600
    if age_hours > ttl_hours:
        return None

    print(f"[cache hit] {age_hours:.1f}h old (TTL: {ttl_hours}h)", file=sys.stderr)
    return cached.get("content")


def write_cache(key, data, command, identifier, credits_used=0, ttl_hours=24):
    """Write result to cache with metadata."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = str(Path(CACHE_DIR) / f"{key}.json")
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
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    # Only retry on transient/network errors
                    if any(kw in err_str for kw in ("timeout", "connection", "rate limit", "429", "500", "502", "503", "504")):
                        if attempt < max_attempts:
                            wait = backoff_base ** attempt
                            print(f"[retry] Attempt {attempt}/{max_attempts} failed: {e}. Retrying in {wait}s...", file=sys.stderr)
                            time.sleep(wait)
                            continue
                    raise  # Non-transient error, don't retry
            raise last_error
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
    ttl = args.cache_ttl or DEFAULT_TTLS["scrape"]
    cache_key = get_cache_key(url, "scrape")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            output = format_output(cached, args.format)
            return write_output(output, args.output)

    client = get_client(args.timeout)

    formats_list = []
    if args.format == "html":
        formats_list = ["markdown", "html"]
    elif args.format == "json":
        formats_list = ["markdown"]
    else:
        formats_list = ["markdown"]

    kwargs = {"formats": formats_list}
    if args.screenshot:
        kwargs["formats"].append("screenshot")

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

    ttl = args.cache_ttl or DEFAULT_TTLS["batch"]

    # Check cache for each URL
    results = {}
    urls_to_fetch = []
    for url in urls:
        cache_key = get_cache_key(url, "scrape")
        if not args.no_cache:
            cached = check_cache(cache_key, ttl)
            if cached is not None:
                results[url] = cached
                continue
        urls_to_fetch.append(url)

    if urls_to_fetch:
        client = get_client(args.timeout)
        formats_list = ["markdown"]
        if args.format == "html":
            formats_list = ["markdown", "html"]

        @_retry()
        def _do_batch():
            return client.batch_scrape(urls_to_fetch, formats=formats_list)
        batch_result = _do_batch()

        # batch_scrape returns a BatchScrapeJob with .data list
        docs = []
        if hasattr(batch_result, "data"):
            docs = batch_result.data
        elif isinstance(batch_result, list):
            docs = batch_result

        credits = len(urls_to_fetch)
        print(f"[{credits} credits] Batch scraped {len(urls_to_fetch)} URLs", file=sys.stderr)

        for i, doc in enumerate(docs):
            doc_dict = document_to_dict(doc)
            url = doc_dict.get("metadata", {}).get("source_url") or doc_dict.get("metadata", {}).get("url") or (urls_to_fetch[i] if i < len(urls_to_fetch) else f"url_{i}")
            results[url] = doc_dict
            if not args.no_cache:
                ck = get_cache_key(url, "scrape")
                write_cache(ck, doc_dict, "scrape", url, 1, ttl)

    # Output all results
    output_parts = []
    for url in urls:
        doc = results.get(url, {})
        content = format_output(doc, args.format)
        output_parts.append(f"--- {url} ---\n{content}")

    return write_output("\n\n".join(output_parts), args.output)


# ============================================================
# Crawl Command
# ============================================================
def cmd_crawl(args):
    """Crawl a website, discovering and scraping pages."""
    url = args.target
    limit = args.limit or 25
    ttl = args.cache_ttl or DEFAULT_TTLS["crawl"]
    cache_key = get_cache_key(f"{url}|limit={limit}|inc={args.include}|exc={args.exclude}", "crawl")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            output = format_output(cached, args.format)
            return write_output(output, args.output)

    client = get_client(args.timeout)

    kwargs = {"limit": limit}
    if args.include:
        kwargs["include_paths"] = args.include.split("|")
    if args.exclude:
        kwargs["exclude_paths"] = args.exclude.split("|")

    formats_list = ["markdown"]
    if args.format == "html":
        formats_list = ["markdown", "html"]
    kwargs["scrape_options"] = {"formats": formats_list}

    @_retry()
    def _do_crawl():
        return client.crawl(url, **kwargs)
    result = _do_crawl()

    # CrawlJob has .data (list of Documents) and .credits_used
    docs = []
    credits = 0
    if hasattr(result, "data"):
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

    if not args.no_cache:
        write_cache(cache_key, crawl_data, "crawl", url, credits, ttl)

    # Format output
    if args.format == "json":
        output = json.dumps(crawl_data, indent=2, ensure_ascii=False)
    else:
        parts = []
        for doc in docs:
            page_url = doc.get("metadata", {}).get("source_url") or doc.get("metadata", {}).get("url") or "unknown"
            content = doc.get("markdown") or doc.get("html") or ""
            parts.append(f"--- {page_url} ---\n{content}")
        output = "\n\n".join(parts)

    return write_output(output, args.output)


# ============================================================
# Map Command
# ============================================================
def cmd_map(args):
    """Discover all URLs on a site."""
    url = args.target
    ttl = args.cache_ttl or DEFAULT_TTLS["map"]
    cache_key = get_cache_key(url, "map")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            output = format_output(cached, args.format)
            return write_output(output, args.output)

    client = get_client(args.timeout)

    kwargs = {}
    if args.limit:
        kwargs["limit"] = args.limit

    @_retry()
    def _do_map():
        return client.map(url, **kwargs)
    result = _do_map()

    # MapData has .links (list of SearchResult with url, title, description)
    links = []
    if hasattr(result, "links"):
        for link in result.links:
            if hasattr(link, "url"):
                links.append({"url": link.url, "title": getattr(link, "title", None), "description": getattr(link, "description", None)})
            elif isinstance(link, str):
                links.append({"url": link})
            elif isinstance(link, dict):
                links.append(link)

    map_data = {"url": url, "links_found": len(links), "links": links}
    print(f"[1 credit] Mapped {len(links)} URLs from {url}", file=sys.stderr)

    if not args.no_cache:
        write_cache(cache_key, map_data, "map", url, 1, ttl)

    if args.format == "json":
        output = json.dumps(map_data, indent=2, ensure_ascii=False)
    else:
        parts = [f"# Site Map: {url}", f"Found {len(links)} URLs\n"]
        for link in links:
            line = link.get("url", "")
            title = link.get("title")
            if title:
                line += f"  -- {title}"
            parts.append(line)
        output = "\n".join(parts)

    return write_output(output, args.output)


# ============================================================
# Search Command
# ============================================================
def cmd_search(args):
    """Search the web and return full page content."""
    query = args.target
    limit = args.limit or 5
    ttl = args.cache_ttl or DEFAULT_TTLS["search"]
    cache_key = get_cache_key(f"{query}|limit={limit}", "search")

    if not args.no_cache:
        cached = check_cache(cache_key, ttl)
        if cached is not None:
            output = format_output(cached, args.format)
            return write_output(output, args.output)

    client = get_client(args.timeout)

    @_retry()
    def _do_search():
        return client.search(query, limit=limit)
    result = _do_search()

    # SearchData has .web (list of SearchResultWeb or Document)
    results_list = []
    if hasattr(result, "web") and result.web:
        for item in result.web:
            results_list.append(document_to_dict(item))

    # Filter out blocked domains
    blocked = load_blocked_domains()
    if blocked:
        filtered = []
        for r in results_list:
            url = r.get("url") or r.get("metadata", {}).get("source_url") or ""
            if not any(bd in url for bd in blocked):
                filtered.append(r)
            else:
                print(f"[filtered] Blocked domain: {url}", file=sys.stderr)
        results_list = filtered

    credits = len(results_list)
    search_data = {
        "query": query,
        "results_count": len(results_list),
        "credits_used": credits,
        "results": results_list,
    }

    print(f"[{credits} credits] Search returned {len(results_list)} results", file=sys.stderr)

    if not args.no_cache:
        write_cache(cache_key, search_data, "search", query, credits, ttl)

    if args.format == "json":
        output = json.dumps(search_data, indent=2, ensure_ascii=False)
    else:
        parts = [f"# Search: {query}\n"]
        for r in results_list:
            url = r.get("url") or r.get("metadata", {}).get("source_url") or "unknown"
            title = r.get("title") or r.get("metadata", {}).get("title") or ""
            markdown = r.get("markdown") or r.get("description") or ""
            parts.append(f"## {title}\n**URL:** {url}\n\n{markdown}")
        output = "\n\n---\n\n".join(parts)

    return write_output(output, args.output)


# ============================================================
# Extract Command
# ============================================================
def cmd_extract(args):
    """Extract structured data from URLs."""
    url = args.target
    ttl = args.cache_ttl or DEFAULT_TTLS["extract"]
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

    print(f"[credits used] Extracted structured data from {url}", file=sys.stderr)

    if not args.no_cache:
        write_cache(cache_key, extract_data, "extract", url, 0, ttl)

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
    if not os.path.exists(CACHE_DIR):
        print("Cache directory does not exist.", file=sys.stderr)
        return

    count = 0
    for f in os.listdir(CACHE_DIR):
        if f.endswith(".json"):
            os.remove(Path(CACHE_DIR) / f)
            count += 1
    print(f"Cleared {count} cached entries.", file=sys.stderr)


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
