#!/usr/bin/env python3
"""memory_touch.py — the single writer of auto-memory access_count.

Bumping is a RANKING signal and nothing else. A memory that is never bumped
sinks in recall order and stays on the shelf forever; nothing in this module,
or downstream of it, may treat a low count as grounds for removal.

Two callers:
  - scripts/memory-touch.py (CLI wrapper, one bump per invocation)
  - scripts/memory-index.py (`query --touch`, debounced to once per day)

Does a minimal, targeted text edit scoped to the frontmatter `metadata:` block:
increments `access_count` (inserting it at 1 if absent) and sets
`last_accessed` to the supplied date. Every other line — comments, key order,
unrelated fields, the whole body — is preserved byte-for-byte. NOT a full YAML
re-serialize. Writes atomically.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from scripts.utils.atomic import atomic_write_text
from scripts.utils.markdown import parse_frontmatter

# The closing fence may end the FILE, and the block between the fences may be
# empty. The old pattern was `^(---\s*\n)(.*?\n)(---\s*\n)`, which demanded a
# newline after the closing fence and at least one line between the fences.
# MEASURED 2026-08-30: the bytes `---\nmetadata:\n  access_count: 3\n---` (no
# trailing newline) and `---\n---\n` both raised
# `TouchError("no frontmatter block found")`, while `parse_frontmatter` - the
# other frontmatter reader in this very module, used by `touch_if_stale` two
# functions down - reads the same files without complaint. The two parsers
# disagreed about what a frontmatter block is, and `TouchError` is reserved for
# a file that has none at all.
FRONTMATTER_RE = re.compile(
    r"^(---[ \t]*\n)(.*?)^(---[ \t]*(?:\n|\Z))", re.DOTALL | re.MULTILINE)


class TouchError(ValueError):
    """Raised when a file cannot be touched (no frontmatter, bad path, etc.)."""


def _bump_frontmatter(text: str, today: str) -> tuple[str, int]:
    """Return (new_text, new_access_count).

    Locates the top-level `metadata:` block inside the frontmatter and bumps
    `access_count`/`last_accessed` within it (inserting either if absent),
    leaving every other line untouched. Raises TouchError if the file has no
    frontmatter block at all.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise TouchError("no frontmatter block found")
    open_marker, fm_body, close_marker = m.group(1), m.group(2), m.group(3)
    rest = text[m.end():]

    lines = fm_body.split("\n")
    meta_idx = None
    for i, line in enumerate(lines):
        if line.rstrip() == "metadata:":
            meta_idx = i
            break

    default_indent = "  "
    if meta_idx is None:
        # No metadata block at all (not expected on real auto-memory files,
        # but handled rather than crashing): append a fresh one.
        new_access_count = 1
        block = [
            "metadata:",
            f"{default_indent}access_count: {new_access_count}",
            f"{default_indent}last_accessed: {today}",
        ]
        if lines and lines[-1] == "":
            lines[-1:-1] = block
        else:
            lines.extend(block)
    else:
        block_end = meta_idx + 1
        while (
            block_end < len(lines)
            and lines[block_end].strip()
            and lines[block_end][0] in (" ", "\t")
        ):
            block_end += 1
        block_lines = lines[meta_idx + 1 : block_end]

        indent = default_indent
        if block_lines:
            im = re.match(r"^([ \t]+)", block_lines[0])
            if im:
                indent = im.group(1)

        found_access = found_last = False
        new_access_count = 1
        new_block_lines = []
        for line in block_lines:
            stripped = line.strip()
            if stripped.startswith("access_count:"):
                # An inline YAML comment is valid on this line, and this
                # module's docstring invites one by promising comments are kept
                # byte-for-byte. The parser handed the whole tail to `int()`,
                # so `access_count: 7  # bumped by cron` raised ValueError, the
                # except reset the count to 0, and the bump wrote 1 - a file
                # with real access history silently demoted to the bottom of
                # recall order, and the comment deleted on the way. MEASURED
                # 2026-08-30 on exactly that line: it came back
                # `access_count: 1`. The comment is split off, kept, and put
                # back; a quoted number is unquoted rather than discarded.
                raw_value = stripped.split(":", 1)[1]
                comment = ""
                cm = re.search(r"\s+#.*$", raw_value)
                if cm:
                    comment = cm.group(0)
                    raw_value = raw_value[:cm.start()]
                value = raw_value.strip().strip('"').strip("'")
                try:
                    current = int(value or 0)
                except ValueError:
                    current = 0
                new_access_count = current + 1
                new_block_lines.append(
                    f"{indent}access_count: {new_access_count}{comment}")
                found_access = True
            elif stripped.startswith("last_accessed:"):
                new_block_lines.append(f"{indent}last_accessed: {today}")
                found_last = True
            else:
                new_block_lines.append(line)
        if not found_access:
            new_block_lines.append(f"{indent}access_count: {new_access_count}")
        if not found_last:
            new_block_lines.append(f"{indent}last_accessed: {today}")

        lines[meta_idx + 1 : block_end] = new_block_lines

    new_fm_body = "\n".join(lines)
    new_text = open_marker + new_fm_body + close_marker + rest
    return new_text, new_access_count


def touch_file(raw_path: str, auto_memory_dir: Path, today: str) -> tuple[int, str]:
    """Touch one file. Returns (access_count, resolved_path_str) on success.

    Raises TouchError if the resolved path is outside auto_memory_dir, does
    not exist, or has no frontmatter.

    The file's atime/mtime are RESTORED after the write. `access_count` and
    `last_accessed` are access metadata, not a content edit, and mtime is a
    shared signal four consumers read as "when did the content last change":
    the SessionStart reconcile hook resolves canonical-vs-native conflicts
    newest-wins (a bumped mtime would let a bump silently revert a real edit
    made in the native store), dream-shadow gates dormancy on it,
    memory_health counts a file stale by it, and the nightly index build skips
    a file whose mtime is unchanged. `atomic_write_text` ends in `os.replace`,
    which stamps a new mtime, so the timestamps are captured before the write
    and put back after it.
    """
    resolved = _resolve(raw_path, auto_memory_dir)
    # BEFORE the read. `read_text` updates atime on a relatime mount, and the
    # stat used to run after it, so the atime "restored" was the post-read one
    # and every touch advanced atime despite the paragraph above saying it does
    # not. MEASURED 2026-08-30 on this workspace's filesystem: a file whose
    # atime was three days behind its mtime had atime moved to now by the read
    # alone.
    before = resolved.stat()
    text = resolved.read_text(encoding="utf-8")
    new_text, access_count = _bump_frontmatter(text, today)
    atomic_write_text(resolved, new_text)
    os.utime(resolved, (before.st_atime, before.st_mtime))
    return access_count, str(resolved)


def _resolve(raw_path: str, auto_memory_dir: Path) -> Path:
    """Resolve a bare filename or a data-root-relative path to a real file.

    The single resolver for both entry points, so they cannot drift apart on
    which paths they accept. memory-index.py emits the data-root-relative
    prefixed form ("auto-memory/x.md") and direct callers pass the bare form
    ("x.md"); both must land on the same file, and anything outside the
    auto-memory directory is refused.
    """
    candidate = Path(raw_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        direct = (auto_memory_dir / candidate).resolve()
        resolved = direct if direct.is_file() else (auto_memory_dir.parent / candidate).resolve()
    auto_memory_resolved = auto_memory_dir.resolve()
    try:
        resolved.relative_to(auto_memory_resolved)
    except ValueError:
        raise TouchError(
            f"{raw_path}: outside auto-memory directory ({auto_memory_resolved})"
        ) from None
    if not resolved.is_file():
        raise TouchError(f"{raw_path}: not found ({resolved})")
    return resolved


def touch_if_stale(raw_path: str, auto_memory_dir: Path, today: str) -> int | None:
    """Bump unless this file was already bumped today.

    Returns the new `access_count` when the file was written, or None when the
    same-day debounce declined to write. Callers that only need "did anything
    happen" test for None; callers that mirror the count elsewhere (the recall
    index) use the value, so the mirror can never invent one.

    The debounce exists because the retrieval hook runs on EVERY prompt: ten
    messages about one subject are one use of a memory, not ten. `last_accessed`
    is already written by the bump, so the debounce needs no new state.

    `today` must be an ISO date string in `YYYY-MM-DD` form, as produced by
    `datetime.date.isoformat()`. The staleness check is a plain string
    comparison against the stored `last_accessed` value, not a date parse; a
    differently formatted `today` will never match and the debounce fails
    open, bumping on every call.

    Read-modify-write, deliberately unlocked. Two hook runs racing on the same
    file can both read `n` and both write `n + 1`, losing one increment.
    `atomic_write_text` ends in `os.replace`, so a reader never sees a torn
    file and the only cost is an undercount on a log-scaled curve whose bonus
    barely moves near a single count. A lock on the read path of a hook with a
    hard timeout would cost more than the count it protects.
    """
    resolved = _resolve(raw_path, auto_memory_dir)
    meta, _ = parse_frontmatter(resolved.read_text(encoding="utf-8"))
    nested = meta.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    last = str(nested.get("last_accessed") or meta.get("last_accessed") or "").strip()
    if last == today:
        return None
    access_count, _resolved_str = touch_file(str(resolved), auto_memory_dir, today)
    return access_count
