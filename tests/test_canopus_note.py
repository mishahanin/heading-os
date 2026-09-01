#!/usr/bin/env python3
"""Tests for the Canopus slice note, the one committed record per slice.

The note is the substrate every later clause reads, so its schema decides what
those clauses can check at all. Three properties matter more than the rest:

  * A digest, never a path. The engine repository is PUBLIC and the note is
    committed to it; the plan and scope documents it points at live in the
    private DATA overlay. `plan_digest` exists precisely so that path never has
    to be written down. Refusing a path-shaped VALUE is what keeps that true
    when a careless author pastes one into free prose instead.
  * Retirement is recorded, not inferred. The workflow in force DELETES the
    contract directory when a slice ships, so a checker that cannot tell
    "retired" from "moved" reports forever from the first shipped slice.
  * The committed notes are checked by the same code that writes them, so a
    hand-edited note is caught rather than trusted.
"""

import pathlib

import pytest

from scripts.utils.canopus_note import (
    NoteError,
    digest_text,
    note_paths,
    read_note,
    write_note,
)


def valid() -> dict:
    """A schema-satisfying note, synthetic throughout.

    The shas are ABBREVIATED refs, the convention this repository already uses
    and which `_SHA` in scripts/utils/canopus_note.py carries: a full
    40-character sha reads to detect-secrets as a hex high-entropy string, and
    every way to silence that is forbidden here. The digests are COMPUTED rather than pasted, for the
    same reason -- a literal 64-character hexdigest is the same tripwire.
    Built fresh per call so no test can mutate another's fixture.
    """
    return {
        "slug": "sample-slice",
        "value": "a refused manifest is not announced as a damaged one",
        "approval_sha": "1a2b3c4",
        "contract": "tests/contract/2026-01-02-sample-slice/",
        "plan_digest": digest_text("the plan document's content, not its path"),
        "scrutinize_plan": "clean",
        "scrutinize_built": "2 findings applied",
        "undo": "revert 1a2b3c4, restore the prior baseline, re-run the suite",
    }


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def test_a_note_round_trips(tmp_path):
    fields = {
        **valid(),
        "scope_digest": digest_text("the scope document's content"),
        "retired_sha": "9f8e7d6",
        "promoted_to": "tests/test_sample_slice.py",
        "body": "Free prose. Why the slice existed and what it cost.",
    }
    path = write_note(tmp_path, "sample-slice", fields)

    assert path == tmp_path / "records" / "slices" / "sample-slice.md"
    assert read_note(tmp_path, "sample-slice") == fields
    assert note_paths(tmp_path) == [path]


def test_a_note_missing_a_required_field_is_refused(tmp_path):
    with pytest.raises(NoteError):
        write_note(tmp_path, "s", {"slug": "s"})


def test_a_note_carrying_a_path_outside_the_engine_is_refused(tmp_path):
    """The engine is public and a note is committed to it.

    A DATA path in a note is a leak, and the digest field exists so the path
    never has to be written. The refusal is on the VALUE of any field, not on
    the digest fields alone, because `undo` and `value` are free prose that a
    careless author could paste a path into just as easily.
    """
    leaks = [
        ("plan_digest", "/home/someone/plans/x.md"),
        ("undo", "restore ~/plans/x.md then re-run"),
        ("value", r"see C:\Users\someone\plans\x.md"),
        ("scrutinize_plan", "clean, notes in ../.heading-os-data/plans/x.md"),
        ("body", "the plan lives at /var/data/plans/x.md"),
    ]
    for field, leaked in leaks:
        with pytest.raises(NoteError):
            write_note(tmp_path, "sample-slice", {**valid(), field: leaked})


def test_a_retired_note_without_a_promotion_target_is_refused(tmp_path):
    with pytest.raises(NoteError):
        write_note(tmp_path, "sample-slice", {**valid(), "retired_sha": "a" * 40})


def test_every_committed_note_satisfies_the_schema(tmp_path):
    """Runs over records/slices/ in the real tree, so a hand-edited note is caught.

    NOT empty since 2026-08-08; this said "Empty today, so the loop is vacuous
    today" long after two notes were committed under it, which is a false claim
    about the past that would send the next reader looking for a vacuous loop
    that is not there. The loop is live.

    No count FLOOR is asserted, and that is deliberate rather than an oversight:
    `canopus_check.main` treats a repository with no slice note as the ORDINARY
    state, so a floor here would fail a tree that is behaving correctly. The two
    guards that stand in for one are the directory existing (a deleted
    records/slices/ fails here rather than turning this test into a permanent
    no-op) and the validation the loop relies on being proved live in the same
    run by pushing a known-bad note through the identical code path.
    """
    root = _repo_root()
    assert (root / "records" / "slices").is_dir(), (
        "records/slices/ is missing, so this check would pass over nothing forever"
    )

    for path in note_paths(root):
        fields = read_note(root, path.stem)
        # Re-writing into a scratch root re-runs every schema rule against the
        # committed content, using only the module's public surface.
        write_note(tmp_path, path.stem, fields)

    with pytest.raises(NoteError):
        write_note(tmp_path, "sample-slice", {**valid(), "plan_digest": "/etc/plan.md"})


# ============================================================
# read_note answers with NoteError, or the whole check run dies
# ============================================================
#
# `scripts/canopus_check.py` takes `note_paths()` as its ENTIRE population and
# catches exactly `(NoteError, CheckError)` around the read. Anything else
# escaping `read_note` is not one unreadable note reported: it is the run
# aborting before the remaining notes are opened at all, which is the outcome
# `_unreadable`'s own docstring says the module exists to prevent.


def _plant(root: pathlib.Path, slug: str, raw: bytes) -> None:
    """Write a note's bytes directly, bypassing the writer's validation."""
    directory = root / "records" / "slices"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{slug}.md").write_bytes(raw)


def test_a_note_that_is_not_utf8_is_refused_rather_than_raising_a_decode_error(
    tmp_path,
):
    """MEASURED 2026-09-01: `UnicodeDecodeError` walked straight out.

    `read_note` reads with `encoding="utf-8"` and no `errors=`, under
    `except OSError`. A decode failure raises `UnicodeDecodeError`, which is a
    `ValueError` and a SIBLING of `OSError`, so the handler never saw it. Notes
    are written by hand in an editor, so a cp1251 byte in a `value:` line is the
    ordinary way to reach this, and the exception names a codec, a byte and an
    offset but NO path.

    Not `errors="replace"`: a note is a committed record whose fields are
    compared (`plan_digest`, `approval_sha`), and a value silently repaired with
    U+FFFD would be compared as if it were what the author wrote. Failing closed
    with the slug named is the safe direction.
    """
    _plant(tmp_path, "sample-slice",
           b"---\nslug: sample-slice\nvalue: caf\xe9 latte\n---\n")

    with pytest.raises(NoteError) as excinfo:
        read_note(tmp_path, "sample-slice")
    assert "sample-slice" in str(excinfo.value)


def test_a_note_whose_frontmatter_is_not_valid_yaml_is_refused(tmp_path):
    """`yaml.safe_load` on the block had no handler at all.

    `split_frontmatter` finds the fences and parses nothing, so a block that
    fences correctly and does not parse reaches `yaml.safe_load` unguarded and
    raises `yaml.YAMLError`, which is neither `NoteError` nor `CheckError`. An
    unclosed flow sequence is one keystroke away in a hand-edited note.
    """
    _plant(tmp_path, "sample-slice", b"---\nslug: [unclosed\n---\n")

    with pytest.raises(NoteError) as excinfo:
        read_note(tmp_path, "sample-slice")
    assert "sample-slice" in str(excinfo.value)


def test_a_note_carrying_a_field_the_schema_does_not_define_is_refused(tmp_path):
    """The allowlist half of the schema, which nothing exercised.

    Deleting the `unknown` branch left the whole suite green. The branch is not
    cosmetic: `write_note` serialises only `REQUIRED_FIELDS + OPTIONAL_FIELDS`,
    so a field accepted here is a field DROPPED on the way to disk, and the
    round-trip test would then be asserting over a note the caller did not ask
    for. Silently discarding a field of a committed record is worse than
    refusing it.
    """
    with pytest.raises(NoteError) as excinfo:
        write_note(tmp_path, "sample-slice", {**valid(), "notes": "a spare room"})
    assert "notes" in str(excinfo.value)


def test_a_note_field_that_is_not_text_is_refused(tmp_path):
    """A non-string value never meets `_LEAK`, so it is a hole in the leak wall.

    `_LEAK.search(value)` needs a string. The type check is what routes every
    value through the wall, and deleting it left the suite green. YAML supplies
    the shapes without anyone trying: `undo:` with nothing after the colon
    arrives as None, a bulleted `undo:` block arrives as a list, and a bare
    `approval_sha: 1a2b3c4` that happens to be all digits arrives as an int.
    A list is the one that matters, because its members are prose and a member
    can hold the private path this repository is public enough to care about.
    """
    for value in (None, ["restore /home/someone/plans/x.md"], 17, {"a": "b"}):
        with pytest.raises(NoteError) as excinfo:
            write_note(tmp_path, "sample-slice", {**valid(), "undo": value})
        assert "undo" in str(excinfo.value), value
