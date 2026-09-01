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
# The tree this hook was LOADED from, kept rather than discarded. `handoff_dir()`
# takes it as an argument for a measured reason recorded in its own docstring: it
# used to ask `engine_root()`, which reads the imported module's `__file__`, and
# the copy a hook ends up importing is not always the copy beside it. In a venv
# where the engine is installed as a package, an editable-install finder runs
# ahead of `sys.path` and a bundled hook imports the ENGINE's checkpoint_paths -
# and is then told it is in an engine tree. The sibling hooks checkpoint-save.py
# and checkpoint-inject.py both pass their own walked root for that reason; this
# one asked `CP.engine_root()` until 2026-08-20 and was the last caller of the
# shape the seam exists to remove.
_ROOT = _BOOT.parent.parent.parent
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        _ROOT = _candidate
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

# The same two facts bounded in CHARACTERS, which is the half that actually
# promises a size. A line here is a path, and a path has no length limit, so "40
# lines" and "25 paths" name any number of characters at all, and the six facts
# have to fit at their own bounds or the drop path below becomes the normal path.
# It had. Measured on this tree 2026-08-31: the six blocks came to branch 20,
# status 1030, log 467, written 944, handoff 118, plan 306, assembling to 4361
# against MAX_OUTPUT's 4000, so an ORDINARY compaction shed the whole
# uncommitted-changes block (the most volatile fact in the set) and every
# compaction of this repository shipped without it.
#
# Sized from that measurement rather than guessed. The fixed instruction block
# plus the facts header is 1466 characters and the four unbounded facts were 20,
# 467, 118 and 306; reserving 46, 500, 140 and 400 for them plus 10 for the
# separators leaves 1295 for status and written together, and their labels and
# the gone-count line take 143 of it. Whichever of the two bounds binds first
# wins, so raising MAX_STATUS_LINES can no longer cost a block.
MAX_STATUS_CHARS = 680
MAX_WRITTEN_CHARS = 600


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
    # Two labels were wider than the method twice over. "and its first unchecked
    # item" promised a second line that a plan without checkboxes never has, and
    # "Active plan" asserted which plan is in force from a modification-time
    # sort that cannot establish it.
    ("plan", "Most recently modified file in plans/ (recency only, not the plan "
             "necessarily in force), with its first unchecked item if it has one"),
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


def _data_root() -> Path | None:
    """The private overlay root, when this tree has one."""
    try:
        from scripts.utils.workspace import get_data_root

        return Path(get_data_root()).resolve()
    except Exception as exc:  # noqa: BLE001 - no overlay is a normal answer
        print(f"checkpoint-precompact: data root unresolved: {exc}", file=sys.stderr)
        return None


def _ref(path: Path, project: Path) -> str:
    """Project-relative first, data-root-relative next, absolute only as a last
    resort.

    An absolute path here carries the operator's home directory and the name and
    location of their private overlay into a compaction summary that a later hook
    writes to a file. `redact()` cannot help: it removes credential-shaped spans
    and has no concept of a private path. So the fix is at the source, and it is
    the form the rest of the checkpoint system already uses - the same two-step
    that `scripts/checkpoint-paths.py` prints for the skill. The pointer stays
    resolvable either way, because the data-root-relative form is exactly what
    `get_plans_dir()` and the @-reference resolve.
    """
    try:
        return path.resolve().relative_to(project).as_posix()
    except ValueError:
        pass
    root = _data_root()
    if root is not None:
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _head(text: str, limit: int, max_chars: int | None = None) -> str:
    """The first `limit` lines, then whole lines off the tail until the result
    fits `max_chars`, saying so when it dropped any.

    The character bound was added 2026-08-31 for the reason recorded beside
    MAX_STATUS_CHARS: a line bound cannot promise a size when the lines are
    paths, and a fact that overruns its share of MAX_OUTPUT is not trimmed by the
    caller below, it is deleted whole.

    Only WHOLE lines are ever dropped, for the reason the drop loop already
    records: a cut inside a path is a pointer that resolves to nothing.
    """
    lines = text.splitlines()
    if len(lines) <= limit and (max_chars is None or len(text) <= max_chars):
        return text

    def rendered(kept: list[str]) -> str:
        body = "\n".join(kept)
        if len(kept) == len(lines):
            return body
        return body + f"\n[... {len(lines) - len(kept)} more line(s)]"

    kept = lines[:limit]
    if max_chars is not None:
        # Measure what will actually be returned, suffix included, so the bound
        # holds for the string the caller gets rather than for the body alone.
        while kept and len(rendered(kept)) > max_chars:
            kept.pop()
    return rendered(kept)


def _mtime(path: Path) -> float:
    """Modification time, or -inf when the filesystem will not say.

    Callers below have already dropped the paths the tree no longer has, so a
    raise here is a race (removed between the two calls) or a permission
    problem, which is rare enough to be worth a line on stderr. Either way the
    path sorts LAST: a file whose age cannot be read does not belong at the head
    of a list whose whole purpose is to say what to read first.
    """
    try:
        return path.stat().st_mtime
    except OSError as exc:
        print(f"checkpoint-precompact: age unavailable for {path}: {exc}",
              file=sys.stderr)
        return float("-inf")


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

    What survives the head cut is the MOST RECENTLY written, not the
    alphabetically first. `sorted(mine)` orders by path components, which has
    nothing to do with the work, and the cost was measured 2026-08-31 against
    this session's own transcript: of 1786 recorded paths, 1651 still on disk,
    the 25 shown were every `.claude/agents/*.md` and `.claude/hooks/*` in the
    tree and not one of the files edited in the preceding hour. Those were in the
    1624 the cut discarded, under a heading that says "Preserve the following
    VERBATIM" inside a block that exists to tell the next turn what to READ.
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
    live = []
    gone = 0
    for path in mine:
        if path.exists():
            live.append(path)
        else:
            gone += 1
    # Newest first, ties alphabetical. `mine` is a SET, so its iteration order
    # varies run to run and a bare mtime sort would order equal timestamps
    # differently on two runs over one tree; the second key makes the block
    # reproducible, which matters for a text a later hook commits to a file.
    shown = [_ref(path, project)
             for path in sorted(live, key=lambda p: (-_mtime(p), p.as_posix()))]
    body = _head("\n".join(shown), MAX_WRITTEN, MAX_WRITTEN_CHARS) if shown else ""
    if gone:
        note = f"[{gone} more path(s) written earlier no longer exist: renamed or deleted.]"
        body = f"{body}\n{note}" if body else note
    return body


def _handoff_pointer(payload: dict, project: Path) -> str:
    """Where the last handoff for this session sits, if one was written."""
    try:
        pointer = CP.latest_dir(
            CP.handoff_dir(project, _ROOT), CP.session_slug(payload)
        ) / "summary.md"
    except Exception as exc:  # noqa: BLE001 - an unresolvable overlay is a missing fact
        print(f"checkpoint-precompact: handoff path unresolved: {exc}",
              file=sys.stderr)
        return ""
    return _ref(pointer, project) if pointer.is_file() else ""


def _plan(project: Path) -> str:
    """The most recently MODIFIED file in the plans directory, and its first
    unchecked box.

    The label says exactly that, and no longer says "active". The method is one
    `st_mtime` sort, which establishes recency and nothing else, and the two come
    apart routinely: a git pull or a checkout rewrites mtimes, and a plan that is
    genuinely in force gets ARCHIVED out of this directory the moment its slice
    lands. Measured on the tree that produced this hook - 42 files in plans/, the
    newest four days stale and unrelated, and the plan actually in force absent
    because it had just been archived. Calling that "the active plan" hands a
    summariser a wrong fact under an instruction to preserve it verbatim
    (.claude/rules/scope-claims.md).
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
    ref = _ref(newest, project)
    others = len(plans) - 1
    tail = f"\n[{others} other plan file(s) not read.]" if others else ""
    return (f"{ref}\n{item}{tail}" if item else f"{ref}{tail}")


def collect_facts(payload: dict) -> dict[str, str]:
    """Every fact worth appending, each one optional.

    A raising collector would cost the whole keep-set, so each source answers
    with an empty string when it cannot answer at all.
    """
    project = CP.project_root(payload)
    facts = {
        "branch": _git(project, "rev-parse", "--abbrev-ref", "HEAD"),
        "status": _head(_git(project, "status", "--short"), MAX_STATUS_LINES,
                        MAX_STATUS_CHARS),
        "log": _git(project, "log", "--oneline", "-5"),
        "written": _written(payload, project),
        "handoff": _handoff_pointer(payload, project),
        "plan": _plan(project),
    }
    return {key: value for key, value in facts.items() if value}


# The order blocks are DROPPED in when the output overflows, which is not the
# order they are shown in. `status` and `log` are one git command away, and
# `written` is the tree itself; the handoff pointer and the plan are the two a
# resumed session cannot re-derive, so they are the last to go.
DROP_ORDER = ("status", "written", "log", "branch", "plan", "handoff")

def _note(dropped: list[str]) -> str:
    """The tail line naming what the drop loop omitted.

    A function rather than the reserved constant it replaces. Until 2026-08-31
    the loop aimed at `MAX_OUTPUT - 320` while the note it then wrote measured
    187 on this tree, so a body landing between 3681 and 3813 characters was cut
    by one WHOLE extra fact to make room for 133 characters nothing would use.
    Latent rather than live at the time (the measured run landed at 3326 after
    its first drop), and cheaper to remove than to keep explaining: the loop now
    asks how long the note it is about to write actually is.
    """
    return (
        f"\n\n[Cut to fit {MAX_OUTPUT} characters. Omitted whole: "
        + (", ".join(dropped) if dropped else "nothing")
        + ". Read them from the tree with `git status`, `git log`, and "
        "`python scripts/checkpoint-paths.py`.]"
    )


def _assemble(blocks_by_key: dict[str, str]) -> str:
    """The fixed keep-set, plus whichever fact blocks are still in play."""
    ordered = [blocks_by_key[key] for key, _label in FACT_LABELS
               if key in blocks_by_key]
    if not ordered:
        return KEEP_SET
    return f"{KEEP_SET}\n\n{FACTS_HEADER}\n\n" + "\n\n".join(ordered)


def render(facts: dict[str, str] | None) -> str:
    """The text that becomes the compaction's custom instructions.

    Redact, then bound. Reversing those two would let a truncation split a
    credential into a fragment the pattern no longer matches, which reads as a
    clean output and is not one.
    """
    blocks_by_key = {}
    for key, label in FACT_LABELS:
        value = (facts or {}).get(key)
        if value and value.strip():
            blocks_by_key[key] = f"{label}:\n{value.strip()}"

    # Redact PER BLOCK, before anything is assembled or dropped.
    #
    # The rule is unchanged - redact, then bound - but the bounding step now
    # re-assembles from these blocks, so redacting the concatenated body once
    # would have left the re-assembled text unredacted. Doing it here means
    # every string that can reach the output has already been through it.
    try:
        from scripts.utils.secret_patterns import redact

        blocks_by_key = {key: redact(value) for key, value in blocks_by_key.items()}
    except Exception as exc:  # noqa: BLE001
        # The fixed block carries nothing secret; the facts might. Losing the
        # redactor means shipping the block without them rather than shipping
        # them unredacted.
        #
        # Exception, not ImportError. A SyntaxError in the module this line
        # imports is as fatal as its absence and is the likelier of the two here,
        # because secret_patterns.py is edited and a compaction can fire mid-edit.
        # The sibling hook checkpoint-save.py guards the identical import the same
        # way; narrowing it here would have turned a broken module into a lost
        # keep-set.
        print(f"checkpoint-precompact: redactor unavailable: {exc}", file=sys.stderr)
        return KEEP_SET

    body = _assemble(blocks_by_key)
    if len(body) <= MAX_OUTPUT:
        return body

    # Drop WHOLE blocks, and name the ones dropped.
    #
    # This used to slice the concatenated body by CHARACTER and append a note
    # saying the tree carries "the plan file named above". Measured on this
    # repository 2026-08-25: the output reached exactly 4001 characters, the
    # last two blocks (the handoff pointer and the plan) were cut out entirely,
    # and the note then named a plan that appeared nowhere in the text - inside
    # a block whose opening instruction is "Preserve the following VERBATIM".
    # The cut also landed mid-path, ending the written-files list at
    # `.claude/ski`, which is the dangling pointer `_written()` goes to
    # deliberate lengths to avoid ("A pointer that resolves to nothing is worse
    # than no pointer") handed straight back under a verbatim instruction.
    #
    # DROP_ORDER is not the display order. `status` and `log` are one git
    # command away and `written` is the tree itself; the handoff pointer and the
    # plan are what a resumed session cannot re-derive, so they go last.
    dropped: list[str] = []
    kept = dict(blocks_by_key)
    for key in DROP_ORDER:
        if len(_assemble(kept)) + len(_note(dropped)) <= MAX_OUTPUT:
            break
        if key in kept:
            dropped.append(dict(FACT_LABELS)[key])
            del kept[key]

    body = _assemble(kept)
    note = _note(dropped)
    if len(body) + len(note) > MAX_OUTPUT:
        # Even the fixed block plus the note overflows. Keep the block; a
        # truncated INSTRUCTION is worse than a missing note.
        return KEEP_SET
    return body + note


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
