#!/usr/bin/env python3
"""pytest plugin: stub exactly what the contract imports, and nothing else.

Loaded with `-p scripts.utils.canopus_nullstub` in the probe child only, never in
an ordinary run. Passing it as a plugin rather than writing a conftest is
deliberate: the contract directory is frozen recursively, and a file written
beside it would read as tampering to the very lock this tool installs.

See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#canopus_nullstub-module
(The design record routes private and lives in the DATA overlay, never in
this repository. A public clone does not carry it; the pointers below name
the section, so the reasoning is one grep away for whoever has the overlay.)
"""
from __future__ import annotations

import copy
import os
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.util import find_spec
from types import ModuleType

MODULES_VAR = "CANOPUS_AST_MODULES"
VALUES_VAR = "CANOPUS_STUB_VALUES"
# Which PASS-CANDIDATE this child carries, if any. Absent or empty means the
# ordinary null stub, so every existing caller keeps its behaviour without
# knowing this variable exists.
CANDIDATE_VAR = "CANOPUS_CANDIDATE"
# Whether the candidate this child carries REPLACES the values a module already
# has, or only answers the names it lacks. Absent or empty means the second, the
# behaviour every caller had before this variable existed.
#
# Any NON-EMPTY value arms it; the string is not parsed as a boolean.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#replace_var
REPLACE_VAR = "CANOPUS_REPLACE_EXISTING"
# The greedy candidate's whole payload, already joined by the parent. ONE
# string rather than the literal set: a NUL cannot cross the boundary.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#greedy_payload_var
GREEDY_PAYLOAD_VAR = "CANOPUS_CANDIDATE_PAYLOAD"

# The one character the claim set is joined on, and the one character a claim may
# therefore not contain. A comma cannot appear in an importable dotted name, so
# the parent drops any collected string carrying it rather than escaping it.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#stub_name_separator
STUB_NAME_SEPARATOR = ","
# What this plugin prefixes its own diagnostics with, on the child's stderr. The
# parent forwards exactly the lines that start with it; see `_report` below.
NULLSTUB_STDERR_MARKER = "canopus-nullstub:"
# What this child says it actually REPLACED, and the one line of this stream the
# parent reads as data rather than forwarding as prose. Defined here, on the
# side that writes it, and imported by the reader for the reason
# `STUB_NAME_SEPARATOR` above already carries: two spellings of one wire format
# is a rename away from a parent that silently learns nothing.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#replaced_report
REPLACED_REPORT = "replaced:"

# Modules compiled into the interpreter, which this plugin never claims. Read
# from the running interpreter rather than written down, so it is a PROPERTY of
# the process and not a list somebody has to maintain: enumerated names are the
# pattern five earlier revisions of `process_facts` were each defeated through.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#builtin_module_names
BUILTIN_MODULE_NAMES = frozenset(sys.builtin_module_names)

# Every channel differs between the two sets. A channel they agreed on could not
# separate a vacuous test from one that reads it.
STUB_VALUES = {
    "A": {"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"},
    "B": {"len": 7, "int": 99, "bool": False, "contains": True, "item": "b"},
}


class Stub:
    """A value-carrying stand-in whose descendants inherit its values.

    Dunder ATTRIBUTE access raises, so a stub cannot answer `__path__` and
    masquerade as a package. `__eq__` deliberately does NOT read `_values`: it is
    True for any other Stub, so a stub-to-stub equality reads the same under both
    value sets and the test built on it counts vacuous.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#stub
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


# ============================================================
# Pass candidates: implementations that EXIST and are WRONG
# ============================================================
#
# Three: `none` returns nothing from every call, `echo` hands back its first
# argument unchanged, `greedy` answers with every string the contract wrote.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#candidates
CANDIDATES = ("none", "echo", "greedy")

# Prepended to the greedy payload so the joined string can never EQUAL any
# single literal the contract wrote.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#greedy_marker
GREEDY_MARKER = "canopus-pass-candidate"


class Candidate:
    """A stand-in for an implementation that EXISTS and is wrong.

    Deliberately NOT a `Stub` subclass: a candidate carries ONE wrong behaviour and
    no differential axis to vary. Dunder attribute access raises, so it cannot
    masquerade as a package. Attribute access and subscription return a SIBLING;
    only a CALL answers — None for `none`, the first positional argument for `echo`,
    the joined payload for `greedy`. Raises KeyError on a mode outside CANDIDATES.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#candidate
    """

    __slots__ = ("_mode", "_payload")

    def __init__(self, mode: str, payload: str):
        # Validated HERE rather than in `candidate_value` alone, so both doors
        # into this object are gated by one rule. The child builds candidates
        # straight from its environment, and a typo on the parent side arrives
        # there; an unvalidated mode would fall through `__call__` to the greedy
        # branch and produce a full table under a candidate nobody ran. Raising
        # kills the probe child, which returns no report, which the parent
        # already reads as a measurement that did not happen.
        if mode not in CANDIDATES:
            raise KeyError(mode)
        object.__setattr__(self, "_mode", mode)
        object.__setattr__(self, "_payload", payload)

    def _sibling(self):
        return Candidate(
            object.__getattribute__(self, "_mode"),
            object.__getattribute__(self, "_payload"),
        )

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self._sibling()

    def __getitem__(self, key):
        return self._sibling()

    def __call__(self, *args, **kwargs):
        mode = object.__getattribute__(self, "_mode")
        if mode == "none":
            return None
        if mode == "echo":
            # The first positional argument, unchanged. With no positional
            # argument there is nothing to echo and `None` is the honest answer:
            # inventing a value here would make `echo` a second constant-return
            # candidate on exactly the calls where it has no input to pass
            # through.
            return args[0] if args else None
        return object.__getattribute__(self, "_payload")


def greedy_payload(literals) -> str:
    """Every string the contract wrote, joined, behind a marker.

    Sorted and de-duplicated so the payload is a function of the SET alone. Two
    runs of one contract that differed only in iteration order would otherwise be
    two different probes.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#greedy_payload
    """
    return "\n".join([GREEDY_MARKER, *sorted(set(literals))])


def candidate_value(name: str, literals):
    """The stand-in one named candidate installs. Raises on a name it lacks.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#candidate_value
    """
    if name not in CANDIDATES:
        raise KeyError(name)
    return Candidate(name, greedy_payload(literals))


def _stub_attribute(name: str):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    candidate = os.environ.get(CANDIDATE_VAR, "")
    if candidate:
        # The payload arrives already joined, so this child never re-derives it
        # and cannot disagree with the parent about what the contract wrote.
        return Candidate(candidate, os.environ.get(GREEDY_PAYLOAD_VAR, ""))
    return Stub(_values())


def _report(message: str) -> None:
    """Say on stderr what was swallowed, so no handler here is silent.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_report
    """
    sys.stderr.write(f"{NULLSTUB_STDERR_MARKER} {message}\n")


def _supply_absent_attributes(module):
    """Answer the names a real module lacks with a stub, keeping the ones it has.

    The module's own PEP 562 `__getattr__` still answers first and the stub catches
    only what it declines, so a real dynamic attribute is never replaced. Returns
    `(existing, supply)` — what was there before, and what was installed — which
    `pytest_unconfigure` needs both to restore a live module and to leave alone one
    somebody else has since rewritten.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_supply_absent_attributes
    """
    existing = module.__dict__.get("__getattr__")

    def supply(name, _existing=existing):
        if _existing is not None:
            try:
                return _existing(name)
            except AttributeError:
                pass
        return _stub_attribute(name)

    module.__getattr__ = supply
    return existing, supply


# The attribute a REPLACING supplier carries its module's own values on, so
# `pytest_unconfigure` undoes both surfaces in the one pass it already walks.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_replaced_attribute
_REPLACED_ATTRIBUTE = "canopus_replaced"


def replace_attributes(module, mode: str, payload: str):
    """Answer a module's OWN names with one candidate, and its absent ones too.

    A superset of `_supply_absent_attributes`: the module's own `__getattr__` is NOT
    chained to, dunders survive untouched, a callable name is replaced by a Candidate
    sibling and a data name by what the candidate answers to a no-argument call. The
    scope is the module's dict AT THIS INSTANT. Returns `(existing, supply)`, with
    the overwritten values riding on `supply` under `_REPLACED_ATTRIBUTE`.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#replace_attributes
    """
    answer = Candidate(mode, payload)
    existing = module.__dict__.get("__getattr__")
    replaced = {
        name: value
        for name, value in module.__dict__.items()
        if not (name.startswith("__") and name.endswith("__"))
    }
    for name, value in replaced.items():
        module.__dict__[name] = answer._sibling() if callable(value) else answer()

    def supply(name, _answer=answer):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _answer._sibling()

    setattr(supply, _REPLACED_ATTRIBUTE, replaced)
    module.__getattr__ = supply
    return existing, supply


def _install_attributes(module):
    """Supply what a module lacks, or replace what it has as well.

    Read from the environment per call rather than captured at import, matching
    `_values` and `_stub_attribute`, so a test can arm the switch after this
    module is loaded.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_install_attributes
    """
    candidate = os.environ.get(CANDIDATE_VAR, "")
    if candidate and os.environ.get(REPLACE_VAR, ""):
        return replace_attributes(
            module, candidate, os.environ.get(GREEDY_PAYLOAD_VAR, "")
        )
    return _supply_absent_attributes(module)


class _StubLoader(Loader):
    """Builds a module whose every non-dunder attribute is a Stub."""

    def create_module(self, spec):
        module = ModuleType(spec.name)
        module.__getattr__ = _stub_attribute  # PEP 562
        return module

    def exec_module(self, module):
        return None


def _record_installation(finder, module, installed) -> None:
    """File one wrapped module against the installation whose finder claimed it.

    Matched BY IDENTITY on the finder, never by position in `_INSTALLED`. An
    unmatched finder records NOTHING, which is the direction that leaks a mutated
    module rather than disarming a live probe.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_record_installation
    """
    for installation in _INSTALLED:
        if installation.finder is finder:
            existing, supply = installed
            installation.supplied.append((module, existing, supply))
            return


class _WrapLoader(Loader):
    """Runs the real loader, then supplies the names the module lacks.

    Every other loader attribute is delegated to the real loader, which importlib,
    pkgutil and inspect all read off `spec.loader`. `finder` is the `_NamedFinder`
    that built this loader, so `exec_module` can file the wrapped module against the
    one installation that owns it; it defaults to None, which records nothing.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_wraploader
    """

    def __init__(self, real, finder=None):
        self._real = real
        self._finder = finder

    def __getattr__(self, name):
        # Only reached for names this class does not define, so create_module and
        # exec_module below still win. `_real` is set in __init__ and is
        # therefore always in __dict__ before this can run.
        return getattr(self._real, name)

    def create_module(self, spec):
        return self._real.create_module(spec)

    def exec_module(self, module):
        self._real.exec_module(module)
        installed = _install_attributes(module)
        # Recorded on THIS installation's ledger, so a module the loader
        # touched gets its surface back exactly as one that was already
        # imported does.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_wraploader-exec_module
        _record_installation(self._finder, module, installed)


class _NamedFinder(MetaPathFinder):
    """Claims exactly the modules the contract's AST named, and no others.

    INSERTED at the front of `sys.meta_path`, so a claimed name is reached before
    PathFinder resolves it unwrapped. The re-entrancy set is load-bearing:
    `find_spec` consults `sys.meta_path`, which reaches this finder again for the
    same name, and without the guard the resolution recurses.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_namedfinder
    """

    def __init__(self, names):
        self._names = tuple(sorted(names))
        self._busy: set[str] = set()

    def _claims(self, fullname: str) -> bool:
        return any(
            fullname == name or fullname.startswith(f"{name}.")
            for name in self._names
        )

    def _must_be_a_package(self, fullname: str) -> bool:
        """True when the claim set names something BELOW this name.

        Derived from the claim set rather than passed in, so this and `_expand_claims`
        cannot disagree. A claimed name with no claimed children is untouched by it.

        See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_must_be_a_package
        """
        prefix = f"{fullname}."
        return any(name.startswith(prefix) for name in self._names)

    def _stub_spec(self, fullname: str, real) -> ModuleSpec:
        """A package stub, carrying real search locations when there are any.

        Real locations are kept so the modules ALREADY WRITTEN below the name still
        resolve, and only the absent ones reach this finder.

        See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_stub_spec
        """
        spec = ModuleSpec(fullname, _StubLoader(), is_package=True)
        locations = None if real is None else real.submodule_search_locations
        if locations:
            spec.submodule_search_locations = list(locations)
        return spec

    def find_spec(self, fullname, path=None, target=None):
        """Resolve a claimed name, stubbing anything that will not resolve.

        Returns None for a name this finder does not claim, a stub spec for one that will
        not resolve, and a fresh COPY of the real spec (never the object handed back)
        when it wraps. An exception from the resolution is stubbed and reported, never
        propagated: `find_spec` executes ancestor packages, so first-party code runs here.

        See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#find_spec
        """
        if fullname in self._busy or not self._claims(fullname):
            return None
        self._busy.add(fullname)
        try:
            real = find_spec(fullname)
        except Exception as exc:  # noqa: BLE001 - see the docstring; it is reported
            _report(f"resolving {fullname} raised {exc!r}; stubbing it instead")
            real = None
        finally:
            self._busy.discard(fullname)
        if (
            real is None
            or real.loader is None
            or (
                real.submodule_search_locations is None
                and self._must_be_a_package(fullname)
            )
        ):
            return self._stub_spec(fullname, real)
        # A fresh spec, never the one that was returned. For an ALREADY-IMPORTED
        # module `importlib.util.find_spec` hands back `module.__spec__` itself,
        # so assigning the wrapper onto it edits the live module's own spec and
        # leaves it wrapped for the rest of the process, long after this probe.
        wrapped = copy.copy(real)
        wrapped.loader = _WrapLoader(real.loader, self)
        return wrapped


def _expand_claims(names):
    """Every named module, plus the prefixes of it that do not resolve.

    A prefix that resolves to a PACKAGE is not claimed; one that resolves to a plain
    module is. A name whose first segment is built into the interpreter is dropped
    whole and reported. Resolution happens here, once, BEFORE the finder is
    installed: doing it inside `find_spec` would re-enter the finder being built.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_expand_claims
    """
    claimed = set()
    for name in names:
        parts = name.split(".")
        if parts[0] in BUILTIN_MODULE_NAMES:
            # Dropped HERE rather than in the finder, so the one exclusion covers both
            # surfaces that consult a claim.
            #
            # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_expand_claims-builtin-drop
            _report(f"not claiming {name}: {parts[0]} is compiled into the "
                    f"interpreter, and stubbing it takes the measurement "
                    f"apparatus down with it")
            continue
        for index in range(1, len(parts) + 1):
            prefix = ".".join(parts[:index])
            if prefix == name:
                claimed.add(prefix)
                continue
            try:
                spec = find_spec(prefix)
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - reported, and claimed below
                _report(f"resolving the prefix {prefix} raised {exc!r}; claiming it")
                spec = None
            if spec is None or spec.submodule_search_locations is None:
                claimed.add(prefix)
    return claimed


class _Installation:
    """Exactly what one `pytest_configure` did, so its own teardown can undo it.

    Three things need undoing: the finder on `sys.meta_path`, the attribute suppliers
    written onto modules that were already imported, and the empty `__path__` given
    to an already-imported plain module that has to carry a claimed child.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_installation
    """

    __slots__ = ("finder", "supplied", "pathed")

    def __init__(self, finder):
        self.finder = finder
        self.supplied = []
        self.pathed = []


# Last in, first out, so a nested probe undoes its own installation and not the
# outer one's. A single slot could not tell the two apart.
_INSTALLED: list[_Installation] = []

# The `finder` an installation records when its configure armed NOTHING. It is a
# private object rather than None because `_WrapLoader` defaults its finder to
# None for a caller that builds it directly, and `_record_installation` matches
# by identity: None here would make the one record that claims nothing the home
# for exactly the modules that are documented to record nowhere. Nothing puts
# this object on `sys.meta_path`, so teardown removes nothing for it either.
_NOTHING_ARMED = object()


def pytest_configure(config):
    """Install the finder, supply the modules already imported, or do nothing.

    Live modules are supplied in place, never evicted and never reloaded. A live
    PLAIN module that has to carry a claimed child is also given an EMPTY `__path__`,
    never overwriting one it already has. An empty claim set arms nothing and still
    records an installation, so configure and unconfigure stay balanced.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#pytest_configure
    """
    names = [
        name
        for name in os.environ.get(MODULES_VAR, "").split(STUB_NAME_SEPARATOR)
        if name
    ]
    if not names:
        # RECORDED, never returned on silently: `pytest_unconfigure` pops one record
        # per call, so an unbalanced pair lands on somebody else's installation.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#pytest_configure-nothing-armed
        _INSTALLED.append(_Installation(_NOTHING_ARMED))
        return
    finder = _NamedFinder(_expand_claims(names))
    sys.meta_path.insert(0, finder)
    installation = _Installation(finder)
    for name, module in list(sys.modules.items()):
        if not isinstance(module, ModuleType) or not finder._claims(name):
            continue
        existing, supply = _install_attributes(module)
        installation.supplied.append((module, existing, supply))
        if finder._must_be_a_package(name) and not hasattr(module, "__path__"):
            search_path: list[str] = []
            module.__path__ = search_path
            installation.pathed.append((module, search_path))
    _INSTALLED.append(installation)


def pytest_unconfigure(config):
    """Take back exactly what this plugin's own configure put in place.

    By IDENTITY, not by type: this installation's finder, its attribute suppliers and
    the `__path__` it added, each left alone if somebody has replaced it since.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#pytest_unconfigure
    """
    if not _INSTALLED:
        return
    installation = _INSTALLED.pop()
    if os.environ.get(CANDIDATE_VAR, "") and os.environ.get(REPLACE_VAR, ""):
        # Said HERE rather than at install time, because the set is complete only now.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#pytest_unconfigure-replaced-report
        _report(f"{REPLACED_REPORT} " + ",".join(
            sorted(module.__name__ for module, _e, _s in installation.supplied)
        ))
    sys.meta_path[:] = [
        finder for finder in sys.meta_path if finder is not installation.finder
    ]
    for module, search_path in reversed(installation.pathed):
        if module.__dict__.get("__path__") is search_path:
            del module.__path__
    for module, existing, supply in reversed(installation.supplied):
        if module.__dict__.get("__getattr__") is not supply:
            continue
        # The values a REPLACING supplier overwrote, put back before its
        # supplier is lifted. Empty for the ordinary absent-name path, so this
        # is one statement rather than a branch, and the two installers share
        # the one teardown they are required to share. Guarded by the same
        # identity check as the supplier above and for the same reason: a
        # module somebody else has since rewritten is not this installation's
        # to restore, and the alternative is clobbering a live value with a
        # snapshot taken before the session started.
        module.__dict__.update(getattr(supply, _REPLACED_ATTRIBUTE, {}))
        if existing is None:
            del module.__getattr__
        else:
            module.__getattr__ = existing
