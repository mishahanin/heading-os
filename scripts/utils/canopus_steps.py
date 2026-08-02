#!/usr/bin/env python3
"""The thirteen moments of a Canopus slice, as data.

The lifecycle engine (`scripts/canopus.py`) enforces the machine-checkable parts
of this. This module holds what the OPERATOR sees: the numbered moments, which
act each belongs to, what happens at each, and which of them the machine can
actually detect. Data, not behaviour, so the engine stays lean and the agenda
has exactly one definition.

**Why thirteen and not eleven.** The two approvals used to sit between the acts
as unnumbered gates, so a counter reading "step 3 of 11" made the operator's own
two moments invisible in the count. Numbering them (operator's decision,
2026-08-02) puts them in the sequence, and each act the operator takes part in
now ENDS with his step. It also splits two things that were quietly one: his
word (12) and the work that closes the slice (13), which is what actually
happens -- the release command, then retiring the contract and writing down
what undoing the slice would mean.

**What the machine can and cannot see.** Six of the thirteen leave a durable
trace: 6 (a committed gate artifact), 7 (a freeze manifest), 9 (an attestation),
12 and 13 (ledger events), and 4 in the weak sense that contract files exist.
The rest are human work that no file records. `machine_visible` says which is
which, so a position display can report what is known and say plainly where it
is guessing rather than inventing precision.
"""

ACT_DECIDE = "Decide"
ACT_BUILD = "Build"
ACT_CHECK = "Check"
ACT_RELEASE = "Release"

ACTS = (
    {"number": 1, "name": ACT_DECIDE, "steps": (1, 6),
     "note": "nothing is built yet"},
    {"number": 2, "name": ACT_BUILD, "steps": (7, 9),
     "note": "no human inside"},
    {"number": 3, "name": ACT_CHECK, "steps": (10, 12),
     "note": "green is not the same as right"},
    {"number": 4, "name": ACT_RELEASE, "steps": (13, 13),
     "note": "the undo is named before it is needed"},
)

# number, name, what happens, act, is this the operator's own moment, and
# whether any file on disk records that it happened.
STEPS = (
    {"number": 1, "act": 1, "approval": False, "machine_visible": False,
     "name": "Say what we want",
     "what": "State what would be worth having. The sentence is kept; step 10 "
             "measures against it."},
    {"number": 2, "act": 1, "approval": False, "machine_visible": False,
     "name": "Decide what to build",
     "what": "Choose what should be built, not how to build what was asked."},
    {"number": 3, "act": 1, "approval": False, "machine_visible": False,
     "name": "Write the plan",
     "what": "Steps, files, risks. Every touched file checked against the "
             "security findings registry first."},
    {"number": 4, "act": 1, "approval": False, "machine_visible": True,
     "name": "Write the test that decides",
     "what": "The actual check, in real test files. Not a description of one."},
    {"number": 5, "act": 1, "approval": False, "machine_visible": False,
     "name": "Try to break the plan",
     "what": "Adversarial pass over the plan, repeated until it returns nothing."},
    {"number": 6, "act": 1, "approval": True, "machine_visible": True,
     "name": "Approval 1 - the plan and the test",
     "what": "Yours. No code exists yet, so this is the cheapest moment to "
             "change anything. Committing the gate artifact IS the approval."},
    {"number": 7, "act": 2, "approval": False, "machine_visible": True,
     "name": "Lock the test",
     "what": "A hash manifest over the frozen paths. From here the test cannot "
             "move under the code."},
    {"number": 8, "act": 2, "approval": False, "machine_visible": False,
     "name": "Write the code",
     "what": "The builder works against the locked test."},
    {"number": 9, "act": 2, "approval": False, "machine_visible": True,
     "name": "Machine checks it",
     "what": "The verdict is mechanical: the locked tests pass, none deselected, "
             "bound to a commit."},
    {"number": 10, "act": 3, "approval": False, "machine_visible": False,
     "name": "Check it's what we wanted",
     "what": "Against step 1. The only place 'passed but wrong' is visible."},
    {"number": 11, "act": 3, "approval": False, "machine_visible": False,
     "name": "Try to break it",
     "what": "Adversarial review of the built thing, converging under its own "
             "termination rule."},
    {"number": 12, "act": 3, "approval": True, "machine_visible": True,
     "name": "Approval 2 - the finished work",
     "what": "Yours. On the evidence, never a summary, including what the test "
             "does NOT cover."},
    {"number": 13, "act": 4, "approval": False, "machine_visible": True,
     "name": "Release it, with the undo named in advance",
     "what": "Retire the contract into the ordinary suite, and write down what "
             "undoing this slice would actually mean: which commit to revert, "
             "which baseline to restore, what to re-run. Named BEFORE it is "
             "needed, because the moment you need it is the worst moment to "
             "invent it."},
)

# Terms that replaced the navigational metaphors, kept here so one file answers
# "what do we call this".
VOCABULARY = (
    ("Approval", "was: taking a fix"),
    ("Lock the test", "was: lock on Canopus"),
    ("The test moved", "was: loss of lock"),
    ("Passed but wrong", "was: star hopping"),
    ("Start fresh", "was: re-acquisition"),
)


def step(number: int) -> dict:
    """One moment by number, or None."""
    for entry in STEPS:
        if entry["number"] == number:
            return entry
    return None


def act(number: int) -> dict:
    for entry in ACTS:
        if entry["number"] == number:
            return entry
    return None


def act_of(step_number: int) -> dict:
    found = step(step_number)
    return act(found["act"]) if found else None


def approvals() -> tuple:
    """The operator's own moments, in order."""
    return tuple(s for s in STEPS if s["approval"])


# The ladder from a machine-visible trace to a numbered moment. Three rungs,
# because there are only three states the disk can be in, and each rung names
# the trace that puts you on it.
NO_SLICE = 0


def position(*, label, attested: bool) -> dict:
    """Which moment the slice is on, given only what the machine can see.

    PURE, and separate from the disk on purpose. It lived inside the CLI until
    2026-08-02, where the only way to reach the attested rung from a test was to
    fabricate a qualifying attestation record on disk -- which would have welded
    the position contract to the attestation machinery's shape, two things that
    change for unrelated reasons. Mutation measured the cost of that: changing
    the derived step from 8 to 3 killed no test at all.

    `derived` says whether the step itself was observed or inferred from a
    NEIGHBOURING trace, and `basis` says in prose which trace that was. Steps 8,
    10 and 11 leave nothing on disk, so the honest answer at those is "the
    earliest unfinished moment", not a measurement. A confident "step 10 of 13"
    where nothing is knowable is a lie the operator would reasonably act on,
    which is worse than an admitted gap.
    """
    if label is None:
        return {
            "slice": None,
            "number": NO_SLICE,
            "derived": False,
            "basis": "no freeze is held, so no slice is open. This is observed, "
                     "not inferred: the absence of a lock is itself a fact.",
        }
    if attested:
        return {
            "slice": label,
            "number": 10,
            "derived": True,
            "basis": "the freeze carries an attestation, so step 9 (the "
                     "machine's own verdict) has passed. Steps 10 and 11 leave "
                     "no trace on disk, so this is the earliest unfinished "
                     "moment rather than a measured one.",
        }
    return {
        "slice": label,
        "number": 8,
        "derived": True,
        "basis": "a freeze is held (step 7) and nothing has attested it yet. "
                 "Writing code leaves no trace this tool can read, so step 8 is "
                 "inferred from the lock, not measured.",
    }
