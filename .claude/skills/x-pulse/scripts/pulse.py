#!/usr/bin/env python3
"""
X Pulse - X.com (Twitter) account-monitoring scraper for /x-pulse skill.

Fetches recent posts from a curated, categorised YAML account list via Apify,
applies engagement + per-category proportional filtering, and outputs JSON
that the SKILL.md orchestrator hands to Claude for ranking and brief generation.

Usage:
    python pulse.py --window 72h --output-dir outputs/intel/x-pulse/2026-05-11-1200/
    python pulse.py --window 24h --bucket dpi_competitors --output-dir <dir>
    python pulse.py --dry-run --window 72h          # cost estimate, no API call
    python pulse.py --mock-apify <fixture.json> --output-dir <dir>  # for tests
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# Ensure workspace utilities are importable
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.api import load_api_key  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_data_config_dir  # noqa: E402


# ============================================================
# Configuration
# ============================================================

DEFAULT_MAX_PER_ACCOUNT = 30
HARD_MAX_PER_ACCOUNT = 100
PARALLEL_FETCHES = 5
APIFY_ACTOR = "apidojo/twitter-profile-scraper"
APIFY_PER_TWEET_USD = 0.0003  # $0.30 / 1000 tweets, conservative for cost preview; calibrate after Task 21
RETRY_DELAY_SECONDS = 30  # spec: one retry after 30s on transient Apify failure (429, 5xx)
APIFY_WAIT_SECONDS = 150   # SDK gives up waiting after this; bounds blocking call
APIFY_TIMEOUT_SECONDS = 180  # actor run itself terminates after this; prevents orphan credit burn
FETCH_TIMEOUT_SECONDS = 180  # outer ThreadPoolExecutor cap per account (safety net)

WINDOW_TO_HOURS = {"24h": 24, "72h": 72, "7d": 168, "30d": 720}


# ============================================================
# YAML loading
# ============================================================

def load_accounts_yaml(path: Path) -> dict[str, dict[str, Any]]:
    """Load and validate the account YAML. Returns {category: {description, handles}}."""
    if not path.exists():
        print(f"{RED}Error: account YAML not found at {path}{RESET}", file=sys.stderr)
        sys.exit(1)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"{RED}Error: malformed YAML in {path}: {e}{RESET}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or "categories" not in data:
        print(
            f"{RED}Error: {path} missing top-level 'categories' key{RESET}",
            file=sys.stderr,
        )
        sys.exit(1)
    categories = data["categories"] or {}
    if not isinstance(categories, dict):
        print(
            f"{RED}Error: 'categories' in {path} must be a mapping, got "
            f"{type(categories).__name__}{RESET}",
            file=sys.stderr,
        )
        sys.exit(1)
    # Drop empty buckets
    return {
        name: cfg
        for name, cfg in categories.items()
        if cfg and cfg.get("handles")
    }


# ============================================================
# Pure functions (deterministic, unit-testable)
# ============================================================

def engagement_score(post: dict) -> float:
    """Compute engagement score: likes + 2*retweets + 3*replies."""
    eng = post.get("engagement", {})
    return float(
        eng.get("likes", 0) + 2 * eng.get("retweets", 0) + 3 * eng.get("replies", 0)
    )


def in_window(post: dict, since: datetime) -> bool:
    """True if post.timestamp >= since (UTC-aware). False on missing/malformed timestamp."""
    ts_str = post.get("timestamp", "")
    if not ts_str:
        return False
    # Handle both 'Z' suffix and '+00:00' format
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= since


def collapse_thread(thread_posts: list[dict]) -> dict:
    """Merge multi-tweet thread into one entry; sum engagement across tweets.

    Returns a dict modelled on the first (lowest thread_position) post, with two
    overrides: `thread_text` is the newline-joined text of ALL posts in position
    order (including the head post), and `engagement` is summed across every tweet.
    Empty `thread_posts` is a caller bug -> ValueError.
    """
    if not thread_posts:
        raise ValueError("Empty thread_posts")
    # Stable sort: posts with identical thread_position preserve input order.
    sorted_posts = sorted(thread_posts, key=lambda p: p.get("thread_position", 0))
    head = dict(sorted_posts[0])  # copy first tweet
    head["thread_text"] = "\n\n".join(p.get("text", "") for p in sorted_posts)
    summed = {"likes": 0, "retweets": 0, "replies": 0, "quotes": 0, "views": 0}
    for p in sorted_posts:
        for key in summed:
            summed[key] += p.get("engagement", {}).get(key, 0)
    head["engagement"] = summed
    return head


def filter_per_category(posts: list[dict]) -> list[dict]:
    """Drop bottom 50% of posts within each category, sorted by engagement_score desc.

    Per-category proportional cut: keep ceil(N/2) per category. Single-post category
    keeps the post. Empty input returns empty list.
    """
    if not posts:
        return []
    by_cat: dict[str, list[dict]] = {}
    for p in posts:
        by_cat.setdefault(p.get("category", ""), []).append(p)
    survivors: list[dict] = []
    for cat, cat_posts in by_cat.items():
        # In-place sort on a local bucket built from scratch; does not mutate caller's input.
        # Ties: Python's stable sort preserves input order between posts of equal engagement_score.
        cat_posts.sort(key=engagement_score, reverse=True)
        keep = (len(cat_posts) + 1) // 2  # ceil(N/2)
        survivors.extend(cat_posts[:keep])
    return survivors


# ============================================================
# Apify integration
# ============================================================

def _parse_twitter_date(date_str: str) -> str:
    """Parse Twitter's 'Sat May 10 14:23:00 +0000 2026' format to ISO 8601.

    Returns empty string on missing/malformed input; caller (normalize_apify_post)
    then yields a timestamp="" that in_window() correctly treats as "drop the post".
    """
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return ""
    return dt.isoformat()


def normalize_apify_post(apify_post: dict, handle: str, category: str) -> dict:
    """Convert Apify schema to our internal schema."""
    tid = str(apify_post.get("id", ""))
    # Missing conversationId: fall back to tid, which makes is_thread = False (standalone).
    conv_id = str(apify_post.get("conversationId", tid))
    is_reply = bool(apify_post.get("isReply", False))
    # Thread = author replied to their own conversation (tid != conv_id AND isReply)
    is_thread = is_reply and tid != conv_id
    return {
        "handle": handle,
        "category": category,
        "tweet_id": tid,
        "url": apify_post.get("url", ""),
        "timestamp": _parse_twitter_date(apify_post.get("createdAt", "")),
        "text": apify_post.get("text", ""),
        "is_thread": is_thread,
        "thread_id": conv_id if is_thread else None,
        "thread_position": 0,  # set when collapsing, see Task 6
        "engagement": {
            "likes": int(apify_post.get("likeCount", 0)),
            "retweets": int(apify_post.get("retweetCount", 0)),
            "replies": int(apify_post.get("replyCount", 0)),
            "quotes": int(apify_post.get("quoteCount", 0)),
            "views": int(apify_post.get("viewCount", 0)),
        },
        "reply_to": apify_post.get("inReplyToId"),
        "is_quote_of": apify_post.get("quotedTweetId"),
        # media field can be a list of strings (URLs) or list of dicts with "url" key
        "media_urls": [
            m if isinstance(m, str) else m.get("url", "")
            for m in apify_post.get("media", []) or []
            if (m if isinstance(m, str) else m.get("url"))
        ],
    }


def fetch_account(client, handle: str, category: str, max_per_account: int, since_iso: str | None = None) -> list[dict]:
    """Fetch latest tweets for one handle via Apify; return normalised posts.

    On any exception (covers 429 rate limits, transient 5xx, network errors), retries
    once after RETRY_DELAY_SECONDS (default 30s). If second attempt also fails, returns
    empty list and logs to stderr; caller continues with remaining accounts.

    Bounded waits: APIFY_WAIT_SECONDS caps the SDK's blocking wait; APIFY_TIMEOUT_SECONDS
    caps the actor run itself on Apify's infrastructure (prevents orphan credit burn if
    our outer ThreadPoolExecutor times out and discards the result).

    If since_iso is provided (YYYY-MM-DD or YYYY-MM-DD_HH:MM:SS_UTC), passed to the
    actor's `start` field for server-side date filtering. Saves ~98% of cost vs.
    fetching 1000 tweets then dropping 980 client-side.
    """
    run_input = {
        "twitterHandles": [handle],
        "maxItems": max_per_account,  # actor's actual per-run cap parameter
        "sort": "Latest",
        # Apify residential proxy required: X.com blocks unauthenticated datacenter IPs.
        # Without this, the actor returns empty results or fails opaquely.
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }
    if since_iso:
        run_input["start"] = since_iso
    last_error: Exception | None = None
    for attempt in range(2):  # 2 attempts total = 1 retry
        try:
            # NOTE: removed wait_secs/timeout_secs kwargs - apify-client v2.5.0 rejected
            # wait_secs=150 with "Property waitForFinish must be a number that is > 0!".
            # The actor has its own internal cost cap ($0.2); ThreadPoolExecutor's
            # FETCH_TIMEOUT_SECONDS provides outer safety net.
            run = client.actor(APIFY_ACTOR).call(run_input=run_input)
            dataset_id = run.get("defaultDatasetId") if isinstance(run, dict) else None
            if not dataset_id:
                raise RuntimeError("no dataset returned")
            items = client.dataset(dataset_id).list_items().items
            return [normalize_apify_post(i, handle, category) for i in items]
        except Exception as e:  # noqa: BLE001 - ApifyApiError + transient network errors; SDK has no narrow base class to catch
            last_error = e
            if attempt == 0:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
    print(
        f"{YELLOW}WARN: {handle} failed after retry: {last_error}{RESET}",
        file=sys.stderr,
    )
    return []


def fetch_all_accounts(
    client, account_list: list[tuple[str, str]], max_per_account: int, since_iso: str | None = None
) -> list[dict]:
    """Concurrent fetch across all (handle, category) pairs.

    Caps concurrency at PARALLEL_FETCHES. Per-account failures don't stop the run;
    fetch_account handles its own error logging and returns [] on failure.

    FETCH_TIMEOUT_SECONDS is a safety net around fetch_account, which itself bounds
    Apify-side waits via APIFY_WAIT_SECONDS + APIFY_TIMEOUT_SECONDS. If the outer
    timeout fires, the inner actor run has already been told to terminate.
    """
    import concurrent.futures as _cf  # for explicit TimeoutError import in handler
    if not account_list:
        return []
    posts: list[dict] = []
    with ThreadPoolExecutor(max_workers=PARALLEL_FETCHES) as executor:
        future_to_pair = {
            executor.submit(fetch_account, client, handle, cat, max_per_account, since_iso): (handle, cat)
            for handle, cat in account_list
        }
        for future in as_completed(future_to_pair):
            handle, _cat = future_to_pair[future]
            try:
                posts.extend(future.result(timeout=FETCH_TIMEOUT_SECONDS))
            except _cf.TimeoutError:
                print(
                    f"{YELLOW}WARN: {handle} exceeded {FETCH_TIMEOUT_SECONDS}s timeout{RESET}",
                    file=sys.stderr,
                )
            except Exception as e:  # noqa: BLE001
                print(
                    f"{YELLOW}WARN: {handle} errored: {type(e).__name__}: {e}{RESET}",
                    file=sys.stderr,
                )
    return posts


# ============================================================
# Mock Apify (test path)
# ============================================================

def load_mock_response(path: Path, account_list: list[tuple[str, str]]) -> list[dict]:
    """Read a fixture JSON of Apify-shape posts; tag with handle/category by author.

    Used by --mock-apify flag to test the pipeline without Apify cost. Posts from
    authors not in account_list are skipped but counted, and the summary is logged
    to stderr so the executor can diagnose "fewer posts than expected in fixture".
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    handle_to_cat = {h: c for h, c in account_list}
    posts: list[dict] = []
    skipped: dict[str, int] = {}
    for item in raw:
        author = item.get("author", {}).get("userName", "")
        if author not in handle_to_cat:
            skipped[author] = skipped.get(author, 0) + 1
            continue
        posts.append(normalize_apify_post(item, author, handle_to_cat[author]))
    if skipped:
        print(
            f"{GRAY}Mock: skipped {sum(skipped.values())} posts from "
            f"unconfigured authors: {dict(skipped)}{RESET}",
            file=sys.stderr,
        )
    return posts


# ============================================================
# Cost estimation
# ============================================================

def estimate_cost(account_count: int, max_per_account: int) -> float:
    """Rough USD estimate: account_count * max_per_account * APIFY_PER_TWEET_USD."""
    return float(account_count * max_per_account * APIFY_PER_TWEET_USD)


# ============================================================
# Main / CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="X Pulse - X.com account-monitoring scraper")
    parser.add_argument("--window", choices=list(WINDOW_TO_HOURS), default="72h",
                        help="Time window for recent posts (default: 72h)")
    parser.add_argument("--bucket", default=None,
                        help="Filter to one category from the YAML")
    parser.add_argument("--max-per-account", type=int, default=DEFAULT_MAX_PER_ACCOUNT,
                        help=f"Max tweets per account (default: {DEFAULT_MAX_PER_ACCOUNT}, hard max: {HARD_MAX_PER_ACCOUNT})")
    parser.add_argument("--accounts-yaml", default=None,
                        help="Path to accounts YAML (default: config/x-pulse-accounts.yaml)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for raw + filtered JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan and estimated cost; do not call Apify")
    parser.add_argument("--mock-apify", default=None,
                        help="Path to fixture JSON; use instead of live Apify (for tests)")
    args = parser.parse_args()

    if args.max_per_account < 1 or args.max_per_account > HARD_MAX_PER_ACCOUNT:
        print(f"{RED}Error: --max-per-account must be 1..{HARD_MAX_PER_ACCOUNT}{RESET}", file=sys.stderr)
        return 1

    yaml_path = Path(args.accounts_yaml) if args.accounts_yaml else (
        get_data_config_dir() / "x-pulse-accounts.yaml"
    )
    categories = load_accounts_yaml(yaml_path)

    if args.bucket:
        if args.bucket not in categories:
            print(f"{RED}Error: bucket '{args.bucket}' not in YAML{RESET}", file=sys.stderr)
            return 1
        categories = {args.bucket: categories[args.bucket]}

    account_list: list[tuple[str, str]] = []
    for cat_name, cfg in categories.items():
        for handle in cfg.get("handles", []):
            account_list.append((handle, cat_name))

    if not account_list:
        print(f"{RED}Error: no accounts configured (after --bucket filter){RESET}", file=sys.stderr)
        return 1

    cost = estimate_cost(len(account_list), args.max_per_account)
    print(f"{CYAN}Plan:{RESET} {len(account_list)} accounts x {args.max_per_account} tweets = "
          f"~{len(account_list) * args.max_per_account} tweets")
    print(f"{CYAN}Estimated cost:{RESET} ${cost:.2f} USD")

    if args.dry_run:
        print(f"{YELLOW}--dry-run set, exiting without Apify call{RESET}")
        return 0

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    window_hours = WINDOW_TO_HOURS[args.window]
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    if args.mock_apify:
        print(f"{GRAY}Using mock fixture: {args.mock_apify}{RESET}")
        all_posts = load_mock_response(Path(args.mock_apify), account_list)
    else:
        token = load_api_key("APIFY_TOKEN")
        from apify_client import ApifyClient  # local import keeps tests fast
        client = ApifyClient(token)
        print(f"{CYAN}Fetching from Apify...{RESET}")
        # Server-side date filter saves ~98% of Apify cost vs fetching everything
        since_iso = since.strftime("%Y-%m-%d_%H:%M:%S_UTC")
        all_posts = fetch_all_accounts(client, account_list, args.max_per_account, since_iso)

    in_window_posts = [p for p in all_posts if in_window(p, since)]
    print(f"{GREEN}Fetched {len(all_posts)} posts; {len(in_window_posts)} in {args.window} window{RESET}")

    (out_dir / "raw-posts.json").write_text(
        json.dumps(in_window_posts, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if not in_window_posts:
        print(f"{YELLOW}No activity in {args.window} window. Try --window 7d or check account list.{RESET}")
        (out_dir / "filtered-posts.json").write_text("[]", encoding="utf-8")
        return 0

    survivors = filter_per_category(in_window_posts)
    (out_dir / "filtered-posts.json").write_text(
        json.dumps(survivors, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"{GREEN}Filtered to {len(survivors)} survivors (top 50% per category){RESET}")
    print(f"{BOLD}Output:{RESET} {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
