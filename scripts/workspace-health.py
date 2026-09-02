#!/usr/bin/env python3
"""
Workspace Health Check for 31C CEO Command Center

Validates file references, context freshness, agent counts,
pipeline health, people completeness, and outputs inventory.

Usage:
    python scripts/workspace-health.py                    # full health check
    python scripts/workspace-health.py --section context  # run one section
    python scripts/workspace-health.py --section refs     # check references only
    python scripts/workspace-health.py --section counts   # check agent counts
    python scripts/workspace-health.py --section pipeline # check pipeline health
    python scripts/workspace-health.py --section people   # check people completeness
    python scripts/workspace-health.py --section outputs  # check outputs inventory
    python scripts/workspace-health.py --section datastore # check datastore status
"""

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.repo_files import ignored_paths_or_none
from scripts.utils.workspace import (
    get_workspace_root, get_context_dir, get_outputs_dir, get_datastore_dir,
    get_templates_dir, get_data_root, get_default_tz,
)

WORKSPACE = get_workspace_root()

COMMANDS_DIR = WORKSPACE / ".claude" / "commands"
SKILLS_DIR = WORKSPACE / ".claude" / "skills"


def context_dir() -> Path:
    """Resolved at call time, never at import: `get_context_dir()` reads
    HEADING_OS_DATA on every call, and a module-level constant asked once
    during its own import and stored the answer."""
    return get_context_dir()


def outputs_dir() -> Path:
    return get_outputs_dir()


def datastore_dir() -> Path:
    return get_datastore_dir()

# The runtime catalog of every script, reference file and system. It lives in
# the private data overlay, so it is absent on a public engine clone; resolved
# through the data-root seam, never as a literal.
REFERENCE_INDEX_RELPATH = Path("reference") / "workspace-overview.md"


def ok(msg):
    print(f"  {GREEN}OK{RESET}  {msg}")


def warn(msg):
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def action(msg):
    print(f"  {RED}ACTION{RESET}  {msg}")


def header(title):
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")


# One backticked token. The index writes paths inline in prose and bullets, not
# in a table, so nothing here may key off `|`.
_BACKTICK_TOKEN = re.compile(r"`([^`\s]+)`")

# A workspace-relative file: at least one directory component, a dotted suffix,
# and only characters a real path uses. The leading class excludes `/`, `~`,
# `<` and `{`, which is what drops absolute paths, home paths and the
# `<data-root>/...` documentation form.
_RELATIVE_FILE = re.compile(
    r"^[A-Za-z0-9_.][A-Za-z0-9._/-]*/[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,5}$")

# Path-shaped but not a literal path: a parent traversal in an example, or a
# date stamp standing in for the run that will produce the file.
_NOT_A_LITERAL_PATH = re.compile(r"\.\.|YYYY|MM-DD|HH-MM")


# Two registries of paths whose absence is not a documentation defect. Both are
# EXACT tokens, never shapes: a rule of the form "ignore anything that looks
# like an example" widens silently, and the whole point of this section is that
# its findings can be believed. Neither may accumulate cover for a reference
# nobody writes any more, so an entry the live index no longer names fails
# `tests/test_a_health_check_that_cried_wolf_seventy_three_times.py`. That check
# lives in the test rather than in this section, because the section also runs
# over scratch fixtures and would judge every one of them against the operator's
# index.

# A stand-in inside a sentence ABOUT paths, naming no real file.
PLACEHOLDER_PATHS = {
    "scripts/x.py":
        "stand-in for any root-relative script in the shell-drift guard's "
        "description; names no script that has ever existed",
    ".claude/hooks/x.py":
        "the same stand-in, in the same sentence, for the hooks directory",
}

# A path whose absence is its normal state, and which no root's ignore rules
# exclude, so `_ignored_by_every_root` cannot derive it. Anything git already
# ignores belongs there and must NOT be duplicated here. Two shapes qualify: a
# file written only while something runs, and an override the resolver that
# reads it documents as normally absent.
ABSENT_BY_DESIGN_PATHS = {
    # The two `outputs/` keys carry `leak-guard: ok` because this dict is a
    # REGISTRY OF STRINGS, not a path builder. Both are only ever the right
    # operand of `in` (lines 419 and 423) or a message lookup (line 463); no
    # branch joins either to a root, which is the construction the guard
    # exists to stop. They are the comparison-key case its own suppression
    # comment names.
    "outputs/operations/handoff.md":  # leak-guard: ok (comparison key)
        "written only when a session hands off, read by /prime at the next "
        "start, then moved into outputs/operations/handoff-archive/",
    "outputs/browser/browser-cdp.json":  # leak-guard: ok (comparison key)
        "the CDP endpoint scripts/browser.py writes while a browser is "
        "attached, and removes when it is not",
    "config/claude-models.json":
        "the optional first step of the Claude model resolution order, which "
        "the index itself records as normally absent; the cache and the live "
        "API answer when it is",
    ".claude/ralph-loop.local.md":
        "written by the ralph-loop plugin for the duration of a loop and "
        "removed on every terminal path it has",
}

# Paths that are absent because the thing was DELETED, and are named on purpose
# in a record that says so. A different class from ABSENT_BY_DESIGN_PATHS above,
# which is about files that come and go at runtime.
#
# Why this exists. The reference index is a catalogue, and a catalogue that
# silently drops a retired subsystem is how someone re-creates it. So a removal
# gets a bullet naming what went, in which commit, and what carries the property
# now. Every one of those bullets then read here as a MISSING reference: measured
# 2026-09-02, 24 of the 27 flagged paths were removal records, and each one is a
# red ACTION line the operator can do nothing about. A check whose findings are
# mostly unactionable is a check that gets skimmed, and the three real ones were
# sitting in the same list.
#
# Hand-maintained on purpose, and narrowly. The alternative considered was a
# regex over the sentence around the path ("RETIRED", "was removed", ...), which
# is a guess about prose: it would suppress a live path in a sentence that merely
# mentions a deletion, and miss a removal record worded any other way. A named
# path with a stated reason is a claim someone made and can be held to, and the
# floor below holds it: an entry whose path EXISTS again fails, so this registry
# cannot outlive its sites.
REMOVED_AND_RECORDED_PATHS = {
    "config/classification.json":
        "replaced by config/routing-map.yaml, removed 2026-06-14",
    "corporate/requirements.txt":
        "the copy-into-workspace path; setup.py reads .corporate-repo/"
        "requirements.txt in place since the copy step was deleted",
    "docs/SETUP-GUIDE.md":
        "retired doc; its links were repointed to docs/DEPLOYMENT.md",
    "reference/31c-docs-dark-theme.css":
        "removed with the setup-guide builder",
    "scripts/build-setup-guide-html.py":
        "removed with docs/SETUP-GUIDE.md and the dark theme",
    "scripts/canary-smoke.py":
        "deleted 2026-08-23 with the canary gate (22e6997)",
    "scripts/depth-gate.py":
        "deleted 2026-08-07 with the depth calibration (28ce7e4)",
    "scripts/eval-drift-daemon.py":
        "retired 2026-08-03 (58aa77d); its input set was empty by construction",
    "scripts/export-sync.py":
        "left with the AIOS segregation, 2026-04-25",
    "scripts/promote-corporate.py":
        "deleted 2026-08-23 with the canary gate (22e6997)",
    "scripts/rollback-corporate.py":
        "deleted 2026-08-23 with the canary gate (22e6997)",
    "scripts/scrutinize-fp-aggregate.py":
        "removed 2026-08-09 with its _fp_aggregate.md",
    "scripts/slice-cycle-time.py":
        "deleted 2026-08-07; it read a ledger nothing writes (70e10bb)",
    "scripts/slice-depth.py":
        "deleted 2026-08-07 with the depth calibration (28ce7e4)",
    "scripts/slice-rollback.py":
        "deleted 2026-08-07; it read a ledger nothing writes (70e10bb)",
    "scripts/sync-all-execs.py":
        "no-op stub removed 2026-08-20 (0ce4506)",
    "scripts/utils/canopus_freeze.py":
        "deleted 2026-08-07 with the freeze lifecycle (28ce7e4)",
    "scripts/utils/canopus_gate.py":
        "deleted 2026-08-07 with the freeze lifecycle (8520623)",
    "scripts/utils/slice_depth.py":
        "deleted 2026-08-07 with the depth calibration (28ce7e4)",
    "scripts/workspace-sync.py":
        "the copy-and-orphan-delete sync engine, removed 2026-06-26",
    "tests/test_canary_branch_switch.py":
        "deleted 2026-08-23 with scripts/canary-smoke.py (22e6997)",
    "tests/test_eval_drift_aggregation.py":
        "deleted 2026-08-03 with the eval-drift daemon (58aa77d)",
    "tests/test_slice_rollback.py":
        "deleted 2026-08-07 with scripts/slice-rollback.py (70e10bb)",
}


def _resolves_under(path_str: str, roots) -> bool:
    """Does `path_str` name a file that exists under any of `roots`?

    Two forms, because the index writes both. The plain form is relative to a
    root (`scripts/send-email.py`, `context/pipeline.md`). The other is written
    from the directory that HOLDS the roots, and appears wherever the index has
    to distinguish the two stores by repo (`.heading-os-data/.memory-index/
    index.db` beside `.heading-os/.memory-index-code/index.db`). The leading
    component is compared against each root's OWN directory name, so a clone
    that lives somewhere else, or under a different name, still resolves; no
    repo name is written down here.
    """
    head, _, tail = path_str.partition("/")
    for root in roots:
        if (root / path_str).exists():
            return True
        if tail and head == root.name and (root / tail).exists():
            return True
    return False


def _ignored_by_every_root(paths, roots) -> set:
    """The subset of `paths` that EVERY root's ignore rules exclude.

    A runtime artefact is derived here rather than listed: a path no root would
    ever track is a file something writes while it runs, and its absence is the
    normal state, not an action item. `.fireside/daemon.pid` and
    `.daemon-state/config.yaml` are the shape.

    EVERY root, never any root. The engine ignores whole DATA directories -
    `plans/`, `outputs/`, `datastore/`, `threads/` - because they belong to the
    overlay, not because anything about them is runtime. MEASURED 2026-09-02
    over this index: the union rule matched 28 paths and the intersection 3, and
    25 of the 28 were the archived-plan and stale-thread references this section
    exists to report. A union here would have deleted the findings instead of
    the noise.

    Fails toward over-reporting: if git cannot answer (not installed, not a
    repository), nothing is excluded and the paths stay in the report.
    """
    if not paths:
        return set()
    excluded = set(paths)
    for root in roots:
        try:
            ignored = ignored_paths_or_none(sorted(paths), root)
        except OSError:
            # `ignored_paths_or_none` reports git's own non-verdict exit codes
            # as None, but it cannot report a git that never STARTED (not on
            # PATH), which surfaces here as FileNotFoundError. So this branch
            # stays. The old `subprocess.SubprocessError` half is dropped: the
            # shared helper passes no `timeout=`, so TimeoutExpired cannot
            # arise, and it passes `check=False`, so neither can
            # CalledProcessError. That does drop the 30s bound this call used to
            # carry, which is the same unbounded shape the two other callers of
            # the helper already run with over the whole tree.
            return set()
        # None = git could not answer (not a repository, bad option). The two
        # verdict exits, 0 (some path ignored) and 1 (none), come back as a set.
        if ignored is None:
            return set()
        excluded &= ignored
        if not excluded:
            return set()
    return excluded


def _archived_plan(path_str: str, roots):
    """Where an active-plan reference went, if it was archived.

    `.claude/rules/documentation.md` § Plans Lifecycle makes archiving the
    normal end of every plan: `git mv plans/<f> plans/archive/<year>/<f>`. The
    file still exists and the index's pointer at it no longer does, which is a
    real finding with a known fix, so this returns the destination rather than
    suppressing it. Only `plans/<file>` is considered; a path already under
    `plans/archive/` is left alone.
    """
    head, _, tail = path_str.partition("/")
    if head != "plans" or not tail or "/" in tail:
        return None
    for root in roots:
        archive = root / "plans" / "archive"
        if not archive.is_dir():
            continue
        for year in sorted(archive.iterdir()):
            if (year / tail).exists():
                return f"plans/archive/{year.name}/{tail}"
    return None


# Sections that RAN but verified nothing: an absent target, an empty corpus. A
# zero from one of these means "no answer", not "clean". Without this the
# summary folds the two together and prints "Section 'refs' passed." one line
# under the section saying it checked 0 paths, which is the vacuous-pass shape
# `.claude/rules/scope-claims.md` names, one level up from the section itself.
INCONCLUSIVE: list[str] = []


def inconclusive(section: str, why: str) -> None:
    """Record that `section` produced no verdict. Never an issue count."""
    INCONCLUSIVE.append(f"{section}: {why}")


def _reference_index_paths(text: str):
    """Split the index's backticked tokens into literal paths and skips.

    Returns (paths, skipped). `skipped` is the count of tokens that look like a
    path but cannot be existence-checked, so the report can say what it left
    out rather than imply it covered everything (`.claude/rules/scope-claims.md`).
    """
    literal, skipped = set(), set()
    for token in _BACKTICK_TOKEN.findall(text):
        tail = token.rsplit("/", 1)[-1]
        if "/" not in token or "." not in tail:
            # A bare filename, a flag, a config key, ordinary code prose. The
            # index is full of these (`.env`, `SKILL.md`, `push-all.py`); they
            # name no directory, so no root can resolve them.
            continue
        if _RELATIVE_FILE.match(token) and not _NOT_A_LITERAL_PATH.search(token):
            literal.add(token)
        else:
            skipped.add(token)
    return sorted(literal), len(skipped)


def check_reference_validation():
    """Check that the paths named in the reference index exist.

    The index is the data overlay's runtime catalog of every script, reference
    file and system, resolved through the data-root seam.

    History, because the previous target is a trap worth not re-entering. This
    check read the ENGINE CLAUDE.md, looking for a "Reference Resources" table
    that file has never carried (`git log -S` finds no commit that added one),
    so the loop ran zero times and the section printed an unconditional green,
    "All reference paths resolve to existing files", over zero paths. Anchoring
    that scan at a markdown HEADING rather than a bare substring was itself a
    fix: `"Reference Resources" in line` fired on any prose sentence carrying
    the phrase, and the flag then stayed on until the next `## `, so unrelated
    table rows were existence-checked and failed the run. That reasoning still
    binds anything that scans for a named section, and it is why nothing below
    matches a heading or a phrase: the whole index file IS the reference
    section, so there is no section to find and no substring to guess at.

    Zero is never a pass, and since 2026-09-02 it is not a zero either. A
    present, readable index over which this section inspected NOTHING is a
    checker that stopped reading, so it REFUSES: one issue, non-zero exit, the
    floor `scripts/ste-check.py` and `scripts/validate-crm-schema.py` already
    carry over their own corpora. The narrower outcome above it - no index at
    all, which is the normal state of a public clone with no data overlay - is
    deliberately NOT folded into that floor. There is no corpus to have read, and
    exiting 1 on every public clone would retire the tool rather than arm it. It
    stays inconclusive, which the summary already refuses to print as clean.
    """
    header("Reference Validation")

    data_root = get_data_root()
    index = data_root / REFERENCE_INDEX_RELPATH
    if not index.exists():
        # First-class outcome, not an edge case: a public engine clone has no
        # data overlay, so the index is simply absent. A missing overlay is not
        # a missing file (the sibling `check_docs_sync` treats templates/ the
        # same way).
        warn(f"reference index not present at "
             f"<data-root>/{REFERENCE_INDEX_RELPATH.as_posix()}; "
             f"0 paths checked (this section verified nothing). Every path it "
             f"would name is not checkable on this clone, never missing")
        inconclusive("refs", "no reference index (no data overlay on this clone)")
        return 0

    try:
        index_text = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # `main`'s loop was UNGUARDED when this was written, so a read that
        # raised here did not fail one section: it ended the run with a
        # traceback and no summary at all. `run_one` now catches it too, and
        # this handler still earns its place: it names the FILE and keeps the
        # section's own verdict, where `run_one` can only say the section
        # died. That was MEASURED on 2026-09-01 in
        # `check_context_freshness` and fixed there; four sibling reads in this
        # same file, this one included, kept the defect. Reported as an issue
        # rather than skipped, because a reference check that could not open the
        # index has not found the references sound.
        action(f"reference index could not be read ({type(exc).__name__}); "
               f"0 paths checked")
        inconclusive("refs", "the reference index could not be read")
        return 1

    paths, skipped = _reference_index_paths(index_text)

    # Which root each path is relative to. The workspace is two layered roots,
    # and the index names files in both: `scripts/send-email.py` is engine,
    # `context/pipeline.md` is data overlay. A path resolves if EITHER root
    # holds it; it is missing only when neither does. Deriving the root from
    # `get_routing_destination()` instead was measured and rejected: routing is
    # a sharing decision, not a location, so `corporate` files such as
    # `docs/GETTING-STARTED.md` physically sit in the overlay and 11 real index
    # paths resolved in the root opposite the one routing named. Those 11 would
    # print as "Missing:" every run, which is how a useful check gets switched
    # off. The cost of the union rule is that it cannot detect a file filed
    # under the wrong root; that is `scripts/classification-health.py`'s job.
    roots = [WORKSPACE]
    if data_root != WORKSPACE:
        roots.append(data_root)

    if not paths:
        action("the reference index names no workspace-relative file paths; "
               "0 paths checked, 0 inspected (this section verified nothing "
               "and refuses to report clean)")
        inconclusive("refs", "the reference index names no paths")
        return 1

    unresolved = [p for p in paths if not _resolves_under(p, roots)]
    resolved = len(paths) - len(unresolved)

    placeholder = [p for p in unresolved if p in PLACEHOLDER_PATHS]
    by_design = [p for p in unresolved if p in ABSENT_BY_DESIGN_PATHS]
    recorded = [p for p in unresolved if p in REMOVED_AND_RECORDED_PATHS]
    rest = [p for p in unresolved
            if p not in PLACEHOLDER_PATHS
            and p not in ABSENT_BY_DESIGN_PATHS
            and p not in REMOVED_AND_RECORDED_PATHS]
    runtime = sorted(_ignored_by_every_root(rest, roots))
    rest = [p for p in rest if p not in set(runtime)]

    # The registry may not outlive its sites. A path listed as removed that now
    # EXISTS again is either a re-creation nobody updated the record for, or an
    # entry added to silence a finding that was real. Both are worse than the
    # noise this registry removes, so both fail here rather than passing quietly.
    revived = sorted(p for p in REMOVED_AND_RECORDED_PATHS
                     if _resolves_under(p, roots))
    for path_str in revived:
        action(f"Recorded as removed, but present again: {path_str} - "
               f"{REMOVED_AND_RECORDED_PATHS[path_str]}. Update the record and "
               f"drop the REMOVED_AND_RECORDED_PATHS entry.")

    moved = [(p, dest) for p in rest if (dest := _archived_plan(p, roots))]
    missing = [p for p in rest if p not in {p for p, _ in moved}]

    for path_str, dest in moved:
        action(f"Moved: {path_str} is now {dest}")
    for path_str in missing:
        action(f"Missing: {path_str}")

    # Inspected means the existence question was actually ASKED of that path.
    # An excluded path was never asked, so it cannot be counted as coverage -
    # which is what makes the floor below reachable rather than decorative: an
    # index whose every path landed in an exclusion bucket has been read by a
    # section that settled nothing.
    inspected = (len(paths) - len(placeholder) - len(by_design)
                 - len(recorded) - len(runtime))
    flagged = len(moved) + len(missing) + len(revived)

    if skipped:
        ok(f"{skipped} path-shaped token(s) skipped as globs, placeholders, "
           f"absolute or home-relative; not checked")
    for path_str in sorted(placeholder):
        ok(f"placeholder, not checked: {path_str} - {PLACEHOLDER_PATHS[path_str]}")
    for path_str in sorted(by_design):
        ok(f"absent by design, not checked: {path_str} - "
           f"{ABSENT_BY_DESIGN_PATHS[path_str]}")
    for path_str in runtime:
        ok(f"absent by design, not checked: {path_str} - ignored by every "
           f"root in this workspace, so nothing ever tracks it")
    for path_str in sorted(recorded):
        ok(f"removed and recorded, not checked: {path_str} - "
           f"{REMOVED_AND_RECORDED_PATHS[path_str]}")

    # Coverage beside verdict, both out of the same arithmetic: a section that
    # stopped reading cannot look clean, because the count it inspected is
    # printed next to the count it flagged (`.claude/rules/scope-claims.md`).
    # Every excluded bucket is named with its size for the same reason - silence
    # about an exclusion reads as coverage.
    coverage = (f"{resolved} of {len(paths)} reference path(s) resolve; "
                f"{inspected} inspected, {flagged} flagged "
                f"({len(moved)} moved, {len(missing)} missing, "
                f"{len(revived)} recorded-but-present); excluded: "
                f"{len(runtime) + len(by_design)} absent by design, "
                f"{len(recorded)} removed and recorded, "
                f"{len(placeholder)} prose placeholder(s)")
    if inspected == 0:
        action(f"{coverage}; this section verified nothing and refuses to "
               f"report clean")
        inconclusive("refs", "every reference path was excluded or unsettled")
        return max(flagged, 1)
    (warn if flagged else ok)(coverage)
    return flagged


def check_context_freshness(max_days=30):
    """Check freshness markers on context files."""
    header("Context Freshness")
    issues = 0
    today = datetime.now(get_default_tz())

    context_files = list(context_dir().glob("*.md"))
    if not context_files:
        action("No context files found!")
        return 1

    for f in sorted(context_files):
        try:
            content = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # No handler at all sat here until 2026-09-01. This is a HEALTH
            # CHECK, and it ran before `/push-updates`. MEASURED that day on
            # two context files, one clean and one carrying a lone 0xe9: it
            # reported on the clean file, then died on the next one with a
            # traceback naming a codec, a byte and an offset but no filename,
            # so every later context file went unchecked and the run produced
            # no verdict at all.
            #
            # Counted as an issue rather than skipped quietly: a freshness
            # check that cannot read a file has not found it fresh.
            action(f"{f.name}: could not be read ({type(exc).__name__}), so "
                   f"its freshness is unknown")
            issues += 1
            continue
        lines = content.split("\n")[:10] if content else []

        # Check for freshness marker: > Last verified|updated: YYYY-MM-DD (first 10 lines).
        # Both verbs count as a freshness signal — both carry a date stamp; the
        # workspace convention uses "Verified" (re-confirmed) and "Updated" (content changed).
        match = None
        for line in lines:
            match = re.match(r">\s*Last (?:verified|updated):\s*(\d{4}-\d{2}-\d{2})", line, re.IGNORECASE)
            if match:
                break
        if match:
            # The regex validates digit SHAPE, never the calendar, so
            # `> Last verified: 2026-02-31` reached `strptime` and raised
            # ValueError. `main` ran these checks in an unguarded loop when
            # this was written, so one malformed marker aborted every remaining
            # section with a traceback and no summary, in front of
            # `/push-updates`. `run_one` now catches that; this handler stays
            # because it names the marker rather than only the section. The sibling
            # `check_doc_versions` wraps the identical parse; this one was
            # missed. A date nobody can parse is an unverified file, not a
            # crash, so it counts as an issue and the run continues.
            try:
                verified_date = datetime.strptime(
                    match.group(1), "%Y-%m-%d").replace(tzinfo=get_default_tz())
            except ValueError:
                warn(f"{f.name}: malformed freshness date {match.group(1)}; "
                     f"freshness NOT checked")
                issues += 1
                continue
            age_days = (today - verified_date).days
            if age_days > max_days:
                warn(f"{f.name}: Last verified {age_days} days ago ({match.group(1)})")
                issues += 1
            else:
                ok(f"{f.name}: Verified {age_days} days ago ({match.group(1)})")
        else:
            # Fall back to file modification time
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=get_default_tz())
            age_days = (today - mtime).days
            if age_days > max_days:
                warn(f"{f.name}: No freshness marker, modified {age_days} days ago")
                issues += 1
            else:
                warn(f"{f.name}: No freshness marker (modified {age_days}d ago - add one)")

    return issues


def check_agent_counts():
    """Count the commands and skills on disk, and flag lowercase `skill.md`.

    Scope, stated because the old docstring and the old section title overstated
    it: this opens nothing and compares nothing. It counts `.claude/commands/*.md`
    and the `.claude/skills/` directories carrying a SKILL.md, prints both lists,
    and returns one issue per skill whose manifest is lowercase. The docstring
    said "compare to CLAUDE.md" and the header said "Verification" while no line
    in the function reads CLAUDE.md, so a CLAUDE.md claiming 22 commands over a
    tree of 19 passed green under a title promising the opposite
    (`.claude/rules/scope-claims.md`). Naming the count is the whole method, so
    the title now says so.
    """
    header("Agent Count (commands and skills found on disk)")
    issues = 0

    # Count actual commands
    commands = list(COMMANDS_DIR.glob("*.md")) if COMMANDS_DIR.exists() else []
    command_count = len(commands)

    # Count actual skills
    skills = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists() or (d / "skill.md").exists()] if SKILLS_DIR.exists() else []
    skill_count = len(skills)

    ok(f"Commands found: {command_count} in .claude/commands/")
    ok(f"Skills found: {skill_count} in .claude/skills/")

    # List them
    print(f"\n  Commands: {', '.join(sorted(f.stem for f in commands))}")
    print(f"  Skills: {', '.join(sorted(d.name for d in skills))}")

    # Check for lowercase skill.md (should be SKILL.md)
    for d in skills:
        if (d / "skill.md").exists() and not (d / "SKILL.md").exists():
            warn(f"{d.name}/skill.md should be SKILL.md (inconsistent naming)")
            issues += 1

    return issues


def check_pipeline_health():
    """Parse pipeline.md for stale deals and missing data."""
    header("Pipeline Health")
    issues = 0

    pipeline_file = context_dir() / "pipeline.md"
    if not pipeline_file.exists():
        action("pipeline.md not found!")
        return 1

    try:
        content = pipeline_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # See `check_reference_validation` above: `main`'s loop was unguarded
        # when this was written, so raising here ended the whole health run
        # with no verdict. `run_one` now catches it; this handler stays because
        # it names the file. pipeline.md is
        # operator-authored data in the private overlay, which is precisely
        # where a stray non-UTF-8 byte arrives from (a paste out of Word, a
        # Latin-1 company name).
        action(f"pipeline.md could not be read ({type(exc).__name__}); "
               f"pipeline health NOT checked")
        return 1

    # The comment here used to promise TBD, placeholder AND empty fields.
    # `placeholder_count` was assigned and never read, nothing anywhere looked
    # at empty cells, and the TBD number was a whole-file substring count
    # printed as "N TBD fields". Same class the sibling
    # `check_people_completeness` already carries a fix-comment for: say what
    # the method counted, and count the things the comment promises.
    tbd_count = content.lower().count("tbd")
    next_action_missing = content.lower().count("[next action]")

    # Table cells, so "placeholder" and "empty" mean something. A row is
    # `| a | b | c |`; the cells are what sits between the pipes.
    table_lines = [ln for ln in content.split("\n")
                   if ln.strip().startswith("|") and "---" not in ln]
    cells = [c.strip() for ln in table_lines for c in ln.strip().strip("|").split("|")]
    placeholder_cells = [c for c in cells if re.fullmatch(r"\[[^\]]*\]", c)]
    empty_cells = [c for c in cells if c == ""]

    if tbd_count > 0:
        warn(f"{tbd_count} 'TBD' occurrence(s) anywhere in pipeline.md "
             f"(a whole-file substring count, not a field count)")
        issues += 1
    if next_action_missing > 0:
        warn(f"{next_action_missing} missing next actions in pipeline.md")
        issues += 1
    if placeholder_cells:
        warn(f"{len(placeholder_cells)} table cell(s) hold only a "
             f"[placeholder] in pipeline.md")
        issues += 1
    if empty_cells:
        warn(f"{len(empty_cells)} empty table cell(s) in pipeline.md")
        issues += 1

    # Check file size (thin pipeline is a signal)
    size = pipeline_file.stat().st_size
    if size < 3000:
        warn(f"pipeline.md is thin ({size} bytes) - may need enrichment")
    else:
        ok(f"pipeline.md size: {size} bytes")

    # Count table rows (rough deal count)
    table_rows = [line for line in content.split("\n") if line.startswith("|") and "---" not in line and "Company" not in line and "Investor" not in line]
    deal_count = len([r for r in table_rows if r.strip() != "|"])
    ok(f"Approximately {deal_count} entries in pipeline tables")

    return issues


def check_people_completeness():
    """Parse people.md for incomplete entries."""
    header("People Completeness")
    issues = 0

    people_file = context_dir() / "people.md"
    if not people_file.exists():
        action("people.md not found!")
        return 1

    try:
        content = people_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Same class and same file as the two above. people.md holds names, so
        # it is the single likeliest context file to carry a byte that is not
        # valid UTF-8.
        action(f"people.md could not be read ({type(exc).__name__}); "
               f"people completeness NOT checked")
        return 1

    # Check for placeholder patterns
    add_patterns = re.findall(r"\[Add[^\]]*\]", content)
    if add_patterns:
        warn(f"{len(add_patterns)} placeholder fields ([Add ...]) in people.md")
        issues += 1

    # Say what this counts. It is a whole-file substring count with no column or
    # field context, so a "TBD" in a role, a company or a next-step column lands
    # here too -- calling every hit a missing EMAIL was a claim the method never
    # established.
    placeholders = content.lower().count("tbd") + content.lower().count("[email]")
    if placeholders > 0:
        warn(f"{placeholders} 'TBD' or '[email]' placeholder(s) anywhere in "
             f"people.md (not necessarily in an email column)")
        issues += 1

    size = people_file.stat().st_size
    ok(f"people.md size: {size} bytes")

    return issues


def check_outputs_inventory():
    """Count and categorize files in outputs/."""
    header("Outputs Inventory")
    issues = 0

    out = outputs_dir()
    if not out.exists():
        action("outputs/ directory not found!")  # leak-guard: ok (string in a message/log, not a path)
        return 1

    # `glob("*")` walked the TOP LEVEL only, while the labels said "Total".
    # On the operator convention of one subdirectory per deliverable that meant
    # 1 file / 0.0 MB reported over a tree holding thousands. Two numbers now,
    # each labelled with the scope its method actually covers.
    files = [f for f in out.rglob("*") if f.is_file()]
    loose = [f for f in out.glob("*") if f.is_file()]

    # Categorize by extension
    by_ext = {}
    total_size = 0
    for f in files:
        ext = f.suffix.lower() or "(no ext)"
        by_ext.setdefault(ext, []).append(f)
        try:
            total_size += f.stat().st_size
        except OSError:
            # A dangling symlink or a file removed mid-walk. Counting it and
            # skipping its bytes beats aborting the whole section.
            continue

    ok(f"Total files (recursive): {len(files)}")
    ok(f"Total size: {total_size / (1024*1024):.1f} MB")

    for ext, ext_files in sorted(by_ext.items()):
        ext_size = 0
        for f in ext_files:
            try:
                ext_size += f.stat().st_size
            except OSError:
                continue
        print(f"       {ext}: {len(ext_files)} files ({ext_size / 1024:.0f} KB)")

    # The nag is about loose files at the top level, which is what "organize
    # into subdirectories" asks you to fix. Firing it on the recursive count
    # would scold an already-organized tree forever.
    if len(loose) > 30:
        warn(f"outputs/ has {len(loose)} loose files at the top level - "  # leak-guard: ok (string in a message/log, not a path)
             f"consider organizing into subdirectories")
        issues += 1

    return issues


def check_datastore():
    """Check DataStore status."""
    header("DataStore Status")
    issues = 0

    store = datastore_dir()
    if not store.exists():
        action("datastore/ directory not found!")
        return 1

    index_file = store / "INDEX.md"
    if not index_file.exists():
        action("datastore/INDEX.md not found!")
        issues += 1
    else:
        ok("INDEX.md exists")

    # Check subdirectories
    expected_dirs = ["brand", "content", "corporate", "events", "intelligence", "investment", "operations", "products"]
    for d in expected_dirs:
        dir_path = store / d
        if dir_path.exists():
            # `glob("*")` counted DIRECTORIES as files, so `brand/` reported
            # "5 file(s)" for five subfolders holding 192 documents -- a number
            # that was neither the file count at that level (0) nor the document
            # count in the subtree. A subdir holding only empty folders also
            # escaped the "awaiting documents" warning.
            file_count = sum(1 for p in dir_path.rglob("*") if p.is_file())
            if file_count > 0:
                ok(f"{d}/: {file_count} document(s)")
            else:
                warn(f"{d}/: empty - awaiting documents")
        else:
            action(f"{d}/ directory missing")
            issues += 1

    # Count total documents
    total_docs = sum(1 for _ in store.rglob("*") if _.is_file() and _.name != "INDEX.md")
    if total_docs == 0:
        warn("DataStore has no documents yet - add source-of-truth files")
        issues += 1
    else:
        ok(f"Total documents in DataStore: {total_docs}")

    return issues


def _docs_path(name: str) -> Path:
    """Resolve a synced doc's distribution copy.

    docs/ is split by routing: most files default to ENGINE (docs/ under the
    workspace root), but CEO-ADMIN-GUIDE.* is `private` and lives under the data
    overlay (.heading-os-data/docs). Prefer whichever root actually holds the file;
    fall back to engine when neither exists, so a genuinely missing doc still flags.
    """
    engine = WORKSPACE / "docs" / name
    data = get_data_root() / "docs" / name
    if engine.exists():
        return engine
    if data.exists():
        return data
    return engine


def check_docs_sync() -> int:
    """Verify templates/ and docs/ are in sync for the 6 shared documentation files.

    The sync-docs.py PostToolUse hook auto-copies templates/ to docs/. Drift means
    either the hook failed or someone edited docs/ directly. Either way, investigate.

    templates/ is `private` (data overlay) and docs/ is split (engine default,
    CEO-ADMIN-GUIDE on data) — both are resolved through the data-seam helpers,
    not the engine root, since the two-part topology moved them off the engine tree.
    """
    header("Docs/Templates Consistency")
    issues = 0
    synced_files = [
        "GETTING-STARTED.md", "GETTING-STARTED.html",
        "CEO-ADMIN-GUIDE.md", "CEO-ADMIN-GUIDE.html",
        "EMERGENCY-PROCEDURES.md", "EMERGENCY-PROCEDURES.html",
    ]
    templates_dir = get_templates_dir()
    if not templates_dir.is_dir():
        # Same state its sibling `check_doc_versions` already treats as
        # legitimate 60 lines below. Without this, a bare public engine clone
        # emitted six ACTIONs (one per file "missing from templates/"),
        # `workspace-health.py` exited 1, and the check that stands in front of
        # `/push-updates` failed on every clone without the private overlay. A
        # missing overlay is not a missing file.
        warn("templates/ is not present (no data overlay); 0 file pairs compared")
        return 0
    for name in synced_files:
        tpl = templates_dir / name
        doc = _docs_path(name)
        if not tpl.exists():
            action(f"{name}: missing from templates/")
            issues += 1
            continue
        if not doc.exists():
            action(f"{name}: missing from docs/ (sync-docs.py failed or never fired)")
            issues += 1
            continue
        try:
            if tpl.read_bytes() != doc.read_bytes():
                action(f"{name}: templates/ and docs/ out of sync (re-save templates/ to trigger sync)")
                issues += 1
            else:
                ok(f"{name}: synced")
        except OSError as e:
            # An unreadable copy is not a synced copy. This was a WARN that did
            # not count, so a run where every comparison failed to read still
            # returned 0 and the summary said "All checks passed." -- the guard
            # reporting clean exactly when it could verify nothing.
            action(f"{name}: read failed ({e}); sync NOT verified")
            issues += 1
    return issues


def check_skill_router_coverage() -> int:
    """Cross-reference .claude/rules/skill-router.md against .claude/skills/ directory listing.

    Every skill directory should either:
    - Appear in the router registry (table rows matching `/skill-name`), or
    - Be explicitly listed as NEVER auto-trigger (e.g., /prime, /osint-advanced)
    - Be a plugin-namespaced skill (documented in the plugin doctrine section)

    A skill in .claude/skills/ without any mention in the router is silently orphaned.

    The match is boundary-anchored. A bare `f"/{name}" in router_text` reported
    `osint` as covered on the strength of the `/osint-advanced` row alone, and
    `queue` on `/queue-draft`; both pairs are live in this repo, so for those
    names the check could not detect the very class it advertises.
    """
    header("Skill Router Coverage")
    issues = 0
    router_file = WORKSPACE / ".claude" / "rules" / "skill-router.md"
    skills_dir = WORKSPACE / ".claude" / "skills"
    if not router_file.exists():
        action("skill-router.md missing")
        return 1
    if not skills_dir.exists():
        action(".claude/skills/ missing")
        return 1
    try:
        router_text = router_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # An unreadable router is the same outcome as a missing one for this
        # check: no skill can be shown to be covered. The branch above already
        # returns 1 for the missing case.
        action(f"skill-router.md could not be read ({type(exc).__name__}); "
               f"0 skills checked for coverage")
        return 1
    skill_dirs = [d.name for d in sorted(skills_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    for name in skill_dirs:
        # A skill name may not run straight into another name character, so
        # `/osint` no longer matches inside `/osint-advanced`.
        if re.search(rf"/{re.escape(name)}(?![\w-])", router_text):
            ok(f"{name}: mentioned in skill-router.md")
        else:
            action(f"{name}: not mentioned in skill-router.md")
            issues += 1
    return issues


def check_doc_versions(max_age_days: int = 90) -> int:
    """Verify four shared templates carry `version:` + `last-updated:` markers.

    Scope, stated because the old docstring overstated it: this opens exactly
    the four `.md`/`.template` files listed in `tracked` below. It opens nothing
    under `docs/` and no `.html` file. `check_docs_sync` byte-compares the six
    synced files, so a drifted marker in a docs/ copy is caught there instead.

    A missing file (with templates/ present) counts as an issue. A stale date
    does not: staleness is a refresh signal, and blocking a push on it was never
    the contract in `.claude/rules/documentation.md`. The stale count is printed
    so it is not silent either.
    """
    header(f"Shared Doc Version Markers (freshness threshold: {max_age_days} days)")
    issues = 0
    stale = 0
    version_pattern = re.compile(r"<!--\s*version:\s*(\S+?)\s*\|\s*last-updated:\s*(\d{4}-\d{2}-\d{2})\s*-->")
    today = datetime.now(get_default_tz()).date()
    templates_dir = get_templates_dir()
    if not templates_dir.is_dir():
        # A bare public engine clone has no data overlay, so there is no
        # templates/ tree to version-check. That is a legitimate state, unlike
        # a file missing from a templates/ that DOES exist.
        warn("templates/ is not present (no data overlay); 0 docs version-checked")
        return 0
    tracked = [
        templates_dir / "GETTING-STARTED.md",
        templates_dir / "CEO-ADMIN-GUIDE.md",
        templates_dir / "EMERGENCY-PROCEDURES.md",
        templates_dir / "CLAUDE.md.template",
    ]
    for f in tracked:
        label = f"templates/{f.name}"  # f is under the data root now; relative_to(WORKSPACE) would raise
        if not f.exists():
            # templates/ exists but this member of the sync set does not: the
            # set is incomplete, which is a defect, not a note. It used to WARN
            # and `continue`, so an entirely missing template set still returned
            # 0 and the run printed "All checks passed."
            action(f"{label}: missing from templates/")
            issues += 1
            continue
        try:
            first_lines = f.read_text(encoding="utf-8").splitlines()[:3]
        except (OSError, UnicodeDecodeError) as exc:
            # The last of the five reads in this file that had no decode
            # guard. A template
            # whose version marker cannot be read is an unverified template,
            # which is what the two `continue` branches below already count.
            action(f"{label}: could not be read ({type(exc).__name__}); "
                   f"version marker NOT checked")
            issues += 1
            continue
        first_block = "\n".join(first_lines)
        match = version_pattern.search(first_block)
        if not match:
            # "in the first 3 lines", because that is the window searched two
            # lines up. The message used to say "on line 1", a stricter contract
            # than the one enforced: a compliant marker on line 2 passes, so an
            # operator following the remediation moved a marker that was already
            # fine.
            action(f"{label}: missing version marker in the first 3 lines")
            issues += 1
            continue
        version, date_str = match.groups()
        try:
            doc_date = date.fromisoformat(date_str)
        except ValueError:
            warn(f"{label}: malformed date {date_str}")
            issues += 1
            continue
        age = (today - doc_date).days
        if age > max_age_days:
            stale += 1
            warn(f"{label}: v{version}, last-updated {date_str} ({age} days old - consider refresh)")
        else:
            ok(f"{label}: v{version}, last-updated {date_str} ({age} days)")
    if stale:
        warn(f"{stale} of {len(tracked)} shared doc(s) past the {max_age_days}-day "
             f"threshold (reported, not counted as an issue)")
    return issues


def check_build_sync() -> int:
    """Compare local corporate repo BUILD.json against the last publish state.

    Passes if the corporate repo is reachable and its BUILD.json parses. The
    per-exec activity check is already in `scripts/admin-health.py`, which since
    2026-08-23 reports each exec's last COMMIT rather than a sync handshake -
    this check focuses on: does our local corporate repo look sane.
    """
    header("Corporate BUILD.json")
    issues = 0
    corporate_root = WORKSPACE.parent / "heading-os-corporate"
    build_json = corporate_root / "BUILD.json"
    if not build_json.exists():
        # Two different states, and the old message conflated them: it blamed a
        # missing clone whether or not the clone was there. As of 2026-08-23 the
        # live repo IS cloned and has never carried a BUILD.json, because
        # `--bump-build` is opt-in and no publish has passed it.
        if not corporate_root.exists():
            warn(f"{corporate_root.name}: not cloned locally; no build number to read")
        else:
            warn(f"{build_json.relative_to(WORKSPACE.parent)}: not found - corporate has "
                 f"never been published with --bump-build, so no build number exists yet")
        return 0  # Not a workspace-health failure - just info
    import json
    try:
        data = json.loads(build_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # `ValueError`, not `json.JSONDecodeError`. A BUILD.json with invalid
        # UTF-8 raises UnicodeDecodeError out of `read_text`, which is a
        # ValueError and not an OSError, so it escaped this handler.
        action(f"BUILD.json parse failed: {e}")
        return 1
    if not isinstance(data, dict):
        # Valid JSON of the wrong shape. A list, string, number or null parsed
        # cleanly, so the handler above never fired and `.get` raised
        # AttributeError on the next line. `main` ran these checks in an
        # unguarded loop when this was written, so the WHOLE health run died
        # there: the remaining sections never ran, no summary printed, and the
        # operator got a traceback instead of a verdict in front of
        # `/push-updates`. `run_one` now catches that class.
        action(f"BUILD.json is a {type(data).__name__}, expected an object")
        return 1
    build_no = data.get("build", "?")
    # `timestamp` is the key `publish-corporate.bump_build` writes. This read
    # `last_updated`, which nothing has ever written, so the line printed "?"
    # regardless of the file's contents.
    written_at = data.get("timestamp", "?")
    ok(f"Corporate BUILD #{build_no}, published: {written_at}")
    return issues


def check_daemon_token_perms() -> int:
    """F-9.2: verify the bridge-daemon bearer token file is 0600.

    The daemon writes .daemon-state/token with 0o600 (auth.mint), but a backup
    or restore can lose permission bits. Absence is not a failure - the token
    exists only after the daemon has run at least once - so a missing file is a
    skip-with-notice, not an ACTION. This check catches external drift, not
    non-start.
    """
    import stat as _stat
    header("Daemon token permissions")
    token_file = WORKSPACE / ".daemon-state" / "token"
    state_dir = token_file.parent
    issues = 0

    # The world-writable test used to sit AFTER the early return below, so a
    # world-writable .daemon-state/ went unflagged whenever the token happened
    # to be absent -- which is the state this workspace is in, the bridge daemon
    # being deliberately stopped.
    if state_dir.is_dir():
        parent_mode = _stat.S_IMODE(state_dir.stat().st_mode)
        if parent_mode & 0o002:
            action(f".daemon-state/ is world-writable (mode {oct(parent_mode)})")
            issues += 1

    if not token_file.exists():
        # Absence establishes that the file is not there, and nothing more. The
        # old line attributed a cause -- "(daemon never started)" -- that no
        # input to this function could support; deleted, moved, or restored
        # without it all look identical. It also used the OK marker for a check
        # that did not run.
        warn("daemon token file absent; permissions NOT checked "
             "(never started, or removed - this check cannot tell which)")
        return issues

    mode = _stat.S_IMODE(token_file.stat().st_mode)
    if mode != 0o600:
        action(f".daemon-state/token has mode {oct(mode)}, expected 0o600 (run: chmod 600 {token_file})")
        issues += 1
    else:
        ok(".daemon-state/token is 0600")
    return issues


# F-7.1: one representative installed DISTRIBUTION per optional-dependency extra.
# Presence of the distribution means the extra is installed ("Armed"); absence
# means the capability is Dormant on this install. Distribution presence
# (importlib.metadata) is used deliberately instead of importability: a workspace
# script like scripts/firecrawl.py sits on sys.path[0] when this file is run as
# `python scripts/workspace-health.py` and would shadow the real `firecrawl`
# module, giving a false reading. Distribution names are immune to that shadow.
_EXTRAS_DISTS = {
    "email": "exchangelib",
    "telegram": "Telethon",
    "browser": "playwright",
    "documents": "python-pptx",
    "media": "yt-dlp",
    "dashboard": "fastapi",
    "ai-extra": "openai",
    "observability": "langfuse",
    "research": "firecrawl-py",
}


def check_extras_importability() -> int:
    """F-7.1: report which optional-dependency extras are Armed vs Dormant.

    Informational only - always returns 0. Dormant is a legitimate state (an
    adopter runs `uv sync` core-only and arms a capability with
    `uv sync --extra <name>`), so a Dormant extra is never an ACTION.
    """
    from importlib import metadata as _md
    header("Capability extras (Armed / Dormant)")
    armed = 0
    for extra, dist in _EXTRAS_DISTS.items():
        try:
            _md.version(dist)
            present = True
        except _md.PackageNotFoundError:
            present = False
        if present:
            ok(f"{extra}: Armed ({dist} installed)")
            armed += 1
        else:
            print(f"  {CYAN}--{RESET}    {extra}: Dormant (uv sync --extra {extra})")
    print(f"\n  {armed}/{len(_EXTRAS_DISTS)} extras Armed on this install.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="31C Workspace Health Check")
    parser.add_argument(
        "--section",
        choices=[
            "refs", "context", "counts", "pipeline", "people", "outputs",
            "datastore", "docs-sync", "skill-router", "doc-versions", "build",
            "daemon-token", "extras",
        ],
        help="Run only a specific check section",
    )
    parser.add_argument("--max-days", type=int, default=30,
                        help="Maximum age in days for context freshness (default: 30)")
    args = parser.parse_args()

    print(f"\n{BOLD}31C Workspace Health Check{RESET}")
    print(f"Workspace: {WORKSPACE}")
    print(f"Date: {datetime.now(get_default_tz()).strftime('%Y-%m-%d %H:%M')}")

    total_issues = 0
    INCONCLUSIVE.clear()
    checks = {
        "refs": check_reference_validation,
        "context": lambda: check_context_freshness(args.max_days),
        "counts": check_agent_counts,
        "pipeline": check_pipeline_health,
        "people": check_people_completeness,
        "outputs": check_outputs_inventory,
        "datastore": check_datastore,
        "docs-sync": check_docs_sync,
        "skill-router": check_skill_router_coverage,
        "doc-versions": check_doc_versions,
        "build": check_build_sync,
        "daemon-token": check_daemon_token_perms,
        "extras": check_extras_importability,
    }

    def run_one(name, check_fn) -> int:
        """One section, whose crash costs that section and no other.

        The loop below used to call each check bare. Every section that raises
        therefore ended the WHOLE run: the remaining sections never executed, no
        summary printed, and the operator got a traceback instead of a verdict,
        in front of `/push-updates`. `check_build_sync`'s own comment has named
        this amplifier since 2026-08-23 and fixed only its own read.

        MEASURED 2026-09-01: five reads under this module still had no handler
        at all (`check_pipeline_health`, `check_people_completeness`,
        `check_skill_router_coverage`, `check_reference_validation`,
        `check_doc_versions`), and a `context/pipeline.md` holding one invalid
        byte took the entire health run down through this loop. Guarding each
        read in turn fixes the five that exist today; guarding the CALL fixes
        the class, including the next check somebody adds.

        Counted as an issue, never swallowed. A section that could not run has
        not passed, and a health check that exits 0 over a section it never
        completed is the failure this whole file is written against.

        The five per-read handlers STAY, and that is not redundancy left by
        accident. This one can only say which section died; each of theirs
        names the FILE that could not be read and lets the rest of that
        section still produce its verdict. Both layers landed on 2026-09-01
        from two different auditors, and the five comments beside those reads
        were reconciled the same day: each said "`main` runs every section in
        an unguarded loop" in the present tense, which this function had just
        made false. A comment that describes a fixed defect as live misleads
        the next audit, so they now say when it was true.
        """
        try:
            return check_fn()
        except Exception as exc:  # noqa: BLE001 - one section must not end the run
            action(f"section '{name}' could not run ({type(exc).__name__}: {exc}); "
                   f"it verified nothing")
            return 1

    if args.section:
        ran = [args.section]
        total_issues = run_one(args.section, checks[args.section])
    else:
        ran = list(checks)
        for name, check_fn in checks.items():
            total_issues += run_one(name, check_fn)

    # Summary. "All checks passed." was printed after `--section extras` too --
    # one section, and one whose own docstring says it is informational and
    # always returns 0. The summary now names the coverage it had.
    header("Summary")
    skipped = len(checks) - len(ran)
    # A section that verified nothing is named before any pass line is printed.
    # "0 paths checked (this section verified nothing)" followed by "Section
    # 'refs' passed." told the operator two opposite things four lines apart,
    # and only the second one survives being skim-read.
    for note in INCONCLUSIVE:
        print(f"  {YELLOW}INCONCLUSIVE{RESET}  {note}")
    if total_issues == 0 and INCONCLUSIVE:
        ran_word = f"Section '{ran[0]}'" if skipped else f"All {len(ran)} checks"
        print(f"  {YELLOW}{BOLD}{ran_word} found no issues, but "
              f"{len(INCONCLUSIVE)} section(s) above verified nothing.{RESET}")
        if skipped:
            print(f"  {YELLOW}The other {skipped} section(s) did not run.{RESET}")
    elif total_issues == 0 and skipped:
        print(f"  {GREEN}{BOLD}Section '{ran[0]}' passed.{RESET} "
              f"{YELLOW}The other {skipped} section(s) did not run.{RESET}")
    elif total_issues == 0:
        print(f"  {GREEN}{BOLD}All {len(ran)} checks passed.{RESET}")
    elif skipped:
        print(f"  {YELLOW}{BOLD}{total_issues} issue(s) found in section "
              f"'{ran[0]}'; the other {skipped} section(s) did not run.{RESET}")
    else:
        print(f"  {YELLOW}{BOLD}{total_issues} issue(s) found.{RESET}")

    sys.exit(0 if total_issues == 0 else 1)


if __name__ == "__main__":
    main()
