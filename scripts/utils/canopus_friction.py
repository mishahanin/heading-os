"""How hard the green was to get, read from the ledger that already records it.

`pack` renders `25 of 25`, `LOCK HELD`, `APPROVED`. A slice that reached that on
the first attempt and one that reached it on the sixth produce the same page,
though the ledger has held the difference line by line the whole time.

Measured over the whole ledger on 2026-08-03 (254 records, 19 shipped slices):
23 windows and 37 retakes, almost all of it concentrated in six slices --
`2026-07-26-canopus-repository-bin` at 3 and 11, `production-shape` at 5 and 5,
`timer-timezone` at 5 and 6. Everything else shipped clean. That concentration is
what makes the number worth printing: it separates two genuinely different claims
about the same green.

THREE THINGS THIS REFUSES TO DO, each of them a test rather than a promise.

**It does not grade.** No "poor", no "too many", no warning colour. A window is
the sanctioned way to correct a contract that turned out wrong; `production-shape`
earned its five by finding five real problems. A page that scolds the count
teaches the builder to suppress windows, and suppressing a window means editing a
frozen contract in place -- the one thing the whole standard exists to prevent.
The counter would then have made the process worse while looking like rigour.

**It does not count what is not structural.** Windows, ships, retakes, refusals
and failed verifies each have their own event or `kind` in the ledger. Waivers do
not: `--contract-satisfied` is joined into a free-text `reason`, so counting them
means matching a substring of a human sentence, which lies quietly the first time
somebody rewords their reason. The waiver state of the CURRENT freeze already
reaches the page from the committed artifact, and that is the honest source.

**It does not present a floor as a total.** `.canopus/` is gitignored; one
`rm -rf` takes the ledger with it, and nothing reconstructs it. So every number
here is a lower bound, and a row of zeroes is ambiguous between a clean slice and
a lost ledger. `recorded` resolves exactly that one ambiguity -- a held freeze
always wrote a `freeze` line -- and the render says which zero it is showing.

Stdlib only, and no I/O: callers pass entries from
`canopus_freeze.read_ledger`, which already skips damaged lines rather than
raising. This module inherits that softness on purpose. It renders on the
approval path, where a traceback costs the operator the entire page.
"""
from __future__ import annotations

from dataclasses import dataclass

FRICTION_HEADING = "friction"

# Printed under BOTH branches, including the one that counted nothing. A page
# that shows `windows 0` with no caveat is claiming a total it does not have:
# .canopus/ is gitignored and unsigned, so a single rm -rf or one hand-edit
# leaves the count silently low. Naming that on the page is the same discipline
# this section exists to enforce on the rest of it.
_FLOOR = (
    "  A floor, not a total: the ledger is gitignored and unsigned, so it "
    "records at least this much and possibly more. Counted by label, so two "
    "slices sharing one merge here. Waivers are not counted at all -- they live "
    "in free text; the waived state of THIS freeze comes from the committed "
    "artifact above."
)

# The ledger's event vocabulary, mapped to the counter each one feeds. Kept as
# data so an event nobody here has seen lands in NO bucket rather than the
# nearest one: the vocabulary grows, and a silent miscount is worse than a
# visible gap.
_RELEASE = "release"
_SIMPLE = {
    "anchor_replaced": "retakes",
    "approve": "approvals",
    "freeze": "freezes",
    "refuse_approve": "refusals",
    "refuse_release": "refusals",
    "verify_fail": "verify_failures",
}


@dataclass(frozen=True)
class Friction:
    """What the ledger remembers about one slice. Every field is a lower bound.

    `recorded` is not a count and not a quality signal. It answers the single
    ambiguity a row of zeroes carries: a held freeze always wrote a `freeze`
    line, so its absence means the ledger lost this slice, not that the slice
    was frictionless.
    """

    label: str
    freezes: int = 0
    approvals: int = 0
    retakes: int = 0
    windows: int = 0
    ships: int = 0
    refusals: int = 0
    verify_failures: int = 0

    @property
    def recorded(self) -> bool:
        return self.freezes > 0

    @property
    def clean(self) -> bool:
        """Recorded, and nothing went sideways. Descriptive, never a verdict:
        a slice that is not `clean` is not thereby a worse slice."""
        return self.recorded and not (
            self.retakes or self.windows or self.refusals or self.verify_failures
        )


def count_friction(entries, label: str) -> Friction:
    """Count one label's friction out of the ledger's structure.

    Scoped by LABEL, which is the only stable key: the root changes on every
    retake, so root-scoping would count zero by construction. The cost is that
    two slices sharing a label merge into one row. That limitation is named on
    the rendered page rather than hidden here.
    """
    tally = dict.fromkeys(
        ("freezes", "approvals", "retakes", "windows", "ships",
         "refusals", "verify_failures"), 0)

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("label") != label:
            continue
        event = entry.get("event")
        if event == _RELEASE:
            # `release` carries both meanings and only `kind` separates them.
            # Counting the event would report every shipped slice as having
            # opened a window. A release with neither kind predates wire 2.2 and
            # is counted as neither, because it genuinely is not known which.
            kind = entry.get("kind")
            if kind == "window":
                tally["windows"] += 1
            elif kind == "ship":
                tally["ships"] += 1
            continue
        bucket = _SIMPLE.get(event)
        if bucket is not None:
            tally[bucket] += 1

    return Friction(label=label, **tally)


def render_friction(friction: Friction, heading_wrap=("", "")) -> str:
    """The section as it appears on the evidence page.

    Two sentences are mandatory and are asserted by the contract: that the count
    is a FLOOR, and -- when nothing was recorded -- that the zeroes describe a
    missing ledger rather than a clean slice. Neither is decoration; each stops
    the page making a claim stronger than its data.

    `heading_wrap` is a (prefix, suffix) pair the caller uses to style the
    heading -- the CLI passes its bold codes. It exists so the whole section is
    ONE expression at the call site: an intermediate variable let a mutation call
    this function and never print the result, with the contract still green,
    because a test can check that a name is called far more easily than that its
    value reached stdout. This module still knows nothing about colour; it
    concatenates two strings it was handed.
    """
    prefix, suffix = heading_wrap
    lines = [f"{prefix}{FRICTION_HEADING}{suffix}"]

    if not friction.recorded:
        lines.append(
            "  no ledger entries for this label. That is not the same as a slice "
            "with no friction: a held freeze always wrote one, so this reads as "
            "unknown, never as clean."
        )
        lines.append(_FLOOR)
        return "\n".join(lines)

    fields = (
        ("retakes", friction.retakes, "approvals replaced after the contract changed"),
        ("windows", friction.windows, "mid-slice releases, each one a contract corrected"),
        ("refusals", friction.refusals, "the gate declining an approve or a release"),
        ("verify failures", friction.verify_failures, "the frozen contract had moved"),
    )
    width = max(len(name) for name, _v, _d in fields)
    for name, value, description in fields:
        lines.append(f"  {name:<{width}}  {value}   {description}")

    lines.append(_FLOOR)
    return "\n".join(lines)
