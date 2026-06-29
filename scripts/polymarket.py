#!/usr/bin/env python3
"""polymarket.py - Query Polymarket Gamma API for prediction-market signal.

Free public API (gamma-api.polymarket.com), no auth required. Used by
/market-brief and /ceo-intel as a 14th source ("Markets Are Pricing" section).

Conditional firing via topic whitelist - only queries when topic matches
AI / big tech / elections / geopolitics / crypto / conflicts / macro / sports.
31C-niche topics (DPI, sovereign telecom, regional defense procurement) skip
because Polymarket has zero useful coverage there.

Usage:
    python scripts/polymarket.py "AI agents"
    python scripts/polymarket.py "Apple" --keywords "company,stock,iPhone"
    python scripts/polymarket.py "regional sovereign telecom"  # skips, prints empty
    python scripts/polymarket.py "Iran tensions" --output markdown
    python scripts/polymarket.py "AI agents" --min-volume-usd 50000 --limit 3

Output: JSON wrapper with markets[], skip_reason, query_used, whitelist_match.
Or markdown table including internal-use footer.

CEO-EYES-ONLY USAGE: Polymarket data is internal signal only. NEVER quote
in external 31C communication (proposals, letters, partnership docs, posts).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


GAMMA_ENDPOINT = "https://gamma-api.polymarket.com/markets"
DEFAULT_TIMEOUT = 30
INTERNAL_USE_FOOTER = (
    "*Polymarket data is internal signal only; never used in external 31C communication.*"
)


WHITELIST_POSITIVE: dict[str, list[str]] = {
    "ai_big_tech": [
        "ai", "artificial intelligence", "agi", "openai", "anthropic", "google", "alphabet",
        "microsoft", "meta", "facebook", "nvidia", "apple", "amazon", "tesla", "deepmind",
        "claude", "gpt", "gemini", "llama", "model release", "ai regulation",
    ],
    "elections": [
        "election", "primary", "vote", "presidential", "senate", "congress", "trump", "biden",
        "harris", "uk election", "general election", "midterm", "parliament", "modi",
    ],
    "geopolitics_conflicts": [
        "iran", "israel", "russia", "ukraine", "china", "taiwan", "north korea", "gaza",
        "war", "ceasefire", "strait of hormuz", "oil supply", "sanctions", "nato", "putin",
        "xi jinping", "kim jong",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "solana", "crypto", "etf", "sec ruling",
        "stablecoin", "defi",
    ],
    "macro_fed": [
        "fed", "fomc", "interest rate", "recession", "cpi", "inflation", "gdp", "unemployment",
        "rate cut", "rate hike", "treasury yield",
    ],
    "global_sports": [
        "world cup", "olympics", "super bowl", "nba finals", "champions league", "world series",
    ],
    "corporate_events": [
        "ipo", "merger", "acquisition", "earnings beat", "earnings miss", "stock buyback",
    ],
}

WHITELIST_NEGATIVE: list[str] = [
    "dpi", "deep packet inspection", "legacy dpi vendor", "competitor dpi",
    "odun.one", "odun one", "31c", "31 concept", "sovereign telecom", "data sovereignty",
    "telco procurement", "tribe communication", "tribe state",
]


def match_whitelist(topic: str) -> tuple[str | None, bool]:
    """Apply precedence rule (P2): positive match required to fire.

    Returns (positive_category_or_None, has_negative_match).
    Caller logic: if positive matched, fire (positive wins over negative).
    If only negative matched (no positive), skip.
    If neither matched, also skip.
    """
    topic_lower = topic.lower()
    positive_match: str | None = None
    for category, terms in WHITELIST_POSITIVE.items():
        if any(t in topic_lower for t in terms):
            positive_match = category
            break

    has_negative = any(neg in topic_lower for neg in WHITELIST_NEGATIVE)
    return positive_match, has_negative


def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict | list:
    """GET JSON with one retry on 429/5xx.

    Sends a browser-like User-Agent header. Without it, Cloudflare in front of
    gamma-api.polymarket.com returns 403 to Python urllib's default UA.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; 31C-Intel/1.0; +https://31c.io)",
    }
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(2)
                continue
            raise
        except urllib.error.URLError:
            if attempt == 1:
                time.sleep(2)
                continue
            raise


def fetch_active_markets(limit: int = 500) -> list[dict]:
    """Fetch active, non-closed markets ordered by volume desc.

    Uses Gamma API: GET /markets?active=true&closed=false&limit=N&order=volume&ascending=false
    Returns raw market objects.

    Default limit=500 because the top-by-volume tail is dominated by esports
    and sports props; topic substring filtering needs a larger pool to surface
    relevant election / AI / geopolitics markets that have lower volume.
    """
    params = urllib.parse.urlencode({
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "volume",
        "ascending": "false",
    })
    url = f"{GAMMA_ENDPOINT}?{params}"
    data = _http_get(url)
    if isinstance(data, list):
        return data
    return data.get("markets", []) if isinstance(data, dict) else []


def filter_markets(
    markets: list[dict],
    topic: str,
    keywords: list[str] | None,
    min_volume_usd: float,
) -> list[dict]:
    """P3: client-side filter on `question` field, case-insensitive substring.

    A market passes if the topic appears in the question text. Keywords (P4)
    further narrow the match - market must contain at least one keyword if any
    are provided. P5: drop markets below the volume threshold.
    """
    topic_lower = topic.lower()
    keyword_set = {k.strip().lower() for k in (keywords or []) if k.strip()}
    out: list[dict] = []
    for m in markets:
        question = (m.get("question") or "").lower()
        if topic_lower not in question:
            continue
        if keyword_set and not any(kw in question for kw in keyword_set):
            continue
        try:
            volume = float(m.get("volume") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        if volume < min_volume_usd:
            continue
        out.append(m)
    return out


def _parse_outcomes_and_prices(market: dict) -> list[dict]:
    """Parse outcomes (e.g., '["Yes","No"]') and outcomePrices into [{name, probability}]."""
    raw_outcomes = market.get("outcomes")
    raw_prices = market.get("outcomePrices")
    try:
        names = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else (raw_outcomes or [])
    except (json.JSONDecodeError, TypeError):
        names = []
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices or [])
    except (json.JSONDecodeError, TypeError):
        prices = []
    out = []
    for i, name in enumerate(names):
        try:
            prob = float(prices[i]) if i < len(prices) else 0.0
        except (ValueError, TypeError):
            prob = 0.0
        out.append({"name": str(name), "probability": prob})
    return out


def normalise_market(market: dict) -> dict:
    """Reduce a Gamma market to the brief-friendly shape."""
    slug = market.get("slug") or market.get("conditionId") or ""
    try:
        volume_usd = float(market.get("volume") or 0)
    except (TypeError, ValueError):
        volume_usd = 0.0
    return {
        "question": market.get("question", ""),
        "outcomes": _parse_outcomes_and_prices(market),
        "end_date": market.get("endDate") or market.get("end_date") or None,
        "volume_usd": volume_usd,
        "link": f"https://polymarket.com/event/{slug}" if slug else "",
    }


def render_markdown(markets: list[dict]) -> str:
    """P7: pin columns - Market | Top Outcome | Probability | Volume | End Date.

    Appends the P1 internal-use footer line.
    """
    if not markets:
        return f"_No matching prediction markets found._\n\n{INTERNAL_USE_FOOTER}\n"

    lines = ["| Market | Top Outcome | Probability | Volume | End Date |",
             "|---|---|---|---|---|"]
    for m in markets:
        outcomes = m.get("outcomes", [])
        if outcomes:
            top = max(outcomes, key=lambda o: o.get("probability", 0))
            top_name = top.get("name", "?")
            top_prob = f"{top.get('probability', 0) * 100:.0f}%"
        else:
            top_name = "?"
            top_prob = "?"
        question = (m.get("question", "") or "").replace("|", "\\|")
        if len(question) > 80:
            question = question[:77] + "..."
        end_date = (m.get("end_date") or "")[:10]
        volume_str = f"${m.get('volume_usd', 0):,.0f}"
        lines.append(f"| {question} | {top_name} | {top_prob} | {volume_str} | {end_date} |")
    lines.append("")
    lines.append(INTERNAL_USE_FOOTER)
    return "\n".join(lines) + "\n"


def query_polymarket(
    topic: str,
    keywords: list[str] | None = None,
    limit: int = 5,
    min_volume_usd: float = 10000.0,
) -> dict:
    """Run the full pipeline and return the wrapper JSON (P6 shape).

    Returns:
        {
            "markets": [...],
            "skip_reason": null | "outside_whitelist" | "no_matches" | "fetch_error",
            "query_used": "...",
            "whitelist_match": "ai_big_tech" | ... | null,
            "error": null | "...",  # only when fetch fails
        }
    """
    positive, _has_negative = match_whitelist(topic)
    if positive is None:
        return {"markets": [], "skip_reason": "outside_whitelist",
                "query_used": topic, "whitelist_match": None}

    try:
        raw_markets = fetch_active_markets(limit=500)
    except Exception as e:  # broad: covers HTTPError, URLError, JSONDecodeError
        return {"markets": [], "skip_reason": "fetch_error",
                "query_used": topic, "whitelist_match": positive,
                "error": f"Gamma API failed: {str(e)[:200]}"}

    filtered = filter_markets(raw_markets, topic, keywords, min_volume_usd)
    if not filtered:
        return {"markets": [], "skip_reason": "no_matches",
                "query_used": topic, "whitelist_match": positive}

    top_n = filtered[:limit]
    return {
        "markets": [normalise_market(m) for m in top_n],
        "skip_reason": None,
        "query_used": topic,
        "whitelist_match": positive,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Polymarket Gamma API for prediction-market signal")
    parser.add_argument("topic", help="Topic to query (e.g., 'AI agents', 'Iran tensions')")
    parser.add_argument("--keywords",
                        help="Comma-separated disambiguators (e.g., 'company,stock' for 'Apple')")
    parser.add_argument("--output", default="json", choices=["json", "markdown"])
    parser.add_argument("--limit", type=int, default=5,
                        help="Max markets to return (default 5)")
    parser.add_argument("--min-volume-usd", type=float, default=10000.0,
                        help="Filter markets below this volume threshold (default 10000)")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
    result = query_polymarket(args.topic, keywords, args.limit, args.min_volume_usd)

    if args.output == "json":
        print(json.dumps(result, indent=2))
    else:
        if result.get("skip_reason") == "outside_whitelist":
            return 0
        print(render_markdown(result.get("markets", [])))

    return 0


if __name__ == "__main__":
    sys.exit(main())
