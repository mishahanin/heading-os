"""Operational visibility sources for the Settings page.

- read_telemetry_summary: counts page_view/launch/return_to_browser/finalize
  events from .daemon-state/usage.jsonl, scoped to today + last 7 days.
- read_log_tail: returns the last N lines from .daemon-state/bridge.log,
  capped by line count + total bytes.

Both read from .daemon-state/ which is per-workspace, per-user, and
contains no credentials. Safe to surface to the authenticated browser.
"""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_TAIL_LINES = 50
LOG_TAIL_MAX_BYTES = 200_000  # cap total bytes returned even if 50 lines is huge
USAGE_MAX_LINES = 20_000  # safety: stop after this many lines (the file rotates eventually)

TELEMETRY_EVENT_TYPES = ("page_view", "launch", "return_to_browser", "finalize")


def _parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def read_telemetry_summary(workspace_root: Path, now: datetime | None = None) -> dict:
    """Aggregate usage.jsonl events into today + last-7-days summaries.

    Returns:
        {
            "ok": bool,
            "today": {event_type: count, ...},
            "last_7d": {event_type: count, ...},
            "today_total": int,
            "last_7d_total": int,
            "last_event_ts": ISO 8601 or None,
            "file_size_bytes": int or None,
        }
    """
    if now is None:
        now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    cutoff_7d = now - timedelta(days=7)

    usage_path = workspace_root / ".daemon-state" / "usage.jsonl"
    if not usage_path.exists():
        return {
            "ok": True,
            "today": {},
            "last_7d": {},
            "today_total": 0,
            "last_7d_total": 0,
            "last_event_ts": None,
            "file_size_bytes": None,
        }

    today_counts: Counter = Counter()
    last_7d_counts: Counter = Counter()
    last_event_ts: str | None = None

    try:
        size = usage_path.stat().st_size
        with usage_path.open("r", encoding="utf-8") as f:
            for line_num, raw in enumerate(f):
                if line_num >= USAGE_MAX_LINES:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("ts")
                evt = rec.get("event")
                if not ts_str or not evt:
                    continue
                ts = _parse_iso(ts_str)
                if ts is None:
                    continue
                if ts >= cutoff_7d:
                    last_7d_counts[evt] += 1
                if ts_str.startswith(today_str):
                    today_counts[evt] += 1
                last_event_ts = ts_str  # JSONL is append-only, last line wins
    except OSError as e:
        return {
            "ok": False,
            "today": {},
            "last_7d": {},
            "today_total": 0,
            "last_7d_total": 0,
            "last_event_ts": None,
            "file_size_bytes": None,
            "error": f"read failed: {e}",
        }

    return {
        "ok": True,
        "today": dict(today_counts),
        "last_7d": dict(last_7d_counts),
        "today_total": sum(today_counts.values()),
        "last_7d_total": sum(last_7d_counts.values()),
        "last_event_ts": last_event_ts,
        "file_size_bytes": size,
    }


def read_log_tail(workspace_root: Path, n_lines: int = LOG_TAIL_LINES) -> dict:
    """Return the last n_lines of bridge.log (capped at LOG_TAIL_MAX_BYTES).

    Returns:
        {"ok": bool, "lines": list[str], "size_bytes": int or None}
    """
    log_path = workspace_root / ".daemon-state" / "bridge.log"
    if not log_path.exists():
        return {"ok": True, "lines": [], "size_bytes": None}

    try:
        size = log_path.stat().st_size
        # Read from end, decode last LOG_TAIL_MAX_BYTES, then take last n_lines.
        with log_path.open("rb") as f:
            f.seek(max(0, size - LOG_TAIL_MAX_BYTES))
            tail_bytes = f.read()
    except OSError as e:
        return {"ok": False, "lines": [], "size_bytes": None, "error": f"read failed: {e}"}

    # Decode bytes; on partial UTF-8 at the start, drop the first incomplete chunk.
    try:
        tail_text = tail_bytes.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return {"ok": False, "lines": [], "size_bytes": size, "error": "decode failed"}

    # Split, drop the leading partial line if we sliced mid-file, take last n_lines.
    lines = tail_text.splitlines()
    if size > LOG_TAIL_MAX_BYTES and lines:
        lines = lines[1:]  # drop possibly-truncated first line
    lines = lines[-n_lines:]

    return {"ok": True, "lines": lines, "size_bytes": size}
