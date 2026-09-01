"""Five things `compute_dormant` and `_memory_meta` do that nothing measured.

`tests/test_dream_shadow.py` is careful about the RULE: access-based rather than
salience-based, an aged-but-surfaced fact is not dormant, a young one never is,
and the report proposes no removal. It says nothing about the reading underneath
the rule, which is where the two functions actually spend their code.

MEASURED 2026-09-01 by mutating `scripts/dream-shadow.py` and running every test
file in the repo that names it (`test_dream_shadow.py`,
`test_a_scan_that_never_ran_reported_nothing_to_do.py`,
`test_memory_touch_util.py`, `test_salience.py`,
`test_ten_reads_that_saw_only_half_of_unreadable.py`,
`test_a_crashed_check_that_rendered_a_clean_brief.py`,
`test_prime_health_registry.py`, 164 tests). All five survived the whole set:

    deleting the `p.name == "MEMORY.md"` exclusion       164 passed
    reversing the oldest-first sort                      164 passed
    dropping ValueError from the access_count coercion   164 passed
    dropping the nested `metadata:` fallback for `type`  164 passed
    dropping `errors="replace"` from the frontmatter read 164 passed

Three of them are documented behaviour with no case. The docstring says "Oldest
first"; nothing looked at the order. The docstring says real auto-memory nests
these fields under `metadata:`, and every fixture in the existing file nests them
too, but `access_count` and `last_accessed` carry the weight in those tests, so
deleting the fallback for `type` alone changed no verdict.

Two are degradations rather than features, and both fail toward a nightly job
that stops running. `access_count` comes from hand-edited frontmatter, so
`access_count: many` reaches `int()`; without the `ValueError` arm that is an
uncaught raise from a cron job. `errors="replace"` is the only thing standing
between one cp1251 memory note and a `UnicodeDecodeError` out of the read, which
is a `ValueError` and matches neither the `except OSError` above it nor anything
in `gather`. `auto-memory/` is a hand-edited directory shared with an external
editor, so a note in the wrong encoding is the ordinary way to get there rather
than the exotic one.

`MEMORY.md` is the always-loaded pointer index rather than a memory, and listing
it as dormant would put the operator's own table of contents on a worklist that
exists to say what has gone quiet.

Nothing here writes to auto-memory: every fixture is a tmp_path tree, and the
subject function is read-only by contract.

Run: .venv/bin/python -m pytest tests/test_a_dormancy_list_that_measured_none_of_its_own_reading.py -q
"""
from __future__ import annotations

import importlib.util
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dream-shadow.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("dream_shadow_reading", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mem(tmp_path):
    directory = tmp_path / "auto-memory"
    directory.mkdir(parents=True)
    return directory


def _age(path: Path, days_old: int) -> None:
    stamp = time.time() - days_old * 86400
    os.utime(path, (stamp, stamp))


def _write(directory: Path, name: str, body: str, days_old: int) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    _age(path, days_old)
    return path


def _nested_fact(mem_type: str, access_count, name: str) -> str:
    """A memory file in the shape the real auto-memory writer produces: every
    field under `metadata:`, never at the top level."""
    return (
        "---\n"
        f"name: {name}\n"
        "description: fixture fact\n"
        "metadata:\n"
        "  node_type: memory\n"
        f"  type: {mem_type}\n"
        f"  access_count: {access_count}\n"
        "---\n\n"
        "Some fact body.\n"
    )


def _names(mod, directory: Path) -> list[str]:
    return [c["name"] for c in mod.compute_dormant(directory,
                                                   datetime.now(timezone.utc))]


# ============================================================
# 1. the index is not one of the things being indexed
# ============================================================

def test_the_memory_index_is_never_listed_as_dormant(mod, mem):
    """`MEMORY.md` is the pointer layer `.claude/rules/memory-discipline.md`
    keeps lean, not a fact. An aged index on a worklist headed "what has gone
    quiet" invites exactly the consolidation it must not receive."""
    _write(mem, "MEMORY.md", "# Memory index\n\n- a pointer\n", days_old=400)

    assert _names(mod, mem) == []


def test_the_exclusion_is_by_name_and_not_a_blanket_drop(mod, mem):
    """Vacuity guard for the exclusion: a filter that dropped everything would
    also pass the test above."""
    _write(mem, "MEMORY.md", "# Memory index\n", days_old=400)
    _write(mem, "quiet-fact.md", _nested_fact("reference", 0, "quiet-fact"),
           days_old=400)

    assert _names(mod, mem) == ["quiet-fact.md"]


# ============================================================
# 2. "Oldest first", which the docstring promises and nothing read
# ============================================================

def test_the_list_is_ordered_oldest_first(mod, mem):
    """The operator reads the top of this list. Reversed, it hands back the
    freshest of the quiet facts as the first thing to look at."""
    _write(mem, "middle.md", _nested_fact("reference", 0, "middle"), days_old=120)
    _write(mem, "oldest.md", _nested_fact("reference", 0, "oldest"), days_old=400)
    _write(mem, "newest.md", _nested_fact("reference", 0, "newest"), days_old=60)

    assert _names(mod, mem) == ["oldest.md", "middle.md", "newest.md"]


# ============================================================
# 3. frontmatter that a person typed
# ============================================================

def test_a_non_numeric_access_count_degrades_to_zero_rather_than_raising(mod, mem):
    """`auto-memory/` is hand-edited and shared with an external editor, so
    `access_count: many` is a typo away. `int("many")` is a `ValueError`, and
    this runs on a nightly timer where an uncaught one is a job that stops."""
    _write(mem, "typo.md", _nested_fact("reference", "many", "typo"), days_old=90)

    assert mod._memory_meta(mem / "typo.md") == ("reference", 0, "")
    assert "typo.md" in _names(mod, mem), (
        "a fact with an unreadable access count vanished from the list instead "
        "of being read as never surfaced")


def test_the_type_is_read_from_the_nested_metadata_block(mod, mem):
    """Every real auto-memory file nests it. Without the fallback the type is ""
    and `composite_salience` grades every fact on its access count alone, which
    silently flattens the ranking the report is sorted and read by."""
    _write(mem, "typed.md", _nested_fact("feedback", 3, "typed"), days_old=90)

    mem_type, access_count, _last = mod._memory_meta(mem / "typed.md")
    assert mem_type == "feedback"
    assert access_count == 3


def test_a_top_level_type_still_wins_over_the_nested_one(mod, mem):
    """The precedence the code states, so the fallback cannot be reordered into
    a preference without a case saying so."""
    _write(mem, "both.md",
           "---\n"
           "name: both\n"
           "type: feedback\n"
           "metadata:\n"
           "  type: reference\n"
           "  access_count: 0\n"
           "---\n\nbody\n",
           days_old=90)

    assert mod._memory_meta(mem / "both.md")[0] == "feedback"


def test_a_note_in_the_wrong_encoding_is_read_rather_than_raised_over(mod, mem):
    """One cp1251 note used to end the nightly scan. `UnicodeDecodeError` is a
    `ValueError`, so the `except OSError` on this read never covered it, and
    nothing further up the call chain does either.

    `errors="replace"` is the right trade HERE and not everywhere: the decoded
    value feeds a type weight and a count, never a name or a subject that gets
    shown to a person or sent, so a replacement character costs nothing and a
    dead nightly job costs the whole report.
    """
    path = mem / "cp1251.md"
    path.write_bytes(
        "---\nname: legacy\nmetadata:\n  type: reference\n  access_count: 0\n"
        "---\n\n".encode("utf-8")
        + b"A note pasted from an older editor: caf\xe9 receipts.\n"
    )
    _age(path, 90)

    assert mod._memory_meta(path) == ("reference", 0, "")
    assert "cp1251.md" in _names(mod, mem)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
