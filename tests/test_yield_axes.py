"""The two axes of gate yield: a wall and a gate are not the same kind of thing.

Promoted whole from `tests/contract/2026-08-03-yield-axes/` when the yield-axes
slice shipped on 2026-08-03. All 18 IDs kept, none dropped; the only change is
`_ROOT`, which moved from `parents[3]` to `parents[1]` with the file.

Two things it deliberately does NOT cover, both named at step 12 rather than
discovered later. The CLI wiring of `--cause` is proven by
`tests/test_canopus_cli.py`, not here: mutations M11 and M12 (approve accepting a
retake with no cause; the cause never reaching the ledger) SURVIVE this file
alone, because it pins `retake_cause_or_error` as a function and nothing in it
reaches `cmd_approve`. And nothing here makes the hand classification RIGHT; it
makes it auditable.

`gate-yield.py` measures every mechanism with one instrument, and the two things
it measures are not the same kind of thing. Two consequences, both measured on
2026-08-03 and both wrong in the same report:

1. **It nominates walls for removal.** Every guard in `DENIAL_MECHANISMS`
   reaches `NO_YIELD` and the FLAGGED list the moment its window passes 31 days.
   For the secret scanner on the push path of a PUBLIC repository that verdict is
   true and useless: zero catches is the success condition, and the loss function
   is asymmetric and unbounded, one miss publishing a live credential
   irreversibly. The criterion that replaces catch counts for this class:
   a zero-catch mechanism may be removed only when its loss function is symmetric
   and bounded, or when the protected asset or threat ceases to exist
   structurally. Never on catch counts.

2. **It undercounts the gate's largest output.** Over the 39 `anchor_replaced`
   records in the lifecycle ledger, classified by hand at step 8: 14 were a frozen
   contract that turned out WEAK and was strengthened, 21 were the enforcer bytes
   moving, 4 lint debt. The report says the whole lifecycle caught FIVE things.
   Fourteen of those retakes are the mechanism doing the thing v3 says the
   standard is for, and no instrument sees a single one of them.

   The ratio is the claim; the exact 14 is not. An earlier hand pass over the
   same records said 17, and the gap between two passes by one classifier is the
   reason SC-4c below asserts coverage rather than any total.

WHAT THIS CONTRACT REFUSES TO ALLOW, each pinned by test rather than promised:

- A wall may never be FLAGGED, and no window length may change that.
- An UNCLASSIFIED mechanism is treated as a wall, not as a gate. The unsafe
  direction here is flagging something for removal that nobody classified; the
  safe direction costs only a missing verdict.
- A retake's cause is a DECLARED field with a closed vocabulary, never a
  substring of a human sentence. `scripts/utils/canopus_friction.py` refused
  exactly this for waivers, on the record, because "a counter built on a
  substring lies quietly the first time somebody rewords their reason." The same
  refusal binds here.
- History that was classified BY HAND says so on the page. Every record that
  makes this instrument correct on the historical axis was classified by
  judgement and cannot be re-derived from anything; a report that presents them
  as measured would be making the stronger claim this whole slice exists to stop
  making.

Every test imports the code under test INSIDE its body.
"""

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _row(event, label="s", kind="", reason="", ts="2026-08-03T00:00:00+00:00"):
    return {"event": event, "label": label, "kind": kind, "reason": reason,
            "ts": ts, "root": "d" * 64}


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
        ledger=[], denials=[],
        since={SOURCE_DENIALS: "2020-01-01T00:00:00+00:00",
               "lifecycle": "2020-01-01T00:00:00+00:00"},
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
        ledger=[], denials=[],
        since={SOURCE_DENIALS: "2020-01-01T00:00:00+00:00",
               "lifecycle": "2020-01-01T00:00:00+00:00"},
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


def test_the_depth_gate_is_NOT_a_wall_and_can_still_be_judged():
    """SC-1d. The split is by LOSS FUNCTION, not by which log a mechanism writes
    to. The depth classifier writes to the denial log like every wall does, and
    its loss function is symmetric and bounded: under-ceremony costs rework,
    over-ceremony costs time. Making the split follow the log would have swept it
    in and made it unjudgeable, which is the opposite of what v2 §5 needs."""
    from scripts.utils.gate_yield import WALLS

    assert "depth-gate" not in WALLS
    assert "check_canopus_freeze" not in WALLS


# ============================================================
# SC-2 -- an unclassified mechanism fails SAFE
# ============================================================

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
    from scripts.utils.gate_yield import (
        DENIAL_MECHANISMS,
        GATES,
        MECHANISMS,
        WALLS,
    )

    classified = set(WALLS) | set(GATES)
    for name in tuple(DENIAL_MECHANISMS) + tuple(MECHANISMS):
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
        ledger=[], denials=written,
        since={"denials": "2020-01-01T00:00:00+00:00",
               "lifecycle": "2020-01-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00")

    entry = summary["mechanisms"][marker]
    assert entry["caught"] == 1, (
        "the writer's real record did not reach the counter; the invented "
        "fixtures above are describing a shape that no longer exists")
    assert is_wall(marker) is True
    assert entry["verdict"] != NO_YIELD


# ============================================================
# SC-3 -- a retake declares its cause; prose is never parsed
# ============================================================

def test_a_retake_cause_comes_from_a_closed_vocabulary():
    """SC-3. WHEN a retake is recorded, THE SYSTEM SHALL accept only a declared
    cause from a closed set."""
    from scripts.utils.gate_yield import RETAKE_CAUSES

    assert "contract-strengthened" in RETAKE_CAUSES
    assert "enforcer-moved" in RETAKE_CAUSES
    assert isinstance(RETAKE_CAUSES, frozenset), (
        "the vocabulary must be closed, so a caller cannot append to it")


def test_approve_replace_refuses_a_retake_with_no_declared_cause(tmp_path):
    """SC-3b. WHEN `approve --replace` is invoked without a declared cause, THE
    SYSTEM SHALL refuse the retake.

    The refusal is the whole mechanism. An optional field on a command the
    assistant types is a field that is present when convenient, and the resulting
    count is then a count of the times somebody remembered.
    """
    from scripts.utils.gate_yield import retake_cause_or_error

    err = retake_cause_or_error(None)
    assert err, "a missing cause was accepted"
    assert "cause" in err.lower()

    assert retake_cause_or_error("not-a-real-cause"), "an unknown cause was accepted"
    assert retake_cause_or_error("contract-strengthened") == "", (
        "a declared cause was refused")


def test_the_cause_is_never_derived_from_the_free_text_reason():
    """SC-3c. A reason whose PROSE says the contract was strengthened, carrying
    no declared cause, must not be counted as a strengthening.

    `canopus_friction.py` refused exactly this for waivers and said why: a
    counter built on a substring lies quietly the first time somebody rewords.
    The same refusal binds here, and this test is what makes it binding rather
    than remembered.
    """
    from scripts.utils.gate_yield import count_retakes

    prose_only = [_row("anchor_replaced", label="s",
                       reason="the contract was strengthened after a mutation "
                              "survived; SC-4 claimed more than it checked")]
    counts = count_retakes(prose_only, hand_classified={})
    assert counts.get("contract-strengthened", 0) == 0, (
        "a cause was inferred from prose")
    assert counts.get("unclassified", 0) == 1, (
        "the record should count as unclassified, not vanish")


# ============================================================
# SC-4 -- contract strengthening is a first-class yield class
# ============================================================

def test_a_declared_strengthening_is_counted_as_yield():
    """SC-4. WHEN the ledger carries retakes with a declared cause, THE SYSTEM
    SHALL count contract strengthening as a first-class class."""
    from scripts.utils.gate_yield import count_retakes

    rows = [_row("anchor_replaced", kind="contract-strengthened"),
            _row("anchor_replaced", kind="contract-strengthened"),
            _row("anchor_replaced", kind="enforcer-moved")]
    counts = count_retakes(rows, hand_classified={})
    assert counts["contract-strengthened"] == 2
    assert counts["enforcer-moved"] == 1


def test_the_hand_classified_history_supplies_records_that_predate_the_field(tmp_path):
    """SC-4b. WHEN a retake predates the declared-cause field, THE SYSTEM SHALL
    take its class from a committed hand classification keyed by record identity.

    Without this the correct denominator does not exist until a fresh population
    of retakes accumulates, which at the measured rate is months. With it, the 39
    already in the ledger count -- and the next test makes the report admit how
    they got there.
    """
    from scripts.utils.gate_yield import count_retakes

    rows = [_row("anchor_replaced", label="old-slice",
                 ts="2026-07-26T10:00:00+00:00")]
    hand = {"2026-07-26T10:00:00+00:00|old-slice": "contract-strengthened"}
    counts = count_retakes(rows, hand_classified=hand)
    assert counts["contract-strengthened"] == 1


def test_the_committed_history_file_classifies_every_retake_in_the_ledger():
    """SC-4c. The file is the point of the slice, not a fixture.

    This criterion has now asserted a magnitude twice and been wrong twice, and
    the second time is why it no longer asserts one at all.

    Version 1 demanded at least THIRTY strengthenings, from a count over 62
    records that mixed windows with retakes and so counted single corrections
    twice. Window 1 replaced it with 17/18/3, from a hand pass over the 38
    `anchor_replaced` records alone. Re-deriving that pass at step 8, from the
    same ledger, one record at a time, gave **14 strengthened, 21 enforcer-moved,
    4 lint** over 39 records. Two hand classifications of one dataset by one
    classifier, three records apart. That is not a data problem to be tuned away;
    it is the direct evidence for C1 in the gate artifact, that a hand
    classification is judgement and must never be asserted as if it were
    measurement.

    So what is asserted here is COVERAGE and SHAPE, and the coverage is computed
    from the live ledger rather than from a number typed into this file. Every
    retake carrying no structural cause must be classified, which is a stronger
    claim than any total and one that cannot go stale: it fails the moment a
    pre-field retake exists with no entry. Records that DO carry a structural
    cause need no entry, because the bridge exists only for history.
    """
    from scripts.utils.canopus_freeze import read_ledger
    from scripts.utils.gate_yield import (
        RETAKE_CAUSES,
        load_hand_classified,
        retake_key,
    )

    hand = load_hand_classified(_ROOT)

    unclassified = [
        retake_key(row) for row in read_ledger(_ROOT)
        if row.get("event") == "anchor_replaced"
        and row.get("kind") not in RETAKE_CAUSES
        and retake_key(row) not in hand
    ]
    assert not unclassified, (
        f"{len(unclassified)} retake(s) in the ledger carry neither a declared "
        f"cause nor a hand classification, so the denominator is incomplete: "
        f"{unclassified[:5]}")

    assert set(hand.values()) <= set(RETAKE_CAUSES)
    strengthened = [k for k, v in hand.items() if v == "contract-strengthened"]
    assert strengthened, (
        "not one retake in the whole recorded history is classified as a "
        "contract strengthening, which would make this slice's premise false")


def test_a_declared_cause_beats_the_hand_classification_for_the_same_record():
    """SC-4d. The hand file is a bridge for history, never an override. If both
    exist for one record the structural field wins, or the file becomes a way to
    restate the present."""
    from scripts.utils.gate_yield import count_retakes

    rows = [_row("anchor_replaced", label="s", kind="enforcer-moved",
                 ts="2026-08-03T00:00:00+00:00")]
    hand = {"2026-08-03T00:00:00+00:00|s": "contract-strengthened"}
    counts = count_retakes(rows, hand_classified=hand)
    assert counts["enforcer-moved"] == 1
    assert counts.get("contract-strengthened", 0) == 0


# ============================================================
# SC-5 -- the page admits what was classified by hand
# ============================================================

def test_the_report_says_how_many_records_were_classified_by_hand():
    """SC-5. WHEN the yield figure draws on the hand classification, THE SYSTEM
    SHALL say so on the page, with the count.

    A number that is 30 parts judgement and 0 parts measurement, printed beside
    numbers that are the reverse, is exactly the overstatement this slice exists
    to remove from the report.
    """
    from scripts.utils.gate_yield import render, summarise

    summary = summarise(
        ledger=[_row("anchor_replaced", label="old",
                     ts="2026-07-26T10:00:00+00:00")],
        denials=[],
        since={"lifecycle": "2026-07-25T00:00:00+00:00",
               "denials": "2026-08-01T00:00:00+00:00"},
        now="2026-08-03T00:00:00+00:00",
        hand_classified={"2026-07-26T10:00:00+00:00|old": "contract-strengthened"})
    text = render(summary, now="2026-08-03T00:00:00+00:00")

    # The count and the admission on ONE line. The first version of this
    # assertion checked `"1" in text` anywhere on the page, which the date in the
    # header already satisfies; it would have passed against a report that said
    # "classified by hand" and named no number at all.
    admission = [ln for ln in text.splitlines() if "by hand" in ln.lower()]
    assert admission, "the page never admits that anything was classified by hand"
    assert any("1" in ln for ln in admission), (
        f"the admission carries no count: {admission}")


def test_the_render_still_cannot_recommend_a_removal():
    """SC-5b. The property `render` already holds must survive this slice. It is
    the reason the report is safe to read, and a new section is the classic way
    such a property gets lost."""
    from scripts.utils.gate_yield import FORBIDDEN_VERBS, render, summarise

    summary = summarise(
        ledger=[], denials=[],
        since={"lifecycle": "2020-01-01T00:00:00+00:00",
               "denials": "2020-01-01T00:00:00+00:00"},
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


def test_the_committed_history_file_is_valid_json_and_uses_declared_causes():
    """SC-5d. The bridge file is data the report trusts. A typo'd cause in it
    would create a silent bucket nobody counts.

    Asserted against the RAW file, never through `load_hand_classified`. The
    loader DROPS any entry whose cause is not in `RETAKE_CAUSES` — that is the
    right posture for a reporter that must render over damaged history, and it
    is the wrong lens for this test: a loop over the loader's output asking
    `cause in RETAKE_CAUSES` cannot fail, because the loader already removed
    every value that would have failed it. Measured, not reasoned: writing
    `contract-strengthend` over one entry in a copy of the file left 38 loaded
    entries instead of 39 and not one undeclared cause for the loop to see.
    That is the exact stub-satisfiable shape this slice spent step 11 closing in
    `cmd_repin`, in the test written to guard the file the slice is named after.

    The two lenses are both kept, and they answer different questions: the raw
    pass is whether the committed data is well-formed, the loader pass is
    whether anything survives it at all.
    """
    from scripts.utils.gate_yield import RETAKE_CAUSES, load_hand_classified

    raw = json.loads((_ROOT / "config" / "canopus-retake-history.json")
                     .read_text(encoding="utf-8"))
    assert isinstance(raw, dict)

    for key, value in raw.items():
        # The same `_`-prefixed prose entries the loader steps over. JSON has no
        # comments, so the file documents itself in keys nothing reads back.
        if str(key).startswith("_"):
            continue
        assert "|" in key, f"the key {key!r} is not ts|label"
        cause = value.get("cause") if isinstance(value, dict) else value
        assert cause in RETAKE_CAUSES, f"{key} carries undeclared cause {cause!r}"

    # And every well-formed entry survives the loader, so a file that passes the
    # pass above is a file the report can actually count.
    hand = load_hand_classified(_ROOT)
    assert isinstance(hand, dict) and hand
    assert len(hand) == len([k for k in raw if not str(k).startswith("_")]), (
        "the loader dropped an entry the raw pass accepted")
