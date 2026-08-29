"""Real-data sources for the /studio endpoint.

Phase 1.38: the Studio page is a reference to artifacts created for
human attention - currently the LinkedIn posts and articles in
datastore/content/linkedin-archive/, each a folder holding the markdown
source plus its image variants. `list_artifacts` / `read_artifact` /
`resolve_artifact_image` drive that page.

The older `recent_inflight_items` / `read_inflight` functions below scan
the in-flight output directories; `recent_inflight_items` is retained
because the unified search (sources/search.py) still consumes it.

Tests: tests/bridge/test_a_link_the_listing_followed_and_a_search_that_saw_fifty.py
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.bridge_daemon._safepath import contains_symlink, normalize_rel_path

logger = logging.getLogger(__name__)

# Must stay in sync with sources/pulse.IN_FLIGHT_DIRS (path components).
# Pulse's count and Studio's item list must agree on the in-flight scope.
IN_FLIGHT_DIRS = (
    ("outputs/operations/email-intelligence", "email"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
    ("outputs/content/linkedin", "linkedin"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
    ("outputs/intel", "intel"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
    ("outputs/negotiations", "negotiations"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
    ("outputs/documents", "documents"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
    ("outputs/content/tribe", "tribe"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
    ("outputs/operations/fundraising", "fundraising"),  # leak-guard: ok (in-flight scan suffix rooted by caller)
)
IN_FLIGHT_WINDOW_DAYS = 7
STUDIO_ROW_CAP = 50

# Directories pruned during the scan before descending into them. Used to
# skip build-pipeline artefacts and template scaffolding that would
# otherwise inflate the in-flight count + waste stat() syscalls (each
# costs a 9P round-trip when the daemon runs in WSL).
_SKIP_DIRS = frozenset({"_archive", "_work", "_build", "_template"})


def _scan_inflight_tree(data_root: Path, window_days: int) -> list[dict]:
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
        root = data_root / rel_dir
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
                        rel = Path(entry.path).relative_to(data_root).as_posix()
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


def recent_inflight_items(data_root: Path, window_days: int = IN_FLIGHT_WINDOW_DAYS,
                          cap: int | None = STUDIO_ROW_CAP) -> dict:
    """Scan in-flight dirs for files modified within the window.

    ``cap`` bounds the returned ``items`` list; pass None for every match.
    The /studio page wants the default (a display list), the unified search
    wants None: it was matching a query against the 50 newest files only, so a
    file the operator had edited six days ago was unfindable while the result
    set looked complete.

    Returns:
        {
            "items": [...] sorted by mtime DESC, capped at ``cap``,
            "categories": {"linkedin": 5, "intel": 3, ...} - counted over the
                RETURNED items, so they follow ``cap``,
            "data_time": ISO 8601 UTC of the most-recent item (None if empty),
            "total_count": int - TRUE count across all in-flight dirs
                (pre-cap; what pulse.kpi.in_flight reports).
        }

    HEADING OS engine/data split: the in-flight output dirs are DATA, so
    ``data_root`` is REQUIRED -- there is no default and no fallback. This
    line promised a ``get_data_root()`` fallback until 2026-08-24; the
    signature has no default, the body never called the seam, and the import
    that made the claim look true was unused. A caller who trusted it would
    have omitted the argument and got a TypeError.
    """
    all_items = _scan_inflight_tree(data_root, window_days)
    all_items.sort(key=lambda r: r["mtime"], reverse=True)
    total_count = len(all_items)
    items = all_items if cap is None else all_items[:cap]

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


def _is_path_under_inflight_dir(data_root: Path, rel_path: str) -> bool:
    """True if rel_path resolves inside one of the IN_FLIGHT_DIRS."""
    target = (data_root / rel_path).resolve()
    for d, _ in IN_FLIGHT_DIRS:
        root = (data_root / d).resolve()
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def read_inflight(data_root: Path, rel_path: str) -> dict:
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
    # Normalize forward slashes. No prefix is stripped: a leading dot or slash
    # means the caller did not name a served file, and the prefix check below
    # is where that dies. See `normalize_rel_path`.
    rel_path = normalize_rel_path(rel_path)
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
    target_raw = data_root / rel_path
    target = target_raw.resolve()
    # Defense-in-depth: resolved target must still match one of the IN_FLIGHT_DIRS roots.
    if not _is_path_under_inflight_dir(data_root, rel_path):
        return {"ok": False, "error": "path escapes in-flight dirs"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if contains_symlink(data_root, target_raw):
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


def _artifact_md_is_readable(base: Path, md: Path) -> bool:
    """False when any component from `base` down to `md` is a symlink, or the
    source is over the byte cap.

    Both guards lived only in `read_artifact`, the DETAIL view.
    `list_artifacts` walks the same tree on every /studio poll and called
    `read_text()` with neither, so the cheap page carried the risk the
    expensive one had already refused: a symlink out of the artifact tree, and
    an unbounded read into the daemon's memory. A guard on one of two readers
    of the same files is a guard on neither.

    The symlink half then under-delivered on that. It was `md.is_symlink()`,
    which only ever asks about the LEAF, while `folder.is_dir()` in the caller
    FOLLOWS links - so an artifact folder that was itself a symlink to a
    directory outside the archive passed every test: it resolved as a
    directory, its name matched the slug pattern, the markdown inside was a
    real file, and that file's prose was read straight into the /studio
    summary. `contains_symlink` asks the question the docstring already
    claimed, of every component, unresolved.
    """
    try:
        return (not contains_symlink(base, md)
                and md.stat().st_size <= ARTIFACT_MD_MAX_BYTES)
    except OSError:
        return False


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


def _artifact_images(folder: Path, data_root: Path) -> list[str]:
    """Workspace-relative paths of every image file in an artifact folder.

    An unreadable folder yields no images rather than an OSError. The markdown
    read beside this call is already guarded; this `iterdir` was not, so one
    permission-denied artifact directory failed the WHOLE Studio listing.
    """
    try:
        entries = list(folder.iterdir())
    except OSError:
        logger.warning("skipping unreadable artifact folder %s", folder, exc_info=True)
        return []
    return sorted(
        str(p.relative_to(data_root)).replace("\\", "/")
        for p in entries
        if p.is_file() and p.suffix.lower() in ARTIFACT_IMAGE_EXTS
    )


def list_artifacts(data_root: Path) -> dict:
    """Scan datastore/content/linkedin-archive/{posts,articles}/.

    Each folder is one content item (the {slug}.md source + image
    variants). Returns {artifacts, counts, total, data_time}, sorted by
    date DESC.
    """
    root = data_root / ARTIFACT_ROOT
    artifacts: list[dict] = []
    most_recent: float = 0.0
    for subdir, kind in _ARTIFACT_SUBDIRS:
        base = root / subdir
        if not base.is_dir():
            continue
        try:
            folders = sorted(base.iterdir())
        except OSError:
            logger.warning("skipping unreadable artifact tree %s", base, exc_info=True)
            continue
        for folder in folders:
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
            if not _artifact_md_is_readable(base, md):
                logger.warning("skipping artifact %s: a path component is a "
                               "symlink, or the source is over the %d byte cap",
                               md, ARTIFACT_MD_MAX_BYTES)
                continue
            try:
                text = md.read_text(encoding="utf-8")
                mtime = md.stat().st_mtime
            except (OSError, UnicodeDecodeError):
                continue
            fm, body = _artifact_frontmatter(text)
            images = _artifact_images(folder, data_root)
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


def _artifact_folder(data_root: Path, kind: str, slug: str) -> Path | None:
    """Resolve + validate the folder for (kind, slug). Returns Path or None."""
    subdir = {"post": "posts", "article": "articles"}.get(kind)
    if subdir is None or not slug or not _ARTIFACT_SLUG_RE.match(slug):
        return None
    base_raw = data_root / ARTIFACT_ROOT / subdir
    folder_raw = base_raw / slug
    base = base_raw.resolve()
    folder = folder_raw.resolve()
    try:
        folder.relative_to(base)
    except ValueError:
        return None
    # Containment alone lets a link that points back INSIDE the archive
    # through, and the workspace bans symlinks outright. The listing refuses
    # them (see `_artifact_md_is_readable`); a detail view that still served
    # one would leave the pair disagreeing about the same folder, which is the
    # asymmetry this module already learned once.
    if contains_symlink(base_raw, folder_raw):
        return None
    return folder if folder.is_dir() else None


def read_artifact(data_root: Path, kind: str, slug: str) -> dict:
    """Read one artifact - full markdown source + image list.

    Returns {ok: True, kind, slug, title, date, content, images} or
    {ok: False, error}.
    """
    folder = _artifact_folder(data_root, kind, slug)
    if folder is None:
        return {"ok": False, "error": "artifact not found"}
    md = folder / f"{slug}.md"
    if not md.is_file():
        mds = sorted(folder.glob("*.md"))
        if not mds:
            return {"ok": False, "error": "no markdown source"}
        md = mds[0]
    try:
        # `folder` came back from `_artifact_folder`, which now refuses a
        # linked component itself, so the remaining question here is the leaf.
        if not _artifact_md_is_readable(folder, md):
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
        "images": _artifact_images(folder, data_root),
    }


def resolve_artifact_image(data_root: Path, rel_path: str) -> Path | None:
    """Validate `rel_path` points at an image inside the LinkedIn archive.

    Returns the absolute Path to serve, or None on any validation
    failure (outside the archive, traversal, non-image, symlink, oversize).
    """
    if not rel_path or not isinstance(rel_path, str):
        return None
    # No prefix is stripped: a leading dot or slash means the caller did not
    # name a served file, and the check below is where that dies. See
    # `normalize_rel_path`.
    rel = normalize_rel_path(rel_path)
    if not rel.startswith(ARTIFACT_ROOT + "/"):
        return None
    parts = [p for p in rel.split("/") if p]
    if any(p == ".." or p.startswith(".") for p in parts):
        return None
    archive_root = (data_root / ARTIFACT_ROOT).resolve()
    target_raw = data_root / rel
    target = target_raw.resolve()
    try:
        target.relative_to(archive_root)
    except ValueError:
        return None
    if target.suffix.lower() not in ARTIFACT_IMAGE_EXTS:
        return None
    try:
        if contains_symlink(data_root / ARTIFACT_ROOT, target_raw) or not target.is_file():
            return None
        if target.stat().st_size > ARTIFACT_IMAGE_MAX_BYTES:
            return None
    except OSError:
        return None
    return target
