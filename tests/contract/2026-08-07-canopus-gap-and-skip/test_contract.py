"""The contract for the `canopus-gap-and-skip` slice.

Five criteria, one per value class of the partition in the plan. Every import of
the code under test sits INSIDE a test body, so the file collects before the
implementation exists.

Assertions are equalities against whole values rather than substring checks, on
the authoring rule: a substring check is satisfied by the `greedy`
pass-candidate, which answers with every string this contract itself wrote.
"""
from __future__ import annotations

import textwrap

import pytest


def _write_contract_tree(root, body: str):
    """A scratch contract directory holding one test module.

    Its own root, never the engine root, on authoring rule 2: a test that reads
    working-tree state and runs against the tree that carries its own slice can
    never be green.

    The shape written here is the shape a real contract file has: a module that
    imports pytest and defines `test_*` functions. Checked against
    `tests/contract/` on disk rather than against what the reader expects.
    """
    target = root / "contract"
    target.mkdir()
    (target / "test_sample.py").write_text(
        textwrap.dedent(body), encoding="utf-8"
    )
    return target


class TestSkipMarkers:
    def test_an_unreasoned_skip_is_named(self, tmp_path):
        """SC-1. A skip marker stating no reason is named by the reader.

        The whole returned value is compared, not searched: a contract test that
        greps for its own name is satisfied by the greedy pass-candidate.
        """
        from scripts.utils.canopus_contract import skip_markers_without_reason

        target = _write_contract_tree(tmp_path, """
            import pytest

            @pytest.mark.skip
            def test_unreasoned():
                assert False

            def test_plain():
                assert False
        """)

        assert skip_markers_without_reason([target], tmp_path) == ["test_unreasoned"]

    def test_a_reasoned_skip_is_not_named(self, tmp_path):
        """SC-2. A skip carrying a reason is left alone by the reader.

        Both spellings pytest accepts are present, because a reader that only
        understood the keyword form would refuse the positional one, and the
        positional one is the commoner.
        """
        from scripts.utils.canopus_contract import skip_markers_without_reason

        target = _write_contract_tree(tmp_path, """
            import pytest

            @pytest.mark.skip(reason="the upstream fixture lands in the next slice")
            def test_keyword_reason():
                assert False

            @pytest.mark.skip("the upstream fixture lands in the next slice")
            def test_positional_reason():
                assert False
        """)

        assert skip_markers_without_reason([target], tmp_path) == []

    def test_every_unreasoned_marker_family_is_named(self, tmp_path):
        """SC-1. skipif, xfail, an empty reason and a module-level mark all count.

        One test rather than four, because the criterion is a single claim about
        a family and four tests asserting one member each would let three pass
        while the family stayed open. The empty reason is the edge: a marker
        carrying `reason=""` states nothing, so it is not a documented skip.
        """
        from scripts.utils.canopus_contract import skip_markers_without_reason

        target = _write_contract_tree(tmp_path, """
            import pytest

            pytestmark = pytest.mark.skip

            @pytest.mark.skipif(True)
            def test_bare_skipif():
                assert False

            @pytest.mark.xfail
            def test_bare_xfail():
                assert False

            @pytest.mark.skip(reason="")
            def test_empty_reason():
                assert False
        """)

        assert skip_markers_without_reason([target], tmp_path) == [
            "<module>",
            "test_bare_skipif",
            "test_bare_xfail",
            "test_empty_reason",
        ]

    def test_the_refusal_carries_the_named_tests(self, tmp_path):
        """SC-1. `refusal_reasons` turns the named tests into a refusal.

        The reader alone is not the control: nothing refuses a contract until
        `refusal_reasons` says so, and that is the function `probe` consults.
        """
        from scripts.utils.canopus_contract import refusal_reasons

        reasons = refusal_reasons(
            {"c/test_one.py": 2},
            [("c/test_one.py", "test_a", "failure"),
             ("c/test_one.py", "test_b", "skipped")],
            ["c/test_one.py"],
            skipped_without_reason=["test_b"],
        )

        assert len(reasons) == 1


class TestCandidatesAgainstExistingCode:
    def test_an_existing_attribute_answers_the_candidate(self):
        """SC-3. A present module's own value is replaced, not merely supplemented.

        This is the whole of R1. `_supply_absent_attributes` installs a PEP 562
        `__getattr__`, which Python consults ONLY for names the module lacks, so
        against shipped code the candidates reach nothing and the probe reports a
        clean page over a suite it never touched.
        """
        from types import ModuleType

        from scripts.utils.canopus_nullstub import replace_attributes

        module = ModuleType("subject")
        module.ANSWER = 42

        replace_attributes(module, "none", "")

        assert module.ANSWER is None

    def test_dunders_survive_the_replacement(self):
        """SC-3. `__name__`, `__file__` and `__spec__` are left alone.

        Replacing them breaks pytest's own machinery, and the resulting failure
        does not look like a measurement, so a reader would diagnose the tool
        rather than the contract.
        """
        from types import ModuleType

        from scripts.utils.canopus_nullstub import replace_attributes

        module = ModuleType("subject")
        module.__file__ = "/real/path/subject.py"
        module.VALUE = "real"

        replace_attributes(module, "none", "")

        # The ordinary attribute is read in the SAME tuple as the two dunders,
        # deliberately. Asserting only that the dunders did not move is true of a
        # run in which nothing happened at all, so the probe labelled the first
        # draft of this test vacuous and was right to: it would have been green
        # against an empty implementation.
        assert (module.__name__, module.__file__, module.VALUE) == (
            "subject", "/real/path/subject.py", None,
        )


class TestVerificationGaps:
    def test_a_test_green_under_every_candidate_is_a_gap(self):
        """SC-4. Surviving all three wrong implementations names a gap.

        The fixtures carry the shapes the real sources emit: `parse_junit`
        returns `(file, name, outcome)` triples whose outcome is one of passed,
        failure, error, skipped, and `run_pass_candidates` returns a dict keyed
        by candidate name holding `(file, name)` pairs.
        """
        from scripts.utils.canopus_contract import verification_gaps

        outcomes = [
            ("tests/test_subject.py", "test_survives", "passed"),
            ("tests/test_subject.py", "test_bites", "passed"),
        ]
        taken = {
            "none": {("tests/test_subject.py", "test_survives"),
                     ("tests/test_subject.py", "test_bites")},
            "echo": {("tests/test_subject.py", "test_survives"),
                     ("tests/test_subject.py", "test_bites")},
            "greedy": {("tests/test_subject.py", "test_survives")},
        }

        assert verification_gaps(outcomes, taken) == [
            ("tests/test_subject.py", "test_survives")
        ]

    def test_a_test_no_candidate_collected_is_not_called_clear(self):
        """SC-4. A test absent from a candidate's run is unmeasured, never clear.

        The edge that decides whether this reading is evidence. A test missing
        from every candidate's set satisfies "green under all three" vacuously
        under a naive subset, which would report the one test nobody measured as
        the one test that is fine.
        """
        from scripts.utils.canopus_contract import ContractError, verification_gaps

        outcomes = [("tests/test_subject.py", "test_never_ran", "passed")]

        with pytest.raises(ContractError):
            verification_gaps(outcomes, {"none": set(), "echo": set(), "greedy": set()})


class TestBudgetLockstep:
    def test_the_code_carries_the_measured_budget(self):
        """SC-5. The two plan-budget numbers live in the agenda module.

        Derived in the plan from 99 real plans: warn mirrors the SKILL.md warn
        this workspace already enforces, hard sits one byte above the measured
        median of 23,704.
        """
        from scripts.utils.canopus_steps import PLAN_BYTE_HARD, PLAN_BYTE_WARN

        assert (PLAN_BYTE_WARN, PLAN_BYTE_HARD) == (16384, 24576)

    def test_the_standard_states_the_same_numbers(self):
        """SC-5. The prose states the numbers the code carries.

        Held in lockstep on the precedent already in `tests/test_canopus_steps.py`:
        two definitions of one rule is how the prose inverted itself against the
        code once already.
        """
        from pathlib import Path

        from scripts.utils.canopus_steps import PLAN_BYTE_HARD, PLAN_BYTE_WARN

        skill = (Path(__file__).resolve().parents[3]
                 / ".claude" / "skills" / "canopus" / "SKILL.md")
        text = skill.read_text(encoding="utf-8")

        assert [f"{PLAN_BYTE_WARN:,}" in text, f"{PLAN_BYTE_HARD:,}" in text] == [True, True]
