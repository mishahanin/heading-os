"""Real-data source for the /investors endpoint.

Parses outputs/operations/fundraising/2026-05-17_investor-outreach-program/
00-master-shortlist-v1.md and joins it against the dossiers/ subdir to
produce a per-firm status view for the active Series B raise.

Phase 1.31 is read-only. Drill-down via /investors/dossier?slug=...
Phase 1.36 adds per-firm send tracking: _send-log.jsonl records when
each first-touch went out so /investors and Pulse can show progress.
"""
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bridge_daemon._atomic import atomic_write_text

PROGRAM_DIR = "outputs/operations/fundraising/2026-05-17_investor-outreach-program"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
SHORTLIST_FILE = "00-master-shortlist-v1.md"
DOSSIERS_DIR = "dossiers"
MESSAGES_DIR = "messages"
SEND_LOG_FILE = "_send-log.jsonl"
SEND_LOG_MAX_BYTES = 1_000_000  # 1MB safety cap on log size

# Single-process lock around send-log appends. Bridge daemon is single-process
# so this is sufficient; cross-process safety would need fcntl/msvcrt locking
# (deferred — every other bridge endpoint already shares this assumption).
_SEND_LOG_LOCK = threading.Lock()

# Region order matches the markdown's heading order.
REGION_ORDER = ["GCC/MENA", "Europe", "US", "UK/Israel", "APAC"]
REGION_HEADINGS = {
    "## GCC / MENA": "GCC/MENA",
    "## Europe": "Europe",
    "## US": "US",
    "## UK / Israel": "UK/Israel",
    "## APAC": "APAC",
}

# Wave / status enrichment from the "Decisions locked" section.
# Matched against firm names (case-insensitive substring).
DEFAULT_WAVE = "TBD"

# Status display ordering: first-5 leads, then parallel-track, then wave-2,
# wave-3, out-of-scope, then anything else.
STATUS_RANK = {
    "first-5": 0,
    "parallel-week-1-2": 1,
    "wave-2": 2,
    "wave-3": 3,
    "out-of-scope": 4,
    DEFAULT_WAVE: 9,
}

# Status label -> short display token.
STATUS_LABEL = {
    "first-5": "First 5",
    "parallel-week-1-2": "Parallel",
    "wave-2": "Wave 2",
    "wave-3": "Wave 3",
    "out-of-scope": "Out of scope",
}

# Regional table row.
# | # | Firm | Type | HQ | Cheque | Fit | Notes |
_REGION_ROW_RE = re.compile(
    r"^\|\s*(?P<num>\d+)\s*\|\s*(?P<firm>[^|]+?)\s*\|\s*(?P<type>[^|]+?)\s*\|\s*"
    r"(?P<hq>[^|]+?)\s*\|\s*(?P<cheque>[^|]+?)\s*\|\s*(?P<fit>[^|]+?)\s*\|\s*"
    r"(?P<notes>[^|]+?)\s*\|"
)

# Decisions-locked row pattern.
# | Slot | Firm | Wave | Notes |
_DECISION_ROW_RE = re.compile(
    r"^\|\s*(?P<slot>[^|]+?)\s*\|\s*(?P<firms>[^|]+?)\s*\|\s*(?P<wave>[^|]+?)\s*\|\s*(?P<notes>[^|]+?)\s*\|"
)


def _slugify_firm(firm: str) -> str:
    """Convert firm name -> slug fragment used to match dossier filenames.

    The dossier filenames use the form `NN-slug.md` where slug is roughly
    a hyphen-separated lowercase version of the firm name with parentheses
    and common suffixes stripped.
    """
    s = firm.lower()
    # Strip parenthetical content.
    s = re.sub(r"\s*\([^)]*\)", "", s)
    # Drop bold markers if any leaked through.
    s = s.replace("**", "")
    # Replace non-alphanumerics with hyphens.
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def _firm_canonical(firm: str) -> str:
    """Strip markdown bold + parenthetical for display + matching."""
    s = firm.strip()
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\s*\([^)]*\)", "", s).strip()
    return s


def _parse_status_from_decisions(text: str) -> dict[str, str]:
    """Walk the 'Decisions locked' section's table to extract firm -> wave.

    Returns dict mapping lowercased canonical firm-name substring -> status
    token from STATUS_LABEL.
    """
    statuses: dict[str, str] = {}
    in_section = False
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Decisions locked") or stripped.startswith("## In-scope firms"):
            in_section = True
            continue
        if in_section and stripped.startswith("#"):
            # Next major heading - leave section unless it's just a sub-header.
            if stripped.startswith("# ") or stripped.startswith("## Out-of-scope"):
                if stripped.startswith("## Out-of-scope"):
                    # Special-case: capture out-of-scope list separately below.
                    pass
                else:
                    # Walked off the section.
                    in_table = False
                    if stripped.startswith("# "):
                        in_section = False
                    continue
        if not in_section:
            continue
        if "Slot" in line and "Firm" in line and "Wave" in line:
            in_table = True
            continue
        if in_table and "---" in line:
            continue
        if in_table and stripped.startswith("|"):
            m = _DECISION_ROW_RE.match(line)
            if not m:
                continue
            slot = m.group("slot").strip().lower()
            firms_cell = m.group("firms").strip()
            status_token: str
            if "first 5" in slot:
                status_token = "first-5"
            elif "parallel" in slot and "wave 1" in slot.replace("week ", "week"):
                status_token = "parallel-week-1-2"
            elif "parallel" in slot:
                status_token = "parallel-week-1-2"
            elif "wave 2" in slot:
                status_token = "wave-2"
            elif "wave 3" in slot:
                status_token = "wave-3"
            else:
                status_token = DEFAULT_WAVE
            # Firm cell may list multiple firms separated by commas; capture
            # the canonical name before the parenthetical for each.
            for chunk in firms_cell.split(","):
                clean = _firm_canonical(chunk)
                if not clean:
                    continue
                statuses[clean.lower()] = status_token
        # Out-of-scope list (Glilot+).
        if "Glilot+" in stripped and ("out" in stripped.lower() or "dropped" in stripped.lower()):
            statuses["glilot+"] = "out-of-scope"
            statuses["glilot"] = "out-of-scope"
    return statuses


_STOPWORDS = {"the", "a", "an", "and", "of", "for", "&"}


def _first_token(name: str) -> str:
    """Return the first significant lowercased word in `name`, skipping
    articles/stopwords. Used as a coarse identity key for fuzzy matching."""
    for w in re.findall(r"[A-Za-z0-9]+", name.lower()):
        if w in _STOPWORDS:
            continue
        return w
    return ""


def _acronym(name: str) -> str:
    """Build initialism from capitalized words. 'NATO Innovation Fund' -> 'NIF'."""
    words = re.findall(r"[A-Z][a-zA-Z]*", name)
    return "".join(w[0] for w in words).upper()


def _match_status(firm_canonical: str, statuses: dict[str, str]) -> str:
    """Best-effort match firm -> status. Tries exact, substring, first-token,
    and acronym matching in that order."""
    name_lower = firm_canonical.lower()
    if name_lower in statuses:
        return statuses[name_lower]
    for key, status in statuses.items():
        if not key:
            continue
        if key in name_lower or name_lower in key:
            return status
    # First-token fallback. "Northgate NGCI" vs "Northgate Capital" both
    # share first significant token "Northgate".
    name_first = _first_token(firm_canonical)
    if name_first:
        for key, status in statuses.items():
            if _first_token(key) == name_first:
                return status
    # Acronym fallback. "NIF" -> "NATO Innovation Fund".
    name_acronym = firm_canonical.upper() if firm_canonical.isupper() else _acronym(firm_canonical)
    if name_acronym and len(name_acronym) >= 2:
        for key, status in statuses.items():
            if key.upper() == name_acronym or _acronym(key) == name_acronym:
                return status
    return DEFAULT_WAVE


def _slug_overlap(slug_a: str, slug_b: str, num: int) -> bool:
    """True if firm slug and dossier-rest slug share enough structure to be
    considered a match. Strategy:
    - exact equality
    - one is a prefix of the other (e.g. eurazeo vs eurazeo-growth-iv)
    - both share the first significant token (e.g. northgate-capital vs
      northgate-capital-international)
    - the e& Capital -> eand-capital convention
    """
    if not slug_a or not slug_b:
        return False
    if slug_a == slug_b:
        return True
    if slug_b.startswith(slug_a + "-") or slug_a.startswith(slug_b + "-"):
        return True
    a_tokens = slug_a.split("-")
    b_tokens = slug_b.split("-")
    # First-token agreement is enough when the file is the only one with
    # that first token (caller scopes by NN already).
    if a_tokens and b_tokens and a_tokens[0] == b_tokens[0]:
        return True
    # e& -> eand convention.
    if slug_a.replace("e-capital", "eand-capital") == slug_b:
        return True
    return False


def _find_program_file(program_path: Path, subdir: str, firm_num: int,
                       firm_canonical: str, suffix: str = "") -> str | None:
    """Find a file under program_path/subdir/ that matches the firm.

    Files are named `NN-slug{suffix}.md` where NN matches firm_num.
    Returns workspace-relative POSIX path or None.
    """
    target_dir = program_path / subdir
    if not target_dir.is_dir():
        return None
    firm_slug = _slugify_firm(firm_canonical)
    pattern = f"{firm_num:02d}-*{suffix}.md" if suffix else f"{firm_num:02d}-*.md"
    candidates = list(target_dir.glob(pattern))
    # If exactly one file matches the numeric prefix, accept it (the markdown
    # shortlist uses these numeric IDs canonically).
    if len(candidates) == 1:
        p = candidates[0]
        return str(p.relative_to(program_path.parent.parent.parent.parent)).replace("\\", "/")
    # Otherwise require slug overlap.
    for p in candidates:
        stem = p.stem
        if suffix:
            stem = stem.replace(suffix, "")
        if "-" not in stem:
            continue
        rest = stem.split("-", 1)[1]
        if _slug_overlap(firm_slug, rest, firm_num):
            return str(p.relative_to(program_path.parent.parent.parent.parent)).replace("\\", "/")
    return None


def _find_dossier(program_path: Path, firm_num: int, firm_canonical: str) -> str | None:
    return _find_program_file(program_path, DOSSIERS_DIR, firm_num, firm_canonical)


def _find_message(program_path: Path, firm_num: int, firm_canonical: str) -> str | None:
    return _find_program_file(program_path, MESSAGES_DIR, firm_num, firm_canonical, suffix="-first-touch")


def _read_send_log(workspace_root: Path) -> dict:
    """Read _send-log.jsonl. Returns {firm_num: {date, ts, note}} keyed by int.

    Silent degradation: corrupt lines are skipped; missing file returns {}.
    Last entry per firm wins, so re-marking a firm overwrites the earlier ts
    and a tombstone entry ('undo': True) cancels the mark. A subsequent
    real mark restores it again.
    """
    log_path = workspace_root / PROGRAM_DIR / SEND_LOG_FILE
    if not log_path.exists():
        return {}
    try:
        size = log_path.stat().st_size
        if size > SEND_LOG_MAX_BYTES:
            # Safety cap: file unexpectedly large; refuse to parse.
            return {}
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[int, dict] = {}
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
        firm_num = entry.get("firm_num")
        if not isinstance(firm_num, int):
            continue
        # Tombstone: cancel any prior mark for this firm.
        if entry.get("undo") is True:
            out.pop(firm_num, None)
            continue
        out[firm_num] = {
            "date": entry.get("date", ""),
            "ts": entry.get("ts", ""),
            "note": entry.get("note", ""),
        }
    return out


def mark_sent(workspace_root: Path, firm_num: int, note: str = "") -> dict:
    """Append a send-log entry for `firm_num`. Returns {ok, date, ts}.

    Validates firm_num is in [1, 100] (defensive — the shortlist tops out
    at 22). Atomically rewrites the log on each append to avoid partial
    lines on crash. Note is trimmed to 200 chars + sanitized of newlines.
    """
    if not isinstance(firm_num, int):
        return {"ok": False, "error": "firm_num must be an integer"}
    if not (1 <= firm_num <= 100):
        return {"ok": False, "error": "firm_num out of range"}
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:200]
    # Phase 1.80: 'date' tracks the CEO's local calendar day for "today"
    # queries (today_activity); 'ts' stays UTC for ordering. The two can
    # disagree near UTC midnight - the local date is what answers "what
    # did I do today?" from the user's perspective.
    now = datetime.now(timezone.utc)
    entry = {
        "firm_num": firm_num,
        "date": date.today().isoformat(),
        "ts": now.isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / PROGRAM_DIR / SEND_LOG_FILE
    with _SEND_LOG_LOCK:
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
    return {"ok": True, "date": entry["date"], "ts": entry["ts"], "firm_num": firm_num}


def undo_sent(workspace_root: Path, firm_num: int) -> dict:
    """Append a tombstone entry that cancels the prior mark-sent for `firm_num`.

    Idempotent: a tombstone on a firm that was never marked is harmless;
    subsequent reads simply return no sent state.
    """
    if not isinstance(firm_num, int):
        return {"ok": False, "error": "firm_num must be an integer"}
    if not (1 <= firm_num <= 100):
        return {"ok": False, "error": "firm_num out of range"}
    now = datetime.now(timezone.utc)
    entry = {
        "firm_num": firm_num,
        "undo": True,
        "ts": now.isoformat(),
    }
    log_path = workspace_root / PROGRAM_DIR / SEND_LOG_FILE
    with _SEND_LOG_LOCK:
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
    return {"ok": True, "firm_num": firm_num, "ts": entry["ts"]}


def list_investors(workspace_root: Path) -> dict:
    """Parse the master shortlist + dossier directory.

    Returns:
        {
            "firms": [
                {
                    "num": int,
                    "firm": str,            # canonical, no bold/parenthetical
                    "firm_raw": str,        # original cell content
                    "region": str,
                    "type": str,
                    "hq": str,
                    "cheque": str,
                    "fit": str,
                    "notes": str,
                    "status": str,          # token from STATUS_LABEL
                    "status_label": str,    # display label
                    "dossier_path": str or None,
                    "message_path": str or None,
                },
                ...
            ] sorted by (status_rank ASC, region ASC, num ASC),
            "counts": {"first-5": N, "parallel-week-1-2": N, ...},
            "total": int,
            "raise_target": str | None,     # "$25-40M" parsed from header
            "data_time": ISO mtime of shortlist file or None,
        }
    """
    program_path = workspace_root / PROGRAM_DIR
    shortlist_path = program_path / SHORTLIST_FILE
    if not shortlist_path.exists():
        return {
            "firms": [], "counts": {}, "total": 0,
            "raise_target": None, "data_time": None,
        }
    try:
        text = shortlist_path.read_text(encoding="utf-8")
        mtime = shortlist_path.stat().st_mtime
    except OSError:
        return {
            "firms": [], "counts": {}, "total": 0,
            "raise_target": None, "data_time": None,
        }

    # Parse raise posture from the header paragraph.
    raise_target = None
    m = re.search(r"\$(\d+-\d+M)\s+anchor", text)
    if m:
        raise_target = f"${m.group(1)}"

    # Phase 1: parse regional tables.
    current_region: str | None = None
    firms: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        # Stop scanning regional tables once we hit cross-cutting / decisions
        # sections so we don't pick up the decisions-table rows as firms.
        if stripped.startswith("## Cross-cutting") or stripped.startswith("# Wave 2 Updates"):
            current_region = None
            continue
        if stripped.startswith("## "):
            # Strip trailing parenthetical row count like "(5)".
            heading_clean = re.sub(r"\s*\(\d+\)\s*$", "", stripped)
            current_region = REGION_HEADINGS.get(heading_clean)
            continue
        if not current_region:
            continue
        if "---" in line:
            continue
        if "Firm" in line and "Type" in line and "HQ" in line:
            continue
        rm = _REGION_ROW_RE.match(line)
        if not rm:
            continue
        firm_raw = rm.group("firm").strip()
        firm = _firm_canonical(firm_raw)
        if not firm or firm.lower() == "firm":
            continue
        try:
            num = int(rm.group("num"))
        except ValueError:
            continue
        firms.append({
            "num": num,
            "firm": firm,
            "firm_raw": firm_raw,
            "region": current_region,
            "type": rm.group("type").strip(),
            "hq": rm.group("hq").strip(),
            "cheque": rm.group("cheque").strip(),
            "fit": rm.group("fit").strip(),
            "notes": rm.group("notes").strip(),
        })

    # Phase 2: parse status enrichment.
    statuses = _parse_status_from_decisions(text)

    # Phase 3: enrich each firm + locate dossier/message files.
    # Phase 1.36: also join the send-log so each firm carries sent_date if known.
    send_log = _read_send_log(workspace_root)
    counts: dict[str, int] = {}
    sent_total = 0
    for f in firms:
        status_token = _match_status(f["firm"], statuses)
        f["status"] = status_token
        f["status_label"] = STATUS_LABEL.get(status_token, status_token)
        f["dossier_path"] = _find_dossier(program_path, f["num"], f["firm"])
        f["message_path"] = _find_message(program_path, f["num"], f["firm"])
        send_entry = send_log.get(f["num"])
        if send_entry:
            f["sent_date"] = send_entry["date"]
            f["sent_note"] = send_entry["note"]
            sent_total += 1
        else:
            f["sent_date"] = None
            f["sent_note"] = ""
        counts[status_token] = counts.get(status_token, 0) + 1

    # Sort by status rank, then region (by REGION_ORDER), then num.
    region_rank = {r: i for i, r in enumerate(REGION_ORDER)}

    def sort_key(d):
        return (
            STATUS_RANK.get(d["status"], 9),
            region_rank.get(d["region"], 99),
            d["num"],
        )
    firms.sort(key=sort_key)

    data_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "firms": firms,
        "counts": counts,
        "total": len(firms),
        "sent_total": sent_total,
        "raise_target": raise_target,
        "data_time": data_time,
    }


# ============================================================
# Drill-down: dossier reader
# ============================================================
DOSSIER_MAX_BYTES = 200_000  # cap any single dossier read


def read_dossier(workspace_root: Path, rel_path: str) -> dict:
    """Read a single dossier or first-touch message safely.

    Path validation:
    - Must start with the program directory prefix
    - Must resolve to a file inside the program directory
    - Must be a .md file
    - Must not be a symlink
    - Must be under DOSSIER_MAX_BYTES

    Returns:
        {"ok": True, "path": rel_path, "content": str, "size": int}
        OR
        {"ok": False, "error": str}
    """
    if not rel_path or not isinstance(rel_path, str):
        return {"ok": False, "error": "missing path"}
    rel_path = rel_path.replace("\\", "/").lstrip("./")
    if not rel_path.startswith(PROGRAM_DIR + "/"):
        return {"ok": False, "error": "path must be under fundraising program"}
    parts = [p for p in rel_path.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return {"ok": False, "error": "invalid path segment"}
    target = (workspace_root / rel_path).resolve()
    program_root = (workspace_root / PROGRAM_DIR).resolve()
    try:
        target.relative_to(program_root)
    except ValueError:
        return {"ok": False, "error": "path escapes program dir"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if target.is_symlink():
            return {"ok": False, "error": "symlinks not allowed"}
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    if target.suffix.lower() != ".md":
        return {"ok": False, "error": "only .md files allowed"}
    try:
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > DOSSIER_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {DOSSIER_MAX_BYTES})"}
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "path": rel_path, "content": content, "size": size}
