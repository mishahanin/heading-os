"""Two push-time secret walls that ran at the wrong moment.

Neither let a secret off the machine. Both let one get further than the wall's
own description promises, and both had no backstop.

* The secret-FILENAME wall read ``git ls-files`` at step 1, i.e. only what git
  ALREADY tracked. Step 3 then runs ``git add -A``, which makes untracked files
  tracked, and the wall never ran again - so a credential this very run was
  about to commit was never tested by it. Three layers had to miss it and all
  three did: ``.gitignore`` carries ``.sessions/`` and one exact
  ``outputs/browser/cookies.json``, not a bare ``*.session`` or ``cookies.json``
  rule, and ``scripts/secret-scanner.py`` lists ``.session`` in
  ``SKIP_EXTENSIONS``, so the content scan returns clean for that file type.

* The secret-CONTENT scan ran at step 3.5, AFTER this script's own
  ``git add -A && git commit``. Nothing left the machine - the push was still
  refused - but by then the secret was in local history, so the repair was a
  history scrub instead of an edit. The two engine walls beside it have always
  run at step 0, and ``_push_delta_files``' docstring says that position is
  deliberate "so that a tree staged with --no-verify cannot slip past".
  ``scripts/publish-service.py`` already scans before its own ``git add -A``.

Run: python3 -m pytest tests/test_two_walls_that_looked_at_the_wrong_moment.py
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# push-all.py calls ensure_venv() at MODULE scope; tests/conftest.py sets the
# guard that stops it re-execing the pytest process.
_spec = importlib.util.spec_from_file_location("push_all_walls", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)

PUSH_ALL_SRC = (ROOT / "scripts" / "push-all.py").read_text(encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _write(repo: Path, rel: str, body: str = "x") -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ============================================================
# The filename wall that read the wrong set
# ============================================================

def _leaks(repo: Path) -> list[str]:
    """The step-1 refusal set, computed the way `push_repo` computes it."""
    return [
        f for f in push_all.repo_carried_paths(repo)
        if push_all.SECRET_TRACKED.search(f)
        and not f.endswith((".example", ".sample", ".template"))
    ]


def test_a_credential_this_run_would_commit_is_caught(tmp_path):
    """THE case. The file is untracked at step 1 and tracked by step 3.

    `git ls-files` returns nothing for it, so the old wall saw an empty set and
    passed, and `git add -A` committed it a moment later.
    """
    repo = _repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _write(repo, "telegram.session", "AUTH-TOKEN")   # never added

    assert "telegram.session" in _leaks(repo)


def test_the_old_set_really_did_miss_it(tmp_path):
    """Pins the test above by measuring what the previous implementation saw.

    Without this, a wall that caught nothing at all would satisfy the assertion
    below by accident, and there would be no evidence the change did anything.
    """
    repo = _repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _write(repo, "telegram.session", "AUTH-TOKEN")

    tracked = [f for f in push_all.run(
        ["git", "ls-files", "-z"], repo).stdout.split("\0") if f]

    assert "telegram.session" not in tracked
    assert "telegram.session" in push_all.repo_carried_paths(repo)


def test_an_untracked_cookies_file_is_caught(tmp_path):
    """`.gitignore` covers one exact `outputs/browser/cookies.json`, not the
    name anywhere else."""
    repo = _repo(tmp_path)
    _write(repo, "scratch/cookies.json", '{"session": "x"}')

    assert "scratch/cookies.json" in _leaks(repo)


def test_an_already_tracked_credential_is_still_caught(tmp_path):
    """A do-not-break guard: the case the wall was written for, a credential
    tracked long before this push."""
    repo = _repo(tmp_path)
    _write(repo, ".env", "TOKEN=x")
    _git(repo, "add", "-f", ".env")

    assert ".env" in _leaks(repo)


def test_an_ignored_credential_is_not_a_refusal(tmp_path):
    """The negative case. `.gitignore` keeps it out of the push, so refusing
    over it would block every backup on a machine that has one - which is how a
    wall gets switched off."""
    repo = _repo(tmp_path)
    _write(repo, ".gitignore", ".sessions/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _write(repo, ".sessions/telegram.session", "AUTH-TOKEN")

    assert _leaks(repo) == []


def test_a_template_is_not_a_refusal(tmp_path):
    """`.env.example` ships in the engine on purpose."""
    repo = _repo(tmp_path)
    _write(repo, ".env.example", "TOKEN=\n")

    assert _leaks(repo) == []


def test_an_ordinary_tree_is_not_a_refusal(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _write(repo, "docs/a.md", "prose\n")

    assert _leaks(repo) == []


def _steps_one_and_two(repo: Path):
    """Run `push_repo` far enough to exercise steps 1 and 2, and no further.

    Same driver shape as tests/test_a_credential_hidden_behind_a_quoted_path.py.
    `dry_run` and `do_commit=False` stop it before the commit and the push.
    """
    return push_all.push_repo("test", repo, "msg", do_commit=False, dry_run=True,
                              push_env={}, is_engine=False)


def test_an_untracked_memory_index_is_refused(tmp_path, capsys, monkeypatch):
    """Step 2 had the identical one-step-behind gap: it read `git ls-files`, so
    an UNTRACKED `.memory-index/` passed and `git add -A` tracked it three lines
    later.

    Driven through `push_repo` rather than asserted against the source. The
    first version of this test grepped the step-2 block for the word "carried"
    and a mutation that put `git ls-files` back SURVIVED it - because the
    comment I had written above the line says "carried" too. Third time in one
    night that my own prose satisfied my own grep.
    """
    repo = _repo(tmp_path)
    _write(repo, ".memory-index/index.db", "blob\n")   # never added
    monkeypatch.setattr(push_all, "content_scan", lambda _r, **_k: None)
    monkeypatch.setattr(push_all, "log_denial", lambda **_k: None)

    with pytest.raises(SystemExit) as exc:
        _steps_one_and_two(repo)

    assert exc.value.code == 2
    assert ".memory-index/" in capsys.readouterr().out


def test_a_tracked_memory_index_is_still_refused(tmp_path, capsys, monkeypatch):
    """A do-not-break guard: the case step 2 was written for."""
    repo = _repo(tmp_path)
    _write(repo, ".memory-index/index.db", "blob\n")
    _git(repo, "add", "-f", ".memory-index/index.db")
    monkeypatch.setattr(push_all, "content_scan", lambda _r, **_k: None)
    monkeypatch.setattr(push_all, "log_denial", lambda **_k: None)

    with pytest.raises(SystemExit) as exc:
        _steps_one_and_two(repo)

    assert exc.value.code == 2


def test_a_tree_with_no_index_passes_both_steps(tmp_path, monkeypatch):
    """The negative case for the pair. A wall that refuses everything refuses
    every backup."""
    repo = _repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    # A base commit so HEAD resolves. Without one the branch check further down
    # dies on `git rev-parse --abbrev-ref HEAD`, which says nothing about the
    # two steps under test.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    monkeypatch.setattr(push_all, "content_scan", lambda _r, **_k: None)
    monkeypatch.setattr(push_all, "log_denial", lambda **_k: None)

    # `push_repo` refuses further down (no remote, no armed gate); the point is
    # that it gets PAST steps 1 and 2 rather than exiting 2.
    with pytest.raises((push_all.RepoNotPushable, SystemExit)) as exc:
        _steps_one_and_two(repo)
    if isinstance(exc.value, SystemExit):
        assert exc.value.code != 2, "refused at step 1 or 2 over a clean tree"


def test_an_untracked_credential_is_refused_through_push_repo(tmp_path, capsys,
                                                              monkeypatch):
    """The filename wall, driven the same way. `_leaks` above computes the set;
    this proves `push_repo` acts on it."""
    repo = _repo(tmp_path)
    _write(repo, "telegram.session", "AUTH-TOKEN\n")   # never added
    monkeypatch.setattr(push_all, "content_scan", lambda _r, **_k: None)
    monkeypatch.setattr(push_all, "log_denial", lambda **_k: None)

    with pytest.raises(SystemExit) as exc:
        _steps_one_and_two(repo)

    assert exc.value.code == 2
    assert "telegram.session" in capsys.readouterr().out


def test_the_wall_reads_the_carried_set_in_the_script_itself():
    """The behaviour above is computed by a helper in this file, so it could
    drift from `push_repo`. This reads the real call."""
    assert "carried = repo_carried_paths(repo)" in PUSH_ALL_SRC
    assert 'tracked = [f for f in run(["git", "ls-files", "-z"], repo)' not in PUSH_ALL_SRC


# ============================================================
# The content scan that ran after the commit
# ============================================================

def _call_lines(name: str) -> list[int]:
    """Line numbers where `push_repo` calls `name`, from the parse tree."""
    tree = ast.parse(PUSH_ALL_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "push_repo")
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name]


def _commit_line() -> int:
    """The line where `push_repo` runs `git commit`."""
    tree = ast.parse(PUSH_ALL_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "push_repo")
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "run" and node.args):
            continue
        first = node.args[0]
        if isinstance(first, ast.List) and first.elts:
            words = [e.value for e in first.elts if isinstance(e, ast.Constant)]
            if words[:2] == ["git", "commit"]:
                return node.lineno
    raise AssertionError("no `git commit` call found in push_repo")


def test_the_content_scan_runs_before_the_commit():
    """THE case, read from the parse tree rather than the text.

    A string search would count the paragraph that DESCRIBES the old ordering,
    which this file's own comments now do at length.
    """
    scans = _call_lines("content_scan")

    assert len(scans) == 1, "the scan is duplicated or gone"
    assert scans[0] < _commit_line()


def test_the_engine_walls_still_run_before_the_commit():
    """The two that were already correct. Moving one wall must not move them."""
    commit = _commit_line()

    for name in ("engine_clean_scan", "engine_content_scan"):
        lines = _call_lines(name)
        assert lines, name
        assert all(ln < commit for ln in lines), name


def test_the_filename_wall_still_runs_before_the_commit():
    """It always did, and it must stay that way: it is the one that decides
    whether `git add -A` may run at all."""
    tree = ast.parse(PUSH_ALL_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "push_repo")
    wall = [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "repo_carried_paths"]

    assert wall
    assert max(wall) < _commit_line()


def test_the_commit_probe_can_find_a_commit():
    """Pins every ordering test. A probe that raised or returned 0 would make
    `scan < commit` trivially true or the whole file red for the wrong reason.
    """
    assert _commit_line() > 0


def test_the_scan_still_covers_untracked_files(tmp_path):
    """Moving the scan earlier is only safe because `_push_delta_files` includes
    untracked-not-ignored files. Without that leg, running before the commit
    would scan LESS than running after it."""
    repo = _repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _write(repo, "docs/brand-new.md", "prose\n")

    assert "docs/brand-new.md" in push_all._push_delta_files(repo)


def test_a_secret_in_content_refuses_before_anything_is_committed(tmp_path, monkeypatch):
    """End to end for the ordering: the refusal must happen with the tree still
    uncommitted, so the repair is an edit and not a history scrub."""
    repo = _repo(tmp_path)
    _write(repo, "scripts/leak.py", "TOKEN = 'x'\n")
    committed: list[str] = []

    def _fake_run(args, cwd, **_k):
        if args[:2] == ["git", "commit"]:
            committed.append(" ".join(args))
        return subprocess.run(["true"], capture_output=True, text=True)

    # The scanner refuses; everything else is inert.
    #
    # BYTES, not str. `_run_scanner` drives the scanner in bytes mode since
    # 2026-09-01, because the NUL-joined path list it writes to the child cannot
    # go through a text-mode pipe; it decodes the two streams itself. A
    # str-shaped double stands in for a call this code does not make, and the
    # decode raises AttributeError from inside the wall under test. The
    # assertion keeps the double honest if the mode ever moves again.
    def _refusing_scanner(*args, **kwargs):
        assert not (kwargs.get("text") or kwargs.get("encoding")
                    or kwargs.get("universal_newlines")), (
            "the scanner handoff is read as BYTES; this double returns bytes "
            "and cannot stand in for a text-mode call")
        return subprocess.CompletedProcess(args, 1, b"hit", b"")

    monkeypatch.setattr(push_all, "_push_delta_files",
                        lambda _r, **_k: {"scripts/leak.py"})
    monkeypatch.setattr(push_all.subprocess, "run", _refusing_scanner)

    with pytest.raises(SystemExit) as exc:
        push_all.content_scan(repo)

    assert exc.value.code == 2
    assert committed == []
