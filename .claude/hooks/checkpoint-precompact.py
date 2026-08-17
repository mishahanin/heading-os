#!/usr/bin/env python3
"""checkpoint-precompact.py - Claude Code PreCompact hook.

Steers what a compaction keeps. Claude Code's own contract for this event, read
off the 2.1.228 binary rather than inferred: "Exit code 0 - stdout appended as
custom compact instructions. Exit code 2 - block compaction. Other exit codes -
show stderr to user only but continue with compaction." So whatever this hook
prints becomes part of the brief handed to the summariser, and it fires on the
AUTOMATIC path as well as on a typed `/compact`.

That automatic path is the reason this exists. Nothing in this workspace can
trigger a compaction and nothing here tries to; the harness decides when. What
was missing was any say in the RESULT, and until this hook the workspace had
none: every automatic compaction kept whatever the summariser happened to keep,
including the ones that fire overnight with nobody present.

Three properties, each load-bearing:

**It exits 0 on every path.** Exit 2 blocks the compaction. A hook that refuses
the compaction it was meant to improve turns a context problem into a stuck
session, so no failure inside this file is allowed to reach that exit code.

**It writes nothing.** The event fires before the context is discarded and the
PostCompact hook already owns the write. Two writers on one event is how the
handoff archive would end up with a half-formed record nobody asked for.

**Everything it prints is redacted first.** The output becomes part of a compact
summary, and `checkpoint-save.py` then commits that summary to a tracked file.
An unredacted fact here is a secret in git one hop later. Redaction runs BEFORE
the length bound, not after: truncating first could cut a credential into a
fragment the pattern no longer matches, leaving residue that reads as clean.

The facts appended below the fixed block are the ones a summariser cannot
recover by paraphrasing - the branch, the working tree, the files this session
wrote, the plan's first unchecked item. Every one is optional, and a tree that
yields none of them still gets the fixed block, because a degraded environment
is precisely when the default behaviour is worst.
"""

import json
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
from pathlib import Path

_BOOT = Path(__file__).resolve()
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402

CP.force_utf8()

# Long enough for a handful of git calls on a cold cache, short enough that a
# hung git never delays the compaction it was meant to help.
GIT_TIMEOUT = 5

# The whole output competes for the budget the compaction is reclaiming, so it
# is bounded at the write, exactly as the pointer summary is.
MAX_OUTPUT = 4000

# Per-fact bounds. A working tree mid-refactor can carry hundreds of lines, and
# the tail of that list says nothing the head did not.
MAX_STATUS_LINES = 40
MAX_WRITTEN = 25


KEEP_SET = """\
Compaction instructions from this project's PreCompact hook.

Preserve the following VERBATIM, even at the cost of summary length. Each item
is expensive or impossible to recover from the repository afterwards:

- The objective in force, in the operator's own words wherever they were used.
- The acceptance criteria, and which of them are already met.
- Every decision taken, WITH the reason it was taken. A decision that arrives
  without its reason gets re-litigated by the next turn.
- Exact file paths touched or named, and the exact command lines that matter.
- The next concrete action, stated precisely enough to begin it without
  re-deriving it.
- The last instruction the operator gave, and any question of theirs left open.
- Any constraint or prohibition stated once that still binds.

Drop the following. It is recoverable from the tree, or it is spent:

- The contents of files that were read. The file is still on disk.
- Output of exploratory commands: the searches, listings and greps that located
  something. Keep what was found, not the search.
- Discussion of work already finished and verified.
- Superseded drafts and abandoned approaches, unless a live decision rests on
  why they were abandoned.

The repository is the state; this conversation is not. Where the summary would
paraphrase a file, name the path instead and let the next turn read it."""


FACTS_HEADER = (
    "Facts read from the tree at compaction time, so the summary does not have "
    "to carry them:"
)

# Order is deliberate: what branch, what is uncommitted, what landed, what this
# session touched, where the handoff is, what the plan says to do next.
FACT_LABELS = (
    ("branch", "Current branch"),
    ("status", "Uncommitted changes (git status --short)"),
    ("log", "Last commits (git log --oneline -5)"),
    ("written", "Files this session wrote"),
    ("handoff", "This session's handoff pointer"),
    # "and its first unchecked item" promised a second line that a plan without
    # checkboxes never has, and the plan in force at the first live run was one
    # of those: its criteria are EARS statements, not a checklist.
    ("plan", "Active plan, with its first unchecked item when it has one"),
)


def _git(project: Path, *args: str) -> str:
    """One git read, or an empty string when git cannot answer.

    Total on purpose. Absent git, a directory that is not a repository, and a
    call that times out are all the same answer here: this fact is unavailable
    and the keep-set ships without it.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["git", *args],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"checkpoint-precompact: git {args[0]} unavailable: {exc}",
              file=sys.stderr)
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _head(text: str, limit: int) -> str:
    """The first `limit` lines, saying so when it dropped any."""
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    return "\n".join(lines[:limit]) + f"\n[... {len(lines) - limit} more line(s)]"


def _written(payload: dict, project: Path) -> str:
    """Paths this session wrote AND that still exist, per its own transcript.

    Uses the shared resolver rather than `git status`, which reports that a file
    changed and never who changed it. On a tree with two sessions open, git's
    answer would put a sibling's edits into this session's summary.

    Gone paths are dropped and counted rather than listed. The transcript is an
    append-only record, so a file written early and renamed later stays in it
    forever, and the first live run of this hook proved the cost: the brief named
    two paths from before a rename, and this block exists to tell the next turn
    what to READ. A pointer that resolves to nothing is worse than no pointer.
    The count keeps the drop visible, because an exclusion nobody mentions reads
    as coverage.
    """
    try:
        from scripts.utils.session_scope import files_written
    except ImportError as exc:
        print(f"checkpoint-precompact: session scope unavailable: {exc}",
              file=sys.stderr)
        return ""
    mine = files_written(payload.get("transcript_path"))
    if not mine:
        # None (unreadable transcript) and an empty set both leave nothing to
        # say. The distinction matters to a caller that NARROWS by this set; it
        # does not matter to one that only reports it.
        return ""
    shown = []
    gone = 0
    for path in sorted(mine):
        if not path.exists():
            gone += 1
            continue
        try:
            shown.append(path.resolve().relative_to(project).as_posix())
        except ValueError:
            shown.append(path.as_posix())
    body = _head("\n".join(shown), MAX_WRITTEN) if shown else ""
    if gone:
        note = f"[{gone} more path(s) written earlier no longer exist: renamed or deleted.]"
        body = f"{body}\n{note}" if body else note
    return body


def _handoff_pointer(payload: dict, project: Path) -> str:
    """Where the last handoff for this session sits, if one was written."""
    try:
        root = CP.engine_root()
        pointer = CP.latest_dir(
            CP.handoff_dir(project, root), CP.session_slug(payload)
        ) / "summary.md"
    except Exception as exc:  # noqa: BLE001 - an unresolvable overlay is a missing fact
        print(f"checkpoint-precompact: handoff path unresolved: {exc}",
              file=sys.stderr)
        return ""
    return pointer.as_posix() if pointer.is_file() else ""


def _plan() -> str:
    """The newest plan file and its first unchecked box.

    The plan is the workspace's own record of what remains, which makes it the
    single highest-value pointer in this block: a summariser that keeps nothing
    else can still be pointed at it.
    """
    try:
        from scripts.utils.workspace import get_plans_dir

        plans = sorted(
            (p for p in get_plans_dir().glob("*.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception as exc:  # noqa: BLE001 - no overlay, no plan, no fact
        print(f"checkpoint-precompact: plans unavailable: {exc}", file=sys.stderr)
        return ""
    if not plans:
        return ""
    newest = plans[0]
    item = ""
    try:
        for line in newest.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(("- [ ]", "* [ ]")):
                item = line.strip()
                break
    except OSError as exc:
        print(f"checkpoint-precompact: plan unreadable: {exc}", file=sys.stderr)
    return f"{newest.as_posix()}\n{item}" if item else newest.as_posix()


def collect_facts(payload: dict) -> dict[str, str]:
    """Every fact worth appending, each one optional.

    A raising collector would cost the whole keep-set, so each source answers
    with an empty string when it cannot answer at all.
    """
    project = CP.project_root(payload)
    facts = {
        "branch": _git(project, "rev-parse", "--abbrev-ref", "HEAD"),
        "status": _head(_git(project, "status", "--short"), MAX_STATUS_LINES),
        "log": _git(project, "log", "--oneline", "-5"),
        "written": _written(payload, project),
        "handoff": _handoff_pointer(payload, project),
        "plan": _plan(),
    }
    return {key: value for key, value in facts.items() if value}


def render(facts: dict[str, str] | None) -> str:
    """The text that becomes the compaction's custom instructions.

    Redact, then bound. Reversing those two would let a truncation split a
    credential into a fragment the pattern no longer matches, which reads as a
    clean output and is not one.
    """
    blocks = []
    for key, label in FACT_LABELS:
        value = (facts or {}).get(key)
        if value and value.strip():
            blocks.append(f"{label}:\n{value.strip()}")
    body = KEEP_SET
    if blocks:
        body = f"{KEEP_SET}\n\n{FACTS_HEADER}\n\n" + "\n\n".join(blocks)

    try:
        from scripts.utils.secret_patterns import redact

        body = redact(body)
    except ImportError as exc:
        # The fixed block carries nothing secret; the facts might. Losing the
        # redactor means shipping the block without them rather than shipping
        # them unredacted.
        print(f"checkpoint-precompact: redactor unavailable: {exc}", file=sys.stderr)
        body = KEEP_SET

    if len(body) <= MAX_OUTPUT:
        return body
    note = (
        f"\n\n[Cut at {MAX_OUTPUT} characters. The tree carries the rest: "
        "git status, git log, and the plan file named above.]"
    )
    return body[: MAX_OUTPUT - len(note)].rstrip() + note


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001 - a bad payload still gets the keep-set
        print(f"checkpoint-precompact: unreadable payload: {exc}", file=sys.stderr)
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        facts = collect_facts(payload)
    except Exception as exc:  # noqa: BLE001 - never lose the block over a fact
        print(f"checkpoint-precompact: fact collection failed: {exc}",
              file=sys.stderr)
        facts = {}

    print(render(facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
