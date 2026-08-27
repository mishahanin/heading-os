#!/usr/bin/env python3
"""Thread file parsing and writing helpers."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from datetime import date, datetime
import re
import yaml
from scripts.utils.atomic import atomic_write_text
from scripts.utils.slugs import transliterate
from scripts.utils.workspace import get_default_tz

REQUIRED_FIELDS = (
    "id", "title", "status", "type", "classification",
    "opened", "last_touched", "links", "tags",
)
VALID_STATUSES = ("active", "on-hold", "closed")
VALID_TYPES = ("business", "personal")
# Every frontmatter key ThreadFile models explicitly. Anything else round-trips
# through `ThreadFile.extra` rather than being dropped on write.
MODELLED_FIELDS = frozenset({
    "id", "title", "status", "type", "classification", "opened", "last_touched",
    "counterparties", "links", "tags", "quiet_until", "do_not_remind",
})


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
    # ISO date up to and including which this thread must not be surfaced
    # proactively. Optional; absent on almost every thread.
    quiet_until: str | None = None
    # Indefinite freeze: quiet with no end date, lifted only when the operator
    # raises the subject themselves. Distinct from `quiet_until`, which expires.
    do_not_remind: bool = False
    # Frontmatter keys this dataclass does not model, carried verbatim so a
    # rewrite cannot silently delete them. `write_thread_file` used to rebuild
    # frontmatter from a fixed field list, which destroyed every hand-added key
    # on the next `/thread log` -- discovered 2026-08-12 when it ate a freeze
    # flag that had been sitting in a thread for six weeks.
    extra: dict[str, Any] = field(default_factory=dict)


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
        quiet_until=str(fm["quiet_until"]) if fm.get("quiet_until") else None,
        do_not_remind=bool(fm.get("do_not_remind")),
        extra={k: v for k, v in fm.items() if k not in MODELLED_FIELDS},
    )


def slugify(text: str) -> str:
    """Convert text to lowercase kebab-case suitable for filenames.

    Dots and whitespace are converted to hyphens (preserves '31c.io' -> '31c-io',
    not destructive '31cio'). Parens, punctuation, and other non-alphanumeric
    chars are stripped. Multiple hyphens collapse to one.

    Cyrillic is transliterated first, so a Russian title keeps its words instead
    of being erased. A MIXED title was the loss that showed: the live thread
    "Миграция CRM на новый сервер" would carry the id `crm`, five words
    in and one word out. `new_thread_path` still RAISES when the result is empty,
    which is the right answer for an id a person has to read.
    """
    text = transliterate(text).lower()
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
            f"title {title!r} slugifies to empty; provide a title with at least "
            f"one Latin or Cyrillic letter, or a digit",
        )
    # Belt-and-braces: `slugify` already strips parens, so this can only fire if
    # that changes. A paren in the stem breaks every markdown link that names the
    # thread path, in the thread's own `links:` and in anything that quotes it.
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
    # Emitted only when set, so the field never appears on the threads that have
    # no quiet period. It IS emitted here rather than dropped: the previous
    # fixed-field rebuild silently deleted any freeze flag on the next log.
    if thread.quiet_until:
        fm["quiet_until"] = thread.quiet_until
    if thread.do_not_remind:
        fm["do_not_remind"] = True
    # Unmodelled keys last, and never allowed to overwrite a modelled one.
    for key, value in thread.extra.items():
        if key not in fm:
            fm[key] = value
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = thread.body.lstrip("\n")
    atomic_write_text(path, f"---\n{fm_yaml}---\n\n{body}")


# ============================================================
# Quiet periods
# ============================================================

def is_quiet(thread: ThreadFile, today: date) -> bool:
    """Is this thread inside a deliberate quiet period on `today`?

    A quiet thread must not be surfaced proactively -- not in a session-opener
    rollup, /next, /dashboard, /weekly-review, or an ad-hoc "what is open"
    answer. An unset or unparseable `quiet_until` is NOT quiet: a broken date
    must fail toward surfacing the thread, never toward silencing it forever.
    `do_not_remind` is the dateless form, lifted only when the operator raises
    the subject themselves.
    """
    if thread.do_not_remind:
        return True
    if not thread.quiet_until:
        return False
    try:
        return today <= date.fromisoformat(thread.quiet_until)
    except (ValueError, TypeError):
        return False


# ============================================================
# The retired MEMORY.md index (removed 2026-08-27)
# ============================================================
#
# A `## Active Threads` block in the auto-memory index used to mirror every
# active thread, and fifteen names here maintained it: ACTIVE_THREADS_HEADER,
# ACTIVE_THREADS_HEADER_RE, ACTIVE_THREADS_MARKER, SUBSECTIONS,
# QUIET_PREFIX_RE, quiet_hook_prefix, ensure_active_threads_section,
# _index_block, _split_at_subheader, compose_thread_hook, add_thread_to_index,
# update_thread_hook, read_thread_hook, read_thread_quiet_marker,
# remove_thread_from_index.
#
# The block was retired on 2026-08-20 on the READER side only. `/prime` moved to
# `thread.py list --status active`, and `scripts/memory-hygiene.py` began
# reporting the block's own row shape as a defect, because every row quoted a
# live status and a live date and `.claude/rules/memory-discipline.md` forbids
# that in a file injected at every SessionStart. The writer here was never
# removed, so `/thread` kept regrowing rows that nothing read and that the
# workspace's own hygiene tool then flagged. Seven days later three rows were
# back.
#
# Removing the writer deletes two whole defect classes with it, both of which
# had already cost a shard each: a `log` on a closed thread silently
# resurrecting it in the index, and a `reopen` dropping the quiet marker. An
# index that does not exist cannot drift from the record.
#
# The record is the thread file. Read the live set with
# `python scripts/thread.py list`.


# ============================================================
# Archive Scanner
# ============================================================

@dataclass
class ArchiveCandidate:
    path: Path
    action: str  # "archive" | "propose-on-hold" | "quiet-expired"
    reason: str


def scan_for_archive(threads_root: Path, *, today: date | None = None) -> list[ArchiveCandidate]:
    """Find threads to archive (closed >90 days), propose on-hold for (active
    >60 days), or whose quiet period has run out.

    A thread inside its quiet period is skipped entirely: the 60-day staleness
    nudge is exactly the noise the quiet exists to suppress, and a deliberate
    pause reads as neglect to a date-only check. Once the quiet expires it is
    reported ONCE as `quiet-expired`, which is what closes the loop -- without
    it a freeze would outlive the condition it was set for.
    """
    today = today or datetime.now(get_default_tz()).date()
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
            if is_quiet(t, today):
                continue
            if t.quiet_until:
                candidates.append(ArchiveCandidate(
                    path=f, action="quiet-expired",
                    reason=f"quiet period ended {t.quiet_until}; clear it with "
                           f"`thread.py quiet {t.id} --clear`",
                ))
            try:
                last = date.fromisoformat(t.last_touched)
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
