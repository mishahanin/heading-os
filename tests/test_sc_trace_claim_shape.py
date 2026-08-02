"""A claim opens the docstring; a mention deeper in it claims nothing.

Not part of the frozen `sc-trace` contract, and deliberately so: the rule this
file pins did not exist when that contract was written. It was earned at step 8,
when `scripts/sc-trace.py` was pointed at its own slice's artifact and refused
it -- three of the contract's own docstrings DESCRIBE the false positives the
artifact parser exists to survive, so they say `SC-13`, `SC-1 to SC-7` and `SC-9`
in prose, and a reader taking any mention as a claim reported all three as orphan
criteria.

The consequence is stronger than noise. Without a leading-position rule a test
cannot explain what it is testing without accidentally binding to whatever it
names, which makes the orphan check unusable rather than merely loud.
"""
from scripts.utils.sc_trace import read_claims


def _claims(body: str) -> dict:
    return read_claims({"test_contract.py": body})


def test_a_criterion_named_in_the_opening_position_is_a_claim():
    body = 'def test_one():\n    """SC-1. What it decides."""\n    assert True\n'

    assert _claims(body) == {"SC-1": {"test_contract.py"}}


def test_a_criterion_mentioned_in_the_prose_body_is_not_a_claim():
    """The exact shape that refused this module's own contract."""
    body = ('def test_one():\n'
            '    """SC-1. Measured false positive: a table row carrying SC-13,\n'
            '    and an artifact line reading SC-1 to SC-7 outside the section.\n'
            '    """\n'
            '    assert True\n')

    assert _claims(body) == {"SC-1": {"test_contract.py"}}


def test_several_criteria_may_open_one_docstring():
    """One test genuinely deciding two criteria says so, and both bind."""
    body = 'def test_one():\n    """SC-1, SC-2. Both."""\n    assert True\n'

    assert _claims(body) == {"SC-1": {"test_contract.py"},
                             "SC-2": {"test_contract.py"}}


def test_a_docstring_opening_with_prose_claims_nothing():
    """Strictness in the direction that fails safe.

    Binding on a trailing mention would let a docstring claim a criterion its
    author never meant to decide, and a false binding reads exactly like a real
    one. An unclaimed criterion is loud; a wrongly claimed one is silent.
    """
    body = ('def test_one():\n'
            '    """Checks the boundary condition. SC-2."""\n'
            '    assert True\n')

    assert _claims(body) == {}


def test_a_non_test_function_claims_nothing():
    """Helpers carry docstrings too, and a helper decides no criterion."""
    body = ('def helper():\n    """SC-1. A helper."""\n    return 1\n'
            '\n\ndef test_one():\n    """SC-2."""\n    assert True\n')

    assert _claims(body) == {"SC-2": {"test_contract.py"}}
