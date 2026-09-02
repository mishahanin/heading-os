#!/usr/bin/env python3
"""Classification health check - audit workspace file classification.

Walks the workspace and resolves each file's classification (corporate vs ceo-only)
via the shared resolver in scripts.utils.workspace, which (HEADING OS step 7) now
collapses config/routing-map.yaml — the single classification input — into the two
values. Reports summary stats, flags unclassified files, and detects outputs/
subdirectory drift (new subdirs that accumulated content without an explicit entry).

Usage:
    python scripts/classification-health.py                # terminal report
    python scripts/classification-health.py --json         # JSON output
    python scripts/classification-health.py --unclassified # only show unclassified
    python scripts/classification-health.py --corporate-only # list corporate files
    python scripts/classification-health.py --outputs-drift  # flag outputs/ subdirs >5 files
                                                              # without explicit config entries

Tests: tests/test_a_topic_list_shredded_into_single_letters.py
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_workspace_root, get_classification, get_outputs_dir, load_routing_map,
    matched_routing_rule,
)
from scripts.utils.colors import GRAY, GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.repo_files import not_ignored

# Directories to skip entirely.
#
# This set is a SPEED filter now, not the correctness boundary. git decides what
# is ignored, below; these names are pruned before git is asked because walking
# them is slow and pointless. Removing a name from here changes no verdict, only
# the time taken. Adding one CAN hide a tracked file, so add nothing that git
# tracks.
SKIP_DIRS = {
    ".git", ".sync", ".sentinel", ".sessions", "__pycache__",
    "node_modules", ".corporate-repo", ".crm-central-repo",
    "chrome-profile",
}

# File patterns to skip
SKIP_FILES = {".DS_Store", "Thumbs.db", ".gitignore", ".gitattributes"}


def walk_workspace(root: Path) -> list[str]:
    """Every file in the workspace that git does not ignore, relative to root.

    The hand-written skip list above cannot know what `.gitignore` says, and the
    gap was not theoretical. MEASURED 2026-08-29 on this repository: this sweep
    returned 2363 files and git ignores 427 of them, eighteen percent. Among
    them `.claude/settings.local.json`, a stale `.bak-...~` file, the marp
    web-font binaries and a scratch `.marp-src-*.md`. The operator reads this
    report to judge whether the engine/data split is holding, and every one of
    those rows is a file no split decision applies to.

    The `.claude` carve-out below is what made it worst. Hidden directories are
    skipped except `.claude`, and `.claude/worktrees/` is where agent worktrees
    are checked out. A worktree is a full second copy of the repository, so
    while one exists this sweep would count the whole tree twice and classify
    every file in the copy.

    `git check-ignore` is the only thing that knows the answer, and
    `scripts/utils/repo_files.py` RAISES when git cannot answer rather than
    degrading to "nothing is ignored" -- that degradation is the silent failure
    this call exists to prevent.
    """
    walked = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(root)
        parts = rel.parts
        # Prune the expensive subtrees before asking git (speed only).
        if any(part in SKIP_DIRS for part in parts):
            continue
        # Skip hidden directories (except .claude)
        if any(part.startswith(".") and part != ".claude" for part in parts[:-1]):
            continue
        # Skip specific files
        if rel.name in SKIP_FILES:
            continue
        walked.append(item)

    kept = not_ignored(walked, root)
    return [str(p.relative_to(root)).replace("\\", "/") for p in kept]


def classify_files(root: Path) -> dict:
    """Classify all workspace files and return results.

    Three lists, and the third OVERLAPS the first two rather than replacing a
    slice of either. `corporate` and `ceo_only` partition every file by the
    two-value collapse `get_classification` performs; `unclassified` re-reports
    whichever of them took the map default instead of matching a rule.

    Which of the two it lands in, measured rather than assumed: an unmatched
    path resolves `engine`, `get_classification` collapses everything that is
    not `private` to `"corporate"`, so a file with no rule is counted CORPORATE.
    This docstring said it "was counted as CEO-only" until 2026-08-24. That was
    never true of any code this repository has carried, and the 2026-08-23 audit
    read the sentence, reasoned from it, and reported a counting defect that
    does not exist. A wrong claim about the past is read as a claim about the
    present.

    What WAS added on 2026-08-24 is the third list itself. `--unclassified` was
    a registered argument nothing read, so an operator who ran it got the
    ordinary summary and reasonably concluded there were none.

    "Unclassified" is a real, askable question: it means the routing map matched
    no rule and the path took the map default. `matched_routing_rule` answers it.
    """
    files = walk_workspace(root)
    corporate = []
    ceo_only = []
    unclassified = []

    for f in files:
        classification = get_classification(f)
        if matched_routing_rule(f) is None:
            unclassified.append(f)
        if classification == "corporate":
            corporate.append(f)
        else:
            ceo_only.append(f)

    return {
        "total": len(files),
        "corporate": corporate,
        "ceo_only": ceo_only,
        "unclassified": unclassified,
    }


def print_report(results: dict):
    """Print colored terminal report."""
    total = results["total"]
    corp_count = len(results["corporate"])
    ceo_count = len(results["ceo_only"])

    print(f"\n{BOLD}Classification Health Report{RESET}")
    print(f"{'=' * 40}")
    print(f"  Total files:  {total}")
    print(f"  {GREEN}Corporate:  {corp_count}{RESET}")
    print(f"  {YELLOW}CEO-only:   {ceo_count}{RESET}")
    # Reported, not alarmed. Taking the map default is the DESIGNED outcome for
    # shareable engine code (`.claude/rules/classification.md`: "A new file left
    # at the engine default needs no entry only when it is genuinely shareable
    # code"), so most of this count is correct and colouring it red would make
    # every healthy run look broken. What the number is for is the inverse: a
    # path under here that should NOT be engine has no rule saying so.
    print(f"  {CYAN}No explicit rule (took the map default): "
          f"{len(results['unclassified'])}{RESET}  {GREEN}--unclassified to list{RESET}")
    print()


def print_corporate(results: dict):
    """Print list of corporate-classified files."""
    print(f"\n{BOLD}Corporate-classified files ({len(results['corporate'])}){RESET}")
    print(f"{'-' * 40}")
    for f in sorted(results["corporate"]):
        print(f"  {GREEN}{f}{RESET}")
    print()


def print_unclassified(results: dict):
    """Print the paths no routing rule governs, grouped by top-level directory.

    The flag this serves was registered and never read, and the list it needs
    did not exist, so the operator who ran `--unclassified` saw the ordinary
    summary. Both are new 2026-08-24. A default-taker is counted CORPORATE by
    `classify_files`, not CEO-only; see the correction in its docstring.

    This is a REVIEW list, not a defect list. The map's default is `engine`, and
    that default is the right answer for most code, so the grouped counts come
    first: the question worth asking is whether any GROUP here should have been
    private or corporate, not whether each of two thousand engine files needs a
    line in the map.
    """
    unclassified = sorted(results["unclassified"])
    default = load_routing_map().get("default", "engine")
    print(f"\n{BOLD}Paths with no explicit routing rule ({len(unclassified)}){RESET}")
    print(f"{'-' * 60}")
    if not unclassified:
        print(f"  {GREEN}none: every scanned path matches a rule in "
              f"config/routing-map.yaml{RESET}\n")
        return
    print(f"  All of these resolve to the map default: {CYAN}{default}{RESET}.")
    print(f"  {GRAY}Normal for shareable code. Check whether any group below "
          f"should not be {default}.{RESET}\n")
    groups: dict[str, int] = {}
    for f in unclassified:
        head = f.split("/", 1)[0] if "/" in f else "(repo root)"
        groups[head] = groups.get(head, 0) + 1
    for head, count in sorted(groups.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>5}  {head}")
    print(f"\n{GRAY}  Full list:{RESET}")
    for f in unclassified:
        print(f"    {f}")
    print(f"\n  To pin one: add its path under `rules:` in "
          f"config/routing-map.yaml, then re-run this check.\n")


def print_json(results: dict):
    """Print JSON output."""
    output = {
        "total": results["total"],
        "corporate_count": len(results["corporate"]),
        "ceo_only_count": len(results["ceo_only"]),
        "unclassified_count": len(results["unclassified"]),
        "unclassified_files": sorted(results["unclassified"]),
        "corporate_files": sorted(results["corporate"]),
        "ceo_only_files": sorted(results["ceo_only"]),
    }
    print(json.dumps(output, indent=2))


def detect_outputs_drift(threshold: int = 5) -> list[dict]:
    """Flag outputs/ subdirectories with > threshold files that lack an explicit config entry.

    New `outputs/` subtrees inherit the `outputs/` -> private routing rule, so
    inheritance is safe (private is the most-restrictive destination). But subdirs
    accumulating significant content without an explicit entry in
    `config/routing-map.yaml` hide from CEO review and can drift into unintended
    routing if the rule is ever changed. This check surfaces them so the CEO can
    decide whether to pin a rule or leave as inherited.

    outputs/ is DATA: resolved under the DATA root via get_outputs_dir() (data-root
    seam), not the engine clone.

    Returns a list of dicts: [{path, file_count, explicit}].
    """
    from scripts.utils.workspace import load_routing_map
    explicit_keys = set(load_routing_map()["rules"].keys())

    outputs_root = get_outputs_dir()
    if not outputs_root.is_dir():
        return []

    findings = []
    for subdir in sorted(outputs_root.iterdir()):
        if not subdir.is_dir():
            continue
        # Workspace convention: underscore-prefixed dirs (_sync, _temp, _scratch)
        # are transient/local and never leave the machine. Inheritance is safe.
        if subdir.name.startswith("_"):
            continue
        rel_subdir = f"outputs/{subdir.name}/"  # leak-guard: ok (relative classification lookup key)
        file_count = sum(1 for p in subdir.rglob("*") if p.is_file())
        explicit = rel_subdir in explicit_keys or any(
            k.startswith(rel_subdir) for k in explicit_keys
        )
        if file_count > threshold and not explicit:
            findings.append({
                "path": rel_subdir,
                "file_count": file_count,
                "explicit": False,
            })
    return findings


def print_outputs_drift(findings: list[dict], threshold: int = 5) -> None:
    """Print outputs/ drift findings."""
    if not findings:
        print(f"\n{GREEN}[PASS]{RESET} No outputs/ subdirectories >{threshold} files without explicit config.")
        return
    print(f"\n{BOLD}outputs/ Drift Detection{RESET}")
    print(f"{'-' * 40}")
    print(f"Subdirectories with >{threshold} files but no explicit entry in config/routing-map.yaml:")
    print()
    for f in findings:
        print(f"  {YELLOW}{f['path']}{RESET}  ({f['file_count']} files, inheriting from outputs/ default)")
    print()
    # Every path, not findings[0]. The instruction interpolated the first
    # finding for all of them, so an operator with three drifted subtrees
    # followed it literally, pinned one, saw the count drop, and left two
    # unpinned that no printed line had ever named.
    print(f"{CYAN}To pin, add these to `rules:` in config/routing-map.yaml "
          f"(private, or corporate/engine):{RESET}")
    for f in findings:
        print(f'{CYAN}  "{f["path"]}": private{RESET}')
    print(f"{CYAN}Leave in place if inheritance is intended.{RESET}")


# Kept in step with the `legal` set in scripts/utils/workspace.py. A destination
# outside it means the map is answering with something no consumer handles.
LEGAL_DESTINATIONS = {"engine", "private", "corporate"}


def routing_map_problems() -> list[str]:
    """Problems with `config/routing-map.yaml`, as operator-readable lines.

    `load_routing_map()` FAILS CLOSED, which is right for a resolver and
    invisible here: a missing file, an unreadable stat, or unparseable YAML all
    return `{"default": "private", "rules": {}}` and say nothing. This report
    then classifies every file against an empty map and prints a full-looking
    summary with no hint that the map never loaded.

    A health check for classification is the one place that state has to be
    named. Anything this returns is a failure, not a warning: the resolver every
    other gate in the workspace calls is not answering from the map on disk.
    """
    problems: list[str] = []
    path = get_workspace_root() / "config" / "routing-map.yaml"
    if not path.is_file():
        problems.append(f"routing map is not a file: {path}")
        return problems

    m = load_routing_map()
    if not m.get("rules"):
        problems.append(
            "routing map carries no rules, so every path takes the default; "
            "this is what a failed load looks like from the outside"
        )
        return problems

    # The map IN EFFECT never carries an illegal destination, because the
    # loader coerces one to 'private' and moves on. That coercion is correct
    # (a typo must not let a CEO subtree fall through to the public default)
    # and it is announced only on stderr, which nothing reads. So the check
    # that matters is not "is the effective map legal" -- it always is -- but
    # "does the map ON DISK say something the loader had to silently rewrite".
    # A one-character typo in one value reclassifies a whole subtree, and this
    # report is where an operator would look for it.
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        problems.append(f"routing map does not parse: {exc}")
        return problems
    if not isinstance(raw, dict):
        problems.append(
            f"routing map is a {type(raw).__name__}, not a mapping"
        )
        return problems

    raw_default = raw.get("default", "private")
    if raw_default not in LEGAL_DESTINATIONS:
        problems.append(
            f"routing map on disk says default: {raw_default!r}, which is not "
            f"one of {sorted(LEGAL_DESTINATIONS)}; the loader silently used "
            f"{m['default']!r} instead"
        )
    raw_rules = raw.get("rules") or {}
    if isinstance(raw_rules, dict):
        for key, value in sorted(raw_rules.items()):
            if value not in LEGAL_DESTINATIONS:
                problems.append(
                    f"routing map on disk gives {key!r} the destination "
                    f"{value!r}, which is not one of "
                    f"{sorted(LEGAL_DESTINATIONS)}; the loader silently used "
                    f"'private' instead"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Classification health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--corporate-only", action="store_true", help="List corporate files only")
    parser.add_argument("--unclassified", action="store_true", help="List unclassified files only")
    parser.add_argument(
        "--outputs-drift",
        action="store_true",
        help="Flag outputs/ subdirs >5 files without explicit config entry",
    )
    parser.add_argument("--drift-threshold", type=int, default=5, help="File-count threshold for drift (default 5)")
    args = parser.parse_args()

    root = get_workspace_root()

    # Every branch below resolves paths through the routing map, so an
    # unloadable map makes every verdict meaningless. Checked first, on stderr
    # so `--json` stays parseable, and it is a REFUSAL rather than a warning.
    problems = routing_map_problems()
    if problems:
        for line in problems:
            print(f"{RED}[FAIL]{RESET} {line}", file=sys.stderr)
        return 1

    if args.outputs_drift:
        findings = detect_outputs_drift(threshold=args.drift_threshold)
        print_outputs_drift(findings, threshold=args.drift_threshold)
        return 1 if findings else 0

    results = classify_files(root)

    # A pass over an empty corpus is not a pass. This walk asks git what to
    # skip, and `not_ignored` raises when git cannot answer -- but a workspace
    # root pointed at the wrong directory returns zero files with no error, and
    # every count below would then print a clean-looking zero.
    if not results["total"]:
        print(f"{RED}[FAIL]{RESET} the walk returned 0 files, so nothing was "
              f"classified; a pass over an empty corpus is not a pass",
              file=sys.stderr)
        return 1

    if args.json:
        print_json(results)
    elif args.unclassified:
        print_unclassified(results)
    elif args.corporate_only:
        print_corporate(results)
    else:
        print_report(results)
    return 0


if __name__ == "__main__":
    # `main()` used to end without a return and the call here discarded it, so
    # the CI step named "Classification health" could not fail on any input. A
    # deliberately corrupted routing map produced a full report and exit 0.
    sys.exit(main())
