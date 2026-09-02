#!/usr/bin/env python3
"""What appeared, changed, or vanished in the datastore, read from git.

A thin reader over the DATA repository's own history. It creates no store, no
daemon and no database: every answer below is a `git` invocation plus a count.

Five questions, all scoped to the datastore directory inside the data repo:

    new        files added since a point in time
    changed    files modified since a point in time
    gone       files deleted since a point in time
    untracked  files on disk that git does not track, plus ignored entries,
               with tracked-but-modified reported separately
    summary    one block: tracked, on disk, untracked, and the window's counts

`untracked` is the one that answers "things appear and disappear here". git can
only report a deletion that was committed, so anything coming and going without
a commit is invisible to `new`, `changed` and `gone` and shows up only there.

Usage:
    python scripts/datastore-log.py new
    python scripts/datastore-log.py changed --since 30d
    python scripts/datastore-log.py gone --since 2026-08-01
    python scripts/datastore-log.py untracked --limit 200
    python scripts/datastore-log.py summary --json

Exit codes:
    0  a successful answer, INCLUDING an answer of zero. A week with no new
       file is a real answer, not a failure.
    1  a refusal (no data root, not a git repository, no datastore directory)
       or a failed git invocation. The reason is named on stderr.
    2  a malformed argument (argparse).

Tests: tests/test_a_datastore_reader_that_could_not_tell_absent_from_empty.py
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import DataRootError, get_data_root, get_datastore_dir


class DatastoreLogError(Exception):
    """A refusal that names which precondition failed.

    Separate from a git failure so the caller can print git's own stderr for
    the latter and this message for the former. Both exit 1; only one of them
    means the operator's setup is wrong rather than the tool.
    """


class GitFailed(DatastoreLogError):
    """git ran and returned non-zero. Carries git's stderr verbatim."""


# ============================================================
# Argument parsing
# ============================================================

_DURATION = re.compile(r"^(\d+)([dh])$")
_UNIT_WORD = {"d": "days", "h": "hours"}


def parse_since(value: str) -> str:
    """A `--since` argument as a string git accepts.

    Two shapes. `Nd` / `Nh` becomes "N days ago" / "N hours ago", which git's
    approxidate parser reads. An ISO date passes through untouched.

    Validated here rather than handed to git raw, because git treats a date it
    cannot parse as the epoch and answers with the whole history instead of
    refusing. A silent widening of the window is the worst failure this tool
    has available: it looks like a real answer.
    """
    text = value.strip()
    match = _DURATION.match(text)
    if match:
        return f"{match.group(1)} {_UNIT_WORD[match.group(2)]} ago"
    try:
        date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is neither a duration like 7d or 24h nor an ISO date "
            f"like 2026-08-01"
        ) from None
    return text


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("--limit must be at least 1")
    return number


# ============================================================
# Resolving the repository and the scope
# ============================================================

def resolve_scope() -> tuple[Path, Path, str]:
    """The data repository root, the datastore directory, and the scope between.

    The scope is the datastore path relative to the root, which is what a git
    pathspec needs.

    Every failure mode gets its own sentence, because "no results" and "I was
    pointed at nothing" print identically once they reach a count, and only one
    of them is an answer.
    """
    try:
        root = get_data_root()
    except DataRootError as exc:
        raise DatastoreLogError(str(exc)) from exc

    if not root.is_dir():
        raise DatastoreLogError(
            f"the data root does not exist: {root}. Nothing was read, and a "
            f"count of zero here would be a lie about an empty datastore."
        )

    # `.git` is a directory in a normal clone and a file in a linked worktree,
    # so existence is the question, not directory-ness. Asked of the root
    # itself rather than through `git rev-parse`, which walks UP and would
    # happily answer for an enclosing repository the datastore is not in.
    if not (root / ".git").exists():
        raise DatastoreLogError(
            f"the data root is not a git repository: {root} has no .git. "
            f"This tool reads history, so it has nothing to read."
        )

    datastore = get_datastore_dir()
    try:
        relative = datastore.resolve().relative_to(root.resolve())
    except ValueError:
        raise DatastoreLogError(
            f"the datastore resolves to {datastore}, which is outside the data "
            f"root {root}, so git in the data repo cannot see it."
        ) from None

    if not datastore.is_dir():
        raise DatastoreLogError(
            f"there is no datastore directory at {datastore}."
        )

    scope = str(relative).replace("\\", "/").rstrip("/") + "/"
    return root, datastore, scope


# ============================================================
# git
# ============================================================

def git_z(root: Path, args: list[str]) -> list[str]:
    """NUL-separated git output as real names, decoded from BYTES.

    Text mode would be wrong here for the reason `scripts/push-all.py` records
    at its own `_z_paths`: universal newlines rewrite every CR byte to LF, and
    a filename may legally contain one. `surrogateescape` carries a non-UTF8
    byte through intact rather than raising on it.

    No `.strip()`, for the same reason: a filename may begin or end with
    whitespace. Empty fields are dropped, which is safe because git never emits
    an empty path; the empties come from the deliberately blank
    `--pretty=format:` header.
    """
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise GitFailed(
            proc.stderr.decode("utf-8", "surrogateescape").strip()
            or f"git exited {proc.returncode} with no message"
        )
    out = proc.stdout.decode("utf-8", "surrogateescape")
    return [field for field in out.split("\0") if field]


def has_commits(root: Path) -> bool:
    """Whether the repository has a commit to read history from.

    A repository with no commit at all makes `git log` fail, and that is not a
    failure of this tool: an empty history legitimately holds no new, changed
    or deleted file. Asked separately so the zero can be reported as an answer
    instead of surfacing as git's error text.

    The two non-zero outcomes must not be merged, and merging them was the
    first version of this function. `rev-parse --verify --quiet HEAD` exits 1
    with an EMPTY stderr when the repository simply has no commit, and 128 with
    a fatal message when the directory is not a repository at all. Reading both
    as "no commits" turns an unreadable repository into a clean report of zero
    new files, which is the exact confusion between absent and empty that this
    whole tool is built to refuse.
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True, check=False,
    )
    if proc.returncode == 0:
        return True
    message = proc.stderr.decode("utf-8", "surrogateescape").strip()
    if message:
        raise GitFailed(message)
    return False


def log_paths(root: Path, scope: str, since: str, diff_filter: str) -> list[str]:
    """Distinct paths under `scope` that git's log reports for one filter.

    Deduplicated and sorted: a file modified in four commits inside the window
    is one changed file, not four.
    """
    if not has_commits(root):
        return []
    paths = git_z(root, [
        "log",
        f"--since={since}",
        f"--diff-filter={diff_filter}",
        "--name-only",
        "--pretty=format:",
        "-z",
        "--",
        scope,
    ])
    return sorted(set(paths))


def tracked_paths(root: Path, scope: str) -> list[str]:
    """Every path under `scope` that git tracks."""
    return sorted(set(git_z(root, ["ls-files", "-z", "--", scope])))


def status_groups(root: Path, scope: str) -> dict[str, list[str]]:
    """Working-tree status under `scope`, split three ways.

    `untracked` is a file git has never heard of. `ignored` is an entry a
    `.gitignore` rule matches; git collapses a wholly-ignored directory into
    one entry with a trailing slash, so this is a count of entries and not
    always of files. `modified` is anything else the status reports, which is a
    tracked file with uncommitted work in it.

    The three are reported separately because they answer different questions.
    An untracked file is invisible to history and can vanish without a trace; a
    modified tracked file is merely uncommitted.
    """
    fields = git_z(root, [
        "status", "--porcelain", "-z",
        "--untracked-files=all", "--ignored=matching",
        "--", scope,
    ])
    groups: dict[str, list[str]] = {"untracked": [], "ignored": [], "modified": []}
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        # The status code is exactly two columns wide and one of them may be a
        # space (" M" is a tracked file modified in the working tree only), so
        # the field is sliced, never split on whitespace.
        code, path = entry[:2], entry[3:]
        # A rename or copy emits the origin path as its own following field.
        # Consuming it here is what keeps the loop aligned; read as an entry it
        # would be parsed as a status code and a path that do not exist.
        if code[:1] in {"R", "C"}:
            index += 1
        if code == "??":
            groups["untracked"].append(path)
        elif code == "!!":
            groups["ignored"].append(path)
        else:
            groups["modified"].append(path)
    for key in groups:
        groups[key] = sorted(set(groups[key]))
    return groups


def on_disk_count(datastore: Path) -> int:
    """Files present in the datastore tree right now, ignored ones included."""
    return sum(1 for item in datastore.rglob("*") if item.is_file())


# ============================================================
# Output
# ============================================================

_HEADINGS = {
    "new": "files added",
    "changed": "files modified",
    "gone": "files deleted",
}


def print_list_report(kind: str, payload: dict, limit: int) -> None:
    print(f"\n{BOLD}Datastore: {kind}{RESET}  {GRAY}({_HEADINGS[kind]}){RESET}")
    print(f"  data root:  {payload['data_root']}")
    print(f"  scope:      {payload['scope']}")
    print(f"  window:     since {payload['since']}")
    print(f"  count:      {payload['count']}")
    files = payload["files"]
    if not files:
        # Zero is an answer, so it is printed in the same shape as any other
        # and coloured like a result rather than like a problem.
        print(f"\n  {GREEN}nothing{RESET} matched in this window\n")
        return
    print(f"  showing:    {len(files)} of {payload['count']}")
    print()
    for path in files:
        print(f"    {path}")
    if payload["truncated"]:
        print(f"\n  {YELLOW}list capped by --limit {limit}; "
              f"the count above is the true total{RESET}")
    print()


def print_untracked_report(payload: dict, limit: int) -> None:
    print(f"\n{BOLD}Datastore: untracked{RESET}  "
          f"{GRAY}(what git history cannot see){RESET}")
    print(f"  data root:  {payload['data_root']}")
    print(f"  scope:      {payload['scope']}")
    print(f"  untracked:  {payload['untracked_count']}")
    print(f"  ignored:    {payload['ignored_count']}   "
          f"{GRAY}(a trailing slash is a whole ignored directory){RESET}")
    print(f"  modified:   {payload['modified_count']}   "
          f"{GRAY}(tracked, with uncommitted changes){RESET}")

    sections = (
        ("untracked", "untracked (git has never seen these)", CYAN),
        ("ignored", "ignored (a .gitignore rule matches)", GRAY),
        ("modified", "tracked but modified (uncommitted)", YELLOW),
    )
    for key, title, color in sections:
        items = payload[key]
        if not items:
            continue
        print(f"\n  {color}{title}{RESET}  "
              f"{len(items)} of {payload[key + '_count']}")
        for path in items:
            print(f"    {path}")
    if payload["truncated"]:
        print(f"\n  {YELLOW}lists capped by --limit {limit}; "
              f"the counts above are the true totals{RESET}")
    print()


def print_summary_report(payload: dict) -> None:
    print(f"\n{BOLD}Datastore: summary{RESET}")
    print(f"  data root:  {payload['data_root']}")
    print(f"  scope:      {payload['scope']}")
    print(f"  window:     since {payload['since']}")
    print()
    print(f"  tracked:    {payload['tracked_count']}")
    print(f"  on disk:    {payload['on_disk_count']}")
    print(f"  {CYAN}untracked:  {payload['untracked_count']}{RESET}")
    print(f"  ignored:    {payload['ignored_count']}")
    print(f"  modified:   {payload['modified_count']}")
    print()
    print(f"  {GREEN}new:        {payload['new_count']}{RESET}")
    print(f"  {YELLOW}changed:    {payload['changed_count']}{RESET}")
    print(f"  {RED}gone:       {payload['gone_count']}{RESET}")
    print()


# ============================================================
# Subcommands
# ============================================================

def run_list(kind: str, root: Path, scope: str, since: str, limit: int) -> dict:
    paths = log_paths(root, scope, since, {"new": "A", "changed": "M", "gone": "D"}[kind])
    return {
        "subcommand": kind,
        "data_root": str(root),
        "scope": scope,
        "since": since,
        "count": len(paths),
        "files": paths[:limit],
        "truncated": len(paths) > limit,
    }


def run_untracked(root: Path, scope: str, limit: int) -> dict:
    groups = status_groups(root, scope)
    payload = {
        "subcommand": "untracked",
        "data_root": str(root),
        "scope": scope,
        "untracked_count": len(groups["untracked"]),
        "ignored_count": len(groups["ignored"]),
        "modified_count": len(groups["modified"]),
        "untracked": groups["untracked"][:limit],
        "ignored": groups["ignored"][:limit],
        "modified": groups["modified"][:limit],
    }
    payload["truncated"] = any(
        len(groups[key]) > limit for key in ("untracked", "ignored", "modified")
    )
    return payload


def run_summary(root: Path, datastore: Path, scope: str, since: str) -> dict:
    groups = status_groups(root, scope)
    return {
        "subcommand": "summary",
        "data_root": str(root),
        "scope": scope,
        "since": since,
        "tracked_count": len(tracked_paths(root, scope)),
        "on_disk_count": on_disk_count(datastore),
        "untracked_count": len(groups["untracked"]),
        "ignored_count": len(groups["ignored"]),
        "modified_count": len(groups["modified"]),
        "new_count": len(log_paths(root, scope, since, "A")),
        "changed_count": len(log_paths(root, scope, since, "M")),
        "gone_count": len(log_paths(root, scope, since, "D")),
    }


# ============================================================
# Entry point
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="What appeared, changed, or vanished in the datastore.",
    )
    parser.add_argument(
        "--since", type=parse_since, default="7d",
        help="a duration (7d, 24h) or an ISO date (2026-08-01); default 7d",
    )
    parser.add_argument("--json", action="store_true", help="machine output on stdout")
    parser.add_argument(
        "--limit", type=positive_int, default=50,
        help="cap the printed list; counts stay the true totals (default 50)",
    )
    parser.add_argument(
        "command",
        choices=["new", "changed", "gone", "untracked", "summary"],
        help="which question to ask",
    )
    return parser


def main() -> int:
    # A datastore filename may carry a byte no locale decodes, and it survives
    # the read via surrogateescape. Without the same handler on the way out,
    # printing it raises UnicodeEncodeError and the whole answer is lost over
    # one name. Guarded on the attribute because a captured stream is not
    # always a reconfigurable TextIOWrapper.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="surrogateescape")

    args = build_parser().parse_args()
    # argparse runs `type=` over a STRING default too, so `args.since` is
    # already the git-friendly form whether or not the flag was passed.
    since = args.since

    try:
        root, datastore, scope = resolve_scope()
        if args.command == "untracked":
            payload = run_untracked(root, scope, args.limit)
        elif args.command == "summary":
            payload = run_summary(root, datastore, scope, since)
        else:
            payload = run_list(args.command, root, scope, since, args.limit)
    except GitFailed as exc:
        print(f"{RED}[FAIL]{RESET} git failed: {exc}", file=sys.stderr)
        return 1
    except DatastoreLogError as exc:
        print(f"{RED}[FAIL]{RESET} {exc}", file=sys.stderr)
        return 1

    if args.json:
        # stdout stays parseable; every diagnostic above went to stderr.
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.command == "untracked":
        print_untracked_report(payload, args.limit)
    elif args.command == "summary":
        print_summary_report(payload)
    else:
        print_list_report(args.command, payload, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
