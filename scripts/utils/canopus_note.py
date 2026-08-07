#!/usr/bin/env python3
"""The Canopus slice note: one committed markdown file per slice.

A note is `records/slices/{slug}.md` -- a YAML frontmatter block between `---`
fences, then free prose. It is the record every later clause reads, so the
schema here decides what those clauses can check at all.

Two rules carry the design and neither is a style preference.

**A digest, never a path.** This engine repository is PUBLIC, and the note is
committed to it, while the plan and scope documents the note refers to live in
the private DATA overlay. `plan_digest` and `scope_digest` exist precisely so
that path never has to be written down: they pin the document's CONTENT, which
is what a later check actually wants to compare. So every value is refused when
it looks like a path -- not the digest fields alone, because `undo` and `value`
are free prose that a careless author could paste one into just as easily.

**Retirement is recorded, not inferred.** The workflow in force DELETES the
contract directory when a slice ships. A checker that cannot tell "retired"
from "moved" reads that deletion as the contract going missing and reports
against it forever, from the first shipped slice onward, until the signal is
ignored. `retired_sha` names the commit that removed the contract and
`promoted_to` names where its coverage went; setting the first without the
second is refused, because a retirement pointing nowhere cannot be told apart
from a contract that was simply dropped.

Usage:
    from scripts.utils.canopus_note import write_note, read_note, note_paths
    write_note(root, "foreign-recipe", {...})   # -> records/slices/foreign-recipe.md
    fields = read_note(root, "foreign-recipe")
    for path in note_paths(root): ...           # sorted, so callers are deterministic
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from scripts.utils.atomic import atomic_write_text

NOTE_DIR = Path("records") / "slices"
DATA_OVERLAY_DIR = ".heading-os-data"

REQUIRED_FIELDS = (
    "slug",
    "value",
    "approval_sha",
    "contract",
    "plan_digest",
    "scrutinize_plan",
    "scrutinize_built",
    "undo",
)
OPTIONAL_FIELDS = ("scope_digest", "retired_sha", "promoted_to")
BODY_FIELD = "body"

# A path-shaped value, matched ANYWHERE in the string rather than only at its
# start, because the leak that matters is a path pasted mid-sentence into free
# prose. The alternatives, in order: a POSIX absolute path token, a
# home-relative path, a Windows drive path (`\b` so "http://" is not read as
# one), a parent-directory escape, and the DATA overlay's directory name.
_LEAK = re.compile(
    r"(?:^|\s)/\S|~/|\b[A-Za-z]:[\\/]|\.\./|" + re.escape(DATA_OVERLAY_DIR)
)
# Abbreviated refs are accepted, and this pattern IS the repository's
# convention for a sha written into a file: a full 40-character sha reads to
# detect-secrets as a hex high-entropy string, and every way to silence that
# is forbidden here. (It used to cite config/canopus-genesis.json as the
# precedent; that file was deleted on 2026-08-07 with the commit-walking
# check that was its only reader, so the rule lives here now.)
_SHA = re.compile(r"[0-9a-f]{7,40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_FENCE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?\Z", re.S)


class NoteError(ValueError):
    """A note that does not satisfy the schema, refused rather than written."""


def digest_text(text: str) -> str:
    """Return the content digest of *text*, algorithm-prefixed.

    The prefix stays so a reader of a committed note can tell what produced the
    hex, and so the format can change later without two algorithms' digests
    being silently compared as equal.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def note_paths(root: Path) -> list[Path]:
    """Return every note under *root*, sorted, so callers are deterministic."""
    return sorted((Path(root) / NOTE_DIR).glob("*.md"))


def _validate(slug: str, fields: dict, body: str) -> None:
    """Raise NoteError unless *fields* satisfies the schema (see module docstring)."""
    unknown = sorted(set(fields) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise NoteError(f"note {slug!r} carries unknown field(s): {', '.join(unknown)}")
    missing = [f for f in REQUIRED_FIELDS if not str(fields.get(f, "")).strip()]
    if missing:
        raise NoteError(f"note {slug!r} is missing required field(s): {', '.join(missing)}")
    if fields["slug"] != slug:
        raise NoteError(f"note {slug!r} carries slug {fields['slug']!r}")

    for name, value in list(fields.items()) + [(BODY_FIELD, body)]:
        if not isinstance(value, str):
            raise NoteError(
                f"note {slug!r} field {name!r} is {type(value).__name__}, not text"
            )
        if _LEAK.search(value):
            raise NoteError(
                f"note {slug!r} field {name!r} carries a path. This repository is "
                "public and the note is committed to it: record a digest_text() "
                "digest of the document's content instead of its location."
            )

    for name in ("approval_sha", "retired_sha"):
        if fields.get(name) and not _SHA.fullmatch(fields[name]):
            raise NoteError(f"note {slug!r} field {name!r} is not a git sha: {fields[name]!r}")
    for name in ("plan_digest", "scope_digest"):
        if fields.get(name) and not _DIGEST.fullmatch(fields[name]):
            raise NoteError(
                f"note {slug!r} field {name!r} is not a digest_text() digest: {fields[name]!r}"
            )
    if fields.get("retired_sha") and not fields.get("promoted_to"):
        raise NoteError(
            f"note {slug!r} sets retired_sha without promoted_to. A retirement that "
            "names no promotion target cannot be told apart from a dropped contract."
        )


def write_note(root: Path, slug: str, fields: dict) -> Path:
    """Validate *fields* and write `records/slices/{slug}.md` atomically.

    *fields* may carry `body`, the free prose written after the closing fence;
    it is validated like every other value. Returns the written path.
    """
    fields = dict(fields)
    body = fields.pop(BODY_FIELD, "")
    _validate(slug, fields, body)
    ordered = {k: fields[k] for k in REQUIRED_FIELDS + OPTIONAL_FIELDS if k in fields}
    # width: a wrapped scalar survives safe_load but stops the file being
    # greppable by field value, which is how a human reads one of these.
    front = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=1000)
    text = f"---\n{front}---\n" + (f"\n{body}\n" if body else "")
    path = Path(root) / NOTE_DIR / f"{slug}.md"
    atomic_write_text(path, text)
    return path


def read_note(root: Path, slug: str) -> dict:
    """Return the frontmatter of `records/slices/{slug}.md`, plus `body` when present."""
    path = Path(root) / NOTE_DIR / f"{slug}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NoteError(f"note {slug!r} is unreadable: {exc}") from exc
    match = _FENCE.match(text)
    if match is None:
        raise NoteError(f"note {slug!r} has no '---' frontmatter fence")
    fields = yaml.safe_load(match.group(1))
    if not isinstance(fields, dict):
        raise NoteError(f"note {slug!r} has frontmatter that is not a mapping")
    body = (match.group(2) or "").strip()
    if body:
        fields[BODY_FIELD] = body
    return fields
