#!/usr/bin/env python3
"""Thread file parsing and writing helpers."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from types import MappingProxyType
from datetime import date, datetime
import re
import yaml
from scripts.utils.atomic import atomic_write_text

REQUIRED_FIELDS = (
    "id", "title", "status", "type", "classification",
    "opened", "last_touched", "links", "tags",
)
VALID_STATUSES = ("active", "on-hold", "closed")
VALID_TYPES = ("business", "personal")


@dataclass
class ThreadFile:
    id: str
    title: str
    status: str
    type: str
    classification: str
    opened: str
    last_touched: str
    counterparties: list[str] = field(default_factory=list)
    links: dict[str, list[str]] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None


def parse_thread_file(path: Path) -> ThreadFile:
    """Parse a thread markdown file into a ThreadFile dataclass."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    _, frontmatter_raw, body = text.split("---\n", 2)
    fm: dict[str, Any] = yaml.safe_load(frontmatter_raw) or {}

    for required in REQUIRED_FIELDS:
        if required not in fm:
            raise ValueError(f"{path}: missing required field '{required}'")
    if fm["status"] not in VALID_STATUSES:
        raise ValueError(f"{path}: invalid status '{fm['status']}'")
    if fm["type"] not in VALID_TYPES:
        raise ValueError(f"{path}: invalid type '{fm['type']}'")
    if fm["id"] != path.stem:
        raise ValueError(
            f"{path}: id {fm['id']!r} does not match filename stem {path.stem!r}",
        )

    # Normalize links: guarantee all four sub-keys exist, even if frontmatter is partial.
    # Defensive copy avoids mutating the YAML-loaded dict in place.
    links = dict(fm.get("links") or {})
    for key in ("crm", "pipeline", "outputs", "knowledge"):
        links.setdefault(key, [])

    return ThreadFile(
        id=fm["id"],
        title=fm["title"],
        status=fm["status"],
        type=fm["type"],
        classification=fm["classification"],
        opened=str(fm["opened"]),
        last_touched=str(fm["last_touched"]),
        counterparties=fm.get("counterparties") or [],
        links=links,
        tags=fm.get("tags") or [],
        body=body.lstrip("\n"),
        path=path,
    )


def slugify(text: str) -> str:
    """Convert text to lowercase kebab-case suitable for filenames.

    Dots and whitespace are converted to hyphens (preserves '31c.io' -> '31c-io',
    not destructive '31cio'). Parens, punctuation, and other non-alphanumeric
    chars are stripped. Multiple hyphens collapse to one.
    """
    text = text.lower()
    # Step 1: dots and whitespace -> hyphen
    text = re.sub(r"[.\s]+", "-", text)
    # Step 2: strip everything that isn't alphanumeric or hyphen
    text = re.sub(r"[^a-z0-9-]", "", text)
    # Step 3: collapse multi-hyphens
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def new_thread_path(threads_root: Path, type_: str, title: str, date: str) -> Path:
    """Build the canonical path for a new thread file."""
    if type_ not in VALID_TYPES:
        raise ValueError(f"invalid type '{type_}'")
    slug = slugify(title)
    if not slug:
        raise ValueError(
            f"title {title!r} slugifies to empty; provide a title with at least one ASCII alphanumeric character",
        )
    # Defensive invariant: paths stored in MEMORY.md regex must not contain parens.
    if "(" in slug or ")" in slug:
        raise ValueError(f"slug must not contain parens: {slug!r}")
    return threads_root / type_ / f"{date}-{slug}.md"


def write_thread_file(path: Path, thread: ThreadFile) -> None:
    """Write a ThreadFile back to disk with frontmatter + body."""
    fm = {
        "id": thread.id,
        "title": thread.title,
        "status": thread.status,
        "type": thread.type,
        "classification": thread.classification,
        "opened": thread.opened,
        "last_touched": thread.last_touched,
        "counterparties": thread.counterparties,
        "links": thread.links,
        "tags": thread.tags,
    }
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = thread.body.lstrip("\n")
    atomic_write_text(path, f"---\n{fm_yaml}---\n\n{body}")


# ============================================================
# MEMORY.md Index Manager
# ============================================================

ACTIVE_THREADS_HEADER = "## Active Threads"
ACTIVE_THREADS_HEADER_RE = re.compile(r"^## Active Threads$", re.MULTILINE)
ACTIVE_THREADS_MARKER = "<!-- managed-by: /thread - do not edit by hand; /dream skips this section -->"
SUBSECTIONS = MappingProxyType({"business": "### Business", "personal": "### Personal (CEO-ONLY)"})


def ensure_active_threads_section(memory_md: Path) -> None:
    """Append the ## Active Threads scaffold if MEMORY.md doesn't have it."""
    text = memory_md.read_text(encoding="utf-8") if memory_md.exists() else ""
    if ACTIVE_THREADS_HEADER_RE.search(text):
        return
    block = (
        f"\n{ACTIVE_THREADS_HEADER}\n{ACTIVE_THREADS_MARKER}\n\n"
        f"### Business\n\n"
        f"### Personal (CEO-ONLY)\n"
    )
    atomic_write_text(memory_md, text.rstrip() + block)


def _index_block(memory_md: Path) -> tuple[str, str, str]:
    """Return (before, threads_block, after) split around ## Active Threads.

    Uses line-anchored regex split so that future MEMORY.md additions like
    `## Active Threads History` (an H2 with a different text) do not collide
    with the substring match on the bare header.
    """
    text = memory_md.read_text(encoding="utf-8")
    if not ACTIVE_THREADS_HEADER_RE.search(text):
        raise ValueError("## Active Threads section not initialised; call ensure_active_threads_section first")
    parts = re.split(r"^## Active Threads$\n", text, maxsplit=1, flags=re.MULTILINE)
    before, rest = parts[0], parts[1]
    next_h2 = re.search(r"^## ", rest, flags=re.MULTILINE)
    if next_h2:
        threads_block = ACTIVE_THREADS_HEADER + "\n" + rest[: next_h2.start()]
        after = rest[next_h2.start():]
    else:
        threads_block = ACTIVE_THREADS_HEADER + "\n" + rest
        after = ""
    return before, threads_block, after


def _split_at_subheader(block: str, sub_header: str) -> tuple[str, str]:
    """Line-anchored split on a level-3 sub-header.

    Avoids substring-collision with body text that happens to contain
    '### Business' inside a hook or title.
    """
    pattern = re.compile(rf"^{re.escape(sub_header)}$", re.MULTILINE)
    m = pattern.search(block)
    if not m:
        raise ValueError(f"sub-section '{sub_header}' missing; section corrupted")
    return block[: m.end()], block[m.end():]


def add_thread_to_index(memory_md: Path, *, type_: str, title: str, path: str, hook: str) -> None:
    """Append a thread line under ### Business or ### Personal (CEO-ONLY)."""
    if "\n" in hook or "\r" in hook:
        raise ValueError("hook must not contain newlines or carriage returns")
    if type_ not in SUBSECTIONS:
        raise ValueError(f"invalid type '{type_}'")
    before, block, after = _index_block(memory_md)
    sub_header = SUBSECTIONS[type_]
    line = f"- [{title}]({path}) - {hook}\n"
    head_with_subheader, tail = _split_at_subheader(block, sub_header)
    # Append after the sub-header (and after any existing entries)
    next_sub = re.search(r"^### ", tail, flags=re.MULTILINE)
    if next_sub:
        existing = tail[: next_sub.start()].rstrip("\n") + "\n"
        rest_of_block = tail[next_sub.start():]
    else:
        existing = tail.rstrip("\n") + "\n"
        rest_of_block = ""
    new_block = head_with_subheader + existing + line + rest_of_block
    atomic_write_text(memory_md, before + new_block + after)


def update_thread_hook(memory_md: Path, *, path: str, hook: str) -> None:
    """Replace the hook text on the line whose link target matches `path`."""
    if "\n" in hook or "\r" in hook:
        raise ValueError("hook must not contain newlines or carriage returns")
    before, block, after = _index_block(memory_md)
    pattern = re.compile(rf"(- \[[^\]]+\]\({re.escape(path)}\)) - [^\n]*", re.MULTILINE)
    new_block, n = pattern.subn(rf"\1 - {hook}", block)
    if n == 0:
        raise ValueError(f"no thread line found for path '{path}'")
    atomic_write_text(memory_md, before + new_block + after)


def remove_thread_from_index(memory_md: Path, *, path: str) -> None:
    """Drop the line whose link target matches `path`."""
    before, block, after = _index_block(memory_md)
    pattern = re.compile(rf"- \[[^\]]+\]\({re.escape(path)}\) - [^\n]*(?:\n|$)", re.MULTILINE)
    new_block, n = pattern.subn("", block)
    if n == 0:
        raise ValueError(f"no thread line found for path '{path}'")
    atomic_write_text(memory_md, before + new_block + after)


# ============================================================
# Archive Scanner
# ============================================================

@dataclass
class ArchiveCandidate:
    path: Path
    action: str  # "archive" | "propose-on-hold"
    reason: str


def scan_for_archive(threads_root: Path, *, today: date | None = None) -> list[ArchiveCandidate]:
    """Find threads to archive (closed >90 days) or propose on-hold for (active >60 days)."""
    today = today or date.today()
    candidates: list[ArchiveCandidate] = []
    for type_ in ("business", "personal"):
        type_dir = threads_root / type_
        if not type_dir.is_dir():
            continue
        for f in type_dir.glob("*.md"):
            try:
                t = parse_thread_file(f)
            except (ValueError, yaml.YAMLError):
                continue
            try:
                last = datetime.strptime(t.last_touched, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            age = (today - last).days
            if t.status == "closed" and age > 90:
                candidates.append(ArchiveCandidate(
                    path=f, action="archive",
                    reason=f"closed {age} days, threshold 90",
                ))
            elif t.status == "active" and age > 60:
                candidates.append(ArchiveCandidate(
                    path=f, action="propose-on-hold",
                    reason=f"active but no touch for {age} days, threshold 60",
                ))
    return candidates
