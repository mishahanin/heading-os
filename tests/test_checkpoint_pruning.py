#!/usr/bin/env python3
"""The checkpoint mechanism's three deletion paths, which had no test at all.

`scripts/utils/checkpoint_paths.py` is the spine of the checkpoint / compact /
handoff mechanism, and on 2026-08-20 it measured **39% line coverage** across
every test that names it: 189 of 308 statements never executed. The whole of
`_prune`, `prune_pointer_dirs` and `prune_state_dir` sat in the uncovered 61%.

Those three are the only code in the mechanism that DELETES. Everything else
writes, and a write that goes wrong leaves evidence; a delete that goes wrong
leaves nothing. So they are the first gap to close, before the merely-uncovered
read paths.

Two rules govern a prune, and both are tested here at their boundary:

  - age: drop anything older than `KEEP_DAYS` (14)
  - count: drop anything past the `KEEP_MAX` (25) newest

and one protection: the LIVE session's own entry is never a candidate. That last
one is what makes this mechanism safe to run from a hook inside the session it
is pruning around, so it is tested by name in every case below.

What is deliberately NOT pruned, and must stay that way: the archive `.md`
files. `prune_pointer_dirs` touches only the per-session pointer DIRS under
`.latest/`, never `.latest/summary.md` or `.latest/prompt.md` (files, read by
`scripts/next-signal.py`), and never the archives those pointers name. The live
overlay holds 261 archives at 5.4 MB and grows without bound BY DESIGN — the
archive is the record. A test that started deleting them would be the defect.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402

DAY = 86400


def _age(path: Path, days: float) -> None:
    """Backdate a path's mtime by `days`."""
    when = CP.utc_now().timestamp() - days * DAY
    os.utime(path, (when, when))


def _dir_entry(base: Path, name: str, days: float) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "summary.md").write_text("s", encoding="utf-8")
    (d / "prompt.md").write_text("p", encoding="utf-8")
    _age(d / "summary.md", days)
    _age(d / "prompt.md", days)
    _age(d, days)
    return d


# ---------------------------------------------------------------- _prune


def test_prune_drops_only_what_is_older_than_keep_days(tmp_path):
    removed = []
    entries = [
        (CP.utc_now().timestamp() - 1 * DAY, tmp_path / "fresh"),
        (CP.utc_now().timestamp() - (CP.KEEP_DAYS - 1) * DAY, tmp_path / "inside"),
        (CP.utc_now().timestamp() - (CP.KEEP_DAYS + 1) * DAY, tmp_path / "stale"),
    ]
    CP._prune(list(entries), removed.append)
    assert [p.name for p in removed] == ["stale"]


def test_prune_keeps_exactly_keep_max_when_all_are_fresh(tmp_path):
    removed = []
    now = CP.utc_now().timestamp()
    entries = [(now - i, tmp_path / f"e{i:03d}") for i in range(CP.KEEP_MAX + 7)]
    CP._prune(list(entries), removed.append)
    assert len(removed) == 7, f"kept {len(entries) - len(removed)}, expected {CP.KEEP_MAX}"
    # The ones dropped are the OLDEST, never the newest.
    assert all(p.name >= f"e{CP.KEEP_MAX:03d}" for p in removed)


def test_prune_at_exactly_keep_max_removes_nothing(tmp_path):
    """The boundary. One more than the limit drops one; the limit itself drops
    none, and an off-by-one here silently deletes a live session's neighbour."""
    now = CP.utc_now().timestamp()
    removed = []
    CP._prune([(now - i, tmp_path / f"e{i}") for i in range(CP.KEEP_MAX)], removed.append)
    assert removed == []

    removed = []
    CP._prune([(now - i, tmp_path / f"e{i}") for i in range(CP.KEEP_MAX + 1)], removed.append)
    assert len(removed) == 1


def test_prune_never_names_the_same_path_twice(tmp_path):
    """A path both too old AND past the count must be removed once, not twice —
    a second unlink raises, and the handler would swallow it as a real error."""
    now = CP.utc_now().timestamp()
    entries = [(now - i, tmp_path / f"fresh{i}") for i in range(CP.KEEP_MAX)]
    entries += [(now - (CP.KEEP_DAYS + 5) * DAY - i, tmp_path / f"old{i}") for i in range(3)]
    removed = []
    CP._prune(entries, removed.append)
    assert len(removed) == len(set(removed)) == 3


def test_prune_survives_a_removal_that_raises(tmp_path, capsys):
    """One unremovable entry must not stop the rest. A prune that aborts halfway
    leaves the directory growing with no signal."""
    now = CP.utc_now().timestamp()
    old = (CP.KEEP_DAYS + 2) * DAY
    seen = []

    def remove(path: Path) -> None:
        seen.append(path)
        if path.name == "locked":
            raise OSError(16, "Device or resource busy")

    CP._prune([(now - old, tmp_path / n) for n in ("locked", "a", "b")], remove)
    assert [p.name for p in seen] == ["locked", "a", "b"]
    assert "could not prune locked" in capsys.readouterr().err


# ------------------------------------------------------ prune_pointer_dirs


def test_pointer_prune_never_touches_the_live_session(tmp_path):
    handoff = tmp_path / "handoff"
    base = CP.latest_root(handoff)
    live = _dir_entry(base, "live-session", days=CP.KEEP_DAYS + 30)
    gone = _dir_entry(base, "old-session", days=CP.KEEP_DAYS + 30)

    CP.prune_pointer_dirs(handoff, keep_slug="live-session")

    assert live.is_dir(), "the live session's pointer dir was deleted"
    assert not gone.exists()


def test_pointer_prune_leaves_the_shared_pointer_files_alone(tmp_path):
    """`.latest/summary.md` and `.latest/prompt.md` are files that
    scripts/next-signal.py reads. Only DIRS are candidates."""
    handoff = tmp_path / "handoff"
    base = CP.latest_root(handoff)
    base.mkdir(parents=True)
    shared_summary = base / "summary.md"
    shared_prompt = base / "prompt.md"
    for f in (shared_summary, shared_prompt):
        f.write_text("shared", encoding="utf-8")
        _age(f, CP.KEEP_DAYS + 90)
    _dir_entry(base, "old-session", days=CP.KEEP_DAYS + 90)

    CP.prune_pointer_dirs(handoff, keep_slug="live-session")

    assert shared_summary.read_text(encoding="utf-8") == "shared"
    assert shared_prompt.read_text(encoding="utf-8") == "shared"
    assert not (base / "old-session").exists()


def test_pointer_prune_is_silent_when_there_is_nothing_there(tmp_path):
    CP.prune_pointer_dirs(tmp_path / "no-such-handoff", keep_slug="x")


# -------------------------------------------------------- prune_state_dir


def test_state_prune_keeps_the_live_state_file(tmp_path):
    live = tmp_path / "checkpoint-live.json"
    old = tmp_path / "checkpoint-old.json"
    for f in (live, old):
        f.write_text("{}", encoding="utf-8")
        _age(f, CP.KEEP_DAYS + 5)

    CP.prune_state_dir(tmp_path, keep_name="checkpoint-live.json")

    assert live.is_file()
    assert not old.exists()


def test_state_prune_ignores_files_it_does_not_own(tmp_path):
    """The glob is `checkpoint-*.json`. Anything else in that directory belongs
    to someone else and is not this function's to delete."""
    stranger = tmp_path / "session-notes.json"
    also = tmp_path / "checkpoint-old.txt"
    for f in (stranger, also):
        f.write_text("{}", encoding="utf-8")
        _age(f, CP.KEEP_DAYS + 40)

    CP.prune_state_dir(tmp_path, keep_name="checkpoint-live.json")

    assert stranger.is_file()
    assert also.is_file()


def test_state_prune_is_silent_when_the_dir_is_absent(tmp_path, capsys):
    """Silent AND creates nothing, which is the half a caller depends on.

    Unlike its pointer-dir twin, this function cannot raise on a missing
    directory with or without its `is_dir()` early return: everything below
    that line is `base.glob(...)`, and `Path.glob` swallows the
    missing-directory error and yields nothing (measured on 3.11.15, and on a
    path that is a FILE too). So "it did not raise" pins nothing here. What a
    caller does depend on is that a hook run after the operator deleted the
    state dir does not quietly put it back.
    """
    missing = tmp_path / "nope"

    CP.prune_state_dir(missing, keep_name="x")

    assert not missing.exists(), "pruning must not resurrect the directory"
    assert capsys.readouterr() == ("", "")


# ------------------------------------------------------------ bound_summary


@pytest.mark.parametrize("length,cut", [
    (10, False),
    (5000, True),
    # The two that decide the comparison. Until 2026-08-27 the set bracketed the
    # limit at 10 and 5000 and stood on neither side of it, so `len(text) <=
    # limit` could become `<` and a summary of exactly the limit would be cut
    # and told the rest lives in an archive file that holds nothing more.
    (100, False),
    (101, True),
])
def test_bound_summary_states_where_the_rest_lives(length, cut):
    text = "x" * length
    out = CP.bound_summary(text, "outputs/operations/handoff-archive/x.md", limit=100)
    if cut:
        assert "Cut at 100 characters" in out
        assert "handoff-archive/x.md" in out
    else:
        assert out == text
