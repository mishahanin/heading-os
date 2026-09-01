#!/usr/bin/env python3
"""The PreCompact brief named the alphabet's first 25 files, and shed the rest.

Two defects in `.claude/hooks/checkpoint-precompact.py`, both MEASURED 2026-08-31
against this repository and this session's own 250 MB transcript, both invisible
because the output they damage is read by a summariser and never by a person.

**The written block sorted by path, not by recency.** `_written` did
`sorted(mine)` and then cut to `MAX_WRITTEN`. `sorted()` on Path objects orders
by path components, so of 1790 recorded paths (1651 still on disk, 137 renamed
or deleted) the 25 that survived the cut were every `.claude/agents/*.md` and
`.claude/hooks/*` in the tree, and not one of the files edited in the preceding
hour. Those were among the 1637 the cut discarded. The block's own docstring says
it "exists to tell the next turn what to READ", and the heading above it in the
rendered brief says "Preserve the following VERBATIM".

**The six facts did not fit at their own bounds, so the drop path was the normal
path.** Measured the same day on the live tree, the six blocks came to branch 20,
status 1030, log 467, written 944, handoff 118, plan 306. Assembled: 4361
characters against `MAX_OUTPUT = 4000`. So the drop loop fired on an ordinary
compaction and omitted the FIRST entry of `DROP_ORDER`, `Uncommitted changes (git
status --short)`: the most volatile fact in the set, gone from EVERY compaction
brief of this repository rather than occasionally. Final output 3513 of 4000,
with 487 characters spare and a tail note naming the block it had just deleted.

The bound that failed was a LINE bound, which is why nobody caught it by reading
it: `MAX_STATUS_LINES = 40` and `MAX_WRITTEN = 25` sound small, and a line here is
a path, which has no length limit. 40 status lines at the width this tree
actually produces is about 1800 characters on its own.

A third, latent, same-family defect went with them. The drop loop aimed at
`MAX_OUTPUT - _NOTE_BUDGET` with `_NOTE_BUDGET = 320`, while the note it then
wrote measured 187, so a body landing between 3681 and 3813 characters lost one
whole extra fact to make room for 133 characters nothing would use. Not reached
in the measured run (3326 after the first drop), so it is pinned here by
construction rather than by the live tree.

What this file pins:

- the written list is ordered newest-first, and the head cut therefore drops the
  OLDEST writes, with ties broken reproducibly (`mine` is a set, so its iteration
  order is not stable between runs and a bare mtime sort is not either);
- the "N more path(s) written earlier no longer exist" exclusion line survives,
  and a path the tree no longer has is still counted rather than listed;
- a fact set built at the bounds the module DECLARES renders with nothing
  dropped, so a future raise of any bound fails here instead of quietly shedding
  a block again;
- and the other direction, so none of the above can be green over a corpus that
  never overflows: a fact genuinely past its ceiling is still cut, still cut on
  whole lines, and still named in the tail note.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "precompact_pointer_block", ROOT / ".claude" / "hooks" /
        "checkpoint-precompact.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bound(module, name: str, default=None):
    """A bound the module declares, or `default` when it declares none.

    Asked rather than assumed, so this file measures the code's own bounds. A
    version that declares no character ceiling is exercised at its line bound and
    fails the fit test, which is the point.
    """
    return getattr(module, name, default)


# ---------------------------------------------------------------------------
# Defect 1 - the written block shipped the alphabet
# ---------------------------------------------------------------------------

def _tree(tmp_path: Path, names: list[str], *, newest_first: list[str]) -> Path:
    """Create `names`, then stamp `newest_first[0]` as the most recent write.

    Timestamps are set explicitly. A test that writes files in order and trusts
    the clock is measuring the filesystem's mtime granularity, and on a fast
    tmpfs several files share one timestamp.
    """
    project = tmp_path.resolve()
    for name in names:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    base = 1_700_000_000
    for rank, name in enumerate(newest_first):
        stamp = base - rank * 60
        os.utime(project / name, (stamp, stamp))
    return project


def _patch_written(monkeypatch, paths):
    """Make `files_written` answer with exactly `paths`.

    `_written` imports the resolver inside its own body, so the module attribute
    is what it reads.
    """
    import scripts.utils.session_scope as scope

    monkeypatch.setattr(scope, "files_written", lambda _transcript: set(paths))


def test_the_head_cut_keeps_the_newest_writes_not_the_alphabetically_first(
        mod, tmp_path, monkeypatch):
    """The defect, in the smallest corpus that shows it.

    Alphabetical order is the exact REVERSE of write order here, so a path sort
    ships the oldest files and a recency sort ships the newest. Measured on the
    live transcript the reversal was not that tidy and was worse in effect: the
    25 shown were all under `.claude/`, which sorts first, and the files edited
    in the last hour sorted later and were dropped.
    """
    limit = mod.MAX_WRITTEN
    total = limit + 12
    # `a_00` is alphabetically first and the OLDEST; `z_..` is last and newest.
    names = [f"src/{chr(ord('a') + i % 26)}_{i:02d}_file.py" for i in range(total)]
    newest_first = list(reversed(names))
    project = _tree(tmp_path, names, newest_first=newest_first)
    _patch_written(monkeypatch, [project / n for n in names])

    block = mod._written({"transcript_path": "t.jsonl"}, project)
    shown = [line for line in block.splitlines() if line.endswith("_file.py")]

    assert shown, f"nothing was listed at all:\n{block}"
    assert shown[0] == newest_first[0], (
        f"the block leads with {shown[0]!r}; the most recent write was "
        f"{newest_first[0]!r}. Ordered by path, not by work."
    )
    # The oldest twelve are the ones the cut has to lose.
    for stale in names[:12]:
        assert stale not in shown, (
            f"{stale!r} was written first and survived the cut while more "
            f"recent files did not:\n{block}"
        )
    assert shown == newest_first[:len(shown)], (
        "the surviving paths are not the N most recent, in order"
    )


def test_the_cut_says_how_many_lines_it_dropped(mod, tmp_path, monkeypatch):
    """The other half of the head cut: it still admits the drop.

    A recency sort that silently discarded the tail would read as complete
    coverage, which `.claude/rules/scope-claims.md` forbids.
    """
    limit = mod.MAX_WRITTEN
    names = [f"src/file_{i:03d}.py" for i in range(limit + 7)]
    project = _tree(tmp_path, names, newest_first=list(reversed(names)))
    _patch_written(monkeypatch, [project / n for n in names])

    block = mod._written({"transcript_path": "t.jsonl"}, project)
    assert "more line(s)]" in block, f"the cut was silent:\n{block}"


def test_a_path_the_tree_no_longer_has_is_counted_and_never_listed(
        mod, tmp_path, monkeypatch):
    """The exclusion line stays, and a gone path never displaces a live one.

    A gone path has no readable mtime, so a recency sort has to put it somewhere.
    Last: 137 of this session's 1790 recorded paths were renamed or deleted, and
    a pointer that resolves to nothing at the head of a list of what to read next
    is the failure `_written` already went to lengths to avoid.
    """
    live = [f"src/live_{i:02d}.py" for i in range(4)]
    project = _tree(tmp_path, live, newest_first=list(reversed(live)))
    gone = [project / f"src/gone_{i:02d}.py" for i in range(3)]
    _patch_written(monkeypatch, [project / n for n in live] + gone)

    block = mod._written({"transcript_path": "t.jsonl"}, project)

    assert "3 more path(s) written earlier no longer exist" in block, block
    for missing in gone:
        assert missing.name not in block, f"a gone path was listed:\n{block}"
    listed = [line for line in block.splitlines() if line.endswith(".py")]
    assert listed == list(reversed(live)), listed


def test_two_writes_at_one_timestamp_order_the_same_way_every_run(
        mod, tmp_path, monkeypatch):
    """Reproducibility, because the input is a SET.

    `files_written` returns a set, whose iteration order varies between
    processes. A sort keyed on mtime alone leaves equal timestamps in that order,
    so one tree would render two different briefs, and a later hook commits this
    text to a tracked file.
    """
    names = [f"src/tie_{i:02d}.py" for i in range(6)]
    project = _tree(tmp_path, names, newest_first=[])
    stamp = 1_700_000_000
    for name in names:
        os.utime(project / name, (stamp, stamp))
    paths = [project / n for n in names]

    _patch_written(monkeypatch, paths)
    first = mod._written({"transcript_path": "t.jsonl"}, project)
    _patch_written(monkeypatch, list(reversed(paths)))
    second = mod._written({"transcript_path": "t.jsonl"}, project)

    assert first == second, f"order depended on set iteration:\n{first}\n---\n{second}"


# ---------------------------------------------------------------------------
# Defect 2 - the six facts did not fit at their own bounds
# ---------------------------------------------------------------------------

# Widths measured on this tree 2026-08-31 by driving the real hook: `git status
# --short` lines averaged 37 characters (max 67, p90 63) and the 1651 live
# written paths averaged 42 (max 101, p90 65). The values below sit above both
# means, so a fact built from them is a realistic worst case rather than a
# convenient one. Once a CHARACTER ceiling exists the width stops deciding the
# block size and only decides how many lines reach the ceiling; before the fix it
# decided everything, which is why a line bound looked safe.
STATUS_WIDTH = 48
WRITTEN_WIDTH = 52

# Room reserved for the three facts that declare no bound of their own, and for
# the branch. Measured the same day on the live tree, block sizes INCLUDING the
# label: branch 20, log 467, handoff 118, plan 306. Reserved with margin, since
# a long branch name, a wordy commit subject, a longer session slug and a longer
# unchecked plan item are all ordinary.
RESERVED = {"branch": 46, "log": 500, "handoff": 140, "plan": 400}


def _label(mod, key: str) -> str:
    return dict(mod.FACT_LABELS)[key]


def _blocks(mod, facts: dict) -> dict:
    """The per-key blocks `render` builds, without the redaction or the bound."""
    return {key: f"{label}:\n{facts[key].strip()}"
            for key, label in mod.FACT_LABELS
            if facts.get(key) and facts[key].strip()}


@pytest.fixture
def facts_at_bounds(mod, tmp_path, monkeypatch):
    """Every fact at its ceiling, collected through the hook's OWN path.

    Deliberately not hand-built. A fact set assembled here with a bound this file
    chose would still pass if the bound were deleted from `collect_facts` or from
    `_written`, and a bound applied nowhere is the defect. So git, the transcript
    and the two pointer facts are stubbed and `collect_facts` does the bounding,
    which is what ships.
    """
    project = tmp_path.resolve()
    over = 40                      # comfortably past every line bound

    def _git(_project, *args):
        if args[0] == "rev-parse":
            return "b" * (RESERVED["branch"] - len(_label(mod, "branch")) - 2)
        if args[0] == "status":
            return "\n".join(f" M {'s' * (STATUS_WIDTH - 6)}{i:02d}"
                             for i in range(mod.MAX_STATUS_LINES + over))
        if args[0] == "log":
            return "\n".join("c" * 90 for _ in range(5))
        raise AssertionError(f"unexpected git call: {args}")

    names = [f"scripts/{'w' * (WRITTEN_WIDTH - 14)}_{i:03d}.py"
             for i in range(mod.MAX_WRITTEN + over)]
    _tree(tmp_path, names, newest_first=list(reversed(names)))
    # A four-digit gone count, because it is part of the block and this session
    # recorded 137 of them against 1651 live paths.
    written = [project / n for n in names]
    written += [project / f"scripts/gone_{i:04d}.py" for i in range(1370)]

    monkeypatch.setattr(mod, "_git", _git)
    monkeypatch.setattr(mod.CP, "project_root", lambda _payload: project)
    monkeypatch.setattr(
        mod, "_handoff_pointer",
        lambda _payload, _project:
            "h" * (RESERVED["handoff"] - len(_label(mod, "handoff")) - 2))
    monkeypatch.setattr(
        mod, "_plan",
        lambda _project: "p" * (RESERVED["plan"] - len(_label(mod, "plan")) - 2))
    _patch_written(monkeypatch, written)

    facts = mod.collect_facts({"transcript_path": "t.jsonl"})
    assert set(facts) == {key for key, _ in mod.FACT_LABELS}, (
        f"a fact never arrived: {sorted(facts)}"
    )
    return facts


def test_the_six_facts_fit_at_their_own_declared_bounds(mod, facts_at_bounds):
    """The test that would have caught it, and it is not "4011 fits".

    Every fact is filled to the ceiling the module itself declares. If the six
    cannot fit there, the drop loop is not an edge case that a busier tree
    reaches: it is the normal path, and the fact it deletes first is the one no
    command in the note recovers as cheaply as reading it here would have.
    """
    facts = facts_at_bounds
    out = mod.render(facts)

    assert "Cut to fit" not in out, (
        "a fact set at the declared bounds still overflowed: assembled "
        f"{len(mod._assemble(_blocks(mod, facts)))} of {mod.MAX_OUTPUT}. "
        f"Tail:\n{out[-320:]}"
    )
    for key, label in mod.FACT_LABELS:
        assert f"{label}:" in out, f"{key} never reached the output"
    assert len(out) <= mod.MAX_OUTPUT


def test_the_declared_bounds_leave_room_for_all_six(mod, facts_at_bounds):
    """The same claim as arithmetic, so a raised bound fails here too.

    Independent of `render`: it adds up the fixed instruction block, the facts
    header, the separators and each fact's ceiling. Raise MAX_STATUS_CHARS,
    MAX_WRITTEN_CHARS, or a line bound in a version that declares no character
    ceiling, and this fails with the arithmetic on the screen instead of a brief
    that quietly stops carrying the working tree.
    """
    blocks = _blocks(mod, facts_at_bounds)
    assert len(blocks) == len(mod.FACT_LABELS), "a fact went missing"
    worst = len(mod._assemble(blocks))

    assert worst <= mod.MAX_OUTPUT, (
        f"the six facts at their bounds assemble to {worst}, over "
        f"{mod.MAX_OUTPUT}. Per block: "
        f"{ {k: len(v) for k, v in blocks.items()} }"
    )


def test_a_character_ceiling_cuts_a_long_line_list_on_line_boundaries(mod):
    """`_head` has to bound characters, not only lines, and cut whole lines.

    A path is not a sentence: half of one is a pointer that resolves to nothing,
    which is the `.claude/ski` fragment the drop loop's own comment records.
    """
    ceiling = _bound(mod, "MAX_STATUS_CHARS")
    assert ceiling, "no character ceiling is declared for the status fact"
    raw = "\n".join(f" M {'z' * 120}/{i:03d}.py" for i in range(40))

    out = mod._head(raw, mod.MAX_STATUS_LINES, ceiling)

    assert len(out) <= ceiling, f"{len(out)} characters against a {ceiling} bound"
    for line in out.splitlines():
        assert line.endswith(".py") or line.startswith("[... "), (
            f"a line was cut mid-string: {line!r}"
        )
    assert "more line(s)]" in out, "the character cut was silent"


# ---------------------------------------------------------------------------
# The other direction: the drop path still works, and no longer over-drops
# ---------------------------------------------------------------------------

def test_a_fact_past_its_ceiling_is_still_dropped_whole_and_named(
        mod, facts_at_bounds):
    """Without this, everything above could be green over a corpus that never
    overflows. `render` bounds nothing itself and takes whatever it is handed, so
    a working tree of 400 changed files still overflows it, and the answer is
    still a whole block gone and named, never a half-written one."""
    facts = dict(facts_at_bounds)
    facts["status"] = "\n".join(f" M scripts/file_{i}.py" for i in range(400))

    out = mod.render(facts)

    assert len(out) <= mod.MAX_OUTPUT
    body, _, note = out.partition("\n\n[Cut to fit")
    assert note, "an overflowing fact set was not cut at all"
    assert _label(mod, "status") in note, note
    assert " M scripts/file_" not in body, "the dropped block left fragments"
    assert note.endswith("]")


def _remainder_of(mod, target: int) -> dict:
    """A fact set whose blocks assemble to `target` once `status` is dropped.

    Computed against the module rather than hardcoded, so the window this
    brackets moves with `KEEP_SET` instead of going stale beside it.
    """
    facts = {
        "status": "\n".join(f" M scripts/file_{i}.py" for i in range(400)),
        "written": "\n".join(f"scripts/utils/module_{i:02d}.py" for i in range(8)),
        "handoff": "outputs/operations/handoff-archive/.latest/slug/summary.md",
        "plan": "p",
    }
    without_status = {k: v for k, v in _blocks(mod, facts).items()
                      if k != "status"}
    pad = target - len(mod._assemble(without_status))
    assert pad >= 0, f"target {target} is below the floor of this fact set"
    facts["plan"] = "p" * (1 + pad)
    return facts


@pytest.mark.parametrize("remainder", [3700, 3750, 3800])
def test_the_loop_drops_only_what_the_note_it_writes_makes_necessary(
        mod, remainder):
    """The latent third defect, reached by construction.

    `_NOTE_BUDGET = 320` reserved for a note that measures 187 on this tree, and
    the loop compared against `MAX_OUTPUT - 320`. Between 3681 and 3813
    characters that difference costs one WHOLE further fact: the body already
    fits beside the note the loop is about to write, and the loop deletes another
    block anyway. The three sizes above sit inside that window.
    """
    facts = _remainder_of(mod, remainder)
    out = mod.render(facts)
    body, _, note = out.partition("\n\n[Cut to fit")

    assert len(out) <= mod.MAX_OUTPUT
    assert note, "the fact set was meant to overflow"
    dropped = note.split("Omitted whole: ")[1].split(". Read them")[0]
    assert dropped == _label(mod, "status"), (
        f"more than the working tree was dropped from a body of {remainder} "
        f"characters: {dropped}"
    )
    assert "scripts/utils/module_00.py" in body, (
        "the written block was dropped to reserve room nothing used"
    )


@pytest.mark.parametrize("remainder", [3900, 3990])
def test_a_body_that_truly_does_not_fit_beside_the_note_still_sheds_a_block(
        mod, remainder):
    """The reserve shrank; it did not disappear.

    Past the window the note genuinely does not fit beside the body, and a
    second block still has to go. A note that overflows the bound it announces
    is the 2026-08-25 defect this loop was written to end.
    """
    facts = _remainder_of(mod, remainder)
    out = mod.render(facts)
    _, _, note = out.partition("\n\n[Cut to fit")

    assert len(out) <= mod.MAX_OUTPUT, f"the note overflowed the bound: {len(out)}"
    assert note, "the fact set was meant to overflow"
    dropped = note.split("Omitted whole: ")[1].split(". Read them")[0]
    assert _label(mod, "status") in dropped
    assert dropped != _label(mod, "status"), (
        f"a body of {remainder} characters cannot hold the note beside it, so a "
        f"second block had to go; only one did: {dropped}"
    )
