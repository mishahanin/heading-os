#!/usr/bin/env python3
"""Pre-publish critical-leak scanner for `/publish-corporate`.

Scans a set of files (or a staged changeset) for the highest-sensitivity banned
terms that must never appear in corporate content, even though corporate is
trusted by the exec fleet. Read-only — blocks publishes when critical leaks
are found and expects the CEO to fix them manually before retrying.

Uses scanning primitives from `scripts/utils/sanitize.py`. (As of 2026-04-25
the AIOS export pipeline that previously shared these primitives lives in a
standalone OSS repo and is no longer part of this workspace.)

The critical-terms list is a deliberately small subset: things whose exposure
to the fleet would be a compliance incident, not just awkward:
- Credentials (API keys, session tokens) - caught separately by prevent-secrets hook
- Personal mail addresses (@gmail.com), which are the operator's, not the org's
  (there is NO mobile-number or postal-address pattern here; adding one is a
  change to SUBSTRING_CRITICAL, not a claim this docstring may make for it)
- CEO-only file paths (`crm/contacts/`, `knowledge/odin-brain/`, `_secure/`)

The `_secure/` path marker is retained as a defensive scan term even though the
vault was removed in Plan 5: if a `_secure/`-prefixed path ever reappears in
content bound for the corporate repo, this still flags it.

(The broader AIOS-for-the-CEO anonymization pass that previously lived
alongside this script in `export-sync.py` was archived on 2026-04-25 when
AIOS became an independent OSS repo.)

Usage:
    python scripts/sanitize-check.py FILE [FILE ...]     # scan specific files
    python scripts/sanitize-check.py --staged             # scan git-staged changes
    python scripts/sanitize-check.py --list-terms         # print the critical-terms set and exit

Exit codes:
    0 - no findings, safe to publish
    1 - one or more files contain critical terms; fix before publishing
    2 - invocation error (bad arguments, file missing, etc.)
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.sanitize import scan_for_terms
from scripts.utils.workspace import get_workspace_root

# Windows consoles default to cp1252; force UTF-8 so findings on non-Latin
# source lines (e.g. Cyrillic) print instead of crashing the scan mid-report.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


# Critical terms - small, curated. Add only when a leak would trigger compliance.
SUBSTRING_CRITICAL = {
    # CEO-only file-path markers that should never appear in corporate content
    "knowledge/odin-brain",  # leak-guard: ok (banned-term substring scanned in content, not a path)
    "odin-brain-health",
    "_secure/",   # defensive: vault removed (Plan 5), term retained to catch any stray _secure/ path
    # Private contact-data patterns
    "@gmail.com",      # Personal email domain (31C uses @31c.io for corporate)
}

WORD_BOUNDARY_CRITICAL: set[str] = set()


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sh",
    ".ps1",
    ".toml",
    ".cfg",
    ".ini",
}


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    if path.name in {".env.example", "Makefile"}:
        return True
    return False


class GitUnavailable(RuntimeError):
    """git could not report the staged set. NOT the same as an empty staged set."""


def staged_files() -> list[Path]:
    """Return git-staged files (adds + modifies) relative to workspace root.

    Raises GitUnavailable when git itself fails. Returning `[]` there made a
    broken git environment -- no repo, no git binary, a corrupt index -- read as
    "nothing staged", and this gate then printed a gray note and exited 0. A
    leak gate that fails OPEN on its own tooling error publishes unscanned
    content while looking healthy.

    `cwd` is pinned to the workspace root for the same reason: without it the
    command reported whatever repo the caller's directory happened to sit in,
    and those paths then failed to exist under the engine root -- another silent
    pass.
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(get_workspace_root()),
    )
    if result.returncode != 0:
        raise GitUnavailable((result.stderr or "git failed").strip())
    return [Path(line) for line in result.stdout.splitlines() if line]


def scan_file(
    path: Path,
    substring_terms: set[str],
    boundary_terms: set[str],
) -> list[tuple[str, int, str, str]]:
    """Scan one file. Returns findings list (empty on clean file or binary)."""
    if not path.exists() or not is_text_file(path):
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_for_terms(content, substring_terms, word_boundary_terms=boundary_terms)


def print_findings(file_findings: dict[Path, list]) -> None:
    for path, findings in file_findings.items():
        print(f"\n{RED}[LEAK]{RESET} {BOLD}{path}{RESET}")
        for term, line_num, line_text, match_type in findings:
            print(f"  {GRAY}line {line_num}:{RESET} {YELLOW}{term}{RESET} ({match_type})")
            print(f"    {GRAY}{line_text}{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan files for critical leak terms before publish.")
    parser.add_argument("files", nargs="*", help="Files to scan (use --staged for git-staged set)")
    parser.add_argument("--staged", action="store_true", help="Scan git-staged changes instead of explicit files")
    parser.add_argument("--list-terms", action="store_true", help="Print critical terms and exit")
    args = parser.parse_args()

    if args.list_terms:
        print(f"{BOLD}Substring-critical terms ({len(SUBSTRING_CRITICAL)}):{RESET}")
        for t in sorted(SUBSTRING_CRITICAL):
            print(f"  {t}")
        if WORD_BOUNDARY_CRITICAL:
            print(f"\n{BOLD}Word-boundary-critical terms ({len(WORD_BOUNDARY_CRITICAL)}):{RESET}")
            for t in sorted(WORD_BOUNDARY_CRITICAL):
                print(f"  {t}")
        return 0

    workspace_root = get_workspace_root()

    substring_terms = set(SUBSTRING_CRITICAL)
    boundary_terms = set(WORD_BOUNDARY_CRITICAL)

    if args.staged:
        try:
            files = staged_files()
        except GitUnavailable as exc:
            print(f"{RED}[ERROR]{RESET} cannot read the staged set: {exc}",
                  file=sys.stderr)
            return 2
        if not files:
            print(f"{GRAY}No staged changes to scan.{RESET}")
            return 0
    else:
        files = [Path(f) for f in args.files]
        # An EXPLICITLY named file that is absent is an invocation error, not a
        # clean file. `scan_file` returns [] for a missing path, so a typo'd
        # argument used to be counted as scanned and reported PASS -- the exact
        # miss this gate exists to prevent. The silent skip stays for --staged,
        # where a file deleted between the diff and the scan is legitimate.
        missing = [f for f in files if not (get_workspace_root() / f).exists()
                   and not f.exists()]
        if missing:
            print(f"{RED}[ERROR]{RESET} file(s) not found: "
                  f"{', '.join(str(m) for m in missing)}", file=sys.stderr)
            return 2

    if not files:
        parser.print_help()
        return 2

    file_findings: dict[Path, list] = {}
    for f in files:
        abs_path = f if f.is_absolute() else workspace_root / f
        findings = scan_file(abs_path, substring_terms, boundary_terms)
        if findings:
            file_findings[f] = findings

    if not file_findings:
        print(f"{GREEN}[PASS]{RESET} {len(files)} files scanned. No critical terms found.")
        return 0

    print(f"{RED}[FAIL]{RESET} {len(file_findings)} of {len(files)} files contain critical terms:")
    print_findings(file_findings)
    print(f"\n{YELLOW}Fix these before publishing to corporate.{RESET} Edit the source files to remove the terms.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
