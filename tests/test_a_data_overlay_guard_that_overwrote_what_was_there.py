"""The data overlay's commit guard: installed once, and never over anyone else.

Two things are asserted, and the second is the one that matters.

FIRST, the installer respects "additions only". A draft of this step ended with
`mv "${DATA_HOOK}.tmp" "$DATA_HOOK"` run unconditionally from every YARD
bootstrap, which destroys whatever pre-commit hook the overlay already had, on
a shared repository, several times a day.

SECOND, the hook it installs actually refuses a REAL commit. A hook that is
present and never fires is indistinguishable from one that is absent, so the
cases below run `git commit` for real in a throwaway repository, with and
without the marker, and assert on whether the commit LANDED -- not on the hook's
text, and not on its exit status alone.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-data-overlay-guard.py"
BODY = ROOT / "scripts" / "herdr" / "heading-os-yard" / "data-overlay-pre-commit"


def _repo(tmp_path: Path, name: str = "overlay") -> Path:
    """A real git repository, standing in for the data overlay."""
    root = tmp_path / name
    root.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"],
                   cwd=str(root), check=True, env=env)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(root), check=True, env=env)
    return root


def _install(overlay: Path, *flags: str):
    env = dict(os.environ, HEADING_OS_DATA=str(overlay))
    env.pop("WORKSPACE_ROOT", None)
    return subprocess.run(
        [sys.executable, str(INSTALLER), *flags],
        cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=120,
    )


def _hook(overlay: Path) -> Path:
    return overlay / ".git" / "hooks" / "pre-commit"


# ============================================================
# Installing
# ============================================================

def test_an_absent_hook_is_installed_and_executable(tmp_path):
    overlay = _repo(tmp_path)
    result = _install(overlay)
    assert result.returncode == 0, result.stderr
    hook = _hook(overlay)
    assert hook.is_file()
    assert os.access(hook, os.X_OK), "the hook must be executable or git ignores it"
    assert "HEADING_OS_YARD" in hook.read_text(encoding="utf-8")


def test_installing_twice_is_a_no_op_refresh(tmp_path):
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    first = _hook(overlay).read_bytes()
    result = _install(overlay)
    assert result.returncode == 0, result.stderr
    assert _hook(overlay).read_bytes() == first


def test_a_foreign_hook_is_refused_and_left_untouched(tmp_path):
    """The defect this test exists for. Additions only, without exception."""
    overlay = _repo(tmp_path)
    hook = _hook(overlay)
    hook.parent.mkdir(parents=True, exist_ok=True)
    existing = "#!/usr/bin/env bash\n# somebody else's hook\nexit 0\n"
    hook.write_text(existing, encoding="utf-8")

    result = _install(overlay)
    assert result.returncode == 1
    assert hook.read_text(encoding="utf-8") == existing, (
        "the installer overwrote a hook it did not write")
    assert "REFUSED" in result.stderr
    assert "HEADING_OS_YARD" in result.stderr, (
        "the refusal must print the fragment to merge by hand, or the operator "
        "is told no and given nothing")


# ============================================================
# Reporting
# ============================================================

def test_check_reports_missing_without_installing(tmp_path):
    overlay = _repo(tmp_path)
    result = _install(overlay, "--check")
    assert result.returncode == 1
    assert "missing" in result.stdout
    assert not _hook(overlay).exists(), "--check must change nothing"


def test_check_reports_armed_after_installation(tmp_path):
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    result = _install(overlay, "--check")
    assert result.returncode == 0
    assert "armed" in result.stdout


def test_check_reports_a_foreign_hook_distinctly(tmp_path):
    """Absent and foreign are different states with different remedies.

    A guard that cannot tell them apart tells the operator to run the installer
    when the installer is exactly what will refuse.
    """
    overlay = _repo(tmp_path)
    hook = _hook(overlay)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    result = _install(overlay, "--check")
    assert result.returncode == 1
    assert "foreign" in result.stdout


def test_print_emits_the_body_and_installs_nothing(tmp_path):
    overlay = _repo(tmp_path)
    result = _install(overlay, "--print")
    assert result.returncode == 0
    assert "HEADING_OS_YARD" in result.stdout
    assert not _hook(overlay).exists()


# ============================================================
# The hook itself, driving real commits
# ============================================================

def _commit(overlay: Path, filename: str, marked: bool):
    (overlay / filename).write_text("x\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("HEADING_OS_YARD", None)
    if marked:
        env["HEADING_OS_YARD"] = "1"
    subprocess.run(["git", "add", filename], cwd=str(overlay), check=True, env=env)
    return subprocess.run(
        ["git", "commit", "-m", f"add {filename}"],
        cwd=str(overlay), capture_output=True, text=True, env=env, timeout=120,
    )


def _commit_count(overlay: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=str(overlay), capture_output=True, text=True,
    )
    return int(result.stdout.strip()) if result.returncode == 0 else 0


def test_a_commit_from_a_yard_does_not_land(tmp_path):
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    assert _commit(overlay, "first.txt", marked=False).returncode == 0
    before = _commit_count(overlay)

    result = _commit(overlay, "second.txt", marked=True)
    assert result.returncode != 0
    assert _commit_count(overlay) == before, (
        "the commit landed anyway; the hook is not armed")
    assert "REFUSED" in result.stderr + result.stdout


def test_a_commit_from_helm_still_lands(tmp_path):
    """The pair. A hook that refused every commit would pass the test above and
    make the overlay unusable from HELM, which is where every commit belongs."""
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    assert _commit(overlay, "first.txt", marked=False).returncode == 0
    before = _commit_count(overlay)
    assert _commit(overlay, "second.txt", marked=False).returncode == 0
    assert _commit_count(overlay) == before + 1


def test_the_refusal_names_what_is_still_allowed(tmp_path):
    """A refusal that does not say what to do instead gets worked around."""
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    _commit(overlay, "first.txt", marked=False)
    result = _commit(overlay, "second.txt", marked=True)
    combined = result.stderr + result.stdout
    assert "FILES" in combined
    assert "HELM" in combined


@pytest.mark.parametrize("value", ["0", "", "false", "no"])
def test_only_the_exact_marker_refuses(tmp_path, value):
    """Fail in the direction that keeps HELM working.

    The marker is set to exactly "1" by the bootstrap. Anything else is not a
    YARD session, and reading a stray value as one would block the operator's
    own commits.
    """
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    (overlay / "f.txt").write_text("x\n", encoding="utf-8")
    env = dict(os.environ, HEADING_OS_YARD=value)
    subprocess.run(["git", "add", "f.txt"], cwd=str(overlay), check=True, env=env)
    result = subprocess.run(
        ["git", "commit", "-m", "x"], cwd=str(overlay),
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_the_installed_hook_is_byte_for_byte_the_tracked_body(tmp_path):
    """One body, in one file. The draft this replaces inlined the hook's text
    into the bootstrap script as a heredoc while also keeping it in a file, so
    there were two copies of one hook waiting to diverge."""
    overlay = _repo(tmp_path)
    assert _install(overlay).returncode == 0
    assert _hook(overlay).read_bytes() == BODY.read_bytes()
