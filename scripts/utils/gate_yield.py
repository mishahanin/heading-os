#!/usr/bin/env python3
"""A2 — what the gates refuse, and when that can be judged.

The REPORTER is minimal, and the minimum is not a compromise. With one day of
denial data its job is NOT to adjudicate the subtraction list; it is to say WHEN
that list can be adjudicated. A confident table of zeros, where zero means "no
occasion arose" and reads as "does not work", is how a mechanism gets removed
for never having had the chance to fire. Two properties make building that table
impossible rather than merely unintended: TOO EARLY is a verdict distinct from
NO YIELD, and `render` cannot form a removal recommendation at all.

The lifecycle half of this module — a recorder for Canopus freeze refusals, the
closed cause vocabulary it wrote, and the retake counter reading a committed hand
classification — was deleted on 2026-08-07 with the freeze machinery that
produced its rows. It read a source nothing writes, so every number it could
render was a permanent zero: exactly the failure the paragraph above names.
Constants fed by a ledger nothing writes manufacture the impression of
measurement. What survives measures something.

Nothing here removes anything, ever. A2 flags with a number; the decision is the
operator's. That is his standing rule, and `FORBIDDEN_VERBS` plus its test are
what make it a property of the code rather than a promise in prose.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.denial_log import printable  # noqa: E402

# The operator's month, from the v2 design's A3. A window shorter than this
# cannot support the verdict NO YIELD.
BUDGET_DAYS = 31

TOO_EARLY = "TOO EARLY"
NO_YIELD = "NO YIELD"
CATCHING = "CATCHING"
# A wall that has caught nothing. Its own success condition, and the reason it
# needs a verdict of its own: reporting it as NO YIELD states the same fact and
# means the opposite.
HOLDING = "HOLDING"

# What `render` may never say. A report that can pronounce a subtraction is one
# bad reading away from a real one; this list is why it cannot.
FORBIDDEN_VERBS = ("remove", "removal", "delete", "cut ", "drop ", "retire",
                   "subtract", "rip out")

# The A1 guards that write to the denial log, by the names they pass. Declared
# rather than discovered so a guard that has NEVER fired still appears in the
# report: a mechanism missing from the table is indistinguishable from a
# mechanism with nothing to say, and that is the confusion this file exists to
# end. Names verified against their call sites 2026-08-02. Anything else in the
# log is added as it is found.
DENIAL_MECHANISMS = (
    "leak-guard:check-paths",
    "leak-guard:check-staged",
    "content-guard",
    "secret-scanner",
    "push:engine-clean-scan",
    "push:engine-content-scan",
    "push:secret-tracked-files",
    # The PreToolUse family, by the names `.claude/hooks/_dispatch.py` actually
    # passes: its deny path calls `_record_denial(check.__name__, ...)`, so the
    # mechanism name IS the function name. Omitting them was the declared
    # property failing on its own largest family -- and the guards LEAST likely
    # to fire, because each one waits on a model mistake. A guard
    # that has never fired was invisible here rather than TOO EARLY, which is
    # the precise confusion the paragraph above says this list exists to end.
    "check_prevent_secrets",
    "check_protect_personal_threads",
    "check_protect_corporate",
    "check_protect_docs",
    "check_cwd_anchor",
    "check_slow_shell",
    "check_rate_limit",
    "check_tool_budget",
    # Added 2026-08-29 with the two walls themselves. Both refuse a model
    # mistake rather than a dangerous write, so both will sit at zero
    # catches for long stretches, and zero-with-a-name is exactly what this
    # list exists to tell apart from absent-entirely.
    "check_graph_first",
    "check_fanout_first",
)

# ============================================================
# The two axes: a wall and a gate are not the same kind of thing
# ============================================================
#
# Everything above measures a mechanism by how often it caught something. That
# instrument is correct for exactly one class of mechanism and wrong for the
# other, and until 2026-08-03 it was pointed at both.
#
# A GATE has a symmetric, bounded loss function. Too little of it costs rework;
# too much costs time. Catch counts are the right way to judge one, and A3's
# month budget is the right window.
#
# A WALL has an asymmetric, unbounded loss function. Zero catches is its SUCCESS
# condition, and one miss is irreversible: a live credential in a public
# repository is published the instant the push lands, and no later refusal
# retracts it. Judging a wall by catch counts inverts its own success signal
# into evidence against it, which is how a guard gets removed for working.
#
# The criterion that replaces catch counts for a wall: it may be removed only
# when its loss function becomes symmetric and bounded, or when the protected
# asset or threat ceases to exist structurally. Never on a number of catches, at
# any window length.
#
# The split is by LOSS FUNCTION and deliberately NOT by which log a mechanism
# writes to. Sorting by log would have been one line and would have swept every
# session-shaped guard in with the secret scanner because they share a writer.
WALL_REASONS = {
    "secret-scanner":
        "one missed credential in a PUBLIC repository is published the moment "
        "the push lands, and no later refusal retracts it",
    "content-guard":
        "one missed private entity in the public engine is disclosure, and the "
        "clone that read it is beyond recall",
    "leak-guard:check-paths":
        "a data path written into the engine tree leaks operator data into a "
        "public repository, irreversibly once pushed",
    "leak-guard:check-staged":
        "the staged half of the same wall, and the same irreversibility",
    "push:engine-clean-scan":
        "the last scan before bytes leave the machine; a miss here has no layer "
        "behind it",
    "push:engine-content-scan":
        "the unbypassable content wall on the push path, whose whole design "
        "premise is that nothing catches what it misses",
    "push:secret-tracked-files":
        "a tracked credential file reaching a remote is a compromised secret, "
        "rotation not correction",
    "check_prevent_secrets":
        "blocks a credential before it reaches the filesystem; a miss becomes "
        "the commit and push layers' problem, and eventually nobody's",
    "check_protect_personal_threads":
        "personal threads are CEO-only by four-layer enforcement; one disclosure "
        "cannot be undone",
    "check_protect_corporate":
        "an exec workspace writing into read-only corporate content corrupts "
        "what the whole fleet then pulls",
    "check_protect_docs":
        "protects the published documentation surface from an unreviewed write",
}

WALLS = tuple(WALL_REASONS)

# The mechanisms whose loss function IS symmetric and bounded, so catch counts
# and the month budget are the right instrument. `check_tool_budget` is the case
# that proves the split is by loss function: it writes to the denial log exactly
# like every wall above, and under-friction costs rework while over-friction
# costs time. Sweeping it in with the walls would have made a session gate
# unjudgeable.
GATES = (
    "check_cwd_anchor",
    "check_slow_shell",
    "check_rate_limit",
    "check_tool_budget",
    # Added 2026-08-29 with the two walls themselves. Their loss function is
    # symmetric and bounded like the gates above: under-friction costs a
    # worse answer and rework, over-friction costs time. Neither protects a
    # write, so neither belongs with the walls.
    "check_graph_first",
    "check_fanout_first",
)


def is_wall(mechanism: str) -> bool:
    """True when this mechanism must never be judged by its catch count.

    An UNDECLARED name answers True, and the asymmetry is the whole reason. The
    two failure directions are not equal: calling a gate a wall costs one missing
    verdict, while calling a wall a gate puts something nobody ever classified
    into the FLAGGED list, which is the input to a removal decision. So the
    default is the expensive-but-safe one, and
    `test_every_declared_mechanism_is_classified_so_the_fail_safe_never_fires`
    keeps it a net rather than a plan.
    """
    return mechanism not in GATES


SOURCE_DENIALS = "denials"


# The denial log stamps `time.time()`. The ISO branch is kept because a caller
# may hand this function a stamp it read from somewhere else, and a parser that
# knew only one form answered None for every row it could not read -- which is
# SILENT here: None reads out as a 0-day window and a blank last-catch rather
# than as an error. Measured 2026-08-02 against the live log, every one of the
# nine A1 guards reported "0 catch(es) in 0 day(s)" and the one guard that HAD
# caught something reported it with no date. A permanently-0-day window can never
# reach the budget, so NO YIELD -- the one verdict this report exists to reach --
# was unreachable for half the mechanisms by construction.
_EPOCH = re.compile(r"^\d+(?:\.\d+)?$")


def _parse(stamp):
    """A timestamp as an aware datetime, or None.

    The numeric branch takes the string form too, because `_earliest` passes
    stamps back through `str()` on the way to the window arithmetic.
    """
    if stamp is None or stamp == "" or isinstance(stamp, bool):
        return None
    text = str(stamp)
    if isinstance(stamp, (int, float)) or _EPOCH.match(text):
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read_sources(root) -> dict:
    """The denial log, plus its name when it was not there.

    An absent log and an empty one are DIFFERENT facts, and reading the first as
    the second is how a mechanism gets called silent because of a missing file.

    `root` is accepted and unused since the lifecycle ledger was deleted: the
    denial log is workspace-global and has never been read from a caller's root.
    """
    out = {"denials": [], "missing": []}
    try:
        from scripts.utils.denial_log import denial_log_path, read_denials

        if denial_log_path().exists():
            out["denials"] = list(read_denials())
        else:
            out["missing"].append(SOURCE_DENIALS)
    except (OSError, ValueError, ImportError, AttributeError):
        out["missing"].append(SOURCE_DENIALS)
    return out


def _window_days(since, now) -> int:
    start, end = _parse(since), _parse(now)
    if start is None or end is None:
        return 0
    return max(0, (end - start) // timedelta(days=1))


def summarise(*, denials, since: dict, now) -> dict:
    """Per mechanism: how often it refused, when last, over WHOSE window.

    `since` is a mapping per SOURCE and not one timestamp. It held two sources
    until the lifecycle ledger was deleted, and it stays a mapping because the
    per-source window is the property, not the count of sources: judging a
    one-day-old mechanism over an eight-day window is how something gets called
    silent before it has had a day to speak.
    """
    windows = {SOURCE_DENIALS: _window_days(since.get(SOURCE_DENIALS), now)}

    caught: dict = {}
    for name in DENIAL_MECHANISMS:
        caught[name] = {"source": SOURCE_DENIALS, "caught": 0, "last_catch": None,
                        "causes": {}}

    for row in denials:
        name = str(row.get("mechanism") or "unnamed")
        entry = caught.setdefault(
            name, {"source": SOURCE_DENIALS, "caught": 0, "last_catch": None,
                   "causes": {}})
        _count(entry, row.get("ts"), row.get("reason"))

    for name, entry in caught.items():
        days = windows.get(entry["source"], 0)
        entry["days"] = days
        # Carried explicitly rather than inferred from the verdict, because the
        # verdict loses it: a wall that HAS caught something reads CATCHING like
        # any gate, so `--json` would give a consumer no way to tell that this
        # mechanism must never be judged by that count.
        entry["wall"] = is_wall(name)
        entry["verdict"] = _verdict(name, entry["caught"], days)

    return {"mechanisms": caught, "windows": windows,
            "budget_days": BUDGET_DAYS, "generated_for": str(now)}


def _count(entry: dict, stamp, cause) -> None:
    entry["caught"] += 1
    when = _parse(stamp)
    previous = _parse(entry["last_catch"])
    if when is not None and (previous is None or when > previous):
        # Normalised, never the raw field. The log stamps a float, and a report
        # answering "when did it last catch" with 1785624388.57 makes the
        # operator do the conversion the report exists to have already done.
        entry["last_catch"] = when.isoformat()
    key = str(cause or "unclassified")
    entry["causes"][key] = entry["causes"].get(key, 0) + 1


def _verdict(name: str, caught: int, days: int) -> str:
    if caught:
        return CATCHING
    if is_wall(name):
        # No window length changes this, and that is the point rather than a
        # simplification. The defect was not that 31 days is too short for the
        # secret scanner; it is that a catch-count verdict exists for this class
        # at all, so a longer window only makes the wrong verdict more confident.
        return HOLDING
    return NO_YIELD if days >= BUDGET_DAYS else TOO_EARLY


def render(summary: dict, *, now) -> str:
    """The report as text. Writes nothing, and cannot recommend a subtraction.

    A flagged mechanism gets its number and the sentence naming whose decision
    it is. That is the strongest thing this report is permitted to say.
    """
    windows = summary["windows"]
    lines = [
        "GATE YIELD",
        "",
        f"  observed over {windows.get(SOURCE_DENIALS, 0)} day(s) of denial log, "
        f"as at {now}",
        f"  a verdict of {NO_YIELD} needs a window of at least "
        f"{summary['budget_days']} day(s); anything shorter reads {TOO_EARLY}",
        "",
    ]
    for name in sorted(summary["mechanisms"]):
        entry = summary["mechanisms"][name]
        safe = printable(name)
        head = (f"  {entry['verdict']:<10} {safe:<28} "
                f"{entry['caught']} catch(es) in {entry['days']} day(s)")
        if entry["last_catch"]:
            head += f", last {entry['last_catch']}"
        lines.append(head)
        for cause, count in sorted(entry["causes"].items()):
            lines.append(f"             {printable(cause)}: {count}")

    # Said plainly, because the layout hides it. A denial cause is A1's human
    # sentence, which carries the offending path, so one guard catching the same
    # class of thing twice in two files shows two buckets of one. Reading a
    # bucket as a class over-counts the variety of what a guard catches.
    if any(e["causes"] for e in summary["mechanisms"].values()):
        lines.append("")
        lines.append("  Causes under a denial-log mechanism are A1's prose reasons, not "
                     "declared")
        lines.append("  classes: they carry the path, so they do not aggregate. Count "
                     "the catches, not the buckets.")

    if any(e["verdict"] == HOLDING for e in summary["mechanisms"].values()):
        lines.append("")
        lines.append(f"  {HOLDING} is not {NO_YIELD}. A mechanism whose loss function is "
                     "asymmetric and")
        lines.append("  unbounded succeeds by catching nothing, so a catch count judges "
                     "it backwards and")
        lines.append("  no window length fixes that. Its stated reason sits beside its "
                     "name in")
        lines.append("  WALL_REASONS, where the classification lives, so a changed "
                     "premise is visible")
        lines.append("  where the judgement was made.")

    flagged = sorted(n for n, e in summary["mechanisms"].items()
                     if e["verdict"] == NO_YIELD)
    lines.append("")
    if flagged:
        lines.append(f"  FLAGGED: {len(flagged)} mechanism(s) have caught nothing "
                     f"over a full budget window.")
        lines.append(f"  {', '.join(printable(n) for n in flagged)}")
        lines.append("  Flagged is all this report does. What happens to a flagged "
                     "mechanism is the operator's decision and nobody else's.")
    else:
        lines.append("  Nothing is flagged: no mechanism has been silent for a "
                     "full budget window yet.")
    return "\n".join(lines) + "\n"
