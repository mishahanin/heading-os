"""A YARD could reach into ANOTHER YARD, and the guard said nothing.

`check_yard_write_guard` asked one question: "does this land inside HELM?".
Everything that was not HELM was somebody else's problem, and two of the
somebodies are not HELM — the operator's other tasks, each in its own worktree
of this same repository, each holding unmerged work and very often a live
session.

MEASURED 2026-09-04 from the live YARD at `.yard/.heading-os/yard-isolation`, by
calling `check_yard_write_guard` directly with the real layout on disk. Four of
the six probes came back permitted:

    write into HELM                     BLOCKED
    rm -rf <HELM>/.git/worktrees        BLOCKED
    write into a NEIGHBOURING yard      ALLOWED      <- hole
    rm -rf a neighbouring yard          ALLOWED      <- hole
    git branch -D <another task's>      ALLOWED      <- hole
    git worktree prune                  ALLOWED      <- hole

The last two need no path at all to do their damage, which is why no path check
could have caught them: the refs and the worktree registry live in HELM's `.git`
and every worktree is a client of that one directory. `git branch` and
`git worktree` were also both on the read-only verb list, so they were being
classified as reads.

The model this file pins, and it is the whole of it:

    own checkout      read and write
    HELM              read yes, write never
    data overlay      files yes, git no
    another YARD      nothing at all

Every refusal below is paired with the case that must still pass, because a
guard that refuses everything satisfies each refusal on its own and makes a
worktree unusable within the hour. The paired direction for the refusals that
are about WHO IS ASKING is sent again from a real main clone, where this wall
must stay silent.

Driven through the REAL dispatcher as a REAL process, in REAL worktrees, cut
from a THROWAWAY clone. Never from the operator's repository: a fixture that can
name a live YARD is half of what this file is about, and `temporary_worktree` in
`tests/conftest.py` carries the incident that established it.

TWO PROPERTIES OF THE FIXTURES HERE, both measured rather than chosen:

  * ONE clone for the whole module. Built per test, these cases spawned about
    530 git subprocesses. That is 66 clones of this repository to answer 66
    questions about one function, and it took 2.5 minutes on four workers.
  * EVERY subprocess gets `HEADING_OS_DATA` pinned at a scratch directory.
    `tests/conftest.py` counts children that could still resolve the operator's
    live overlay and ratchets against `config/overlay-reachability-baseline.json`,
    which only ever shrinks. MEASURED 2026-09-04: the first version of this file
    took the full-suite figure from 9659 to 10191 and set `session.exitstatus`
    to 1 with every test passing. Pinned, this file contributes none.
"""

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_REL = Path(".claude") / "hooks" / "_dispatch.py"


# ============================================================
# One bench: a main clone, a yard, a neighbour, an overlay
# ============================================================

def _env(scratch: Path) -> dict:
    """The environment every child of this file gets.

    `HEADING_OS_DATA` at a scratch directory, so no child can resolve the
    operator's real overlay. `CLAUDE_PROJECT_DIR` removed, because nothing in
    this design may depend on it and a test that inherits it is not testing the
    design.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["HEADING_OS_DATA"] = str(scratch)
    return env


def _git(args: list[str], cwd: Path, scratch: Path):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=_env(scratch), timeout=300)


def _copy_working_tree(target: Path, scratch: Path) -> None:
    """Put THIS checkout's uncommitted state into `target`.

    `git clone` and `git worktree add` check out a COMMIT, so a guard that is
    not committed yet would be absent from the copy under test and every case
    here would fail for the wrong reason. Deletions are skipped: copying cannot
    express a removal, and no case here turns on a deleted file.
    """
    listing = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=str(ROOT), capture_output=True, check=True, env=_env(scratch))
    entries = listing.stdout.decode("utf-8", "surrogateescape").split("\0")
    for entry in entries:
        if len(entry) < 4:
            continue
        status, rel = entry[:2], entry[3:]
        if "D" in status:
            continue
        source = ROOT / rel
        if not source.is_file():
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        destination.chmod(source.stat().st_mode)


def _registration_of(checkout: Path) -> Path | None:
    """The shared-git-dir entry `checkout` is registered under, or None."""
    try:
        pointer = (checkout / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    return Path(pointer.split(":", 1)[1].strip())


def _add_worktree(bench, path: Path, branch: str | None = None) -> Path:
    """One worktree of the bench's clone, with this checkout's tree in it."""
    spec = ["-b", branch, str(path), "HEAD"] if branch else ["--detach",
                                                             str(path), "HEAD"]
    created = _git(["worktree", "add", *spec], bench.helm, bench.scratch)
    if created.returncode != 0:
        pytest.skip(f"git worktree add failed: {created.stderr.strip()}")
    _copy_working_tree(path, bench.scratch)
    return path


def _drop_worktree(bench, path: Path) -> None:
    """Remove ONE worktree and ONE registration, this file's own.

    NEVER `git worktree prune`, which is the very operation the cases below
    refuse: it reaches every entry in the shared registry, including those of
    processes holding one open right now. The clone is disposable, so this is
    belt over braces, and it is written this way because the technique is what
    the file is arguing for.
    """
    registration = _registration_of(path)
    _git(["worktree", "remove", "--force", str(path)], bench.helm, bench.scratch)
    if registration is not None and registration.is_dir():
        shutil.rmtree(registration, ignore_errors=True)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    """A whole HELM/YARD layout, built once for this module.

        <base>/origin              a real MAIN clone, carrying this working tree
        <base>/the-yard            the worktree under test, detached
        <base>/a-neighbour         another task's worktree
        <base>/.heading-os-data    where the guard looks for the data overlay
        <base>/scratch-data-root   what every child gets pinned to

    `origin` is a main clone (its `.git` is a directory), so it doubles as the
    "sent from HELM" direction without a second clone. The overlay sits beside
    it because `_yard_data_roots` resolves `<HELM>/../.heading-os-data`; with
    nothing there the overlay branch of the guard sees no roots at all and every
    overlay case would pass while asserting nothing.
    """
    base = tmp_path_factory.mktemp("yard-isolation")
    scratch = base / "scratch-data-root"
    scratch.mkdir()
    helm = base / "origin"
    cloned = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(helm)],
        capture_output=True, text=True, env=_env(scratch), timeout=300)
    if cloned.returncode != 0:
        pytest.skip(f"could not clone a bench: {cloned.stderr.strip()}")

    overlay = base / ".heading-os-data"
    (overlay / "outputs" / "operations").mkdir(parents=True)
    (overlay / "CLAUDE.operational.md").write_text("bench\n", encoding="utf-8")

    made = types.SimpleNamespace(base=base, helm=helm, scratch=scratch,
                                 overlay=overlay, yard=None, neighbour=None)
    _copy_working_tree(helm, scratch)
    made.yard = _add_worktree(made, base / "the-yard")
    made.neighbour = _add_worktree(made, base / "a-neighbour")
    yield made
    for path in (made.neighbour, made.yard):
        _drop_worktree(made, path)


@pytest.fixture
def disposable_neighbour(bench, tmp_path):
    """A neighbour this test may damage. Its own, so the module's is untouched."""
    path = _add_worktree(bench, tmp_path / "a-disposable-neighbour")
    yield path
    _drop_worktree(bench, path)


@pytest.fixture
def yard_on_a_branch(bench, tmp_path):
    """A worktree standing on a branch, and the branch's name.

    The module's yard is DETACHED on purpose, so it has no own branch and the
    own-branch exception cannot apply to it. The cases that assert the exception
    need a worktree that is actually standing somewhere, and they must not put
    the shared one on a branch to get it.

    The name is per-test. A fixed one collided on the second use — removing a
    worktree does not remove its branch — and `git worktree add` failed, which
    this fixture turns into a SKIP. MEASURED 2026-09-04: one of the two cases
    that assert the own-branch exception was skipping, and the file reported
    "67 passed, 1 skipped" rather than a failure.
    """
    branch = f"a-task-branch-{tmp_path.name}"
    path = _add_worktree(bench, tmp_path / "a-branched-yard", branch=branch)
    yield path, branch
    _drop_worktree(bench, path)


# ============================================================
# Driving the wall
# ============================================================

def _run(bench, checkout: Path, payload: dict) -> dict | None:
    """Feed one payload to the dispatcher in `checkout`. Return its decision."""
    result = subprocess.run(
        [sys.executable, str(checkout / DISPATCH_REL)],
        input=json.dumps(payload), cwd=str(checkout), capture_output=True,
        text=True, env=_env(bench.scratch), timeout=120,
    )
    assert result.returncode == 0, (
        f"dispatcher exited {result.returncode}: {result.stderr}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _call_the_guard_directly(bench, checkout: Path, payload: dict) -> dict | None:
    """Call `check_yard_write_guard`, stepping around the CHECKS order.

    Needed only where an EARLIER wall legitimately answers first:
    `check_release_gate` refuses any `git commit` the operator did not ask for
    in the current turn, so end to end it answers before this one, and
    asserting "something said no" there would pass with this wall deleted. The
    case that matters is the one where the operator DID ask: that gate opens,
    and this wall must still refuse. The real function runs in the real
    worktree either way; only the ordering is stepped around.
    """
    harness = (
        "import json, sys, importlib.util\n"
        "spec = importlib.util.spec_from_file_location('d', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(json.dumps(mod.check_yard_write_guard(json.loads(sys.argv[2]))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", harness, str(checkout / DISPATCH_REL),
         json.dumps(payload)],
        cwd=str(checkout), capture_output=True, text=True,
        env=_env(bench.scratch), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _reason(decision: dict | None) -> str:
    if not decision:
        return ""
    return decision.get("hookSpecificOutput", {}).get(
        "permissionDecisionReason", "")


def _denied_by_this_wall(decision: dict | None) -> bool:
    """Denied BY THIS WALL, not merely denied.

    Ten other checks sit in the same dispatcher and several refuse git commands
    for their own reasons. Asserting "something said no" would pass with this
    wall deleted.
    """
    if not decision:
        return False
    if decision.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
        return False
    reason = _reason(decision)
    return "YARD isolation guard" in reason or "YARD write guard" in reason


def _write(path, cwd) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)},
            "cwd": str(cwd)}


def _bash(command, cwd) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "cwd": str(cwd)}


# ============================================================
# A neighbouring YARD: nothing at all
# ============================================================

def test_a_write_into_a_neighbouring_yard_is_refused(bench):
    decision = _run(bench, bench.yard,
                    _write(bench.neighbour / "scripts" / "x.py", bench.yard))
    assert _denied_by_this_wall(decision)
    assert "ANOTHER YARD" in _reason(decision)


def test_a_relative_write_that_resolves_into_a_neighbour_is_refused(bench):
    """The path is relative and innocent; the cwd it is joined to is not."""
    decision = _run(bench, bench.yard, _write("scripts/x.py", bench.neighbour))
    assert _denied_by_this_wall(decision)


@pytest.mark.parametrize("command", [
    "rm -rf {yard}",
    "rm -rf {yard}/scripts",
    "echo x > {yard}/scripts/x.py",
    "cp /tmp/x {yard}/scripts/x.py",
    "cd {yard} && rm -rf scripts",
    "cat /tmp/x; rm -rf {yard}",
])
def test_a_bash_command_reaching_into_a_neighbour_is_refused(bench, command):
    """Nothing is run: the wall answers on the payload, so these stay strings."""
    decision = _run(bench, bench.yard,
                    _bash(command.format(yard=bench.neighbour), bench.yard))
    assert _denied_by_this_wall(decision), command


@pytest.mark.parametrize("command", [
    "cat {yard}/CLAUDE.md",
    "ls {yard}/scripts",
    "rg needle {yard}",
    "cd {yard} && ls",
    "git -C {yard} log --oneline -5",
    "git -C {yard} status",
])
def test_even_READING_a_neighbour_is_refused(bench, command):
    """The one place this guard refuses a read, and the asymmetry is the model.

    HELM is readable because a worktree cannot function without its objects and
    its refs, and the data overlay is readable because that is where the
    operator's material is. A neighbour is neither. It is a different task's
    half-finished work, and a YARD is not supposed to know it exists.
    """
    decision = _run(bench, bench.yard,
                    _bash(command.format(yard=bench.neighbour), bench.yard))
    assert _denied_by_this_wall(decision), command


@pytest.mark.parametrize("tool_name,key", [
    ("Read", "file_path"),
    ("Grep", "path"),
    ("Glob", "path"),
])
def test_the_reading_tools_are_refused_a_neighbour(bench, tool_name, key):
    decision = _run(bench, bench.yard, {
        "tool_name": tool_name,
        "tool_input": {key: str(bench.neighbour / "CLAUDE.md")},
        "cwd": str(bench.yard),
    })
    assert _denied_by_this_wall(decision), tool_name


def test_a_command_naming_the_directory_the_yards_live_in_is_refused(bench):
    """`rm -rf <the directory they all sit in>` never spells a neighbour's name.

    It reaches one anyway. A check that only asks "does this text contain a
    neighbour's path" answers no here, so the containing direction is asked as
    well: does a neighbour sit UNDER a path this command names?
    """
    decision = _run(bench, bench.yard, _bash(f"rm -rf {bench.base}", bench.yard))
    assert _denied_by_this_wall(decision)


def test_listing_that_same_directory_is_permitted(bench):
    """The paired direction for the case above, and why it is narrow.

    The containing check runs only on links that are not reads. `ls` of a
    directory that happens to hold worktrees reveals their names and nothing
    else, and refusing it would refuse `ls ~`.
    """
    assert not _denied_by_this_wall(
        _run(bench, bench.yard, _bash(f"ls {bench.base}", bench.yard)))


def test_a_path_operator_in_a_heredoc_is_not_read_as_reaching_a_neighbour(bench):
    """A lone `/` is a character, not a claim about a worktree.

    THE DEFECT, MEASURED 2026-09-05 in `yard-day-mode-routes`. The containing
    check asked, of every word starting with `/`, whether a neighbour sat under
    it. `/` satisfies that for every neighbour there is, so any non-read-only
    command carrying a bare `/` was refused, and the refusal named a worktree
    the command had never mentioned. Two `python - <<EOF` measurement harnesses
    were blocked over the pathlib operator in `SCRATCH / rel` before anyone
    understood what the guard was objecting to.

    `/` is also an ancestor of HELM and of the data overlay, both of which a
    YARD may reach, so it never distinguished a neighbour from a permitted
    directory. It is now excluded, and `rm -rf /` keeps its own refusal below.
    """
    command = (
        "python3 - <<'EOF'\n"
        "from pathlib import Path\n"
        "SCRATCH = Path('/tmp/scratch')\n"
        "print(SCRATCH / 'x.py')\n"
        "EOF"
    )
    assert not _denied_by_this_wall(_run(bench, bench.yard, _bash(command, bench.yard)))


def test_a_heredoc_that_names_a_neighbour_is_still_refused(bench):
    """The paired direction, and the reason the body is still read.

    Excluding `/` narrows one signal. It must not narrow the one that matters:
    the guard reads the WHOLE command, heredoc body included, because a script
    fed on stdin that opens another task's file is exactly the shape this wall
    exists for.
    """
    command = (
        "python3 - <<'EOF'\n"
        f"print(open('{bench.neighbour}/CLAUDE.md').read())\n"
        "EOF"
    )
    assert _denied_by_this_wall(_run(bench, bench.yard, _bash(command, bench.yard)))


def test_destroying_the_filesystem_root_is_still_refused(bench):
    """What `/` cost when it stopped being evidence, kept rather than lost.

    `rm -rf /` reaches every neighbour without naming one. It is refused by its
    own branch now, so the reason can say what the command actually does instead
    of claiming it touched one particular worktree.
    """
    decision = _run(bench, bench.yard, _bash("rm -rf /", bench.yard))
    assert _denied_by_this_wall(decision)
    assert "filesystem root" in _reason(decision)


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -rf '/'",
    "rm -fr //",
    "mv / /elsewhere",
    "chmod -R 777 /",
])
def test_every_destructive_shape_aimed_at_the_root_is_refused(bench, command):
    assert _denied_by_this_wall(_run(bench, bench.yard, _bash(command, bench.yard)))


def test_a_neighbour_is_still_recognised_after_its_registration_is_gone(
    bench, disposable_neighbour,
):
    """The registry is the first signal, and the registry can be the casualty.

    On 2026-09-03 a fixture's `shutil.rmtree` emptied `<HELM>/.git/worktrees`
    and every live YARD lost its entry. A guard that enumerates neighbours from
    that directory alone goes blind at exactly the moment the damage is being
    done, so a second signal is read from the neighbour's own `.git` file, which
    still points into this repository's registry whether or not the entry it
    names survives.
    """
    registration = _registration_of(disposable_neighbour)
    assert registration is not None and registration.is_dir(), (
        "the neighbour must be registered before the registration is removed, "
        "or this case proves nothing")
    shutil.rmtree(registration)

    decision = _run(bench, bench.yard,
                    _write(disposable_neighbour / "scripts" / "x.py", bench.yard))
    assert _denied_by_this_wall(decision)


# ============================================================
# The shared directory: branches and the worktree registry
# ============================================================

@pytest.mark.parametrize("command", [
    "git branch -D someone-elses-branch",
    "git branch -d someone-elses-branch",
    "git branch --delete someone-elses-branch",
    "git branch -M someone-elses-branch renamed",
    "git branch a-brand-new-branch",
    "git branch --set-upstream-to=origin/main someone-elses-branch",
])
def test_editing_another_branch_of_the_shared_repository_is_refused(bench,
                                                                    command):
    """No path is named and none is needed: one set of refs serves every checkout."""
    decision = _run(bench, bench.yard, _bash(command, bench.yard))
    assert _denied_by_this_wall(decision), command


@pytest.mark.parametrize("command", [
    "git worktree prune",
    "git worktree add /tmp/a-new-worktree",
    "git worktree remove /tmp/somebody-elses",
    "git worktree move /tmp/a /tmp/b",
    "git worktree lock /tmp/somebody-elses",
])
def test_editing_the_shared_worktree_registry_is_refused(bench, command):
    decision = _run(bench, bench.yard, _bash(command, bench.yard))
    assert _denied_by_this_wall(decision), command


@pytest.mark.parametrize("command", [
    "git branch",
    "git branch -a",
    "git branch -r",
    "git branch --list",
    "git branch --show-current",
    "git branch --contains HEAD",
    "git branch -v --sort=-committerdate",
    # A flag of `git` itself, not of the subcommand. Counting it as a branch
    # flag made a plain listing look like a mutation.
    "git --no-pager branch",
    "git worktree list",
    "git worktree list --porcelain",
])
def test_the_listing_forms_stay_permitted(bench, command):
    """`branch` and `worktree` are not read-only verbs and not write verbs.

    They came off the read-only list because two of the four measured holes
    were there. Putting them on the refused list instead would break the answer
    to "which branch am I on", which is asked constantly.
    """
    assert not _denied_by_this_wall(
        _run(bench, bench.yard, _bash(command, bench.yard))), command


def test_a_yard_may_delete_its_own_branch(bench, yard_on_a_branch):
    """Your own branch is yours. The refusal above is about everybody else's."""
    path, branch = yard_on_a_branch
    assert not _denied_by_this_wall(
        _run(bench, path, _bash(f"git branch -D {branch}", path)))


def test_a_yard_may_rename_the_branch_it_is_standing_on(bench, yard_on_a_branch):
    """`git branch -m <new>` with one operand renames the current branch."""
    path, _ = yard_on_a_branch
    assert not _denied_by_this_wall(
        _run(bench, path, _bash("git branch -m a-new-name", path)))


def test_a_detached_yard_gets_no_own_branch_exception(bench):
    """The module's yard is detached, so there is no own branch to name.

    The exception must not widen into "any branch" when the guard cannot tell
    which one is yours. Unknown is refused, not permitted.
    """
    decision = _run(bench, bench.yard,
                    _bash("git branch -D anything-at-all", bench.yard))
    assert _denied_by_this_wall(decision)


# ============================================================
# The paired direction: HELM, the overlay, and the yard itself
# ============================================================

def test_a_write_inside_the_yard_itself_is_permitted(bench):
    assert not _denied_by_this_wall(
        _run(bench, bench.yard,
             _write(bench.yard / "scripts" / "x.py", bench.yard)))


@pytest.mark.parametrize("command", [
    "cat {helm}/CLAUDE.md",
    "ls {helm}/scripts",
    "rg needle {helm}/scripts",
    "head -20 {helm}/pyproject.toml",
    "git -C {helm} log --oneline -5",
    "git -C {helm} status",
    "git -C {helm} branch -a",
    "git -C {helm} worktree list",
    "git -C {helm} rev-parse HEAD",
])
def test_reading_helm_still_works(bench, command):
    """The line that would be easiest to break while closing the neighbour.

    HELM's `.git` holds the objects and the refs this worktree runs on. A guard
    that closed HELM to reads would leave git in a worktree dead, and
    `git -C <HELM> branch -a` in particular goes through the branch
    classification this change rewrote.
    """
    assert not _denied_by_this_wall(
        _run(bench, bench.yard,
             _bash(command.format(helm=bench.helm), bench.yard))), command


def test_reading_helm_through_the_read_tool_still_works(bench):
    decision = _run(bench, bench.yard, {
        "tool_name": "Read",
        "tool_input": {"file_path": str(bench.helm / "CLAUDE.md")},
        "cwd": str(bench.yard),
    })
    assert not _denied_by_this_wall(decision)


def test_a_write_into_helm_is_still_refused(bench):
    """The refusal this wall already had, re-asserted here because the branch it
    lives in was rewritten around it."""
    decision = _run(bench, bench.yard,
                    _write(bench.helm / "scripts" / "x.py", bench.yard))
    assert _denied_by_this_wall(decision)
    assert "HELM" in _reason(decision)


@pytest.mark.parametrize("template", [
    "git -C {data} log --oneline -5",
    "git -C {data} status",
    "cat {data}/CLAUDE.operational.md",
    "ls {data}",
])
def test_reading_the_data_overlay_still_works(bench, template):
    assert not _denied_by_this_wall(
        _run(bench, bench.yard,
             _bash(template.format(data=bench.overlay), bench.yard))), template


def test_writing_a_file_into_the_data_overlay_still_works(bench):
    """The rule people get wrong, and the one this change must not touch."""
    target = bench.overlay / "outputs" / "operations" / "a-report.md"
    assert not _denied_by_this_wall(_run(bench, bench.yard,
                                         _write(target, bench.yard)))


@pytest.mark.parametrize("template", [
    "git -C {data} commit -m x",
    "git -C {data} add .",
    "cd {data} && git commit -m x",
])
def test_running_git_in_the_data_overlay_is_still_refused(bench, template):
    """The other half of the same rule, and the reason the bench's overlay is
    real rather than skipped over: a change that quietly stopped resolving data
    roots would leave every case above green."""
    payload = _bash(template.format(data=bench.overlay), bench.yard)
    decision = _call_the_guard_directly(bench, bench.yard, payload)
    assert decision is not None, template
    assert decision.get("decision") == "block", template
    assert "the one rule" in decision.get("reason", ""), template


def test_a_write_to_a_temporary_directory_is_permitted(bench, tmp_path):
    assert not _denied_by_this_wall(
        _run(bench, bench.yard, _write(tmp_path / "scratch.txt", bench.yard)))


# ============================================================
# From HELM this wall stays silent
# ============================================================

def test_none_of_it_is_refused_from_the_main_clone(bench):
    """The anchor. Sent from a real main clone, where HELM sees everything.

    Without this the whole file is satisfied by a guard that refuses every
    command in every checkout.
    """
    for command in ("git branch -D someone-elses-branch",
                    "git worktree prune",
                    "git worktree add /tmp/a-new-worktree",
                    f"rm -rf {bench.neighbour}",
                    f"cat {bench.neighbour}/CLAUDE.md"):
        assert not _denied_by_this_wall(
            _run(bench, bench.helm, _bash(command, bench.helm))), command


def test_the_main_clone_may_write_into_itself(bench):
    assert not _denied_by_this_wall(
        _run(bench, bench.helm,
             _write(bench.helm / "scripts" / "x.py", bench.helm)))


def test_the_main_clone_may_write_into_a_worktree(bench):
    """HELM is the one checkout that sees them all, and it reviews them."""
    assert not _denied_by_this_wall(
        _run(bench, bench.helm,
             _write(bench.neighbour / "scripts" / "x.py", bench.helm)))
