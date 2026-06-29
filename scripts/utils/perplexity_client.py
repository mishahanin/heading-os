"""Perplexity Sonar transport — reusable research client.

Pure transport extracted from scripts/perplexity-research.py (2026-06-18, decision C)
so orchestrators can get structured (content, citations) without parsing stdout.
The CLI script now imports research() from here.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402

DEFAULT_MODEL = "sonar-pro"
_API_URL = "https://api.perplexity.ai/chat/completions"
_DEFAULT_SYSTEM = (
    "You are a research assistant providing thorough, factual analysis. "
    "Include specific data points, dates, and numbers where available. "
    "Focus on actionable intelligence. Cite your sources."
)


def research(
    question, model=DEFAULT_MODEL, system_prompt=None,
    domains=None, exclude_domains=None, recency="week",
):
    """Call Perplexity and return (content, citations).

    citations is a list of URL strings (may be empty). urllib errors are
    converted to RuntimeError so callers don't sys.exit on a transport failure.

    recency is for programmatic callers (e.g. orchestrators); the CLI keeps
    the historical "week" default and exposes no flag for it (YAGNI). Pass a
    falsy recency (None/"") to search the full index with NO time window —
    required for evergreen/footprint research, where a "week" window starves
    the search and Sonar pads the result with off-topic recent content.
    """
    try:
        api_key = load_api_key("PERPLEXITY_API_KEY")
    except ValueError as e:
        raise RuntimeError(f"Perplexity API key missing: {e}") from e
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM},
            {"role": "user", "content": question},
        ],
        "max_tokens": 4000,
        "temperature": 0.2,
        "return_citations": True,
    }
    if recency:
        payload["search_recency_filter"] = recency
    if domains:
        payload["search_domain_filter"] = [d.strip() for d in domains.split(",")][:20]
    elif exclude_domains:
        payload["search_domain_filter"] = [f"-{d.strip()}" for d in exclude_domains.split(",")][:20]

    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:  # nosec B310 - Perplexity API endpoint
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Perplexity API error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Perplexity connection error: {e.reason}") from e
    except (TimeoutError, OSError) as e:
        # socket.timeout (== TimeoutError) on SSL read escapes URLError; catch it
        # and any other low-level socket OSError so a slow/dropped call degrades
        # one angle in the orchestrator instead of crashing the whole run.
        raise RuntimeError(f"Perplexity request failed (timeout/socket): {e}") from e

    content = result["choices"][0]["message"]["content"]
    citations = result.get("citations", [])
    return content, citations
