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
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root, get_classification, get_outputs_dir
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

# Directories to skip entirely
SKIP_DIRS = {
    ".git", ".sync", ".sentinel", ".sessions", "__pycache__",
    "node_modules", ".corporate-repo", ".crm-central-repo",
    "chrome-profile",
}

# File patterns to skip
SKIP_FILES = {".DS_Store", "Thumbs.db", ".gitignore", ".gitattributes"}


def walk_workspace(root: Path) -> list[str]:
    """Walk workspace and return list of relative file paths."""
    files = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(root)
        parts = rel.parts
        # Skip excluded directories
        if any(part in SKIP_DIRS for part in parts):
            continue
        # Skip hidden directories (except .claude)
        if any(part.startswith(".") and part != ".claude" for part in parts[:-1]):
            continue
        # Skip specific files
        if rel.name in SKIP_FILES:
            continue
        files.append(str(rel).replace("\\", "/"))
    return files


def classify_files(root: Path) -> dict:
    """Classify all workspace files and return results."""
    files = walk_workspace(root)
    corporate = []
    ceo_only = []

    for f in files:
        classification = get_classification(f)
        if classification == "corporate":
            corporate.append(f)
        else:
            ceo_only.append(f)

    return {
        "total": len(files),
        "corporate": corporate,
        "ceo_only": ceo_only,
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
    print()


def print_corporate(results: dict):
    """Print list of corporate-classified files."""
    print(f"\n{BOLD}Corporate-classified files ({len(results['corporate'])}){RESET}")
    print(f"{'-' * 40}")
    for f in sorted(results["corporate"]):
        print(f"  {GREEN}{f}{RESET}")
    print()


def print_json(results: dict):
    """Print JSON output."""
    output = {
        "total": results["total"],
        "corporate_count": len(results["corporate"]),
        "ceo_only_count": len(results["ceo_only"]),
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
    print(f"{CYAN}To pin: add `\"{findings[0]['path']}\": private` (or corporate/engine) to the rules{RESET}")
    print(f"{CYAN}in config/routing-map.yaml. Leave in place if inheritance is intended.{RESET}")


def main():
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

    if args.outputs_drift:
        findings = detect_outputs_drift(threshold=args.drift_threshold)
        print_outputs_drift(findings, threshold=args.drift_threshold)
        return

    results = classify_files(root)

    if args.json:
        print_json(results)
    elif args.corporate_only:
        print_corporate(results)
    else:
        print_report(results)


if __name__ == "__main__":
    main()
