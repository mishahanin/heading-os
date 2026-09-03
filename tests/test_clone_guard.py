"""`scripts/utils/clone_guard.py` — the HELM/YARD predicate.

Every other guard in the HELM/YARD design calls this one, so a wrong answer here
is a wrong answer everywhere at once. Both directions are asserted for each
entry point, and the refusal path is driven through a real subprocess so the
assertion is on the observable consequence (exit status 2, a message naming
HELM) rather than on a restated copy of the guard's own condition.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.clone_guard import (  # noqa: E402
    CloneGuardError,
    is_main_clone,
    main_clone_path,
)

ROOT = Path(__file__).resolve().parent.parent

# ROOT is the checkout the suite was LAUNCHED from, which is a YARD worktree
# whenever engine work is in progress. Every assertion below that needs a MAIN
# CLONE therefore takes `armed_main_clone`, and every one that needs HELM's path
# as data takes `helm_root`. MEASURED 2026-09-03: eleven of these read ROOT as
# the main clone and inverted here, while the same commit is green in HELM.


# ============================================================
# is_main_clone
# ============================================================

def test_the_main_clone_answers_true(armed_main_clone):
    # A real main clone carrying this working tree, because ROOT is a worktree
    # here and `is_main_clone(ROOT)` is legitimately False.
    assert is_main_clone(armed_main_clone) is True


def test_a_worktree_answers_false(temporary_worktree):
    assert is_main_clone(temporary_worktree) is False


def test_the_fast_path_is_the_shape_of_dot_git(armed_main_clone,
                                               temporary_worktree):
    """The measurement the fast path rests on, asserted rather than assumed.

    If git ever stopped writing a `gitdir:` FILE into a worktree, the fast path
    would fall through to `rev-parse` and still be correct, but this test would
    go red first and say why.

    The `.git`-is-a-directory half is asked of `armed_main_clone`: ROOT's own
    `.git` is a FILE here, which is the very shape the other half asserts.
    """
    assert (armed_main_clone / ".git").is_dir()
    assert (temporary_worktree / ".git").is_file()


def test_a_subdirectory_falls_through_to_git_and_still_answers(
    armed_main_clone, temporary_worktree,
):
    """No `.git` entry at all, so the authority path runs. Both directions."""
    # The True side needs a checkout that really is a main clone; ROOT is not.
    assert is_main_clone(armed_main_clone / "scripts") is True
    assert is_main_clone(temporary_worktree / "scripts") is False


def test_a_directory_outside_any_repository_raises(tmp_path):
    """"I cannot tell" reaches the caller as an exception, never as False.

    A False here would read as "not a worktree, carry on" and open the guard.
    """
    with pytest.raises(CloneGuardError):
        is_main_clone(tmp_path)


def test_a_path_that_is_not_a_directory_raises(tmp_path):
    victim = tmp_path / "not-a-dir"
    victim.write_text("x", encoding="utf-8")
    with pytest.raises(CloneGuardError):
        is_main_clone(victim)


def test_a_string_argument_is_accepted(armed_main_clone, temporary_worktree):
    # Same reason as above: the True side is asked of a real main clone.
    assert is_main_clone(str(armed_main_clone)) is True
    assert is_main_clone(str(temporary_worktree)) is False


# ============================================================
# main_clone_path
# ============================================================

def test_main_clone_path_from_the_main_clone(armed_main_clone):
    # A main clone resolves to ITSELF. Asked of `armed_main_clone`, so an
    # implementation that returned some fixed path would still go red here.
    assert main_clone_path(armed_main_clone) == armed_main_clone


def test_main_clone_path_from_a_worktree_points_back_at_its_main_clone(
    temporary_worktree, worktree_origin,
):
    """The property the write guard needs: a YARD can name its main clone
    without being told where it is, and without any path arithmetic.

    This compared against `helm_root` until 2026-09-03, because the fixture
    registered its worktrees in the operator's HELM. It no longer does -- a
    mutation against the cleanup code reached the operator's live YARDs through
    exactly that coupling -- so the fixture clones first and the expected answer
    is that clone. The property is unchanged; only the address moved.
    """
    assert main_clone_path(temporary_worktree) == worktree_origin


def test_main_clone_path_from_a_subdirectory_of_a_worktree(temporary_worktree,
                                                           worktree_origin):
    # Same correction as the test above: the worktree's own main clone, never
    # the checkout the suite was launched from.
    assert main_clone_path(temporary_worktree / "scripts") == worktree_origin


def test_main_clone_path_raises_outside_a_repository(tmp_path):
    with pytest.raises(CloneGuardError):
        main_clone_path(tmp_path)


# ============================================================
# require_main_clone — driven through a real process
# ============================================================

_HARNESS = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from scripts.utils.clone_guard import require_main_clone
    require_main_clone(sys.argv[2])
    print("REACHED THE BODY")
    """
)


def _drive(cwd: Path, guarded_script: Path):
    """Run require_main_clone in a child and report what the caller would see.

    The module is always imported from HELM, and that is the point rather than a
    convenience: the guard's verdict must come from the `script_path` it is
    handed and from the cwd, never from where the module itself happens to sit.
    A YARD script importing HELM's copy of `clone_guard` is the real scenario
    the `$0` contract exists for.
    """
    return subprocess.run(
        [sys.executable, "-c", _HARNESS, str(ROOT), str(guarded_script)],
        cwd=str(cwd), capture_output=True, text=True,
    )


def test_from_helm_the_guarded_body_runs(helm_root):
    # Both the cwd and the script must be HELM's for the permitted direction.
    # The harness never EXECUTES the named script, so this path is pure data.
    result = _drive(helm_root, helm_root / "scripts" / "push-all.py")
    assert result.returncode == 0, result.stderr
    assert "REACHED THE BODY" in result.stdout


def test_from_a_worktree_it_exits_two_and_names_its_main_clone(
        temporary_worktree, worktree_origin):
    script = temporary_worktree / "scripts" / "push-all.py"
    result = _drive(temporary_worktree, script)
    assert result.returncode == 2
    assert "REACHED THE BODY" not in result.stdout
    assert "HELM" in result.stderr
    # The path the message names is the worktree's real main clone, never the
    # checkout the suite was launched from.
    assert str(worktree_origin) in result.stderr


def test_the_modules_own_location_does_not_decide_the_verdict(
    temporary_worktree,
):
    """HELM's copy of the module, asked about a YARD script, still refuses.

    This is the defect Review-01 item 8 named: a predicate that resolved
    `script_root` from `Path(__file__)` of the module would answer for HELM
    here and let a daemon installer through.
    """
    result = _drive(temporary_worktree,
                    temporary_worktree / "scripts" / "install-daemon-service.sh")
    assert result.returncode == 2


def test_a_yard_script_launched_by_absolute_path_from_helm_is_refused(
    temporary_worktree, helm_root, worktree_origin,
):
    """The hole that `__file__` left open, closed by passing `$0`.

    `bash <YARD>/scripts/install-memory-index-timer.sh` typed in a HELM shell
    has cwd = HELM. A predicate that only read the cwd would answer "main
    clone", and systemd would receive a unit pointing at a checkout that is
    removed two days later. The script's OWN location is the second thing
    checked, which is what refuses this.
    """
    yard_script = temporary_worktree / "scripts" / "push-all.py"
    # The cwd genuinely has to be HELM for "typed in a HELM shell" to be the
    # scenario under test; ROOT is a YARD here, which would be refused by the
    # cwd check instead and never reach the one this case is about.
    result = _drive(helm_root, yard_script)
    assert result.returncode == 2
    assert "Detected script:" in result.stderr


def test_a_helm_script_launched_from_a_worktree_cwd_is_refused(
    temporary_worktree, helm_root,
):
    """The mirror case: the script is HELM's, but the shell sits in a YARD."""
    # Only HELM's own copy of the script isolates the cwd check; a script under
    # ROOT is a YARD script and is refused one check earlier.
    result = _drive(temporary_worktree, helm_root / "scripts" / "push-all.py")
    assert result.returncode == 2
    assert "Detected cwd:" in result.stderr


def test_an_undeterminable_script_location_refuses_rather_than_passing(tmp_path):
    """Fail closed on the authoritative signal.

    The script is not inside any repository, so git cannot say whether its
    checkout is a worktree. That is the answer the guard exists to have, and it
    refuses rather than guessing.
    """
    fake = tmp_path / "scripts" / "thing.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("", encoding="utf-8")
    result = _drive(tmp_path, fake)
    assert result.returncode == 2
    assert "cannot determine clone type" in result.stderr
    assert "REACHED THE BODY" not in result.stdout


def test_a_cwd_outside_any_repository_is_skipped_not_refused(tmp_path,
                                                             helm_root):
    """The asymmetry between the two checks, asserted so it cannot drift.

    A shell in `/tmp` is not a YARD, and refusing it would break every honest
    caller that runs from a temporary directory, this suite included, without
    closing any real path. The script's own location is still checked first, so
    nothing is weakened: the next test proves a YARD script is still refused
    from exactly this cwd.
    """
    # HELM's own script, because the permitted direction is what is being
    # asserted and a script under ROOT is a YARD script here.
    result = _drive(tmp_path, helm_root / "scripts" / "push-all.py")
    assert result.returncode == 0, result.stderr
    assert "REACHED THE BODY" in result.stdout


def test_a_yard_script_is_still_refused_from_a_non_repository_cwd(
    tmp_path, temporary_worktree,
):
    """The pair to the test above: the skip does not open a way through."""
    result = _drive(tmp_path, temporary_worktree / "scripts" / "push-all.py")
    assert result.returncode == 2
    assert "Detected script:" in result.stderr
