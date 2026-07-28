"""Frozen contract: vacuity is measured without the contract author's testimony.

Canopus accepts a frozen contract only when at least one test is RED, and the
authoring rule guarantees redness for free: before the code exists every test dies
on ImportError. So redness proves the code is absent and says nothing about
whether the contract asserts anything. Wire 2.1 closed that as a class by
resolving the absent modules to mocks and re-running: a test that PASSES with the
implementation still missing is proved to assert nothing.

One bypass survived. To learn what to mock, the probe read the child's failure
text for `No module named`, and that text is produced by the contract file, which
the builder writes. `raise AssertionError(...) from None` inside the ImportError
handler erases it, so nothing is mocked, the refusal cannot fire, and freeze exits
0 over a contract every test of which asserts nothing.

This contract binds the fix on two axes, and each closes a measured failure.

WHAT IS STUBBED comes from the contract's own import AST. `try/except` erases the
failure MESSAGE; it cannot erase the `from x import y` STATEMENT, because the AST
is what the interpreter executes. An earlier design answered EVERY otherwise
failing import instead, and that broke pytest itself: under the
`--import-mode=importlib` this repository pins, pytest builds the parent packages
of each collected test module inside `try: importlib.import_module(parent) /
except ModuleNotFoundError`, so a global answer made a stub the parent package of
the collected module.

HOW THE VERDICT IS READ is differential. A single stub cannot separate a vacuous
test from a container assertion: `len` is 0, `int` is 1, `in` is False, so
`assert len(result) == 0` passes under one stub and earns a label it did not
deserve. A test is vacuous only when it passes under TWO stubs carrying different
values, because an outcome invariant to the value cannot be reading the value.

AUTHORING RULE, enforced: every import of the code under test is INSIDE the test
body. A module-scope import stops the file collecting, and a file that collects
nothing cannot be frozen.
"""
import sys

import pytest


@pytest.fixture
def clean_imports():
    """Restore sys.meta_path AND sys.modules however the test ends.

    sys.modules is half the fixture, not tidiness: without it the session keeps a
    stubbed module under a plain name for every later test in the run, and the
    next reader who reuses that name gets a stub and an order-dependent failure a
    long way from here.
    """
    saved_meta_path = list(sys.meta_path)
    saved_modules = dict(sys.modules)
    yield
    sys.meta_path[:] = saved_meta_path
    for name in set(sys.modules) - set(saved_modules):
        del sys.modules[name]
    sys.modules.update(saved_modules)


# ============================================================
# SC-1 and SC-2: the bypass, in both of its halves
# ============================================================


def test_sc1_the_from_none_bypass_over_an_absent_module_is_caught(tmp_path):
    """The defect this slice exists to close.

    `from None` suppresses the chained traceback, so the child's report carries no
    `No module named` line at all. The revision this replaces read that text to
    decide what to stub, found nothing, stubbed nothing, and returned an empty set
    over a test that asserts nothing.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    try:\n"
        "        from absent_thing import answer\n"
        "    except ImportError:\n"
        "        raise AssertionError('not implemented yet') from None\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_sc2_the_same_bypass_over_a_half_built_module_is_caught(tmp_path):
    """The half a global sink cannot see, and the state a retake is taken in.

    Where the module EXISTS and the name in it does not, no finder is consulted at
    all under ordinary import: the module was found, and the failure is `cannot
    import name`. This is the mid-build shape, so a mechanism closing only the
    first half closes the bypass exactly where it is least likely to be used.
    """
    from scripts.utils.canopus_contract import run_null_stub

    (tmp_path / "halfbuilt.py").write_text("EXISTS = 1\n", encoding="utf-8")
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    try:\n"
        "        from halfbuilt import NOT_THERE_YET\n"
        "    except ImportError:\n"
        "        raise AssertionError('not implemented yet') from None\n"
        "    assert NOT_THERE_YET is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


# ============================================================
# SC-3 and SC-4: the legitimate contract is not collateral damage
# ============================================================


def test_sc3_a_real_value_assertion_is_not_accused(tmp_path):
    """Stubbing must not turn a genuine assertion green.

    This is the ordinary contract the tool must never refuse: red because a value
    is wrong, not because the code is missing.
    """
    from scripts.utils.canopus_contract import run_null_stub

    (tmp_path / "present.py").write_text(
        "def answer():\n    return 1\n", encoding="utf-8"
    )
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from present import answer\n"
        "    assert answer() == 42\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == set()


def test_sc4_a_container_assertion_is_not_accused(tmp_path):
    """THIS is what the second stub run buys, and it is the v1 regression.

    Measured on a prototype before this contract was written: under ONE stub this
    test passes, because `len(stub)` is 0, and it would be labelled vacuous. Under
    two stubs carrying different lengths it passes once and fails once, so its
    outcome is not invariant to the value and it asserts something after all.

    The same shape covers `assert key not in result` and `assert int(v) == 1`.
    Four such assertions were misclassified by the single-stub rule, every one of
    them toward refusing a good contract, which is the direction that teaches a
    builder to route around the gate.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from absent_thing import listing\n"
        "    assert len(listing()) == 0\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == set()


def test_sc4c_a_vacuous_test_behind_a_skip_is_still_caught(tmp_path):
    """The one-call bypass, cheaper than the one this slice exists to close.

    A verdict phrased as "passed under both stubs" is blind to a test that never
    passes at all. Measured on a prototype: `pytest.skip()` at the top of a
    vacuous test yields `skipped` under BOTH runs, so it never enters an
    intersection of passes and the freeze proceeds.

    It is also a recurrence. Wire 2.3 already found that a skipped test is never
    in the vacuous set, and fixed it in one function without fixing the rule the
    function serves. A test that did not run was not proved innocent.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import pytest\n"
        "\n\n"
        "def test_a():\n"
        "    pytest.skip('not implemented yet')\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_sc4d_a_wholly_absent_dotted_import_is_stubbed(tmp_path):
    """Python resolves the PARENT first, and a finder claiming only the full
    dotted name is never consulted for the child.

    Measured: claiming `ghost.sub` alone makes `from ghost.sub import thing` die
    with `ModuleNotFoundError: No module named 'ghost'`. The test then stays red
    under both stubs and is never labelled, which is an ESCAPE rather than a
    false accusation, and escapes are the direction that costs the guarantee.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from brandnew.pkg import thing\n"
        "    assert thing() is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_sc4b_partial_vacuity_stays_per_test(tmp_path):
    """Reported per test, never collapsed to the file.

    A verdict that collapsed to "this file is vacuous" would refuse a contract
    holding one weak test beside good ones, which is most real contracts.
    """
    from scripts.utils.canopus_contract import run_null_stub

    (tmp_path / "present.py").write_text(
        "def answer():\n    return 1\n", encoding="utf-8"
    )
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_weak():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_strong():\n"
        "    from present import answer\n"
        "    assert answer() == 42\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_weak")}


# ============================================================
# SC-5: what is stubbed comes from the AST, not from the output
# ============================================================


def test_sc5_the_ast_reader_sees_an_import_hidden_in_a_try_block(tmp_path):
    """The property the whole slice rests on.

    An import cannot be hidden from the AST by anything the author writes in the
    handler, because the AST is what the interpreter executes.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    try:\n"
        "        from absent_thing import answer\n"
        "    except ImportError:\n"
        "        raise AssertionError('nope') from None\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"absent_thing"}


def test_sc5b_the_ast_reader_ignores_a_relative_import(tmp_path):
    """A relative import names no absolute module, so there is nothing to claim.

    Recording its bare text would make the finder claim a name no import statement
    can produce, which is a claim that can only ever be wrong.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from . import sibling\n"
        "    assert sibling\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == set()


def test_sc5c_the_ast_reader_refuses_a_file_it_cannot_parse(tmp_path):
    """A syntax error must not read as "this contract imports nothing".

    An empty set stubs nothing, so every test stays red for its original reason
    and the vacuity refusal cannot fire. Silence here is the same defect class
    this slice exists to remove.
    """
    from scripts.utils.canopus_contract import ContractError, contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text("def test_a(:\n", encoding="utf-8")

    with pytest.raises(ContractError):
        contract_imports([contract], tmp_path)


# ============================================================
# SC-6: the stub object carries values, and two sets disagree
# ============================================================


def test_sc6_a_stub_hands_its_values_to_every_descendant():
    """`stub.answer()` must carry the same values as `stub`.

    This is the property MagicMock could not give. Measured: configuring its
    dunders recurses without bound, because reading `mock.__len__` in order to set
    it creates a child that must itself be configured; and subclassing does not
    help, because MagicMock owns its dunders on the instance, so a subclass whose
    `__len__` returns 7 still measured `len(s) == 0`.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 7, "int": 99, "bool": True, "contains": True, "item": "b"})

    assert len(stub.answer()) == 7
    assert int(stub.obj().attr()) == 99
    assert "k" in stub.result()


def test_sc6b_the_two_value_sets_disagree_on_every_channel():
    """A channel the two sets agree on is a blind spot, not a stub.

    The differential verdict is exactly "did the outcome change when the values
    changed", so a value present in both sets can never separate anything.
    """
    from scripts.utils.canopus_nullstub import STUB_VALUES

    assert sorted(STUB_VALUES) == ["A", "B"]

    first, second = STUB_VALUES["A"], STUB_VALUES["B"]
    assert set(first) == set(second)
    assert all(first[key] != second[key] for key in first)


def test_sc6c_a_stub_refuses_dunder_attributes():
    """A stub answering `__path__` would masquerade as a package.

    The import machinery reads dunders to decide HOW to import, so answering them
    changes Python's behaviour rather than measuring the contract's.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"})

    assert not hasattr(stub, "__path__")


# ============================================================
# SC-7: the finder claims exactly what the contract named
# ============================================================


def test_sc7_a_named_module_that_does_not_resolve_becomes_a_stub(
    clean_imports, monkeypatch
):
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"canopus_contract_absent_fixture"}))

    from canopus_contract_absent_fixture import answer

    assert len(answer()) == 7


def test_sc7b_a_named_module_that_resolves_supplies_only_what_it_lacks(
    clean_imports, tmp_path, monkeypatch
):
    """Both assertions matter.

    The first proves the absent name is supplied. The second proves the PRESENT
    one is not replaced, which is what stops a good contract being mislabelled by
    a wrapper that answered everything.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "halfbuilt_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"halfbuilt_fixture"}))

    import halfbuilt_fixture

    assert halfbuilt_fixture.EXISTS == 1
    assert len(halfbuilt_fixture.NOT_THERE_YET) == 7


def test_sc7c_a_module_the_contract_did_not_name_is_never_claimed(
    clean_imports, tmp_path, monkeypatch
):
    """The blocker that killed the previous design, pinned as a test.

    An earlier revision answered EVERY otherwise failing import, which under this
    repository's `--import-mode=importlib` made a stub the parent package of every
    collected test module and disabled `pytest.importorskip` besides.

    Two assertions, because either alone is unfalsifiable. `find_spec` returning
    None survives an already-imported target; the import is the second half, over
    a module written for this test so the finder genuinely is consulted.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "unnamed_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "A")
    finder = _NamedFinder({"something_else_entirely"})
    sys.meta_path.insert(0, finder)

    assert finder.find_spec("unnamed_fixture") is None

    import unnamed_fixture

    assert not hasattr(unnamed_fixture, "no_such_attribute")


def test_sc7d_the_finder_survives_a_named_package_chain(
    clean_imports, tmp_path, monkeypatch
):
    """Resolving a spec re-walks sys.meta_path and reaches this finder again.

    Without a re-entrancy guard the resolution recurses until the interpreter
    stops it, and the symptom is an unrelated RecursionError inside pytest.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    pkg = tmp_path / "chain_fixture"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "leaf.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "A")
    sys.meta_path.insert(0, _NamedFinder({"chain_fixture"}))

    from chain_fixture.leaf import VALUE

    assert VALUE == 2


# ============================================================
# SC-8: an absent measurement never reads as a clean one
# ============================================================


def test_sc8_a_stub_run_that_executed_nothing_refuses(tmp_path):
    """Zero executed tests is not "nothing was vacuous".

    A stub run that collected nothing measured nothing, and the caller cannot tell
    that from a run where every test genuinely failed under the stub. The two mean
    opposite things.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import pytest\n"
        "pytest.skip('nothing runs here', allow_module_level=True)\n"
        "\n"
        "def test_a():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError):
        run_null_stub([contract], tmp_path)


def test_sc8b_two_runs_that_collected_different_tests_refuse(tmp_path, monkeypatch):
    """An intersection over two different populations means nothing.

    The verdict is "passed under BOTH stub value sets", so a test present in one
    run and absent from the other silently counts as "did not pass both times",
    which reads as "asserts something" whatever it actually does. The two runs
    differ only in the stub VALUES, so a collection that depends on them is
    precisely the surprise worth refusing on rather than averaging over.
    """
    import scripts.utils.canopus_contract as contract_mod
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    real = contract_mod.run_pytest_report
    seen = []

    def _drop_a_case_on_the_second_run(*args, **kwargs):
        xml_text = real(*args, **kwargs)
        seen.append(1)
        if len(seen) == 2:
            return xml_text.replace("<testcase", "<skipped-case", 1)
        return xml_text

    monkeypatch.setattr(
        contract_mod, "run_pytest_report", _drop_a_case_on_the_second_run
    )

    with pytest.raises(ContractError):
        run_null_stub([contract], tmp_path)


# ============================================================
# SC-9: the operator can tell vacuity from a missing dependency
# ============================================================


def test_sc9_the_refusal_names_the_other_readings():
    """A module the contract names and that does not resolve is stubbed.

    That covers three different worlds: the implementation is unwritten, an extra
    is not installed, or a first-party circular import made the resolution raise.
    All three earn the same label. The direction errs toward refusal rather than
    acceptance, so it cannot wave a bad contract through, but an operator who is
    not told will edit a correct test.
    """
    from scripts.utils.canopus_contract import vacuity_refusal

    reasons = vacuity_refusal(
        [("c/test_one.py", "test_a", "failure")], {("c/test_one.py", "test_a")}
    )

    assert len(reasons) == 1
    assert "asserts nothing" in reasons[0]
    assert "not installed" in reasons[0]
