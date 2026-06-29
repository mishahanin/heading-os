#!/usr/bin/env python3
"""
YT Pulse - YouTube search and metadata extraction for /yt-pulse skill.

Searches YouTube via yt-dlp using YouTube's native date filters,
scores by engagement, and outputs structured JSON for Claude to analyze.

Uses extract_flat mode with YouTube search URL parameters to avoid
bot detection issues with full metadata extraction.

Usage:
    python pulse.py -q "AI agents" -t 72h -m 50 -o results.json
"""

import argparse
import json
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print(json.dumps({"error": "yt-dlp not installed. Run: pip install yt-dlp"}))
    sys.exit(1)


# YouTube search filter parameters (sp= values)
# These tell YouTube to only return videos from specific time windows
TIMEFRAME_SP = {
    "24h": "EgIIAg%3D%3D",  # Today
    "72h": "EgIIAw%3D%3D",  # This week (closest to 72h)
    "7d": "EgIIAw%3D%3D",   # This week
    "30d": "EgIIBA%3D%3D",  # This month
}


def parse_browser_spec(spec: str):
    """
    Parse a BROWSER[:PROFILE] spec into a yt-dlp cookiesfrombrowser tuple.

    Returns None if spec is falsy or 'none' (explicit opt-out).
    """
    if not spec or spec.strip().lower() == "none":
        return None
    parts = spec.split(":", 1)
    browser = parts[0].strip().lower()
    profile = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
    return (browser, profile, None, None)


def search_youtube(
    query: str,
    timeframe: str,
    max_results: int = 50,
    cookies: str = None,
    browser: str = None,
) -> list:
    """
    Search YouTube using yt-dlp with YouTube's native date filter.

    Uses extract_flat mode (fast, avoids bot detection) combined with
    YouTube's sp= search parameter for server-side date filtering.
    Retries once on failure with a 2-second delay.
    """
    sp = TIMEFRAME_SP.get(timeframe, TIMEFRAME_SP["72h"])
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded_query}&sp={sp}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "no_check_certificates": True,
        "playlistend": max_results,
        "ignoreerrors": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    }
    if cookies:
        ydl_opts["cookiefile"] = cookies
    else:
        browser_spec = parse_browser_spec(browser)
        if browser_spec:
            ydl_opts["cookiesfrombrowser"] = browser_spec

    videos = []
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(url, download=False)
                if result and "entries" in result:
                    for entry in result["entries"]:
                        if entry is None:
                            continue
                        videos.append(entry)
            break
        except Exception as e:
            if attempt == 0:
                print(f"Retry after error: {e}", file=sys.stderr)
                time.sleep(2)
                videos = []
            else:
                print(
                    json.dumps(
                        {"error": f"YouTube search failed: {e}", "videos": []}
                    ),
                    file=sys.stdout,
                )
                sys.exit(1)

    return videos


def calculate_engagement_score(video: dict) -> float:
    """
    Score videos by engagement quality, not just raw views.

    Since we use YouTube's date filter (all results are within the timeframe),
    we score primarily on view count with duration modifiers.
    Without exact upload timestamps, we use views as the primary signal -
    higher views within the same time window = more engagement.

    Duration modifiers:
    - Penalize very short videos (< 2 min) - likely shorts or low effort
    - Boost 5-20 min videos - sweet spot for in-depth analysis content
    """
    views = video.get("view_count") or 0
    duration = video.get("duration") or 0

    score = float(views)

    # Subscriber normalization if available
    subscribers = video.get("channel_follower_count")
    if subscribers and subscribers > 0:
        ratio = (views / subscribers) * 1000
        score = score * 0.7 + ratio * 0.3

    # Duration modifiers
    if duration < 120:
        score *= 0.5  # Penalize very short content / shorts
    elif 300 <= duration <= 1200:
        score *= 1.2  # Boost 5-20 min sweet spot

    return round(score, 2)


def format_duration(seconds) -> str:
    """Convert seconds to human-readable duration string."""
    if not seconds:
        return "unknown"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def clean_video(v: dict) -> dict:
    """Extract clean output fields from raw yt-dlp entry."""
    return {
        "video_id": v.get("id", ""),
        "url": v.get("url") or f"https://www.youtube.com/watch?v={v.get('id', '')}",
        "title": v.get("title", ""),
        "channel": v.get("uploader") or v.get("channel") or "",
        "channel_id": v.get("channel_id", ""),
        "view_count": v.get("view_count") or 0,
        "duration_seconds": int(v.get("duration") or 0),
        "duration_string": format_duration(v.get("duration")),
        "description": (v.get("description") or "")[:500],
        "thumbnail": "",
        "subscriber_count": v.get("channel_follower_count"),
        "engagement_score": v.get("_engagement_score", 0),
        "more_from_channel_count": v.get("_more_from_channel_count", 0),
    }


def _channel_key(video: dict) -> str:
    """Group key for the per-channel cap. Prefers channel_id, falls back to normalised name (Y2)."""
    cid = (video.get("channel_id") or "").strip()
    if cid:
        return cid
    name = (video.get("uploader") or video.get("channel") or "").strip().lower()
    return name


def apply_per_channel_cap(videos: list, cap: int) -> tuple[list, dict]:
    """Enforce max `cap` videos per channel after engagement scoring.

    Returns (capped_videos, rollup_counts) where rollup_counts maps
    channel_key -> number of additional videos suppressed for that channel.

    Pass cap=0 to disable (legacy uncapped behaviour).

    The surviving N per channel are the highest-scoring N (input must already
    be sorted by score descending). Filtered videos surface via
    rollup_counts; callers attach the count to the highest-scoring survivor
    of each channel as `_more_from_channel_count`.
    """
    if cap <= 0:
        return videos, {}

    seen_per_channel: dict = {}
    survivors: list = []
    rollup_counts: dict = {}

    for v in videos:
        key = _channel_key(v)
        if not key:
            survivors.append(v)
            continue
        count = seen_per_channel.get(key, 0)
        if count < cap:
            seen_per_channel[key] = count + 1
            survivors.append(v)
        else:
            rollup_counts[key] = rollup_counts.get(key, 0) + 1

    # Attach rollup count to the FIRST (highest-scoring) survivor for each channel
    seen_marked: set = set()
    for v in survivors:
        key = _channel_key(v)
        if key in rollup_counts and key not in seen_marked:
            v["_more_from_channel_count"] = rollup_counts[key]
            seen_marked.add(key)

    return survivors, rollup_counts


def main():
    parser = argparse.ArgumentParser(
        description="YT Pulse - YouTube search and metadata extraction"
    )
    parser.add_argument("--query", "-q", required=True, help="YouTube search query")
    parser.add_argument(
        "--timeframe",
        "-t",
        default="72h",
        choices=list(TIMEFRAME_SP.keys()),
        help="Time window (default: 72h)",
    )
    parser.add_argument(
        "--max-results", "-m", type=int, default=50, help="Max videos to fetch"
    )
    parser.add_argument(
        "--min-duration", type=int, default=120, help="Min video duration in seconds"
    )
    parser.add_argument(
        "--max-duration", type=int, default=None, help="Max video duration in seconds"
    )
    parser.add_argument(
        "--min-views", type=int, default=0, help="Min view count filter"
    )
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument(
        "--cookies", help="Path to Netscape-format cookie file for YouTube auth"
    )
    parser.add_argument(
        "--browser",
        default="brave:ClaudeCode",
        help=(
            "Browser to extract YouTube cookies from (format: BROWSER[:PROFILE]). "
            "Default: brave:ClaudeCode (cross-platform, yt-dlp-native). "
            "Pass 'none' to disable. Overridden by --cookies if both are supplied."
        ),
    )
    parser.add_argument(
        "--per-channel-cap",
        type=int,
        default=3,
        help=(
            "Max videos per channel in the ranked output (default 3). "
            "v1.3 BEHAVIOUR CHANGE: prevents any single channel from dominating. "
            "Pass 0 to restore legacy uncapped behaviour."
        ),
    )

    args = parser.parse_args()

    # Search with YouTube's native date filter
    print(
        f"Searching YouTube for: {args.query} (timeframe: {args.timeframe}, max: {args.max_results})...",
        file=sys.stderr,
    )
    raw_videos = search_youtube(
        args.query, args.timeframe, args.max_results, args.cookies, args.browser
    )
    total_fetched = len(raw_videos)
    print(f"Fetched {total_fetched} videos. Filtering...", file=sys.stderr)

    videos = raw_videos

    # Filter by duration
    if args.min_duration:
        videos = [v for v in videos if (v.get("duration") or 0) >= args.min_duration]
    if args.max_duration:
        videos = [v for v in videos if (v.get("duration") or 0) <= args.max_duration]

    # Filter by views
    if args.min_views > 0:
        videos = [v for v in videos if (v.get("view_count") or 0) >= args.min_views]

    # Calculate engagement scores
    for v in videos:
        v["_engagement_score"] = calculate_engagement_score(v)

    # Sort by engagement score descending
    videos.sort(key=lambda x: x.get("_engagement_score", 0), reverse=True)

    # Apply per-channel cap (v1.3) - prevents single-channel domination of output
    videos, rollup_counts = apply_per_channel_cap(videos, args.per_channel_cap)
    if rollup_counts:
        suppressed_total = sum(rollup_counts.values())
        print(
            f"Per-channel cap={args.per_channel_cap}: suppressed {suppressed_total} videos "
            f"across {len(rollup_counts)} channels.",
            file=sys.stderr,
        )

    # Clean output
    clean_videos = [clean_video(v) for v in videos]

    # Extract thumbnails from raw data (pick highest resolution by area)
    for i, v in enumerate(videos):
        thumbs = v.get("thumbnails")
        if thumbs and len(thumbs) > 0:
            best = max(
                thumbs,
                key=lambda t: t.get("width", 0) * t.get("height", 0),
            )
            clean_videos[i]["thumbnail"] = best.get("url", "")

    result = {
        "query": args.query,
        "timeframe": args.timeframe,
        "search_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_fetched": total_fetched,
        "filtered_results": len(clean_videos),
        "videos": clean_videos,
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(
            f"Results saved to {args.output} ({len(clean_videos)} videos)",
            file=sys.stderr,
        )
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"Done. {len(clean_videos)} videos after filtering.", file=sys.stderr)


if __name__ == "__main__":
    main()
