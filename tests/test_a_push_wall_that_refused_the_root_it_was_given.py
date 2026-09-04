#!/usr/bin/env python3
"""The push chokepoint decoded a repository PATH through subprocess text mode.

`git_push.enclosing_repo_root` is the guard every `supervised_push` runs before
it lets anything move, and six callers reach it. It asked git for the work-tree
root with `subprocess.run(..., text=True)` and compared the answer to
`repo.resolve()`. Both halves of that are wrong for a path that is not plain
ASCII, and the failure directions are opposite:

MEASURED 2026-09-01 against real scratch repositories on ext4:

    repo at /tmp/gpprobe/re\\rpo
        git stdout (bytes) : b'/tmp/gpprobe/re\\rpo\\n'
        enclosing_repo_root: PosixPath('/tmp/gpprobe/re\\npo')   != repo.resolve()

    repo at /tmp/gpprobe/re\\xffpo
        git stdout (bytes) : b'/tmp/gpprobe/re\\xffpo\\n'
        enclosing_repo_root: UnicodeDecodeError: 'utf-8' codec can't decode
                             byte 0xff in position 15

1. **A CR in the path is a FALSE REFUSAL.** Text mode turns on universal
   newlines and rewrites every CR byte to LF, so a genuine repository root came
   back spelled as a sibling that does not exist, compared unequal to itself,
   and `supervised_push` refused the operator's own backup with "it sits inside
   the repository at <a path with no file at it>". The module's own comment
   names this as the expensive direction: "a non-canonical answer would cause a
   false refusal".

2. **A non-UTF-8 byte in the path is a TRACEBACK.** `UnicodeDecodeError` is a
   `ValueError`, so `except (subprocess.SubprocessError, OSError)` walks past
   it. The function documents None as "could not establish", and instead it
   raised out of the universal chokepoint.

The AST sweep in `tests/test_a_reader_that_lost_a_byte_on_the_way_in.py` cannot
see this one: it fires on a literal `-z` argv carrying a token from
`PATH_SUBCOMMANDS`, and `rev-parse --show-toplevel` has neither. That file's
`--show-toplevel` blind spot is stated below rather than left implicit.

The fix is the shape that file names as the only correct one: no `text=`, then
`.stdout.decode("utf-8", "surrogateescape")`.

Nothing here reaches a network. Every repository is local, and no push wall is
removed to be tested - the refusal is asserted to still refuse.

Run: .venv/bin/python -m pytest \
     tests/test_a_push_wall_that_refused_the_root_it_was_given.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.git_push import enclosing_repo_root, supervised_push  # noqa: E402

EXOTIC = {
    "carriage-return": b"re\rpo",
    "not-utf8": b"re\xffpo",
    "newline": b"re\npo",
    "non-ascii-utf8": "\u0440\u0435\u043f\u043e".encode("utf-8"),
    # A leading or trailing SPACE is a legal path component, and `.strip()` on
    # git's answer renames it into a directory that does not exist - the same
    # corruption already fixed in `ops_signals._repo_uncommitted`.
    "leading-space": b" repo",
    "trailing-space": b"repo ",
}


@pytest.fixture(autouse=True)
def _supervisor_log_in_tmp_path(tmp_path, monkeypatch):
    """Real pushes in this file keep their supervisor log under `tmp_path`.

    `run_supervised` returns `verdict["log_path"]` so a human can open it after
    a push that went wrong. It therefore does not remove the log, and production
    must keep that. Under pytest nobody opens it and nothing removed it:
    MEASURED 2026-09-04 over a full `-n auto` run, 20 surviving
    `/tmp/supervise-*.log`, from the four test files that reach this call.

    Patched at `git_push.run_supervised`, not at each `supervised_push(...)`
    call site below. Two reasons and the second is the one that matters: most
    of those calls are refused by a wall before any child starts, so a
    `log_dir=` on each would be noise on the many to reach the few; and a call
    site is a place to forget, while a test added to this file tomorrow
    inherits this without knowing it exists.

    A test that installs its own `run_supervised` fake replaces this wrapper
    outright, which is correct: a fake spawns nothing and so leaks nothing.
    """
    from scripts.utils import git_push as _gp

    real = _gp.run_supervised

    def pinned(*a, **kw):
        # `is None`, NOT `setdefault`. `supervised_push` forwards `log_dir`
        # unconditionally, so the key is always PRESENT and always None unless
        # a caller set it; `setdefault` saw the key and did nothing, and the
        # first version of this fixture pinned nothing at all. Caught by the
        # leak guard itself, which still named all 20 logs after the "fix".
        if kw.get("log_dir") is None:
            kw["log_dir"] = str(tmp_path)
        return real(*a, **kw)

    monkeypatch.setattr(_gp, "run_supervised", pinned)


def _repo_named(base: Path, raw: bytes) -> Path:
    """A real git repository whose directory name is `raw`, or skip."""
    target = base / os.fsdecode(raw)
    try:
        target.mkdir(parents=True)
    except (OSError, ValueError):
        pytest.skip(f"{raw!r} is not a creatable directory name here")
    proc = subprocess.run(["git", "init", "-q", "-b", "main", str(target)],
                          capture_output=True)
    if proc.returncode != 0:
        pytest.skip(f"git refused to init at {raw!r}")
    return target


# ============================================================
# 1 - the root answers about itself, whatever it is called
# ============================================================

@pytest.mark.parametrize("raw", list(EXOTIC.values()), ids=list(EXOTIC))
def test_a_root_is_its_own_root_whatever_bytes_its_name_holds(tmp_path, raw):
    repo = _repo_named(tmp_path, raw)

    root = enclosing_repo_root(repo)

    assert root is not None, "a real repository resolved to unknown"
    assert root == repo.resolve(), (
        f"the root came back as {root!r} for a repository at {repo!r}; the guard "
        f"above `supervised_push` would refuse this repository as a subdirectory "
        f"of itself")


@pytest.mark.parametrize("raw", list(EXOTIC.values()), ids=list(EXOTIC))
def test_a_subdirectory_of_an_exotic_root_still_resolves_upward(tmp_path, raw):
    """The negative case ON the line: the guard must still see a subdirectory as
    one, or the whole finding this file guards would be traded for a blind spot.
    """
    repo = _repo_named(tmp_path, raw)
    sub = repo / "nested"
    sub.mkdir()

    assert enclosing_repo_root(sub) == repo.resolve()


# ============================================================
# 2 - and the chokepoint neither crashes nor falsely refuses
# ============================================================

def _local_remote(tmp_path: Path, work: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    for args in (["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "Test"],
                 ["remote", "add", "origin", str(remote)]):
        subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)
    (work / "f.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"],
                   check=True, capture_output=True)
    return remote


@pytest.mark.parametrize("raw", list(EXOTIC.values()), ids=list(EXOTIC))
def test_the_chokepoint_does_not_refuse_a_root_for_being_itself(tmp_path, raw):
    work = _repo_named(tmp_path, raw)
    remote = _local_remote(tmp_path, work)

    verdict = supervised_push(work, remote="origin", branch="main", stall_window=15)

    assert "not a git repository root" not in verdict.get("reason", ""), verdict
    assert verdict["state"] == "ok", verdict
    head = subprocess.run(["git", "-C", str(remote), "rev-parse", "main"],
                          capture_output=True, text=True, errors="replace")
    assert head.returncode == 0, "the push was refused or lost"


def test_the_chokepoint_still_refuses_a_subdirectory_of_an_exotic_root(tmp_path):
    """The wall is verified by making it REFUSE, never by removing it."""
    work = _repo_named(tmp_path, b"re\rpo")
    remote = _local_remote(tmp_path, work)
    sub = work / "nested"
    sub.mkdir()

    verdict = supervised_push(sub, remote="origin", branch="main", stall_window=15)

    assert verdict["state"] == "failed", verdict
    assert "not a git repository root" in verdict["reason"]
    head = subprocess.run(["git", "-C", str(remote), "rev-parse", "main"],
                          capture_output=True, text=True, errors="replace")
    assert head.returncode != 0, "the parent repository was pushed anyway"


# ============================================================
# 3 - the decode is deliberate, not inherited
# ============================================================

def test_the_root_reader_does_not_run_in_subprocess_text_mode():
    """The property, read off the source, because the behaviours above can also
    be satisfied on a filesystem that refuses the exotic names and skips."""
    import ast

    src = Path(ROOT / "scripts" / "utils" / "git_push.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "enclosing_repo_root")

    runs = [n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and "subprocess.run" in ast.unparse(n.func)]
    assert len(runs) == 1, f"{len(runs)} subprocess calls; this floor models one"

    kwargs = {kw.arg for kw in runs[0].keywords if kw.arg}
    assert not kwargs & {"text", "encoding", "universal_newlines"}, (
        f"the root reader is back in text mode ({sorted(kwargs)}); a CR in a "
        f"repository path becomes an LF and the root compares unequal to itself")
    assert "surrogateescape" in ast.unparse(fn), (
        "the deliberate decode is gone; git's raw path bytes are being guessed at")


def test_the_sibling_ast_sweep_still_cannot_see_this_reader():
    """State the blind spot rather than assume the other guard covers this.

    The sweep in `test_a_reader_that_lost_a_byte_on_the_way_in.py` fires only on
    a literal `-z` argv holding a `PATH_SUBCOMMANDS` token. `rev-parse
    --show-toplevel` has neither, so this reader was invisible to it - which is
    why the defect survived a repo-wide sweep written for exactly this class.
    """
    import ast
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cr_sweep_probe",
        ROOT / "tests" / "test_a_reader_that_lost_a_byte_on_the_way_in.py")
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)

    probe = ast.parse(
        "import subprocess\n"
        "def root(path):\n"
        "    return subprocess.run(['git', '-C', str(path), 'rev-parse',\n"
        "                           '--show-toplevel'], capture_output=True,\n"
        "                          text=True).stdout.strip()\n"
    )
    assert sweep._text_mode_dash_z_offenders(probe) == [], (
        "the sibling sweep learned to see `--show-toplevel`; fold this reader "
        "into it and delete this test")
