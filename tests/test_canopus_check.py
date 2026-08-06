"""The four clauses, each shown RED against a tree broken on purpose.

Every test here builds its own scratch repository under `tmp_path` and breaks
exactly one property, because a clause that has never been watched fail is a
clause nobody knows the calibration of. The naive implementation each test
refutes is named in its docstring, so a later reader can re-break the clause and
watch the same test go red rather than take this file's word for it.

Nothing in this file touches the engine repository: `git worktree add` runs
against the scratch repo, so a leftover worktree would be under `tmp_path` and
not in `git worktree list` here.
"""
import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

from scripts.utils.canopus_note import digest_text, write_note

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "canopus_check", ROOT / "scripts" / "canopus_check.py"
)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

SLUG = "widget-slice"
CONTRACT = "tests/contract/test_slice.py"
PROMOTED = "tests/test_widget.py"

# Red before the implementation exists and green after it, with the import
# INSIDE the test body: a module-scope import of an absent module is a
# collection error, and this contract has to be a test that runs and fails.
RED_CONTRACT = (
    "def test_widget_adds():\n"
    "    from widget import add\n"
    "    assert add(2, 2) == 4\n"
)
GREEN_CONTRACT = "def test_widget_adds():\n    assert 2 + 2 == 4\n"
SKIPPED_CONTRACT = (
    "import pytest\n"
    "\n"
    "\n"
    '@pytest.mark.skip(reason="parked")\n'
    "def test_widget_adds():\n"
    "    assert False\n"
)
IMPLEMENTATION = "def add(left, right):\n    return left + right\n"


def _git(repo: pathlib.Path, *argv: str, extra_env: dict | None = None) -> str:
    """git in *repo*, with every GIT_* variable out of the child environment.

    The scrub is `scripts/utils/canopus_git._child_env`'s, for its reason: this
    suite runs inside the engine's pre-push hook, and git exports GIT_DIR and
    GIT_INDEX_FILE to a hook, so an unscrubbed `git add -A` here would stage
    against the ENGINE's index instead of the fixture's.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(extra_env or {})
    proc = subprocess.run(["git", "-C", str(repo), *argv], check=False,
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {' '.join(argv)}: {proc.stderr.strip()}"
    return proc.stdout.strip()


def _init(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "builder@example.invalid")
    _git(repo, "config", "user.name", "Builder")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _write(repo: pathlib.Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: pathlib.Path, message: str, *, date: str | None = None) -> str:
    """Commit everything and return the abbreviated sha.

    Abbreviated, and never the full 40 characters: that is this repository's
    convention for a sha written into a file (config/canopus-genesis.json),
    because a 40-character hex string reads to the commit gate as a
    high-entropy secret and every way to silence that is forbidden here.
    """
    extra = {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else None
    _git(repo, "add", "-A", extra_env=extra)
    _git(repo, "commit", "-q", "-m", message, extra_env=extra)
    return _git(repo, "rev-parse", "--short=10", "HEAD")


def _fields(approval: str, **extra: str) -> dict:
    """The subset of a note the clauses read, as `read_note` would hand it over."""
    note = {"slug": SLUG, "approval_sha": approval, "contract": CONTRACT}
    note.update(extra)
    return note


def _clean_slice(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict]:
    """A repository where all four clauses hold, with the note committed."""
    repo = _init(tmp_path)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: the contract, red")
    _write(repo, "widget.py", IMPLEMENTATION)
    _commit(repo, "the implementation the contract was waiting for")
    write_note(repo, SLUG, {
        "slug": SLUG,
        "value": "one widget adds two numbers",
        "approval_sha": approval,
        "contract": CONTRACT,
        "plan_digest": digest_text("a synthetic plan"),
        "scrutinize_plan": "none surviving",
        "scrutinize_built": "none surviving",
        "undo": "revert the implementation commit",
    })
    _commit(repo, "record the slice")
    return repo, _fields(approval)


def test_C1_reports_when_the_contract_moved_after_approval(tmp_path):
    """Refutes: a C1 that compares the contract against itself, or not at all."""
    repo = _init(tmp_path)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: the contract, red")
    _write(repo, CONTRACT, RED_CONTRACT + "\n\ndef test_added_later():\n    assert True\n")
    _commit(repo, "the contract moved after it was approved")

    ok, message = cc.C1(repo, _fields(approval))

    assert not ok
    assert "contract" in message


def test_C1_is_silent_when_a_retired_note_names_its_retirement_commit(tmp_path):
    """The blocker's regression test: a shipped slice must not report forever.

    Refutes: a C1 that always diffs against HEAD. The workflow in force DELETES
    the contract directory when a slice ships, so against HEAD every retired
    slice reads as a moved contract, from the first shipped slice onward.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: the contract, red")
    _write(repo, "widget.py", IMPLEMENTATION)
    _commit(repo, "the implementation the contract was waiting for")
    _write(repo, PROMOTED, GREEN_CONTRACT)
    _commit(repo, "promote the contract id to the ordinary suite")
    _git(repo, "rm", "-r", "-q", "tests/contract")
    retired = _commit(repo, "retire the contract, keeping the id it held")
    _write(repo, "README.md", "a later commit, so HEAD is past the retirement\n")
    _commit(repo, "work that came after the slice shipped")

    ok, message = cc.C1(repo, _fields(approval, retired_sha=retired,
                                      promoted_to=PROMOTED))

    assert ok, message


def test_C2_reports_when_the_implementation_does_not_descend_from_the_approval(tmp_path):
    """Refutes: a C2 that compares timestamps instead of ancestry.

    The approval commit is dated 2020, so it PREDATES the head commit by every
    clock in the repository, and a "the approval is older, therefore fine"
    reading passes. The head commit sits on a root of its own and descends from
    nothing, which is the property that actually matters.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: the contract, red",
                       date="2020-01-01T00:00:00+00:00")
    _git(repo, "checkout", "-q", "--orphan", "other")
    _write(repo, "widget.py", IMPLEMENTATION)
    _commit(repo, "an implementation on a history of its own")

    ok, message = cc.C2(repo, _fields(approval))

    assert not ok
    assert approval in message


def test_C3_reports_when_the_contract_was_already_green_at_the_approval_sha(tmp_path):
    """Refutes: a C3 that runs the contract in the CHECKED-OUT tree.

    The implementation is already there when the contract is frozen, so the
    contract asserts nothing: it passed the moment it was approved. The tree
    then moves on and the contract goes red at HEAD, which is what makes this
    test discriminating — a clause reading the working tree finds red, calls the
    freeze sound, and certifies a contract that never defined anything. Only a
    run at the approval sha, in a worktree of its own, sees the green.
    """
    repo = _init(tmp_path)
    _write(repo, "widget.py", IMPLEMENTATION)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: a contract the code already satisfies")
    _git(repo, "rm", "-q", "widget.py")
    _commit(repo, "the implementation was dropped, so the contract is red at HEAD")

    ok, message = cc.C3(repo, _fields(approval))

    assert not ok
    assert "green" in message.lower()


def test_C4_reports_when_the_contract_collects_but_runs_nothing(tmp_path):
    """Refutes: a C4 that reads the exit code alone.

    Every test in this contract is skipped, so pytest COLLECTS it, reports it,
    and exits 0. Collected is not run, and the junit report is what tells the
    two apart.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, SKIPPED_CONTRACT)
    approval = _commit(repo, "approval: a contract nothing runs")

    ok, message = cc.C4(repo, _fields(approval))

    assert not ok
    assert "ran no tests" in message


def test_C4_reports_when_a_retired_note_names_a_promoted_file_that_no_longer_runs(tmp_path):
    """Refutes: a C4 that checks the CONTRACT for a retired slice.

    The contract is gone by construction once a slice ships, so the only thing
    left to hold is the promotion target, and here it was dropped after the
    retirement.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: the contract, red")
    _write(repo, "widget.py", IMPLEMENTATION)
    _write(repo, PROMOTED, GREEN_CONTRACT)
    _commit(repo, "promote the contract id to the ordinary suite")
    _git(repo, "rm", "-r", "-q", "tests/contract")
    retired = _commit(repo, "retire the contract, keeping the id it held")
    _git(repo, "rm", "-q", PROMOTED)
    _commit(repo, "the promoted coverage was dropped")

    ok, message = cc.C4(repo, _fields(approval, retired_sha=retired,
                                      promoted_to=PROMOTED))

    assert not ok
    assert PROMOTED in message


def test_a_well_formed_note_over_clean_history_reports_nothing(tmp_path):
    repo, note = _clean_slice(tmp_path)

    for clause in (cc.C1, cc.C2, cc.C3, cc.C4):
        ok, message = clause(repo, note)
        assert ok, f"{clause.__name__}: {message}"

    assert cc.main(["--root", str(repo)]) == 0


def test_main_exits_one_and_names_the_slice_when_a_clause_reports(tmp_path, capsys):
    repo, _note = _clean_slice(tmp_path)
    _write(repo, CONTRACT, GREEN_CONTRACT)
    _commit(repo, "the contract moved after the slice was recorded")

    status = cc.main(["--root", str(repo)])

    assert status == 1
    assert SLUG in capsys.readouterr().out


def test_the_range_keeps_the_expensive_clauses_off_untouched_notes(tmp_path, capsys):
    """The cost bound: C3 and C4 spawn a worktree and a test run, so an empty
    push range must leave every note carrying C1 and C2 alone."""
    repo, _note = _clean_slice(tmp_path)
    head = _git(repo, "rev-parse", "--short=10", "HEAD")

    status = cc.main(["--root", str(repo), "--range", f"{head}..{head}", "--json"])

    assert status == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["clause"] for row in rows} == {"C1", "C2"}


def test_a_note_missing_the_fields_the_clauses_read_is_reported_not_raised(tmp_path):
    repo = _init(tmp_path)
    (repo / "records" / "slices").mkdir(parents=True)
    (repo / "records" / "slices" / "broken.md").write_text(
        "---\nslug: broken\n---\n", encoding="utf-8"
    )
    _commit(repo, "a note nobody can check")

    assert cc.main(["--root", str(repo)]) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
