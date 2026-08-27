"""Web search backends for entity resolution.

Two backends with stacked fallback:
- Tavily (primary) - search + extracted content optimised for LLM agents
- Brave Search (fallback) - independent web index, raw search results

If TAVILY_API_KEY is missing or the call fails, search_with_fallback drops
to BRAVE_API_KEY automatically. If both keys are missing, raises
NoBackendsConfigured so callers can emit a structured error.

Public API:
    tavily_search(query, max_results=5) -> list[dict]
    brave_search(query, max_results=5) -> list[dict]
    search_with_fallback(query, max_results=5) -> tuple[list[dict], str]

Result shape (normalised across backends):
    [{"title": str, "url": str, "content": str, "score": float}, ...]
"""

import gzip
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import zlib

from .api import load_api_key


TAVILY_ENDPOINT = "https://api.tavily.com/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT = 30


class NoBackendsConfigured(Exception):
    """Neither TAVILY_API_KEY nor BRAVE_API_KEY is configured."""


class SearchBackendError(Exception):
    """A search backend returned an error after retries."""


def _post_json(url: str, payload: dict, headers: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """POST JSON, return parsed response. Retry once on 429/5xx with 2s backoff."""
    body = json.dumps(payload).encode("utf-8")
    headers_full = {"Content-Type": "application/json", "Accept": "application/json", **headers}

    for attempt in (1, 2):
        req = urllib.request.Request(url, data=body, headers=headers_full, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(2)
                continue
            raise SearchBackendError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        except urllib.error.URLError as e:
            raise SearchBackendError(f"Connection error: {e.reason}")
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as e:
            # Same contract as `_get_json` twenty lines down, and for the same
            # reason: `tavily_search` documents "Raises SearchBackendError on API
            # failure", and `search_with_fallback` catches only that plus
            # NoBackendsConfigured. A Tavily reply this reader cannot turn into
            # JSON - an HTML error page from a proxy is the ordinary case -
            # escaped as json.JSONDecodeError past the fallback, so Brave was
            # never tried and the caller got a decode error instead of results.
            # OSError last: it is the parent of URLError, so it must not shadow
            # the clause above.
            raise SearchBackendError(
                f"unreadable response body: {type(e).__name__}: {e}") from e


def _decode_body(resp) -> str:
    """Return the response text, honouring the encoding the server applied.

    `brave_search` sends `Accept-Encoding: gzip` and this reader decoded the
    bytes as UTF-8 regardless, so a compressed reply raised

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0x8b in position 1

    UnicodeDecodeError is not a `SearchBackendError`, so it escaped past
    `search_with_fallback`'s except clause: a compressing Brave endpoint took
    down the whole search call instead of falling back. Reproduced 2026-08-26
    against a loopback server that honours the header. An encoding nobody asked
    for is reported as a backend error rather than guessed at.
    """
    body = resp.read()
    encoding = (resp.headers.get("Content-Encoding") or "").strip().lower()
    if encoding in ("gzip", "x-gzip"):
        body = gzip.decompress(body)
    elif encoding == "deflate":
        try:
            body = zlib.decompress(body)
        except zlib.error:  # raw deflate, no zlib wrapper
            body = zlib.decompress(body, -zlib.MAX_WBITS)
    elif encoding and encoding != "identity":
        raise SearchBackendError(f"unsupported Content-Encoding: {encoding}")
    return body.decode("utf-8")


def _get_json(url: str, headers: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET JSON, return parsed response. Retry once on 429/5xx with 2s backoff."""
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                return json.loads(_decode_body(resp))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(2)
                continue
            raise SearchBackendError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        except urllib.error.URLError as e:
            raise SearchBackendError(f"Connection error: {e.reason}")
        except (UnicodeDecodeError, json.JSONDecodeError, zlib.error, OSError) as e:
            # A body this reader cannot turn into JSON is a backend failure and
            # must be raised as one, or `search_with_fallback` cannot fall back.
            # OSError is last on purpose: it is the parent of both URLError and
            # gzip.BadGzipFile, so it must not shadow the two clauses above.
            raise SearchBackendError(
                f"unreadable response body: {type(e).__name__}: {e}") from e


def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Query Tavily search API. Returns normalised result list.

    Raises NoBackendsConfigured if TAVILY_API_KEY is absent.
    Raises SearchBackendError on API failure after retries.
    """
    api_key = load_api_key("TAVILY_API_KEY", required=False)
    if not api_key:
        raise NoBackendsConfigured("TAVILY_API_KEY not configured")

    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    raw = _post_json(TAVILY_ENDPOINT, payload, headers)
    results = []
    for item in raw.get("results", []):
        results.append({
            "title": item.get("title", "") or "",
            "url": item.get("url", "") or "",
            "content": item.get("content", "") or "",
            "score": float(item.get("score", 0.0) or 0.0),
        })
    return results


def brave_search(query: str, max_results: int = 5) -> list[dict]:
    """Query Brave Search API. Returns normalised result list.

    Raises NoBackendsConfigured if BRAVE_API_KEY is absent.
    Raises SearchBackendError on API failure after retries.
    """
    api_key = load_api_key("BRAVE_API_KEY", required=False)
    if not api_key:
        raise NoBackendsConfigured("BRAVE_API_KEY not configured")

    params = urllib.parse.urlencode({"q": query, "count": max_results})
    url = f"{BRAVE_ENDPOINT}?{params}"
    headers = {"X-Subscription-Token": api_key, "Accept-Encoding": "gzip"}

    raw = _get_json(url, headers)
    web = raw.get("web", {}) or {}
    results = []
    for i, item in enumerate(web.get("results", [])):
        snippets = item.get("extra_snippets", []) or []
        content_parts = [item.get("description", "") or ""]
        content_parts.extend(snippets)
        content = "\n".join(p for p in content_parts if p)
        results.append({
            "title": item.get("title", "") or "",
            "url": item.get("url", "") or "",
            "content": content,
            "score": 1.0 - (i * 0.1),
        })
    return results


def search_with_fallback(query: str, max_results: int = 5) -> tuple[list[dict], str]:
    """Try Tavily first; fall back to Brave on failure or missing key.

    Returns (results, backend_used) where backend_used is "tavily" or "brave".
    Raises NoBackendsConfigured if both keys are absent.
    """
    tavily_key = load_api_key("TAVILY_API_KEY", required=False)
    brave_key = load_api_key("BRAVE_API_KEY", required=False)

    if not tavily_key and not brave_key:
        raise NoBackendsConfigured("Neither TAVILY_API_KEY nor BRAVE_API_KEY is configured")

    if tavily_key:
        try:
            return tavily_search(query, max_results=max_results), "tavily"
        except (SearchBackendError, NoBackendsConfigured):
            if not brave_key:
                raise

    return brave_search(query, max_results=max_results), "brave"
