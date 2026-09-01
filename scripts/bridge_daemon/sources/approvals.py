"""Real-data source for the /approvals endpoint.

Walks outputs/communications/email/ for outbound email drafts pending
CEO go/no-go. Each .md file is parsed for its To/Cc/Subject header
block. Phase 1.56 is read-only; sending stays on scripts/send-email.py
to keep the high-blast-radius send path off the dashboard.

Phase 1.71 adds a mark-sent workflow so the CEO can clear a draft from
the queue after sending via scripts/send-email.py. The draft file stays
on disk; the dashboard simply filters it out of the queue. JSONL log
with tombstone undo, mirroring the inbox-dismiss pattern.

A future phase may extend coverage to other draft surfaces (LinkedIn
posts in outputs/content/linkedin/, fundraising first-touches, etc.).
"""
import logging
import re
import threading
from datetime import date, datetime, timezone
from scripts.bridge_daemon._safepath import contains_symlink, normalize_rel_path
from scripts.utils.workspace import get_default_tz
from pathlib import Path

from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped
from scripts.bridge_daemon._shapes import entry_ts, is_undo

logger = logging.getLogger(__name__)

EMAIL_DRAFTS_DIR = "outputs/communications/email"  # leak-guard: ok (relative suffix rooted by caller)
APPROVALS_ROW_CAP = 20  # safety cap; CEO unlikely to have more pending
DRAFT_MAX_BYTES = 200_000  # 200 KB upper bound on any single draft body read

# Phase 1.71: sent log for the mark-sent workflow.
SENT_LOG_FILE = "outputs/operations/bridge/approval-sent-log.jsonl"  # leak-guard: ok (relative suffix rooted by caller)
SENT_LOG_MAX_BYTES = 1_000_000
SENT_NOTE_MAX_CHARS = 200
_SENT_LOG_LOCK = threading.Lock()

# Header lines in each draft file. Look for '**Key:** value' until the
# first separator '---' line. Skip the opening H1.
_HDR_RE = re.compile(r"^\*\*([A-Za-z]+):\*\*\s*(.+?)\s*$")


# Forward-slash + trim, so log entries are comparable across OSes. This module
# owned the only correct copy of that normalisation until 2026-08-29, while the
# five other readers under `sources/` stripped with `.lstrip("./")` and accepted
# what this one refused. The implementation now lives once, in `_safepath`, and
# its docstring carries the measurement; the local name stays because six call
# sites below read it.
_normalize_rel_path = normalize_rel_path


def validate_draft_rel_path(rel_path: str) -> str | None:
    """Return an error string for a bad draft path, or None when it is fine.

    One validator for `read_draft`, `mark_sent` and `undo_sent`, which guard the
    same directory and used to disagree: the writers checked only the
    `EMAIL_DRAFTS_DIR` prefix, so a traversal-shaped string like
    `outputs/.../email/../email/x.md` was rejected by the reader and written
    straight into the sent log by the writer. Two validators on one tree drift;
    the weaker one is the one attackers and typos find.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        return "path is required"
    normalised = _normalize_rel_path(rel_path)
    if not normalised.startswith(EMAIL_DRAFTS_DIR + "/"):
        return "path must be under email drafts dir"
    parts = [p for p in normalised.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return "invalid path segment"
    if not normalised.endswith(".md"):
        return "path must be a .md draft"
    return None


def read_sent_log(workspace_root: Path) -> set[str]:
    """Return the set of draft paths the CEO has marked sent.

    Last entry per path wins; an undo tombstone removes the path from
    the set so the draft surfaces again. Mirrors the inbox-dismiss log.
    """
    log_path = workspace_root / SENT_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, SENT_LOG_MAX_BYTES)
    out: dict[str, dict] = {}
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        path = _normalize_rel_path(path)
        if is_undo(entry):
            out.pop(path, None)
            continue
        out[path] = entry
    return set(out.keys())


def sent_log_recent(workspace_root: Path, limit: int = 20) -> list[dict]:
    """Return the most-recent active sent entries (tombstoned ones omitted).

    Each entry: {path, ts, date, note, filename}. Ordered by ts DESC.
    Used by the /approvals page's "Recently sent" footer so the CEO can
    restore an accidental mark-sent.
    """
    log_path = workspace_root / SENT_LOG_FILE
    entries, _truncated = read_jsonl_capped(log_path, SENT_LOG_MAX_BYTES)
    # Last record per path wins (matches read_sent_log semantics).
    active: dict[str, dict] = {}
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        path = _normalize_rel_path(path)
        if is_undo(entry):
            active.pop(path, None)
            continue
        active[path] = entry
    rows = []
    for path, entry in active.items():
        rows.append({
            "path": path,
            "filename": path.rsplit("/", 1)[-1],
            "ts": entry_ts(entry),
            "date": entry.get("date", ""),
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[: max(0, int(limit))]


def mark_sent(workspace_root: Path, rel_path: str, note: str = "") -> dict:
    """Append a sent entry for `rel_path`. Returns {ok, path, ts}.

    Path must be under EMAIL_DRAFTS_DIR; otherwise rejected. Note is
    capped at SENT_NOTE_MAX_CHARS and stripped of newlines.
    """
    problem = validate_draft_rel_path(rel_path)
    if problem:
        return {"ok": False, "error": problem}
    rel_path = _normalize_rel_path(rel_path)
    safe_note = (note or "").replace("\n", " ").replace("\r", " ").strip()[:SENT_NOTE_MAX_CHARS]
    # Phase 1.80: 'date' is local (CEO calendar day), 'ts' stays UTC.
    now = datetime.now(timezone.utc)
    entry = {
        "path": rel_path,
        "ts": now.isoformat(),
        "date": datetime.now(get_default_tz()).date().isoformat(),
        "note": safe_note,
    }
    log_path = workspace_root / SENT_LOG_FILE
    with _SENT_LOG_LOCK:
        try:
            # `mkdir` INSIDE the try. It sat above it, so a read-only mount, a
            # missing parent, or `outputs/operations/bridge` existing as a plain
            # file raised OSError straight out of a function whose contract is
            # "{ok, path, ts}" or "{ok: False, error}" -- and the endpoint 500'd
            # instead of surfacing a handled error. `heartbeat.py` fixed this
            # exact shape on 2026-08-24, in words this file could have used:
            # "mkdir sat above the try ... raised OSError straight out of a
            # function whose docstring promises the opposite".
            log_path.parent.mkdir(parents=True, exist_ok=True)
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "path": rel_path, "ts": entry["ts"], "date": entry["date"]}


def undo_sent(workspace_root: Path, rel_path: str) -> dict:
    """Tombstone a prior mark-sent for `rel_path`. Idempotent."""
    problem = validate_draft_rel_path(rel_path)
    if problem:
        return {"ok": False, "error": problem}
    rel_path = _normalize_rel_path(rel_path)
    now = datetime.now(timezone.utc)
    entry = {"path": rel_path, "undo": True, "ts": now.isoformat()}
    log_path = workspace_root / SENT_LOG_FILE
    with _SENT_LOG_LOCK:
        try:
            # `mkdir` INSIDE the try. It sat above it, so a read-only mount, a
            # missing parent, or `outputs/operations/bridge` existing as a plain
            # file raised OSError straight out of a function whose contract is
            # "{ok, path, ts}" or "{ok: False, error}" -- and the endpoint 500'd
            # instead of surfacing a handled error. `heartbeat.py` fixed this
            # exact shape on 2026-08-24, in words this file could have used:
            # "mkdir sat above the try ... raised OSError straight out of a
            # function whose docstring promises the opposite".
            log_path.parent.mkdir(parents=True, exist_ok=True)
            append_jsonl(log_path, entry)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "path": rel_path, "ts": entry["ts"]}


def _parse_headers(text: str) -> dict:
    """Return {to, cc, subject, _body_offset} from the draft header block.

    ``_body_offset`` is the character index where the body content begins,
    after the first standalone ``---`` separator. It is 0 when the text holds
    no separator, and that reads unambiguously: a real ``---`` line is four
    characters at minimum, so a separator that WAS found always leaves a
    positive offset. Zero means "no separator", never "separator at the start".

    The header block is CONTIGUOUS: capture begins at the first ``**Key:**``
    line and ends at the first ordinary prose line after it, or at ``---``,
    whichever comes first. Without that end condition the regex ran over every
    line of the 4 KB the caller reads, so a draft with no separator -- or one
    whose separator sits past that read cap -- had its real ``**To:**``
    silently OVERWRITTEN by a header-shaped line further down in the body, and
    the approvals queue displayed a recipient the draft was never addressed to.
    Last-write-wins over the whole file is not a header parser.

    A blank line inside the block is tolerated, so a header block a human broke
    up with one still parses whole.
    """
    headers: dict = {}
    body_offset = 0
    pos = 0
    # `started` is read, unlike the `in_header_block` flag removed from here
    # earlier: that one was initialised True, never set False, and gated a
    # `continue` that could not run. This flag is what distinguishes preamble
    # (an H1 above the headers, which must not end a block that has not begun)
    # from body prose (which must).
    started = False
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip()
        pos += len(raw)
        if line == "---":
            body_offset = pos
            break
        m = _HDR_RE.match(line)
        if m:
            started = True
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            headers[key] = val
        elif started and line:
            # Ordinary prose after the header block. The block has ended, and
            # anything header-shaped below here is body content.
            break
    headers["_body_offset"] = body_offset
    return headers


def list_approvals(workspace_root: Path) -> dict:
    """Return pending email drafts as approval items.

    Sort: most-recently-modified first. Cap at APPROVALS_ROW_CAP.

    Returns:
        {
            "items": [
                {
                    "kind": "email-draft",
                    "path": "outputs/communications/email/foo.md",  # leak-guard: ok (docstring return-shape example, not a filesystem path)
                    "filename": str,
                    "title": str,        # H1 from file or filename
                    "to": str,
                    "cc": str,
                    "subject": str,
                    "mtime": ISO 8601 UTC,
                },
                ...
            ],
            "total": int,
            "data_time": ISO 8601 UTC of most-recent mtime or None,
        }
    """
    drafts_dir = workspace_root / EMAIL_DRAFTS_DIR
    if not drafts_dir.is_dir():
        # The SAME shape the parsed payload returns. `pipeline.py` learned this
        # the hard way and pulled its zero payload into one `_empty_pipeline`
        # writer: a key added to the parsed dict and not to the degraded one is
        # missing exactly when the drafts directory is absent. Caught here by a
        # test written for the keys added on 2026-08-28, minutes after they were
        # added.
        return {"items": [], "total": 0, "truncated": False,
                "row_cap": APPROVALS_ROW_CAP, "sent_count": 0,
                "data_time": None}
    # Phase 1.71: filter out drafts the CEO has marked sent.
    sent_paths = read_sent_log(workspace_root)
    sent_count = 0
    items: list[dict] = []
    most_recent: float = 0.0
    for p in drafts_dir.glob("*.md"):
        # The symlink policy `read_draft` enforces, applied by the LIST scan
        # too. `p.is_file()` follows a symlink, so a link planted in the drafts
        # directory had the first 4 KB of a file OUTSIDE the workspace read
        # here and its `**To:**` / `**Subject:**` published on the approvals
        # queue -- while the drill-down on that same row refused to open it,
        # and the row stayed markable-sent. `capabilities.py` carries the twin
        # of this check.
        try:
            if contains_symlink(drafts_dir, p):
                logger.warning("skipping symlinked draft %s; symlinks are not "
                               "served, and read_draft refuses this row too", p)
                continue
        except OSError:
            logger.warning("skipping unstattable draft %s", p, exc_info=True)
            continue
        if not p.is_file():
            continue
        rel = str(p.relative_to(workspace_root)).replace("\\", "/")
        if rel in sent_paths:
            sent_count += 1
            continue
        try:
            stat = p.stat()
            # Read just enough to grab headers + H1 (cap bytes for safety).
            with p.open("r", encoding="utf-8") as f:
                head = f.read(4096)
        except (OSError, UnicodeDecodeError):
            # Named, like the two skips above it. This was a bare `continue`,
            # the only silent one in the loop: a draft the daemon could not
            # read left the approvals queue with no record that it had ever
            # been there. MEASURED 2026-08-31 on a chmod 000 draft carrying
            # `**Subject:** DO NOT LOSE` beside one readable draft: the
            # listing returned 1 item and logged nothing at any level. This
            # queue is where the operator decides what to send, so a draft
            # that is present on disk and absent from the page has to say why.
            #
            # `UnicodeDecodeError` joined the clause on 2026-09-01. It is a
            # `ValueError`, not an `OSError`, so a draft that is not valid
            # UTF-8 was not skipped-and-logged here: it raised straight out of
            # the walk and took the WHOLE queue with it, losing every other
            # pending draft rather than the one bad file. MEASURED that day
            # with one 0xe9 in a `**Subject:**` line beside one clean draft:
            # `list_approvals` raised UnicodeDecodeError. Skipping one draft is
            # the documented behaviour above; dropping the page is not.
            logger.warning("skipping unreadable draft %s; it will not appear "
                           "in the approvals queue", p, exc_info=True)
            continue
        # The SIZE cap `read_draft` enforces, applied by the LIST scan too, for
        # the same reason the symlink rule above it is. MEASURED 2026-09-01 on a
        # 250 KB draft: `list_approvals` published the row with its `**To:**`
        # and `**Subject:**` and `read_draft` answered
        # `file too large (250054 bytes, max 200000)`, so the approvals queue
        # carried a card the operator cannot open and can still mark sent.
        if stat.st_size > DRAFT_MAX_BYTES:
            logger.warning("skipping oversized draft %s (%d bytes, max %d); "
                           "read_draft refuses this row too",
                           p, stat.st_size, DRAFT_MAX_BYTES)
            continue
        headers = _parse_headers(head)
        # First H1 if present, else filename stem.
        title = ""
        for line in head.splitlines():
            s = line.strip()
            if s.startswith("# ") and not s.startswith("## "):
                title = s[2:].strip()
                break
        if not title:
            title = p.stem.replace("_", " ").replace("-", " ")
        items.append({
            "kind": "email-draft",
            "path": rel,
            "filename": p.name,
            "title": title,
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "subject": headers.get("subject", ""),
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "_mtime_ts": stat.st_mtime,
        })
        if stat.st_mtime > most_recent:
            most_recent = stat.st_mtime
    items.sort(key=lambda x: x.pop("_mtime_ts"), reverse=True)
    # `total` is measured BEFORE the cap, because it is not a length of this
    # list: `pulse.py` reads it into the `approvals_total` KPI, which the
    # dashboard shows as "drafts waiting for approval". Counting the sliced list
    # made 35 pending drafts read as exactly APPROVALS_ROW_CAP, and the number
    # stopped moving as the backlog grew - a truncated list reported as a
    # complete count. `threads.py` measures its own total before the slice; this
    # is that fix landing in the second of two copies.
    total = len(items)
    items = items[:APPROVALS_ROW_CAP]
    data_time = (
        datetime.fromtimestamp(most_recent, tz=timezone.utc).isoformat()
        if most_recent else None
    )
    return {
        "items": items,
        "total": total,
        # Named so a caller cannot mistake a capped page for the whole set.
        "truncated": total > len(items),
        "row_cap": APPROVALS_ROW_CAP,
        "sent_count": sent_count,
        "data_time": data_time,
    }


def read_draft(workspace_root: Path, rel_path: str) -> dict:
    """Read a single draft file safely.

    Path validation: `validate_draft_rel_path` for the string, then the
    filesystem checks this function owns -- resolves inside the drafts
    directory, no symlink, a real file, under DRAFT_MAX_BYTES.

    The string half USED to be a second copy of that validator, and the copy
    was the looser one on two counts. It stripped with `.lstrip("./")`, which
    removes a CHARACTER SET rather than a prefix, so `./outputs/...` was
    accepted here and refused by `mark_sent`; and it tested the suffix with
    `.lower()`, so `x.MD` read fine and could never be marked sent. The whole
    point of the shared validator is that the reader and the writers agree on
    what a draft path is.

    Returns:
        {"ok": True, "path": rel_path, "content": str, "size": int}
        OR
        {"ok": False, "error": str}
    """
    err = validate_draft_rel_path(rel_path)
    if err:
        return {"ok": False, "error": err}
    rel_path = _normalize_rel_path(rel_path)
    target_raw = workspace_root / rel_path
    target = target_raw.resolve()
    drafts_root = (workspace_root / EMAIL_DRAFTS_DIR).resolve()
    try:
        target.relative_to(drafts_root)
    except ValueError:
        return {"ok": False, "error": "path escapes drafts dir"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if contains_symlink(workspace_root / EMAIL_DRAFTS_DIR, target_raw):
            return {"ok": False, "error": "symlinks not allowed"}
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    # No suffix check here any more. It was the ONLY thing enforcing `.md` on
    # this path, and it used `.lower()`, which is what let `x.MD` through.
    # `validate_draft_rel_path` above now requires a lowercase `.md` on the
    # string itself, and the symlink refusal three lines up means the resolved
    # target cannot carry a different suffix from the string that named it.
    try:
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > DRAFT_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {DRAFT_MAX_BYTES})"}
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "path": rel_path, "content": content, "size": size}
