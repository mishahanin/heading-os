#!/usr/bin/env python3
"""
secret-scanner.py - Scan files for accidentally included secrets.

Usage:
  python3 scripts/secret-scanner.py FILE [FILE...]       # Scan specific files
  python3 scripts/secret-scanner.py --stdin               # Read file list from stdin (for git hooks)
  python3 scripts/secret-scanner.py --scan-dir DIR        # Scan all files in directory

Exit codes:
  0 = clean (no secrets found)
  1 = secrets detected
  2 = scanner error

Used by:
  - .git/hooks/pre-commit (git pre-commit hook)
  - Standalone scanning
"""

import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import RED, YELLOW, GREEN, BOLD, RESET
from scripts.utils.denial_log import log_denial
from scripts.utils.secret_patterns import ALLOWLIST_TOKEN, iter_patterns
from scripts.utils.paths import get_workspace_root

# Binary/non-text extensions to skip
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dll", ".so",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".webm",
    ".pyc", ".pyo", ".class", ".o", ".a", ".lib",
    ".bin", ".dat", ".db", ".sqlite",
    ".pptx", ".docx", ".xlsx", ".dotx", ".potx",
    ".pen", ".session",
}

# Files that legitimately contain secret patterns (self-references, examples).
# Repo-relative paths, NOT basenames. A basename match blinds the scan to any
# file of that name anywhere in either repository, which is a blind spot the
# three original entries carried and Task 1 widened by one. Measured by the
# Task 1 reviewer with a planted key in a nested file.
SKIP_PATHS = {
    "scripts/secret-scanner.py",
    ".claude/hooks/prevent-secrets.py",
    "scripts/utils/secret_patterns.py",
    ".env.example",
}


def scan_file(filepath: str) -> list:
    """Scan a single file for secret patterns.

    Returns list of (line_num, pattern_desc) tuples. Never includes the actual secret.
    """
    findings = []
    ext = os.path.splitext(filepath)[1].lower()

    # Skip binary files
    if ext in SKIP_EXTENSIONS:
        return findings

    # Skip self-referencing files. Repo-relative path match, not basename: a
    # file outside the repository root (a /tmp fixture, for instance) has no
    # relative path and is simply scanned.
    try:
        rel = Path(filepath).resolve().relative_to(get_workspace_root()).as_posix()
    except ValueError:
        rel = None
    if rel in SKIP_PATHS:
        return findings

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError):
        return findings

    for line_num, line in enumerate(lines, 1):
        if ALLOWLIST_TOKEN in line:
            continue
        for pattern, desc in iter_patterns(line):
            if pattern.search(line):
                findings.append((line_num, desc))
                break  # One finding per line is enough

    return findings


def check_vault_path(filepath: str) -> bool:
    """Check if a file path is inside a `_secure/` directory (should never be staged).

    The `_secure/` vault was removed in Plan 5; this guard is retained as cheap
    defence-in-depth — if any `_secure/`-prefixed path ever reappears it is still
    blocked from being committed.
    """
    normalized = filepath.replace("\\", "/")
    return "/_secure/" in normalized or normalized.startswith("_secure/")


def scan_files(file_list: list) -> dict:
    """Scan multiple files. Returns {filepath: [(line_num, desc), ...]}."""
    results = {}
    vault_files = []
    for filepath in file_list:
        filepath = filepath.strip()
        if not filepath or not os.path.isfile(filepath):
            continue
        if check_vault_path(filepath):
            vault_files.append(filepath)
            continue
        findings = scan_file(filepath)
        if findings:
            results[filepath] = findings

    if vault_files:
        # Vault files should never be staged - report as critical finding
        for vf in vault_files:
            results[vf] = [(0, "VAULT FILE - air-gapped, must never be committed")]

    return results


def print_results(results: dict) -> None:
    """Print scan results with colored output."""
    if not results:
        print(f"{GREEN}No secrets detected.{RESET}")
        return

    total = sum(len(findings) for findings in results.values())
    print(f"\n{RED}{BOLD}SECRETS DETECTED: {total} finding(s) in {len(results)} file(s){RESET}\n")

    for filepath, findings in results.items():
        print(f"  {YELLOW}{filepath}{RESET}")
        for line_num, desc in findings:
            print(f"    Line {line_num}: {RED}{desc}{RESET}")
        print()

    print(f"{BOLD}Remove secrets before committing. Store API keys in .env, passwords in password manager.{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Scan files for accidentally included secrets."
    )
    parser.add_argument("files", nargs="*", help="Files to scan")
    parser.add_argument(
        "--stdin", action="store_true",
        help="Read file list from stdin (one per line, for git hooks)"
    )
    parser.add_argument(
        "--scan-dir",
        help="Scan all text files in directory recursively"
    )
    args = parser.parse_args()

    file_list = []

    if args.stdin:
        file_list = sys.stdin.read().strip().split("\n")
    elif args.scan_dir:
        scan_dir = Path(args.scan_dir)
        for path in scan_dir.rglob("*"):
            if path.is_file():
                file_list.append(str(path))
    elif args.files:
        file_list = args.files
    else:
        parser.print_help()
        sys.exit(2)

    try:
        results = scan_files(file_list)
        print_results(results)
        # Count the refusal, one record per refused file. The reason names the
        # pattern description only; log_denial redacts, but the finding tuples
        # never carried the matched text in the first place. When the push wall
        # drives this scanner as a subprocess it sets HEADING_OS_DENIAL_CONTEXT
        # and does NOT log again, so one refusal is one record.
        for filepath, findings in results.items():
            descriptions = sorted({desc for _line, desc in findings})
            log_denial(mechanism="secret-scanner", action="scan",
                       path=filepath, reason="; ".join(descriptions))
        sys.exit(1 if results else 0)
    except Exception as e:
        print(f"{RED}Scanner error: {e}{RESET}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
