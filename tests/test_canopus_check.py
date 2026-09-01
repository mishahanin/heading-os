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

    The scrub is `canopus_check.git_child_env`'s, for its reason: this
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
    convention for a sha written into a file, carried by `_SHA` in
    scripts/utils/canopus_note.py, because a 40-character hex string reads to
    the commit gate as a high-entropy secret and every way to silence that is
    forbidden here.
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


def test_C3_reports_when_the_contract_collected_nothing_at_the_approval(tmp_path):
    """Refutes: a C3 that reads the exit code alone.

    Found by the 2026-08-23 audit. C3 called `_pytest` and threw the junit
    report away (`code, _xml = _pytest(...)`), then treated ANY non-zero exit as
    proof the contract was red. Pytest exits 5 when it collects nothing and 2 on
    a collection error, so a contract that did not exist at the approval sha, or
    that failed to import there, certified the freeze as sound.

    That is the exact "collected is not run" trap the module already documents
    for C4, and C4 already refuses it — the two clauses disagreed about what a
    measurement is.

    Here the contract file is added by a LATER commit, so at the approval sha
    the path does not exist: pytest exits 5, having measured nothing.
    """
    repo = _init(tmp_path)
    _write(repo, "widget.py", IMPLEMENTATION)
    approval = _commit(repo, "approval: recorded before the contract was written")
    _write(repo, CONTRACT, RED_CONTRACT)
    _commit(repo, "the contract, written after the approval it claims")

    ok, message = cc.C3(repo, _fields(approval))

    assert not ok, (
        "a contract that did not exist at its own approval sha was certified "
        "as having been red there"
    )
    assert "ran no tests" in message or "no tests" in message, message


def test_C3_reports_when_every_test_was_skipped_at_the_approval(tmp_path):
    """The other half of the same trap: collected, reported, never executed.

    Pytest exits 0 here, so this one was already caught by the green branch —
    but for the wrong reason, and the message said "already GREEN" about a
    contract that was never run. The distinction matters because the operator
    reads the message and goes looking for an implementation that does not exist.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, SKIPPED_CONTRACT)
    approval = _commit(repo, "approval: a contract nothing runs")

    ok, message = cc.C3(repo, _fields(approval))

    assert not ok
    assert "ran no tests" in message or "no tests" in message, message


def test_C3_still_accepts_a_contract_that_really_was_red(tmp_path):
    """The mutation guard. The fix must not reject a sound freeze."""
    repo = _init(tmp_path)
    _write(repo, CONTRACT, RED_CONTRACT)
    approval = _commit(repo, "approval: the contract, red, with nothing to satisfy it")

    ok, message = cc.C3(repo, _fields(approval))

    assert ok, message
    assert "red" in message


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


def test_a_run_with_no_note_at_all_says_it_checked_nothing(tmp_path, capsys):
    """Measured 2026-08-07: `records/slices/` held only `.gitkeep`, so the CI
    step printed `0 clause(s) over 0 note(s); 0 report(s)` and exited 0. Nothing
    in that line, or in the green tick above it, separated a check that ran and
    held from a check that had nothing to run against.

    The exit code stays 0 deliberately. A repository with no open slice is the
    ordinary state, not an error; what is owed is the DISTINCTION, not a
    failure.
    """
    repo = _init(tmp_path)
    _write(repo, "widget.py", IMPLEMENTATION)
    _commit(repo, "a repository with code but no slice note at all")

    status = cc.main(["--root", str(repo)])

    assert status == 0
    out = capsys.readouterr().out
    assert "NOTHING WAS CHECKED" in out
    assert "0 clause(s) over 0 note(s)" not in out, (
        "the empty run still renders as a completed one")


def test_the_json_payload_says_it_checked_nothing_too(tmp_path, capsys):
    """The machine-readable half of the same distinction.

    `[]` is what a caller also gets from a run that checked four clauses and
    found nothing wrong on zero notes, so the payload has to carry the reason
    rather than leave it to the human line. It stays a LIST OF ROWS, so a caller
    iterating rows keeps working; `clause == "scope"` is what marks it, and no
    clause is named that.
    """
    repo = _init(tmp_path)
    _write(repo, "widget.py", IMPLEMENTATION)
    _commit(repo, "a repository with code but no slice note at all")

    status = cc.main(["--root", str(repo), "--json"])

    assert status == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["clause"] for row in rows] == ["scope"]
    assert rows[0]["ok"] is True, "an empty repository is not a failing one"
    assert "NOTHING WAS CHECKED" in rows[0]["message"]


def test_a_run_that_did_check_something_does_not_claim_it_checked_nothing(
    tmp_path, capsys
):
    """The other side of the distinction, and the reason it is a pair.

    A message that appeared on every run would be noise nobody reads. This
    asserts the clean run still renders as a COUNT of what it weighed, and never
    borrows the empty run's sentence.
    """
    repo, _note = _clean_slice(tmp_path)

    status = cc.main(["--root", str(repo)])

    assert status == 0
    out = capsys.readouterr().out
    assert "NOTHING WAS CHECKED" not in out
    assert "note(s)" in out and "report(s)" in out
    assert "0 clause(s)" not in out, "a clean run weighed at least one clause"


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


@pytest.mark.parametrize("shape", ["", "..HEAD", "0" * 40 + "..HEAD"])
def test_a_push_range_that_names_no_push_scopes_to_nothing_and_does_not_error(
    tmp_path, capsys, shape
):
    """The three shapes CI really produces, none of which may read as a failure.

    `github.event.before` is EMPTY on a pull_request and on workflow_dispatch,
    and forty zeros on the first push to a new branch, so the workflow's
    `${{ github.event.before }}..${{ github.sha }}` expands to one of these. The
    null-sha shape is the one that used to matter: `git rev-list` exits 128 on
    it, which reached the operator as a report against the slice rather than as
    what it is -- a range that names no push.

    Scoping to NOTHING is the deliberate answer, and it is not the same as
    passing no `--range` at all: the flag being present says "scope me to a
    push", so an unresolvable push scopes the expensive clauses to nothing,
    while its absence stays the whole-history local reading that runs them all.
    """
    repo, _note = _clean_slice(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")

    status = cc.main(["--root", str(repo), "--range", shape.replace("HEAD", head),
                      "--json"])

    assert status == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["clause"] for row in rows} == {"C1", "C2"}


def test_without_a_range_main_runs_all_four_clauses_over_every_note(tmp_path, capsys):
    """The whole-history local reading, which nothing asserted.

    `test_the_range_keeps_the_expensive_clauses_off_untouched_notes` and its
    parametrised sibling both pin the NEGATIVE: an empty or unresolvable
    `--range` leaves C3 and C4 off. Neither has a positive control, so
    `_in_range` answering False for the no-range case (`scope is None`) was
    invisible: MEASURED 2026-09-01, flipping that one `return True` to
    `return False` left all 27 tests in this file green while every local run
    and every clean CI run silently dropped the two clauses that spawn a
    worktree and a test run. The module docstring calls this reading
    "deliberately the slow one"; a check that got quietly fast is a check that
    stopped being taken.
    """
    repo, _note = _clean_slice(tmp_path)

    status = cc.main(["--root", str(repo), "--json"])

    assert status == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["clause"] for row in rows} == {"C1", "C2", "C3", "C4"}, (
        "a run with no --range did not weigh the expensive clauses, so the "
        "local whole-history reading measured less than the CI one")


def test_C4_reports_a_target_whose_run_exited_non_zero_with_every_test_green(
    tmp_path,
):
    """`red or code != 0`, and nothing ever reached the second half.

    MEASURED 2026-09-01: cutting C4 down to `if red:` left this file and
    `tests/test_canopus_cli.py` green over 61 tests. The exit code is not
    belt-and-braces here, and this repository is the proof: its own root
    `conftest.py` sets a non-zero `exitstatus` in `pytest_sessionfinish` when the
    operator's live overlay moved during a run. A session hook, a plugin, or a
    `-W error` teardown can all end a run non-zero while every testcase in the
    report says `passed`, and a target whose RUN failed has not certified
    anything, whatever its individual cases say.

    The conftest below is the same shape, planted in the contract's own
    directory so `_pytest` picks it up from the target's parent.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, GREEN_CONTRACT)
    _write(repo, "tests/contract/conftest.py",
           "def pytest_sessionfinish(session, exitstatus):\n"
           "    session.exitstatus = 1\n")
    approval = _commit(repo, "approval: a contract whose RUN fails while its tests pass")

    ok, message = cc.C4(repo, _fields(approval))

    assert not ok, (
        "a run that exited non-zero certified the target because every "
        "individual testcase was green")
    assert "not green at HEAD" in message, message


def test_C4_measures_a_contract_recorded_as_a_DIRECTORY(tmp_path):
    """`_ran`'s prefix branch, which is the only thing that reads a directory.

    A note's `contract` is a directory in this repository's own convention --
    `tests/contract/2026-01-02-sample-slice/` is what `valid()` carries in
    `tests/test_canopus_note.py`. Every clause test here used a FILE, so
    `rel == target` answered them all and the `rel.startswith(prefix)` branch
    was never taken: MEASURED 2026-09-01, deleting it left 61 tests green.

    The consequence is a false report, not a missed one. With no prefix match
    `_ran` returns 0 for a directory target, and C4's first branch then says
    "ran no tests at HEAD" about a directory whose tests all ran and passed --
    the clause reporting against a slice that is behaving perfectly, which is
    exactly the forever-reporting failure C1's retirement window exists to
    remove.
    """
    repo = _init(tmp_path)
    _write(repo, CONTRACT, GREEN_CONTRACT)
    approval = _commit(repo, "approval: a contract recorded as a directory")

    directory = _fields(approval)
    directory["contract"] = "tests/contract/"

    ok, message = cc.C4(repo, directory)

    assert ok, message
    assert "ran 1 test(s) green at HEAD" in message, message


def test_a_note_this_repository_cannot_decode_is_reported_not_raised(tmp_path, capsys):
    """One unreadable note is one report, never the end of the run.

    `main` takes `note_paths()` as its ENTIRE population and catches exactly
    `(NoteError, CheckError)`. Until 2026-09-01 `read_note` let
    `UnicodeDecodeError` (a `ValueError`, a SIBLING of the `OSError` it caught)
    and `yaml.YAMLError` (caught nowhere) escape, so a single hand-edited note
    in the wrong encoding did not get reported -- it ended the check for every
    OTHER note, on a traceback naming a codec and an offset but no path.

    The good note is committed alongside, and its clause verdicts are asserted,
    because "the run survived" is not the property: the property is that the
    remaining notes were still weighed.
    """
    repo, _note = _clean_slice(tmp_path)
    (repo / "records" / "slices" / "undecodable.md").write_bytes(
        b"---\nslug: undecodable\nvalue: caf\xe9 latte\n---\n")
    (repo / "records" / "slices" / "unparseable.md").write_text(
        "---\nslug: unparseable\nvalue: [unclosed\n---\n", encoding="utf-8")
    _commit(repo, "two notes nobody can read, beside one that is fine")

    status = cc.main(["--root", str(repo), "--json"])

    assert status == 1
    rows = json.loads(capsys.readouterr().out)
    by_slug: dict = {}
    for row in rows:
        by_slug.setdefault(row["slug"], []).append(row)

    for broken in ("undecodable", "unparseable"):
        assert [r["clause"] for r in by_slug.get(broken, [])] == ["note"], (
            f"{broken} was not reported as one unreadable note: {by_slug}")
        assert broken in by_slug[broken][0]["message"]

    # The property: the OTHER notes were still weighed, all four clauses each.
    assert {r["clause"] for r in by_slug.get(SLUG, [])} == {"C1", "C2", "C3", "C4"}
    assert all(r["ok"] for r in by_slug[SLUG]), by_slug[SLUG]


def test_a_note_missing_the_fields_the_clauses_read_is_reported_not_raised(tmp_path):
    repo = _init(tmp_path)
    (repo / "records" / "slices").mkdir(parents=True)
    (repo / "records" / "slices" / "broken.md").write_text(
        "---\nslug: broken\n---\n", encoding="utf-8"
    )
    _commit(repo, "a note nobody can check")

    assert cc.main(["--root", str(repo)]) == 1


# ============================================================
# git_child_env -- ported with the function itself (K6, 2026-08-07)
# ============================================================
#
# `_child_env` lived in the freeze lifecycle's git helper and this module
# imported the PRIVATE name across the module boundary. That helper was deleted
# with the rest of the lifecycle and the function moved here.
#
# No test in the helper's own file named `_child_env` as its subject. Two named
# `repo_identity`, and pinned the scrub INDIRECTLY by poisoning one
# variable at a time and asserting the identity was unchanged; `repo_identity`
# is deleted, so the indirection has nowhere to land. What is ported is the
# claim those two were making, asserted on the function that now holds it: the
# guard is a PREFIX over the family, not a denylist of the two variables that
# happen to be famous. A denylist naming GIT_DIR and GIT_WORK_TREE passed both
# of the originals and fails this.

POISONS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_INDEX_FILE",
    "GIT_CEILING_DIRECTORIES",
    # Not a real git variable. A prefix scrub removes it; a denylist of the
    # names somebody thought of cannot, and the next release of git may add it.
    "GIT_A_VARIABLE_GIT_HAS_NOT_INVENTED_YET",
)


@pytest.mark.parametrize("variable", POISONS)
def test_no_single_git_variable_reaches_the_child(variable, monkeypatch):
    monkeypatch.setenv(variable, "/nowhere")
    monkeypatch.setenv("X31C_TRACE_ID", "trace-for-the-child")

    child = cc.git_child_env()

    assert variable not in child
    assert not [key for key in child if key.startswith("GIT_")]
    # The scrub is a prefix, never an emptying: a child with no environment at
    # all would satisfy the line above and break every caller.
    assert child.get("X31C_TRACE_ID") == "trace-for-the-child"


def test_the_clauses_run_git_through_the_scrubbed_environment(tmp_path, monkeypatch):
    """The measured reason the scrub exists, end to end.

    This suite runs inside the engine's pre-commit and pre-push hooks, and git
    exports GIT_DIR and GIT_INDEX_FILE to a hook. An unscrubbed child would ask
    the HOOK's repository about a note's approval sha, so a clean slice in a
    scratch tree would answer from the wrong history.
    """
    repo, _note = _clean_slice(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "nowhere"))

    assert cc.main(["--root", str(repo)]) == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
