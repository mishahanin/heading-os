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
import re
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import RED, YELLOW, GREEN, BOLD, RESET

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

# Files that legitimately contain secret patterns (self-references, examples)
SKIP_FILES = {
    "secret-scanner.py",
    "prevent-secrets.py",
    ".env.example",
}

# Inline allowlist token (same convention as Yelp/detect-secrets). A line carrying
# this marker is an intentional, reviewed pattern (test fixtures, docs) and is skipped.
ALLOWLIST_TOKEN = "pragma: allowlist secret"

# Secret patterns: (compiled_regex, description)
# Thresholds tuned to avoid matching placeholders like "sk-ant-your-key-here"
SECRET_PATTERNS = [
    # API key formats - require 16+ chars of key material after prefix (aligned with _dispatch.py, F-L4)
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{16,}'), "Anthropic API key"),
    (re.compile(r'pplx-[a-zA-Z0-9]{16,}'), "Perplexity API key"),
    (re.compile(r'r8_[a-zA-Z0-9]{16,}'), "Replicate API token"),
    (re.compile(r'fc-[A-Za-z0-9]{16,}'), "Firecrawl API key"),
    (re.compile(r'ctx7sk-[a-zA-Z0-9-]{16,}'), "Context7 API key"),
    (re.compile(r'ghp_[a-zA-Z0-9]{16,}'), "GitHub personal access token"),
    (re.compile(r'gho_[a-zA-Z0-9]{16,}'), "GitHub OAuth token"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS access key"),
    (re.compile(r'xoxb-[0-9]+-[a-zA-Z0-9]+'), "Slack bot token"),
    (re.compile(r'xoxp-[0-9]+-[a-zA-Z0-9]+'), "Slack user token"),
    (re.compile(r'ya29\.[A-Za-z0-9._-]{50,}'), "Google OAuth token"),
    # JWT, PEM private keys, and credentialed connection strings (F-L3; mirror in _dispatch.py)
    (re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'), "JWT bearer token"),
    (re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'), "PEM private key"),
    (re.compile(r'[a-zA-Z][a-zA-Z0-9+.-]*://(?!user:pass(?:word)?@|username:password@)[^:@\s/?]{2,}:[^:@\s/?]{2,}@'), "connection string with inline credentials"),
    # Markdown password fields with actual values (not placeholders)
    (re.compile(
        r'\*\*Password:\*\*\s+'
        r'(?!Stored|REDACTED|N/A|See |TBD|Change|Reset|Set |Use |Your )'
        r'[^\n]{8,}'
    ), "Plaintext password in markdown"),
    # Generic env-style password assignments with real values
    (re.compile(
        r'(?:EXCHANGE_PASSWORD|DB_PASSWORD|SMTP_PASSWORD|AUTH_PASSWORD)'
        r'\s*=\s*'
        r'(?!(?i:your[-_]|changeme|example|placeholder|redacted|dummy|xxx|<))'
        r'[A-Za-z0-9!@#$%^&*_+=-]{8,}'
    ), "Password in environment variable assignment"),
]


def scan_file(filepath: str) -> list:
    """Scan a single file for secret patterns.

    Returns list of (line_num, pattern_desc) tuples. Never includes the actual secret.
    """
    findings = []
    basename = os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    # Skip binary files
    if ext in SKIP_EXTENSIONS:
        return findings

    # Skip self-referencing files
    if basename in SKIP_FILES:
        return findings

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError):
        return findings

    for line_num, line in enumerate(lines, 1):
        if ALLOWLIST_TOKEN in line:
            continue
        for pattern, desc in SECRET_PATTERNS:
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
        sys.exit(1 if results else 0)
    except Exception as e:
        print(f"{RED}Scanner error: {e}{RESET}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
