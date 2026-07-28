#!/usr/bin/env python3
"""pytest plugin: stub exactly what the contract imports, and nothing else.

Loaded with `-p scripts.utils.canopus_nullstub` in the probe child only, never in
an ordinary run. Passing it as a plugin rather than writing a conftest is
deliberate: the contract directory is frozen recursively, and a file written
beside it would read as tampering to the very lock this tool installs.

What it buys. Before the implementation exists every contract test dies on
ImportError, so "the contract is red" proves the code is absent and says nothing
about whether the contract asserts anything. With the contract's own imports
resolved to stubs, a test that PASSES under two stubs carrying DIFFERENT values
has been proved not to depend on those values, and therefore to assert nothing
about them.

Why two stubs and not one. A single stub cannot separate a vacuous test from a
container assertion: measured on MagicMock, `len` is 0, `int` is 1, `list` is
empty and `in` is False, so `assert len(result) == 0` passes under the stub and
earns a label it did not deserve. The differential rule got nine of nine
assertions right where the single-stub rule got four wrong, every one of them
toward refusing a good contract.

Why the name set comes from the AST. An earlier revision read it from the child's
failure text, which the contract author writes, so `raise AssertionError(...) from
None` inside the ImportError handler erased the evidence. A later one answered
EVERY otherwise-failing import, which broke pytest's own
`importlib.import_module(parent)` under `--import-mode=importlib`. The AST is what
the interpreter executes: it cannot be suppressed by the handler, and it names
nothing the contract did not write.
"""
from __future__ import annotations

import os
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.util import find_spec
from types import ModuleType

MODULES_VAR = "CANOPUS_AST_MODULES"
VALUES_VAR = "CANOPUS_STUB_VALUES"

# Every channel differs between the two sets. A channel they agreed on could not
# separate a vacuous test from one that reads it.
STUB_VALUES = {
    "A": {"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"},
    "B": {"len": 7, "int": 99, "bool": False, "contains": True, "item": "b"},
}


class Stub:
    """A value-carrying stand-in whose descendants inherit its values.

    Deliberately not a MagicMock. Configuring a MagicMock's dunders recurses
    without bound, and subclassing it does not help because it owns its dunders
    on the instance; both were measured before this class was written.

    Dunder ATTRIBUTE access raises, so a stub cannot answer `__path__` and
    masquerade as a package.
    """

    __slots__ = ("_values",)

    def __init__(self, values):
        object.__setattr__(self, "_values", values)

    def _sibling(self):
        return Stub(object.__getattribute__(self, "_values"))

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self._sibling()

    def __call__(self, *args, **kwargs):
        return self._sibling()

    def __getitem__(self, key):
        return self._sibling()

    def __len__(self):
        return object.__getattribute__(self, "_values")["len"]

    def __int__(self):
        return object.__getattribute__(self, "_values")["int"]

    def __bool__(self):
        return object.__getattribute__(self, "_values")["bool"]

    def __contains__(self, key):
        return object.__getattribute__(self, "_values")["contains"]

    def __iter__(self):
        values = object.__getattribute__(self, "_values")
        return iter([values["item"]] * values["len"])

    def __eq__(self, other):
        # Equal only to another stub, so `assert answer() == 42` stays red. This
        # is the property the whole vacuity reading rests on for value asserts.
        return isinstance(other, Stub)

    def __hash__(self):
        return id(self)

    def __str__(self):
        return object.__getattribute__(self, "_values")["item"]

    def __repr__(self):
        return f"<canopus stub {object.__getattribute__(self, '_values')['item']}>"
