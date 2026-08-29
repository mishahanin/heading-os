"""An orphan memory file that hid inside a longer name, and one that hid behind a path.

`memory_health.compute_memory_defects` answered "is this fact file referenced
from MEMORY.md?" with `p.name not in content` - a substring test against the
whole index text. A substring test cannot see where a name begins or ends, and
two ordinary inputs walked straight through it:

* **Name nesting.** With `harbour-lantern-ledger.md` linked from the index and
  `lantern-ledger.md` not linked at all, the shorter file was reported as
  referenced forever, because its name sits inside its neighbour's. Adding a
  memory whose name ends with an existing one silently retires the older file
  from the orphan report.
* **Path-qualified pointers.** The index used to carry pointers to records under
  subdirectories, `](threads/business/drop.md)` among them. A bare memory file
  `drop.md` was then permanently "referenced" by a pointer that names a
  different record entirely.

Both failures are silent in the expensive direction: the orphan count reads
zero, `/memory-hygiene` prints "none", and the operator believes an index nobody
checked. The correct rule already existed one module over, in
`memory_expiry.strip_index_pointers`, which matches the exact `](<name>)` link
target and says so in its docstring. `compute_memory_defects` is the copy where
it was never applied; it now reads the shared `index_link_targets`.

The fixtures here use real `[title](file.md)` pointers, the shape the live
MEMORY.md is written in, grouped several to a line with ` · ` separators.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.memory_expiry import (  # noqa: E402
    index_link_targets,
    strip_index_pointers,
)
from scripts.utils.memory_health import compute_memory_defects  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: one index, five fact files, two of them deliberately unreferenced.
# ---------------------------------------------------------------------------

# A file that IS linked, a file whose name NESTS inside it, a file reached only
# through a path-qualified pointer, a plainly-linked file, and a plain orphan.
LINKED_LONG_NAME = "harbour-lantern-ledger.md"
NESTED_ORPHAN = "lantern-ledger.md"
PATH_SHADOWED_ORPHAN = "drop.md"
PLAINLY_LINKED = "tin-whistle-inventory.md"
PLAIN_ORPHAN = "gravel-yard-permit.md"

INDEX = (
    "# Memory index\n"
    "\n"
    "- Ledgers: [harbour lantern ledger](harbour-lantern-ledger.md) · "
    "[tin whistle inventory](tin-whistle-inventory.md)\n"
    "- Threads: [the drop thread](threads/business/drop.md)\n"
)


def _corpus(tmp_path: Path, names, index: str = INDEX) -> Path:
    """Write the named fact files plus an index, and return the directory."""
    for name in names:
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(index, encoding="utf-8")
    return tmp_path


ALL_FILES = (
    LINKED_LONG_NAME,
    NESTED_ORPHAN,
    PATH_SHADOWED_ORPHAN,
    PLAINLY_LINKED,
    PLAIN_ORPHAN,
)


# ---------------------------------------------------------------------------
# The two inputs that defeated the substring test
# ---------------------------------------------------------------------------

def test_a_file_whose_name_nests_inside_a_linked_name_is_still_an_orphan(tmp_path):
    d = _corpus(tmp_path, ALL_FILES)

    orphans = compute_memory_defects(d)["orphans"]

    assert NESTED_ORPHAN in orphans, (
        f"{NESTED_ORPHAN} is linked from nowhere; only the longer "
        f"{LINKED_LONG_NAME} is, and its name merely contains the shorter one"
    )


def test_a_pointer_to_a_subdirectory_record_does_not_reference_the_bare_file(tmp_path):
    d = _corpus(tmp_path, ALL_FILES)

    orphans = compute_memory_defects(d)["orphans"]

    assert PATH_SHADOWED_ORPHAN in orphans, (
        "](threads/business/drop.md) names a thread record, not the memory file "
        "drop.md, so it cannot be what references it"
    )


# ---------------------------------------------------------------------------
# The rule still has to answer the ordinary questions correctly
# ---------------------------------------------------------------------------

def test_a_file_with_no_pointer_at_all_is_reported(tmp_path):
    d = _corpus(tmp_path, ALL_FILES)

    assert PLAIN_ORPHAN in compute_memory_defects(d)["orphans"]


def test_a_genuinely_linked_file_is_never_reported(tmp_path):
    d = _corpus(tmp_path, ALL_FILES)

    orphans = compute_memory_defects(d)["orphans"]

    assert PLAINLY_LINKED not in orphans
    assert LINKED_LONG_NAME not in orphans


def test_the_whole_verdict_over_a_grouped_pointer_index_is_exact(tmp_path):
    """Anti-vacuity: the corpus holds files on BOTH sides of the question, and
    the test names every one of them. A rule that reported everything, or
    nothing, fails this."""
    d = _corpus(tmp_path, ALL_FILES)

    orphans = sorted(compute_memory_defects(d)["orphans"])

    assert orphans == sorted([NESTED_ORPHAN, PATH_SHADOWED_ORPHAN, PLAIN_ORPHAN])
    assert 0 < len(orphans) < len(ALL_FILES), (
        "the fixture must contain at least one orphan and at least one "
        "referenced file, or these assertions measure nothing"
    )
    assert sorted(index_link_targets(INDEX)) == [
        "harbour-lantern-ledger.md",
        "threads/business/drop.md",
        "tin-whistle-inventory.md",
    ]


def test_the_index_pointers_are_the_shape_the_live_index_uses(tmp_path):
    """The fixture would be worthless written as bare bullets: `- name.md`
    passes a substring test by construction, so it could never have caught the
    defect. Pin the shape."""
    assert "](harbour-lantern-ledger.md)" in INDEX
    assert " · " in INDEX
    assert "\n- harbour-lantern-ledger.md" not in INDEX


# ---------------------------------------------------------------------------
# The empty corpus must not make the assertions vacuous
# ---------------------------------------------------------------------------

def test_an_index_with_no_fact_files_reports_no_orphans_but_the_index_was_read(tmp_path):
    """Zero orphans over an empty corpus is the answer a broken check also
    gives, so assert the reasons: the directory was readable, the index was
    read, and the file count is the count of MEMORY.md alone."""
    (tmp_path / "MEMORY.md").write_text(INDEX, encoding="utf-8")

    result = compute_memory_defects(tmp_path)

    assert result["orphans"] == []
    assert result["status"] == "ok"
    assert result["index_readable"] is True
    assert result["index_problem"] == ""
    assert result["file_count"] == 1


def test_an_empty_index_makes_every_present_file_an_orphan(tmp_path):
    """The other end of the empty case: a corpus with files and an index that
    points at nothing must report all of them, so an empty result can never be
    mistaken for a clean one."""
    d = _corpus(tmp_path, ALL_FILES, index="# Memory index\n\nnothing here yet\n")

    assert sorted(compute_memory_defects(d)["orphans"]) == sorted(ALL_FILES)


# ---------------------------------------------------------------------------
# Two links on one index line, joined by anything but the middot separator
# ---------------------------------------------------------------------------

def test_a_second_link_on_the_same_line_is_still_a_reference():
    """The removal pattern eats the trailing note up to the next `·`, so on a
    line that joins two links with `;` it swallowed the second one and the file
    it points at was reported as an orphan.

    Measured 2026-08-29 against the operator's live index, which carries the
    line `Address him as [...](a.md); he [calls me Mimir](b.md)`: the strict
    rule reported exactly one orphan, and the index points straight at it. A
    reader of the index uses the LINK grammar; only the rewriter needs the
    trailing run.
    """
    line = ("- Address him as [Misha](address-user-as-misha.md); "
            "he [calls me Mimir](calls-me-mimir.md)")
    assert index_link_targets(line) == {"address-user-as-misha.md",
                                        "calls-me-mimir.md"}


@pytest.mark.parametrize("separator", ["; ", ", ", " and ", " -- ", " · "])
def test_links_are_found_whatever_joins_them(separator):
    line = f"- Group: [one](first.md){separator}[two](second.md)"
    assert index_link_targets(line) == {"first.md", "second.md"}


def test_a_semicolon_joined_line_reports_neither_file_as_an_orphan(tmp_path):
    """End to end, not just the matcher: the defect was visible in the report."""
    index = ("# Memory index\n\n"
             "- Address him as [Misha](first.md); he [calls me Mimir](second.md)\n")
    d = _corpus(tmp_path, ["first.md", "second.md"], index=index)
    assert compute_memory_defects(d)["orphans"] == []


def test_retiring_one_of_two_links_on_a_line_still_takes_its_note(tmp_path):
    """The removal path keeps its trailing run: this is why the two patterns
    stay separate rather than one being deleted."""
    line = "- Group: [one](first.md) a note about one · [two](second.md)\n"
    out = strip_index_pointers(line, ["first.md"])
    assert "a note about one" not in out
    assert "second.md" in out


@pytest.mark.parametrize("retire,expected", [
    (["first.md"], "- Group: [two](threads/business/drop.md)\n"),
    (["threads/business/drop.md"], "- Group: [one](first.md)\n"),
])
def test_a_path_qualified_neighbour_survives_a_retirement(retire, expected):
    """The rewriter has to SEE a pointer whose target carries a slash.

    If its pattern forbids one, that pointer stops being a pointer: retiring the
    bare-name link beside it leaves the separator dangling and the line is
    quietly malformed. Both patterns are built from one link grammar so this
    cannot drift; the test is what says the grammar has to allow a slash.
    """
    line = "- Group: [one](first.md) · [two](threads/business/drop.md)\n"
    assert strip_index_pointers(line, retire) == expected
