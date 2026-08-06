"""Tests for the Canopus CLI: `note`, `check`, and `probe`.

The lifecycle this file used to exercise - approve, freeze, verify, status,
release, repin, pack, where - was deleted on 2026-08-07 along with the freeze
manifest it read. What survives is the reading `probe` produces over a contract
whose implementation does not exist yet, and every assertion below is about that
reading rather than about a lock.

`note` and `check` came back on 2026-08-07 because the skill promised both and
the tool offered neither. Both are thin, so the tests below assert what an
operator can SEE at the command surface - the file that appears on disk, the
refusal sentence, the exit code, the clause rows - and never that a particular
function was called. Renaming anything inside `canopus_note` or `canopus_check`
must not move a single assertion here.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

import scripts.canopus as canopus
from scripts.canopus import main
from scripts.utils.canopus_note import digest_text, write_note


def _make_tree(root: Path) -> Path:
    """A synthetic working tree to run a scratch contract inside."""
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    return root


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    root = _make_tree(tmp_path / "tree")
    monkeypatch.chdir(root)
    return root


def _run(argv, tree):
    # --root is a top-level option, so it must precede the subcommand.
    return main(["--root", str(tree), *argv])


# A complete, schema-valid record. Every `note` test starts from this and breaks
# exactly one thing, so a refusal names the property under test and nothing else.
_PLAN_DIGEST = digest_text("the plan this slice was approved on")
_FIELDS = {
    "value": "the skill promised two subcommands the tool did not have",
    "approval_sha": "abc1234",
    "contract": "tests/contract/2026-08-07-widget/",
    "plan_digest": _PLAN_DIGEST,
    "scrutinize_plan": "3 findings, all applied",
    "scrutinize_built": "1 finding, applied",
    "undo": "revert abc1234 and re-run the suite",
}


def _note_argv(slug="widget", **overrides):
    """`note <slug> ...` with every schema-required flag, plus any override."""
    fields = dict(_FIELDS, **overrides)
    argv = ["note", slug]
    for name, value in fields.items():
        argv += [f"--{name.replace('_', '-')}", value]
    return argv


def _git(repo: Path, *argv: str) -> str:
    """git in *repo*, with every GIT_* variable out of the child environment.

    The scrub is `canopus_check.git_child_env`'s, for its reason: this suite runs
    inside the engine's pre-push hook, and git exports GIT_DIR and GIT_INDEX_FILE
    to a hook, so an unscrubbed `git add` here would stage against the ENGINE's
    index instead of this fixture's.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    proc = subprocess.run(["git", "-C", str(repo), *argv], check=False,
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {' '.join(argv)}: {proc.stderr.strip()}"
    return proc.stdout.strip()


@pytest.fixture
def repo(tree):
    """`tree` as a git repository holding one commit, and that commit's sha.

    The clauses read git, so `check` has nothing to answer without one.
    """
    _git(tree, "init", "-q", "-b", "main")
    _git(tree, "config", "user.email", "builder@example.invalid")
    _git(tree, "config", "user.name", "Builder")
    _git(tree, "config", "commit.gpgsign", "false")
    contract = tree / "tests" / "contract" / "test_widget.py"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text("def test_widget():\n    assert True\n")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "approve the widget contract")
    # Abbreviated, never the full 40 characters: this repository's own
    # convention, because a full sha reads to detect-secrets as a hex
    # high-entropy string and every way to silence that is forbidden here.
    return tree, _git(tree, "rev-parse", "--short=7", "HEAD")


def _record(root: Path, sha: str, **overrides) -> None:
    """Commit-free: write one schema-valid note straight into the scratch tree."""
    write_note(root, "widget", dict(
        _FIELDS, slug="widget", approval_sha=sha,
        contract="tests/contract/test_widget.py", **overrides))


# The range that scopes C3 and C4 to nothing. Both spawn a worktree and a pytest
# run, and neither is what these tests are about: what is under test here is
# whether the subcommand reaches the clauses at all and carries its flags there.
_NO_RANGE = "0000000..HEAD"


def _write_contract(tree, red=True):
    """Two tests: one red under `red=True`, one green either way.

    The red one dies on an ABSENT import and then asserts a value no stand-in can
    satisfy, so it is red for real, red under both stub value sets, and therefore
    never vacuous. The absent import is load-bearing rather than decoration: a
    contract whose source names no module at all is one the vacuity probe now
    refuses, because with no name to stand in for, nothing is stubbed and nothing
    is measured. `red=False` keeps its import-free all-green shape, which
    `refusal_reasons` owns and the probe is never reached for.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    # The `SC-1` docstring is left as it was written: it names the success
    # criterion the test claims, which is the shape a real contract carries.
    first = ('def test_a():\n'
             '    """SC-1."""\n'
             "    from absent_thing import answer\n"
             "    assert answer() == 42\n"
             if red else
             'def test_a():\n    """SC-1."""\n    assert True\n')
    (directory / "test_contract.py").write_text(
        first + "\n\ndef test_b():\n    assert True\n"
    )
    return directory


def _write_vacuous_contract(tree: Path, real: bool = False) -> Path:
    """A contract whose tests all die on an absent import.

    With real=True one of them asserts a value a MagicMock cannot satisfy, so it
    stays red under the stub and the contract is not wholly vacuous.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    second = "assert answer() == 42" if real else "assert answer() is not None"
    (directory / "test_contract.py").write_text(
        'def test_vacuous():\n'
        '    """SC-1."""\n'
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_other():\n"
        "    from absent_thing import answer\n"
        f"    {second}\n"
    )
    return directory


def _write_conftest_fixture_contract(tree) -> Path:
    """Escape shape (a): the contract's only absent import is in its conftest.

    Building the subject in a fixture is ordinary pytest, and the AST reader
    globbed `test_*.py` only, so the claim set came back empty, nothing was
    stubbed, and the probe returned a verdict it had never taken. Measured
    through this CLI before the fix: `probe` exited 0 printing no vacuity word at
    all.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conftest.py").write_text(
        "import pytest\n"
        "\n\n"
        "@pytest.fixture\n"
        "def widget():\n"
        "    from absent_thing import Widget\n"
        "    return Widget()\n"
    )
    (directory / "test_contract.py").write_text(
        'def test_widget_exists(widget):\n'
        '    """SC-1."""\n'
        "    assert widget is not None\n"
    )
    return directory


def _write_importless_contract(tree) -> Path:
    """Escape shape (b): a red contract whose source names no module at all.

    Nothing is stubbed, so the probe measures nothing, and the empty set it
    returned was indistinguishable from a completed measurement that found
    nothing vacuous. Measured before the fix: `probe` exited 0.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_contract.py").write_text(
        'def test_a():\n    """SC-1."""\n    assert 1 == 2\n')
    return directory


def _population_spy(monkeypatch) -> list[dict]:
    """Capture the keyword arguments every `run_null_stub` call was given.

    Asserted at the CALL rather than by reading the source, because
    `expected_population` carries a default: a caller that stops passing it
    raises nothing, fails no type check, and silently sends `run_null_stub` off
    to run its OWN unstubbed baseline. The probe's lost-test guard then measures
    one pytest session while the caller applies the verdict to another, and
    nothing holds the two populations equal.
    """
    seen: list[dict] = []

    def _spy(paths, root, **kwargs):
        seen.append(kwargs)
        return set()

    monkeypatch.setattr(canopus, "run_null_stub", _spy)
    return seen


_REAL_POPULATION = [
    ("tests/contract/slice/test_contract.py", "test_a", "failure"),
    ("tests/contract/slice/test_contract.py", "test_b", "passed"),
]


def test_a_bare_invocation_prints_help_naming_every_subcommand(capsys):
    """Non-zero, and the whole help rather than a one-line usage string.

    The help is the only place the three subcommands are enumerated to an
    operator who typed the command with nothing after it, so a subparser that was
    written but never registered shows up here and nowhere else.
    """
    assert main([]) == 2
    err = capsys.readouterr().err
    assert all(name in err for name in ("note", "check", "probe"))


def test_check_carries_the_json_flag_to_the_clauses(repo, capsys):
    """--json has to REACH the clauses, not merely be accepted by this parser.

    A flag parsed here and dropped on the way through leaves the operator reading
    the text listing while their tooling waits for JSON, and nothing errors.
    """
    root, sha = repo
    _record(root, sha)

    _run(["check", "--range", _NO_RANGE, "--json"], root)

    rows = json.loads(capsys.readouterr().out)
    assert {row["clause"] for row in rows} == {"C1", "C2"}
    assert all(row["slug"] == "widget" for row in rows)


def test_check_carries_the_range_flag_to_the_clauses(repo, capsys):
    """Same passthrough property, on the flag that decides what the run COSTS.

    A dropped --range does not fail: it silently widens the run to every note's
    expensive clauses, which is a worktree and a full pytest session per slice.
    The range named here scopes those to nothing and says so, so the sentence is
    the evidence the flag arrived.
    """
    root, sha = repo
    _record(root, sha)

    _run(["check", "--range", _NO_RANGE], root)

    assert "names no push range" in capsys.readouterr().err


def test_check_reports_a_note_whose_approval_is_not_in_the_history(repo, capsys):
    """The clauses are RUN, not merely reached: this one has to come back red.

    `0123456` is a well-formed abbreviated sha that names no commit in the
    scratch repository, so the record claims an approval the history does not
    carry. A subcommand that printed a listing and always exited 0 would pass the
    green test above and fail this one.
    """
    root, sha = repo
    _record(root, sha)
    write_note(root, "widget", dict(
        _FIELDS, slug="widget", approval_sha="0123456",
        contract="tests/contract/test_widget.py"))

    assert _run(["check", "--range", _NO_RANGE], root) == 1
    assert "C2" in capsys.readouterr().out


def test_check_runs_the_clauses_over_a_committed_note(repo, capsys):
    """A record whose approval IS the history reads clean, and exits 0."""
    root, sha = repo
    _record(root, sha)

    assert _run(["check", "--range", _NO_RANGE], root) == 0
    out = capsys.readouterr().out
    assert "C1" in out and "C2" in out and "widget" in out


def test_note_reads_a_record_back(tree, capsys):
    """--show is the cheap half of the round trip: what was written comes back."""
    _run(_note_argv(), tree)
    capsys.readouterr()

    assert _run(["note", "widget", "--show"], tree) == 0
    out = capsys.readouterr().out
    assert f"value: {_FIELDS['value']}" in out
    assert f"approval_sha: {_FIELDS['approval_sha']}" in out


def test_note_records_a_retirement_that_names_where_the_coverage_went(tree):
    """The retirement pair is written, and it is written as a PAIR.

    A note carrying `retired_sha` without `promoted_to` is the shape the schema
    refuses, and the flags for both exist only because they are derived from that
    schema rather than typed out by hand.
    """
    code = _run(_note_argv(retired_sha="def5678",
                           promoted_to="tests/test_widget.py"), tree)

    assert code == 0
    text = (tree / "records" / "slices" / "widget.md").read_text(encoding="utf-8")
    assert "retired_sha: def5678" in text
    assert "promoted_to: tests/test_widget.py" in text


def test_note_refuses_a_field_carrying_a_path(tree, capsys):
    """The property that matters most at this surface: the repository is PUBLIC.

    The plan lives in the operator's private overlay, so a note pins it by
    digest. A CLI that wrote its arguments through without the schema's leak
    check would put an overlay path into a committed public file, and the digest
    field is not even the likeliest carrier: `undo` is free prose.
    """
    code = _run(_note_argv(undo="revert, then restore ~/private/plan.md"), tree)

    assert code == 1
    assert "carries a path" in capsys.readouterr().err
    assert not (tree / "records").exists()


def test_note_refuses_a_record_missing_a_required_field(tree, capsys):
    """A plain sentence naming every missing field, no traceback, nothing written.

    Nothing written is the half that would be missed: a CLI that wrote first and
    validated after would leave a half-formed record on disk for the four clauses
    to report against forever.
    """
    code = _run(["note", "widget", "--value", _FIELDS["value"]], tree)

    assert code == 1
    err = capsys.readouterr().err
    assert "missing required field" in err
    assert "undo" in err and "approval_sha" in err
    assert "Traceback" not in err
    assert not (tree / "records").exists()


def test_note_refuses_a_retirement_that_names_no_promotion(tree, capsys):
    """Half a retirement is refused, because it cannot be told from a dropped
    contract: the clauses would then hold a shipped slice to a deleted target."""
    code = _run(_note_argv(retired_sha="def5678"), tree)

    assert code == 1
    assert "promoted_to" in capsys.readouterr().err
    assert not (tree / "records").exists()


def test_note_writes_the_slice_record(tree, capsys):
    """The record appears where the standard says it does, carrying its fields.

    The printed path is engine-relative rather than absolute, which is not
    cosmetic: this repository is public, and an absolute path is the one thing an
    operator is most likely to paste out of a terminal into a commit message.
    """
    code = _run(_note_argv(), tree)

    assert code == 0
    assert capsys.readouterr().out.strip() == "records/slices/widget.md"
    text = (tree / "records" / "slices" / "widget.md").read_text(encoding="utf-8")
    assert "slug: widget" in text
    assert f"approval_sha: {_FIELDS['approval_sha']}" in text
    assert f"plan_digest: {_PLAN_DIGEST}" in text
    assert _FIELDS["undo"] in text


def test_probe_does_not_call_an_already_green_test_vacuous(tree, capsys):
    """`vacuous` holds every test that passed under the stub, and a test that
    passed for REAL is in it too. Labelling that one "asserts nothing" is a false
    claim about a test asserting real behaviour, and it hides the already-green
    reading the operator is told to question."""
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    # The green case asserts against the standard library, so it passes for a
    # reason no environment can take away, and the stub never touches it.
    (directory / "test_contract.py").write_text(
        "def test_green_for_real():\n"
        "    import json\n"
        "    assert json.dumps({'a': 1}) == '{\"a\": 1}'\n"
        "\n\n"
        "def test_vacuous():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
    )

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    green = next(line for line in out.splitlines() if "test_green_for_real" in line)
    assert "passed" in green
    assert "asserts nothing" not in green
    assert "asserts nothing" in next(
        line for line in out.splitlines() if "test_vacuous" in line
    )


def test_probe_exits_one_on_a_contract_that_would_be_refused(tree):
    _write_contract(tree, red=False)
    assert _run(["probe", "tests/contract/slice"], tree) == 1


def test_probe_hands_the_probe_the_real_runs_population(tree, monkeypatch):
    """The probe's own run is what its verdict is applied to, and nothing else.

    Pinned at the CALL, because `expected_population` carries a default: drop
    the argument and `run_null_stub` runs its own unstubbed baseline instead,
    the lost-test guard weighs one pytest session, and this command applies the
    verdict to another.
    """
    seen = _population_spy(monkeypatch)
    _write_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 0

    assert len(seen) == 1
    assert sorted(seen[0]["expected_population"]) == _REAL_POPULATION


def test_probe_labels_how_a_red_test_failed(tree, capsys):
    """The operator's first question is whether anything failed for a reason
    other than the code being absent.

    Its own contract rather than `_write_contract`, because that helper's red test
    now dies on an absent import and would be labelled `import` on every run: this
    test needs one red test of each kind side by side to show the label
    discriminating.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "def test_asserts():\n"
        "    assert 1 == 2\n"
        "\n\n"
        "def test_imports():\n"
        "    from absent_thing import answer\n"
        "    assert answer() == 42\n"
    )

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    rows = {
        name: next(line for line in out.splitlines() if name in line)
        for name in ("test_asserts", "test_imports")
    }
    assert "assertion" in rows["test_asserts"]
    assert "import" in rows["test_imports"]


def test_probe_leaves_a_green_row_alone_when_the_probe_failed(
    tree, monkeypatch, capsys,
):
    """UNKNOWN belongs on the rows the verdict would have judged, and no others.

    Only RED tests are weighed by `vacuity_refusal`, so a green row's vacuity was
    never going to be reported either way; stamping it UNKNOWN would invent a
    missing measurement rather than name one.
    """
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "def test_green_for_real():\n"
        "    import json\n"
        "    assert json.dumps({'a': 1}) == '{\"a\": 1}'\n"
        "\n\n"
        "def test_red():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
    )

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    lines = capsys.readouterr().out.splitlines()
    green = next(line for line in lines if "test_green_for_real" in line)
    red = next(line for line in lines if "test_red" in line)
    assert "vacuity UNKNOWN" not in green
    assert "vacuity UNKNOWN" in red


def test_probe_lists_the_tests_that_assert_nothing(tree, capsys):
    _write_vacuous_contract(tree, real=True)

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    assert "asserts nothing" in out
    assert "test_vacuous" in out


def test_probe_marks_every_red_row_unknown_when_the_probe_failed(
    tree, monkeypatch, capsys,
):
    """The table has to say it too, not only the trailing refusal.

    With `vacuous` empty every red row printed its ordinary failure line and
    nothing on it marked vacuity as unmeasured, so an operator who read to the
    end was told and one who skimmed the rows was not. The rows are the surface
    this command exists to be read from.
    """
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    _write_vacuous_contract(tree, real=True)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    lines = capsys.readouterr().out.splitlines()
    rows = [
        next(line for line in lines if name in line)
        for name in ("test_vacuous", "test_other")
    ]
    assert all("vacuity UNKNOWN" in row for row in rows)


def test_probe_names_a_vacuous_test_built_in_a_conftest_fixture(tree, capsys):
    _write_conftest_fixture_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    out = capsys.readouterr().out
    assert "asserts nothing" in next(
        line for line in out.splitlines() if "test_widget_exists" in line
    )


def test_probe_prints_outcomes_and_writes_nothing(tree, capsys):
    _write_contract(tree)

    code = _run(["probe", "tests/contract/slice"], tree)

    out = capsys.readouterr().out
    assert code == 0
    assert "test_a" in out and "test_b" in out
    assert not (tree / ".canopus").exists()


def test_probe_refuses_a_contract_that_names_no_module(tree, capsys):
    """And it is a refusal, not a traceback: the CLI turns ContractError into an
    operator-visible line on every one of the three surfaces."""
    _write_importless_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    out = capsys.readouterr().out
    assert "NOT measured" in out
    assert "vacuity UNKNOWN" in next(
        line for line in out.splitlines() if "test_a" in line
    )


def test_probe_refuses_when_the_vacuity_probe_cannot_run(
    tree, monkeypatch, capsys,
):
    """The same unmeasurable state on the surface the operator reads.

    `probe` runs the stub session unconditionally, so it meets this failure
    first. Two things have to hold and only one of them is the message. The
    per-test table reads `vacuous` twenty lines below the call and
    `vacuity_refusal` reads it again at the end, so an except branch that only
    printed left both reading an UNBOUND name and killed the command with an
    `UnboundLocalError` on exactly the failure it was added to report. An empty
    `vacuous` must also never be weighed as a verdict: a real `cases` set against
    an empty one finds no subset and returns [], which is the silent "measured,
    and nothing was vacuous" reading this refusal exists to remove.
    """
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    _write_vacuous_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    out = capsys.readouterr().out
    assert "could not be measured" in out
    # The table still rendered: the failure is reported, not raised through it.
    assert "test_vacuous" in out


def test_probe_shows_a_skipped_test_as_skipped_rather_than_vacuous(tree, capsys):
    """`pytest.importorskip` is an ordinary idiom, and nothing refuses a skipped
    contract test.

    The display filter was `outcome != "passed"` while `vacuity_refusal` filters
    on `outcome in RED_OUTCOMES`, so a skipped test landed in the vacuous branch:
    it is in `vacuous` because the stub supplies the module it skipped on, and
    the `continue` swallowed the only line that would have said it never ran. The
    one surface the operator is told to read reclassified it into the bucket they
    are invited to strike off by eye.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "import pytest\n\n\n"
        "def test_parked():\n"
        "    answer = pytest.importorskip('absent_thing').answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_red():\n"
        "    from absent_thing import answer\n"
        "    assert answer() == 42\n"
    )

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    parked = next(line for line in out.splitlines() if "test_parked" in line)
    assert "skipped" in parked
    assert "vacuous" not in parked
    assert "asserts nothing" not in parked
    # No invented failure mode either: a skipped test carries no failure child,
    # so the heuristic would have defaulted it to `other`.
    assert "other" not in parked
