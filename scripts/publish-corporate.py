#!/usr/bin/env python3
"""Canonical /push-updates file-copy step (Phase 2).

Closes the build-77 hotfix gap: every previous /push-updates run hand-typed
the list of files to copy to ../heading-os-corporate/. On 2026-05-27 the hand-typed
list missed scripts/implement-trajectory-log.py (the critical helper for
/implement v1.3 trajectory emission), shipping a functionally broken build
77 that required a build-78 hotfix.

The fix: derive the canonical "files to publish" set deterministically from
two inputs only — workspace classification rules and current file contents
versus the corporate repo. No hand-typed lists.

Usage:
  # Preview what would be copied (no changes):
  python scripts/publish-corporate.py --preview

  # Copy + verify (canonical /push-updates Phase 2):
  python scripts/publish-corporate.py --copy

  # Re-verify the corporate repo matches ceo-main (post-copy audit):
  python scripts/publish-corporate.py --verify

Exit codes:
  0  ok
  2  argument error
  3  workspace identity not admin (CEO-only operation)
  4  corporate repo missing at ../heading-os-corporate/
  5  (retired) was: classification config missing — now resolved via routing-map; never emitted
  6  copy failed (filesystem error)
  7  verify failed (a file differs, or is absent from the corporate repo)
  8  corporate .gitattributes lacks '* text=auto'
  9  refused: untracked corporate-classified files in the source working tree
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap only. This one is derived from __file__ because `get_workspace_root`
# lives behind the very sys.path entry it is adding; the authoritative value is
# reassigned from that helper immediately after the imports below. An audit read
# the pair as a dead first assignment -- removing it breaks every import here.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from scripts.utils.colors import CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_corporate_repo_path,
    get_data_root,
    get_routing_destination,
    get_workspace_root,
)
from scripts.utils.operator_identity import operator_slug  # noqa: E402

# Identity gate reads .workspace-identity.json from the engine clone root.
WORKSPACE_ROOT = get_workspace_root()
# Data-root seam (HEADING OS engine/data split): corporate-classified content
# (datastore/, knowledge/shared/, the context carve-outs, crm config/address-book,
# corporate/ daemon config) lives in the DATA overlay, NOT the engine tree. The
# publish source is therefore the data root; reading from the engine root would
# enumerate zero corporate files post-split (the build-? cutover gap).
SOURCE_ROOT = get_data_root()
# Destination resolves via the canonical resolver -> ../heading-os-corporate for
# the CEO workspace (was a hardcoded literal; centralised here).
CORPORATE_ROOT = get_corporate_repo_path()


# ============================================================
# Classification (HEADING OS step 7: the shared routing-map-backed resolver in
# scripts.utils.workspace is the single source of truth — no local copy).
#
# Cutover (step 8, 2026-06-14): publish-corporate ships ONLY files whose
# three-value routing destination is 'corporate'. Engine code is NOT published
# here — execs receive it by cloning the engine repo (.heading-os). This narrows
# the prior two-value collapse (which shipped routing 'corporate' ∪ 'engine').
# The corporate set is therefore content-only: datastore/, knowledge/shared/, the
# two context carve-outs, crm config/aliases/address-book — no scripts/skills/
# rules/tests. The two-value get_classification stays elsewhere (memory-index,
# health checks); publish uses get_routing_destination's three-value result directly.
# ============================================================
# ============================================================
# File enumeration (git-tracked workspace files)
# ============================================================
def _ls_files(*extra: str) -> list[str]:
    """`git ls-files` in the data overlay, NUL-separated, as real paths.

    The separator is not a style choice. `core.quotePath` defaults to on, so git
    C-quotes any path holding a byte outside printable ASCII: a Cyrillic
    filename comes back as `"datastore/\\320\\261.../x.md"`, quotes and octal
    escapes included. Fed to `get_routing_destination`, that string matches no
    rule key and falls to the map's `engine` default, so the file is silently
    never published and nothing reports a skip.

    Measured against the live overlay on 2026-08-27: 8294 tracked files, 66 of
    them C-quoted; resolved from their real names, 65 route `private` and one
    routes `corporate`. That corporate file had never reached an executive. The
    `engine` default is what made this an omission rather than a leak, which is
    luck, not a control.

    `-z` also suppresses the quoting, and it survives a path holding a newline,
    which `splitlines()` would cut in two. `scripts/push-all.py` reads the same
    form.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", *extra],
        cwd=str(SOURCE_ROOT),
        capture_output=True, text=True, check=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def list_tracked_files() -> list[str]:
    """Every file git knows about in the DATA overlay as a relative POSIX path.

    Source is the data root (not the engine root): corporate-classified content
    lives in the data repo post-split, so enumeration must run there.
    """
    return _ls_files()


def list_untracked_corporate_files() -> list[str]:
    """Untracked files (newly created this session, not yet git-added) that classify as corporate.

    Important for /push-updates: when the CEO has run `git commit` but the
    corporate-classified content of the commit hasn't yet propagated, those
    files ARE tracked. But for safety, also catch corporate-classified files
    that are untracked - they should be either committed or excluded before
    publish.
    """
    candidates = _ls_files("--others", "--exclude-standard")
    return [p for p in candidates if get_routing_destination(p) == "corporate"]


# ============================================================
# Diff vs corporate repo
# ============================================================
def diff_corporate(corporate_files: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    """Returns (new_files, modified_files, unchanged_files, missing_in_source)."""
    new_files: list[str] = []
    modified: list[str] = []
    unchanged: list[str] = []
    missing_in_source: list[str] = []
    for rel in corporate_files:
        src = SOURCE_ROOT / rel
        dst = CORPORATE_ROOT / rel
        if not src.exists():
            missing_in_source.append(rel)
            continue
        if not dst.exists():
            new_files.append(rel)
        elif filecmp.cmp(str(src), str(dst), shallow=False):
            unchanged.append(rel)
        else:
            modified.append(rel)
    return new_files, modified, unchanged, missing_in_source


# ============================================================
# Copy + verify
# ============================================================
def copy_files(files: list[str]) -> int:
    copied = 0
    for rel in files:
        src = SOURCE_ROOT / rel
        dst = CORPORATE_ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(dst))
            copied += 1
        except OSError as exc:
            print(f"{RED}ERROR: copy failed for {rel}: {exc}{RESET}", file=sys.stderr)
            return -1
    return copied


def verify_files(files: list[str]) -> tuple[int, list[str]]:
    matched = 0
    mismatches: list[str] = []
    for rel in files:
        src = SOURCE_ROOT / rel
        dst = CORPORATE_ROOT / rel
        if not dst.exists():
            mismatches.append(f"{rel} (missing in corporate)")
            continue
        if filecmp.cmp(str(src), str(dst), shallow=False):
            matched += 1
        else:
            mismatches.append(f"{rel} (content differs)")
    return matched, mismatches


# ============================================================
# Identity gate
# ============================================================
def verify_admin_identity() -> None:
    identity_path = WORKSPACE_ROOT / ".workspace-identity.json"
    if not identity_path.exists():
        print(f"{RED}ERROR: .workspace-identity.json missing.{RESET}", file=sys.stderr)
        sys.exit(3)
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{RED}ERROR: .workspace-identity.json is not valid JSON: {exc}{RESET}",
              file=sys.stderr)
        sys.exit(3)
    role = identity.get("role")
    if role != "admin":
        print(f"{RED}ERROR: this script is CEO-only. Current role: {role}{RESET}",
              file=sys.stderr)
        sys.exit(3)


def corporate_gitattributes_ok() -> bool:
    """The corporate repo's .gitattributes MUST carry `* text=auto` so exec
    corporate clones normalise line endings on checkout. Without it the clones
    accumulate CRLF working-tree churn that blocks `git pull --ff-only`, and
    propagation silently stalls (root cause of the build-84 fix). The corporate
    .gitattributes is hand-maintained and not published from ceo-main (the two
    legitimately differ on LFS), so it can drift stale -- this guard catches it.
    """
    ga = CORPORATE_ROOT / ".gitattributes"
    try:
        lines = ga.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(s == "* text=auto" or s.startswith("* text=auto ")
               for s in (ln.strip() for ln in lines))


def verify_corporate_repo() -> None:
    if not CORPORATE_ROOT.exists():
        print(f"{RED}ERROR: corporate repo not found at {CORPORATE_ROOT}.{RESET}",
              file=sys.stderr)
        sys.exit(4)
    if not (CORPORATE_ROOT / ".git").exists():
        print(f"{RED}ERROR: {CORPORATE_ROOT} exists but is not a git repo.{RESET}",
              file=sys.stderr)
        sys.exit(4)
    if not corporate_gitattributes_ok():
        print(f"{RED}WARN: corporate .gitattributes lacks `* text=auto`. Exec "
              f"clones will accumulate CRLF churn and silently fail "
              f"`git pull --ff-only` (see build 84). Fix before publishing.{RESET}",
              file=sys.stderr)


# ============================================================
# Modes
# ============================================================
def mode_preview(as_json: bool = False) -> int:
    """Show what --copy would publish. `as_json` emits a machine-readable summary.

    The JSON form exists for `ops_signals.publish_state`, the /radar signal that
    reports publish-to-fleet debt. It shelled out to `--dry-run --json`, two
    flags this parser has never defined, so the subprocess exited 2 on every
    run, `pending` never left 0, and the radar row was structurally incapable of
    firing. Measured 2026-08-27: `due=False` against a threshold of 1, with real
    corporate changes on disk.

    Everything human goes to stderr in JSON mode, so stdout carries one object
    and nothing else.
    """
    tracked = list_tracked_files()
    untracked_corp = list_untracked_corporate_files()
    corporate_files = [p for p in tracked if get_routing_destination(p) == "corporate"]

    if untracked_corp:
        print(f"{YELLOW}WARN: {len(untracked_corp)} untracked corporate-classified "
              f"file(s) detected. These must be committed before /push-updates "
              f"or they will not propagate. List:{RESET}", file=sys.stderr)
        for p in untracked_corp[:10]:
            print(f"  ?? {p}", file=sys.stderr)
        if len(untracked_corp) > 10:
            print(f"  ... and {len(untracked_corp) - 10} more", file=sys.stderr)

    new_files, modified, unchanged, missing = diff_corporate(corporate_files)

    if as_json:
        print(json.dumps({
            "pending": len(new_files) + len(modified),
            "new": len(new_files),
            "modified": len(modified),
            "unchanged": len(unchanged),
            "missing": len(missing),
            "untracked_corporate": len(untracked_corp),
        }))
        return 0

    print(f"{CYAN}Preview: {len(corporate_files)} corporate-classified tracked files "
          f"vs ../heading-os-corporate/{RESET}")
    print(f"  {GREEN}NEW: {len(new_files)}{RESET}")
    for p in new_files:
        print(f"    {p}")
    print(f"  {YELLOW}MODIFIED: {len(modified)}{RESET}")
    for p in modified:
        print(f"    {p}")
    print(f"  {GRAY}UNCHANGED: {len(unchanged)}{RESET}")
    if missing:
        print(f"  {RED}MISSING IN SOURCE: {len(missing)} (would orphan in corporate){RESET}")
        for p in missing:
            print(f"    {p}")
        print(f"{YELLOW}NOTE: missing-in-source files are not auto-deleted from corporate "
              f"by this script. Surface them to the CEO for manual cleanup.{RESET}",
              file=sys.stderr)
    print()
    print(f"Total to publish: {len(new_files) + len(modified)} files")
    return 0


def mode_copy() -> int:
    if not corporate_gitattributes_ok():
        print(f"{RED}ERROR: refusing to publish - corporate .gitattributes lacks "
              f"`* text=auto` (would reintroduce exec CRLF churn that silently "
              f"blocks propagation). Fix ../heading-os-corporate/.gitattributes first.{RESET}",
              file=sys.stderr)
        return 8
    tracked = list_tracked_files()
    untracked_corp = list_untracked_corporate_files()
    if untracked_corp:
        print(f"{RED}ERROR: {len(untracked_corp)} untracked corporate-classified "
              f"file(s) detected. Commit or .gitignore them before publishing.{RESET}",
              file=sys.stderr)
        for p in untracked_corp[:10]:
            print(f"  ?? {p}", file=sys.stderr)
        # 9, not 6. This is a working-tree hygiene REFUSAL: nothing was copied
        # and nothing failed. Sharing 6 with the filesystem-error path meant the
        # documented meaning ("copy failed") was false half the time it fired,
        # and a caller could not tell "fix your tree" from "the disk broke".
        return 9

    corporate_files = [p for p in tracked if get_routing_destination(p) == "corporate"]
    new_files, modified, _, missing = diff_corporate(corporate_files)
    to_copy = new_files + modified

    if not to_copy:
        print(f"{GREEN}Nothing to copy - corporate repo is already in sync.{RESET}")
        return 0

    print(f"{CYAN}Copying {len(to_copy)} file(s) to ../heading-os-corporate/...{RESET}")
    copied = copy_files(to_copy)
    if copied < 0:
        return 6

    print(f"{CYAN}Verifying {copied} copies...{RESET}")
    matched, mismatches = verify_files(to_copy)
    if mismatches:
        print(f"{RED}ERROR: post-copy verify found {len(mismatches)} mismatch(es):{RESET}",
              file=sys.stderr)
        for m in mismatches:
            print(f"  {m}", file=sys.stderr)
        return 7

    print(f"{GREEN}Published {copied} file(s): {len(new_files)} new + "
          f"{len(modified)} modified. All verified clean.{RESET}")
    if missing:
        print(f"{YELLOW}NOTE: {len(missing)} file(s) classified corporate exist in "
              f"corporate repo but not in ceo-main (orphans). Not auto-deleted. "
              f"Surface to CEO for cleanup.{RESET}")
    return 0


def mode_verify() -> int:
    tracked = list_tracked_files()
    corporate_files = [p for p in tracked if get_routing_destination(p) == "corporate"]
    new_files, modified, unchanged, missing = diff_corporate(corporate_files)
    # `new_files` means "classified corporate, present in the source, ABSENT
    # from the corporate repo" -- a file that never reached a single exec. It
    # used to be discarded into `_`, so this gate, which /push-updates Phase 3
    # runs immediately before the corporate commit, printed VERIFY OK over a
    # file that was simply not there. Absent is a worse failure than differing,
    # so it fails the same way.
    failures = [(p, "differs") for p in modified] + \
               [(p, "absent from corporate") for p in new_files]
    if failures:
        print(f"{RED}VERIFY FAILED: {len(failures)} file(s) do not match between "
              f"ceo-main and corporate.{RESET}", file=sys.stderr)
        for p, why in failures[:20]:
            print(f"  {p} ({why})", file=sys.stderr)
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more", file=sys.stderr)
        return 7
    if missing:
        print(f"{YELLOW}VERIFY WARNING: {len(missing)} file(s) classified corporate "
              f"are missing from ceo-main (orphans in corporate).{RESET}",
              file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        # Orphans are a warning, not a hard failure - return 0
    # `len(unchanged)`, not `len(corporate_files)`. Files in `missing` are
    # `continue`d inside diff_corporate and never compared to anything, yet the
    # old sentence counted them among the files it called matching -- it warned
    # about three files and then reported those same three as verified in the
    # next line.
    print(f"{GREEN}VERIFY OK: {len(unchanged)} corporate-classified file(s) compared "
          f"and identical.{RESET}")
    # Say what was NOT looked at. The file set is enumerated from the SOURCE
    # index alone (`git ls-files` in the data root); CORPORATE_ROOT is never
    # walked, so a file that exists only in the corporate repo cannot appear in
    # any count above, including the orphan warning.
    print(f"{GRAY}Source-side enumeration only: a file present ONLY in the corporate "
          f"repo is not visible to this check.{RESET}")
    return 0


def bump_build(summary: str = "Workspace update", structural: bool = False,
               files_changed: int | None = None) -> int:
    """Increment BUILD.json in the corporate repo: which build each exec holds.

    Opt-in (`--bump-build`); a plain publish does not touch it. PATCH bump by
    default, MINOR with --structural. Atomic write (tmp + os.replace). Does NOT
    commit or push - the caller stages it with the file copy.

    It was introduced for the two-stage staging/canary flow, which was removed
    on 2026-08-23 as unwired (see docs/EXTENDING.md). Build numbering stands on
    its own: `scripts/check-build.py` compares this number against each exec's
    copy to show sync drift. Note that the live corporate repo carries no
    BUILD.json yet, so nothing reads a real number until a first bump.
    """
    build_path = CORPORATE_ROOT / "BUILD.json"
    try:
        cur = json.loads(build_path.read_text(encoding="utf-8")) if build_path.exists() else {}
    except json.JSONDecodeError:
        cur = {}
    new_build = int(cur.get("build", 0)) + 1
    parts = (str(cur.get("version", "0.0.0")).split(".") + ["0", "0", "0"])[:3]
    try:
        major, minor, patch = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        major, minor, patch = 0, 0, 0
    if structural:
        minor, patch = minor + 1, 0
    else:
        patch += 1
    # Start from the file as found so a future BUILD.json key survives the bump.
    # Rebuilding from scratch and re-grafting only `history` erased everything
    # else, and guaranteed the same erasure for whatever key is added next.
    payload = dict(cur)
    payload |= {
        "version": f"{major}.{minor}.{patch}",
        "build": new_build,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "publisher": operator_slug(),
        "summary": summary,
    }
    # `files_changed` is written ONLY when the caller counted something.
    #
    # `--bump-build` is its own invocation, mutually exclusive with `--copy`, so
    # a bump never sees a copy's result. `main` therefore never passed this
    # argument and the default 0 was stamped into every BUILD.json the CLI ever
    # wrote: a hard number, in a published audit file, that no method computed.
    # Omitting the key is the honest state, and the pop is required because
    # `dict(cur)` above would otherwise carry the PREVIOUS build's count forward
    # under the new build number, which is worse than a zero.
    if files_changed is None:
        payload.pop("files_changed", None)
    else:
        payload["files_changed"] = files_changed
    # An old BUILD.json may still carry a `history` array from when the
    # since-removed promotion gate wrote force-promote records here. `dict(cur)`
    # above carries it (and anything else) forward, so no bump deletes an old
    # audit trail; nothing writes new entries into it any more.
    tmp = CORPORATE_ROOT / "BUILD.json.tmp"
    tmp.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")
    tmp.replace(build_path)
    print(f"{GREEN}BUILD.json bumped: build {new_build}, version {payload['version']}.{RESET}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The argument surface, separated so a test can check an argv against it.

    `verify_admin_identity()` runs inside `main`, so a test that tried to prove
    a caller's argv is accepted would have had to be an admin. That is why the
    `--dry-run --json` call in ops_signals went four months unnoticed.
    """
    parser = argparse.ArgumentParser(
        description="Canonical /push-updates Phase 2 file-copy step.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true",
                      help="Show what would be copied; no changes.")
    mode.add_argument("--copy", action="store_true",
                      help="Copy all corporate-classified changed files; verify post-copy.")
    mode.add_argument("--verify", action="store_true",
                      help="Verify corporate repo matches ceo-main; no changes.")
    mode.add_argument("--bump-build", action="store_true",
                      help="Increment BUILD.json (PATCH; MINOR with --structural). "
                           "Opt-in; a plain publish leaves it alone. No commit/push.")
    parser.add_argument("--summary", default="Workspace update",
                        help="Summary line written into BUILD.json on --bump-build.")
    parser.add_argument("--structural", action="store_true",
                        help="With --bump-build: MINOR version bump instead of PATCH.")
    parser.add_argument("--files-changed", type=int, default=None,
                        help="With --bump-build: the count `--copy` reported, written "
                             "into BUILD.json. Omitted from the file when not given, "
                             "rather than recorded as 0.")
    parser.add_argument("--json", action="store_true",
                        help="With --preview: emit a machine-readable summary on "
                             "stdout instead of the human table.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and not args.preview:
        parser.error("--json is only defined with --preview")

    verify_admin_identity()
    verify_corporate_repo()

    if args.preview:
        return mode_preview(as_json=args.json)
    if args.copy:
        return mode_copy()
    if args.verify:
        return mode_verify()
    if args.bump_build:
        return bump_build(summary=args.summary, structural=args.structural,
                          files_changed=args.files_changed)
    # Unreachable: the mode group is `required=True`, so argparse exits 2 itself
    # before any of the four branches is missed. Kept as an assertion rather than
    # a `return 2` the docstring documents as an argument error this path cannot
    # produce.
    raise AssertionError("no mode selected despite a required mutually exclusive group")


if __name__ == "__main__":
    sys.exit(main())
