"""Real-data source for the /library endpoint.

Walks knowledge/ (excluding INDEX.md and dotfiles), parses YAML
frontmatter, returns 50 most-recently-updated notes sorted DESC by the
'updated' field (falling back to file mtime if absent).

Phase 1.12 is browse-only. Phase 2 will add full-text search + a click
handler that drills into the note detail.
"""
import re
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.utils.paths import get_data_root

LIBRARY_ROW_CAP = 50
KNOWLEDGE_ROOT = "knowledge"
# Skip these subtrees + filenames.
SKIP_DIRS = {"_archive", "_work", "_build"}
SKIP_NAMES = {"INDEX.md", "README.md"}

# Phase 1.66: lock the type-section order so the Library page sectioning
# is deterministic. Types not in this list fall to the end alphabetically.
LIBRARY_TYPE_ORDER = ["principle", "position", "source", "reference", "conflict", "observation"]

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        # Skip list items continuing a previous key (cheap parser limitation).
        if line.startswith(" ") or line.startswith("\t"):
            continue
        km = _KEY_RE.match(line)
        if not km:
            continue
        key = km.group(1).strip()
        val = km.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[key] = val
    return result


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def list_library(workspace_root: Path, data_root: "Path | None" = None) -> dict:
    """Walk knowledge/ for markdown notes with frontmatter.

    Returns:
        {
            "notes": [
                {
                    "path": "knowledge/notes/example.md",
                    "title": str,
                    "type": str,
                    "keywords": list[str],  # parsed from "[a, b, c]" or empty
                    "status": str,
                    "updated": "YYYY-MM-DD" or None,
                    "mtime": ISO 8601 UTC,
                },
                ...
            ] sorted by updated DESC (None last) then mtime DESC, capped at LIBRARY_ROW_CAP,
            "counts": {"principle": N, "position": N, "source": N, ...},
            "total": int (full count, NOT capped),
            "data_time": ISO 8601 UTC of most-recent file mtime,
        }

    HEADING OS engine/data split: knowledge/ is DATA, so it resolves under
    ``data_root``. Back-compat: falls back to ``workspace_root`` when not
    supplied (identical on transitional ceo-main).
    """
    if data_root is None:
        data_root = get_data_root()
    root = data_root / KNOWLEDGE_ROOT
    if not root.exists():
        return {"notes": [], "counts": {}, "total": 0, "data_time": None}

    notes_collected = []
    type_counts: dict = {}
    most_recent_mtime: float = 0.0

    for p in root.rglob("*.md"):
        if not p.is_file():
            continue
        # Skip dotted dirs.
        parts_relative = p.relative_to(root).parts
        if any(seg.startswith(".") for seg in parts_relative):
            continue
        # Skip helper subtrees.
        if any(seg in SKIP_DIRS for seg in parts_relative):
            continue
        # Skip top-level convention files.
        if p.name in SKIP_NAMES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
            stat_result = p.stat()
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        ntype = fm.get("type", "")
        if ntype:
            type_counts[ntype] = type_counts.get(ntype, 0) + 1
        keywords_raw = fm.get("keywords", "")
        keywords: list = []
        # Parse "[a, b, c]" or "a, b, c" formats.
        if keywords_raw:
            inner = keywords_raw.strip().strip("[]")
            keywords = [k.strip().strip('"').strip("'") for k in inner.split(",") if k.strip()]
        mtime_iso = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).isoformat()
        notes_collected.append({
            "path": str(p.relative_to(data_root)).replace("\\", "/"),
            "title": fm.get("title", p.stem),
            "type": ntype,
            "keywords": keywords,
            "status": fm.get("status", ""),
            "updated": fm.get("updated") or None,
            "mtime": mtime_iso,
            "_mtime_ts": stat_result.st_mtime,  # internal sort key, stripped before return
            "_updated_date": _parse_iso_date(fm.get("updated")),
        })
        if stat_result.st_mtime > most_recent_mtime:
            most_recent_mtime = stat_result.st_mtime

    total = len(notes_collected)

    # Sort by updated DESC (None last), tiebreak by mtime DESC.
    def sort_key(n):
        d = n["_updated_date"]
        return (
            0 if d is None else 1,
            d.toordinal() if d else 0,
            n["_mtime_ts"],
        )
    notes_collected.sort(key=sort_key, reverse=True)
    notes_collected = notes_collected[:LIBRARY_ROW_CAP]

    # Strip internal sort keys.
    for n in notes_collected:
        n.pop("_mtime_ts", None)
        n.pop("_updated_date", None)

    data_time = (
        datetime.fromtimestamp(most_recent_mtime, tz=timezone.utc).isoformat()
        if most_recent_mtime else None
    )
    # Phase 1.66: locked type order + tail of unknown types alphabetically.
    known = [t for t in LIBRARY_TYPE_ORDER if t in type_counts]
    unknown = sorted([t for t in type_counts if t not in LIBRARY_TYPE_ORDER])
    type_order = known + unknown
    return {
        "notes": notes_collected,
        "counts": type_counts,
        "type_order": type_order,
        "total": total,
        "data_time": data_time,
    }


# Path safety: only files under knowledge/, no traversal, no symlinks.
NOTE_MAX_BYTES = 200_000  # 200 KB upper bound for any zk note


def read_note(workspace_root: Path, rel_path: str, data_root: "Path | None" = None) -> dict:
    """Read a single note file safely.

    Path validation:
    - Must start with 'knowledge/'
    - Must resolve to a file inside workspace_root/knowledge
    - Must not be a symlink (avoid /etc/passwd via symlink trick)
    - Must be under NOTE_MAX_BYTES

    Returns:
        {"ok": True, "path": rel_path, "content": str, "size": int}
        OR
        {"ok": False, "error": str}

    HEADING OS engine/data split: knowledge/ is DATA, so it resolves under
    ``data_root`` (falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    if not rel_path or not isinstance(rel_path, str):
        return {"ok": False, "error": "missing path"}
    # Normalize forward slashes (Windows-friendly).
    rel_path = rel_path.replace("\\", "/").lstrip("./")
    if not rel_path.startswith("knowledge/"):
        return {"ok": False, "error": "path must be under knowledge/"}
    # No traversal segments.
    parts = [p for p in rel_path.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return {"ok": False, "error": "invalid path segment"}
    target = (data_root / rel_path).resolve()
    knowledge_root = (data_root / "knowledge").resolve()
    # Resolved target must still be inside knowledge_root.
    try:
        target.relative_to(knowledge_root)
    except ValueError:
        return {"ok": False, "error": "path escapes knowledge/"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    # Block symlinks - on Windows is_symlink may be False for junctions;
    # the resolve() above already follows symlinks, then our relative_to
    # check would catch any escape. Still, explicit is good.
    try:
        if target.is_symlink():
            return {"ok": False, "error": "symlinks not allowed"}
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    if not target.suffix.lower() == ".md":
        return {"ok": False, "error": "only .md files allowed"}
    try:
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > NOTE_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {NOTE_MAX_BYTES})"}
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "path": rel_path, "content": content, "size": size}
