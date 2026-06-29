"""Real-data sources for the /studio endpoint.

Phase 1.38: the Studio page is a reference to artifacts created for
human attention - currently the LinkedIn posts and articles in
datastore/content/linkedin-archive/, each a folder holding the markdown
source plus its image variants. `list_artifacts` / `read_artifact` /
`resolve_artifact_image` drive that page.

The older `recent_inflight_items` / `read_inflight` functions below scan
the in-flight output directories; `recent_inflight_items` is retained
because the unified search (sources/search.py) still consumes it.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.utils.paths import get_data_root

# Must stay in sync with sources/pulse.IN_FLIGHT_DIRS (path components).
# Pulse's count and Studio's item list must agree on the in-flight scope.
IN_FLIGHT_DIRS = (
    ("outputs/operations/email-intelligence", "email"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    ("outputs/content/linkedin", "linkedin"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    ("outputs/intel", "intel"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    ("outputs/negotiations", "negotiations"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    ("outputs/documents", "documents"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    ("outputs/content/tribe", "tribe"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
    ("outputs/operations/fundraising", "fundraising"),  # leak-guard: ok (in-flight scan suffix rooted by caller; data-root wiring is Plan 3)
)
IN_FLIGHT_WINDOW_DAYS = 7
STUDIO_ROW_CAP = 50

# Directories pruned during the scan before descending into them. Used to
# skip build-pipeline artefacts and template scaffolding that would
# otherwise inflate the in-flight count + waste stat() syscalls (each
# costs a 9P round-trip when the daemon runs in WSL).
_SKIP_DIRS = frozenset({"_archive", "_work", "_build", "_template"})


def _scan_inflight_tree(workspace_root: Path, window_days: int) -> list[dict]:
    """Walk IN_FLIGHT_DIRS once, returning every recent file as a dict.

    Uses os.scandir + manual recursion so we can prune _SKIP_DIRS BEFORE
    descending into them (Path.rglob has no pruning hook and stats every
    file under the tree). DirEntry.stat() reuses the cached stat from
    the directory scan, halving syscall count vs. a separate Path.stat().

    Both recent_inflight_items() and pulse.in_flight_count() derive from
    this list, so the daemon scans the tree once per refresh instead of
    twice. Combined with pruning, this turned the WSL refresher tick
    from ~8 s to <1 s.
    """
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp()
    items: list[dict] = []
    for rel_dir, category in IN_FLIGHT_DIRS:
        root = workspace_root / rel_dir
        if not root.exists():
            continue
        stack = [str(root)]
        while stack:
            dir_path = stack.pop()
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        name = entry.name
                        if name.startswith("."):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if name in _SKIP_DIRS:
                                    continue
                                stack.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if st.st_mtime < cutoff_ts:
                            continue
                        rel = Path(entry.path).relative_to(workspace_root).as_posix()
                        items.append({
                            "path": rel,
                            "name": name,
                            "category": category,
                            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                            "size_bytes": st.st_size,
                        })
            except OSError:
                continue
    return items


def recent_inflight_items(workspace_root: Path, window_days: int = IN_FLIGHT_WINDOW_DAYS,
                          data_root: "Path | None" = None) -> dict:
    """Scan in-flight dirs for files modified within the window.

    Returns:
        {
            "items": [...] sorted by mtime DESC, capped at STUDIO_ROW_CAP,
            "categories": {"linkedin": 5, "intel": 3, ...} (post-cap counts),
            "data_time": ISO 8601 UTC of the most-recent item (None if empty),
            "total_count": int - TRUE count across all in-flight dirs
                (pre-cap; what pulse.kpi.in_flight reports).
        }

    HEADING OS engine/data split: the in-flight output dirs are DATA,
    resolved under ``data_root`` (falls back to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    all_items = _scan_inflight_tree(data_root, window_days)
    all_items.sort(key=lambda r: r["mtime"], reverse=True)
    total_count = len(all_items)
    items = all_items[:STUDIO_ROW_CAP]

    categories: dict[str, int] = {}
    for r in items:
        categories[r["category"]] = categories.get(r["category"], 0) + 1

    data_time = items[0]["mtime"] if items else None
    return {
        "items": items,
        "categories": categories,
        "data_time": data_time,
        "total_count": total_count,
    }


# Allowed text extensions for in-band content rendering. Anything else
# returns a binary placeholder.
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".py", ".yaml", ".yml", ".csv", ".html", ".css", ".js"}

FILE_MAX_BYTES = 1_000_000  # 1 MB upper bound for any in-flight file


def _is_path_under_inflight_dir(workspace_root: Path, rel_path: str) -> bool:
    """True if rel_path resolves inside one of the IN_FLIGHT_DIRS."""
    target = (workspace_root / rel_path).resolve()
    for d, _ in IN_FLIGHT_DIRS:
        root = (workspace_root / d).resolve()
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def read_inflight(workspace_root: Path, rel_path: str) -> dict:
    """Read a single in-flight file safely.

    Path validation:
    - Must start with one of the IN_FLIGHT_DIRS prefixes
    - Resolved file must be inside that directory (no traversal escape)
    - No symlinks
    - Size <= FILE_MAX_BYTES
    - Text content only for files with extension in TEXT_EXTENSIONS

    Returns:
        {"ok": True, "path": rel_path, "content": str, "size": int, "is_text": bool}
        OR
        {"ok": False, "error": str}
    """
    if not rel_path or not isinstance(rel_path, str):
        return {"ok": False, "error": "missing path"}
    # Normalize forward slashes.
    rel_path = rel_path.replace("\\", "/").lstrip("./")
    # Allow only paths that BEGIN with one of the IN_FLIGHT_DIRS prefixes.
    if not any(rel_path.startswith(d + "/") for d, _ in IN_FLIGHT_DIRS):
        return {"ok": False, "error": "path not under in-flight dirs"}
    # No traversal segments.
    parts = [p for p in rel_path.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return {"ok": False, "error": "invalid path segment"}
    # Skip helper subtrees explicitly.
    if any(seg in {"_archive", "_work", "_build", "_template"} for seg in parts):
        return {"ok": False, "error": "path in excluded subtree"}
    target = (workspace_root / rel_path).resolve()
    # Defense-in-depth: resolved target must still match one of the IN_FLIGHT_DIRS roots.
    if not _is_path_under_inflight_dir(workspace_root, rel_path):
        return {"ok": False, "error": "path escapes in-flight dirs"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if target.is_symlink():
            return {"ok": False, "error": "symlinks not allowed"}
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    try:
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > FILE_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {FILE_MAX_BYTES})"}

    ext = target.suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        return {
            "ok": True,
            "path": rel_path,
            "content": f"[binary file: {target.name} ({size} bytes) - open externally]",
            "size": size,
            "is_text": False,
        }
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "path": rel_path, "content": content, "size": size, "is_text": True}


# ============================================================
# Phase 1.38: LinkedIn artifacts - the Studio page
# ============================================================
# datastore/content/linkedin-archive/{posts,articles}/{slug}/ holds one
# folder per content item: the {slug}.md source plus its image variants.
ARTIFACT_ROOT = "datastore/content/linkedin-archive"
_ARTIFACT_SUBDIRS = (("posts", "post"), ("articles", "article"))
ARTIFACT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ARTIFACT_IMAGE_MAX_BYTES = 8_000_000
ARTIFACT_MD_MAX_BYTES = 500_000
_ARTIFACT_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FM_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def _artifact_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter -> (dict, body-without-frontmatter)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm: dict = {}
    for line in m.group(1).splitlines():
        km = _FM_KEY_RE.match(line.strip())
        if km:
            val = km.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            fm[km.group(1).strip()] = val
    return fm, text[m.end():]


def _artifact_preview(body: str, limit: int = 240) -> str:
    """First meaningful prose of the body - headings/blank lines skipped."""
    out: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---"):
            continue
        out.append(s)
        if sum(len(x) for x in out) >= limit:
            break
    text = " ".join(out)
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def _date_from_slug(slug: str) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", slug)
    return m.group(1) if m else ""


def _title_from_slug(slug: str) -> str:
    s = re.sub(r"^\d{4}-\d{2}-\d{2}[_-]?", "", slug)
    s = re.sub(r"^linkedin[-_](post|article|comment)[-_]", "", s)
    return s.replace("-", " ").replace("_", " ").strip().title() or slug


def _artifact_images(folder: Path, workspace_root: Path) -> list[str]:
    """Workspace-relative paths of every image file in an artifact folder."""
    return sorted(
        str(p.relative_to(workspace_root)).replace("\\", "/")
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in ARTIFACT_IMAGE_EXTS
    )


def list_artifacts(workspace_root: Path) -> dict:
    """Scan datastore/content/linkedin-archive/{posts,articles}/.

    Each folder is one content item (the {slug}.md source + image
    variants). Returns {artifacts, counts, total, data_time}, sorted by
    date DESC.
    """
    root = workspace_root / ARTIFACT_ROOT
    artifacts: list[dict] = []
    most_recent: float = 0.0
    for subdir, kind in _ARTIFACT_SUBDIRS:
        base = root / subdir
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or folder.name.startswith((".", "_")):
                continue
            slug = folder.name
            if not _ARTIFACT_SLUG_RE.match(slug):
                continue
            md = folder / f"{slug}.md"
            if not md.is_file():
                mds = sorted(folder.glob("*.md"))
                if not mds:
                    continue
                md = mds[0]
            try:
                text = md.read_text(encoding="utf-8")
                mtime = md.stat().st_mtime
            except OSError:
                continue
            fm, body = _artifact_frontmatter(text)
            images = _artifact_images(folder, workspace_root)
            artifacts.append({
                "kind": kind,
                "slug": slug,
                "title": fm.get("title") or _title_from_slug(slug),
                "date": fm.get("date") or _date_from_slug(slug),
                "series": fm.get("series", ""),
                "format": fm.get("format", ""),
                "status": fm.get("status", ""),
                "summary": _artifact_preview(body),
                "images": images,
                "image_count": len(images),
            })
            if mtime > most_recent:
                most_recent = mtime

    artifacts.sort(key=lambda a: (a["date"] or "", a["slug"]), reverse=True)
    counts: dict = {}
    for a in artifacts:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    data_time = (
        datetime.fromtimestamp(most_recent, tz=timezone.utc).isoformat()
        if most_recent else None
    )
    return {"artifacts": artifacts, "counts": counts,
            "total": len(artifacts), "data_time": data_time}


def _artifact_folder(workspace_root: Path, kind: str, slug: str) -> Path | None:
    """Resolve + validate the folder for (kind, slug). Returns Path or None."""
    subdir = {"post": "posts", "article": "articles"}.get(kind)
    if subdir is None or not slug or not _ARTIFACT_SLUG_RE.match(slug):
        return None
    base = (workspace_root / ARTIFACT_ROOT / subdir).resolve()
    folder = (workspace_root / ARTIFACT_ROOT / subdir / slug).resolve()
    try:
        folder.relative_to(base)
    except ValueError:
        return None
    return folder if folder.is_dir() else None


def read_artifact(workspace_root: Path, kind: str, slug: str) -> dict:
    """Read one artifact - full markdown source + image list.

    Returns {ok: True, kind, slug, title, date, content, images} or
    {ok: False, error}.
    """
    folder = _artifact_folder(workspace_root, kind, slug)
    if folder is None:
        return {"ok": False, "error": "artifact not found"}
    md = folder / f"{slug}.md"
    if not md.is_file():
        mds = sorted(folder.glob("*.md"))
        if not mds:
            return {"ok": False, "error": "no markdown source"}
        md = mds[0]
    try:
        if md.is_symlink() or md.stat().st_size > ARTIFACT_MD_MAX_BYTES:
            return {"ok": False, "error": "source unreadable"}
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    fm, _body = _artifact_frontmatter(text)
    return {
        "ok": True, "kind": kind, "slug": slug,
        "title": fm.get("title") or _title_from_slug(slug),
        "date": fm.get("date") or _date_from_slug(slug),
        "content": text,
        "images": _artifact_images(folder, workspace_root),
    }


def resolve_artifact_image(workspace_root: Path, rel_path: str) -> Path | None:
    """Validate `rel_path` points at an image inside the LinkedIn archive.

    Returns the absolute Path to serve, or None on any validation
    failure (outside the archive, traversal, non-image, symlink, oversize).
    """
    if not rel_path or not isinstance(rel_path, str):
        return None
    rel = rel_path.replace("\\", "/").lstrip("./")
    if not rel.startswith(ARTIFACT_ROOT + "/"):
        return None
    parts = [p for p in rel.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return None
    archive_root = (workspace_root / ARTIFACT_ROOT).resolve()
    target = (workspace_root / rel).resolve()
    try:
        target.relative_to(archive_root)
    except ValueError:
        return None
    if target.suffix.lower() not in ARTIFACT_IMAGE_EXTS:
        return None
    try:
        if target.is_symlink() or not target.is_file():
            return None
        if target.stat().st_size > ARTIFACT_IMAGE_MAX_BYTES:
            return None
    except OSError:
        return None
    return target
