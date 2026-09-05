"""install-git-hooks.py installs and verifies the pre-push gate."""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("install_git_hooks", ROOT / "scripts" / "install-git-hooks.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# `ROOT / ".githooks/pre-push"`, ONE string, not `ROOT / ".githooks" / "pre-push"`.
# This is not style. `scripts/utils/day_mode.py` finds the files a test drives as
# a subprocess by reading the test's string constants, and neither fragment of the
# two-part form is a constant it can recognise: `.githooks` is not an extension it
# accepts and `pre-push` has no separator and no dot. MEASURED 2026-09-04: every
# reference to either hook in this tree used the two-part form, so `.githooks/pre-push`
# and `.githooks/pre-push-data` had ZERO importers and ZERO literal users, and a
# change to the engine's own push gate selected no test for it. The blind-spot guard
# could not report it either, because `blind_files()` walks `.py` files only and a
# hook is shell. Joining the path in one literal is what makes the edge visible.


def test_install_writes_pre_push(tmp_path):
    # a throwaway git repo
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    src = ROOT / ".githooks/pre-push"
    mod.install_pre_push(tmp_path, src)
    hook = tmp_path / ".git" / "hooks" / "pre-push"
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111  # executable
    assert "run-tests.py" in hook.read_text(encoding="utf-8")


def test_check_detects_missing(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert mod.check_pre_push(tmp_path) is False
    mod.install_pre_push(tmp_path, ROOT / ".githooks/pre-push")
    assert mod.check_pre_push(tmp_path) is True


def _hook_body_without_comments() -> str:
    """The hook's executable lines only.

    Comment lines are dropped because the header discusses `git lfs pre-push`
    and `run-tests.py` by name, and an assertion that reads prose is an
    assertion about prose - cross-shard finding 19, and the shape that made two
    files in this campaign green over a comment.
    """
    return "\n".join(
        line for line in
        (ROOT / ".githooks/pre-push").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_engine_hook_still_delegates_to_git_lfs():
    """The engine tracks LFS objects, and this hook occupies git-lfs's slot.

    The mirror of tests/test_data_repo_test_gate.py::
    test_shipped_data_hook_still_delegates_to_git_lfs. The data overlay got that
    guard on 2026-08-20 while the engine hook, which had dropped the same
    delegation on 2026-06-29, got none - so a newly added .png or .pdf would push
    as a pointer with no object behind it and only fail on someone else's clone.

    Read off the comment-stripped body since 2026-09-01. The header sentence
    "it hands off to `git lfs pre-push` at the end" satisfied the old substring
    search on its own, so replacing the real `exec git lfs pre-push "$@"` with
    `exec true "$@"` left this assertion green.
    """
    assert "git lfs pre-push" in _hook_body_without_comments()


def test_engine_hook_still_runs_the_suite_before_handing_off():
    """The hand-off must come AFTER the gate, never instead of it.

    `exec`ing git-lfs on line one would satisfy the test above and skip every
    test in the repository, so pin the order rather than the presence.

    Comment lines are stripped first. Both strings are discussed in the header,
    and an assertion that reads prose is an assertion about prose.
    """
    body = _hook_body_without_comments()
    assert body.index("run-tests.py") < body.index("git lfs pre-push")


# ============================================================
# The hook EXECUTED, not the hook read
# ============================================================
#
# Everything above this line reads the hook as text. MEASURED 2026-09-01, three
# mutations of `.githooks/pre-push` each left 176 tests across this file and
# seven neighbours green while the engine's push gate stopped refusing:
#
#   | mutation                                    | failing suite -> |
#   |---------------------------------------------|------------------|
#   | `set -euo pipefail` -> `set -uo pipefail`   | exit 0           |
#   | `"$PY" .../run-tests.py` -> `... \|\| true` | exit 0           |
#   | `"$PY" .../run-tests.py` -> `true ...`      | exit 0           |
#
# The third keeps the string `run-tests.py` in the file, so the order assertion
# above stays green over a hook that never runs the suite at all.
#
# This is the ENGINE half of the finding the coordinator recorded against
# `.githooks/pre-push-data` on the same day: a shipped hook that printed its
# refusal and returned success. The data overlay's hook got behavioural tests
# then; the engine's, guarding the PUBLIC repository, still had none.
#
# Hermetic: a scratch repository, a recording stub interpreter at the path the
# hook prefers, and a bare remote so the git-lfs hand-off has a real remote name
# to resolve. Nothing touches this repository's own `.git/hooks`.


def _armed_repo(tmp_path: Path, suite_exit: int) -> tuple[Path, Path]:
    """A scratch repo carrying the shipped hook and a stub suite. -> (repo, record)."""
    repo = tmp_path / "engine"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)],
                   check=True)

    record = tmp_path / "suite-ran"
    venv_python = repo / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{record}"\nexit {suite_exit}\n',
        encoding="utf-8")
    venv_python.chmod(0o755)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "run-tests.py").write_text("raise SystemExit(0)\n",
                                                   encoding="utf-8")

    mod.install_pre_push(repo, ROOT / ".githooks/pre-push")
    return repo, record


def _push(repo: Path) -> subprocess.CompletedProcess:
    """Invoke the installed hook the way git does: `pre-push <remote> <url>`."""
    return subprocess.run(
        ["bash", str(repo / ".git" / "hooks" / "pre-push"), "origin",
         str(repo.parent / "remote.git")],
        cwd=str(repo), input="", capture_output=True, text=True,
    )


def test_the_installed_engine_hook_refuses_the_push_when_the_suite_fails(tmp_path):
    """The whole point of the gate, asserted on the EXIT STATUS.

    Not on the text it prints: `.githooks/pre-push-data` printed "push blocked"
    and returned 0, so a human watching the terminal saw a refusal that did not
    happen.
    """
    repo, record = _armed_repo(tmp_path, suite_exit=1)
    result = _push(repo)
    assert record.is_file(), (
        "the hook never invoked the suite at all, so the exit status below "
        "would say nothing about whether a failing suite blocks a push")
    assert "run-tests.py" in record.read_text(encoding="utf-8")
    assert result.returncode != 0, (
        f"the engine push gate let a push through over a FAILING test suite "
        f"(rc={result.returncode}); stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")


def test_the_installed_engine_hook_allows_the_push_when_the_suite_passes(tmp_path):
    """The anchor. A gate that refuses everything is a gate that gets removed."""
    repo, record = _armed_repo(tmp_path, suite_exit=0)
    result = _push(repo)
    assert record.is_file(), "the suite was not run on the passing path either"
    assert result.returncode == 0, (
        f"the gate refused a push over a PASSING suite: rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}")


def test_the_engine_hook_runs_the_repos_own_venv_interpreter(tmp_path):
    """Which interpreter ran the suite is the difference between the pinned
    toolchain and whatever `python3` resolves to. The stub sits at
    `<repo>/.venv/bin/python` and nowhere else, so its record existing is the
    proof that branch was taken.

    The record holds one argv entry per line. It was asserted with `endswith` on
    the whole blob until 2026-09-04, which read the LAST argument rather than the
    script, so the gate gaining a flag failed a test about which interpreter ran.
    The flag is now asserted on its own line, because the hook passing
    `--pre-push` is the thing that makes a push run the tests it can reach
    instead of all of them.
    """
    repo, record = _armed_repo(tmp_path, suite_exit=0)
    _push(repo)
    argv = record.read_text(encoding="utf-8").split()
    assert argv, "the venv interpreter recorded no argv at all"
    assert argv[0].endswith("scripts/run-tests.py"), argv
    assert "--pre-push" in argv, (
        f"the installed hook stopped asking for the narrowed mode, so every "
        f"push runs the whole suite again: {argv}")


def test_the_engine_hook_says_so_out_loud_when_there_is_no_venv(tmp_path):
    """The fallback is loud by design: a bare `python3` holds none of the pinned
    dependencies and can run the suite green under a toolchain nobody chose.
    Silence there is what the engine hook did until 2026-08-23."""
    repo, _record = _armed_repo(tmp_path, suite_exit=0)
    shutil.rmtree(repo / ".venv")
    result = _push(repo)
    assert "NOT the pinned" in result.stderr, (
        f"the hook fell back to a bare python3 without saying so: "
        f"{result.stderr!r}")
