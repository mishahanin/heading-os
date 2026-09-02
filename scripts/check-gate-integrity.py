#!/usr/bin/env python3
"""Ratchet: a declared gate that guards nothing, or that nothing tests.

Usage:
    python scripts/check-gate-integrity.py            # report
    python scripts/check-gate-integrity.py --check    # exit 1 on a NEW finding
    python scripts/check-gate-integrity.py --json

The two shapes this catches
---------------------------
**A gate scoped to nothing.** A `repo: local` hook carries a `files:` regex. Get
the regex slightly wrong -- a missing extension, an anchor in the wrong place, a
directory that was later renamed -- and the hook matches no file in the tree. It
then passes every commit, vacuously, forever, and its green line in the
pre-commit output reads exactly like a gate that ran. This is the same defect as
a test that is green over an empty corpus, one level up: the corpus here is the
set of files the hook was pointed at.

**A gate nothing tests.** A wall is code, and code that refuses is the hardest
kind to get right, because its correct behaviour is invisible on the happy path.
A gate with no test is a gate whose refusal has never been observed. The campaign
that ended 2026-09-02 found two walls that exited 0 unconditionally because of an
`or` short-circuit, and the reason nobody noticed is that nothing had ever asked
them to refuse.

MEASURED 2026-09-02 against `.pre-commit-config.yaml`: 25 local hooks; 0 with a
`files:` pattern matching no tracked file; 2 naming a script that no test file
names (`scripts/lint-ratchet.py`, `scripts/run-integration-tests.py`). Both are
frozen in BASELINE with that reason, so the ratchet holds today's state and
refuses tomorrow's third.

Scope, precisely (`.claude/rules/scope-claims.md`)
--------------------------------------------------
This reads `.pre-commit-config.yaml` and NOTHING ELSE. An earlier draft of this
paragraph said it also read the guard steps of `.github/workflows/ci.yml`; it
never did, and a scanner whose docstring claims a surface it does not open is
this file's own defect shape written in prose. A CI step with no pre-commit
sibling is therefore NOT covered here, and that gap is named rather than left to
be discovered.

Of what it does read, it asks two questions that are decidable from text alone:

1. Does this hook's `files:` regex match at least one path in `git ls-files`?
2. Is the script this hook or step invokes named, as a literal string, anywhere
   under `tests/`?

Question 2 is a NAMING check, not a coverage check. A test that names a script
in a comment satisfies it. That is deliberate and it is the honest limit: what a
test actually exercises is not decidable from the fact that it mentions a path,
and a check that claimed otherwise would be committing the defect this file
exists to find. The value is narrower and real: a gate that appears in no test
file at all has certainly never been driven to refuse.

An entry that runs inline Python (`python -c "..."`) names no script, so
question 2 cannot reach it. Those are listed in BASELINE by hook id with the
reason, rather than silently skipped -- a skipped case is a claim that decays.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.repo_files import IndexUnreadable, git_index_paths  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
PRECOMMIT = ".pre-commit-config.yaml"

# Below this the config itself has not been read. A hook list this short means the
# parse found something other than this repository's configuration.
MIN_HOOKS = 15  # measured 2026-09-02: 25 local hooks

# Frozen findings, each with the reason it is not a defect to fix today. A new
# finding is NOT added here by any code path; it has to be fixed or the reason
# has to be written by a person.
BASELINE: dict[str, str] = {
    "hook:lint-ratchet": (
        "scripts/lint-ratchet.py is named by no test. Real gap, pre-existing, "
        "and fixing it is writing a behavioural test for the ratchet rather "
        "than an edit to this file."
    ),
    "hook:sentinel-integration-tests": (
        "scripts/run-integration-tests.py is named by no test. Real gap, "
        "pre-existing, same shape as lint-ratchet above."
    ),
    "hook:vault-guard": (
        "Inline `python -c`, so it names no script and question 2 cannot reach "
        "it. Its behaviour is covered by tests/security/ vault-path cases."
    ),
    "hook:runtime-state-guard": (
        "Inline `python -c`, so it names no script. Same limit as vault-guard."
    ),
    "hook:pip-audit-cve": (
        "Inline `python -c` that shells out to pip-audit, an external tool. "
        "Nothing local to name."
    ),
    "hook:adversarial-suite-validate": (
        "Entry names tests/security/prompt-injection/run-adversarial-suite.py, "
        "which lives under tests/ and IS the harness. Question 2 looks for a "
        "scripts/ path and finds none."
    ),
    "hook:data-root-bypass-guard": (
        "Entry runs pytest directly on tests/test_data_root_no_bypass.py. The "
        "gate IS a test, so a test naming it is the file itself."
    ),
    "hook:engine-tree-clean": (
        "Entry runs pytest directly on tests/test_engine_tree_clean.py. Same "
        "as data-root-bypass-guard."
    ),
}


# ============================================================
# Reading the declarations
# ============================================================

class Unreadable(Exception):
    """The inputs could not be read, so no verdict is possible."""


def tracked_paths(root: Path) -> list[str]:
    """Every path git tracks, through the one shared reader.

    `git_index_paths` carries the three details this reader kept getting wrong
    on its own: `-z` so a non-ASCII or newline-bearing path is not C-quoted away,
    no text mode so a carriage return in a filename is not turned into a line
    feed, and surrogateescape so a path that is not valid UTF-8 survives. An
    empty or failed listing raises there rather than reading as "nothing to
    check", which is this file's own defect shape.
    """
    try:
        return git_index_paths(root)
    except IndexUnreadable as exc:
        raise Unreadable(str(exc)) from exc


def local_hooks(root: Path) -> list[dict]:
    path = root / PRECOMMIT
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Unreadable(f"{PRECOMMIT} unreadable: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("repos"), list):
        raise Unreadable(f"{PRECOMMIT} has no repos list")
    hooks = [hook
             for repo in config["repos"] if repo.get("repo") == "local"
             for hook in repo.get("hooks", [])
             if isinstance(hook, dict) and hook.get("id")]
    return hooks


def test_corpus(root: Path, tracked: list[str]) -> str:
    """Every tracked Python file under tests/, concatenated.

    Concatenation is the point: the question is whether a path is named
    ANYWHERE under tests/, not which file names it.
    """
    parts = []
    for rel in tracked:
        if not (rel.startswith("tests/") and rel.endswith(".py")):
            continue
        try:
            parts.append((root / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"{RED}cannot read {rel}: {exc}{RESET}", file=sys.stderr)
    if not parts:
        raise Unreadable("no tracked test files found")
    return "\n".join(parts)


# ============================================================
# The two rules, pure
# ============================================================

SCRIPT_RE = re.compile(r"(scripts/[\w\-./]+\.py)")


def hook_matches_nothing(hook: dict, tracked: list[str]) -> bool:
    """A `files:` regex that matches no tracked path. `always_run` is exempt:
    it declares no scope, so it has no scope to get wrong."""
    if hook.get("always_run"):
        return False
    pattern = hook.get("files")
    if not pattern:
        return False
    try:
        rx = re.compile(pattern)
    except re.error:
        return True  # an unparseable regex matches nothing, loudly
    return not any(rx.search(path) for path in tracked)


def hook_script(hook: dict) -> str | None:
    """The `scripts/` path this hook invokes, or None for inline entries."""
    match = SCRIPT_RE.search(hook.get("entry", ""))
    return match.group(1) if match else None


def findings(hooks: list[dict], tracked: list[str], tests: str) -> dict[str, str]:
    """`key -> reason` for every gate that guards nothing or that nothing names."""
    out: dict[str, str] = {}
    for hook in hooks:
        key = f"hook:{hook['id']}"
        if hook_matches_nothing(hook, tracked):
            out[key] = (f"files: {hook.get('files')!r} matches no tracked path, "
                        f"so this hook passes every commit vacuously")
            continue
        script = hook_script(hook)
        if script is None:
            out[key] = "entry names no scripts/ path, so no test can be found for it"
        elif script not in tests:
            out[key] = f"{script} is named by no file under tests/"
    return out


# ============================================================
# CLI
# ============================================================

def refuse(reason: str, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"refused": reason}, indent=2))
    else:
        print(f"{RED}{BOLD}REFUSED{RESET} {reason}", file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on a finding that is not in BASELINE")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    try:
        tracked = tracked_paths(ROOT)
        hooks = local_hooks(ROOT)
        tests = test_corpus(ROOT, tracked)
    except Unreadable as exc:
        return refuse(str(exc), as_json=args.json)

    if len(hooks) < MIN_HOOKS:
        return refuse(f"read {len(hooks)} local hooks, below the floor of "
                      f"{MIN_HOOKS}; the parse did not find this repository's "
                      f"configuration", as_json=args.json)

    found = findings(hooks, tracked, tests)
    new = {k: v for k, v in found.items() if k not in BASELINE}
    stale = sorted(k for k in BASELINE if k not in found)

    if args.json:
        print(json.dumps({"hooks": len(hooks), "findings": found,
                          "new": new, "stale_baseline": stale}, indent=2))
        return 1 if (args.check and new) else 0

    print(f"{BOLD}gate-integrity{RESET}  {len(hooks)} local hooks, "
          f"{len(found)} findings, {len(new)} new, {len(BASELINE)} frozen")

    if stale:
        print(f"{GRAY}{len(stale)} BASELINE entries no longer fire; remove them "
              f"from the dict with the same commit that fixed them{RESET}")
        for key in stale:
            print(f"  {GRAY}-{RESET} {key}")

    if not new:
        print(f"{GREEN}OK -- every declared gate has a scope and a test that "
              f"names it.{RESET}")
        return 0

    print(f"\n{RED}{BOLD}{len(new)} gate(s) guarding nothing, or named by no "
          f"test{RESET}")
    for key, reason in sorted(new.items()):
        print(f"  {RED}x{RESET} {key}: {reason}")
    print(f"\n{YELLOW}Fix the scope, or write the test that drives this gate to "
          f"refuse. Adding it to BASELINE needs a reason a person wrote.{RESET}")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
