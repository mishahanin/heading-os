#!/usr/bin/env python3
"""A2 — what the gates refuse, and when that can be judged.

Two halves with different maturity, deliberately.

The RECORDER is complete. Measured 2026-08-02: the Canopus ledger held 152
events and not one refusal, because the twelve early returns in `cmd_approve`,
`cmd_freeze` and `cmd_release` all exit without touching it. Every refusal the
standard has ever made vanished. Data you did not record is data you can never
get back, so this half is not deferrable.

The REPORTER is minimal, and the minimum is not a compromise. With one day of
denial data its job is NOT to adjudicate the subtraction list; it is to say WHEN
that list can be adjudicated. A confident table of zeros, where zero means "no
occasion arose" and reads as "does not work", is how a mechanism gets removed
for never having had the chance to fire. Two properties make building that table
impossible rather than merely unintended: TOO EARLY is a verdict distinct from
NO YIELD, and `render` cannot form a removal recommendation at all.

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
from scripts.utils.secret_patterns import redact  # noqa: E402

# The name the AST guard looks for. One name, so a refusal cannot be recorded
# by a differently-spelled helper and slip past the enumeration.
RECORDER = "_record_refusal"

# The operator's month, from the v2 design's A3. A window shorter than this
# cannot support the verdict NO YIELD.
BUDGET_DAYS = 31

TOO_EARLY = "TOO EARLY"
NO_YIELD = "NO YIELD"
CATCHING = "CATCHING"

# What `render` may never say. A report that can pronounce a subtraction is one
# bad reading away from a real one; this list is why it cannot.
FORBIDDEN_VERBS = ("remove", "removal", "delete", "cut ", "drop ", "retire",
                   "subtract", "rip out")

# The refusal causes, as classes rather than prose, so two refusals of one kind
# count as two of one thing. Every entry is emitted from scripts/canopus.py and
# a test fails if one is declared here and emitted nowhere.
CAUSE_FREEZE_ALREADY_ACTIVE = "freeze_already_active"
CAUSE_ANCHOR_ALREADY_RECORDED = "anchor_already_recorded"
CAUSE_REPLACE_WITHOUT_REASON = "replace_without_reason"
CAUSE_CANDIDATE_REFUSED = "candidate_refused"
CAUSE_APPROVAL_DISAGREES = "approval_disagrees"
CAUSE_WAIVER_UNAPPROVED = "waiver_unapproved"
CAUSE_LEDGER_WRITE_FAILED = "ledger_write_failed"
CAUSE_ARTIFACT_WRITE_FAILED = "artifact_write_failed"
CAUSE_NO_ACTIVE_FREEZE = "no_active_freeze"
# The four that RAISE rather than return. Half the lifecycle's refusals never
# reach a `return 1` -- an anchor that is not a file, a contract that is not red,
# a damaged manifest -- and counting only the returns would have measured half
# the yield and called it the yield.
CAUSE_FREEZE_CORRUPT = "freeze_corrupt"
CAUSE_FREEZE_ERROR = "freeze_error"
CAUSE_CONTRACT_ERROR = "contract_error"
CAUSE_UNREADABLE = "unreadable"

CAUSES = frozenset({
    CAUSE_FREEZE_ALREADY_ACTIVE,
    CAUSE_ANCHOR_ALREADY_RECORDED,
    CAUSE_REPLACE_WITHOUT_REASON,
    CAUSE_CANDIDATE_REFUSED,
    CAUSE_APPROVAL_DISAGREES,
    CAUSE_WAIVER_UNAPPROVED,
    CAUSE_LEDGER_WRITE_FAILED,
    CAUSE_ARTIFACT_WRITE_FAILED,
    CAUSE_NO_ACTIVE_FREEZE,
    CAUSE_FREEZE_CORRUPT,
    CAUSE_FREEZE_ERROR,
    CAUSE_CONTRACT_ERROR,
    CAUSE_UNREADABLE,
})

# The lifecycle gates. These three are the mechanisms whose refusals the ledger
# now carries.
MECHANISMS = ("approve", "freeze", "release")

# The A1 guards that write to the denial log, by the names they pass. Declared
# rather than discovered so a guard that has NEVER fired still appears in the
# report: a mechanism missing from the table is indistinguishable from a
# mechanism with nothing to say, and that is the confusion this file exists to
# end. Names verified against their call sites 2026-08-02. Anything else in the
# log is added as it is found.
DENIAL_MECHANISMS = (
    "depth-gate",
    "depth-gate:override",
    "leak-guard:check-paths",
    "leak-guard:check-staged",
    "content-guard",
    "secret-scanner",
    "push:engine-clean-scan",
    "push:engine-content-scan",
    "push:secret-tracked-files",
)

SOURCE_LIFECYCLE = "lifecycle"
SOURCE_DENIALS = "denials"

_EVENT_PREFIX = "refuse_"
_MAX_REASON = 512


# An opaque run of 20 or more token characters, carrying no path separator and
# no dot. Deliberately WIDER than the credential vocabulary: that vocabulary
# names the shapes we have seen, and a refused value is by definition something
# somebody tried to push past a guard, so the shapes we have not seen are the
# interesting ones. Measured 2026-08-02: `redact` passes `sk-` + 24 characters
# through untouched, because the pattern it knows is `sk-ant-`.
_OPAQUE = re.compile(r"[A-Za-z0-9_+=-]{20,}")


def redact_reason(text: str) -> str:
    """A refusal's human sentence, reduced to the CLASS of what it refused.

    Two passes, in order. The credential vocabulary first, so a known shape is
    replaced by its own name and the reader learns what kind of thing it was.
    Then every remaining opaque token, because the boundary this holds is not
    "no known credentials" but "the class of thing refused, never the thing".

    Paths survive: they carry separators and dots, and a refusal whose path is
    unreadable is a refusal nobody can act on.
    """
    scrubbed = redact(str(text))
    scrubbed = _OPAQUE.sub(lambda m: f"[opaque:{len(m.group(0))}]", scrubbed)
    return scrubbed[:_MAX_REASON]


def record_refusal(root, *, mechanism: str, cause: str, label: str = "",
                   reason: str = "") -> str:
    """Append one refusal to the ledger. Returns the failure text, or "".

    NEVER raises, and never changes the refusal it is recording. The one thing
    worse than an unrecorded refusal is a refusal that stops refusing because
    its own logging failed, so an unwritable ledger costs the record and nothing
    else. Same shape as `_record` in the CLI, and the same posture as
    `log_denial` for A1.
    """
    try:
        from scripts.utils.canopus_freeze import append_history

        append_history(Path(root), f"{_EVENT_PREFIX}{mechanism}", digest="",
                       label=redact_reason(label), kind=cause,
                       reason=redact_reason(reason))
    except OSError as exc:
        return f"the refusal could not be recorded: {exc}"
    return ""


def _parse(stamp):
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read_sources(root) -> dict:
    """The two logs, plus the names of any that were not there.

    An absent log and an empty one are DIFFERENT facts, and reading the first as
    the second is how a mechanism gets called silent because of a missing file.
    """
    root = Path(root)
    out = {"ledger": [], "denials": [], "missing": []}
    try:
        from scripts.utils.canopus_freeze import history_state_path, read_ledger

        if history_state_path(root).exists():
            out["ledger"] = list(read_ledger(root))
        else:
            out["missing"].append(SOURCE_LIFECYCLE)
    except (OSError, ValueError, ImportError, AttributeError):
        out["missing"].append(SOURCE_LIFECYCLE)
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


def summarise(*, ledger, denials, since: dict, now) -> dict:
    """Per mechanism: how often it refused, when last, over WHOSE window.

    `since` is a mapping per SOURCE and not one timestamp, and that is the whole
    point. The lifecycle ledger began 2026-07-25 and the denial log 2026-08-01;
    one shared window would judge a one-day-old mechanism over an eight-day one
    and call it silent before it had a day to speak. Caught at step 5 of this
    slice, before any code existed.
    """
    windows = {source: _window_days(since.get(source), now)
               for source in (SOURCE_LIFECYCLE, SOURCE_DENIALS)}

    caught: dict = {}
    for name in MECHANISMS:
        caught[name] = {"source": SOURCE_LIFECYCLE, "caught": 0, "last_catch": None,
                        "causes": {}}
    for name in DENIAL_MECHANISMS:
        caught[name] = {"source": SOURCE_DENIALS, "caught": 0, "last_catch": None,
                        "causes": {}}

    for row in ledger:
        event = str(row.get("event") or "")
        if not event.startswith(_EVENT_PREFIX):
            continue
        name = event[len(_EVENT_PREFIX):]
        entry = caught.setdefault(
            name, {"source": SOURCE_LIFECYCLE, "caught": 0, "last_catch": None,
                   "causes": {}})
        _count(entry, row.get("ts"), row.get("kind"))

    for row in denials:
        name = str(row.get("mechanism") or "unnamed")
        entry = caught.setdefault(
            name, {"source": SOURCE_DENIALS, "caught": 0, "last_catch": None,
                   "causes": {}})
        _count(entry, row.get("ts"), row.get("reason"))

    for entry in caught.values():
        days = windows.get(entry["source"], 0)
        entry["days"] = days
        entry["verdict"] = _verdict(entry["caught"], days)

    return {"mechanisms": caught, "windows": windows,
            "budget_days": BUDGET_DAYS, "generated_for": str(now)}


def _count(entry: dict, stamp, cause) -> None:
    entry["caught"] += 1
    when = _parse(stamp)
    previous = _parse(entry["last_catch"])
    if when is not None and (previous is None or when > previous):
        entry["last_catch"] = str(stamp)
    key = str(cause or "unclassified")
    entry["causes"][key] = entry["causes"].get(key, 0) + 1


def _verdict(caught: int, days: int) -> str:
    if caught:
        return CATCHING
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
        f"  observed over {windows.get(SOURCE_LIFECYCLE, 0)} day(s) of lifecycle "
        f"ledger and {windows.get(SOURCE_DENIALS, 0)} day(s) of denial log, "
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
