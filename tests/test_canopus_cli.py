"""Tests for the Canopus CLI, which is now one command: `probe`.

The lifecycle this file used to exercise - approve, freeze, verify, status,
release, repin, pack, where - was deleted on 2026-08-07 along with the freeze
manifest it read. What survives is the reading `probe` produces over a contract
whose implementation does not exist yet, and every assertion below is about that
reading rather than about a lock.
"""
from pathlib import Path

import pytest

import scripts.canopus as canopus
from scripts.canopus import main


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
