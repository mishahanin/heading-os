#!/usr/bin/env python3
"""Whether a shippable state carries an evidence render nothing later invalidated.

The page an operator signs off from at step 12 already exists (`canopus.py
pack`). Nothing required it and it wrote nothing, so whether it ran could not be
established afterwards. Measured 2026-08-02: the slice shipped that day was
signed off on a PROSE SUMMARY, the exact thing the standard's own NEVER list
forbids, and no artifact records that it happened.

**What this buys, stated narrowly on purpose.** It does NOT make the second
approval real, and nothing can: no machine witnesses a human reading. Four
independent voices agreed on that and the wide claim is retired rather than
defended. What survives is worth having on its own: a render exists, and it is
no older than the attestation it reports on. Shipping without one becomes
impossible; skipping one becomes auditable instead of invisible.

**The second half, and why one function was not enough.** `evidence_state`
compares the render against the attestation's STORED stamp, and a stamp is not a
tree. An edit made AFTER the render and never re-attested moves neither stamp,
so a render describing an earlier state still qualified: `attested_at` is a
string, `read_attestation` returns the record whatever the tree now looks like,
and the release path never asked whether that record still speaks for anything.
Found at step 11 by `/scrutinize`, on the very property this module was built to
provide. `attestation_refusal` below closes it, and only with both halves in
place does the chain hold: a qualifying render post-dates the attestation, and
the attestation still describes the tree being shipped.

**When the ledger is believed.** It is trusted about the render exactly when it
remembers the freeze it is being asked about. A ledger holding this freeze's own
`freeze` event is intact enough to be trusted about a MISSING `pack`; one that
has lost it cannot answer and must not refuse. That single test settles the
fragility the architecture council pressed hardest on: a gate that pushes an
honest operator toward `--force` is worse than no gate, and that risk only
materialises on a refusal. Fail closed against haste, open against a broken
disk.

Pure over the entries: no disk, no git, no clock. `scripts/canopus.py` already
reads the ledger and the attestation for its own reasons and hands both in,
which keeps this module stdlib-only and keeps its whole behaviour reachable from
a list literal.

Consumed by: scripts/canopus.py (pack, release).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence, Tuple

# The one name imported rather than respelled. `attestation_refusal` compares
# against the state `attestation_state` actually returns, so a rename there
# fails here instead of silently making this branch unreachable.
from scripts.utils.canopus_freeze import ATTESTED as _ATTESTED

# The three answers, named rather than spelled inline at each call site. The
# precedent is REASON_DIFFERENT_RECIPE in canopus_freeze: a second spelling of a
# state string in another module is a rename away from a branch going silent,
# and that was measured as a live defect once already.
EVIDENCE_FRESH = "evidence_fresh"
EVIDENCE_MISSING = "evidence_missing"
EVIDENCE_UNVERIFIABLE = "evidence_unverifiable"


def _when(raw) -> Optional[datetime]:
    """A stamp, or None when it cannot be read. Never raises."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def evidence_state(
    entries: Sequence[dict], freeze_root: str, attested_at=None
) -> Tuple[str, str]:
    """(state, reason) for the freeze identified by `freeze_root`.

    The reason is written to be printed at a refusal, so it names the command
    that clears it. A refusal that does not say what to run leaves the operator
    guessing, which is how a gate becomes something to disable.
    """
    remembered = any(
        row.get("event") == "freeze" and row.get("root") == freeze_root
        for row in entries
    )
    if not remembered:
        return EVIDENCE_UNVERIFIABLE, (
            "the lifecycle ledger does not carry this freeze, so whether an "
            "evidence page was rendered is unverifiable; shipping anyway"
        )

    packs = [
        row for row in entries
        if row.get("event") == "pack" and row.get("root") == freeze_root
    ]
    if not packs:
        return EVIDENCE_MISSING, (
            "no evidence page was rendered for this freeze; run "
            "`python scripts/canopus.py pack` and read it before shipping"
        )

    # Compared only when BOTH sides read, and only when they can be ordered at
    # all. An unparseable stamp, or a naive one beside an aware one, is a fault
    # rather than haste, and the posture above says a fault must not refuse: the
    # render's existence is then enough and the freshness claim is simply not
    # made. Both stamps come from the same machine in the same slice, so this is
    # a wall-clock comparison and says so rather than implying more.
    attested = _when(attested_at)
    readable = [moment for moment in (_when(row.get("ts")) for row in packs)
                if moment is not None]
    if attested is not None and readable:
        try:
            stale = max(readable) < attested
        except TypeError:
            stale = False
        if stale:
            return EVIDENCE_MISSING, (
                "the evidence page for this freeze was rendered before the "
                "attestation it reports on, so it describes an earlier state; "
                "run `python scripts/canopus.py pack` again and read it before "
                "shipping"
            )
    return EVIDENCE_FRESH, ""


def attestation_refusal(state: str, reason: str, *, judgeable: bool) -> str:
    """The refusal for a record that no longer speaks for the tree, or "".

    `judgeable` is the tree sample's own answer to whether it could be taken at
    all: `tree_state` returns None for a root that is not a git working copy,
    and this refuses nothing there. That is the same posture the rest of this
    module takes and it is not a hedge -- a root that cannot be described is a
    FAULT, and the standing rule is to fail closed against haste and open
    against a broken environment. It is also what keeps the rule testable: every
    scratch root a contract builds is a plain directory, so a blanket refusal on
    NOT ATTESTED would refuse every ship in every test for a reason that has
    nothing to do with the discipline.

    Takes the state and reason rather than sampling them, so no sentence from
    `canopus_freeze` is duplicated here. A second spelling of a reason string in
    another module is a rename away from a branch going silent, which
    `REASON_DIFFERENT_RECIPE` exists to warn about and which was measured as a
    live defect once already.
    """
    if state == _ATTESTED or not judgeable:
        return ""
    return (
        f"the attestation does not stand for the tree being shipped ({reason}); "
        f"re-run `python scripts/run-tests.py`, then render the evidence page "
        f"again"
    )
