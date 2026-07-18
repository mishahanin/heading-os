#!/usr/bin/env python3
"""memory-touch.py -- bump auto-memory access_count/last_accessed (Gap #2).

Usage:
    python scripts/memory-touch.py <path> [<path> ...]

Each <path> may be relative to the auto-memory directory (get_auto_memory_dir())
or absolute. Refuses any path that does not resolve inside that directory.

Does a minimal, targeted text edit scoped to the frontmatter `metadata:` block:
increments `access_count` (inserting it at 1 if absent) and sets
`last_accessed` to today's date (get_default_tz()). Every other line --
comments, key order, unrelated fields, the whole body -- is preserved
byte-for-byte. NOT a full YAML re-serialize.

Writes atomically (tempfile + os.replace(), scripts.utils.atomic).

Consumed by:
  - .claude/skills/recall/SKILL.md (Phase 1, one touch per cited memory-layer hit)
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.atomic import atomic_write_text
from scripts.utils.colors import GRAY, GREEN, RED, RESET
from scripts.utils.workspace import get_auto_memory_dir, get_default_tz

FRONTMATTER_RE = re.compile(r"^(---\s*\n)(.*?\n)(---\s*\n)", re.DOTALL)


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
                try:
                    current = int(stripped.split(":", 1)[1].strip() or 0)
                except ValueError:
                    current = 0
                new_access_count = current + 1
                new_block_lines.append(f"{indent}access_count: {new_access_count}")
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
    """
    candidate = Path(raw_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        # memory-index.py's JSON `path` for a memory-layer hit is data-root-
        # relative and already carries the "auto-memory/" prefix (the form
        # /recall actually passes); a bare filename is also accepted for direct
        # or manual use. Try the direct join under auto_memory_dir first, then
        # fall back to the parent (data-root) join so the prefixed form resolves
        # to the same file instead of a doubled auto-memory/auto-memory/ path.
        direct = (auto_memory_dir / candidate).resolve()
        resolved = direct if direct.is_file() else (auto_memory_dir.parent / candidate).resolve()
    auto_memory_resolved = auto_memory_dir.resolve()
    try:
        resolved.relative_to(auto_memory_resolved)
    except ValueError:
        raise TouchError(f"{raw_path}: outside auto-memory directory ({auto_memory_resolved})") from None
    if not resolved.is_file():
        raise TouchError(f"{raw_path}: not found ({resolved})")

    text = resolved.read_text(encoding="utf-8")
    new_text, access_count = _bump_frontmatter(text, today)
    atomic_write_text(resolved, new_text)
    return access_count, str(resolved)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump auto-memory access_count/last_accessed")
    ap.add_argument("paths", nargs="+", help="auto-memory file path(s), relative or absolute")
    args = ap.parse_args()

    auto_memory_dir = get_auto_memory_dir()
    today = datetime.datetime.now(get_default_tz()).date().isoformat()

    exit_code = 0
    for raw_path in args.paths:
        try:
            access_count, resolved = touch_file(raw_path, auto_memory_dir, today)
        except TouchError as exc:
            sys.stderr.write(f"{RED}refused:{RESET} {exc}\n")
            exit_code = 1
            continue
        print(
            f"{GREEN}touched{RESET} {resolved} "
            f"{GRAY}access_count={access_count} last_accessed={today}{RESET}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
