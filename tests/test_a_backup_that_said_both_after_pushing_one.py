"""The closing headline said "Both repos pushed." after pushing one repo.

In pre-cutover mode the data root IS the engine root, `main()` announces
"Pushing one repo.", `attempted` is 1, and one repository is pushed. The success
line then claimed a second off-machine copy that does not exist. Every other
summary line in `push-all.py` is careful about exactly this -- `_report_skips`
branches its headline on `attempted` so a one-repo skip cannot read as "partial"
-- and the success line was the one that did not.

The harm is the same shape as the one `_report_skips` was built to prevent: this
command exists so the operator knows what left the machine, and a false plural
in the last line the run prints is a false claim about the backup's coverage.

Nothing here runs `git push`, `git commit`, or `push-all.py` as a program:
`push_repo` is replaced with a recording stub, so `main()` reaches its closing
line without a single subprocess. Wiring copied from
`tests/test_push_all_orchestration.py`, which loads the module the same way and
for the same reason -- `ensure_venv()` at module scope `os.execv`s the pytest
process on a plain import.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "push_all_headline", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)


@pytest.fixture(autouse=True)
def _reach_main(unguard_main_clone):
    """`main()`'s first statement is `require_main_clone(__file__)`, which exits
    2 from a worktree before the headline under test is printed. Neutralised on
    THIS loaded module, for the duration of one test.

    The guard keeps its own tests:
    `tests/test_guarded_entry_points_refuse_from_a_worktree.py` pins through the
    AST that the call is the first statement of `main()` and is passed
    `__file__`, and `tests/test_clone_guard.py` pins that it fires. This file
    owns the behaviour behind it.
    """
    unguard_main_clone(push_all)


def _code(fn):
    """fn()'s exit code, treating a clean return as 0."""
    try:
        fn()
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def _wire(tmp_path, monkeypatch, *, single: bool):
    """main() with the roots faked and push_repo recorded. Returns the call log.

    `single=True` collapses the data root onto the engine root, which is the
    pre-cutover mode. `-m` is passed so the default message never reads the host
    clock.
    """
    engine = tmp_path / "engine"
    engine.mkdir()
    data = engine
    if not single:
        data = tmp_path / "data"
        data.mkdir()
    calls = []

    def fake_push_repo(name, repo, message, do_commit, dry_run, push_env, **kw):
        calls.append(name)

    monkeypatch.setattr(push_all, "push_repo", fake_push_repo)
    monkeypatch.setattr(push_all, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(push_all, "get_data_root", lambda: data)
    monkeypatch.setattr(push_all, "is_exec_workspace", lambda: False)
    monkeypatch.setattr(push_all, "gh_token", lambda: "t")
    monkeypatch.setattr("sys.argv", ["push-all.py", "-m", "test backup"])
    return calls


def test_the_pre_cutover_mode_does_not_claim_a_second_repo(
        tmp_path, monkeypatch, capsys):
    calls = _wire(tmp_path, monkeypatch, single=True)

    assert _code(push_all.main) == 0
    out = capsys.readouterr().out

    # the premise: exactly one repository was pushed on this run
    assert calls == ["repo"]
    assert "Pushing one repo." in out
    # ...so the headline must not say two
    assert "Both repos pushed." not in out
    assert "Repo pushed." in out


def test_the_two_repo_mode_still_says_both(tmp_path, monkeypatch, capsys):
    """The other direction. A headline narrowed to "Repo pushed." everywhere
    would pass the test above while under-reporting the ordinary run."""
    calls = _wire(tmp_path, monkeypatch, single=False)

    assert _code(push_all.main) == 0
    out = capsys.readouterr().out

    assert calls == ["DATA", "ENGINE"]
    assert "Both repos pushed." in out


def test_a_dry_run_claims_no_push_in_either_mode(tmp_path, monkeypatch, capsys):
    """A dry run pushed nothing, so neither headline may appear."""
    _wire(tmp_path, monkeypatch, single=True)
    monkeypatch.setattr("sys.argv", ["push-all.py", "-m", "test", "--dry-run"])

    assert _code(push_all.main) == 0
    out = capsys.readouterr().out

    assert "dry-run complete." in out
    assert "Both repos pushed." not in out
    assert "Repo pushed." not in out
