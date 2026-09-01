"""A publish that committed to one branch and reported another.

`publish()` runs `git commit` in the downstream clone, which lands on whatever
HEAD points at, then pushes with `branch="main"` and prints "Pushed build N to
origin/main." Nothing between the two ever asked which branch the clone was on.

MEASURED 2026-08-30 on a scratch clone checked out to `scratch`:

    publish(dest, push=False)   -> 0
    HEAD                        -> scratch
    scratch tip                 -> "service-host: publish build 1"
    main tip                    -> (empty; main never got the build)

and the run closed by advising `git push origin main`, a push of a branch that
does not carry the build. With --push the supervised push targets `main` all
the same. `push-all.py` already gates exactly this shape
(`branch is '...', expected 'main'`); this second publication path had none.

The guard runs in `main()` BEFORE `copy_includes`, so a refusal leaves the
downstream clone untouched rather than rmtree'd, rewritten and left dirty.

Every git repository here is created under tmp_path. Nothing pushes: `publish`
is replaced by a recorder in the `main()` cases, and the branch cases never
reach it.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def pub():
    spec = importlib.util.spec_from_file_location(
        "publish_service_branch_probe", ROOT / "scripts" / "publish-service.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_service_branch_probe"] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def _clone(tmp_path: Path, name: str = "downstream") -> Path:
    """A one-commit repo whose default branch is 'main'."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                   capture_output=True)
    _git(repo, "config", "user.email", "builder@example.invalid")
    _git(repo, "config", "user.name", "Builder")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


# --- branch_objection, both directions ---

def test_a_clone_on_main_raises_no_objection(pub, tmp_path):
    """The guard must not refuse the ordinary case; that is the whole run."""
    repo = _clone(tmp_path)
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    assert pub.branch_objection(repo) is None


def test_a_clone_on_a_side_branch_is_refused_by_name(pub, tmp_path):
    repo = _clone(tmp_path)
    _git(repo, "checkout", "-qb", "scratch")

    objection = pub.branch_objection(repo)

    assert objection is not None, (
        "the build commit would land on 'scratch' while the run pushes 'main'")
    assert "scratch" in objection and "main" in objection


def test_a_detached_head_is_refused(pub, tmp_path):
    """The objection must name DETACHMENT, not merely refuse.

    This asserted `"detached" in objection.lower()` until 2026-09-01, and that
    assertion could not fail. Every objection interpolates `dest`, and pytest
    derives `tmp_path` from the test function's own name, so the string
    `test_a_detached_head_is_refused0` sat inside every candidate message.
    MEASURED by mutation: renaming the guard's condition from
    `if branch == "HEAD"` to `if branch == "HEAD_NEVER"` drops a detached clone
    into the ordinary side-branch branch below it, whose message says nothing
    about detachment, and the whole 440-test scope around publish-service
    stayed green at 440 passed.

    `dest` is cut off the front before the message is read, so no part of the
    fixture path can satisfy the assertion, and the uppercase literal the
    detached branch alone emits is what is asked for.
    """
    repo = _clone(tmp_path)
    _git(repo, "checkout", "-q", "--detach")

    objection = pub.branch_objection(repo)

    assert objection is not None
    message = objection.replace(str(repo), "")
    assert "DETACHED HEAD" in message, message
    assert "is on branch" not in message, (
        "a detached clone fell through to the side-branch message, which never "
        f"tells the operator that HEAD points at no branch at all: {message}")


def test_a_directory_that_is_not_a_repo_is_refused_not_passed(pub, tmp_path):
    """rev-parse fails; an unreadable answer must never resolve to 'on main'."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    assert pub.branch_objection(plain) is not None


# --- main() refuses before it destroys anything ---

def _wire(pub, monkeypatch, tmp_path, repo: Path):
    """Point main() at `repo`, recording the two steps that must not run."""
    workspace = tmp_path / "workspace"
    (workspace / "config").mkdir(parents=True)
    config_dir = tmp_path / "dataconfig"
    config_dir.mkdir()
    (config_dir / "service-manifest.json").write_text(json.dumps({
        "include": ["scripts"],
        "exclude_names": [],
        "downstream_repo": repo.name,
    }), encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(pub, "get_workspace_root", lambda: workspace)
    monkeypatch.setattr(pub, "get_data_config_dir", lambda: config_dir)
    monkeypatch.setattr(pub, "copy_includes",
                        lambda *a, **k: calls.append("copy_includes"))
    monkeypatch.setattr(pub, "publish",
                        lambda *a, **k: (calls.append("publish"), 0)[1])
    monkeypatch.setattr(sys, "argv", ["publish-service.py"])
    return calls


def test_main_refuses_a_side_branch_before_it_rewrites_the_mirror(
        pub, monkeypatch, tmp_path):
    # The clone must be a SIBLING of the workspace: downstream_dest requires it.
    workspace_parent = tmp_path
    repo = _clone(workspace_parent)
    _git(repo, "checkout", "-qb", "scratch")
    calls = _wire(pub, monkeypatch, tmp_path, repo)

    rc = pub.main()

    assert rc == 1, "the run reported success while HEAD was 'scratch'"
    assert calls == [], (
        f"{calls} ran anyway; copy_includes rmtree's directory includes in the "
        f"clone, so a refusal after it is a refusal that already did the damage")


def test_main_proceeds_normally_when_the_clone_is_on_main(
        pub, monkeypatch, tmp_path):
    """The other direction: a guard that refuses everything is not a guard."""
    repo = _clone(tmp_path)
    calls = _wire(pub, monkeypatch, tmp_path, repo)

    rc = pub.main()

    assert rc == 0
    assert calls == ["copy_includes", "publish"]
