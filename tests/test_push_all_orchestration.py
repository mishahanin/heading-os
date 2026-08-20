"""Orchestration of push-all.py: which repos are attempted, and what a skip reports.

Created by the /scrutinize pass over the backup-per-repo-refusal slice, ahead of the
retirement step that was to create this file from the promoted contract tests. It
holds the coverage the frozen contract does not: the exec short-circuit, the
pre-cutover single-repo mode, and the closing summary's distinction between a run
that pushed something and a run that pushed nothing. Retirement appends the promoted
tests here rather than creating the file.

`push-all.py` is loaded BY PATH rather than imported, and that is not a style choice:
it calls `ensure_venv()` at module scope, so a plain import `os.execv`s the whole
pytest process under any interpreter that is not `.venv/bin/python`. Same load and
same reason as `tests/test_push_all_gate.py`.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "push_all_orchestration", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)


class _Args:
    def __init__(self, dry_run=False, no_commit=False):
        self.dry_run = dry_run
        self.no_commit = no_commit


def _code(fn):
    """fn()'s exit code, treating a clean return as 0."""
    try:
        fn()
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


# ============================================================
# The closing summary tells the truth about what was pushed
# ============================================================

def test_a_run_that_pushed_nothing_does_not_call_itself_partial(capsys):
    """The harm this guards: exit 3 with one repo attempted and one skipped is a
    backup that produced NO off-machine copy. Announcing it as "partial" with
    "everything that could be pushed was" is a false success claim about the only
    irreplaceable half of the workspace."""
    code = _code(lambda: push_all._report_skips(
        [("DATA", "branch is 'wip', expected 'main'")], _Args(), 1))
    out = capsys.readouterr().out

    assert code == 3
    assert "NOTHING PUSHED" in out
    assert "Partial" not in out
    assert "Everything that could be pushed was" not in out
    # the reason still has to travel, whatever the headline says
    assert "wip" in out


def test_a_genuinely_partial_run_still_says_partial_and_counts_both(capsys):
    code = _code(lambda: push_all._report_skips(
        [("ENGINE", "branch is 'feat/x', expected 'main'")], _Args(), 2))
    out = capsys.readouterr().out

    assert code == 3
    assert "Partial: 1 of 2" in out
    assert "NOTHING PUSHED" not in out
    assert "Everything that could be pushed was" in out
    assert "committed locally" in out


def test_the_committed_locally_claim_is_withheld_under_dry_run(capsys):
    """It is a claim about the disk, and a dry run wrote nothing to the disk."""
    _code(lambda: push_all._report_skips([("DATA", "branch is 'wip'")],
                                         _Args(dry_run=True), 1))
    out = capsys.readouterr().out

    assert "this was a dry run" in out
    assert "committed locally" not in out


def test_the_committed_locally_claim_is_withheld_under_no_commit(capsys):
    _code(lambda: push_all._report_skips([("ENGINE", "branch is 'feat/x'")],
                                         _Args(no_commit=True), 2))
    out = capsys.readouterr().out

    assert "still uncommitted" in out
    assert "committed locally" not in out


# ============================================================
# The exec short-circuit: the path least likely to be run by hand
# ============================================================

def _wire_exec(tmp_path, monkeypatch, raises=None):
    """main() on the exec path with the roots faked and push_repo recorded."""
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    calls = []

    def fake_push_repo(name, repo, message, do_commit, dry_run, push_env, **kw):
        calls.append((name, kw))
        if raises:
            raise push_all.RepoNotPushable(raises)

    monkeypatch.setattr(push_all, "push_repo", fake_push_repo)
    monkeypatch.setattr(push_all, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(push_all, "get_exec_data_root", lambda: data)
    monkeypatch.setattr(push_all, "is_exec_workspace", lambda: True)
    monkeypatch.setattr(push_all, "gh_token", lambda: "t")
    monkeypatch.setattr("sys.argv", ["push-all.py"])
    return calls


def test_the_exec_path_pushes_the_data_overlay_and_never_the_engine(
        tmp_path, monkeypatch, capsys):
    calls = _wire_exec(tmp_path, monkeypatch)

    assert _code(push_all.main) == 0
    assert [name for name, _kw in calls] == ["DATA"]
    # no suite gate: an exec's engine clone is pull-only, so there is no engine
    # push on this path and nothing to require a gate for
    assert "test_gate" not in calls[0][1]
    assert "Data overlay pushed." in capsys.readouterr().out


def test_the_exec_success_line_cannot_print_over_a_skip(
        tmp_path, monkeypatch, capsys):
    """A backup that says "Data overlay pushed." after pushing nothing is worse
    than one that fails loudly."""
    _wire_exec(tmp_path, monkeypatch, raises="branch is 'wip', expected 'main'")

    assert _code(push_all.main) == 3
    out = capsys.readouterr().out
    assert "Data overlay pushed." not in out
    assert "NOTHING PUSHED" in out


# ============================================================
# Pre-cutover single repo
# ============================================================

def _wire_single(tmp_path, monkeypatch, raises=None):
    """main() with the data root collapsed onto the engine root (pre-cutover)."""
    engine = tmp_path / "engine"
    engine.mkdir()
    calls = []

    def fake_push_repo(name, repo, message, do_commit, dry_run, push_env, **kw):
        calls.append((name, kw))
        if raises:
            raise push_all.RepoNotPushable(raises)

    monkeypatch.setattr(push_all, "push_repo", fake_push_repo)
    monkeypatch.setattr(push_all, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(push_all, "get_data_root", lambda: engine)
    monkeypatch.setattr(push_all, "is_exec_workspace", lambda: False)
    monkeypatch.setattr(push_all, "gh_token", lambda: "t")
    monkeypatch.setattr("sys.argv", ["push-all.py"])
    return calls


def test_the_pre_cutover_mode_pushes_one_repo_and_requires_the_suite_gate(
        tmp_path, monkeypatch):
    """This mode pushes the ENGINE clone to the engine remote with `is_engine`
    deliberately off, so the suite gate has to ride on `test_gate` to reach it."""
    calls = _wire_single(tmp_path, monkeypatch)

    assert _code(push_all.main) == 0
    assert [name for name, _kw in calls] == ["repo"]
    assert calls[0][1].get("test_gate") is True
    assert "is_engine" not in calls[0][1]


def test_a_skipped_pre_cutover_repo_reports_that_nothing_was_pushed(
        tmp_path, monkeypatch, capsys):
    _wire_single(tmp_path, monkeypatch, raises="branch is 'feat/x', expected 'main'")

    assert _code(push_all.main) == 3
    out = capsys.readouterr().out
    assert "NOTHING PUSHED" in out
    assert "Both repos pushed." not in out


# ============================================================
# The two-repo mode: what main() attempts, in what order
#
# Promoted from tests/contract/2026-07-30-backup-per-repo-refusal/, the frozen
# contract of the slice that introduced RepoNotPushable. Unchanged apart from
# this banner and one adaptation: the contract's own `_code()` took no argument
# and called main() itself, so each call below passes `push_all.main` to the
# `_code(fn)` already defined above rather than carrying a second copy of it.
# ============================================================

def _wire(tmp_path, monkeypatch, behaviour):
    """main() with both roots faked and push_repo recorded. Returns the call log.

    Each entry is (name, kwargs), so a test can assert on order and on which
    call sites carry test_gate without caring about paths.
    """
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    calls = []

    def fake_push_repo(name, repo, message, do_commit, dry_run, push_env, **kw):
        calls.append((name, kw))
        if name in behaviour:
            raise push_all.RepoNotPushable(behaviour[name])

    monkeypatch.setattr(push_all, "push_repo", fake_push_repo)
    monkeypatch.setattr(push_all, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(push_all, "get_data_root", lambda: data)
    monkeypatch.setattr(push_all, "is_exec_workspace", lambda: False)
    monkeypatch.setattr(push_all, "gh_token", lambda: "t")
    monkeypatch.setattr("sys.argv", ["push-all.py"])
    return calls


def test_both_repositories_pushed_exits_zero(tmp_path, monkeypatch):
    calls = _wire(tmp_path, monkeypatch, {})

    assert _code(push_all.main) == 0
    assert {name for name, _kw in calls} == {"ENGINE", "DATA"}


def test_data_is_attempted_before_the_engine(tmp_path, monkeypatch):
    """Measured, not aesthetic: the engine's pre-push hook runs the full suite
    inside the push and took 320 seconds on the machine this was written on. The
    data overlay is the only half that cannot be reconstructed, so it does not
    queue behind a several-minute gate that may fail, stall, or be interrupted."""
    calls = _wire(tmp_path, monkeypatch, {})
    _code(push_all.main)

    assert calls[0][0] == "DATA"


def test_a_skipped_engine_does_not_stop_the_data_push(tmp_path, monkeypatch):
    """THE defect the slice that wrote this test existed to close."""
    calls = _wire(tmp_path, monkeypatch,
                  {"ENGINE": "branch is 'feat/x', expected 'main'"})

    assert _code(push_all.main) == 3
    assert "DATA" in {name for name, _kw in calls}


def test_the_summary_names_the_skipped_repo_and_the_reason(
        tmp_path, monkeypatch, capsys):
    _wire(tmp_path, monkeypatch, {"ENGINE": "branch is 'feat/x', expected 'main'"})
    _code(push_all.main)

    out = capsys.readouterr().out
    assert "ENGINE" in out
    assert "feat/x" in out


def test_a_skipped_data_overlay_also_exits_three(tmp_path, monkeypatch):
    """Symmetry is not decoration: the rule is about repositories, not about the
    engine. A rule that only ever fires one way is a special case in disguise."""
    _wire(tmp_path, monkeypatch, {"DATA": "branch is 'wip', expected 'main'"})

    assert _code(push_all.main) == 3


def test_the_suite_gate_is_required_at_both_engine_pushing_call_sites(
        tmp_path, monkeypatch):
    """The two-repo ENGINE call and the pre-cutover single-repo call both reach
    the engine remote, so both must carry test_gate.

    Until 2026-08-20 this test also asserted that the DATA call carried NO gate,
    which was true and is no longer: the data overlay now has a gate of its own,
    running that overlay's tests through .githooks/pre-push-data. What still must
    not happen is DATA borrowing the ENGINE's marker, because that would demand
    the engine suite on a repository that has no engine in it.
    """
    calls = _wire(tmp_path, monkeypatch, {})
    _code(push_all.main)
    by_name = dict(calls)

    assert by_name["ENGINE"].get("test_gate") is True
    assert by_name["ENGINE"].get("gate_marker", push_all.ENGINE_GATE_MARKER) == \
        push_all.ENGINE_GATE_MARKER
    assert by_name["DATA"].get("test_gate") is True
    assert by_name["DATA"].get("gate_marker") == push_all.DATA_GATE_MARKER


def test_a_stop_the_world_exit_is_never_absorbed(tmp_path, monkeypatch):
    """RepoNotPushable is the ONLY thing main() catches. A SystemExit from a
    security refusal has to travel, or the change that introduced the type would
    have quietly converted every unbypassable wall into a skip."""
    _wire(tmp_path, monkeypatch, {})

    def exploding(name, *args, **kwargs):
        raise SystemExit(2)

    monkeypatch.setattr(push_all, "push_repo", exploding)
    assert _code(push_all.main) == 2
