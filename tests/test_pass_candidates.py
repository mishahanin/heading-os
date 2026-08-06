"""Pass candidates: does this contract accept an implementation that is WRONG?

Retired from `tests/contract/2026-08-04-pass-candidates/` when the slice shipped,
with every one of its nineteen IDs kept. Left in `tests/contract/` it would bind
every later slice to this one's behaviour; here it is ordinary regression cover.

The null-stub probe asks whether a test passes when the code is ABSENT. This
slice adds the other question: whether a test passes when the code is PRESENT
AND WRONG. Different question, so a different instrument, but it reuses the
null-stub's whole apparatus: the AST claim set, the wire format to the child,
the JUnit parse, and the population guards.

Imports still sit INSIDE each test body. That was the contract's authoring rule
when none of these names existed; it is kept because these tests drive whole
pytest child processes, and a module-scope import of the subject would make a
collection failure HERE look like a probe failure THERE.

**What this file does not cover on its own**, carried forward from the slice's
step 11 rather than left to be rediscovered. Three of these nineteen tests were
satisfied by a wrong implementation of the very instrument they pin, and the
mutations that proved it are killed by three tests living elsewhere:
`tests/test_canopus_nullstub.py::test_a_candidate_validates_its_mode_at_the_door_the_child_uses`,
and in `tests/test_canopus_contract.py`,
`test_the_greedy_payload_carries_nothing_the_contract_did_not_write` and
`test_the_refusal_carries_one_candidates_cure_and_not_the_others`. Delete any of
those and the coverage this file appears to give is not there.
"""
import pytest


# ----------------------------------------------------------------------------
# SC-1  The candidates are three, named, and each is a distinct shape of wrong
# ----------------------------------------------------------------------------

def test_the_candidate_names_are_the_three_that_were_argued():
    """SC-1. Four were proposed; two of them already exist as the null stub.

    `constant-return` IS the A/B stub, which is two constant-return modules
    carrying deliberately disagreeing constants. `import-only` IS the stub at
    import time: the names resolve and nothing is called, and a test satisfied
    by that already passes under both value sets and is already labelled
    vacuous. Shipping either as a new candidate would spend a pytest session per
    probe to re-measure what is measured. The two that remain are joined by
    `echo`, which the design does not name and which catches the "it did
    something" assertion a pass-through satisfies.
    """
    from scripts.utils.canopus_nullstub import CANDIDATES

    assert CANDIDATES == ("none", "echo", "greedy")


def test_each_candidate_answers_a_call_with_its_own_shape():
    """SC-1. A candidate that agreed with another could separate nothing.

    Asserted on the RESULT of a call rather than on the class, because the
    result is what a contract test sees.
    """
    from scripts.utils.canopus_nullstub import candidate_value

    assert candidate_value("none", ()).anything() is None

    sentinel = object()
    assert candidate_value("echo", ()).anything(sentinel) is sentinel

    greedy = candidate_value("greedy", ("alpha",)).anything()
    assert isinstance(greedy, str)
    assert greedy != "alpha"


def test_a_candidate_refuses_dunders_while_still_answering_ordinary_calls():
    """SC-1. A stand-in answering `__path__` masquerades as a package.

    The import machinery reads dunders to decide HOW to import, so answering
    them changes Python's behaviour instead of measuring the contract's. The
    null stub holds this line and a candidate installed through the same finder
    must hold it identically, or the candidate runs measure a different import
    graph from the stub runs they are compared against.

    The dunder refusal is asserted BESIDE the candidate's own call shape, and
    the pairing is the point rather than economy. The probe named this test
    vacuous when it stood alone: the null stub already refuses dunders, so an
    assertion about that alone is satisfied by the stand-in it is meant to
    distinguish itself from.
    """
    from scripts.utils.canopus_nullstub import candidate_value

    candidate = candidate_value("none", ())

    assert candidate.anything() is None
    with pytest.raises(AttributeError):
        candidate.__path__  # noqa: B018 - the access itself is the assertion


def test_an_unknown_candidate_name_raises_rather_than_defaulting():
    """SC-1. A silent default is a run that measured a candidate nobody chose.

    The child reads its candidate from the environment, so a typo on the parent
    side arrives here. Defaulting to `none` would produce a full green table
    under a candidate the parent never ran, and the operator would read it as
    the candidate named in the report.
    """
    from scripts.utils.canopus_nullstub import candidate_value

    with pytest.raises(KeyError):
        candidate_value("constant", ())


# ----------------------------------------------------------------------------
# SC-2  The discriminating property: loose contracts fall, strict ones stand
# ----------------------------------------------------------------------------

def test_a_contract_whose_every_red_test_is_loose_is_taken_by_a_candidate(
    tmp_path,
):
    """SC-2. The whole slice, stated as one measurement.

    Both tests here are red for a real reason before the code exists, so the
    null stub has nothing to say about them: neither is vacuous, both die on an
    absent import. A wrong implementation satisfies both anyway.
    """
    from scripts.utils.canopus_contract import run_pass_candidates

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_loose.py").write_text(
        "def test_it_ran():\n"
        "    from absent_subject import answer\n"
        "    assert answer('x') is not None\n"
        "\n\n"
        "def test_it_mentions_the_word():\n"
        "    from absent_subject import answer\n"
        "    assert 'refused' in str(answer('x'))\n",
        encoding="utf-8",
    )

    taken = run_pass_candidates([directory], tmp_path)

    assert any(
        {("c/test_loose.py", "test_it_ran"),
         ("c/test_loose.py", "test_it_mentions_the_word")} <= passed
        for passed in taken.values()
    )


def test_a_contract_carrying_one_strict_assertion_survives_every_candidate(
    tmp_path,
):
    """SC-2. The direction that matters more: no false accusation.

    An instrument that refuses good contracts teaches the builder to route
    around it, after which it proves nothing while looking as though it does.
    So the property asserted here is the one a false positive would break.
    """
    from scripts.utils.canopus_contract import run_pass_candidates

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_strict.py").write_text(
        "def test_exact_value():\n"
        "    from absent_subject import answer\n"
        "    assert answer('x') == 42\n",
        encoding="utf-8",
    )

    taken = run_pass_candidates([directory], tmp_path)

    assert taken
    assert all(
        ("c/test_strict.py", "test_exact_value") not in passed
        for passed in taken.values()
    )


def test_a_raises_assertion_is_not_satisfied_by_any_candidate(tmp_path):
    """SC-2. The most common strict shape in this repository's contracts.

    None of the three candidates raises, so a `pytest.raises` block fails under
    all of them. Pinned because a later candidate that raised on some path would
    silently start taking every refusal test in every contract.
    """
    from scripts.utils.canopus_contract import run_pass_candidates

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_raises.py").write_text(
        "import pytest\n"
        "\n\n"
        "def test_refuses():\n"
        "    from absent_subject import act\n"
        "    with pytest.raises(ValueError):\n"
        "        act('')\n",
        encoding="utf-8",
    )

    taken = run_pass_candidates([directory], tmp_path)

    assert taken
    assert all(
        ("c/test_raises.py", "test_refuses") not in passed
        for passed in taken.values()
    )


# ----------------------------------------------------------------------------
# SC-3  `greedy` is built from the contract's own literals, and only those
# ----------------------------------------------------------------------------

def test_contract_literals_reads_the_string_constants_the_contract_wrote(
    tmp_path,
):
    """SC-3. The greedy string's source is the contract, never a guess.

    A candidate that carried an alphabet, or a random blob, would satisfy
    substring assertions the contract never wrote and manufacture refusals. It
    carries exactly what the contract greps for.
    """
    from scripts.utils.canopus_contract import contract_literals

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_lit.py").write_text(
        "def test_a():\n"
        "    from absent_subject import render\n"
        "    assert 'cure: repin' in render()\n",
        encoding="utf-8",
    )

    literals = contract_literals([directory], tmp_path)

    assert "cure: repin" in literals
    assert "absent_subject" in literals


def test_greedy_satisfies_a_substring_assertion_and_not_an_equality_one(
    tmp_path,
):
    """SC-3. The measured defect shape, reproduced at Approval 1.

    Three times in the `manifest-split` slice a test proved satisfiable by a
    stand-in because it asserted a WORD in an output rather than the output.
    This is the pair that separates the two readings, and both halves are
    required: a candidate satisfying the equality too would refuse honest
    contracts.
    """
    from scripts.utils.canopus_contract import run_pass_candidates

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_greedy.py").write_text(
        "def test_word_appears():\n"
        "    from absent_subject import render\n"
        "    assert 'refused' in render()\n"
        "\n\n"
        "def test_whole_value():\n"
        "    from absent_subject import render\n"
        "    assert render() == 'refused'\n",
        encoding="utf-8",
    )

    taken = run_pass_candidates([directory], tmp_path)

    assert ("c/test_greedy.py", "test_word_appears") in taken["greedy"]
    assert ("c/test_greedy.py", "test_whole_value") not in taken["greedy"]


def test_a_literal_that_cannot_cross_the_process_boundary_is_dropped(tmp_path):
    """SC-3. The same rule the claim set already follows, for the same reason.

    An environment value holding a NUL raises `ValueError: embedded null byte`
    out of `subprocess`, which is not the `ContractError` this module promises
    its callers, so the CLI that catches `ContractError` would die with a
    traceback instead of a refusal. Dropped rather than escaped, and dropping
    can only make the greedy string satisfy LESS.
    """
    from scripts.utils.canopus_contract import contract_literals, passable_literals

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_nul.py").write_text(
        "def test_a():\n"
        "    from absent_subject import render\n"
        "    assert 'ok' in render('a\\x00b')\n",
        encoding="utf-8",
    )

    literals = contract_literals([directory], tmp_path)
    passable = passable_literals(literals)

    assert "a\x00b" in literals
    assert "a\x00b" not in passable
    assert "ok" in passable


# ----------------------------------------------------------------------------
# SC-4  The refusal rule, and the trap that would fire it on nothing
# ----------------------------------------------------------------------------

def test_the_refusal_fires_only_when_one_candidate_takes_the_whole_red_set():
    """SC-4. Whole-contract, mirroring `vacuity_refusal`, and for its reason.

    "These three tests assert too little" is a judgement for a human; "every
    single thing this contract checks is satisfied by an implementation that
    returns None" is not. Partial coverage is reported by name and never
    refused.
    """
    from scripts.utils.canopus_contract import pass_candidate_refusal

    outcomes = [
        ("c/t.py", "test_one", "failure"),
        ("c/t.py", "test_two", "failure"),
    ]

    whole = pass_candidate_refusal(
        outcomes,
        {"none": {("c/t.py", "test_one"), ("c/t.py", "test_two")},
         "echo": set(), "greedy": set()},
    )
    partial = pass_candidate_refusal(
        outcomes,
        {"none": {("c/t.py", "test_one")}, "echo": set(), "greedy": set()},
    )

    assert len(whole) == 1
    assert partial == []


def test_the_refusal_names_the_candidate_that_took_the_contract():
    """SC-4. The operator's next action depends on WHICH wrongness sufficed.

    A contract taken by `none` needs a value assertion; one taken by `greedy`
    needs its substring check replaced by an equality. A refusal that named
    neither would send the reader back to re-derive it.
    """
    from scripts.utils.canopus_contract import pass_candidate_refusal

    reasons = pass_candidate_refusal(
        [("c/t.py", "test_one", "failure")],
        {"none": set(), "echo": set(), "greedy": {("c/t.py", "test_one")}},
    )

    assert len(reasons) == 1
    assert "greedy" in reasons[0]
    assert "none" not in reasons[0]


def test_an_empty_red_set_is_not_a_contract_every_candidate_took():
    """SC-4. The subset trap, which holds vacuously and reads as a refusal.

    `set() <= anything` is True, so a contract with no red test at all would be
    refused here with a sentence about wrong implementations, instead of by
    `refusal_reasons`, which owns that case and says why. The identical guard
    is what `vacuity_refusal` carries, and it is written here rather than
    inherited, because the two functions do not share a code path.
    """
    from scripts.utils.canopus_contract import pass_candidate_refusal

    reasons = pass_candidate_refusal(
        [("c/t.py", "test_one", "passed")],
        {"none": set(), "echo": set(), "greedy": set()},
    )

    assert reasons == []


def test_only_tests_that_were_red_for_real_are_weighed():
    """SC-4. The same evidence rule the rest of the probe follows.

    A test that PASSED in the real run had no absent import for a candidate to
    satisfy, so its pass under a candidate has another explanation and proves
    nothing. Counting it would let one green test drag a genuinely loose
    contract out of the refusal.
    """
    from scripts.utils.canopus_contract import pass_candidate_refusal

    reasons = pass_candidate_refusal(
        [("c/t.py", "test_red", "failure"), ("c/t.py", "test_green", "passed")],
        {"none": {("c/t.py", "test_red")}, "echo": set(), "greedy": set()},
    )

    assert len(reasons) == 1


# ----------------------------------------------------------------------------
# SC-5  Wired into the one command that reads a contract
# ----------------------------------------------------------------------------

def test_probe_refuses_a_taken_contract_and_leaves_a_strict_one_alone(
    tmp_path, capsys, monkeypatch
):
    """SC-5. Both halves in ONE test, deliberately.

    A wiring test that only asserts the refusal is satisfied by a stub that
    always refuses, which is exactly how `M11` survived the previous slice's
    frozen contract. The pair cannot be satisfied by any constant.

    The loose fixture greps for a WORD rather than asserting absence, and that
    choice is what makes this test measure the new instrument. The probe found
    the first draft already green: `assert answer() is not None` is vacuous
    under the null stub, so today's `probe` already exits 1 on it and the
    assertion proved nothing about pass-candidates. A substring assertion is
    red under one stub value set and green under the other, so it is NOT
    vacuous, today's probe exits 0, and only `greedy` can move it.
    """
    from scripts.canopus import main

    def _tree(name, body):
        root = tmp_path / name
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "run-tests.py").write_text("# stub gate\n")
        directory = root / "tests" / "contract" / "slice"
        directory.mkdir(parents=True)
        (directory / "test_contract.py").write_text(body, encoding="utf-8")
        return root

    loose = _tree(
        "loose",
        "def test_a():\n"
        "    from absent_subject import render\n"
        "    assert 'refused' in render()\n",
    )
    strict = _tree(
        "strict",
        "def test_a():\n"
        "    from absent_subject import answer\n"
        "    assert answer() == 42\n",
    )

    monkeypatch.chdir(loose)
    taken_code = main(["--root", str(loose), "probe", "tests/contract/slice"])
    taken_out = capsys.readouterr().out

    monkeypatch.chdir(strict)
    strict_code = main(["--root", str(strict), "probe", "tests/contract/slice"])

    assert taken_code == 1
    assert strict_code == 0
    assert "none" in taken_out


def test_probe_names_the_candidate_that_took_the_contract_and_writes_nothing(
    tmp_path, capsys, monkeypatch
):
    """SC-5. The refusal reaches the operator's surface, naming WHICH candidate.

    Promoted from `freeze` on 2026-08-07. It read: the refusal has to reach the
    command that WRITES the manifest, or a contract any wrong implementation
    satisfies binds the whole slice anyway. There is no such command now -- the
    freeze lifecycle is deleted and `probe` is the only surface -- so the
    assertion moved rather than being dropped, and it is the same assertion:
    exit non-zero, name `greedy` on the surface the operator reads, and leave
    nothing behind.

    Distinct from the pair above, which proves the refusal discriminates loose
    from strict but reads only the candidates summary line. This one reads the
    REFUSAL, and a refusal that fires without naming its candidate tells the
    author nothing about which assertion to strengthen.

    The fixture greps for a word for the reason argued at the `probe` test
    above. The probe found this test ALREADY GREEN twice over while it was a
    freeze test, once for vacuity and once for an anchor stating no criteria at
    all, and `assert code != 0` could not tell any of the three refusals apart.
    Naming `greedy` is what tells them apart.
    """
    from scripts.canopus import main

    root = tmp_path / "tree"
    root.mkdir()
    directory = root / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        'def test_a():\n'
        '    """SC-1."""\n'
        "    from absent_subject import render\n"
        "    assert 'refused' in render()\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)

    code = main(["--root", str(root), "probe", "tests/contract/slice"])

    out = capsys.readouterr().out
    assert code == 1
    refusals = [line for line in out.splitlines() if "would be refused" in line]
    assert refusals, out
    assert any("greedy" in line for line in refusals), refusals
    assert not (root / ".canopus").exists()


# ----------------------------------------------------------------------------
# SC-6  What the instrument did NOT measure is never read as a clean bill
# ----------------------------------------------------------------------------

def test_a_red_test_a_candidate_never_collected_cannot_be_called_taken(
    tmp_path, capsys
):
    """SC-6. Not measured is not proved guilty, and the loss is said out loud.

    A module-scope statement can make a candidate run collect fewer tests than
    the real run. The arithmetic already errs safe (a test missing from the
    candidate's passed set breaks the subset, so no refusal), but silence there
    would let a probe that measured half a contract print the same page as one
    that measured all of it.

    The `try`/`except` at module scope is what makes this fixture measure
    anything, and Window 1 exists because the first draft lacked it. A bare
    module-scope import of the absent subject kills collection for the WHOLE
    file: measured, the real run collected zero tests, so `test_b` was in no
    population any run could report and the assertion below was satisfiable only
    by gaming. With the handler the real run collects both tests red, and the
    candidate run loses `test_b` because the stand-in for `FLAG` is truthy.
    """
    from scripts.utils.canopus_contract import run_pass_candidates

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_lost.py").write_text(
        "try:\n"
        "    from absent_subject import FLAG\n"
        "except ImportError:\n"
        "    FLAG = False\n"
        "\n\n"
        "def test_a():\n"
        "    from absent_subject import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "if not FLAG:\n"
        "    def test_b():\n"
        "        from absent_subject import answer\n"
        "        assert answer() is not None\n",
        encoding="utf-8",
    )

    run_pass_candidates([directory], tmp_path)

    assert "test_b" in capsys.readouterr().err


def test_a_contract_naming_no_module_is_refused_rather_than_measured(tmp_path):
    """SC-6. Nothing was stood in for, so nothing was attacked.

    The identical posture `run_null_stub` takes, and for the identical reason:
    an empty verdict from that state is indistinguishable from "measured, and
    no candidate took it", which is the reading that freezes a weak contract.
    """
    from scripts.utils.canopus_contract import ContractError, run_pass_candidates

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_bare.py").write_text(
        "def test_a():\n    assert 1 == 2\n", encoding="utf-8"
    )

    with pytest.raises(ContractError):
        run_pass_candidates([directory], tmp_path)


# ----------------------------------------------------------------------------
# SC-7  The price is pinned, so it cannot grow in silence
# ----------------------------------------------------------------------------

def test_one_pytest_session_per_candidate_and_not_one_more(tmp_path,
                                                           monkeypatch):
    """SC-7. Three sessions on top of the three the probe already spends.

    `probe` goes from three pytest sessions to six. That is the budget this
    slice asked for, and an instrument whose cost drifts upward unmeasured is
    one an operator eventually stops running.
    """
    from scripts.utils import canopus_contract
    from scripts.utils.canopus_contract import run_contract, run_pass_candidates
    from scripts.utils.canopus_nullstub import CANDIDATES

    directory = tmp_path / "c"
    directory.mkdir()
    (directory / "test_one.py").write_text(
        "def test_a():\n"
        "    from absent_subject import answer\n"
        "    assert answer() == 42\n",
        encoding="utf-8",
    )
    _counts, outcomes = run_contract([directory], tmp_path)

    calls = []
    real = canopus_contract.run_pytest_report

    def counting(*args, **kwargs):
        calls.append(kwargs.get("extra_env", {}))
        return real(*args, **kwargs)

    monkeypatch.setattr(canopus_contract, "run_pytest_report", counting)
    run_pass_candidates([directory], tmp_path, expected_population=outcomes)

    assert len(calls) == len(CANDIDATES)
