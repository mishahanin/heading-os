"""The friction counters, promoted from the frozen contract that built them.

`tests/contract/2026-08-03-friction-counters/` retired when the slice shipped.
These are its twelve tests, kept because every one of them pins permanent
behaviour of `scripts/utils/canopus_friction.py` rather than the transition that
introduced it. Three of them earned their present shape by killing a mutation
that survived an earlier version, and each says so where it stands.

WHAT THE MODULE REFUSES TO DO, pinned here rather than left to its docstring:

- It does not judge. A high count is not a failure; `production-shape` earned its
  five windows by finding five real problems. A page that scolds the count
  teaches the builder to suppress windows, and suppressing a window means editing
  a frozen contract in place, which is the one thing Canopus exists to prevent.
- It counts only what the ledger records STRUCTURALLY: a window is `release` with
  `kind == "window"`, a retake is `anchor_replaced`, a refusal is
  `refuse_approve` / `refuse_release`, a failed verify is `verify_fail`. Waivers
  are NOT counted, because `--contract-satisfied` lands in a free-text `reason`
  and a counter built on a substring lies quietly the first time somebody rewords.
- The count is a FLOOR, never a total. `.canopus/` is gitignored and one `rm -rf`
  takes the ledger with it, so a zero is ambiguous between a clean slice and a
  lost ledger. `recorded` resolves exactly that one ambiguity.
- Counts are scoped by LABEL, so two slices sharing a label merge into one row,
  and the page may not pretend otherwise.

The end-to-end claim -- that the section reaches a rendered page at all -- had two
tests, both against `canopus pack`. That command and the evidence page it printed
were deleted on 2026-08-07 with the rest of the freeze lifecycle, so the module
now has no caller and the claim has no subject. What is below is unchanged: the
counters' own behaviour, which is what the twelve tests were always about.
"""

import json


def _row(event, label="s", kind="", reason="", ts="2026-08-03T00:00:00+00:00"):
    return {"event": event, "label": label, "kind": kind, "reason": reason,
            "ts": ts, "root": "d" * 64}


# ============================================================
# The four structural counts
# ============================================================

def test_a_window_is_a_release_of_kind_window_and_nothing_else():
    """`release` carries both meanings in one event name; only `kind` separates
    the mid-slice window from the end-of-slice ship. Counting `release` would
    report every shipped slice as having opened a window."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("release", kind="window"),
        _row("release", kind="ship"),
        _row("release", kind="window"),
    ], label="s")
    assert counts.windows == 2
    assert counts.ships == 1


def test_a_retake_is_an_anchor_replaced_entry():
    """`approve --replace` writes BOTH an `approve` and an `anchor_replaced`, so
    counting `approve` double-counts a retake as an approval and hides that it
    was a second one."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("approve"), _row("approve"), _row("anchor_replaced"),
    ], label="s")
    assert counts.retakes == 1
    assert counts.approvals == 2


def test_refusals_and_failed_verifies_are_counted_apart():
    """A refusal is the gate declining an action; a failed verify is the contract
    having moved. Merging them would let a slice that was refused twice read the
    same as one whose tree drifted twice."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("refuse_approve"), _row("refuse_release"), _row("verify_fail"),
    ], label="s")
    assert counts.refusals == 2
    assert counts.verify_failures == 1


def test_entries_for_other_labels_are_not_counted():
    """The page describes ONE slice. A ledger holding hundreds of records across
    dozens of labels must not report the fleet's friction as this slice's."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("release", label="mine", kind="window"),
        _row("release", label="other", kind="window"),
        _row("anchor_replaced", label="other"),
    ], label="mine")
    assert counts.windows == 1
    assert counts.retakes == 0


def test_an_unknown_event_is_ignored_rather_than_miscounted():
    """The ledger's vocabulary grows. An event this counter has never seen must
    not land in the nearest bucket."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([_row("something_new"), _row("release", kind="")],
                            label="s")
    assert counts.windows == 0 and counts.ships == 0
    assert counts.retakes == 0 and counts.refusals == 0


# ============================================================
# A zero must never read as a clean bill on its own
# ============================================================

def test_a_slice_with_no_freeze_entry_is_reported_as_unrecorded():
    """The distinction the whole section turns on. A held freeze always wrote a
    `freeze` line, so no `freeze` line means the ledger lost it, and a row of
    zeroes then describes a missing ledger, not a frictionless slice."""
    from scripts.utils.canopus_friction import count_friction

    absent = count_friction([], label="s")
    assert absent.recorded is False

    present = count_friction([_row("freeze")], label="s")
    assert present.recorded is True
    assert present.windows == 0


def test_the_rendered_section_says_which_zero_it_is():
    """Same claim at the render layer, because the operator reads the render and
    never calls the function."""
    from scripts.utils.canopus_friction import (
        FRICTION_HEADING,
        count_friction,
        render_friction,
    )

    unrecorded = render_friction(count_friction([], label="s"))
    recorded = render_friction(count_friction([_row("freeze")], label="s"))

    assert "no ledger entries" in unrecorded.lower()
    assert "no ledger entries" not in recorded.lower()

    # Both branches open with the heading. A mutation dropped it and stayed
    # green: the numbers reached the page under no title, which on a report whose
    # every other section is titled reads as part of the section above.
    for text in (unrecorded, recorded):
        assert text.splitlines()[0].strip().endswith(FRICTION_HEADING), (
            f"the section does not open with {FRICTION_HEADING!r}: "
            f"{text.splitlines()[0]!r}"
        )


# ============================================================
# The page states the boundary, every time
# ============================================================

def test_the_render_always_states_that_the_count_is_a_floor():
    """`.canopus/` is gitignored and deletable, so every count here is a floor. A
    page that prints `windows 0` without that sentence is making a stronger claim
    than the data supports, which is the defect this whole section exists to stop
    making elsewhere."""
    from scripts.utils.canopus_friction import count_friction, render_friction

    for rows in ([], [_row("freeze")],
                 [_row("freeze"), _row("release", kind="window")]):
        text = render_friction(count_friction(rows, label="s")).lower()
        assert "floor" in text or "at least" in text, (
            "the render omits the floor caveat for rows=%r" % rows)


def test_the_render_never_grades_the_slice():
    """Report, do not judge. `production-shape` earned its five windows by
    finding five real problems; a page that calls that "poor" teaches the builder
    to avoid windows, which is exactly backwards."""
    from scripts.utils.canopus_friction import count_friction, render_friction

    text = render_friction(count_friction(
        [_row("freeze")] + [_row("release", kind="window")] * 9,
        label="s")).lower()
    for verdict in ("poor", "bad", "excessive", "too many", "warning",
                    "concerning"):
        assert verdict not in text, f"the render grades the slice: {verdict!r}"


# ============================================================
# Wired into the page an operator actually approves from
# ============================================================

# ============================================================
# The reader it depends on is not weakened
# ============================================================

def test_a_corrupt_ledger_line_costs_only_that_line(tmp_path):
    """`read_ledger` already skips damaged lines rather than raising, because the
    ledger is evidence and nine readable entries beat a traceback. The counter
    must inherit that, not undo it."""
    from scripts.utils.canopus_freeze import read_ledger
    from scripts.utils.canopus_friction import count_friction

    root = tmp_path / "ws"
    (root / ".canopus").mkdir(parents=True)
    (root / ".canopus" / "history.jsonl").write_text(
        json.dumps(_row("release", kind="window")) + "\n"
        + "{ not json\n"
        + json.dumps(_row("anchor_replaced")) + "\n",
        encoding="utf-8")

    counts = count_friction(read_ledger(root), label="s")
    assert counts.windows == 1
    assert counts.retakes == 1


def test_the_counter_never_raises_on_a_malformed_entry():
    """A dict missing `event`, `kind` or `label` is readable JSON and so survives
    `read_ledger`. It must not take the page down: this renders on the approval
    path, where a traceback costs the operator the whole page."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([{}, {"event": None}, {"label": "s"},
                             {"event": "release", "label": "s"}], label="s")
    assert counts.windows == 0
    assert counts.recorded is False
