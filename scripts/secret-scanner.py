#!/usr/bin/env python3
"""
secret-scanner.py - Scan files for accidentally included secrets.

Usage:
  python3 scripts/secret-scanner.py FILE [FILE...]       # Scan specific files
  python3 scripts/secret-scanner.py --stdin               # Read file list from stdin (for git hooks)
  python3 scripts/secret-scanner.py --stdin0              # Same, NUL-delimited (for callers that ran `git -z`)
  python3 scripts/secret-scanner.py --scan-dir DIR        # Scan all files in directory

`--stdin0` exists because `--stdin` cannot carry every legal filename and the
callers that matter had already gone to the trouble of preserving them.
`scripts/push-all.py` and `scripts/publish-service.py` both list the files about
to leave the machine with `git ... -z` and decode the raw bytes, precisely so a
path holding a newline survives; both then joined that list with `"\n"` for this
scanner, which split it back into two names that open nothing. MEASURED
2026-09-01 in scratch repositories: a tracked `two\nlines.env` carrying a
`ghp_`-shaped token produced "No secrets detected." and a clean verdict from
BOTH gates, while the identical token in `creds.env` was refused. `--stdin0`
splits on NUL, so a name is a name whatever bytes are in it.

Exit codes:
  0 = clean (no secrets found)
  1 = secrets detected
  2 = scanner error, which includes a file that exists and could not be read
      (UNKNOWN coverage, never a pass)

  When both occur in one run, the exit is 1. A detected secret is a certainty
  and must never be reported as a tool malfunction; the unreadable list is
  printed to stderr either way, so nothing is hidden by the precedence.

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

# Binary/non-text extensions to skip.
#
# `.svg` is NOT here, and that is the point. SVG is text XML, so it was the one
# member of this set a secret could actually sit in - in a comment, an XML
# metadata block, a `data:` URI, or an inline `<script>` - and the skip runs
# before the read in every mode, so explicit files, `--stdin` (the git
# pre-commit path) and `--scan-dir` were all blind to it. `docs/assets/demo.svg`
# is tracked, so the hole was reachable here today.
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
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
    "scripts/utils/secret_patterns.py",
    ".env.example",
}


class UnreadableFile(OSError):
    """A file the scanner could not read. Never silently treated as clean."""


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
    except OSError as exc:
        # An unreadable file is UNKNOWN, not clean. Returning `findings` (empty)
        # here made a permission-denied or transiently unreadable file pass the
        # gate silently -- failing open exactly when the filesystem misbehaves,
        # with nothing logged. `PermissionError` is a subclass of `OSError`, so
        # the old two-item tuple was redundant as well.
        raise UnreadableFile(f"{filepath}: {exc}") from exc

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


WALK_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".memory-index",
    ".memory-index-code", ".codegraph",
})
# Above this, a "text" file is a build artefact or a data dump, not source.
WALK_MAX_BYTES = 5 * 1024 * 1024


def _walk_scannable(root: Path, oversized: list | None = None):
    """Yield files under `root`; record what is skipped rather than hiding it.

    A file skipped for SIZE is coverage this scanner did not have, not a
    file it found clean. MEASURED 2026-08-29: a 6 MB log holding a live AWS
    key made `--scan-dir` print "No secrets detected." and exit 0, while
    naming the same file explicitly exited 1. Its path goes into
    `oversized` so `main` can say so.

    An entry that cannot be STATTED is worse and was silently dropped. A
    directory at mode 0444 is listable but not stattable into, and
    MEASURED on ordinary ext4 a real key inside one was missed with exit 0.
    Yield it and let `scan_files` decide: absent resolves to not-a-file and
    is skipped, refused is reported UNKNOWN.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > WALK_MAX_BYTES:
                    if oversized is not None:
                        oversized.append(path)
                    continue
            except OSError:
                yield path
                continue
            yield path


def scan_files(file_list: list, unreadable: list | None = None) -> dict:
    """Scan multiple files. Returns {filepath: [(line_num, desc), ...]}.

    Paths the scanner could not read are appended to `unreadable` rather than
    counted as clean; `main` turns a non-empty list into exit 2.
    """
    results = {}
    vault_files = []
    if unreadable is None:
        unreadable = []
    for filepath in file_list:
        # NOT `filepath.strip()`. A leading or trailing space is legal in a
        # POSIX filename, and `git ls-files -z` reports it verbatim; stripping
        # it here produced a name that opens nothing, which the `is_file()`
        # branch below then skipped in silence. MEASURED 2026-09-01: a tracked
        # `" leading-space.env"` holding a `ghp_`-shaped token came back clean
        # from `publish-service.secret_scan`, as did `"trailing-space.env "`.
        # The line-oriented `--stdin` reader strips its own lines, which is
        # where the padding it was written for actually comes from.
        if not filepath:
            continue
        try:
            if not Path(filepath).is_file():
                continue
        except OSError as exc:
            # `os.path.isfile` swallows every OSError and answers False, so
            # a path the scanner was REFUSED information about read exactly
            # like a path that is simply not there. `Path.is_file()` returns
            # False for ENOENT and RAISES for EACCES, which is the
            # distinction a gate needs: unreadable is UNKNOWN, never clean.
            unreadable.append(f"{filepath}: {exc}")
            continue
        if check_vault_path(filepath):
            vault_files.append(filepath)
            continue
        try:
            findings = scan_file(filepath)
        except UnreadableFile as exc:
            unreadable.append(str(exc))
            continue
        if findings:
            results[filepath] = findings

    if vault_files:
        # Vault files should never be staged - report as critical finding
        for vf in vault_files:
            results[vf] = [(0, "VAULT FILE - air-gapped, must never be committed")]

    return results


def print_results(results: dict, covered: bool = True) -> None:
    """Print scan results with colored output.

    `covered` is False when the run did not read everything it was pointed
    at. The green line is a claim about the whole scope, and it was printed
    unconditionally: MEASURED 2026-08-30, a 6 MB file holding a live AWS key
    under `--scan-dir` produced "No secrets detected." on stdout and exit 0,
    with the only trace on stderr. That is the sentence
    `.claude/rules/scope-claims.md` forbids -- one asserting more than the
    method established. Say what was scanned instead.
    """
    if not results:
        if covered:
            print(f"{GREEN}No secrets detected.{RESET}")
        else:
            print(f"{YELLOW}No secrets detected IN THE FILES THAT WERE READ. "
                  f"Coverage was incomplete (see stderr), so this is not a "
                  f"clean verdict.{RESET}")
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
        "--stdin0", action="store_true",
        help="Read a NUL-delimited file list from stdin (for `git ... -z` callers)"
    )
    parser.add_argument(
        "--scan-dir",
        help="Scan all text files in directory recursively"
    )
    args = parser.parse_args()

    file_list = []
    # Only --scan-dir can populate this: the other three modes scan exactly the
    # paths they were handed, so the gates that drive them see no change in
    # verdict. Those gates are the standalone pre-commit hook `install-hooks.py`
    # writes (`--stdin`), push-all.py and publish-service.py (`--stdin0` since
    # 2026-09-01), and the `secret-scanner-31c` pre-commit entry (argv).
    oversized: list = []

    if args.stdin0:
        # `sys.stdin.buffer`, not `sys.stdin`: the caller wrote raw path bytes
        # and the locale is not guaranteed to decode them. `surrogateescape`
        # round-trips whatever git emitted back through `os.fsencode` when the
        # path is opened, so a name that is not valid UTF-8 still names its
        # file. Nothing is stripped and nothing is split on a newline.
        raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
        file_list = [name for name in raw.split("\0") if name]
    elif args.stdin:
        file_list = [line.strip() for line in sys.stdin.read().strip().split("\n")]
    elif args.scan_dir:
        scan_dir = Path(args.scan_dir)
        # Prune whole trees rather than filtering afterwards. The bare rglob fed
        # every byte of .git (packfiles read as text), .venv and node_modules
        # through the pattern set, which is where the recursive sweep spent its
        # time and where its false positives came from.
        for path in _walk_scannable(scan_dir, oversized):
            file_list.append(str(path))
        if oversized:
            print(f"{YELLOW}{len(oversized)} file(s) over {WALK_MAX_BYTES} "
                  f"bytes were NOT scanned:{RESET}", file=sys.stderr)
            for path in oversized:
                print(f"  {path}", file=sys.stderr)
    elif args.files:
        # An EXPLICITLY named target that is not a file is an invocation
        # error, not a clean file. `scan_files` skips anything failing
        # `is_file`, so a typo'd path printed "No secrets detected." and
        # exited 0 over something never scanned. `sanitize-check.py` fixed
        # this same class three files away and it was not carried here.
        # The silent skip stays for --stdin and --scan-dir, where a path
        # that vanished between the listing and the scan is legitimate.
        missing = [f for f in args.files if not os.path.isfile(f)]
        if missing:
            print(f"{RED}{BOLD}SCANNER ERROR: not a readable file, so "
                  f"nothing was scanned: {', '.join(missing)}{RESET}",
                  file=sys.stderr)
            sys.exit(2)
        file_list = args.files
    else:
        parser.print_help()
        sys.exit(2)

    try:
        unreadable: list = []
        results = scan_files(file_list, unreadable)
        print_results(results, covered=not (unreadable or oversized))
        if unreadable:
            print(f"{RED}{BOLD}SCANNER ERROR: {len(unreadable)} file(s) could not "
                  f"be read and were NOT scanned:{RESET}", file=sys.stderr)
            for item in unreadable:
                print(f"  {item}", file=sys.stderr)
        # Count the refusal, one record per refused file. The reason names the
        # pattern description only; log_denial redacts, but the finding tuples
        # never carried the matched text in the first place. When the push wall
        # drives this scanner as a subprocess it sets HEADING_OS_DENIAL_CONTEXT
        # and does NOT log again, so one refusal is one record.
        # --scan-dir is the hand-run recursive sweep, driven by no gate; the hit
        # is real either way, but a report is not a refusal and the record says
        # which it was.
        action = "audit" if args.scan_dir else "scan"
        for filepath, findings in results.items():
            descriptions = sorted({desc for _line, desc in findings})
            log_denial(mechanism="secret-scanner", action=action,
                       path=filepath, reason="; ".join(descriptions))
        # A DETECTED secret outranks an unreadable file. The `sys.exit(2)` that
        # used to stand above the loop cost two things in a mixed run: every
        # detected refusal was printed and never written to the denial log, and
        # `scripts/publish-service.py` - which branches on 1-vs-2 - printed
        # "secret-scanner error" over a real leak. Both blocks still print, so
        # nothing is hidden; only the code changed, to the one true statement
        # about what was found.
        if results:
            sys.exit(1)
        # A file skipped for SIZE is coverage this run did not have, which the
        # exit-code contract above calls UNKNOWN and "never a pass" -- the same
        # verdict an unreadable file already gets. It was printed to stderr and
        # then exited 0, so every machine consumer read the run as clean.
        # MEASURED 2026-08-30: a 6 MB file holding a live AWS key exited 0 under
        # `--scan-dir`, while naming the same file explicitly exited 1.
        sys.exit(2 if (unreadable or oversized) else 0)
    except Exception as e:
        print(f"{RED}Scanner error: {e}{RESET}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
