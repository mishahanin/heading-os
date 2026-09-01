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
from datetime import datetime, timezone
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


def test_prune_keeps_an_entry_sitting_exactly_on_the_age_cutoff(tmp_path, monkeypatch):
    """The case ON the line, which no age test stood on.

    `test_prune_drops_only_what_is_older_than_keep_days` brackets the cutoff at
    a day either side, so it is true of `mtime < cutoff` and of `mtime <=
    cutoff` alike. Mutation-confirmed 2026-09-01: flipping that comparator left
    this file green at 18 passed. These three entries are one second apart
    around the boundary, against a frozen clock - `_prune` reads
    `CP.utc_now()` itself, so an exact hit is unconstructible without pinning
    it, which is why the boundary went untested.
    """
    frozen = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(CP, "utc_now", lambda: frozen)
    cutoff = frozen.timestamp() - CP.KEEP_DAYS * DAY

    removed = []
    CP._prune(
        [
            (cutoff, tmp_path / "on-the-line"),
            (cutoff - 1, tmp_path / "one-second-older"),
            (cutoff + 1, tmp_path / "one-second-younger"),
        ],
        removed.append,
    )
    assert [p.name for p in removed] == ["one-second-older"], (
        "an entry exactly KEEP_DAYS old is inside the window and must survive"
    )


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


def test_state_prune_keeps_the_lock_of_a_session_whose_state_file_is_there(tmp_path):
    """The lock sweep's key is "its state file is gone", and nothing tested it.

    `locked_state` writes `<name>.json.lock` beside each state file and HOLDS an
    flock on it while a hook reads-modifies-writes. The flock lives on the
    inode: unlink the path and the next process creates a different inode and
    takes an uncontended lock, so mutual exclusion is lost with nothing raised
    and nothing logged. The live session's lock is exactly the one being held
    while this function runs, from `checkpoint-save.py`.

    Mutation-confirmed 2026-09-01: replacing the existence test with `if True`
    - unlinking every lock in the directory, the live one included - was green
    across all twelve checkpoint test files and green again across the wider
    seventeen-file set that names this module.

    Three locks, one per case, or the sweep could be satisfied by doing nothing:
    the live session's survives, a session pruned by this very call loses its
    lock with it, and a lock left behind by a session pruned long ago goes too.
    """
    live = tmp_path / "checkpoint-live.json"
    stale = tmp_path / "checkpoint-stale.json"
    for f in (live, stale):
        f.write_text("{}", encoding="utf-8")
        _age(f, CP.KEEP_DAYS + 40)

    live_lock = tmp_path / "checkpoint-live.json.lock"
    stale_lock = tmp_path / "checkpoint-stale.json.lock"
    orphan_lock = tmp_path / "checkpoint-longgone.json.lock"
    for f in (live_lock, stale_lock, orphan_lock):
        f.write_text("", encoding="utf-8")

    CP.prune_state_dir(tmp_path, keep_name="checkpoint-live.json")

    assert live.is_file(), "the fixture's live state file was pruned"
    assert live_lock.is_file(), (
        "the live session's lock was unlinked while a hook may be holding an "
        "flock on it; the next writer takes a fresh inode and the exclusion is "
        "silently gone"
    )
    assert not stale.exists(), "the fixture no longer prunes a state file"
    assert not stale_lock.exists(), (
        "a lock whose state file this same call deleted was left behind"
    )
    assert not orphan_lock.exists(), (
        "a lock left by a session pruned before the sweep existed was left behind"
    )


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


# The case above asserts the trailing NOTE and nothing else, because a run of
# identical "x" cannot tell one retained prefix from another. So the retained
# text itself was unbound: `text[:limit].rstrip()` could be replaced by `""` and
# the parametrised case above still passed. That is not a cosmetic gap. This
# return value is interpolated straight into the `## Summary` section of the
# pointer that `.claude/hooks/checkpoint-save.py` writes, and the pointer is what
# gets injected into the resumed session. Losing the prefix leaves the next
# session a heading, a note, and no handoff at all - while the note it kept
# still promises the reader that a summary was written.
#
# Distinct words, not a run of one character, so an assertion can name what
# survived and what did not.
KEPT = "alpha bravo charlie delta echo foxtrot golf hotel india juliett"
DROPPED = "kilo lima mike november oscar papa quebec"
ARCHIVE = "outputs/operations/handoff-archive/2026-01-01-example.md"


def test_bound_summary_keeps_the_text_it_retained():
    """The cut branch must return the retained CONTENT, then the note.

    `limit` is the length of KEPT, so the retained prefix is exactly that phrase
    and the assertion compares against a literal rather than re-running the
    slice the code under test performs.
    """
    out = CP.bound_summary(KEPT + " " + DROPPED, ARCHIVE, limit=len(KEPT))

    assert out.startswith(KEPT), "the retained summary was lost, only the note survived"
    assert "quebec" not in out, "text past the limit came through"
    # The note still has to be there. It names where the rest went.
    assert f"Cut at {len(KEPT)} characters" in out
    assert ARCHIVE in out


def test_bound_summary_cuts_at_the_limit_not_somewhere_short_of_it():
    """A prefix shorter than `limit` keeps the note honest and the summary wrong.

    `startswith` above already refuses a short slice, but only because KEEP is
    one phrase. This states the length directly: everything before the note is
    the full budget, and the first dropped word is the one that sat past it.
    """
    text = KEPT + " " + DROPPED
    out = CP.bound_summary(text, ARCHIVE, limit=len(KEPT))
    retained = out.split("\n\n[Cut at")[0]

    assert len(retained) == len(KEPT)
    assert retained.endswith("juliett")
    assert text[len(KEPT):].split()[0] == "kilo"
    assert "kilo" not in out


def test_bound_summary_does_not_leave_the_cut_sitting_on_whitespace():
    """`.rstrip()` in the retained expression, which nothing exercised either.

    Every existing case cuts mid-word, so the strip never had anything to do and
    could be deleted unnoticed. Here the limit lands on a run of spaces.
    """
    text = KEPT + "     " + DROPPED
    out = CP.bound_summary(text, ARCHIVE, limit=len(KEPT) + 3)

    assert out.startswith(KEPT + "\n\n[Cut at"), "the cut kept trailing whitespace"

