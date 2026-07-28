"""The stub object and the finder, tested at the mechanism.

Each test drives the piece directly rather than launching pytest, so a failure
names the mechanism rather than a child process's exit code. The end-to-end path
is Task 4's.
"""
import sys

import pytest


def test_a_stub_hands_its_values_to_every_descendant():
    """`m.answer()` must carry the same values as `m`.

    This is the property MagicMock could not give: configuring its dunders
    recurses, and a subclass's dunders are ignored because MagicMock owns them on
    the instance. Measured before this object was written.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 7, "int": 99, "bool": True, "contains": True, "item": "b"})

    assert len(stub.answer()) == 7
    assert int(stub.obj().attr()) == 99
    assert "k" in stub.result()


def test_two_stub_value_sets_disagree_on_every_channel():
    """A channel the two sets agree on cannot separate anything.

    The differential verdict is exactly "did the outcome change when the values
    changed", so a value present in both sets is a blind spot, not a stub.
    """
    from scripts.utils.canopus_nullstub import STUB_VALUES

    first, second = STUB_VALUES["A"], STUB_VALUES["B"]

    assert set(first) == set(second)
    assert all(first[key] != second[key] for key in first)


def test_a_stub_refuses_dunder_attributes():
    """A stub answering `__path__` would masquerade as a package.

    The import machinery reads dunders to decide HOW to import, so answering them
    changes Python's behaviour rather than measuring the contract's.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"})

    with pytest.raises(AttributeError):
        stub.__path__  # noqa: B018 - the access itself is the assertion


# The three tests above are the brief's exact corpus. The four below are added
# during this task's own mutation-matrix pass: the brief's tests never exercise
# `__getitem__`, `__iter__`, `__eq__`, `__hash__`, `__str__`, or `__repr__`, so a
# mutation to any of those would have survived silently.


def test_stub_equality_is_only_to_another_stub():
    """`__eq__` returning True only for another `Stub` is what the docstring
    calls load-bearing: it is what keeps `assert answer() == 42` red under the
    stub instead of accidentally passing.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"})
    other = Stub({"len": 7, "int": 99, "bool": False, "contains": True, "item": "b"})

    assert stub == other
    assert stub != 42
    assert stub != "a"


def test_stub_getitem_and_iter_carry_the_same_values():
    """Indexing and iterating are descendant channels too, like attribute access
    and calling - they must carry the same value set.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 3, "int": 5, "bool": True, "contains": True, "item": "z"})

    assert len(stub["k"]) == 3
    assert list(stub) == ["z", "z", "z"]


def test_stub_str_and_repr_surface_the_item_value():
    """Use an `item` that is not itself a substring of the fixed wrapper text
    ("<canopus stub ...>"), or a mutation that drops the item would pass this
    check by accident - "a" is already inside the word "canopus".
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 0, "int": 1, "bool": True, "contains": False, "item": "qz"})

    assert str(stub) == "qz"
    assert repr(stub) == "<canopus stub qz>"


def test_stub_bool_carries_the_value():
    """`__bool__` must read the values dict, not default to Python's normal
    "objects are truthy" behaviour - a stub configured `bool: False` must be
    falsy so `assert not result` can be measured through the stub too.
    """
    from scripts.utils.canopus_nullstub import Stub

    truthy = Stub({"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"})
    falsy = Stub({"len": 0, "int": 1, "bool": False, "contains": False, "item": "a"})

    assert bool(truthy) is True
    assert bool(falsy) is False


def test_stub_hash_is_identity_based():
    """Two stubs sharing one values dict are still distinct for hashing
    (`__hash__` returns `id(self)`), so a stub can sit in a set or dict key
    without every stub collapsing into one bucket.
    """
    from scripts.utils.canopus_nullstub import Stub

    values = {"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"}
    first, second = Stub(values), Stub(values)

    assert hash(first) != hash(second)
