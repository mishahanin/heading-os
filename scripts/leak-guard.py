#!/usr/bin/env python3
"""Leak guard for the HEADING OS engine/data boundary.

Two checks (HEADING OS spec Section 6):
  check-paths   Lint engine source for hardcoded data-path literals authored
                outside the get_*_dir() seam (scripts/utils/workspace.py).
  check-staged  Fail if any staged file routes to private/corporate while in
                the engine repo (gated by an engine-repo marker; inert on ceo-main).

Usage:
  python scripts/leak-guard.py check-paths  --files a.py b.py
  python scripts/leak-guard.py check-staged --files a.md b.md
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.denial_log import log_denial
from scripts.utils.workspace import get_data_root, get_routing_destination, get_workspace_root

# Data-path tokens that must never be hardcoded as string literals in engine code.
# Kept narrow on purpose: directory roots that resolve to private/corporate data.
DATA_PATH_TOKENS = [
    "crm/contacts",
    "knowledge/odin-brain",
    "outputs/",
    "threads/",
    "datastore/operations/tribe/fireside-state",
]

# Files allowed to contain these literals (the seam owns the canonical paths).
# Only .py/.sh members matter — the suffix filter below skips everything else,
# so non-code files do not need listing here (review finding L1).
SEAM_ALLOWLIST = {
    "scripts/utils/workspace.py",
    "scripts/leak-guard.py",
    "scripts/init-data.py",  # owns the canonical data-tree definition (scaffolds it from scratch)
}

# Match a quoted literal that STARTS with a data token — i.e. a path literal
# like "crm/contacts/..." — not a URL or log message that merely CONTAINS the
# substring ("https://x/outputs/y", "writing outputs/report"). Anchoring the
# token to the opening quote removes the URL / log-message false positives
# (review finding M4). A literal that builds a path from a token still starts
# with it: root / "crm/contacts" -> the literal is "crm/contacts".
_LITERAL_RE = re.compile(r"""['"]((?:%s)[^'"]*)['"]""" % "|".join(
    re.escape(t) for t in DATA_PATH_TOKENS
))


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(get_workspace_root()))
    except ValueError:
        return str(path)


# A path whose destination this gate knows without asking: it is engine code by
# definition, since it IS the engine's leak gate. Used only to ask the classifier
# a question whose right answer is already known.
_CLASSIFIER_CANARY = "scripts/leak-guard.py"


def _classifier_is_answering() -> bool:
    """True when `get_routing_destination` still recognises engine code.

    `load_routing_map()` fails CLOSED: an unreadable, non-UTF-8, or malformed
    `config/routing-map.yaml` classifies EVERY path 'private'. That is the right
    direction for `check_staged`, which then refuses everything. Composed with
    the `!= "engine"` skip in `check_paths` it is the WRONG direction: every file
    is skipped, no literal is ever examined, and the gate returns 0 - which is
    byte-for-byte what a clean tree looks like.

    MEASURED 2026-09-01 on one engine file holding a real violation
    (`P = "crm/contacts/x.md"`):

        healthy map   -> 1 violation, BLOCKED
        degraded map  -> 0 violations, silence          <- the defect

    At commit time the sibling `leak-guard-staged` hook masks it, because a
    degraded map makes it refuse every staged path. Nothing masks it in CI:
    `.github/workflows/ci.yml` runs `check-paths` over `git ls-files` with no
    `check-staged` beside it, so the whole-tree lint would pass having read
    nothing. Same shape as the unreadable-file skip fixed the same day, one
    level up: a control whose failure is indistinguishable from its success.
    """
    return get_routing_destination(_CLASSIFIER_CANARY) == "engine"


def check_paths(files) -> int:
    if not _classifier_is_answering():
        log_denial(mechanism="leak-guard:check-paths", action="commit",
                   path=_CLASSIFIER_CANARY,
                   reason="routing map degraded, so nothing was checked")
        print("BLOCKED - the routing map is not classifying engine code, so this "
              "gate checked nothing:")
        print(f"  config/routing-map.yaml answers "
              f"'{get_routing_destination(_CLASSIFIER_CANARY)}' for "
              f"{_CLASSIFIER_CANARY}, which is engine code by definition.")
        print("  The map failed closed to 'private' for every path, which makes "
              "this lint skip every file and report clean. Fix the map "
              "(unreadable? not UTF-8? `rules:` written as a list?) and re-run.")
        return 1
    violations = []
    # Paths this gate could NOT read. Tracked because until 2026-09-01 an
    # unreadable engine file was skipped with a bare `continue` and the gate
    # returned 0, which is byte-for-byte what a clean file looks like.
    #
    # MEASURED that day on one file holding a real violation
    # (`P = "crm/contacts/x.md"`), the same bytes in all three states:
    #
    #     readable      -> 1 violation, BLOCKED
    #     mode 0o000    -> 0 violations, silence          <- the defect
    #     not UTF-8     -> RAISED UnicodeDecodeError      <- the second defect
    #
    # The first is the shape SEC-007 refuses: a control whose failure is
    # indistinguishable from its success. The second is the decode class this
    # tree keeps finding, `UnicodeDecodeError` being a `ValueError` and not an
    # `OSError`, so the handler below could not catch it and the gate died
    # instead of refusing. A crash at least fails closed under pre-commit; the
    # silent skip did not fail at all.
    unreadable = []
    for f in files:
        p = Path(f)
        rel = _rel(p)
        if rel in SEAM_ALLOWLIST:
            continue
        if p.suffix not in {".py", ".sh"}:
            continue
        # Only lint actual engine code. Test files legitimately embed data-path
        # literals as fixtures; archived scripts under scripts/archive/ are inert
        # dead code (never run, retained for history); and a .py that itself routes
        # to private/corporate (e.g. a throwaway build script inside outputs/) is
        # not shippable engine code — linting any of these for "engine must not
        # hardcode data paths" is wrong.
        if (
            rel.startswith("tests/")
            or rel.startswith("scripts/archive/")
            or get_routing_destination(rel) != "engine"
        ):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append((rel, f"{type(exc).__name__}: {exc}"))
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # Inline suppression for legitimate relative-path usages (comparison
            # keys, prefix patterns, f-string display, log/error/regex strings)
            # that are NOT absolute-path construction. Annotate with a reason:
            #   "crm/contacts/",  # leak-guard: ok (relative prefix match)
            if "leak-guard: ok" in line:
                continue
            m = _LITERAL_RE.search(line)
            if m:
                violations.append((rel, n, m.group(1)))
    if violations:
        for rel, n, lit in violations:
            # The TOKEN, never the matched literal. `_LITERAL_RE` captures the
            # token plus everything up to the closing quote, so the literal can
            # carry a real path tail ("outputs/clients/<name>-contract.pdf") —
            # the refused content itself, which `denial_log` states a record
            # never carries and which `redact()` does not strip because a path
            # is not credential-shaped. content-guard and the push walls already
            # log only the class; this now matches them.
            token = next((t for t in DATA_PATH_TOKENS if lit.startswith(t)), "unknown")
            log_denial(mechanism="leak-guard:check-paths", action="commit",
                       path=f"{rel}:{n}", reason=f"hardcoded data-path literal [{token}]")
        print("BLOCKED - hardcoded data-path literal(s) outside the get_*_dir() seam:")
        for rel, n, lit in violations:
            print(f"  {rel}:{n}  \"{lit}\"  -> use a get_*_dir() helper from scripts/utils/workspace.py")
    if unreadable:
        # Refuse, rather than report a scope this gate never assembled. The
        # reason is only NOT to say "clean": an engine file that could not be
        # opened has not been checked, and the commit is the moment to say so.
        # Denials are logged with the class only, matching the violation branch
        # above, since a path is not credential-shaped and `redact()` would not
        # strip one.
        for rel, why in unreadable:
            log_denial(mechanism="leak-guard:check-paths", action="commit",
                       path=rel, reason=f"unreadable, so unchecked [{why.split(':')[0]}]")
        print("BLOCKED - engine file(s) this gate could not read, so did not check:")
        for rel, why in unreadable:
            print(f"  {rel}  -> {why}")
        print("  Fix the file, or take it out of the commit. A file the gate "
              "cannot read is not a file the gate found clean.")
    if violations or unreadable:
        return 1
    return 0


def _in_engine_repo() -> bool:
    """True when this clone is the split-topology engine (data lives in a sibling).

    Auto-detected from the data-root seam: when get_data_root() resolves to a
    DIFFERENT path than the workspace root, we are in the two-part topology and the
    working tree is the engine -- which must stay code-only. The legacy
    HEADING_OS_ENGINE_REPO=1 env var still forces-on as an explicit override, but is
    no longer the SOLE trigger: relying on a hand-set env var is exactly why this
    guard sat inert while four private specs leaked (2026-06-22). Pre-cutover single
    repo (data_root == workspace_root) -> inert, since data is legitimately tracked.
    """
    import os

    if os.environ.get("HEADING_OS_ENGINE_REPO") == "1":
        return True
    try:
        return get_data_root() != get_workspace_root()
    except Exception:
        # Fail-closed: if the seam cannot resolve, assume engine and enforce.
        return True


def check_staged(files) -> int:
    """Fail if a staged file routes to private/corporate, but only in the engine repo.

    Active whenever this clone is the split-topology engine (auto-detected via the
    data-root seam, or forced by HEADING_OS_ENGINE_REPO=1). Inert on a pre-cutover
    single repo where data files are legitimately tracked.
    """
    if not _in_engine_repo():
        return 0
    leaked = []
    for f in files:
        rel = f.replace("\\", "/").lstrip("/")
        if get_routing_destination(rel) in {"private", "corporate"}:
            leaked.append(rel)
    if leaked:
        for rel in leaked:
            log_denial(mechanism="leak-guard:check-staged", action="commit",
                       path=rel, reason=f"routes {get_routing_destination(rel)}")
        print("BLOCKED - non-engine content staged into the engine repo:")
        for rel in leaked:
            print(f"  {rel}  -> routes to '{get_routing_destination(rel)}'; belongs in the data/corporate repo")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="HEADING OS leak guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("check-paths")
    cp.add_argument("--files", nargs="*", default=[])
    cs = sub.add_parser("check-staged")
    cs.add_argument("--files", nargs="*", default=[])
    args = ap.parse_args()
    if args.cmd == "check-paths":
        return check_paths(args.files)
    if args.cmd == "check-staged":
        return check_staged(args.files)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
