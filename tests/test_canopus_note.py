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

    Empty today, so the loop is vacuous today -- but not vacuous forever, and
    not silently dead either. Two guards: the directory must exist (a deleted
    records/slices/ fails here rather than turning this test into a permanent
    no-op), and the validation the loop relies on is proved live in the same
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
