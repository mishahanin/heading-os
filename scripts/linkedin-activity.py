#!/usr/bin/env python3
"""Scrape a LinkedIn profile's recent activity feed authenticated via Floorp cookies.

Launches Playwright Chromium through the Decodo residential proxy, injects the
li_at + JSESSIONID cookies from the Floorp ClaudeCode profile, navigates to
/in/<slug>/recent-activity/all/, scrolls to load the requested number of posts,
then extracts structured post data from the rendered DOM.

Usage:
  python scripts/linkedin-activity.py --slug mishahanin --limit 3
  python scripts/linkedin-activity.py --slug mishahanin --limit 5 --headed
  python scripts/linkedin-activity.py --slug mishahanin --proxy-slot 2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.firefox_cookies import get_cookies
from scripts.utils.workspace import get_default_tz, get_default_tz_name, get_outputs_dir, load_env


def parse_proxy(url: str) -> dict:
    """Convert user:pass@host:port URL into Playwright proxy dict."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"Proxy URL missing host or port: {url}")
    return {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        "username": parsed.username or "",
        "password": parsed.password or "",
    }


def floorp_cookies_for_playwright() -> list[dict]:
    """Read Floorp ClaudeCode cookies and reshape for Playwright context.add_cookies()."""
    raw = get_cookies("linkedin.com", profile_name="ClaudeCode", browser="floorp")
    if "li_at" not in raw:
        raise RuntimeError(
            "li_at cookie missing from Floorp ClaudeCode profile. "
            "Log in to LinkedIn in that profile first."
        )
    cookies: list[dict] = []
    for name, value in raw.items():
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".linkedin.com",
            "path": "/",
            "secure": True,
            "httpOnly": name in {"li_at", "JSESSIONID", "bcookie", "bscookie"},
            "sameSite": "None",
        })
    return cookies


def check_session_alive(cookies: dict[str, str], slug: str) -> tuple[bool, str]:
    """Probe a strictly auth-gated endpoint to confirm the li_at session is live.

    Uses `/in/{slug}/` which requires auth for a real response. `/feed/` is a false
    positive: it returns 200 for everyone. LinkedIn signals session termination by
    setting `li_at=delete me; Max-Age=0` in the response.
    Returns (is_alive, reason).
    """
    try:
        import requests
    except ImportError:
        return True, "requests not available - skipping pre-flight"
    from requests.cookies import RequestsCookieJar
    jar = RequestsCookieJar()
    for n, v in cookies.items():
        jar.set(n, v, domain=".linkedin.com", path="/")
    try:
        r = requests.get(
            f"https://www.linkedin.com/in/{slug}/",
            cookies=jar,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
                                   "Gecko/20100101 Firefox/128.0"},
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return True, f"pre-flight probe failed ({exc}) - proceeding anyway"
    set_cookie = r.headers.get("Set-Cookie", "")
    if "li_at=delete me" in set_cookie or "li_at=\"delete me\"" in set_cookie:
        return False, "server set `li_at=delete me` - session terminated"
    if r.status_code == 429:
        return False, "429 Too Many Requests - back off and retry in 10+ minutes"
    if r.status_code == 302:
        loc = r.headers.get("Location", "")
        if f"/in/{slug}" in loc or "/authwall" in loc or "/login" in loc:
            return False, f"bounced to {loc[:80]} - session invalidated"
    return True, f"status={r.status_code}"


def scroll_until(page, min_posts: int, max_scrolls: int = 10) -> int:
    """Scroll the feed until at least min_posts activity cards are present."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    selector = '[data-urn*="urn:li:activity:"], div.feed-shared-update-v2'
    for i in range(max_scrolls):
        count = page.locator(selector).count()
        if count >= min_posts:
            return count
        page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            time.sleep(1.5)  # feed may have already rendered; proceed
    return page.locator(selector).count()


def extract_posts(html: str, limit: int) -> list[dict]:
    """Parse rendered HTML for post data.

    Strategy: walk bpr-guid JSON blobs (post-JS-execution those are populated with
    Voyager UpdateV2 objects for each visible post). Fall back to JSON-LD and
    regex-on-DOM if bpr is empty.
    """
    posts: dict[str, dict] = {}  # urn -> record

    # Strategy 1: bpr-guid Voyager blobs
    bpr = re.findall(r'<code[^>]*id="bpr-guid-\d+"[^>]*>(.*?)</code>', html, re.DOTALL)
    for blob in bpr:
        try:
            data = json.loads(unescape(blob))
        except json.JSONDecodeError:
            continue
        included = data.get("included") or []
        for obj in included:
            t = obj.get("$type", "")
            urn = obj.get("updateMetadata", {}).get("urn") or obj.get("urn", "")
            if "UpdateV2" not in t and "FeedUpdate" not in t:
                continue
            if not urn or "urn:li:activity:" not in urn:
                continue
            rec = posts.setdefault(urn, {"urn": urn})
            # Text commentary
            commentary = obj.get("commentary") or {}
            text = commentary.get("text", {}).get("text") if isinstance(commentary.get("text"), dict) else commentary.get("text")
            if text and "text" not in rec:
                rec["text"] = text
            # Engagement counts
            social = obj.get("socialDetail") or {}
            counts = social.get("totalSocialActivityCounts") or {}
            for k in ("numLikes", "numComments", "numShares", "numImpressions"):
                if k in counts and k not in rec:
                    rec[k] = counts[k]

    # Strategy 2: JSON-LD (always available, but only exposes likes + text)
    jsonld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if jsonld:
        try:
            g = json.loads(jsonld.group(1)).get("@graph", [])
            for node in g:
                if node.get("@type") != "DiscussionForumPosting":
                    continue
                url = node.get("mainEntityOfPage") or node.get("url") or ""
                m = re.search(r"activity-(\d+)", url)
                if not m:
                    continue
                urn = f"urn:li:activity:{m.group(1)}"
                rec = posts.setdefault(urn, {"urn": urn})
                rec.setdefault("text", node.get("text") or node.get("headline") or "")
                rec.setdefault("date", node.get("datePublished", ""))
                rec.setdefault("url", url)
                likes = node.get("interactionStatistic", {}).get("userInteractionCount")
                if likes is not None:
                    rec.setdefault("numLikes", likes)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Backfill URL from urn when absent
    for urn, rec in posts.items():
        rec.setdefault("url", f"https://www.linkedin.com/feed/update/{urn}/")

    # Sort by date desc (fall back to urn as tiebreak - higher urn = more recent)
    ordered = sorted(
        posts.values(),
        key=lambda r: (r.get("date", ""), r.get("urn", "")),
        reverse=True,
    )
    return ordered[:limit]


def render_markdown(posts: list[dict], slug: str) -> str:
    lines = [f"# LinkedIn Recent Activity - {slug}", "", f"_Scraped via Playwright + Floorp auth + Decodo proxy_", ""]
    for i, p in enumerate(posts, 1):
        metrics = []
        for label, key in [("likes", "numLikes"), ("comments", "numComments"), ("shares", "numShares"), ("views", "numImpressions")]:
            if key in p:
                metrics.append(f"{p[key]} {label}")
        header = f"## [{i}] {p.get('date', '')[:10] or 'unknown date'}"
        if metrics:
            header += f" - {', '.join(metrics)}"
        lines.append(header)
        lines.append("")
        lines.append(f"URL: {p.get('url', '')}")
        lines.append("")
        text = p.get("text", "").strip()
        lines.append(text if text else "_[no text extracted]_")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True, help="LinkedIn profile slug, e.g. mishahanin")
    parser.add_argument("--limit", type=int, default=3, help="Posts to extract (default 3)")
    parser.add_argument("--profile", default="ClaudeCode", help="Floorp profile (default ClaudeCode)")
    parser.add_argument("--proxy-slot", type=int, default=0, choices=[0, 1, 2, 3],
                        help="DECODO_PROXY_N slot. 0 (default) = no proxy. "
                             "Authenticated LinkedIn sessions typically fail through proxies due to "
                             "IP-travel detection; leave at 0 unless you know the Floorp session was "
                             "established via that Decodo slot.")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--timeout", type=int, default=45000, help="Per-action timeout (ms)")
    parser.add_argument("--engine", default="firefox", choices=["firefox", "chromium"],
                        help="Playwright engine (default firefox, which matches the Floorp "
                             "session family and avoids cookie-quoting issues).")
    args = parser.parse_args()

    load_env()

    import os
    proxy_cfg = None
    if args.proxy_slot:
        proxy_url = os.environ.get(f"DECODO_PROXY_{args.proxy_slot}")
        if not proxy_url:
            print(f"{RED}ERROR: DECODO_PROXY_{args.proxy_slot} not in .env{RESET}", file=sys.stderr)
            return 1
        proxy_cfg = parse_proxy(proxy_url)

    try:
        cookies = floorp_cookies_for_playwright()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"{RED}ERROR: {exc}{RESET}", file=sys.stderr)
        return 2
    print(f"{GRAY}Loaded {len(cookies)} cookies from Floorp {args.profile} profile{RESET}")

    raw_cookies = {c["name"]: c["value"] for c in cookies}
    alive, reason = check_session_alive(raw_cookies, args.slug)
    if not alive:
        print(f"{RED}ERROR: LinkedIn session invalid - {reason}{RESET}", file=sys.stderr)
        print(f"{YELLOW}FIX: Open Floorp, visit linkedin.com, log in (may require "
              f"completing a challenge), then rerun this script.{RESET}", file=sys.stderr)
        return 3
    print(f"{GRAY}Pre-flight: session alive ({reason}){RESET}")

    from playwright.sync_api import sync_playwright

    url = f"https://www.linkedin.com/in/{args.slug}/recent-activity/all/"
    out_dir = get_outputs_dir() / "browser"
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        launch_kwargs = {"headless": not args.headed}
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg
        engine = getattr(pw, args.engine)
        browser = engine.launch(**launch_kwargs)
        try:
            ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
                  "Gecko/20100101 Firefox/128.0" if args.engine == "firefox"
                  else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            context = browser.new_context(
                user_agent=ua,
                locale="en-US",
                timezone_id=get_default_tz_name(),
                viewport={"width": 1440, "height": 900},
            )
            context.add_cookies(cookies)
            page = context.new_page()
            page.set_default_timeout(args.timeout)
            print(f"{CYAN}Navigating to {url}{RESET}")
            response = page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else None
            page.wait_for_load_state("networkidle", timeout=args.timeout)

            if status == 429 or "/hp?" in page.url or "Too Many Requests" in page.content()[:2000]:
                print(f"{RED}ERROR: LinkedIn returned 429 (rate limited on this IP).{RESET}", file=sys.stderr)
                print(f"{YELLOW}FIX: wait 10-15 minutes, or switch Mullvad exit for a fresh IP, then rerun.{RESET}", file=sys.stderr)
                return 5
            if "/login" in page.url or "/authwall" in page.url:
                print(f"{RED}ERROR: hit auth wall at {page.url}. li_at cookie may be expired.{RESET}", file=sys.stderr)
                return 3

            count = scroll_until(page, min_posts=args.limit + 1, max_scrolls=8)
            print(f"{GRAY}Activity cards visible after scroll: {count}{RESET}")

            html = page.content()
            (out_dir / "linkedin-activity-rendered.html").write_text(html, encoding="utf-8")
        finally:
            browser.close()

    posts = extract_posts(html, limit=args.limit)
    if not posts:
        print(f"{YELLOW}WARNING: no posts extracted. See linkedin-activity-rendered.html{RESET}")
        return 4

    md = render_markdown(posts, args.slug)
    md_path = out_dir / "linkedin-activity-auth.md"
    json_path = out_dir / "linkedin-activity-auth.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(posts, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{BOLD}{GREEN}Extracted {len(posts)} post(s){RESET}")
    print(f"  Markdown: {md_path.resolve()}")
    print(f"  JSON:     {json_path.resolve()}")
    print(f"  HTML:     {(out_dir / 'linkedin-activity-rendered.html').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
