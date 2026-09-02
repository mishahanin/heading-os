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
import stat
import time
from pathlib import Path

from scripts.utils.markdown import FM_OK, split_frontmatter

logger = logging.getLogger(__name__)

# Serialised fingerprint of the last scan, so the component version only moves
# when the in-flight set actually moved. Module-level: one daemon, one scanner.
_LAST_SCAN: str | None = None

SCAN_DIRS = {
    "linkedin": "outputs/content/linkedin",  # leak-guard: ok (in-flight scan suffix rooted by caller)
    "osint": "outputs/intel/osint",  # leak-guard: ok (in-flight scan suffix rooted by caller)
    "negotiation": "outputs/negotiations",  # leak-guard: ok (in-flight scan suffix rooted by caller)
}

SESSION_ID_RE = re.compile(r"^session_id:\s*(\S+)", re.MULTILINE)


def _extract_session_id(text: str) -> str | None:
    """The `session_id` of an in-flight artifact, or None when it carries none.

    The fences come from the shared splitter. `^---\\n(.*?)\\n---` sat here and
    required the fence to be exactly three characters followed by a newline.
    MEASURED 2026-08-29: an artifact whose opening fence carries a trailing
    space or a tab returned None, so its row went to the dashboard with no
    session_id at all -- indistinguishable from an artifact that genuinely has
    none, which is the one thing this function exists to tell apart.
    """
    fm, _body, kind = split_frontmatter(text)
    if fm is None or kind != FM_OK:
        return None
    sid_match = SESSION_ID_RE.search(fm)
    return sid_match.group(1) if sid_match else None

def scan_inflight(workspace_root: Path, retention_hours: int = 24) -> list[dict]:
    cutoff = time.time() - retention_hours * 3600
    rows = []
    for category, rel in SCAN_DIRS.items():
        d = workspace_root / rel
        if not d.exists():
            continue
        # Per-DIRECTORY: a tree that became unreadable after `exists()` passed
        # took the whole scan with it, including the categories already walked.
        try:
            entries = sorted(d.iterdir())
        except OSError:
            logger.warning("inflight: could not list %s; skipping that category",
                           d, exc_info=True)
            continue
        for p in entries:
            if p.suffix != ".md":
                continue
            # Per-FILE, and ONE stat. These are producers' output directories,
            # where files rotate while this walks them, and the only exception
            # caught here was UnicodeDecodeError. A file unlinked between
            # `iterdir` and `stat`, or between the two SEPARATE stats this used
            # to take, raised OSError out of `scan_inflight` -- and `refresh`
            # catches it, so the price was the entire scan: every LinkedIn and
            # OSINT row already collected was thrown away and the component
            # version left alone, until a tick happened to race nothing.
            #
            # "ONE stat" was false when it was written: `p.stat()` followed by
            # `p.is_file()` is two, because `is_file()` stats again. Corrected
            # 2026-09-02 by making the code true rather than the comment
            # weaker. `is_file()` is exactly "stat, following symlinks, then
            # S_ISREG", so reading the mode off the stat already taken is the
            # same test with the second syscall removed. Only the enclosing
            # handler kept the extra window harmless, and a later reader has no
            # way to tell that handler is load-bearing from a comment that says
            # the race is not there.
            try:
                st = p.stat()
                mtime = st.st_mtime
                if not stat.S_ISREG(st.st_mode) or mtime < cutoff:
                    continue
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rows.append({
                "id": p.stem,
                "category": category,
                "path": str(p.relative_to(workspace_root)),
                "modified_at": mtime,
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
