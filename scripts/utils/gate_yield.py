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

import json
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
# A wall that has caught nothing. Its own success condition, and the reason it
# needs a verdict of its own: reporting it as NO YIELD states the same fact and
# means the opposite.
HOLDING = "HOLDING"

# What `render` may never say. A report that can pronounce a subtraction is one
# bad reading away from a real one; this list is why it cannot.
FORBIDDEN_VERBS = ("remove", "removal", "delete", "cut ", "drop ", "retire",
                   "subtract", "rip out")

# The refusal causes, as classes rather than prose, so two refusals of one kind
# count as two of one thing. The correspondence with scripts/canopus.py is
# pinned in BOTH directions: a test fails on a cause declared here and emitted
# nowhere, and a second test fails on a cause emitted there and declared
# nowhere. One direction alone was not enough -- `evidence_missing` reached the
# CLI on 2026-08-03 and never reached this table, and every test stayed green
# because the only guard walked from here outward.
CAUSE_FREEZE_ALREADY_ACTIVE = "freeze_already_active"
CAUSE_ANCHOR_ALREADY_RECORDED = "anchor_already_recorded"
CAUSE_REPLACE_WITHOUT_REASON = "replace_without_reason"
CAUSE_CANDIDATE_REFUSED = "candidate_refused"
CAUSE_APPROVAL_DISAGREES = "approval_disagrees"
CAUSE_WAIVER_UNAPPROVED = "waiver_unapproved"
CAUSE_LEDGER_WRITE_FAILED = "ledger_write_failed"
CAUSE_ARTIFACT_WRITE_FAILED = "artifact_write_failed"
CAUSE_NO_ACTIVE_FREEZE = "no_active_freeze"
CAUSE_EVIDENCE_MISSING = "evidence_missing"
CAUSE_ATTESTATION_PERISHED = "attestation_perished"
CAUSE_RETAKE_CAUSE_MISSING = "retake_cause_missing"
# The two neighbours of the token above, split apart on the rule stated for
# CAUSE_ENFORCER_UNVERIFIABLE eight lines down and broken in the same slice that
# wrote it: one token covered a retake that declared NO cause, a retake that
# declared an unknown one, and a first approval that declared a cause it had
# nowhere to put. Three kinds counting as one thing, in the one table whose whole
# premise is that they must not.
#
# The cures differ, which is the test this table applies: the first is "type
# --cause", the second is "type a cause from the closed set", and the third is
# "you did not mean to approve, you meant to replace" — a mistake about the
# COMMAND, not about the vocabulary.
CAUSE_RETAKE_CAUSE_UNKNOWN = "retake_cause_unknown"
CAUSE_CAUSE_WITHOUT_REPLACE = "cause_without_replace"
# `repin` refusing because the enforcer bytes it would record are not committed.
# The one refusal the manifest-split slice adds, and the reason that slice is not
# a security trade: the cheap path still makes the change land in git.
CAUSE_ENFORCER_UNCOMMITTED = "enforcer_uncommitted"
# `repin` refusing because it could not ESTABLISH whether those bytes are
# committed: the tree is inside a repository and git could not describe it.
# A class of its own rather than a second reading of the one above, for the
# reason this whole table exists — "two refusals of one kind count as two of one
# thing" is false the moment one token covers two kinds. The cures differ too:
# the first is `git commit`, this one is finding out why git is failing here.
CAUSE_ENFORCER_UNVERIFIABLE = "enforcer_unverifiable"
# The four that RAISE rather than return. Half the lifecycle's refusals never
# reach a `return 1` -- an anchor that is not a file, a contract that is not red,
# a damaged manifest -- and counting only the returns would have measured half
# the yield and called it the yield.
CAUSE_FREEZE_CORRUPT = "freeze_corrupt"
CAUSE_FREEZE_ERROR = "freeze_error"
CAUSE_CONTRACT_ERROR = "contract_error"
CAUSE_UNREADABLE = "unreadable"

CAUSES = frozenset({
    CAUSE_ENFORCER_UNCOMMITTED,
    CAUSE_ENFORCER_UNVERIFIABLE,
    CAUSE_FREEZE_ALREADY_ACTIVE,
    CAUSE_ANCHOR_ALREADY_RECORDED,
    CAUSE_REPLACE_WITHOUT_REASON,
    CAUSE_CANDIDATE_REFUSED,
    CAUSE_APPROVAL_DISAGREES,
    CAUSE_WAIVER_UNAPPROVED,
    CAUSE_LEDGER_WRITE_FAILED,
    CAUSE_ARTIFACT_WRITE_FAILED,
    CAUSE_NO_ACTIVE_FREEZE,
    CAUSE_EVIDENCE_MISSING,
    CAUSE_ATTESTATION_PERISHED,
    CAUSE_RETAKE_CAUSE_MISSING,
    CAUSE_RETAKE_CAUSE_UNKNOWN,
    CAUSE_CAUSE_WITHOUT_REPLACE,
    CAUSE_FREEZE_CORRUPT,
    CAUSE_FREEZE_ERROR,
    CAUSE_CONTRACT_ERROR,
    CAUSE_UNREADABLE,
})

# The lifecycle gates: the mechanisms whose refusals the ledger now carries.
# `repin` joined them with the manifest-split slice. Declared here for the same
# reason DENIAL_MECHANISMS below is declared rather than discovered — a gate
# missing from this tuple never appears in the report at all, which is
# indistinguishable from a gate with nothing to say.
MECHANISMS = ("approve", "freeze", "release", "repin")

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
    # The PreToolUse family, by the names `.claude/hooks/_dispatch.py` actually
    # passes: its deny path calls `_record_denial(check.__name__, ...)`, so the
    # mechanism name IS the function name. Omitting them was the declared
    # property failing on its own largest family -- eight guards, and the eight
    # LEAST likely to fire, because each one waits on a model mistake. A guard
    # that has never fired was invisible here rather than TOO EARLY, which is
    # the precise confusion the paragraph above says this list exists to end.
    "check_prevent_secrets",
    "check_canopus_freeze",
    "check_protect_personal_threads",
    "check_protect_corporate",
    "check_protect_docs",
    "check_cwd_anchor",
    "check_rate_limit",
    "check_tool_budget",
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
# writes to. Sorting by log would have been one line and would have swept the
# depth gate in with the secret scanner because they share a writer.
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
# and the month budget are the right instrument. `depth-gate` is the case that
# proves the split is by loss function: it writes to the denial log exactly like
# every wall above, and under-ceremony costs rework while over-ceremony costs
# time. Sweeping it in with the walls would have made the one mechanism v2 most
# needs to judge unjudgeable.
GATES = (
    "approve",
    "freeze",
    "release",
    # A `repin` refusal is slice friction, exactly like the three above it: the
    # enforcer bytes are uncommitted, or no lock is held. Declared here rather
    # than left to the undeclared default, which would file it as a WALL and put
    # a lifecycle gate into the set that is never judged by its catch count.
    "repin",
    "depth-gate",
    "depth-gate:override",
    "check_canopus_freeze",
    "check_cwd_anchor",
    "check_rate_limit",
    "check_tool_budget",
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


# ============================================================
# Retake causes: the yield class the instrument could not see
# ============================================================
#
# A retake (`anchor_replaced`) is the standard's largest single output and the
# report counted none of them. Measured over the 39 in the ledger on 2026-08-03,
# by hand: 14 were a frozen contract that turned out too weak and was
# strengthened, 21 were the enforcer bytes moving, 4 were lint debt. The
# lifecycle's whole reported yield at the time was FIVE.
#
# The cause is a DECLARED field from a closed set, never a substring of the human
# reason. `scripts/utils/canopus_friction.py` refused exactly this shape for
# waivers and said why: a counter built on a substring lies quietly the first
# time somebody rewords their sentence.
CAUSE_CONTRACT_STRENGTHENED = "contract-strengthened"
CAUSE_ENFORCER_MOVED = "enforcer-moved"
CAUSE_LINT = "lint"
CAUSE_SET_WRONG = "frozen-set-wrong"

RETAKE_CAUSES = frozenset({
    CAUSE_CONTRACT_STRENGTHENED,
    CAUSE_ENFORCER_MOVED,
    CAUSE_LINT,
    CAUSE_SET_WRONG,
})

UNCLASSIFIED = "unclassified"

# The hand classification of the retakes that predate the declared field, keyed
# by record identity. Engine-side and COMMITTED, because a bridge kept in a
# gitignored directory is a bridge one `rm -rf` removes.
HAND_CLASSIFIED_PATH = Path("config") / "canopus-retake-history.json"


def retake_key(row: dict) -> str:
    """The identity of one retake: its timestamp and its label.

    Not the digest. Two retakes of one slice can carry the same root when the
    second re-approves an unchanged set, and a key that collides silently merges
    two classifications into one.
    """
    return f"{row.get('ts', '')}|{row.get('label', '')}"


def load_hand_classified(root) -> dict:
    """The committed hand classification, as `{ts|label: cause}`.

    Each entry on disk carries the RAW PROSE REASON recorded at the time beside
    its class, so every line can be checked against what was actually written
    rather than against the classifier's summary of it. That column is C1's
    remedy in the gate artifact and it is the only thing that makes this file
    auditable; the loader drops it because the counter needs the class alone.

    A missing or damaged file answers `{}` rather than raising. This is a bridge
    for history, and a report that cannot render because a historical annotation
    is unreadable has turned a footnote into an outage.

    DAMAGED is said out loud, and ABSENT is not, and the two were one silence
    until 2026-08-04. `{}` from a corrupt file renders exactly like `{}` from a
    tree that never had one: every historical retake drops to `unclassified` and
    the page reports "0 were classified BY HAND" with nothing anywhere saying 39
    classifications were just lost. The absent case IS the ordinary state of any
    clone that is not this one, so it stays quiet; the damaged case is a fault,
    and this module's own rule for a check that could not run is that it must not
    read as a check that found nothing. Reported rather than raised, because the
    caller is a report and the sentence above still holds.
    """
    path = Path(root) / HAND_CLASSIFIED_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"gate-yield: the committed retake classification at {path} could "
              f"not be read, so every retake predating the declared --cause "
              f"field will count as {UNCLASSIFIED}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print(f"gate-yield: the committed retake classification at {path} is not "
              f"an object, so every retake predating the declared --cause field "
              f"will count as {UNCLASSIFIED}.", file=sys.stderr)
        return {}
    out = {}
    for key, value in raw.items():
        # A leading underscore is prose for the reader, not a record. JSON has no
        # comments, and a data file whose own purpose is undocumented is the
        # thing somebody drains in six months without knowing what it was for.
        if str(key).startswith("_"):
            continue
        cause = value.get("cause") if isinstance(value, dict) else value
        if isinstance(cause, str) and cause in RETAKE_CAUSES:
            out[str(key)] = cause
    return out


def count_retakes(ledger, *, hand_classified: dict) -> dict:
    """Retakes per cause, across the whole ledger.

    The structural field wins over the hand file for the same record, always. The
    reverse would let the committed annotation restate the present rather than
    only supply the past, and a bridge that can overwrite live data is not a
    bridge.

    A record with neither counts as `unclassified` and is never inferred from its
    prose. It stays visible as a gap instead of quietly joining a class.
    """
    counts: dict = {}
    for row in ledger:
        if row.get("event") != "anchor_replaced":
            continue
        declared = row.get("kind")
        cause = (declared if declared in RETAKE_CAUSES
                 else hand_classified.get(retake_key(row), UNCLASSIFIED))
        counts[cause] = counts.get(cause, 0) + 1
    return counts


def retake_cause_or_error(cause) -> str:
    """"" when the cause is a declared one, else the sentence refusing it.

    Returns text rather than raising because every caller is a CLI early-return
    that prints and exits 1, and the one caller that is not would have to catch
    an exception to do the same thing.
    """
    if not cause:
        return ("a retake must declare its --cause; an optional field is a field "
                "that is present when convenient, and the resulting count is a "
                "count of the times somebody remembered. One of: "
                + ", ".join(sorted(RETAKE_CAUSES)))
    if cause not in RETAKE_CAUSES:
        return (f"unknown --cause {cause!r}; the vocabulary is closed so two "
                f"retakes of one kind count as two of one thing. One of: "
                + ", ".join(sorted(RETAKE_CAUSES)))
    return ""


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
    except Exception as exc:  # noqa: BLE001 — totality IS the guarantee
        # `except Exception`, matching `log_denial`, which this docstring claims
        # the same posture as and which is total. Catching only OSError left the
        # guarantee above false for every other failure: an import that cannot
        # resolve, a ledger line that will not serialise. The caller runs this
        # one line BEFORE its `return 1`, so anything escaping here converts a
        # clean refusal into a traceback -- the outcome this function's own
        # first paragraph names as the worst one available.
        return f"the refusal could not be recorded: {type(exc).__name__}: {exc}"
    return ""


# The two sources have never stamped alike, and nothing read them together
# until this module did. The lifecycle ledger writes
# `datetime.now(timezone.utc).isoformat()`; the denial log writes `time.time()`.
_EPOCH = re.compile(r"^\d+(?:\.\d+)?$")


def _parse(stamp):
    """A timestamp from EITHER log, as an aware datetime, or None.

    Reading only the ISO form is not a loud failure, which is why it survived:
    an unparsed stamp answers None, and None reads out as a 0-day window and a
    blank last-catch rather than as an error. Measured 2026-08-02 against the
    live log, every one of the nine A1 guards reported "0 catch(es) in 0
    day(s)" and the one guard that HAD caught something reported it with no
    date. A permanently-0-day window can never reach the budget, so NO YIELD --
    the one verdict this report exists to reach -- was unreachable for half the
    mechanisms by construction.

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


def summarise(*, ledger, denials, since: dict, now, hand_classified=None) -> dict:
    """Per mechanism: how often it refused, when last, over WHOSE window.

    `since` is a mapping per SOURCE and not one timestamp, and that is the whole
    point. The lifecycle ledger began 2026-07-25 and the denial log 2026-08-01;
    one shared window would judge a one-day-old mechanism over an eight-day one
    and call it silent before it had a day to speak. Caught at step 5 of this
    slice, before any code existed.

    `hand_classified` supplies the class for retakes that predate the declared
    field. It defaults to nothing rather than to a disk read, so a caller that
    wants the bridge asks for it and a test that does not is never reading the
    operator's real history through a default argument.
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

    for name, entry in caught.items():
        days = windows.get(entry["source"], 0)
        entry["days"] = days
        # Carried explicitly rather than inferred from the verdict, because the
        # verdict loses it: a wall that HAS caught something reads CATCHING like
        # any gate, so `--json` would give a consumer no way to tell that this
        # mechanism must never be judged by that count.
        entry["wall"] = is_wall(name)
        entry["verdict"] = _verdict(name, entry["caught"], days)

    hand = dict(hand_classified or {})
    retakes = count_retakes(ledger, hand_classified=hand)
    from_hand = sum(
        1 for row in ledger
        if row.get("event") == "anchor_replaced"
        and row.get("kind") not in RETAKE_CAUSES
        and retake_key(row) in hand)

    return {"mechanisms": caught, "windows": windows,
            "budget_days": BUDGET_DAYS, "generated_for": str(now),
            "retakes": retakes, "retakes_from_hand": from_hand}


def _count(entry: dict, stamp, cause) -> None:
    entry["caught"] += 1
    when = _parse(stamp)
    previous = _parse(entry["last_catch"])
    if when is not None and (previous is None or when > previous):
        # Normalised, never the raw field. One source stamps ISO and the other
        # a float, and a report answering "when did it last catch" with
        # 1785624388.57 makes the operator do the conversion the report exists
        # to have already done.
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

    # Said plainly, because the two halves of this table are not the same kind
    # of thing and the layout hides it. A lifecycle cause is a declared class
    # from CAUSES, so two refusals of one kind count as two of one thing. A
    # denial cause is A1's human sentence, which carries the offending path, so
    # one guard catching the same class of thing twice in two files shows two
    # buckets of one. Reading the second as the first over-counts the variety
    # of what a guard catches.
    if any(e["source"] == SOURCE_DENIALS and e["causes"]
           for e in summary["mechanisms"].values()):
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

    retakes = summary.get("retakes") or {}
    if retakes:
        total = sum(retakes.values())
        by_hand = summary.get("retakes_from_hand", 0)
        lines.append("")
        lines.append(f"  RETAKES: {total} frozen-contract retake(s) across the whole "
                     f"ledger, by declared cause.")
        for cause, count in sorted(retakes.items()):
            lines.append(f"             {printable(cause)}: {count}")
        lines.append(f"  Of these, {by_hand} were classified BY HAND from the record's "
                     f"prose, because they")
        lines.append("  predate the declared field. That part is judgement, not "
                     "measurement, and it is")
        lines.append("  auditable line by line in config/canopus-retake-history.json, "
                     "which carries the")
        lines.append("  raw reason beside every class. A cause is never inferred from "
                     "prose by the code.")

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
