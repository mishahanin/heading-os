"""Real-data source for the /pipeline endpoint.

Parses context/pipeline.md's '## Active Deals' table and returns
structured deal records sorted by stage progression (Won first ->
Negotiation -> Proposal -> Demo/POC -> Qualified -> Lead).

The CEO uses this for sales pipeline visibility. Phase 1.28 was read-only;
Phase 1.55 adds per-deal touch tracking so the CEO can suppress
stalled-signal noise without editing pipeline.md by hand.
"""
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text

# Stage progression: higher index = closer to closed-won.
# We sort by -stage_rank so Won appears first, Lead last.
STAGE_ORDER = ["Lead", "Qualified", "Demo/POC", "Proposal", "Negotiation", "Won"]
STAGE_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}

PIPELINE_FILE = "context/pipeline.md"
PIPELINE_ROW_CAP = 100  # safety upper bound

# Phase 1.55 touch log.
TOUCH_LOG_FILE = "outputs/operations/pipeline/_touch-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
TOUCH_LOG_MAX_BYTES = 1_000_000   # 1MB safety cap on log size
TOUCH_NOTE_MAX_CHARS = 200
_TOUCH_LOG_LOCK = threading.Lock()

# Match a deal row. Anchored to start of line.
# Columns: | Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |
# We accept whitespace inside cells. Stop at the next pipe per cell.
_ROW_RE = re.compile(
    r"^\|\s*(?P<company>[^|]+?)\s*\|\s*(?P<country>[^|]*?)\s*\|\s*(?P<stage>[^|]*?)\s*\|\s*"
    r"(?P<value>[^|]*?)\s*\|\s*(?P<stage_date>[^|]*?)\s*\|\s*(?P<owner>[^|]*?)\s*\|\s*"
    r"(?P<next_action>[^|]+?)\s*\|\s*(?P<due_date>[^|]*?)\s*\|"
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
    today = today or date.today()
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


def read_touch_log(workspace_root: Path) -> dict:
    """Read _touch-log.jsonl. Returns {company_key: {date, ts, note}}.

    Last entry per company key wins (so re-marking overwrites the prior ts).
    Corrupt lines are skipped silently.
    """
    log_path = workspace_root / TOUCH_LOG_FILE
    if not log_path.exists():
        return {}
    try:
        if log_path.stat().st_size > TOUCH_LOG_MAX_BYTES:
            return {}
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        key = entry.get("company_key")
        if not isinstance(key, str) or not key:
            continue
        out[key] = {
            "date": entry.get("date", ""),
            "ts": entry.get("ts", ""),
            "note": entry.get("note", ""),
            "company": entry.get("company", ""),
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
        "date": date.today().isoformat(),
        "ts": now.isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / TOUCH_LOG_FILE
    with _TOUCH_LOG_LOCK:
        existing = ""
        if log_path.exists():
            try:
                existing = log_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        new_content = existing
        if existing and not existing.endswith("\n"):
            new_content += "\n"
        new_content += json.dumps(entry) + "\n"
        try:
            atomic_write_text(log_path, new_content, mode=0o644)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "date": entry["date"], "ts": entry["ts"], "company_key": key}


def list_pipeline(workspace_root: Path, today: date | None = None) -> dict:
    """Parse pipeline.md's Active Deals table.

    Returns:
        {
            "deals": [
                {
                    "company": str,
                    "country": str,
                    "stage": str,
                    "value_usd": int or None,
                    "value_display": str,
                    "owner": str,
                    "next_action": str,
                    "due_date": ISO YYYY-MM-DD or None,
                    "days_until_due": int or None,
                    "is_overdue": bool,
                },
                ...
            ] sorted by (stage_rank DESC, days_until_due ASC None-last, company ASC),
            "counts": {stage: int, ...},
            "overdue_count": int,
            "total_value_usd": int (sum of priced deals),
            "tbd_count": int,
            "data_time": ISO mtime of pipeline.md or None,
        }
    """
    pipeline_path = workspace_root / PIPELINE_FILE
    if not pipeline_path.exists():
        return {
            "deals": [], "counts": {}, "overdue_count": 0,
            "total_value_usd": 0, "tbd_count": 0, "data_time": None,
        }
    try:
        text = pipeline_path.read_text(encoding="utf-8")
        mtime = pipeline_path.stat().st_mtime
    except OSError:
        return {
            "deals": [], "counts": {}, "overdue_count": 0,
            "total_value_usd": 0, "tbd_count": 0, "data_time": None,
        }

    # Find the '## Active Deals' section and walk lines until the next ## heading.
    in_active = False
    deals = []
    for line in text.splitlines():
        stripped = line.strip()
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
        if len(deals) >= PIPELINE_ROW_CAP:
            break

    # Sort: stage_rank DESC (Won first), then due ASC (None last), then company.
    def sort_key(d):
        rank = STAGE_RANK.get(d["stage"], -1)
        due = d["days_until_due"]
        due_key = 999_999 if due is None else due
        return (-rank, due_key, d["company"].lower())
    deals.sort(key=sort_key)

    # Phase 1.55: join touch log so the UI + signal analyzer can see
    # the CEO's last touch on each deal.
    today_resolved = today or date.today()
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
    return {
        "deals": deals,
        "counts": counts,
        "overdue_count": overdue_count,
        "total_value_usd": total_value_usd,
        "tbd_count": tbd_count,
        "touched_total": touched_total,
        "data_time": data_time,
    }
