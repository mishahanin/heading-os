"""Operational visibility sources for the Settings page.

- read_telemetry_summary: counts EVERY event type present in the tail of
  .daemon-state/usage.jsonl it reads, scoped to today + last 7 days. The read
  is capped by bytes and then by lines, and it says so: `truncated` is True
  whenever a cap kept it from the whole file.
  TELEMETRY_EVENT_TYPES names the four the daemon writes today; it is
  documentation of the expected vocabulary, not a filter. Counting only those
  four would hide a writer-side change instead of showing it, which is why the
  totals are deliberately open-ended.
- read_log_tail: returns the last N lines from .daemon-state/bridge.log,
  capped by line count + total bytes.

Both read from .daemon-state/ which is per-workspace, per-user, and
contains no credentials. Safe to surface to the authenticated browser.

Tests: tests/bridge/test_a_summary_that_read_the_wrong_end_of_the_file.py
"""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.utils.timeparse import parse_iso
from scripts.utils.workspace import get_default_tz

LOG_TAIL_LINES = 50
LOG_TAIL_MAX_BYTES = 200_000  # cap total bytes returned even if 50 lines is huge
# Both caps are applied to the TAIL of usage.jsonl, never the head. The file is
# append-only and nothing in this repository rotates it, so a head-first cap
# discards exactly the events the summary is about: past 20,000 lines `today`
# and `last_7d` froze at zero and `last_event_ts` reported line 20,000 instead
# of the last event, with no flag and no error saying so.
USAGE_MAX_LINES = 20_000
USAGE_MAX_BYTES = 4_000_000  # read at most this much from the end of the file

# The event types the daemon writes today. Read the module docstring before
# turning this into a filter: it is expected vocabulary, not an allowlist.
TELEMETRY_EVENT_TYPES = ("page_view", "launch", "return_to_browser", "finalize")


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
            "truncated": bool (True when the caps stopped this from reading
                        the whole file, so events older than the scanned
                        window are not counted),
        }

    The scan reads the END of usage.jsonl, bounded by USAGE_MAX_BYTES and then
    USAGE_MAX_LINES, so what the caps drop is the OLDEST events. It used to
    read head-first and stop at line 20,000, which dropped the newest ones
    instead; see the comment on the constants.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # The operator's calendar day, matching inbox.py, investors.py and
    # pipeline.py, which all define "today" via get_default_tz(). It used to be
    # a UTC date compared as a STRING PREFIX of the raw `ts` field, which is two
    # defects in one line: UTC is not the day the operator is having, and a
    # prefix test silently misses any stamp whose date is not at position 0.
    # Comparing parsed, zone-converted dates fixes both.
    local_tz = get_default_tz()
    today_local = now.astimezone(local_tz).date()
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
            "truncated": False,
        }

    today_counts: Counter = Counter()
    last_7d_counts: Counter = Counter()
    last_event_ts: str | None = None

    try:
        size = usage_path.stat().st_size
        # Byte-tail first, mirroring read_log_tail below: seek to the last
        # USAGE_MAX_BYTES and drop a line the slice landed inside, so a
        # half-record at the boundary is never parsed as a whole one.
        offset = max(0, size - USAGE_MAX_BYTES)
        with usage_path.open("rb") as f:
            starts_clean = True
            if offset:
                f.seek(offset - 1)
                starts_clean = f.read(1) == b"\n"
            else:
                f.seek(0)
            tail_bytes = f.read()
    except OSError as e:
        return {
            "ok": False,
            "today": {},
            "last_7d": {},
            "today_total": 0,
            "last_7d_total": 0,
            "last_event_ts": None,
            "file_size_bytes": None,
            "truncated": False,
            "error": f"read failed: {e}",
        }

    lines = tail_bytes.decode("utf-8", errors="replace").splitlines()
    if not starts_clean and lines:
        lines = lines[1:]
    # Then the line cap, also from the end.
    over_line_cap = len(lines) > USAGE_MAX_LINES
    if over_line_cap:
        lines = lines[-USAGE_MAX_LINES:]
    truncated = bool(offset) or over_line_cap

    for raw in lines:
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
        ts = parse_iso(ts_str)
        if ts is None:
            continue
        if ts >= cutoff_7d:
            last_7d_counts[evt] += 1
        if ts.astimezone(local_tz).date() == today_local:
            today_counts[evt] += 1
        last_event_ts = ts_str  # JSONL is append-only, last line wins

    return {
        "ok": True,
        "today": dict(today_counts),
        "last_7d": dict(last_7d_counts),
        "today_total": sum(today_counts.values()),
        "last_7d_total": sum(last_7d_counts.values()),
        "last_event_ts": last_event_ts,
        "file_size_bytes": size,
        "truncated": truncated,
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
        offset = max(0, size - LOG_TAIL_MAX_BYTES)
        with log_path.open("rb") as f:
            # Is the byte before the slice a newline? If so the first line in
            # `tail_bytes` is COMPLETE and dropping it loses a real line. The
            # old code dropped it whenever the file was over the cap, without
            # looking.
            starts_clean = True
            if offset:
                f.seek(offset - 1)
                starts_clean = f.read(1) == b"\n"
            else:
                f.seek(0)
            tail_bytes = f.read()
    except OSError as e:
        return {"ok": False, "lines": [], "size_bytes": None, "error": f"read failed: {e}"}

    # `errors="replace"` cannot raise, so the try/except that used to wrap this
    # was unreachable and its "decode failed" result was fiction. A partial
    # UTF-8 sequence at the slice boundary becomes U+FFFD, and the partial-line
    # drop below removes the line carrying it.
    tail_text = tail_bytes.decode("utf-8", errors="replace")

    lines = tail_text.splitlines()
    if not starts_clean and lines:
        lines = lines[1:]  # the slice landed mid-line
    # `lines[-0:]` is `lines[0:]`, i.e. EVERYTHING -- the exact inverse of what
    # a caller asking for zero lines wants, and up to 200 KB of it.
    lines = lines[-n_lines:] if n_lines > 0 else []

    return {"ok": True, "lines": lines, "size_bytes": size}
