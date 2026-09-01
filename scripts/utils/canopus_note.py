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
from scripts.utils.markdown import FM_OK, split_frontmatter

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
#
# The POSIX alternative used to read `(?:^|\s)/\S`, which required whitespace or
# start-of-string before the slash -- so it did NOT fire on the way this
# repository actually writes a path in prose: inside backticks, in parentheses,
# after a colon, or as a markdown link target. Every one of those forms passed
# validation and would have been committed into this PUBLIC repository, while
# the three other alternatives matched anywhere as the comment above says. It is
# now anchored on what must NOT precede the slash instead:
#
#   `[\w]`  - so "3/4", "and/or" and "a/b test" stay prose, not paths.
#   `/`     - so the second slash of "http://" is not read as a fresh path.
#
# and the tail requires the slash to be followed by a real path shape: a segment
# then another separator, or a segment with a file extension. A bare `:` is NOT
# excluded, because "path:/home/x/plan.md" is exactly the leak this guard is
# for; "http://example.com" is stopped by the tail instead (its first slash is
# followed by another slash, which no path segment can start with).
#
# That last exclusion is what the `file://` alternative pays for. MEASURED
# 2026-09-01: `file:///home/operator/private/plan.md` passed validation, because
# the POSIX branch refuses a slash preceded by a slash and the third slash of a
# file URL is exactly that. A markdown link to a local document is an ordinary
# way to write a path down, and this repository is public. `file://` is never a
# public web address, so the alternative costs no false positive that
# `https://` does not already avoid.
#
# The parent-directory escape reads `[\\/]`, not `/`, because the comment above
# says "a parent-directory escape" while the pattern knew only the POSIX
# spelling: `..\up\one` walked through. `(?<!\.)` keeps an ellipsis followed by
# a backslash from reading as one.
_LEAK = re.compile(
    r"(?<![\w/])/[\w.-]+(?:/|\.[A-Za-z0-9]{1,8}\b)"
    r"|file://|~/|\b[A-Za-z]:[\\/]|(?<!\.)\.\.[\\/]|" + re.escape(DATA_OVERLAY_DIR)
)
# Abbreviated refs are accepted, and this pattern IS the repository's
# convention for a sha written into a file: a full 40-character sha reads to
# detect-secrets as a hex high-entropy string, and every way to silence that
# is forbidden here. (It used to cite config/canopus-genesis.json as the
# precedent; that file was deleted on 2026-08-07 with the commit-walking
# check that was its only reader, so the rule lives here now.)
_SHA = re.compile(r"[0-9a-f]{7,40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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
    # A slug is one filename, not a path. `write_note(root, "sub/hidden", ...)`
    # wrote records/slices/sub/hidden.md and returned it, but `note_paths` globs
    # `*.md` non-recursively, so the note was invisible to the only enumerator:
    # `scripts/canopus_check.py` takes note_paths() as its ENTIRE population and
    # would print "N clause(s) over M note(s); 0 report(s)" and exit 0 over a
    # note it never opened. Refusing the shape is cheaper than making every
    # reader recursive.
    if "/" in slug or "\\" in slug or slug in ("", ".", ".."):
        raise NoteError(
            f"note slug {slug!r} is not a single file name. A slug with a path "
            "separator writes a note that note_paths() does not enumerate, so no "
            "clause would ever check it."
        )
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
    # `(OSError, ValueError)`, not `OSError` alone. MEASURED 2026-09-01 on a note
    # carrying a lone 0xe9: `UnicodeDecodeError` is a `ValueError` and a SIBLING
    # of `OSError`, so it walked past this handler and out of the function.
    # `canopus_check.main` takes `note_paths()` as its ENTIRE population and
    # catches exactly `(NoteError, CheckError)`, so one hand-edited note in the
    # wrong encoding did not get reported: it ENDED the check for every other
    # note, on a traceback naming a codec, a byte and an offset but no path.
    #
    # Not `errors="replace"`. A note is a committed record whose fields are
    # compared (`plan_digest`, `approval_sha`), and a value silently repaired
    # with U+FFFD would be compared as though it were what the author wrote.
    # Failing closed with the slug named is the safe direction, and it is what
    # `census_oracles._read` already does for the same reason.
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise NoteError(f"note {slug!r} is unreadable: {exc}") from exc
    # The fences come from the shared splitter. `_FENCE` accepted trailing
    # whitespace on the CLOSING fence and not on the OPENING one, which is the
    # kind of asymmetry no reader of the pattern would predict. MEASURED
    # 2026-08-29: a note opening `--- ` or `---\t` raised "has no '---'
    # frontmatter fence" on a note whose YAML is fine, and `canopus_check.py`
    # takes `note_paths()` as its ENTIRE population, so one such note aborts the
    # check for every clause in the set.
    block, body, kind = split_frontmatter(text)
    if block is None or kind != FM_OK:
        raise NoteError(f"note {slug!r} has no '---' frontmatter fence")
    # `split_frontmatter` finds the FENCES and parses nothing, so a block that
    # fences correctly and does not parse arrives here. This call had no handler
    # at all, and `yaml.YAMLError` is neither `NoteError` nor `CheckError`, so it
    # took the whole `canopus_check` run down exactly as the decode above did.
    # An unclosed flow sequence (`slug: [unclosed`) is one keystroke away in a
    # note that is written by hand.
    try:
        fields = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise NoteError(
            f"note {slug!r} has frontmatter that is not valid YAML: {exc}"
        ) from exc
    if not isinstance(fields, dict):
        raise NoteError(f"note {slug!r} has frontmatter that is not a mapping")
    body = body.strip()
    if body:
        fields[BODY_FIELD] = body
    return fields
