"""Deleting a YARD was one command, and nothing asked whether it was finished.

MEASURED 2026-09-06. Removing a task's worktree, the agent ran

    git worktree remove <path> --force

`--force` was not needed and the tree was clean, so nothing was lost that day.
What the flag does is switch off git's own single objection: the refusal to
remove a worktree holding modified or untracked files. Driven against the
dispatcher as it stood before this change, every one of these was PERMITTED:

    git worktree remove <a yard with uncommitted work>       ALLOWED
    git worktree remove <a yard whose branch is unmerged>    ALLOWED
    rm -rf <a yard with a session standing in it>            ALLOWED
    rm -rf <the directory every yard lives in>               ALLOWED
    herdr worktree remove --workspace <that yard's id>       ALLOWED

A yard is the ONLY copy of what is in it. Its branch is unmerged by definition
while the task is running, and its working tree exists nowhere else, so this is
the one action in the HELM/YARD cycle that nothing can undo.

Three conditions define FINISHED, and this file drives each of them on its own,
because "commit it", "merge it" and "wait for the session to close" are three
different instructions:

    1. the working tree is clean
    2. HEAD holds no commit `main` cannot reach
    3. no LIVE process has its cwd inside it that THIS COMMAND would not close

Condition 3 took two corrections on 2026-09-06, both of the same shape: an
exemption whose condition never arrives, so the wall refuses the last step of
the documented cycle always. Asked as "is anybody in it", seven processes stood
in a finished yard. Asked as "is anybody in it that this command would not
close", nine MORE stood in it naming a yard deleted weeks earlier. Each clause
and its measurements are in the section that drives it, below.

Every refusal here is paired with the case that must still pass. The pairs
matter more than the refusals: a guard that refuses every deletion makes the
ordinary end of every task a fight, and it is switched off within the week.

WHERE THE BENCH LIVES, and it is not an accident. The guard exempts worktrees
under `/tmp` and `/var/tmp`, because the suite creates and destroys real
worktrees of this repository there and a guard fighting its own test suite gets
removed. So the yards under test are cut in a directory OUTSIDE the temp tree
(under `~/.cache`), which is what lets the refusals be driven end to end through
the real dispatcher rather than around it. The exemption itself is then driven
in the other direction, from a `tmp_path` worktree that is dirty and unmerged
and must still be permitted.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_REL = Path(".claude") / "hooks" / "_dispatch.py"


def _env(scratch: Path, **extra) -> dict:
    """The environment every child of this file gets.

    `HEADING_OS_DATA` at a scratch directory, so no child can resolve the
    operator's real overlay. `CLAUDE_PROJECT_DIR` removed, because nothing in
    this design may depend on it and a test that inherits it is not testing the
    design.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["HEADING_OS_DATA"] = str(scratch)
    env.update(extra)
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


def _drop_worktree(bench, path: Path) -> None:
    """Remove ONE worktree and ONE registration, this file's own.

    NEVER `git worktree prune`, which reaches every entry in the shared
    registry, including those of processes holding one open right now. The
    clone is disposable, so this is belt over braces.
    """
    registration = _registration_of(path)
    _git(["worktree", "remove", "--force", str(path)], bench.helm, bench.scratch)
    if registration is not None and registration.is_dir():
        shutil.rmtree(registration, ignore_errors=True)
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    """A main clone and a yards directory, both OUTSIDE the temp tree.

        <base>/yards/origin        a real MAIN clone, carrying this working tree
        <base>/yards/<a yard>      cut per test
        <scratch>                  what every child gets pinned to

    `<base>` is created under `~/.cache` rather than `tmp_path`, and the reason
    is the thing under test: the guard exempts `/tmp` and `/var/tmp` so the
    suite's own throwaway worktrees are left alone, so a bench built there would
    make every refusal below pass vacuously. The scratch data root stays in the
    temp tree, where it belongs.

    `main` is created as a local branch in the clone. `git clone` leaves `main`
    as `origin/main` only, and `rev-list main..HEAD` does not fall back to a
    remote-tracking ref: without this every branch case would report "could not
    compare" instead of the count it is asserting.
    """
    cache = Path.home() / ".cache" / "heading-os-tests"
    cache.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="yard-deletion-", dir=str(cache)))
    scratch = tmp_path_factory.mktemp("scratch-data-root")
    yards = base / "yards"
    yards.mkdir()
    helm = yards / "origin"

    cloned = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(helm)],
        capture_output=True, text=True, env=_env(scratch), timeout=300)
    if cloned.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"could not clone a bench: {cloned.stderr.strip()}")
    _copy_working_tree(helm, scratch)

    base_ref = "origin/main"
    if _git(["rev-parse", "--verify", "--quiet", base_ref], helm,
            scratch).returncode != 0:
        base_ref = "HEAD"
    if _git(["branch", "-f", "main", base_ref], helm, scratch).returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip("could not create a local `main` in the bench")

    # An identity, per clone. This repository's is set at REPOSITORY level, and
    # a clone inherits none of it: `git commit` then exits 128 with "empty ident
    # name" and the unmerged-branch case silently becomes a merged one, which is
    # a case asserting nothing while reporting green. The commit below is
    # checked rather than assumed for the same reason.
    _git(["config", "user.name", "yard deletion bench"], helm, scratch)
    _git(["config", "user.email", "bench@example.invalid"], helm, scratch)

    made = types.SimpleNamespace(base=base, helm=helm, yards=yards,
                                 scratch=scratch)
    yield made
    for path in sorted(yards.iterdir()):
        if path != helm and path.is_dir():
            _drop_worktree(made, path)
    shutil.rmtree(base, ignore_errors=True)


def _cut(bench, name: str, *, dirty: bool, ahead: bool = False,
         where: Path | None = None) -> Path:
    """One worktree of the bench, detached at `main`, in a chosen state.

    Detached rather than on a branch: `main` is checked out in the clone itself,
    and the states these cases need are about the WORK in the worktree, not
    about which name points at it. Detachment also drives the harder half of
    condition 2, since a detached HEAD has no branch to compare and the guard
    has to ask HEAD instead.
    """
    path = (where or bench.yards) / name
    created = _git(["worktree", "add", "--detach", str(path), "main"],
                   bench.helm, bench.scratch)
    if created.returncode != 0:
        pytest.skip(f"git worktree add failed: {created.stderr.strip()}")
    if dirty:
        (path / "a-half-finished-file.txt").write_text("work\n", encoding="utf-8")
    if ahead:
        made = _git(["commit", "--allow-empty", "-m", "unmerged work"], path,
                    bench.scratch)
        if made.returncode != 0:
            pytest.skip(f"could not commit in the bench: {made.stderr.strip()}")
        counted = _git(["rev-list", "--count", "main..HEAD"], path, bench.scratch)
        assert counted.stdout.strip() == "1", (
            f"this fixture exists to be AHEAD of main and is not: "
            f"{counted.stdout.strip()!r} {counted.stderr.strip()}")
    return path


@pytest.fixture
def finished(bench):
    """A yard at the ordinary end of a task: clean, merged, nobody in it."""
    path = _cut(bench, "a-finished-yard", dirty=False)
    yield path
    _drop_worktree(bench, path)


@pytest.fixture
def dirty(bench):
    """A yard holding an uncommitted file."""
    path = _cut(bench, "a-yard-with-uncommitted-work", dirty=True)
    yield path
    _drop_worktree(bench, path)


@pytest.fixture
def unmerged(bench):
    """A yard whose tree is clean and whose HEAD `main` cannot reach."""
    path = _cut(bench, "a-yard-whose-branch-is-unmerged", dirty=False, ahead=True)
    yield path
    _drop_worktree(bench, path)


# ============================================================
# Driving the wall
# ============================================================

def _run(bench, checkout: Path, command: str, cwd: Path | None = None,
         **env) -> dict | None:
    """Feed one Bash payload to the dispatcher in `checkout`. Return its decision."""
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "cwd": str(cwd or checkout)}
    result = subprocess.run(
        [sys.executable, str(checkout / DISPATCH_REL)],
        input=json.dumps(payload), cwd=str(checkout), capture_output=True,
        text=True, env=_env(bench.scratch, **env), timeout=300,
    )
    assert result.returncode == 0, (
        f"dispatcher exited {result.returncode}: {result.stderr}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _reason(decision: dict | None) -> str:
    if not decision:
        return ""
    return decision.get("hookSpecificOutput", {}).get(
        "permissionDecisionReason", "")


def _denied_by_this_wall(decision: dict | None) -> bool:
    """Denied BY THIS WALL, not merely denied.

    Twelve other checks sit in the same dispatcher and several refuse commands
    for their own reasons. Asserting "something said no" would pass with this
    wall deleted.
    """
    if not decision:
        return False
    if decision.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
        return False
    return "YARD deletion guard" in _reason(decision)


def _permitted(decision: dict | None) -> bool:
    """Nothing in the dispatcher refused. Not "this wall stayed silent"."""
    if not decision:
        return True
    return decision.get("hookSpecificOutput", {}).get(
        "permissionDecision") != "deny"


# ============================================================
# 1. A working tree with changes in it
# ============================================================

@pytest.mark.parametrize("form", [
    "git worktree remove {path}",
    "git worktree remove {path} --force",
    "git worktree remove --force {path}",
    "rm -rf {path}",
    "rm -r {path}",
    "rm --recursive --force {path}",
])
def test_every_form_of_deleting_a_dirty_yard_is_refused(bench, dirty, form):
    decision = _run(bench, bench.helm, form.format(path=dirty))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "uncommitted change" in _reason(decision)


def test_force_does_not_lift_the_refusal(bench, dirty):
    """The flag from the incident. It removes git's objection, not this one."""
    plain = _run(bench, bench.helm, f"git worktree remove {dirty}")
    forced = _run(bench, bench.helm, f"git worktree remove {dirty} --force")
    assert _denied_by_this_wall(plain) and _denied_by_this_wall(forced)


def test_a_relative_path_from_the_yards_directory_is_refused(bench, dirty):
    """`cd <yards> && rm -rf <name>` spells no absolute path anywhere."""
    decision = _run(bench, bench.helm,
                    f"cd {bench.yards} && rm -rf {dirty.name}")
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"


def test_deleting_the_directory_the_yards_live_in_is_refused(bench, dirty):
    """Erases several yards without spelling one of their names."""
    decision = _run(bench, bench.helm, f"rm -rf {bench.yards}")
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "contains it" in _reason(decision)


# ============================================================
# 2. Commits `main` cannot reach
# ============================================================

def test_a_clean_yard_whose_head_is_ahead_of_main_is_refused(bench, unmerged):
    decision = _run(bench, bench.helm, f"git worktree remove {unmerged}")
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "1 commit(s)" in _reason(decision)
    assert "uncommitted change" not in _reason(decision), (
        "the tree is clean; naming the wrong condition sends the operator to "
        "commit something that does not exist")


def test_an_unreachable_main_is_reported_as_unknown_not_as_merged(bench,
                                                                 finished):
    """`main` deleted: the comparison cannot be made, and that is not a pass.

    A guard reading a failed `rev-list` as zero would permit the deletion of
    every worktree in a repository whose base branch is named anything else.
    """
    _git(["branch", "-D", "main"], bench.helm, bench.scratch)
    try:
        decision = _run(bench, bench.helm, f"git worktree remove {finished}")
    finally:
        _git(["branch", "-f", "main", "HEAD"], bench.helm, bench.scratch)
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    # The BRANCH's own words, not the shared "UNKNOWN". Found by mutation:
    # turning `if not ok:` into `if False:` sends the stderr text down the
    # counting path, where `int()` raises and the fallback ALSO says UNKNOWN, so
    # a test asserting only that word passed against a version that no longer
    # distinguishes a failed comparison from an unparseable one. That version is
    # not harmless: a git failure with EMPTY stderr counts as zero and permits.
    assert "could not be compared against `main`" in _reason(decision)


def test_a_working_tree_that_cannot_be_read_is_unknown_not_clean(bench, tmp_path):
    """Both git questions fail: neither answer may be read as "nothing there".

    A checkout whose `.git` pointer is corrupt still exists on disk and is still
    registered, so it is still a deletion target. `git status` and `rev-list`
    both fail in it, and a guard reading either failure as a pass deletes a tree
    it never managed to look inside.
    """
    path = _cut(bench, "a-yard-whose-git-file-is-corrupt", dirty=False)
    try:
        (path / ".git").write_text("gitdir: /nowhere-at-all\n", encoding="utf-8")
        decision = _run(bench, bench.helm, f"rm -rf {path}")
    finally:
        _drop_worktree(bench, path)
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "its working tree could not be read" in _reason(decision)
    assert "could not be compared against `main`" in _reason(decision)


# ============================================================
# 3. A process standing in it
# ============================================================

def test_a_yard_with_a_process_in_it_is_refused(bench, finished):
    """Clean and merged, so only the third condition can refuse this.

    The workspace variable is stripped from the child on purpose. This suite
    runs both inside a yard, where the runner carries one, and in HELM, where it
    does not; inheriting it would make the case turn on whether that workspace
    happens to be live in the operator's herdr record. A process naming no
    workspace is the plainest form of "somebody is in here" and is what this
    case is about.
    """
    env = _env(bench.scratch)
    env.pop("HERDR_WORKSPACE_ID", None)
    held = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            cwd=str(finished), env=env)
    try:
        # The cwd link exists as soon as the child does; give the fork a moment
        # so this asserts about a running process rather than about a race.
        for _ in range(50):
            if held.poll() is None:
                break
            time.sleep(0.02)
        decision = _run(bench, bench.helm, f"rm -rf {finished}")
    finally:
        held.kill()
        held.wait(timeout=30)
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "standing in it" in _reason(decision)
    assert str(held.pid) in _reason(decision)


def test_an_unreadable_proc_is_unknown_not_nobody(bench, finished, monkeypatch):
    """`/proc` gone: "could not look" must not be delivered as "nobody is here".

    In-process rather than through the dispatcher, and the reason is stated
    rather than hidden: the guard asks the REAL `/proc`, and there is no payload
    field, flag or variable that redirects it -- deliberately, since one would be
    a way to blind the guard from outside. So the owner it calls is replaced
    here instead. The branch under test is the real one, in the real function.
    """
    import importlib.util

    from scripts.utils import proc_cwd

    # THIS checkout's dispatcher, not the bench's. Loading the bench's would run
    # its `sys.path.insert(0, <bench>)` at import and leave that entry in this
    # worker for every test after it, which is a fixture poisoning a process.
    spec = importlib.util.spec_from_file_location(
        "dispatch_under_test", ROOT / DISPATCH_REL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(proc_cwd, "processes_in", lambda *a, **k: None)
    unmet = module._yard_unfinished(finished)
    assert all(kind for kind, _ in unmet), "every reason carries its kind"
    assert any("UNKNOWN" in text and "/proc" in text
               for _, text in unmet), (
        f"an unreadable /proc was read as an empty machine: {unmet}")


# ============================================================
# Whose processes they are, and whether the command closes them
# ============================================================
#
# The first version of this wall asked only "is anybody in it", and refused the
# last step of the documented cycle always, for everyone. MEASURED 2026-09-06
# against the live yard `w5M`: seven processes stood in it -- the agent, three
# MCP servers, the pane shell and two children -- and that is the NORMAL state
# of a yard whose work is finished. "Nobody in it" never arrives while the yard
# exists as a herdr workspace.
#
# The fix is not to weaken condition 3 but to ask what the COMMAND does.
# `herdr worktree remove --workspace <ID>` closes that workspace's session as
# part of the removal; `rm -rf` and `git worktree remove` close nothing and
# leave every process inside with a deleted working directory. So the exemption
# is per form, and every case below is paired with the same state under a form
# that closes nothing.
#
# Ownership is read from `HERDR_WORKSPACE_ID`, which herdr exports into the pane
# and every descendant inherits. MEASURED the same day: all seven processes in
# `w5M` carried `w5M` with a readable environment, and a process started with
# the variable stripped read back as absent. The processes here are REAL ones
# with real environments, not a fixture standing in for `/proc`.

@pytest.fixture
def standing(bench):
    """Start a real process in a directory, with a chosen workspace id."""
    started: list[subprocess.Popen] = []

    def make(cwd: Path, workspace: str | None) -> subprocess.Popen:
        env = _env(bench.scratch)
        env.pop("HERDR_WORKSPACE_ID", None)
        if workspace is not None:
            env["HERDR_WORKSPACE_ID"] = workspace
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            cwd=str(cwd), env=env)
        started.append(process)
        # `environ` is written by exec, which has not necessarily happened when
        # Popen returns. Wait for the kernel to agree, so a pass is about the
        # guard rather than about scheduling.
        deadline = time.monotonic() + 10
        want = f"HERDR_WORKSPACE_ID={workspace or ''}".encode()
        while time.monotonic() < deadline:
            try:
                raw = Path(f"/proc/{process.pid}/environ").read_bytes()
            except OSError:
                raw = b""
            if (workspace is None and raw) or (workspace and want in raw.split(b"\0")):
                return process
            time.sleep(0.02)
        pytest.skip("the staged process never showed the environment asked for")

    yield make
    for process in started:
        process.kill()
        process.wait(timeout=30)


def test_the_herdr_form_ignores_the_session_it_is_about_to_close(
        bench, finished, standing, tmp_path):
    """The defect: a finished yard could never be removed, because it is alive.

    Clean tree, merged HEAD, and its own session standing in it. That is the
    ordinary end of every task, and before 2026-09-06 this refused.
    """
    standing(finished, "wTEST")
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST",
                    HERDR_SOCKET_PATH=str(socket))
    assert _permitted(decision), _reason(decision)


def test_a_process_from_another_workspace_still_refuses(
        bench, finished, standing, tmp_path):
    """The exemption is for the session this command closes, and no other.

    A neighbouring yard's agent that has changed directory into this one is not
    closed by removing this workspace, so it is exactly the casualty condition 3
    exists for.
    """
    standing(finished, "wSOMEONE-ELSE")
    # `also_live`, or the neighbour is an ORPHAN and gets dropped for a
    # different reason entirely, turning this case into its opposite.
    socket = _herdr_session(tmp_path, "wTEST", finished,
                            also_live=("wSOMEONE-ELSE",))
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "standing in it that this command would not close" in _reason(decision)


def test_a_process_that_claims_no_workspace_still_refuses(
        bench, finished, standing, tmp_path):
    """Unattributable is not owned. A process started outside herdr carries no
    workspace at all, and nothing establishes that removing this one closes it."""
    standing(finished, None)
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "standing in it that this command would not close" in _reason(decision)


@pytest.mark.parametrize("form", ["rm -rf {path}", "git worktree remove {path}"])
def test_a_form_that_closes_nothing_keeps_the_whole_condition(
        bench, finished, standing, tmp_path, form):
    """The paired direction, and the narrowness of the exemption.

    Same yard, same owned process, a command that shuts nothing down. Every one
    of those processes would be left with a deleted working directory.

    The session file is supplied even though no herdr command is run here: it is
    what makes `wTEST` a LIVE workspace. Without it the guard reads the
    operator's real record, `wTEST` is in nobody's, and the process is dropped
    as an orphan, so the case would pass while asserting nothing.
    """
    process = standing(finished, "wTEST")
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm, form.format(path=finished),
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert str(process.pid) in _reason(decision)
    assert "standing in it that this command would not close" not in _reason(decision), (
        "this form closes nothing, so no process in it is exempt and the "
        "qualifier would be a false narrowing. Anchored on the UNMET line, not "
        "on the phrase alone: the closing paragraph repeats those words in the "
        "general statement of what finished means, and a looser match reads "
        "that as the qualifier.")


# ============================================================
# Orphans: a process whose workspace no longer exists
# ============================================================
#
# The ownership clause fixed the first refusal and still refused every removal,
# for a reason nobody had looked for. MEASURED 2026-09-06 in HELM, on the first
# real deletion after the merge: fourteen processes stood in this yard, five its
# own and NINE naming `w4G`, a yard deleted long ago and absent from both
# `herdr workspace list` and `session.json`. Claude Code's warmed background
# workers and the MCP servers they spawn drift into live yards, and the
# population only grows.
#
# The evidence is against condition 3 for that class rather than for it: `w4G`'s
# own checkout was deleted UNDER those processes and nothing broke. So a process
# naming a workspace herdr does not record is an orphan, and it is dropped for
# EVERY form -- the argument is about the process, not about what the command
# closes.

def test_an_orphan_does_not_block_the_herdr_form(
        bench, finished, standing, tmp_path):
    """The defect: nine processes from a yard deleted weeks ago, and a refusal."""
    standing(finished, "wGHOST")
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST",
                    HERDR_SOCKET_PATH=str(socket))
    assert _permitted(decision), _reason(decision)


@pytest.mark.parametrize("form", ["rm -rf {path}", "git worktree remove {path}"])
def test_an_orphan_does_not_block_a_form_that_closes_nothing(
        bench, finished, standing, tmp_path, form):
    """The orphan clause is NOT the ownership clause, and this is the difference.

    Ownership asks what the command closes, so it applies to the herdr form
    alone. An orphan's yard is already gone, so it is nobody's unfinished work
    whatever the command is, and `rm -rf` of a finished yard has the same claim
    on the exemption. Without this pair the two clauses would look like one.
    """
    standing(finished, "wGHOST")
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm, form.format(path=finished),
                    HERDR_SOCKET_PATH=str(socket))
    assert _permitted(decision), _reason(decision)


def test_an_unreadable_session_record_drops_nothing(
        bench, finished, standing, tmp_path):
    """Orphan cannot be told from neighbour, so neither is dropped, and it says so.

    Both candidate session files are taken away: `HERDR_SOCKET_PATH` points at a
    directory that does not exist, and `HOME` at an empty one. Dropping on that
    evidence would be inventing the exemption rather than establishing it, and
    reporting a process count that rests on a lookup nobody managed would be the
    same defect one level up.
    """
    process = standing(finished, "wGHOST")
    empty_home = tmp_path / "no-herdr-here"
    empty_home.mkdir()
    decision = _run(bench, bench.helm, f"rm -rf {finished}",
                    HERDR_SOCKET_PATH=str(tmp_path / "nowhere" / "herdr.sock"),
                    HOME=str(empty_home))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert str(process.pid) in _reason(decision)
    assert "could not be read" in _reason(decision)


# ============================================================
# The refusal names the remedy that applies, and no other
# ============================================================

def test_a_refusal_over_processes_alone_does_not_ask_for_a_commit_or_a_merge(
        bench, finished, standing, tmp_path):
    """The text lied on the first live refusal.

    MEASURED 2026-09-06 in HELM: the work was committed and the branch was
    merged, and the wall still ended "commit the work in that yard and have HELM
    merge the branch, then this command passes without a word". It named the two
    things the operator had already done and not the one that was blocking.
    """
    standing(finished, "wTEST")
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm, f"rm -rf {finished}",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    reason = _reason(decision)
    assert "Commit the work in that yard" not in reason, reason
    assert "Have HELM merge the branch" not in reason, reason
    assert "herdr worktree remove --workspace wTEST" in reason


def test_a_refusal_over_the_working_tree_asks_for_a_commit(bench, dirty):
    """The paired direction: the remedy that DOES apply is still printed.

    A composed message that printed nothing would satisfy the case above and be
    useless, which is the shape of a guard that refuses everything.
    """
    decision = _run(bench, bench.helm, f"rm -rf {dirty}")
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "Commit the work in that yard" in _reason(decision)


def test_a_refusal_over_an_unmerged_branch_asks_for_the_merge(bench, unmerged):
    decision = _run(bench, bench.helm, f"git worktree remove {unmerged}")
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "Have HELM merge the branch" in _reason(decision)
    assert "Commit the work in that yard" not in _reason(decision), (
        "the tree is clean; asking for a commit sends the operator to do "
        "something that would produce an empty one")


def test_a_process_whose_environment_cannot_be_read_is_not_owned(
        bench, finished, monkeypatch):
    """`env is None` is "could not look", and it must not read as "it is ours".

    `/proc/<pid>/environ` is readable only for a process this user owns, so on a
    machine where another user is standing in a yard the answer is not "no
    workspace" but "no answer". Every case above stages its own processes, so
    none of them can reach this branch: the owner it calls is replaced instead,
    in process, and the real filter in the real function runs over the result.
    """
    import importlib.util

    from scripts.utils import proc_cwd

    spec = importlib.util.spec_from_file_location(
        "dispatch_ownership_under_test", ROOT / DISPATCH_REL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    unreadable = proc_cwd.Process(4242, "claude", finished, None)
    monkeypatch.setattr(proc_cwd, "processes_in", lambda *a, **k: [unreadable])
    named, caveat = module._yard_live_processes(finished, "wTEST")
    assert caveat is None or "could not be read" in caveat
    assert named and "4242" in named[0], (
        f"a process with an unreadable environment was treated as belonging to "
        f"the workspace being closed: {named}")


def test_an_unreadable_environment_reads_back_as_none_not_as_empty():
    """The seam the case above cannot reach, driven with a real OSError.

    `_yard_live_processes` treats `env is None` as foreign, so the whole clause
    rests on `proc_cwd` returning None rather than `{}` when the read fails.
    Found by mutation: `return {}` there left every case green, because the one
    above replaces `processes_in` and so never runs this code, and no test can
    stage a genuinely unreadable environment without a second user's process.

    A pid that cannot exist raises the same `FileNotFoundError` an unreadable
    one raises, and it is an OSError arriving at the real handler. The private
    helper is called directly because that IS the seam; going through
    `processes_in` would only reach it for a process this user owns, whose
    environment is by definition readable.
    """
    from scripts.utils.proc_cwd import _environ

    assert _environ(Path("/proc/2147483646"), ("HERDR_WORKSPACE_ID",)) is None, (
        "a read that failed was reported as an environment with nothing in it, "
        "which makes an uninspectable process look like one that simply "
        "carries no workspace"
    )


def test_the_refusal_names_the_spelling_that_would_close_the_session(
        bench, finished, standing, tmp_path):
    """A wall that only says no, on the last step of the cycle, gets removed.

    When the ONLY thing unfinished is the yard's own session, the operator is
    holding the wrong spelling rather than unfinished work, so the refusal
    carries the right one with the workspace id in it.
    """
    standing(finished, "wTEST")
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm, f"rm -rf {finished}",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "herdr worktree remove --workspace wTEST" in _reason(decision)


def test_the_incident_case_is_untouched_by_the_ownership_clause(
        bench, dirty, standing, tmp_path):
    """`--force` over uncommitted work, which is why this wall exists.

    The herdr form with its own session standing in it: condition 3 is satisfied
    by the exemption, and the refusal must come anyway, from condition 1. The
    exemption reaches one condition and it is not this one.
    """
    standing(dirty, "wTEST")
    socket = _herdr_session(tmp_path, "wTEST", dirty)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST --force",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "uncommitted change" in _reason(decision)
    assert "process(es)" not in _reason(decision), (
        "the owned session was reported as a casualty of the command that "
        "closes it")


# ============================================================
# The ordinary end of a task must stay silent
# ============================================================

def test_deleting_a_finished_yard_is_permitted(bench, finished):
    """Clean, merged, empty. This is the daily path and it costs no words."""
    decision = _run(bench, bench.helm, f"git worktree remove {finished}")
    assert _permitted(decision), _reason(decision)


def test_rm_of_a_finished_yard_is_permitted(bench, finished):
    decision = _run(bench, bench.helm, f"rm -rf {finished}")
    assert _permitted(decision), _reason(decision)


@pytest.mark.parametrize("command", [
    "git status --porcelain",
    "git worktree list",
    "git log --oneline -5",
    "ls -la",
    "rm -rf build",
    "rm -rf .pytest_cache",
])
def test_ordinary_commands_are_untouched(bench, dirty, command):
    """A dirty yard exists on the bench, and none of these names it."""
    decision = _run(bench, bench.helm, command)
    assert _permitted(decision), _reason(decision)


def test_rm_without_recursion_is_untouched(bench, dirty):
    """`rm <dir>` cannot remove a directory, so it cannot remove a checkout."""
    decision = _run(bench, bench.helm, f"rm {dirty}")
    assert _permitted(decision), _reason(decision)


def test_a_throwaway_worktree_in_the_temp_tree_is_permitted(bench, tmp_path):
    """The suite's own worktrees. Dirty AND unmerged, and still permitted.

    The paired direction of every refusal above: the same command against the
    same state, differing only in living under `/tmp`. Without this exemption
    the guard fights the suite that holds it.
    """
    path = _cut(bench, "a-throwaway", dirty=True, ahead=True, where=tmp_path)
    try:
        # noqa S108: the literal is the assertion's SUBJECT, not a path anything
        # here opens. This case is only a case if pytest really put the worktree
        # in the tree the guard exempts, and checking that is the whole line.
        assert str(path).startswith("/tmp"), (  # noqa: S108
            f"this case is about the temp tree and pytest put it at {path}")
        decision = _run(bench, bench.helm, f"rm -rf {path}")
        assert _permitted(decision), _reason(decision)
    finally:
        _drop_worktree(bench, path)


# ============================================================
# The herdr spelling, which names no path at all
# ============================================================

def _herdr_session(tmp_path: Path, workspace_id: str, checkout: Path,
                   also_live: tuple[str, ...] = ()) -> Path:
    """A herdr session file mapping one workspace id to one checkout.

    Its own file, never the operator's: this reads herdr's real state on the
    machine, and a test that edited it would be reaching into a running
    program's records.

    `also_live` adds workspaces the guard should treat as EXISTING without
    giving them a checkout here. It is not decoration: since 2026-09-06 a
    process naming a workspace herdr does not record is an orphan and is
    dropped, so a case about a live NEIGHBOUR has to say that the neighbour is
    live. Without it the case silently becomes the orphan case and asserts the
    opposite of what it was written for.
    """
    config = tmp_path / "herdr-config"
    config.mkdir(exist_ok=True)
    workspaces = [{
        "id": workspace_id,
        "worktree_space": {"checkout_path": str(checkout),
                           "is_linked_worktree": True},
    }]
    workspaces += [{"id": other} for other in also_live]
    (config / "session.json").write_text(json.dumps({
        "version": 1, "workspaces": workspaces,
    }), encoding="utf-8")
    return config / "herdr.sock"


def test_the_herdr_form_resolves_its_workspace_id_and_refuses(bench, dirty,
                                                             tmp_path):
    """`--workspace <ID>` spells no path; the path is looked up and judged."""
    socket = _herdr_session(tmp_path, "wTEST", dirty)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "uncommitted change" in _reason(decision)


def test_the_herdr_form_with_an_equals_sign_resolves_too(bench, dirty, tmp_path):
    socket = _herdr_session(tmp_path, "wTEST", dirty)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace=wTEST --force",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"


def test_the_herdr_form_naming_a_finished_yard_is_permitted(bench, finished,
                                                            tmp_path):
    socket = _herdr_session(tmp_path, "wTEST", finished)
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wTEST",
                    HERDR_SOCKET_PATH=str(socket))
    assert _permitted(decision), _reason(decision)


def test_an_unresolvable_herdr_removal_is_refused_rather_than_waved_through(
        bench, tmp_path):
    """No `--workspace`, so nothing says WHICH checkout would be erased.

    Silence here would be the hole the whole file is about, one command later:
    a form the guard recognises and then permits because it could not work out
    what it points at.
    """
    socket = _herdr_session(tmp_path, "wTEST", bench.yards / "nothing")
    decision = _run(bench, bench.helm, "herdr worktree remove --force",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "names no checkout this hook could resolve" in _reason(decision)


def test_an_unknown_workspace_id_is_refused(bench, tmp_path):
    socket = _herdr_session(tmp_path, "wTEST", bench.yards / "nothing")
    decision = _run(bench, bench.helm,
                    "herdr worktree remove --workspace wNOTHING",
                    HERDR_SOCKET_PATH=str(socket))
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"


def test_the_fixed_config_path_resolves_it_with_no_socket_variable(
        bench, dirty, tmp_path):
    """No `HERDR_SOCKET_PATH` at all: `~/.config/herdr` still answers.

    Both candidates are read and their answers unioned, so pointing the variable
    at a doctored file can only ADD a checkout to guard, never remove one. A
    resolver trusting the variable alone would take its answer from a file the
    running process can choose, which is the shape a guard must not have. Found
    by mutation: dropping the fixed candidate left every case here green,
    because each one sets the variable.
    """
    home = tmp_path / "herdr-home"
    (home / ".config" / "herdr").mkdir(parents=True)
    (home / ".config" / "herdr" / "session.json").write_text(json.dumps({
        "version": 1,
        "workspaces": [{"id": "wFIXED",
                        "worktree_space": {"checkout_path": str(dirty)}}],
    }), encoding="utf-8")

    env = _env(bench.scratch, HOME=str(home))
    env.pop("HERDR_SOCKET_PATH", None)
    result = subprocess.run(
        [sys.executable, str(bench.helm / DISPATCH_REL)],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command":
                                         "herdr worktree remove --workspace wFIXED"},
                          "cwd": str(bench.helm)}),
        cwd=str(bench.helm), capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout) if result.stdout.strip() else None
    assert _denied_by_this_wall(decision), _reason(decision) or "permitted"
    assert "uncommitted change" in _reason(decision)


def test_other_herdr_commands_are_untouched(bench, dirty, tmp_path):
    socket = _herdr_session(tmp_path, "wTEST", dirty)
    for command in ("herdr worktree list", "herdr worktree create --branch x",
                    "herdr pane run w1:p1 'echo hi'"):
        decision = _run(bench, bench.helm, command,
                        HERDR_SOCKET_PATH=str(socket))
        assert _permitted(decision), f"{command}: {_reason(decision)}"
