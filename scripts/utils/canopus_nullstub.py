#!/usr/bin/env python3
"""pytest plugin: resolve named absent modules to mocks, for the null-stub probe.

Loaded with `-p scripts.utils.canopus_nullstub` in the probe child only, never in
an ordinary run. Passing it as a plugin rather than writing a conftest is
deliberate: the contract directory is frozen recursively, and a file written
beside it would read as tampering to the very lock this tool installs.

What it buys. Before the implementation exists every contract test dies on
ImportError, so "the contract is red" proves the code is absent and says nothing
about whether the contract asserts anything. With the absent modules resolved to
mocks the imports succeed and the implementation still does not exist, so a test
that PASSES has been proved to assert nothing, by construction rather than by
opinion: `assert answer() == 42` fails against a MagicMock, while
`assert answer() is not None` passes and earns the label.
"""
from __future__ import annotations

import os
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from unittest.mock import MagicMock

ENV_VAR = "CANOPUS_STUB_MODULES"


class _MockLoader(Loader):
    """Builds a module whose every attribute is a fresh MagicMock.

    Fresh per access, so `mod.f is mod.f` is False. That is safe for the same
    reason the finder's docstring leans on: an identity assertion FAILS under the
    stub, so the test keeps its red outcome and is never mislabelled vacuous. It
    is named here because the property is easy to trip over when reading a
    passing probe run.
    """

    def create_module(self, spec):
        module = ModuleType(spec.name)
        module.__getattr__ = lambda name: MagicMock()  # PEP 562
        return module

    def exec_module(self, module):
        return None


class _MockFinder(MetaPathFinder):
    """Answers for the named modules and their submodules, and nothing else.

    Matching on the FULL dotted name is load-bearing. An earlier draft matched on
    the first segment, so a single absent `scripts.utils.canopus_git` mocked the
    whole `scripts` package, including the modules the contract legitimately
    imports. Every test then passed against mocks and the wholly-vacuous refusal
    fired on a perfectly good contract.

    Be exact about what the name list is, because the obvious claim is not quite
    true. It is what the real run could not import, and half of it comes from
    `cannot import name 'x' from 'y'`, where `y` EXISTS and is merely incomplete.
    So a partially built module IS shadowed whole, and every name it already
    carries correctly becomes a MagicMock for the probe run. That is safe rather
    than sound: a MagicMock compares unequal to every literal, so a test asserting
    on one of those real names still FAILS under the stub and is not mislabelled
    vacuous. Only a test asserting mere presence passes, which is the label it has
    earned. The property this leans on is MagicMock inequality, not the absence of
    shadowing, and it is written down here so the next reader does not weaken the
    matcher on a false premise.
    """

    def __init__(self, names):
        self._names = tuple(sorted(names))

    def find_spec(self, fullname, path=None, target=None):
        if not any(
            fullname == name or fullname.startswith(f"{name}.")
            for name in self._names
        ):
            return None
        return ModuleSpec(fullname, _MockLoader(), is_package=True)


def pytest_configure(config):
    """Install the finder before collection, or do nothing when unconfigured."""
    names = [name for name in os.environ.get(ENV_VAR, "").split(",") if name]
    if names:
        sys.meta_path.insert(0, _MockFinder(names))


def pytest_unconfigure(config):
    """Take the finder back out at session end.

    The probe child exits straight after, so nothing observable depends on this
    today. It is here so "the finder is removed cleanly" is a property of the
    code rather than of the process boundary, which is what would be relied on
    the first time this plugin is loaded into a session that keeps running.
    Absent finders are tolerated: pytest_configure installs nothing when the
    environment names no modules, and unconfigure still runs.
    """
    for finder in [f for f in sys.meta_path if isinstance(f, _MockFinder)]:
        sys.meta_path.remove(finder)
