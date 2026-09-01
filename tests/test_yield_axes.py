"""The two axes of gate yield: a wall and a gate are not the same kind of thing.

Promoted whole from `tests/contract/2026-08-03-yield-axes/` when the yield-axes
slice shipped on 2026-08-03. All 18 IDs kept, none dropped; the only change was
the repository-root constant, which moved with the file.

Eight of those IDs are gone as of 2026-08-07. SC-3, SC-3b, SC-3c, SC-4, SC-4b,
SC-4c, SC-4d and SC-5 all had their subject inside the LIFECYCLE half of
`gate_yield`: the closed retake vocabulary, the counter over `anchor_replaced`
rows, and the committed hand classification that supplied a class for rows
predating the declared field. The freeze lifecycle that wrote those rows was
deleted, so nothing produces them and no window of any length can. The wall/gate
axis below is untouched, and it is the half that measures something.

`gate-yield.py` measured every mechanism with one instrument, and the two things
it measured were not the same kind of thing. The consequence, measured on
2026-08-03:

**It nominates walls for removal.** Every guard in `DENIAL_MECHANISMS` reaches
`NO_YIELD` and the FLAGGED list the moment its window passes 31 days. For the
secret scanner on the push path of a PUBLIC repository that verdict is true and
useless: zero catches is the success condition, and the loss function is
asymmetric and unbounded, one miss publishing a live credential irreversibly. The
criterion that replaces catch counts for this class: a zero-catch mechanism may
be removed only when its loss function is symmetric and bounded, or when the
protected asset or threat ceases to exist structurally. Never on catch counts.

WHAT THIS CONTRACT REFUSES TO ALLOW, each pinned by test rather than promised:

- A wall may never be FLAGGED, and no window length may change that.
- An UNCLASSIFIED mechanism is treated as a wall, not as a gate. The unsafe
  direction here is flagging something for removal that nobody classified; the
  safe direction costs only a missing verdict.

Every test imports the code under test INSIDE its body.
"""

def _denial(mechanism, ts=1785000000.0, reason="r"):
    return {"mechanism": mechanism, "ts": ts, "reason": reason}


# ============================================================
# SC-1 -- a wall is never judged by yield
# ============================================================

def test_a_wall_never_reaches_the_no_yield_verdict_at_any_window():
    """SC-1. WHEN a mechanism's loss function is declared asymmetric, THE SYSTEM
    SHALL report a verdict that is never NO_YIELD, at any window length.

    Asserted at 10x the budget window, because the defect is not that the current
    window is short. It is that the verdict exists for this class at all.
    """
    from scripts.utils.gate_yield import (
        BUDGET_DAYS,
        NO_YIELD,
        SOURCE_DENIALS,
        WALLS,
        summarise,
    )

    assert WALLS, "no mechanism is declared a wall, so the split does not exist"

    summary = summarise(
        denials=[],
        since={SOURCE_DENIALS: "2020-01-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00")

    for name in WALLS:
        entry = summary["mechanisms"][name]
        assert entry["days"] > BUDGET_DAYS * 10, (
            f"the window for {name} is not long enough to prove the point")
        assert entry["verdict"] != NO_YIELD, (
            f"{name} is a wall and still reached {NO_YIELD} after "
            f"{entry['days']} days")


def test_the_flagged_list_can_never_contain_a_wall():
    """SC-1b. Same claim at the render layer, because FLAGGED is the line the
    operator reads and the function is not what he reads."""
    from scripts.utils.gate_yield import SOURCE_DENIALS, WALLS, render, summarise

    summary = summarise(
        denials=[],
        since={SOURCE_DENIALS: "2020-01-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00")
    text = render(summary, now="2026-08-03T00:00:00+00:00")

    flagged = text.split("FLAGGED:")[1] if "FLAGGED:" in text else ""
    for name in WALLS:
        assert name not in flagged, f"the wall {name} appears in the FLAGGED list"


def test_the_secret_scanner_and_the_push_walls_are_declared_walls():
    """SC-1c. The declaration is not a free-form list somebody may drain. These
    four are the push-path guards on a PUBLIC repository, and each one's single
    miss is irreversible."""
    from scripts.utils.gate_yield import WALLS

    for name in ("secret-scanner", "push:engine-content-scan",
                 "push:secret-tracked-files", "check_prevent_secrets"):
        assert name in WALLS, f"{name} is not declared a wall"


def test_a_session_gate_is_NOT_a_wall_and_can_still_be_judged():
    """SC-1d. The split is by LOSS FUNCTION, not by which log a mechanism writes
    to. A session gate writes to the denial log like every wall does, and its
    loss function is symmetric and bounded: under-friction costs rework,
    over-friction costs time. Making the split follow the log would have swept
    them in and made them unjudgeable, which is the opposite of what v2 §5 needs.

    Re-pointed on 2026-08-07: the two names this asserted over were the Canopus
    commit gate and its PreToolUse sibling, both deleted. The property is
    unchanged and needs a mechanism that still exists.
    """
    from scripts.utils.gate_yield import GATES, WALLS

    for name in GATES:
        assert name not in WALLS, f"{name} is a gate and a wall at once"
    assert "check_tool_budget" in GATES


# ============================================================
# SC-2 -- an unclassified mechanism fails SAFE
# ============================================================

def test_the_json_row_does_not_carry_a_field_nobody_reads():
    """SC-1e, inverted on 2026-09-01. The field was dropped; this pins that.

    `summarise` used to stamp `entry["wall"] = is_wall(name)` for a `--json`
    consumer. The 2026-09-01 audit established there has never been such a
    consumer: `scripts/gate-yield.py --json` prints to stdout, and nothing in
    `scripts/`, `.claude/`, `tests/`, `docs/` or `config/` captures, pipes or
    parses that output. Operator decision the same day: drop it.

    This test replaces the one that pinned the field's PRESENCE. It is written
    as an absence check rather than simply deleted, because an unread field is
    exactly the kind of thing that gets re-added by someone who reads the
    comment explaining what it was for and mistakes it for a requirement. If it
    is being restored deliberately, restore a READER in the same commit and say
    where the output goes.

    ## Why the removal is safe

    The property the field carried is untouched. A wall that HAS caught
    something reads CATCHING exactly like an ordinary gate, so a consumer
    reading verdicts alone cannot tell that a wall must never be judged by its
    catch count. That rule lives in `_verdict`, through `is_wall`, and is pinned
    by `test_a_wall_never_reaches_the_no_yield_verdict_at_any_window` two
    functions above. `is_wall` stays public for any future consumer. Only the
    export went.

    ## The anti-vacuity jaw

    An absence assertion is satisfied by a summary with no rows at all, by a
    `summarise` that raised and returned nothing, and by a row shape that lost
    every field. So the fields that MUST still be there are asserted first, over
    the same wall-with-a-catch and gate-without-one fixture the old test used.
    Without those three lines this test passes over an empty dict, which is the
    single most common defect the 2026-08-31 audit found across this suite.
    """
    from scripts.utils.gate_yield import (
        CATCHING,
        GATES,
        SOURCE_DENIALS,
        WALLS,
        summarise,
    )

    a_wall = WALLS[0]
    a_gate = GATES[0]
    summary = summarise(
        denials=[_denial(a_wall)],
        since={SOURCE_DENIALS: "2020-01-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00")

    rows = summary["mechanisms"]
    # The jaw: this test means nothing over an empty or gutted summary.
    assert rows, "the summary named no mechanism at all"
    assert rows[a_wall]["verdict"] == CATCHING, (
        "the wall fixture did not reach CATCHING, so this is no longer the "
        "case in which the verdict loses the wall/gate split, and the absence "
        "check below is being made over the wrong scenario")
    assert a_gate in rows, f"the gate fixture {a_gate} produced no row"

    offenders = sorted(name for name, entry in rows.items() if "wall" in entry)
    assert not offenders, (
        f"row(s) {offenders} carry a `wall` key again. It was removed on "
        f"2026-09-01 because no consumer in the tree reads `--json` output at "
        f"all. If a reader now exists, name it here and restore the field with "
        f"a test that exercises the reader, not the writer.")


def test_an_unknown_mechanism_is_treated_as_a_wall_not_as_a_gate():
    """SC-2. WHEN a mechanism appears in a log but in no classification, THE
    SYSTEM SHALL treat it as a wall.

    The two failure directions are not symmetric. Treating an unclassified guard
    as a gate lets the report FLAG something nobody ever classified, and FLAGGED
    is the input to a removal decision. Treating it as a wall costs one missing
    verdict.

    Asserted on the PREDICATE, and this test earned that shape. Its first version
    drove `summarise` with one denial row for an undeclared mechanism and checked
    the verdict was not NO_YIELD. It passed against the unchanged code -- because
    a mechanism with one catch reads CATCHING, so the assertion was satisfied by
    the row rather than by any fail-safe, and an implementation treating unknown
    names as gates would have passed it too. Caught by the probe at step 4, which
    is what the probe is for.
    """
    from scripts.utils.gate_yield import is_wall

    assert is_wall("some-guard-nobody-declared") is True, (
        "an undeclared mechanism is not failing safe")


def test_every_declared_mechanism_is_classified_so_the_fail_safe_never_fires():
    """SC-2b. The fail-safe above is a net, not a plan. Every name the report
    declares must sit on one side of the split deliberately, or the split is
    being done by the default rather than by anybody's judgement."""
    from scripts.utils.gate_yield import DENIAL_MECHANISMS, GATES, WALLS

    classified = set(WALLS) | set(GATES)
    for name in tuple(DENIAL_MECHANISMS):
        assert name in classified, (
            f"{name} is declared in the report but classified on neither side; "
            f"it would fall through to the fail-safe and never be judgeable")


def test_a_real_denial_written_by_the_writer_flows_through_the_split():
    """SC-2c. The fixtures above invent the denial record's shape. This one does
    not: it writes through `log_denial()`, reads back through `read_denials()`,
    and drives the split with whatever the writer actually emits.

    The gate refused this contract at `approve` for exactly this gap -- "every
    fixture for that store is invented and nothing compares it to the shape the
    writer emits" -- and it was right. Without this test, a change to the record's
    field names leaves sixteen green tests and a broken report.

    Keyed on a marker unique to this call rather than on a count, because the
    suite's isolated log is shared across xdist workers and roughly 1300 foreign
    records arrive in it during a run.
    """
    import os
    import time

    from scripts.utils.denial_log import log_denial, read_denials
    from scripts.utils.gate_yield import NO_YIELD, is_wall, summarise

    marker = f"yield-axes-probe-{os.getpid()}-{time.time_ns()}"
    assert log_denial(mechanism=marker, action="test",
                      path="tests/contract/2026-08-03-yield-axes/test_contract.py",
                      reason="written by the yield-axes contract") is True

    written = [r for r in read_denials() if r.get("mechanism") == marker]
    assert written, "the record this test wrote is absent from the suite's log"

    summary = summarise(
        denials=written,
        since={"denials": "2020-01-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00")

    entry = summary["mechanisms"][marker]
    assert entry["caught"] == 1, (
        "the writer's real record did not reach the counter; the invented "
        "fixtures above are describing a shape that no longer exists")
    assert is_wall(marker) is True
    assert entry["verdict"] != NO_YIELD


# ============================================================
# SC-5 -- the report still cannot pronounce a subtraction
# ============================================================

def test_the_render_still_cannot_recommend_a_removal():
    """SC-5b. The property `render` already holds must survive this slice. It is
    the reason the report is safe to read, and a new section is the classic way
    such a property gets lost."""
    from scripts.utils.gate_yield import FORBIDDEN_VERBS, render, summarise

    summary = summarise(
        denials=[],
        since={"denials": "2020-01-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00")
    text = render(summary, now="2026-08-03T00:00:00+00:00").lower()
    for verb in FORBIDDEN_VERBS:
        assert verb not in text, f"the report can now say {verb!r}"


def test_the_wall_declaration_carries_its_reason_not_just_its_name():
    """SC-5c. A bare list of names is a list somebody drains in six months
    without knowing what each entry was for. Each wall carries the sentence that
    makes it a wall, and the page can print it."""
    from scripts.utils.gate_yield import WALL_REASONS, WALLS

    assert set(WALL_REASONS) == set(WALLS), (
        "a wall exists with no stated reason, or a reason with no wall")
    for name, why in WALL_REASONS.items():
        assert len(why) > 20, f"the reason for {name} says nothing: {why!r}"
