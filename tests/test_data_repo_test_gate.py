"""The data overlay's own tests must gate its push, the way the engine's do.

Measured on 2026-08-20: `.heading-os-data/tests/` sat in no gate at all. The
engine pre-push hook runs the ENGINE suite, and the data overlay's push went out
with `test_gate` unset, so twenty-four admin tests -- the cover on exec
provisioning -- ran only when somebody remembered to type the command.

Two things make this awkward, and both are pinned below. The data repo's
`pre-push` slot is already occupied by git-lfs, and that repo really does track
LFS objects, so a gate that overwrites the hook breaks pushes instead of guarding
them. And an executive's data overlay carries no `tests/` directory at all, so the
gate has to pass on absence rather than fail closed.
"""
import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("push_all", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)

_ih_spec = importlib.util.spec_from_file_location(
    "install_git_hooks", ROOT / "scripts" / "install-git-hooks.py")
install_git_hooks = importlib.util.module_from_spec(_ih_spec)
_ih_spec.loader.exec_module(install_git_hooks)

# One string, for the reason written out in tests/test_install_git_hooks.py: the
# two-part form spells nothing day mode's literal route can match, so the data
# overlay's push gate had zero edges from this file and a change to it selected no
# test at all.
SHIPPED_HOOK = ROOT / ".githooks/pre-push-data"


def _init_repo(tmp_path, name="data") -> Path:
    repo = tmp_path / name
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


# --- the shipped hook -------------------------------------------------------

def test_shipped_data_hook_exists():
    assert SHIPPED_HOOK.is_file()


def test_shipped_data_hook_still_delegates_to_git_lfs():
    """The data repo tracks LFS objects. A gate that drops this breaks pushes.

    Asked of the CODE, not of the file. This read

        assert "git lfs pre-push" in SHIPPED_HOOK.read_text(...)

    and the hook carries a comment block that explains the delegation in those
    exact words, five lines above the line that performs it. Measured
    2026-09-01: replacing `exec git lfs pre-push "$@"` with `exec true "$@"`
    left this assertion GREEN, because the phrase it looks for was still
    sitting in the prose describing the thing that had just been deleted. So
    the one test standing between an LFS-tracking overlay and pushes that
    silently stop uploading content was satisfied by a comment.

    Comment lines are therefore stripped before the search. A shell comment is
    everything from an unquoted `#` to end of line; the hook has no `#` inside a
    string, and if one ever appears this test over-strips and goes red, which is
    the safe direction for a check about LFS delivery.
    """
    body = SHIPPED_HOOK.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "git lfs pre-push" in code, (
        "the hook no longer delegates to git-lfs in any executable line; the "
        "phrase survives only in comments, so an overlay that tracks LFS "
        "objects would push without uploading them:\n" + body
    )


def test_the_gate_does_not_ask_to_write_into_the_overlay_it_guards():
    """The gate ran pytest INSIDE the operator's data, and pytest writes.

    `scripts/utils/overlay_write_guard.py` refuses any write into the overlay
    from an untracked caller. pytest is untracked: it lives in the engine's
    `.venv`. Collecting a test tree makes it write two things into that tree -
    assertion-rewritten `.pyc` files under `__pycache__/`, and its own
    `.pytest_cache/`.

    So this gate passed only while the bytecode cache was both present and
    current. MEASURED 2026-09-01: editing two files under the overlay's
    `tests/admin/` made the very next push fail at COLLECTION with
    `OverlayWriteRefused`, on a repository whose tests all passed. The gate was
    not failing the tests; it could not reach them.

    That failure mode is worse than a flaky gate. A gate that breaks every time
    somebody edits the thing it guards is a gate that teaches its operator to
    reach for `--no-verify`, which is the one habit this repository cannot
    afford.

    Refusing the write is correct and is not what changed. The gate stopped
    asking for it.

    Asked of the CODE line rather than of the file, because the comment block
    above it explains both switches by name, and a `in text` check would be
    satisfied by the explanation of the very thing it is meant to require. Six
    findings in the 2026-08-31 audit were assertions satisfied by the comment
    describing the defect they guarded.
    """
    lines = [
        line.strip() for line in SHIPPED_HOOK.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("if !") and "pytest" in line
    ]
    assert len(lines) == 1, (
        f"expected exactly one pytest invocation line in the shipped data hook, "
        f"found {len(lines)}: {lines}")
    invocation = lines[0]

    assert "PYTHONDONTWRITEBYTECODE=1" in invocation, (
        f"the data-overlay gate runs pytest without PYTHONDONTWRITEBYTECODE, so "
        f"it writes .pyc files into the operator's data and the overlay write "
        f"guard refuses them. The gate then fails at collection on a healthy "
        f"repository. Line: {invocation}")
    assert "-p no:cacheprovider" in invocation, (
        f"the data-overlay gate lets pytest write .pytest_cache/ into the "
        f"operator's data, which the overlay write guard refuses. "
        f"Line: {invocation}")


def test_shipped_data_hook_carries_the_gate_marker():
    assert push_all.DATA_GATE_MARKER in SHIPPED_HOOK.read_text(encoding="utf-8")


def test_shipped_data_hook_passes_when_the_repo_has_no_tests(tmp_path):
    """An exec's data overlay has no tests/. The gate must pass, not fail closed."""
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)

    result = subprocess.run(["bash", str(SHIPPED_HOOK)], cwd=str(repo),
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


# --- the gate BLOCKS, which nothing above ever asked it to do ---------------
#
# Until 2026-09-01 the shipped hook had exactly one behavioural test, the one
# directly above, and it asserts the hook PASSES. Everything else in this file
# reads the hook as TEXT (does it carry the marker, does it mention git-lfs) or
# exercises the installer around it. So the one thing the gate exists to do had
# no case at all, and three separate mutations of the hook left the whole
# 133-test neighbourhood green:
#
#   `exit 1`               -> `exit 0`     failing overlay tests stop blocking
#   `if ! "$PY" -m pytest` -> `if false`   the suite is never run
#   the `-x $ENGINE/.venv` branch removed  the pinned interpreter is never used
#
# That third one is the failure the hook's own comment block describes at
# length: falling through to a bare `python3` runs the overlay's tests under
# none of the pinned dependencies and calls them green. The comment argued the
# case; no test held it.
#
# The fixtures below stub the interpreter rather than run a real pytest, so the
# cases are hermetic: they need no pytest on PATH, no engine venv, and they work
# on a fresh public clone with no data overlay. The stub also RECORDS that it
# ran, which is what makes "the suite is never run" distinguishable from "the
# suite ran and passed".

def _engine_with_stub_interpreter(path: Path, exit_code: int, witness: Path) -> Path:
    """An engine tree whose `.venv/bin/python` is a recording stub.

    A real file with a shebang, not a symlink. It touches `witness` and exits
    with `exit_code`, ignoring its arguments -- so a test can tell whether the
    hook invoked THE STAMPED INTERPRETER, as opposed to invoking nothing or
    falling through to whatever `python3` the machine happens to carry.
    """
    venv_bin = path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "python"
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {witness}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return path


def _overlay_with_tests(tmp_path) -> Path:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_placeholder.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8")
    return repo


def _run_installed_hook(repo: Path):
    return subprocess.run(["bash", str(repo / ".git" / "hooks" / "pre-push")],
                          cwd=str(repo), capture_output=True, text=True)


def test_the_gate_blocks_the_push_when_the_overlay_tests_fail(tmp_path):
    """The whole point of the gate, and it had no case.

    A non-zero suite must end the hook non-zero. `exit 1` -> `exit 0` in the
    shipped hook is a one-character edit that turns the data overlay's push gate
    into a decoration, and it survived every test in the repository.
    """
    repo = _overlay_with_tests(tmp_path)
    witness = tmp_path / "interpreter-ran"
    engine = _engine_with_stub_interpreter(tmp_path / "engine", 1, witness)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK, engine)

    result = _run_installed_hook(repo)

    assert witness.exists(), (
        "the stamped interpreter was never invoked, so the gate ran no tests "
        f"at all; stdout={result.stdout!r} stderr={result.stderr!r}")
    assert result.returncode != 0, (
        "the overlay's test suite failed and the hook still let the push "
        f"through; stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "push blocked" in result.stderr, result.stderr


def test_the_gate_lets_the_push_through_when_the_overlay_tests_pass(tmp_path):
    """The other side of the same branch.

    Without this, a hook hard-wired to `exit 1` would satisfy the test above and
    block every data-overlay push forever. A refusal that fires on the passing
    case is not a gate either.
    """
    repo = _overlay_with_tests(tmp_path)
    witness = tmp_path / "interpreter-ran"
    engine = _engine_with_stub_interpreter(tmp_path / "engine", 0, witness)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK, engine)

    result = _run_installed_hook(repo)

    assert witness.exists(), "the gate did not run the suite it just passed"
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")


def test_the_gate_runs_the_stamped_interpreter_not_a_bare_python3(tmp_path):
    """Named because the hook's own comment says this is the quiet failure.

    A wrong engine path falls through to `python3`, which on a machine with
    pytest on PATH runs the overlay's tests under none of the pinned
    dependencies and reports green. The stub interpreter exits 1, so if the hook
    reaches it the push is blocked; if the hook ignored the stamp and used the
    system python3 the placeholder test would PASS and the push would go
    through. The two outcomes are opposite, which is what makes this readable.
    """
    repo = _overlay_with_tests(tmp_path)
    witness = tmp_path / "interpreter-ran"
    engine = _engine_with_stub_interpreter(tmp_path / "engine", 1, witness)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK, engine)

    result = _run_installed_hook(repo)

    assert witness.exists(), (
        "the hook did not invoke the interpreter stamped in at install time")
    invocation = witness.read_text(encoding="utf-8")
    assert "-m pytest" in invocation, (
        f"the stamped interpreter was invoked, but not to run pytest: "
        f"{invocation!r}")
    assert result.returncode != 0, (
        "the hook ignored the stamped interpreter's verdict, which is what a "
        "silent fall back to a bare python3 looks like from the outside")


def test_a_missing_engine_venv_says_so_rather_than_falling_through_quietly(tmp_path):
    """The documented degradation, held to its documented loudness.

    The hook is allowed to fall back to `python3`; it is not allowed to do it in
    silence, because a green run under an unpinned interpreter is worse than no
    run at all. Only the warning is asserted here: what the fallback
    interpreter then decides is the machine's business, not this gate's.
    """
    repo = _overlay_with_tests(tmp_path)
    engine = tmp_path / "engine-that-has-no-venv"
    engine.mkdir()
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK, engine)

    result = _run_installed_hook(repo)

    assert "NOT the pinned" in result.stderr, (
        f"the gate fell back to an unpinned interpreter without saying so: "
        f"{result.stderr!r}")


# --- the installer ----------------------------------------------------------

def test_check_data_hook_is_false_before_install(tmp_path):
    assert install_git_hooks.check_pre_push_data(_init_repo(tmp_path)) is False


def test_install_data_hook_makes_the_check_pass(tmp_path):
    repo = _init_repo(tmp_path)

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    assert install_git_hooks.check_pre_push_data(repo) is True


def test_installed_data_hook_is_executable(tmp_path):
    repo = _init_repo(tmp_path)

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    mode = (repo / ".git" / "hooks" / "pre-push").stat().st_mode
    assert mode & stat.S_IXUSR


def test_shipped_data_hook_carries_the_engine_root_token(tmp_path):
    """The engine path is a token in the repository, resolved only at install.

    Guessing it as the sibling named `.heading-os` was the previous shape, and it
    failed in the quiet direction: a clone under any other name fell through to a
    bare `python3`, which on a machine with pytest on PATH runs the overlay's
    tests green under none of the pinned dependencies.
    """
    assert install_git_hooks.ENGINE_ROOT_PLACEHOLDER in SHIPPED_HOOK.read_text(
        encoding="utf-8")


def test_installed_data_hook_has_the_real_engine_path_stamped_in(tmp_path):
    repo = _init_repo(tmp_path)
    engine = _make_git_repo(tmp_path / "engine-named-anything-else")

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK, engine)

    body = (repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")
    assert install_git_hooks.ENGINE_ROOT_PLACEHOLDER not in body
    assert str(engine.resolve()) in body


def test_check_fails_when_the_stamped_engine_is_gone(tmp_path):
    """Stamping the path created a staleness the marker cannot see.

    Relocate the workspace and the ENGINE= line points at nothing while the gate
    marker stays exactly where it was, so a check reading only the marker would
    call a gate healthy after it had fallen back to a bare `python3`.
    """
    repo = _init_repo(tmp_path)
    engine = _make_git_repo(tmp_path / "engine-then-moved")
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK, engine)
    assert install_git_hooks.check_pre_push_data(repo) is True

    engine.rename(tmp_path / "engine-somewhere-else")

    assert install_git_hooks.check_pre_push_data(repo) is False


def test_check_fails_on_an_unstamped_hook(tmp_path):
    """A hook copied by hand, token and all, never resolved its engine."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        SHIPPED_HOOK.read_text(encoding="utf-8"), encoding="utf-8")

    assert install_git_hooks.check_pre_push_data(repo) is False


def test_installing_the_data_hook_does_not_lose_lfs(tmp_path):
    """Installing over the stock git-lfs hook must keep LFS delegation."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        '#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")

    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    assert "git lfs pre-push" in (repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")


# --- push-all's refusal predicate ------------------------------------------

def test_gate_predicate_rejects_the_stock_lfs_hook(tmp_path):
    """The stock LFS hook is not a test gate, and must not read as one."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        '#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")

    assert push_all._pre_push_gate_armed(
        repo, marker=push_all.DATA_GATE_MARKER) is False


def test_gate_predicate_accepts_the_installed_data_hook(tmp_path):
    repo = _init_repo(tmp_path)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    assert push_all._pre_push_gate_armed(
        repo, marker=push_all.DATA_GATE_MARKER) is True


def test_engine_marker_remains_the_default(tmp_path):
    """The engine gate keeps working unchanged when no marker is passed."""
    repo = _init_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        "#!/bin/sh\nexec python scripts/run-tests.py\n", encoding="utf-8")

    assert push_all._pre_push_gate_armed(repo) is True


# --- push_repo refuses an ungated data overlay -----------------------------

def _bare_repo_with_remote(tmp_path):
    """A real repo on a real (local) remote, so push_repo gets past git plumbing."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "data"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                ["config", "user.name", "t"], ["remote", "add", "origin", str(remote)]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def test_push_repo_refuses_a_data_overlay_with_no_gate(tmp_path):
    repo = _bare_repo_with_remote(tmp_path)
    (repo / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "hooks" / "pre-push").write_text(
        '#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")

    with pytest.raises(push_all.RepoNotPushable) as exc:
        push_all.push_repo("DATA", repo, "m", False, True, {},
                           test_gate=True, gate_marker=push_all.DATA_GATE_MARKER)

    assert "install-git-hooks.py" in str(exc.value)


def test_push_repo_allows_a_data_overlay_once_gated(tmp_path):
    repo = _bare_repo_with_remote(tmp_path)
    install_git_hooks.install_pre_push_data(repo, SHIPPED_HOOK)

    push_all.push_repo("DATA", repo, "m", False, True, {},
                       test_gate=True, gate_marker=push_all.DATA_GATE_MARKER)


# --- which repo the installer should gate ----------------------------------

def _make_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path


def test_installer_gates_a_separate_data_repo(tmp_path):
    engine = _make_git_repo(tmp_path / ".heading-os")
    data = _make_git_repo(tmp_path / ".heading-os-data")

    assert install_git_hooks.data_repo_to_gate(data, engine) == data


def test_installer_skips_when_data_root_is_the_engine(tmp_path):
    """Pre-cutover single-repo mode: one repo, already gated as the engine."""
    engine = _make_git_repo(tmp_path / ".heading-os")

    assert install_git_hooks.data_repo_to_gate(engine, engine) is None


def test_installer_skips_a_data_root_that_is_not_a_git_repo(tmp_path):
    """Demo mode resolves the data root to the bundled examples/ tree."""
    engine = _make_git_repo(tmp_path / ".heading-os")
    examples = tmp_path / ".heading-os" / "examples"
    examples.mkdir(parents=True)

    assert install_git_hooks.data_repo_to_gate(examples, engine) is None


def test_installer_skips_a_missing_data_root(tmp_path):
    engine = _make_git_repo(tmp_path / ".heading-os")

    assert install_git_hooks.data_repo_to_gate(tmp_path / "nope", engine) is None
