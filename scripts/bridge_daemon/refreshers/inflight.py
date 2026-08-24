"""Inflight scanner.

Scans the directories in ``SCAN_DIRS`` for `.md` files modified within the
retention window, and parses frontmatter for `session_id` when present.

Read `SCAN_DIRS`, not this line, for the list. Until 2026-08-24 the docstring
named four directories where the code scans three: it said `outputs/intel/`
where the code has `outputs/intel/osint`, and it claimed
`outputs/operations/email-intelligence/drafts/` was covered when nothing has
ever scanned it. Someone hunting a missing draft card would have started from
the wrong premise.

The scan is one level deep (`iterdir`), not a recursive walk; a file in a
subdirectory of a scanned directory is not picked up.
"""
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Serialised fingerprint of the last scan, so the component version only moves
# when the in-flight set actually moved. Module-level: one daemon, one scanner.
_LAST_SCAN: str | None = None

SCAN_DIRS = {
    "linkedin": "outputs/content/linkedin",  # leak-guard: ok (in-flight scan suffix rooted by caller)
    "osint": "outputs/intel/osint",  # leak-guard: ok (in-flight scan suffix rooted by caller)
    "negotiation": "outputs/negotiations",  # leak-guard: ok (in-flight scan suffix rooted by caller)
}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
SESSION_ID_RE = re.compile(r"^session_id:\s*(\S+)", re.MULTILINE)

def _extract_session_id(text: str) -> str | None:
    m = FRONTMATTER_RE.search(text)
    if not m:
        return None
    fm = m.group(1)
    sid_match = SESSION_ID_RE.search(fm)
    return sid_match.group(1) if sid_match else None

def scan_inflight(workspace_root: Path, retention_hours: int = 24) -> list[dict]:
    cutoff = time.time() - retention_hours * 3600
    rows = []
    for category, rel in SCAN_DIRS.items():
        d = workspace_root / rel
        if not d.exists():
            continue
        for p in d.iterdir():
            if not p.is_file() or p.suffix != ".md":
                continue
            if p.stat().st_mtime < cutoff:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rows.append({
                "id": p.stem,
                "category": category,
                "path": str(p.relative_to(workspace_root)),
                "modified_at": p.stat().st_mtime,
                "session_id": _extract_session_id(text),
            })
    return sorted(rows, key=lambda r: r["modified_at"], reverse=True)

def refresh(workspace_root: Path, state_obj) -> None:
    """Recompute the in-flight set and bump ONLY if it actually changed.

    This used to be a bare `state_obj.bump("inflight")` with no scan behind it:
    every tick told ETag-watching clients the data was new while nothing had
    been recomputed, and `scan_inflight` -- the function that does the work --
    was unreferenced. A freshness signal that fires on a schedule rather than on
    a change is the exact failure the freshness envelope was redesigned around.
    """
    try:
        current = json.dumps(scan_inflight(workspace_root), sort_keys=True, default=str)
    except OSError:
        logger.warning("inflight scan failed; leaving the component version alone",
                       exc_info=True)
        return
    global _LAST_SCAN
    if current == _LAST_SCAN:
        return
    _LAST_SCAN = current
    state_obj.bump("inflight")
