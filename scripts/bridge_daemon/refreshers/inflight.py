"""Inflight scanner.

Walks outputs/content/linkedin/, outputs/intel/, outputs/negotiations/,
outputs/operations/email-intelligence/drafts/ for files modified within
the retention window. Parses frontmatter for session_id if present.
"""
import re
import time
from pathlib import Path

SCAN_DIRS = {
    "linkedin": "outputs/content/linkedin",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "osint": "outputs/intel/osint",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    "negotiation": "outputs/negotiations",  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
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
    state_obj.bump("inflight")
