"""Real-data source for the /investors endpoint.

Parses outputs/operations/fundraising/2026-05-17_investor-outreach-program/
00-master-shortlist-v1.md and joins it against the dossiers/ subdir to
produce a per-firm status view for the active Series B raise.

Phase 1.31 is read-only. Drill-down via /investors/dossier?slug=...
Phase 1.36 adds per-firm send tracking: _send-log.jsonl records when
each first-touch went out so /investors and Pulse can show progress.
"""
import re
import threading
from datetime import date, datetime, timezone
from scripts.utils.workspace import get_default_tz
from pathlib import Path

from scripts.bridge_daemon._shapes import is_undo

from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped
from scripts.bridge_daemon._safepath import contains_symlink

PROGRAM_DIR = "outputs/operations/fundraising/2026-05-17_investor-outreach-program"  # leak-guard: ok (relative suffix rooted by caller)
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
    # `[^|]*?`, not `[^|]+?`: a firm row whose Notes cell is blank used to fail
    # the whole match and vanish from `firms`, the counts and the total, with no
    # error. An empty note is not a reason to hide a firm.
    r"(?P<notes>[^|]*?)\s*\|"
)

# Decisions-locked row pattern.
# | Slot | Firm | Wave | Notes |
# `notes` is `[^|]*?`, not `[^|]+?`. With the plus, a row whose Notes cell is
# truly empty (`| Wave 1 ||`, no spacing) failed to match at all, so its firms
# never entered `statuses` and fell back to "TBD" on the dashboard. The region
# table in this same file was fixed for exactly this, with a comment reading
# "an empty note is not a reason to hide a firm"; the decisions row was not.
_DECISION_ROW_RE = re.compile(
    r"^\|\s*(?P<slot>[^|]+?)\s*\|\s*(?P<firms>[^|]+?)\s*\|\s*(?P<wave>[^|]+?)\s*\|\s*(?P<notes>[^|]*?)\s*\|"
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
    in_out_of_scope = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Decisions locked") or stripped.startswith("## In-scope firms"):
            in_section = True
            in_out_of_scope = False
            continue
        if in_section and stripped.startswith("#"):
            # A heading always ends a table, whatever else it does. Only H1 and
            # `## Out-of-scope` used to reset `in_table`, so an ordinary
            # `## Rationale` inside the decisions area left the flag SET and
            # every markdown row under it was matched as a decision row.
            # `_match_status` matches on substrings, so one bogus firm key was
            # enough to re-label a real firm's wave on the raise dashboard.
            in_table = False
            # Any heading ENDS the out-of-scope list. It used to end only at an
            # H1, so an ordinary `## Notes` or `## Rationale` after the
            # out-of-scope block left the flag set, and every `- **Name**`
            # bullet down to the next H1 was filed as out-of-scope. `_match_status`
            # matches on substrings, so one stray key was enough to push a live
            # firm to the bottom of the raise dashboard as "Out of scope".
            in_out_of_scope = stripped.startswith("## Out-of-scope")
            if stripped.startswith("# ") or stripped.startswith("## Out-of-scope"):
                if not in_out_of_scope:
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
            # An explicitly numbered wave is the more specific signal, so it is
            # tested BEFORE the "parallel" catch-all. It used to come after, and
            # a slot reading "Parallel to Wave 3" was filed as week 1-2 and
            # sorted above the firms actually being contacted first.
            #
            # The catch-all is now one term. It was
            # `"parallel" in slot and "wave 1" in slot.replace(...) or "parallel" in slot`,
            # which `and` binding tighter than `or` collapses to the second term
            # alone: the wave-1 conjunct and the `week ` normalisation could
            # never affect the result. Measured against the live shortlist, they
            # never did anyway -- its parallel slot reads "parallel-track week
            # 1-2" and contains no "wave 1" at all, so the dead conjunct was
            # matching nothing and the catch-all was doing all the work.
            if "first 5" in slot:
                status_token = "first-5"
            elif "wave 2" in slot:
                status_token = "wave-2"
            elif "wave 3" in slot:
                status_token = "wave-3"
            elif "parallel" in slot:
                status_token = "parallel-week-1-2"
            else:
                status_token = DEFAULT_WAVE
            # Firm cell may list multiple firms separated by commas; capture
            # the canonical name before the parenthetical for each.
            for chunk in firms_cell.split(","):
                clean = _firm_canonical(chunk)
                if not clean:
                    continue
                statuses[clean.lower()] = status_token
        # Out-of-scope bullets name the firm in bold: "- **Firm+** -- dropped ...".
        # The name is READ OFF the line rather than hard-coded, so a firm nobody
        # curated is still captured and no real firm name lives in this file. Both
        # the written form and its plus-stripped base are registered, because the
        # regional table usually carries the base name alone.
        if in_out_of_scope and stripped.startswith("-"):
            for token in re.findall(r"\*\*(.+?)\*\*", stripped):
                name = token.strip().lower()
                base = name.rstrip("+").strip()
                if name:
                    statuses[name] = "out-of-scope"
                if base:
                    statuses[base] = "out-of-scope"
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
    """Build an initialism. 'Northwind Innovation Fund' -> 'NIF'.

    Case-INSENSITIVE, and that is the fix. It used to match ``[A-Z][a-zA-Z]*``,
    so it needed a capital letter to see a word at all -- while every key it is
    called against is lowercased when the decisions table is parsed. It
    therefore returned "" for each of them and the acronym fallback below could
    only ever fire on a key that literally WAS the acronym. The documented
    resolution, a regional table naming a fund by its initials against a
    decisions table naming it in full, silently never worked.

    Stopwords are dropped so "Fund of Funds" gives FF, not FOF.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9]+", name)
             if w.lower() not in _STOPWORDS]
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
    # First-token fallback. "Contoso CCI" vs "Contoso Capital" both
    # share first significant token "Contoso".
    name_first = _first_token(firm_canonical)
    if name_first:
        for key, status in statuses.items():
            if _first_token(key) == name_first:
                return status
    # Acronym fallback. "NIF" -> "Northwind Innovation Fund".
    #
    # Now that `_acronym` actually reads a lowercased key, this fallback fires,
    # and a fallback that fires can be wrong. It is the LAST resort, three
    # strategies down, and its output is a wave status the operator sorts a live
    # raise by -- so it refuses an AMBIGUOUS match rather than picking one. Two
    # funds whose initials collide leave the firm at the default wave, which
    # reads as "not yet placed", instead of borrowing the other one's status.
    name_acronym = firm_canonical.upper() if firm_canonical.isupper() else _acronym(firm_canonical)
    if name_acronym and len(name_acronym) >= 2:
        matched = {status for key, status in statuses.items()
                   if key and (key.upper() == name_acronym or _acronym(key) == name_acronym)}
        if len(matched) == 1:
            return matched.pop()
    return DEFAULT_WAVE


def _slug_overlap(slug_a: str, slug_b: str, num: int) -> bool:
    """True if firm slug and dossier-rest slug share enough structure to be
    considered a match. Strategy:
    - exact equality
    - one is a prefix of the other (e.g. eurazeo vs eurazeo-growth-iv)
    - both share the first significant token (e.g. contoso-capital vs
      contoso-capital-international)
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

    Corrupt lines are skipped; a missing file returns {}.
    Last entry per firm wins, so re-marking a firm overwrites the earlier ts
    and a tombstone entry ('undo': True) cancels the mark. A subsequent
    real mark restores it again.

    Over SEND_LOG_MAX_BYTES this reads the TAIL, through the same
    `read_jsonl_capped` primitive the other eight capped logs use, and that
    function logs the truncation. It used to `return {}` on the whole file, so
    one byte past the cap made EVERY firm read as never-sent and the program
    view invited a second first-touch to people who already had one. The write
    half of this same log was migrated to the shared O_APPEND primitive
    (`append_jsonl`, sixty lines down) and the read half kept the old shape.
    A dropped head still loses the firms whose only mark is in it; that is one
    stale row instead of all of them, and it is now logged rather than silent.
    """
    log_path = workspace_root / PROGRAM_DIR / SEND_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, SEND_LOG_MAX_BYTES)
    out: dict[int, dict] = {}
    for entry in entries:
        firm_num = entry.get("firm_num")
        if not isinstance(firm_num, int):
            continue
        # Tombstone: cancel any prior mark for this firm.
        if is_undo(entry):
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
    at 22). Note is trimmed to 200 chars + sanitized of newlines.

    Appends ONE line via the shared O_APPEND primitive. This block claimed an
    "atomic rewrite on each append" until 2026-08-24, which is the read-modify-
    rewrite `_jsonl.py` exists to have replaced; the rest of this module (the
    lock comment, `undo_sent`, the tombstone replay in `_read_send_log`) is
    built around an append-only log, so the promise was the stale part.
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
        "date": datetime.now(get_default_tz()).date().isoformat(),
        "ts": now.isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / PROGRAM_DIR / SEND_LOG_FILE
    with _SEND_LOG_LOCK:
        try:
            append_jsonl(log_path, entry)
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
        try:
            append_jsonl(log_path, entry)
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
    target_raw = workspace_root / rel_path
    target = target_raw.resolve()
    program_root = (workspace_root / PROGRAM_DIR).resolve()
    try:
        target.relative_to(program_root)
    except ValueError:
        return {"ok": False, "error": "path escapes program dir"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if contains_symlink(workspace_root / PROGRAM_DIR, target_raw):
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
