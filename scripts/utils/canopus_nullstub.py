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

    Why `__eq__` does not read `_values`, deliberately. Every other dunder here
    answers from the values dict, so it disagrees between the "A" and "B" sets
    and can carry a differential verdict. `__eq__` is the one exception: it
    returns `True` for any other `Stub` regardless of which values dict either
    side carries, so `assert one_stub() == another_stub()` reads the same under
    both sets. This is a choice, not an oversight, for four reasons.

    First, equality between two stubs is not a thing this instrument can
    measure. Whichever constant `__eq__` returns, that constant is the answer
    for every value the stub was built with, so there is no value to vary it
    against. Second, because the outcome does not move with the stubbed value,
    a test built on it asserts nothing this instrument can see, and it is
    counted vacuous, the same rule a skipped test follows: not proved is not
    proved innocent. Third, making equality differential would turn that
    honest refusal into an escape hatch. `assert thing() == thing()` would
    then fail under one of the two value sets, never land in the intersection
    both sets have to agree on, and never be flagged, which is exactly the
    kind of one-line escape this whole mechanism exists to close. Fourth, the
    cost is named rather than hidden: a builder whose contract test asserts an
    equivalence between two absent-code results, `assert normalise("a") ==
    normalise("A")`, sees that one test counted vacuous. Vacuity is judged per
    test, so a contract that also carries real assertions is unaffected; only
    a contract whose every red test takes this shape is refused.
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


def _values():
    """The value set this child was told to carry.

    Read per call rather than captured at import, so a test can set the variable
    after the module is loaded.
    """
    return STUB_VALUES[os.environ.get(VALUES_VAR, "A")]


def _stub_attribute(name: str):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    return Stub(_values())


class _StubLoader(Loader):
    """Builds a module whose every non-dunder attribute is a Stub."""

    def create_module(self, spec):
        module = ModuleType(spec.name)
        module.__getattr__ = _stub_attribute  # PEP 562
        return module

    def exec_module(self, module):
        return None


class _WrapLoader(Loader):
    """Runs the real loader, then supplies the names the module lacks.

    The catch-all `__getattr__` delegation is not decoration. A loader is read
    for far more than create/exec: `get_source`, `get_filename`, `is_package`,
    `get_data` and `get_resource_reader` are pulled off `spec.loader` by
    importlib.reload, importlib.resources, pkgutil and inspect.getsource. A
    wrapper answering only two of them narrows the real loader for the length of
    the probe, and the failure surfaces as an unrelated AttributeError inside
    somebody else's library.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        # Only reached for names this class does not define, so create_module and
        # exec_module below still win. `_real` is set in __init__ and is
        # therefore always in __dict__ before this can run.
        return getattr(self._real, name)

    def create_module(self, spec):
        return self._real.create_module(spec)

    def exec_module(self, module):
        self._real.exec_module(module)
        existing = module.__dict__.get("__getattr__")

        def supply(name, _existing=existing):
            if _existing is not None:
                try:
                    return _existing(name)
                except AttributeError:
                    pass
            return _stub_attribute(name)

        module.__getattr__ = supply


class _NamedFinder(MetaPathFinder):
    """Claims exactly the modules the contract's AST named, and no others.

    INSERTED at the front, because it must claim a named module before
    PathFinder resolves it unwrapped. It is safe there only because the claim is
    narrow: an earlier revision answered every otherwise-failing import and made
    a stub the parent package of the collected test module, under the
    `--import-mode=importlib` this repository pins.

    The re-entrancy set is load-bearing. `find_spec` consults sys.meta_path,
    which reaches this finder again for the same name; without the guard the
    resolution recurses.
    """

    def __init__(self, names):
        self._names = tuple(sorted(names))
        self._busy: set[str] = set()

    def _claims(self, fullname: str) -> bool:
        return any(
            fullname == name or fullname.startswith(f"{name}.")
            for name in self._names
        )

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self._busy or not self._claims(fullname):
            return None
        self._busy.add(fullname)
        try:
            real = find_spec(fullname)
        except (ImportError, AttributeError, ValueError):
            # The name does not resolve, which is the stub case below. A
            # first-party CIRCULAR import reaches here too: find_spec on a
            # submodule reads the parent's __path__, and a parent still being
            # initialised has none. That genuine defect is then stubbed and its
            # test can earn a vacuity label. The direction is toward REFUSAL, so
            # it cannot wave a bad contract through, and the refusal text names
            # the alternative readings.
            real = None
        finally:
            self._busy.discard(fullname)
        if real is None or real.loader is None:
            return ModuleSpec(fullname, _StubLoader(), is_package=True)
        real.loader = _WrapLoader(real.loader)
        return real


def _expand_claims(names):
    """Every named module, plus the prefixes of it that do not resolve.

    Measured: claiming `ghost.sub` ALONE makes `from ghost.sub import thing` die
    with `ModuleNotFoundError: No module named 'ghost'`, and the finder is never
    consulted for the child at all, because Python resolves the parent first. The
    test then stays red under both stubs and is never labelled, so a vacuous test
    escapes the verdict entirely. Claiming both names made the same import
    succeed.

    A prefix that RESOLVES is deliberately NOT claimed: `PathFinder` handles it,
    and claiming it would wrap a real package for nothing. Measured on this
    repository, `brandnew.pkg.mod` expands to all three levels while
    `scripts.utils.canopus_contract` claims only the full name, leaving `scripts`
    and `scripts.utils` untouched. This is what stops the previous design's blast
    radius returning through the prefix door.

    Resolution happens here, once, BEFORE the finder is installed. Doing it inside
    `find_spec` would re-enter the finder being constructed.
    """
    claimed = set()
    for name in names:
        parts = name.split(".")
        for index in range(1, len(parts) + 1):
            prefix = ".".join(parts[:index])
            if prefix == name:
                claimed.add(prefix)
                continue
            try:
                resolves = find_spec(prefix) is not None
            except (ImportError, AttributeError, ValueError):
                resolves = False
            if not resolves:
                claimed.add(prefix)
    return claimed


def pytest_configure(config):
    """Install the finder, or do nothing when unconfigured."""
    names = [
        name for name in os.environ.get(MODULES_VAR, "").split(",") if name
    ]
    if names:
        sys.meta_path.insert(0, _NamedFinder(_expand_claims(names)))


def pytest_unconfigure(config):
    """Take the finder back out at session end.

    The probe child exits straight after, so nothing observable depends on this
    today. It is here so "the finder is removed cleanly" is a property of the
    code rather than of the process boundary.
    """
    sys.meta_path[:] = [
        finder for finder in sys.meta_path
        if not isinstance(finder, _NamedFinder)
    ]
