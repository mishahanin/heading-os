#!/usr/bin/env python3
"""SEC-003: Verify git actually refuses to track the sensitive paths.

Vulnerability: a sensitive path git will happily stage. Expected safe behavior:
`git check-ignore` matches every one of them.

Until 2026-08-27 this file asked the wrong question. It read `.gitignore` as
text and satisfied each requirement with `entry in line`, a substring scan over
every line, which two ordinary gitignore constructs defeat:

  * `!.env.example` CONTAINS `.env`, so the negation line - which grants the
    opposite of what the control asserts - satisfied the `.env` requirement.
    Measured: deleting the real `.env` and `.env.*` rules from the live
    `.gitignore` and leaving `!.env.example` in place kept this file green,
    while `git check-ignore .env` reported NOT IGNORED. `.env` holds the
    Exchange password and every API key.
  * The comment filter tested `line.startswith("#")` on the UNSTRIPPED line, so
    an indented `  # .sessions/` survived it, stripped to `# .sessions/`, and
    satisfied the `.sessions/` requirement. `.sessions/` holds OAuth refresh
    tokens.

The consumer of this control is git's ignore decision, so git is what the test
asks. That is also the established pattern in this suite
(tests/test_lock_sidecars_are_never_tracked.py, tests/test_denial_counter.py,
tests/test_apply_wizard_answers.py).
"""

import subprocess
from pathlib import Path

import pytest


# One representative path per sensitive rule. A directory rule needs a path
# INSIDE it, because `check-ignore` answers about a pathname, not a pattern.
# None of these has to exist on disk: the decision is pattern-based.
SENSITIVE_PATHS = [
    ".env",
    ".env.local",
    ".sessions/google-oauth-token.json",
    "outputs/browser/cookies.json",
    "outputs/browser/firecrawl-cache/some-page.json",
    "outputs/_sync/state.json",
    ".sentinel/state.json",
    "outputs/clipboard/clip.png",
]

# The control is vacuous if `_ignored` can only ever return True. These are
# tracked files, so a predicate that answers "ignored" for them is broken.
TRACKED_CONTROL_PATHS = [
    "README.md",
    "scripts/sentinel.py",
]


def _repo_root() -> Path:
    root = Path(__file__).resolve().parent.parent.parent
    if not (root / ".git").exists():
        pytest.skip("not a git checkout here")
    return root


def _ignored(repo: Path, path: str) -> bool:
    """True when git would refuse to track `path`.

    `check-ignore` exits 0 on a match and 1 on none. Anything above 1 is a real
    error and must not be read as "not ignored", which would turn a broken git
    invocation into a silent pass.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode > 1:
        pytest.fail(f"git check-ignore failed in {repo}: {result.stderr.strip()}")
    return result.returncode == 0


@pytest.mark.parametrize("rel", SENSITIVE_PATHS)
def test_git_refuses_to_track_the_sensitive_path(rel):
    repo = _repo_root()
    assert _ignored(repo, rel), (
        f"git would track {rel}. A `git add -A` sweeps it into the index, and "
        f"the engine repository is public. Add a rule to .gitignore - and note "
        f"that a negation line naming a neighbouring path does not count."
    )


@pytest.mark.parametrize("rel", TRACKED_CONTROL_PATHS)
def test_the_ignore_check_can_still_answer_no(rel):
    """Anchor. Without this, a `check-ignore` that matched everything - a stray
    `*` rule, a wrong cwd, a git that answers 0 for any input - would report
    total coverage and pass the whole file."""
    repo = _repo_root()
    assert not _ignored(repo, rel), (
        f"{rel} is tracked in this repository, so the ignore check answering "
        f"'ignored' for it means the check itself is broken, not that the file "
        f"is protected."
    )


def test_the_env_rule_is_not_satisfied_by_its_own_negation():
    """Pin the exact hole this file was rewritten to close.

    `.gitignore` legitimately carries `!.env.example` so the template stays
    tracked. The old substring predicate read that line as coverage for `.env`.
    This asserts both halves of the intended arrangement at once, from git.
    """
    repo = _repo_root()
    assert _ignored(repo, ".env"), "the credential file is not ignored"
    assert not _ignored(repo, ".env.example"), (
        ".env.example must stay tracked; it is the template a fresh clone copies"
    )
