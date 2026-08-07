#!/usr/bin/env python3
"""The seven steps of a Canopus slice, as data.

This module holds what the OPERATOR sees: the numbered steps, which act each
belongs to, what happens at each, and which of them the machine can actually
detect. Data, not behaviour, so the agenda has exactly one definition and the
skill that describes it can summarise but never renumber.

**Why seven and not thirteen.** The thirteen moments were four acts of ceremony
around two things git already provides. The freeze is a commit: the approval
commit carries the plan and the RED contract, and `git diff` against its sha
answers whether the contract moved. The separation is a dispatch:
`superpowers:subagent-driven-development` sends a fresh implementer per task
plus a reviewer, so the entity that decides what "done" means is not the entity
that decides it is done. Everything the retired agenda numbered around those two
facts was removed on 2026-08-06.

**What the machine can and cannot see.** Two of the seven leave a durable trace
in this repository: 4 (the approval commit, and the contract files it carries)
and 7 (the note under `records/slices/`). The rest are human and agent work that
no file here records; the scope document and the plan live in the operator's
private overlay and are referenced by digest, never by path, because this
repository is public. `machine_visible` says which is which, so a reader of the
agenda can tell the two steps this repository can evidence from the five it
cannot.
"""

# The plan's own byte budget, checked at step 3 property 2. A PROPOSAL WITH
# OPERATOR OVERRIDE, never a gate the way SKILL.md's own budget is: nothing here
# refuses a commit. Measured across 99 real plans in the operator's private
# overlay: min 336 bytes, p25 13,911, median 23,704, p75 35,200, max 164,805.
# PLAN_BYTE_WARN mirrors the SKILL.md warn `skill-metadata-check.py` already
# enforces, so the workspace carries one number rather than two. PLAN_BYTE_HARD
# is 24 KiB, the first binary-round number above the measured median, clearing
# it by 872 bytes; 51 of the 99 plans measured would pass it unchanged.
PLAN_BYTE_WARN = 16384
PLAN_BYTE_HARD = 24576

ACT_DECIDE = "Decide"
ACT_BUILD = "Build"
ACT_CHECK = "Check"
ACT_PRODUCTION = "Production"

ACTS = (
    {"number": 1, "name": ACT_DECIDE, "steps": (1, 4),
     "note": "nothing is built yet"},
    {"number": 2, "name": ACT_BUILD, "steps": (5, 5),
     "note": "the implementer is not the planner"},
    {"number": 3, "name": ACT_CHECK, "steps": (6, 6),
     "note": "green is not the same as right"},
    {"number": 4, "name": ACT_PRODUCTION, "steps": (7, 7),
     "note": "the undo is named before it is needed"},
)

# number, name, what happens, act, is this the operator's own moment, and
# whether any file in this repository records that it happened.
STEPS = (
    {"number": 1, "act": 1, "approval": False, "machine_visible": False,
     "name": "Define the Value",
     "what": "One sentence saying what would be worth having. It goes in the "
             "note verbatim, and step 6 measures the built thing against it."},
    {"number": 2, "act": 1, "approval": False, "machine_visible": False,
     "name": "Brainstorm the scope",
     "what": "superpowers:brainstorming, until a scope document says what should "
             "be built rather than how to build what was asked."},
    {"number": 3, "act": 1, "approval": False, "machine_visible": False,
     "name": "Write the plan",
     "what": "superpowers:writing-plans. Criteria come from a partition of the "
             "input domain, one row per value class with the edges included, and "
             "the contract is real test files rather than a description of them."},
    {"number": 4, "act": 1, "approval": True, "machine_visible": True,
     "name": "Scrutinize the plan, apply every finding",
     "what": "Yours. No code exists yet, so this is the cheapest moment to "
             "change anything. Your COMMIT of the plan and the RED contract IS "
             "the approval, and that sha is the freeze."},
    {"number": 5, "act": 2, "approval": False, "machine_visible": False,
     "name": "Build it, under separation",
     "what": "superpowers:subagent-driven-development: a fresh implementer per "
             "task and a reviewer who did not write the code. Every commit "
             "descends from the approval sha."},
    {"number": 6, "act": 3, "approval": False, "machine_visible": False,
     "name": "Scrutinize the built thing, apply every finding",
     "what": "Relentless, and each finding carries an origin. A contract-origin "
             "finding returns to step 3 and produces a NEW contract, never a "
             "patch that leaves the old one green."},
    {"number": 7, "act": 4, "approval": True, "machine_visible": True,
     "name": "Production",
     "what": "Yours, on the evidence rather than a summary, including what the "
             "contract does NOT cover. Ship it, write the note under "
             "records/slices/, and name the undo before it is needed: which "
             "commit to revert, which baseline to restore, what to re-run."},
)


def step(number: int) -> dict:
    """One step by number, or None."""
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
