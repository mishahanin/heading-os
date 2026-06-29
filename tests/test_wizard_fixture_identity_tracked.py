"""Regression guard: the exec-workspace test fixture's identity file must be
git-tracked, not swallowed by the broad `.workspace-identity.json` gitignore rule.

Bug (found 2026-06-29 on a fresh clone): `.gitignore` ignores
`.workspace-identity.json` everywhere (it is a machine-local runtime file), and
the rule also stripped `tests/fixtures/exec_workspace/.workspace-identity.json`
from clean checkouts. With the fixture identity absent, the wizard misdetects the
audience as 'public' instead of 'exec', rejects the exec-only `exec_full_name`
question (exit 5), and `tests/integration/test_setup_wizard_e2e.py` fails. It
passed only on machines where the untracked file happened to linger -- masking
the bug. The fix is a `!tests/fixtures/**/.workspace-identity.json` negation plus
committing the file; this test fails deterministically on EVERY machine if the
fixture is ever untracked again, not just on a fresh clone.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = "tests/fixtures/exec_workspace/.workspace-identity.json"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_exec_fixture_identity_is_git_tracked():
    # On disk for the test harness to copy.
    assert (REPO / FIXTURE).exists(), f"{FIXTURE} missing on disk"

    # Tracked in git so a fresh clone gets it (the actual regression).
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", FIXTURE],
        cwd=REPO, capture_output=True, text=True,
    )
    assert tracked.returncode == 0, (
        f"{FIXTURE} is not git-tracked -- a fresh clone would omit it and "
        f"test_setup_wizard_e2e.py would fail with audience misdetection. "
        f"Ensure the `!tests/fixtures/**/.workspace-identity.json` negation in "
        f".gitignore is present and the file is committed."
    )

    # And not re-ignored by some later rule.
    ignored = subprocess.run(
        ["git", "check-ignore", FIXTURE],
        cwd=REPO, capture_output=True, text=True,
    )
    assert ignored.returncode != 0, f"{FIXTURE} is matched by a .gitignore rule"
