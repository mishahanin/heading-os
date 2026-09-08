"""`--is-inside-work-tree` answered for the wrong repository, and it committed it.

`git_init_commit` in `scripts/create-data-repo.py` decided whether to run
`git init` with:

    is_repo = run(["git", "rev-parse", "--is-inside-work-tree"], target,
                  check=False).returncode == 0

That question is "is this path inside ANY repository". It was read as "is this
the repository I already initialised". For a target under an existing checkout
it answers yes, so `git init` is skipped, and the four lines below it then
operate on THE ENCLOSING REPOSITORY:

    git config user.name ...      # writes the enclosing repo's .git/config
    git config user.email ...     # same
    git add -A                    # stages the WHOLE enclosing work tree
    git commit -m "chore: initialize private data overlay"

MEASURED 2026-09-08 in the operator's live clone. A test run started with
`--basetemp=.tmp/night-repair/pytest-bt` put every `tmp_path` inside the engine
work tree, a test scaffolded a fake data overlay there, and the result was
commit `ed7cee1` on `main`: six files, two unrelated sessions' work, under a
message describing neither. `git add -A` has been whole-tree since git 2.0, so
the cwd being a leaf under `.tmp/` did not limit it.

REPRODUCED 2026-09-08 against the pre-fix code, in a throwaway repository under
the system temp directory: `git_init_commit(<outer>/sub/overlay)` produced
`94e5185 chore: initialize private data overlay` on the OUTER repo, staging
`bystander.txt` (a file the overlay knows nothing about) alongside
`sub/overlay/README.md`, and left `sub/overlay` with no `.git` of its own.

`require_main_clone(__file__)` is not a defence and never was. The incident
happened IN HELM, where that guard passes. It answers "which checkout am I
running from", never "where am I about to commit".

Two repairs, and the tests below are in that order:

  * `is_repo_root` asks `git rev-parse --show-toplevel` and compares it with the
    target, so the probe answers an IDENTITY rather than a yes/no.
  * `main()` refuses a target inside the engine work tree outright, before it
    scaffolds anything, via the workspace's existing
    `require_outside_engine_clone`.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ENGINE_ROOT / "scripts" / "create-data-repo.py"


@pytest.fixture
def cdr():
    """The script under test, loaded by path (its name is not a module name)."""
    spec = importlib.util.spec_from_file_location("create_data_repo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["create_data_repo"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("create_data_repo", None)


@pytest.fixture
def engine_scratch(request):
    """A path under `.tmp/` removed before AND after; see the sibling file.

    The before half matters: a mutation run removes the guard on purpose, so a
    surviving mutant leaves the path behind and the next run fails on the litter
    rather than on the code.
    """
    import shutil

    made = []

    def make(name: str) -> Path:
        path = ENGINE_ROOT / ".tmp" / name
        shutil.rmtree(path, ignore_errors=True)
        made.append(path)
        return path

    yield make
    for path in made:
        shutil.rmtree(path, ignore_errors=True)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True)


@pytest.fixture
def enclosing_repo(tmp_path):
    """A repository with one uncommitted bystander file and a nested directory.

    The bystander is the whole point: it is what `git add -A` sweeps up, and it
    is how the incident put two unrelated sessions' work into one commit.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q", "-b", "main", ".")
    _git(outer, "config", "user.name", "Fixture")
    _git(outer, "config", "user.email", "fixture@example.invalid")
    (outer / "bystander.txt").write_text("somebody else's unfinished work\n",
                                         encoding="utf-8")
    nested = outer / "sub" / "overlay"
    nested.mkdir(parents=True)
    (nested / "README.md").write_text("# fabricated data overlay\n",
                                      encoding="utf-8")
    return outer, nested


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


def test_a_target_inside_another_repository_is_never_taken_for_its_own(
        cdr, enclosing_repo):
    """The narrowest statement of the defect, at the predicate."""
    outer, nested = enclosing_repo
    assert cdr.is_repo_root(outer) is True
    assert cdr.is_repo_root(nested) is False


def test_an_unresolved_target_is_still_recognised_as_its_own_root(cdr, tmp_path):
    """Both sides are resolved before the comparison, and that is load-bearing.

    Found by mutation: `return toplevel == str(target)` SURVIVED the first
    version of this file, because every target it built was already absolute and
    normalised. git always answers with a fully resolved path; a caller does not
    have to. `main()` resolves, but `git_init_commit` is reachable directly and
    a `..` or a trailing `.` in the target would then make an existing overlay
    look like no repository at all, and `git init` would run over it on every
    resumed bootstrap.
    """
    target = tmp_path / ".heading-os-data"
    target.mkdir()
    _git(target, "init", "-q", "-b", "main", ".")

    unresolved = tmp_path / "sibling" / ".." / ".heading-os-data" / "."
    (tmp_path / "sibling").mkdir()
    assert str(unresolved) != str(target), "the fixture normalised the path"
    assert cdr.is_repo_root(unresolved) is True


def test_a_directory_in_no_repository_at_all_is_not_a_root(cdr, tmp_path):
    """The other exit path: git declines to answer, and that is not a root."""
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    assert cdr.is_repo_root(lonely) is False


def test_the_nested_target_gets_its_own_repo_and_the_outer_one_is_untouched(
        cdr, enclosing_repo):
    """The observable consequence, through the function that caused it.

    Three assertions, because the defect had three halves: the outer repo must
    gain no commit, its bystander must stay unstaged, and the target must end up
    a repository in its own right.
    """
    outer, nested = enclosing_repo

    assert cdr.git_init_commit(nested, dry_run=False) == 0

    log = _git(outer, "log", "--oneline")
    assert log.returncode != 0, (
        f"the outer repository was committed: {log.stdout.strip()!r}")
    status = _git(outer, "status", "--porcelain").stdout
    assert "?? bystander.txt" in status, (
        f"the bystander was staged into somebody's commit; status was {status!r}")

    assert (nested / ".git").is_dir(), "the target did not get its own repository"
    inner_log = _git(nested, "log", "--oneline", "--name-only")
    assert inner_log.returncode == 0, inner_log.stderr
    assert "initialize private data overlay" in inner_log.stdout
    assert "README.md" in inner_log.stdout
    assert "bystander.txt" not in inner_log.stdout, inner_log.stdout


def test_the_identity_of_the_enclosing_repo_is_left_alone(cdr, enclosing_repo):
    """`git config user.name` wrote the ENCLOSING repo's `.git/config` too."""
    outer, nested = enclosing_repo
    cdr.git_init_commit(nested, dry_run=False)
    assert _git(outer, "config", "user.name").stdout.strip() == "Fixture"


# ---------------------------------------------------------------------------
# The other direction: the legitimate flow still works, end to end
# ---------------------------------------------------------------------------


def test_a_sibling_target_is_initialised_and_committed(cdr, tmp_path):
    """The real shape: a directory beside the engine, in no repository."""
    target = tmp_path / ".heading-os-data"
    target.mkdir()
    (target / "README.md").write_text("# overlay\n", encoding="utf-8")

    assert cdr.git_init_commit(target, dry_run=False) == 0

    assert (target / ".git").is_dir()
    branch = _git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert branch == "main", branch
    assert "initialize private data overlay" in _git(
        target, "log", "--oneline").stdout


def test_a_second_run_on_the_same_target_is_a_no_op(cdr, tmp_path, capsys):
    """Idempotence is what the broken probe was reaching for; it must survive.

    A resumable bootstrap is why the probe existed at all, so a repair that
    re-inits or re-commits on the second pass would have traded one defect for
    another.
    """
    target = tmp_path / ".heading-os-data"
    target.mkdir()
    (target / "README.md").write_text("# overlay\n", encoding="utf-8")

    cdr.git_init_commit(target, dry_run=False)
    first = _git(target, "rev-parse", "HEAD").stdout.strip()
    capsys.readouterr()

    assert cdr.git_init_commit(target, dry_run=False) == 0
    assert "no changes to commit" in capsys.readouterr().out
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == first
    assert len(_git(target, "log", "--oneline").stdout.strip().splitlines()) == 1


def test_dry_run_still_touches_nothing(cdr, tmp_path):
    target = tmp_path / ".heading-os-data"
    target.mkdir()
    assert cdr.git_init_commit(target, dry_run=True) == 0
    assert not (target / ".git").exists()


# ---------------------------------------------------------------------------
# Containment: no legitimate call has an engine-internal target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative", [".tmp/fake-overlay", "scripts", "."])
def test_a_target_inside_the_engine_is_refused_by_git_init_commit(cdr, relative):
    """Refused at the function that commits, so a direct caller is covered."""
    from scripts.utils.paths import DataRootError

    target = (ENGINE_ROOT / relative).resolve()
    with pytest.raises(DataRootError) as caught:
        cdr.git_init_commit(target, dry_run=False)
    assert str(target) in str(caught.value)


def test_main_refuses_an_engine_internal_target_before_it_scaffolds_anything(
        cdr, disarm_clone_guard, monkeypatch, capsys, engine_scratch):
    """The whole entry point, and the assertion is that nothing was written.

    `disarm_clone_guard` because `require_main_clone(__file__)` is the first
    statement of `main()` and exits 2 from this worktree, which would make the
    test pass for a reason that has nothing to do with containment. That guard
    is owned by `tests/test_a_prohibition_written_as_a_list_of_verbs.py`; this
    file owns the behaviour behind it, and the incident happened in HELM where
    the guard passes anyway.
    """
    disarm_clone_guard(cdr)
    victim = engine_scratch("create-data-repo-should-refuse")
    monkeypatch.setattr(
        sys, "argv",
        ["create-data-repo.py", "--path", str(victim), "--no-remote"])

    assert cdr.main() == 2
    assert "REFUSING" in capsys.readouterr().out
    assert not victim.exists(), "the refusal ran after the scaffold, not before"


def test_a_relative_engine_internal_path_is_refused_too(
        cdr, disarm_clone_guard, monkeypatch, capsys, engine_scratch):
    """`--path .tmp/x` from the engine root, which is how the flag gets typed."""
    disarm_clone_guard(cdr)
    victim = engine_scratch("relative-should-refuse")
    monkeypatch.chdir(ENGINE_ROOT)
    monkeypatch.setattr(
        sys, "argv",
        ["create-data-repo.py", "--path", ".tmp/relative-should-refuse",
         "--no-remote"])

    assert cdr.main() == 2
    assert "REFUSING" in capsys.readouterr().out
    assert not victim.exists()


def test_a_sibling_target_is_not_refused_by_main(cdr, tmp_path):
    """The other direction at the entry point: the default shape must pass.

    The predicate alone, not `main()` end to end -- the full run reaches `gh`
    and the network. What must hold is that the containment check does not
    stand in the way of the sibling layout the script exists to create.
    """
    from scripts.utils.workspace import require_outside_engine_clone

    sibling = tmp_path / ".heading-os-data"
    sibling.mkdir()
    assert require_outside_engine_clone(sibling, "--path") == sibling.resolve()


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


def test_the_old_probe_is_no_longer_PASSED_to_git_by_this_script():
    """Asked of the AST, because a substring scan cannot survive the fix.

    Written as `"--is-inside-work-tree" not in source` first, and it went red on
    its own commit: the docstring above and the one on `is_repo_root` both quote
    the flag in order to explain what was wrong with it. A text scan that
    punishes an explanation teaches the next author to stop explaining, which is
    the trade this repository has already refused elsewhere.

    So the question is not "does this string appear" but "is it handed to a
    subprocess", and only an argument list answers that. Narrow on purpose:
    `--is-inside-work-tree` is a good question elsewhere in the tree --
    `scripts/apply-wizard-answers.py` uses it as a REFUSAL gate, which is the
    correct consequence for it. What must not come back is THIS script using it
    to decide whether to skip `git init`.
    """
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    passed = {
        node.value
        for call in ast.walk(tree) if isinstance(call, ast.Call)
        for arg in call.args
        for node in ast.walk(arg)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "--is-inside-work-tree" not in passed
    assert "--show-toplevel" in passed, (
        "the identity probe is gone; without it the skip-init decision has "
        "nothing to compare the target against")


def test_the_containment_check_precedes_the_scaffold_in_main():
    """Asked of the AST: order is the property, and a comment is not evidence."""
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main_fn = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "main")
    called = [
        (node.lineno, node.func.id)
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("require_outside_engine_clone", "scaffold")
    ]
    names = [name for _, name in sorted(called)]
    assert names[:2] == ["require_outside_engine_clone", "scaffold"], names
