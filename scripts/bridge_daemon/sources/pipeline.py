"""Real-data source for the /pipeline endpoint.

Parses context/pipeline.md's '## Active Deals' table and returns
structured deal records sorted by stage progression (Won first ->
Negotiation -> Proposal -> Demo/POC -> Qualified -> Lead).

The CEO uses this for sales pipeline visibility. Phase 1.28 was read-only;
Phase 1.55 adds per-deal touch tracking so the CEO can suppress
stalled-signal noise without editing pipeline.md by hand.

Tests: tests/bridge/test_a_summary_that_read_the_wrong_end_of_the_file.py
"""
import re
import threading
from datetime import date, datetime, timezone
from scripts.utils.workspace import get_default_tz
from pathlib import Path

from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped

# Stage progression: higher index = closer to closed-won.
# We sort by -stage_rank so Won appears first, Lead last.
STAGE_ORDER = ["Lead", "Qualified", "Demo/POC", "Proposal", "Negotiation", "Won"]
STAGE_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}

PIPELINE_FILE = "context/pipeline.md"
PIPELINE_ROW_CAP = 100  # safety upper bound

# Phase 1.55 touch log.
TOUCH_LOG_FILE = "outputs/operations/pipeline/_touch-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
TOUCH_LOG_MAX_BYTES = 1_000_000   # 1MB safety cap on log size
TOUCH_NOTE_MAX_CHARS = 200
_TOUCH_LOG_LOCK = threading.Lock()

# Match a deal row. Anchored to start of line.
# Columns: | Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |
# We accept whitespace inside cells. Stop at the next pipe per cell.
_ROW_RE = re.compile(
    r"^\|\s*(?P<company>[^|]+?)\s*\|\s*(?P<country>[^|]*?)\s*\|\s*(?P<stage>[^|]*?)\s*\|\s*"
    r"(?P<value>[^|]*?)\s*\|\s*(?P<stage_date>[^|]*?)\s*\|\s*(?P<owner>[^|]*?)\s*\|\s*"
    # `[^|]*?` on next_action. A deal row with a ZERO-WIDTH Next Action cell
    # (`||`) used to fail the whole match and disappear from /pipeline, from
    # the counts and from total_value_usd, silently. No next action is exactly
    # the state a deal is in when the operator most needs to see it.
    #
    # Measured 2026-08-24, narrower than the audit reported: `[^|]+?` needed
    # one character and a SPACE is one, so `|  |` always matched. Only the
    # zero-width cell was lost.
    r"(?P<next_action>[^|]*?)\s*\|\s*(?P<due_date>[^|]*?)\s*\|"
)

_VALUE_USD_RE = re.compile(r"\$([\d,]+)")
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _parse_value(s: str) -> tuple[int | None, str]:
    """Parse value cell. Returns (usd_int_or_None, display_string).

    Examples:
        '$5,500,000' -> (5500000, '$5,500,000')
        'TBD' -> (None, 'TBD')
        'TBD (rev share, 3yr)' -> (None, 'TBD (rev share, 3yr)')
    """
    s = (s or "").strip()
    if not s or s.upper().startswith("TBD"):
        return None, s or "TBD"
    m = _VALUE_USD_RE.search(s)
    if not m:
        return None, s
    try:
        return int(m.group(1).replace(",", "")), s
    except ValueError:
        return None, s


def _parse_due(s: str, today: date | None = None) -> tuple[str | None, int | None, bool]:
    """Parse due-date cell. Returns (iso_date_or_None, days_until_due, is_overdue)."""
    if not s:
        return None, None, False
    m = _ISO_DATE_RE.search(s)
    if not m:
        return None, None, False
    try:
        due = date.fromisoformat(m.group(1))
    except ValueError:
        return None, None, False
    today = today or datetime.now(get_default_tz()).date()
    delta = (due - today).days
    return m.group(1), delta, delta < 0


def _company_key(company: str) -> str:
    """Normalise a company name for touch-log keying.

    Pipeline.md sometimes carries parentheticals ('[Region] (via [Local Entity])').
    The touch key strips them so the CEO can touch by the canonical company
    name and re-marks still find the same row.
    """
    if not company:
        return ""
    base = re.sub(r"\s*\([^)]*\)", "", company).strip()
    return base.lower()


def _text(value) -> str:
    """A log field as a string, "" when the line holds something else.

    `entry.get("date", "")` returns the VALUE whenever the key is present, so a
    hand-edited or restored line carrying `"date": null` came back as None and
    `entry["date"][:10]` in `list_pipeline` raised
    `TypeError: 'NoneType' object is not subscriptable` - which the `except
    ValueError` there does not catch, so one malformed line 500'd the whole
    /pipeline surface. This function is the single producer of that dict, so
    the coercion belongs here rather than as a second guard at the consumer.
    """
    return value if isinstance(value, str) else ""


def read_touch_log(workspace_root: Path) -> dict:
    """Read _touch-log.jsonl. Returns {company_key: {date, ts, note, company}}.

    `company` is the display name; this line listed only three of the four keys
    until 2026-08-24, and the display name is the one a caller reaches for.

    Every value is a string. "Corrupt lines are skipped silently" used to cover
    only JSON-parse corruption; a line that parses but carries the wrong TYPE
    reached the callers untouched. It now covers both: a non-string field
    becomes "", which the callers already handle (`date.fromisoformat("")`
    raises ValueError, which `list_pipeline` catches).

    Last entry per company key wins (so re-marking overwrites the prior ts).
    """
    log_path = workspace_root / TOUCH_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, TOUCH_LOG_MAX_BYTES)
    out: dict[str, dict] = {}
    for entry in entries:
        key = entry.get("company_key")
        if not isinstance(key, str) or not key:
            continue
        out[key] = {
            "date": _text(entry.get("date")),
            "ts": _text(entry.get("ts")),
            "note": _text(entry.get("note")),
            "company": _text(entry.get("company")),
        }
    return out


def mark_touched(workspace_root: Path, company: str, note: str = "") -> dict:
    """Append a touch entry for `company`. Returns {ok, date, ts, company_key}.

    Defensive validation: company is required, note is trimmed to
    TOUCH_NOTE_MAX_CHARS chars and newlines stripped.
    """
    if not isinstance(company, str) or not company.strip():
        return {"ok": False, "error": "company is required"}
    if len(company) > 200:
        return {"ok": False, "error": "company name too long"}
    key = _company_key(company)
    if not key:
        return {"ok": False, "error": "company name empty after normalisation"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:TOUCH_NOTE_MAX_CHARS]
    # Phase 1.80: 'date' is local (CEO calendar day), 'ts' stays UTC.
    now = datetime.now(timezone.utc)
    entry = {
        "company": company.strip(),
        "company_key": key,
        "date": datetime.now(get_default_tz()).date().isoformat(),
        "ts": now.isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / TOUCH_LOG_FILE
    with _TOUCH_LOG_LOCK:
        try:
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "date": entry["date"], "ts": entry["ts"], "company_key": key}


def _empty_pipeline() -> dict:
    """The zero payload, in the SAME SHAPE the parsed one returns.

    Both early exits used to spell the dict out, and both omitted
    ``touched_total`` -- so the one key added after they were written was
    missing exactly when pipeline.md is absent or unreadable. One writer, so
    the next key added cannot go missing from the degraded path only.
    """
    return {
        "deals": [], "counts": {}, "overdue_count": 0,
        "total_value_usd": 0, "tbd_count": 0, "touched_total": 0,
        "total": 0, "truncated": False, "row_cap": PIPELINE_ROW_CAP,
        "data_time": None,
    }


def list_pipeline(workspace_root: Path, today: date | None = None) -> dict:
    """Parse pipeline.md's Active Deals table.

    Returns:
        {
            "deals": [
                {
                    "company": str,
                    "company_key": str (normalised join key; see _company_key),
                    "country": str,
                    "stage": str,
                    "value_usd": int or None,
                    "value_display": str,
                    "stage_date": str (verbatim cell, not parsed),
                    "owner": str,
                    "next_action": str,
                    "due_date": ISO YYYY-MM-DD or None,
                    "days_until_due": int or None,
                    "is_overdue": bool,
                    "touched_date": str or None (from the touch log),
                    "touched_note": str ("" when never touched),
                    "days_since_touched": int or None,
                },
                ...
            ] sorted by (stage_rank DESC, days_until_due ASC None-last, company ASC),
            "counts": {stage: int, ...},
            "overdue_count": int,
            "total_value_usd": int (sum of priced deals),
            "tbd_count": int,
            "touched_total": int (deals with ANY touch entry, at any age),
            "total": int (rows PARSED, which is not len(deals) past the cap),
            "truncated": bool (True when `total` exceeds `row_cap`),
            "row_cap": int (PIPELINE_ROW_CAP, the bound on `deals`),
            "data_time": ISO mtime of pipeline.md or None,
        }

    The five join fields (``company_key``, ``stage_date``, ``touched_date``,
    ``touched_note``, ``days_since_touched``) were returned but undocumented
    until 2026-08-25. ``days_since_touched`` is the field ``pulse.signals()``
    reads for touch suppression, so a consumer written against the old block
    could not see the one key it most needed.

    ``touched_total`` said "deals touched inside the touch-log window", which
    no code implements: there is no recency predicate here, the 1 MB cap in
    ``read_jsonl_capped`` is a size bound and not a time window, and
    ``SIGNALS_TOUCH_SUPPRESS_DAYS`` lives in pulse.py and is not applied here.
    The count is monotonic - a deal touched a year ago still counts. The
    sentence now describes the code. Whether it SHOULD carry a window is an
    open question for the Phase 1.55 owner, not something to change quietly:
    the two readings hand the operator different numbers.
    """
    pipeline_path = workspace_root / PIPELINE_FILE
    if not pipeline_path.exists():
        return _empty_pipeline()
    try:
        text = pipeline_path.read_text(encoding="utf-8")
        mtime = pipeline_path.stat().st_mtime
    except OSError:
        return _empty_pipeline()

    # Find the '## Active Deals' section and walk lines until the next ## heading.
    in_active = False
    deals = []
    for line in text.splitlines():
        stripped = line.strip()
        # An H1 ends the section too. It used to end only at an H2, so a later
        # `# Archive` heading carrying an 8-column table had its rows ingested
        # as live deals.
        if stripped.startswith("# "):
            in_active = False
            continue
        if stripped.startswith("## "):
            in_active = stripped.startswith("## Active Deals")
            continue
        if not in_active:
            continue
        # Skip header + separator lines (start with | but contain --- or are the col-name row).
        if "---" in line:
            continue
        if "Company" in line and "Country" in line and "Stage" in line:
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        # Defensive: skip rows with too many pipes (unescaped pipe in a cell would shift columns).
        if line.count("|") > 9:
            continue
        company = m.group("company").strip()
        if not company or company.lower() == "company":
            continue
        stage = m.group("stage").strip()
        value_usd, value_display = _parse_value(m.group("value"))
        due_iso, days_until, is_overdue = _parse_due(m.group("due_date"), today=today)
        deals.append({
            "company": company,
            "company_key": _company_key(company),
            "country": m.group("country").strip(),
            "stage": stage,
            "value_usd": value_usd,
            "value_display": value_display,
            "stage_date": m.group("stage_date").strip(),
            "owner": m.group("owner").strip(),
            "next_action": m.group("next_action").strip(),
            "due_date": due_iso,
            "days_until_due": days_until,
            "is_overdue": is_overdue,
        })
        # No `break` at PIPELINE_ROW_CAP. The parse used to STOP there, so every
        # aggregate below was over the first N rows of the markdown and not over
        # the pipeline: `total_value_usd`, `counts` and `overdue_count` were
        # published with no sign of it, and `pulse.py` compares that value
        # against the file's own summary line - so past the cap the dashboard
        # raised a `pipeline_summary_drift` warning about a discrepancy it had
        # created itself. Parsing is a walk over a string already in memory; the
        # cap is a bound on ROWS RETURNED, which is a UI concern, and it is
        # applied after the sort below so the page is the top N rather than
        # whichever N happened to be first in the file.

    # Sort: stage_rank DESC (Won first), then due ASC (None last), then company.
    def sort_key(d):
        rank = STAGE_RANK.get(d["stage"], -1)
        due = d["days_until_due"]
        due_key = 999_999 if due is None else due
        return (-rank, due_key, d["company"].lower())
    deals.sort(key=sort_key)

    # Phase 1.55: join touch log so the UI + signal analyzer can see
    # the CEO's last touch on each deal.
    today_resolved = today or datetime.now(get_default_tz()).date()
    touch_log = read_touch_log(workspace_root)
    counts: dict = {}
    overdue_count = 0
    total_value_usd = 0
    tbd_count = 0
    touched_total = 0
    for d in deals:
        counts[d["stage"]] = counts.get(d["stage"], 0) + 1
        if d["is_overdue"]:
            overdue_count += 1
        if d["value_usd"] is not None:
            total_value_usd += d["value_usd"]
        else:
            tbd_count += 1
        entry = touch_log.get(d["company_key"])
        if entry:
            d["touched_date"] = entry["date"]
            d["touched_note"] = entry["note"]
            try:
                td = date.fromisoformat(entry["date"][:10])
                d["days_since_touched"] = (today_resolved - td).days
            except ValueError:
                d["days_since_touched"] = None
            touched_total += 1
        else:
            d["touched_date"] = None
            d["touched_note"] = ""
            d["days_since_touched"] = None

    data_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    # The cap lands here, after the sort and after every aggregate is measured.
    total_rows = len(deals)
    deals = deals[:PIPELINE_ROW_CAP]
    return {
        "deals": deals,
        "counts": counts,
        "overdue_count": overdue_count,
        "total_value_usd": total_value_usd,
        "tbd_count": tbd_count,
        "touched_total": touched_total,
        "total": total_rows,
        "truncated": total_rows > len(deals),
        "row_cap": PIPELINE_ROW_CAP,
        "data_time": data_time,
    }
