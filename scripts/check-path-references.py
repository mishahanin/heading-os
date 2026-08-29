#!/usr/bin/env python3
"""Advisory audit: engine prose naming repo paths that do not exist.

Documentation rot is silent. A rule, skill or doc page names `scripts/foo.py`,
the script is later renamed or deleted, and the prose keeps pointing at nothing.
Nobody notices until someone pastes the command and it fails. On 2026-08-21 a
sweep found eight such sites that had accumulated over months, including a
`/odin` source-ingest command that could not run and a `docs/SECURITY-MODEL.md`
paragraph describing two hook files deleted in ba1affd.

Scope, precisely (`.claude/rules/scope-claims.md`): this checks paths that the
routing map classifies as **engine**, named in **git-tracked Markdown**, against
the **engine working tree**. It says nothing about the private data overlay -- a
path routing `private` or `corporate` is skipped, because the overlay is absent
on a public clone and its absence is not evidence. It also says nothing about
paths named in Python, YAML or JSON; those have their own callers and tests.

Extraction is a regex over prose, so it is heuristic in BOTH directions: it can
MISS a path (an unusual spelling), and it can capture a fragment that was never
a path (`action_queue.append(` truncates to `action_queue.appen`). Both classes
live in BASELINE with a stated reason rather than being silently filtered, so
the list of what this tool ignores is readable.

That sentence read "in one direction only" until 2026-08-25 and then listed two
directions in the same breath. BASELINE carries four over-match entries, so the
over-reporting half is not hypothetical, and a reader who took the claim at its
word would conclude this scanner cannot over-report.

One class is filtered instead of listed: a path `.gitignore` covers. Runtime
state such as `.claude/scheduled_tasks.json` is present on the operator's
machine and absent from every clone, so BASELINE cannot express it -- the entry
reads stale locally and the path reads dangling in CI. Prose naming a gitignored
path is correct by construction, so `scan()` drops it.

BASELINE is a frozen ratchet, the same shape as `audit-skill-bash-paths.py`: a
dangling path already listed is tolerated, a NEW one fails `--check`. Removing
an entry (cleaning the prose) is welcome -- delete the line in the same change.

Usage:
  python scripts/check-path-references.py           # list every dangling reference
  python scripts/check-path-references.py --check   # exit 1 on any NEW one
  python scripts/check-path-references.py --json    # machine-readable

Tests: tests/test_a_heading_match_that_was_never_anchored.py
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root, get_routing_destination  # noqa: E402
from scripts.utils.paths import get_data_root, data_root_is_demo  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET  # noqa: E402
from scripts.utils.repo_files import ignored_paths_or_none  # noqa: E402

# Repo-relative paths, anchored on the top-level directories the engine owns.
# The trailing class refuses a bare trailing dot so `foo.py.` yields `foo.py`.
#
# The extension length was {2,5} until 2026-08-23, which SILENTLY TRUNCATED any
# longer one: prose naming `reference/sentinel.service` was scanned as
# `reference/sentinel.servi`, a path that of course does not exist, so a real
# file was reported as rot. Under-matching here is the worse direction, because
# it manufactures a finding against correct prose. Widened to 9, which covers
# `.service` and `.template`.
_PATH = re.compile(
    r"(?<![\w/.-])"
    r"((?:scripts|config|docs|reference|tests|examples|templates|\.claude|\.github)"
    r"/[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.[a-z]{2,9})"
)

# History by design: a changelog names what a release removed. Never rot.
_SKIP_FILES = {"CHANGELOG.md"}

# Frozen 2026-08-21, after the eight real rot sites were fixed. Every entry is a
# path that does not exist and SHOULD NOT: a placeholder, a regex fragment, or
# correct prose about something deleted or not yet built. Reason is mandatory --
# an entry without one is indistinguishable from rot somebody gave up on.
BASELINE = {
    # Regex fragments: a sentence continued past what looked like a path.
    # These two were spelled `.appen` / `.assig` until 2026-08-23, when the
    # extension cap above went from 5 to 9 and the scanner started seeing the
    # whole word.
    "scripts/bridge_daemon/sources/action_queue.append": "fragment of `.append(`",
    "scripts/scrutinize-dispatch.assign": "fragment of `.assign`",
    "scripts/scrutinize-dispatch.swap": "prose, not a filename",
    "scripts/utils/tool_risk.tier": "fragment of `tool_risk.tier_for()`",
    ".claude/settings.local": "fragment of `.claude/settings.local.json`",
    "scripts/install-...-timer.sh": "ellipsis naming a family of installers",
    # Deliberate placeholders in templates and naming-convention examples.
    "scripts/models/product.py": "example file in the plan template",
    "scripts/models/user.py": "example file in the plan template",
    "tests/test_integration.py": "example file in the plan template",
    "scripts/rule-regression-runner.py": "named as to-be-built when the first rule artefact lands",
    "scripts/name.py": "stands for `scripts/<name>.py` in the naming rule",
    "scripts/dashboard.py": "hypothetical finding in the scrutinize eval template",
    "reference/playbook-gcc.md": "example of the per-region playbook naming",
    "scripts/linkedin_archive.py": "quoted error message demonstrating a snake_case failure",
    # Correct prose about things deleted, retired, or not yet built.
    "scripts/export-sync.py": "named as archived",
    "scripts/workspace-sync.py": "named as now-deleted",
    ".claude/rules/secure-projects.md": "named as gone, superseded by SENSITIVE_MODE",
    ".claude/rules/vpn-preflight.md": "named as the path this file moved from",
    "config/classification.json": "named as replaced by routing-map.yaml",
    "scripts/telegram_client.py": "named as the WRONG path, to warn against it",
}


def gitignored(root: Path, paths: list[str]) -> set[str]:
    """Of `paths`, those `.gitignore` covers -- absent from a clone by design.

    Outside a git repo (a synthetic root in a test) git cannot answer, so
    nothing is filtered: the tool over-reports rather than going quiet, per
    `.claude/rules/scope-claims.md`. That choice is made HERE, in one visible
    line, which is why this calls the `_or_none` form.

    The `git check-ignore` invocation itself used to be spelled out here, a
    second copy of the one in `scripts/utils/repo_files.py`. The two had drifted
    to opposite contracts -- this one degraded silently, the other raised -- and
    a bug fixed in one would not have reached the other. The contracts still
    differ, deliberately; the CALL no longer does.
    """
    return ignored_paths_or_none(paths, root) or set()


def tracked_markdown(root: Path) -> list[str]:
    """Tracked `*.md`, or an empty list outside a git repo.

    `check=True` made `git ls-files` failing OUTSIDE a repo raise
    CalledProcessError, and `scan()` and `named_paths()` both call this first
    -- so the process died on a traceback before `gitignored`'s documented
    fail-soft path could ever run. The docstring above promised graceful
    degradation the tool did not have.

    `-z` for the same reason as above: `.split()` turned a tracked
    `my notes.md` into two names that both fail to open, so that file's prose
    was never scanned and no dangling path inside it could ever be caught.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"], cwd=root, capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"warning: `git ls-files` failed in {root} ({out.stderr.strip()[:200]}); "
              f"no Markdown was scanned", file=sys.stderr)
        return []
    names = [f for f in out.stdout.split("\0") if f]
    return [f for f in names if f not in _SKIP_FILES]


def scan(root: Path) -> dict[str, list[tuple[str, int]]]:
    """Return {dangling engine-routed path: [(file, lineno), ...]}."""
    hits: dict[str, list[tuple[str, int]]] = {}
    for rel in tracked_markdown(root):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"{YELLOW}skipped {rel}: {exc}{RESET}", file=sys.stderr)
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for path in _PATH.findall(line):
                if (root / path).exists():
                    continue
                if get_routing_destination(path) != "engine":
                    continue  # lives in an overlay this tool cannot see
                hits.setdefault(path, []).append((rel, lineno))
    for path in gitignored(root, sorted(hits)):
        hits.pop(path, None)
    return hits


# ============================================================
# Coverage: which engine code no prose describes
# ============================================================
#
# The design spec's Phase 3 asked for a persisted `(prose_file, line, code_path)`
# edge table in the DATA store. Measured 2026-08-21 before building it, the table
# would hold 28,067 rows and answer nothing a cheaper thing does not:
#
#   - "who mentions scripts/foo.py?"  -- `grep -rn` over both roots: 0.33 s. A
#     table saves a third of a second and costs a schema.
#   - "what is undocumented?"         -- 19 files out of 364. A nineteen-line
#     answer does not need a store; it needs the extraction that already exists
#     five lines above this comment.
#   - 16,530 of the 28,067 edges (59%) come from `outputs/` and `plans/` --
#     handoff summaries and finished plans that MENTION a path in passing. A
#     table that is more than half archive noise makes the signal harder to find.
#
# So Phase 3 ships as a report over the extraction, not as a table. Operator
# approved the reduction on the condition it was proved, 2026-08-21.

# Prose that MENTIONS a path is not prose that DOCUMENTS it. A handoff summary
# quoting a filename does not make that file documented, so these trees are read
# for reporting and excluded from the "is it documented" verdict.
_ARCHIVE_PREFIXES = (
    "outputs/",            # leak-guard: ok (prefix match on a repo-relative string)
    "plans/archive/",      # leak-guard: ok (prefix match on a repo-relative string)
    "chronicle/",          # leak-guard: ok (prefix match on a repo-relative string)
    "docs/superpowers/",
    "threads/",            # leak-guard: ok (prefix match on a repo-relative string)
)

# What "documented" is claimed ABOUT. Engine Python only: these are the files the
# documentation-propagation rule governs, and the only tree present in every
# clone. Nothing here says anything about .claude/ content or the overlay's code.
_CODE_GLOB = "scripts/**/*.py"


def code_files(root: Path) -> tuple[list[str], int]:
    """(documentable engine Python paths, count of package markers dropped).

    `__init__.py` is a package marker, not a unit anybody documents by name, so
    counting it as undocumented would put permanent noise in the report. The drop
    is RETURNED rather than swallowed, because a narrowed check that prints like a
    complete one is the defect `.claude/rules/scope-claims.md` exists to stop.
    """
    # `.as_posix()`, not `str()`. The keys these are compared against come from
    # the prose regex, whose character class admits `/` and not `\`, so on a
    # Windows checkout every native-separator path failed the `f not in named`
    # test and the report claimed 100% of engine code was undocumented. The
    # sibling `checkpoint-paths.py` already uses `.as_posix()` for repo-relative
    # strings for this reason.
    every = [p.relative_to(root).as_posix() for p in root.glob(_CODE_GLOB)
             if "__pycache__" not in p.parts]
    keep = sorted(f for f in every if not f.endswith("__init__.py"))
    return keep, len(every) - len(keep)


def named_paths(root: Path, *, skip_archives: bool) -> dict[str, list[tuple[str, int]]]:
    """Every repo path this root's tracked Markdown names -> where it is named."""
    found: dict[str, list[tuple[str, int]]] = {}
    for rel in tracked_markdown(root):
        if skip_archives and rel.startswith(_ARCHIVE_PREFIXES):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for path in _PATH.findall(line):
                found.setdefault(path, []).append((rel, lineno))
    return found


def coverage(root: Path, data_root: Path | None) -> dict:
    """Which engine code files no non-archive prose names.

    `data_root` is the private overlay when it is on disk and None otherwise. Its
    absence NARROWS the claim rather than failing: a public clone has no overlay,
    so a file documented only there would read as undocumented. The return value
    carries `overlay_scanned` so the caller can say which claim it is making
    (`.claude/rules/scope-claims.md`).
    """
    named = named_paths(root, skip_archives=True)
    if data_root is not None:
        for path, sites in named_paths(data_root, skip_archives=True).items():
            named.setdefault(path, []).extend(sites)
    files, package_markers = code_files(root)
    undocumented = [f for f in files if f not in named]
    return {
        "code_files": len(files),
        "package_markers_skipped": package_markers,
        "undocumented": undocumented,
        "overlay_scanned": data_root is not None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 on a dangling path not in BASELINE")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--coverage", action="store_true",
                    help="report engine code that no non-archive prose names (advisory)")
    args = ap.parse_args()
    if args.coverage and args.check:
        # Refused, not ignored. The coverage branch returns before `scan()` ever
        # runs, so `--coverage --check` exited 0 while no dangling-path check
        # had happened - a green result standing in for a check nobody made.
        # The two flags answer different questions and neither subsumes the
        # other, so the caller picks one.
        ap.error("--coverage and --check answer different questions; "
                 "run them separately")

    root = get_workspace_root()

    if args.coverage:
        overlay = get_data_root()
        if data_root_is_demo() or not (overlay / ".git").exists():
            overlay = None
        cov = coverage(root, overlay)
        if args.json:
            print(json.dumps(cov, indent=2))
            return 0
        scanned = "engine + private overlay" if cov["overlay_scanned"] else "engine ONLY"
        print(f"{BOLD}{CYAN}Documentation coverage of engine code (advisory){RESET}")
        print(f"{GRAY}Prose sources: {scanned}. Archive trees excluded "
              f"({', '.join(_ARCHIVE_PREFIXES)}) -- a handoff that mentions a path")
        print(f"does not document it. Claim is about {_CODE_GLOB} and nothing else.{RESET}")
        if not cov["overlay_scanned"]:
            print(f"{YELLOW}The overlay is absent, so a file documented only there "
                  f"reads as undocumented here.{RESET}")
        n = len(cov["undocumented"])
        print(f"\n{BOLD}{n}{RESET} of {cov['code_files']} named in no prose "
              f"{GRAY}({cov['package_markers_skipped']} __init__.py package markers "
              f"not counted){RESET}:")
        for f in cov["undocumented"]:
            print(f"  {GRAY}{f}{RESET}")
        return 0
    found = scan(root)
    new = {p: sites for p, sites in found.items() if p not in BASELINE}

    if args.json:
        print(json.dumps(
            {"dangling": dict(sorted(found.items())),
             "new": sorted(new),
             "baseline": BASELINE},
            indent=2,
        ))
    else:
        print(f"{BOLD}{CYAN}Engine prose path references (advisory){RESET}")
        print(f"{GRAY}Engine-routed paths in tracked Markdown, checked against the engine tree.")
        print(f"Overlay-routed paths are not checked -- the overlay may be absent.{RESET}\n")
        for path, sites in sorted(found.items()):
            reason = BASELINE.get(path)
            tag = f"{GREEN}baseline: {reason}{RESET}" if reason else f"{RED}NEW{RESET}"
            print(f"{BOLD}{path}{RESET} [{len(sites)}] {tag}")
            for rel, lineno in sites[:5]:
                print(f"  {GRAY}{rel}:{lineno}{RESET}")
        stale = sorted(p for p in BASELINE if p not in found)
        if stale:
            print(f"\n{YELLOW}Baseline entries no longer found (drop them):{RESET}")
            for p in stale:
                print(f"  {p}")

    if args.check:
        if new:
            print(f"\n{RED}{BOLD}FAIL{RESET} -- prose names {len(new)} path(s) that do not exist:",
                  file=sys.stderr)
            for path, sites in sorted(new.items()):
                where = ", ".join(f"{r}:{n}" for r, n in sites[:3])
                print(f"  {RED}{path}{RESET}  ({where})", file=sys.stderr)
            print(f"{YELLOW}Fix the path, or add it to BASELINE with the reason it should not exist.{RESET}",
                  file=sys.stderr)
            return 1
        print(f"\n{GREEN}OK{RESET} -- no new dangling path references.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
