#!/usr/bin/env python3
"""
compaction-probe.py - read where compaction actually fired, from ground truth.

Claude Code writes a `system` record with `subtype: "compact_boundary"` into the
session transcript at the moment it compacts, carrying a `compactMetadata` block
with `trigger`, `preTokens`, `postTokens`, `cumulativeDroppedTokens` and
`durationMs`. That block is recorded BY the harness AT the event. Everything else
this workspace can see - the statusline percentage, `--compact-history`'s
`used_pct_at_or_above` - is a sample taken at an unrelated moment, and the gap
between sample and event has been measured at five and a half minutes. So this
script reads the transcripts and nothing else.

TIMESTAMP SEMANTICS, stated once because three earlier drafts disagreed:

1. Handoff archives are compared by their FILENAME stamp, never by mtime.
   `checkpoint-save.py` truncates the stamp to `%H%M%S`, and mtime drifts with
   any later touch, so the filename is the only stable record of when the
   archive was written.
2. There are TWO clocks here and they must not be mixed. Transcript timestamps
   are UTC; archive filenames are stamped in the operator's own zone. So
   `assert_driven` compares UTC against UTC (`compact_requests[].at` is
   `CP.utc_now()`) via `_event_stamp`, and `assert_handoff_precedes` compares
   local against local via `_event_stamp_local`. Mixing them was a real defect,
   found 2026-08-23: on this UTC+4 host it produced 8 false violations and hid 2
   real ones out of 91 boundaries.

Usage:
    python scripts/compaction-probe.py
    python scripts/compaction-probe.py --session <id> --json
    python scripts/compaction-probe.py --since 2026-08-01
    python scripts/compaction-probe.py --assert-driven-compaction

Assertions (each exits 1 on violation; a bare run asserts nothing and exits 0):
    --assert-driven-compaction            every boundary correlates to a request
                                          this workspace's hook wrote
    --assert-handoff-precedes-compaction  every boundary has a PRE-compaction
                                          handoff before it
    --assert-no-native-compaction         no boundary carries trigger="auto"
    --assert-no-cascade                   no two boundaries within N assistant
                                          turns
"""

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_workspace_root  # noqa: E402

# `checkpoint-save.py` names archives YYYY-MM-DD-HHMMSS_handoff_<kind>_<slug>.md
ARCHIVE_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}-\d{6})_handoff_(?P<kind>[^_]+)_(?P<slug>.+)\.md$"
)

# The kinds written BEFORE a compaction. `compact-manual` and `compact-auto` are
# written by the PostCompact hook, AFTER the event, and including them makes the
# handoff assertion vacuous: every boundary is then satisfied by its own
# post-hoc archive, whose truncated %H%M%S stamp reads earlier than the event it
# followed. Measured on session 31cea474, where four boundaries each "passed"
# against the archive each of them had caused.
PRE_COMPACTION_KINDS = frozenset({"auto", "manual"})

# The harness's own anti-thrash constant: it gives up after the context refills
# within 3 turns of a compaction, 3 times running.
DEFAULT_CASCADE_TURNS = 3


def _iter_transcripts(project: Path, session: str | None) -> tuple[list[Path], list[str]]:
    """Every transcript for this workspace, plus what could not be read."""
    directory = CP.transcript_dir(project)
    if not directory.is_dir():
        return [], [f"no transcript directory at {directory}"]
    paths = sorted(directory.glob("*.jsonl"))
    if session:
        paths = [p for p in paths if p.stem == session]
    return paths, []


def _scan(path: Path) -> tuple[list[dict], str | None]:
    """Boundaries in one transcript, with the assistant-turn index of each."""
    events: list[dict] = []
    assistant_turns = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("type") == "assistant":
                    assistant_turns += 1
                    continue
                if record.get("subtype") != "compact_boundary":
                    continue
                meta = record.get("compactMetadata") or {}
                events.append({
                    "session": path.stem,
                    "timestamp": record.get("timestamp") or "",
                    "assistant_turn": assistant_turns,
                    "trigger": meta.get("trigger"),
                    "preTokens": meta.get("preTokens"),
                    "postTokens": meta.get("postTokens"),
                    "cumulativeDroppedTokens": meta.get("cumulativeDroppedTokens"),
                    "durationMs": meta.get("durationMs"),
                })
    except OSError as exc:
        return [], f"{path.name}: {exc}"
    return events, None


def _archives(project: Path) -> tuple[dict[str, list[dict]], list[str]]:
    """Handoff archives grouped by session slug, read from filenames only."""
    try:
        directory = CP.handoff_dir(project)
    except Exception as exc:  # noqa: BLE001 - an unresolvable overlay is reported, not fatal
        return {}, [f"handoff archive unresolved: {exc}"]
    if not directory.is_dir():
        return {}, [f"no handoff archive at {directory}"]
    grouped: dict[str, list[dict]] = {}
    for path in directory.glob("*.md"):
        match = ARCHIVE_RE.match(path.name)
        if not match:
            continue
        grouped.setdefault(match.group("slug"), []).append({
            "stamp": match.group("stamp"),
            "kind": match.group("kind"),
            "name": path.name,
        })
    for entries in grouped.values():
        entries.sort(key=lambda item: item["stamp"])
    return grouped, []


def _event_stamp(event: dict) -> str:
    """The boundary timestamp, on the UTC clock, in stamp format.

    Compare this against anything SERIALIZED — `compact_requests[].at` is
    `CP.utc_now()`. For a handoff FILENAME use `_event_stamp_local`; see the
    note on that function for why the two must not be merged.
    """
    raw = (event.get("timestamp") or "").replace("Z", "")
    try:
        date, clock = raw.split("T", 1)
    except ValueError:
        return ""
    clock = clock.split(".", 1)[0].replace(":", "")
    return f"{date}-{clock[:6]}"


def _event_stamp_local(event: dict) -> str:
    """The boundary timestamp, converted to the operator's zone, in stamp format.

    Two clocks meet in this file and only one comparison is string-safe without
    a conversion. Transcript timestamps are UTC (`...Z`). Handoff archive
    filenames are stamped with `CP.local_now()` — deliberately, so a handoff
    written at 02:56 in Dubai is not filed under the previous calendar day. On
    any host that is not UTC, comparing the raw strings compares two clocks.

    Measured on 2026-08-23 before this existed: `assert_handoff_precedes`
    reported 40 violations against this workspace's own transcripts, among them
    session c2f703f7 at 2026-08-21T13:08:05Z, whose `auto` handoff is filed as
    `2026-08-21-170420` — 13:04:20 UTC, four minutes BEFORE the boundary it was
    reported as missing. The assertion's whole guarantee was noise.

    Returns "" on an unparseable timestamp, matching `_event_stamp`; the caller
    then finds no archive at or below "" and reports a violation, which is the
    fail-toward-reporting direction `.claude/rules/scope-claims.md` asks for.
    """
    raw = (event.get("timestamp") or "").strip()
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(get_default_tz()).strftime("%Y-%m-%d-%H%M%S")


def _requests(project: Path, session: str) -> list[dict]:
    """Compaction requests this workspace's own Stop hook recorded.

    Reads `compact_requests`, the append-only list the hook writes. Falls back to
    the scalar `compact_requested_at` / `compact_requested_bucket` pair so a
    state file written before the list existed still correlates its one request.
    """
    path = CP.state_path(project, CP.safe_slug(session))
    state = CP.read_json(path)
    entries = state.get("compact_requests")
    if isinstance(entries, list) and entries:
        return [e for e in entries if isinstance(e, dict) and e.get("at")]
    at = state.get("compact_requested_at")
    if at:
        return [{"at": at, "bucket": state.get("compact_requested_bucket")}]
    return []


def _stamp_of_request(entry: dict) -> str:
    raw = str(entry.get("at") or "").replace("Z", "")
    try:
        date, clock = raw.split("T", 1)
    except ValueError:
        return ""
    clock = clock.split(".", 1)[0].replace(":", "")
    return f"{date}-{clock[:6]}"


# ============================================================
# Assertions
# ============================================================

def assert_driven(events: list[dict], project: Path) -> list[str]:
    """CAP-5. Every boundary correlates to a request this hook wrote.

    Deliberately NOT a `trigger` test. `trigger` records HOW the harness was
    asked, not WHO asked: 78 `trigger="manual"` boundaries sit in this
    workspace's transcripts from 2026-07-19 onward, every one of them typed by
    the operator after a HARD_BODY prompt, and 52 of those land in the same
    400k-500k band a driven compaction lands in. Only the state file separates
    the two, because only the hook writes it.
    """
    violations = []
    cache: dict[str, list[dict]] = {}
    for event in events:
        session = event["session"]
        if session not in cache:
            cache[session] = _requests(project, session)
        stamp = _event_stamp(event)
        matched = [
            r for r in cache[session]
            if _stamp_of_request(r) and _stamp_of_request(r) <= stamp
        ]
        if not matched:
            violations.append(
                f"{session} @ {event['timestamp']} (trigger={event['trigger']}, "
                f"preTokens={event['preTokens']}): no compact_requested_at "
                "precedes this boundary - the driven path did not cause it"
            )
    return violations


def assert_handoff_precedes(events: list[dict], project: Path) -> list[str]:
    """Every boundary has a PRE-compaction handoff written before it.

    The kind filter IS the assertion. Without it the check passes for any
    session that compacted even once, satisfied by the archive the compaction
    itself produced.

    Everything here is on the ARCHIVE clock — the operator's zone — because that
    is the clock the filenames are stamped on. `_event_stamp_local` does the
    conversion; `assert_driven` deliberately stays on UTC.
    """
    grouped, notes = _archives(project)
    violations = list(notes)
    previous: dict[str, str] = {}
    for event in events:
        session = event["session"]
        slug = CP.safe_slug(session)
        stamp = _event_stamp_local(event)
        floor = previous.get(session, "")
        found = [
            a for a in grouped.get(slug, [])
            if a["kind"] in PRE_COMPACTION_KINDS and floor < a["stamp"] <= stamp
        ]
        if not found:
            violations.append(
                f"{session} @ {event['timestamp']}: no pre-compaction handoff "
                f"(kind {' or '.join(sorted(PRE_COMPACTION_KINDS))}) between "
                f"{floor or 'session start'} and {stamp}"
            )
        previous[session] = stamp
    return violations


def assert_no_native(events: list[dict]) -> list[str]:
    """Secondary signal. An `auto` event means the driven path did not fire."""
    return [
        f"{e['session']} @ {e['timestamp']}: trigger=auto at preTokens="
        f"{e['preTokens']} - the harness compacted, not this workspace"
        for e in events if e.get("trigger") == "auto"
    ]


def assert_no_cascade(events: list[dict], gap: int) -> list[str]:
    violations = []
    last: dict[str, dict] = {}
    for event in events:
        session = event["session"]
        previous = last.get(session)
        if previous is not None:
            turns = event["assistant_turn"] - previous["assistant_turn"]
            if turns < gap:
                violations.append(
                    f"{session}: {turns} assistant turn(s) between "
                    f"{previous['timestamp']} and {event['timestamp']} "
                    f"(minimum {gap})"
                )
        last[session] = event
    return violations


# ============================================================
# Output
# ============================================================

def render(events: list[dict], skipped: list[str]) -> None:
    if not events:
        print(f"{YELLOW}No compact_boundary events found.{RESET}")
    by_session: dict[str, list[dict]] = {}
    for event in events:
        by_session.setdefault(event["session"], []).append(event)
    for session, rows in by_session.items():
        print(f"\n{BOLD}{session}{RESET}  {GRAY}{len(rows)} event(s){RESET}")
        print(f"{GRAY}{'timestamp':<26}{'trigger':<9}{'pre':>10}{'post':>10}"
              f"{'turn':>7}{RESET}")
        for row in rows:
            colour = RED if row["trigger"] == "auto" else CYAN
            print(f"{row['timestamp']:<26}{colour}{str(row['trigger']):<9}{RESET}"
                  f"{str(row['preTokens']):>10}{str(row['postTokens']):>10}"
                  f"{row['assistant_turn']:>7}")
    if events:
        pre = [e["preTokens"] for e in events if isinstance(e["preTokens"], int)]
        triggers: dict[str, int] = {}
        for event in events:
            key = str(event.get("trigger"))
            triggers[key] = triggers.get(key, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(triggers.items()))
        print(f"\n{BOLD}Summary{RESET} {len(events)} event(s); {summary}")
        if pre:
            print(f"preTokens  min={min(pre)}  median={int(statistics.median(pre))}"
                  f"  max={max(pre)}")

    # Coverage, per .claude/rules/scope-claims.md: name what was left out rather
    # than letting a narrowed scan read as a complete one.
    if skipped:
        print(f"\n{YELLOW}Not covered ({len(skipped)}):{RESET}")
        for note in skipped:
            print(f"  {note}")
    print(f"{GRAY}Reads transcripts only. It cannot see a compaction whose "
          f"transcript was pruned.{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read compaction ground truth from session transcripts."
    )
    parser.add_argument("--session", metavar="ID", help="restrict to one session")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="bound the window")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--assert-driven-compaction", action="store_true")
    parser.add_argument("--assert-handoff-precedes-compaction", action="store_true")
    parser.add_argument("--assert-no-native-compaction", action="store_true")
    parser.add_argument("--assert-no-cascade", action="store_true")
    parser.add_argument("--cascade-turns", type=int, default=DEFAULT_CASCADE_TURNS)
    args = parser.parse_args()

    project = get_workspace_root()
    paths, skipped = _iter_transcripts(project, args.session)
    events: list[dict] = []
    for path in paths:
        found, problem = _scan(path)
        if problem:
            skipped.append(problem)
            continue
        events.extend(found)
    if args.since:
        events = [e for e in events if (e["timestamp"] or "")[:10] >= args.since]
    events.sort(key=lambda e: (e["session"], e["timestamp"]))

    violations: list[str] = []
    if args.assert_driven_compaction:
        violations += assert_driven(events, project)
    if args.assert_handoff_precedes_compaction:
        violations += assert_handoff_precedes(events, project)
    if args.assert_no_native_compaction:
        violations += assert_no_native(events)
    if args.assert_no_cascade:
        violations += assert_no_cascade(events, args.cascade_turns)

    if args.json:
        print(json.dumps(
            {"events": events, "skipped": skipped, "violations": violations},
            indent=2, ensure_ascii=False,
        ))
    else:
        render(events, skipped)
        if violations:
            print(f"\n{RED}{len(violations)} violation(s):{RESET}")
            for item in violations:
                print(f"  {item}")
        elif any([args.assert_driven_compaction,
                  args.assert_handoff_precedes_compaction,
                  args.assert_no_native_compaction,
                  args.assert_no_cascade]):
            print(f"\n{GREEN}All requested assertions hold.{RESET}")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
