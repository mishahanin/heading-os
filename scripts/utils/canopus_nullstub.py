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

Why the claim is applied twice, on two surfaces. A finder on `sys.meta_path`
only ever sees imports that REACH it, and an import of a module already in
`sys.modules` short-circuits there. Measured in this repository, the root
conftest imports `scripts.utils.venv` at module level and initial conftests load
before a `-p` plugin's `pytest_configure`, so a contract naming that module was
claimed, never stubbed, and stayed red for its original reason under both value
sets. Both runs agreeing "red" never fires the vacuity rule, so `pytest_configure`
also supplies the modules already imported, in place.

Why the name set comes from the AST. An earlier revision read it from the child's
failure text, which the contract author writes, so `raise AssertionError(...) from
None` inside the ImportError handler erased the evidence. A later one answered
EVERY otherwise-failing import, which broke pytest's own
`importlib.import_module(parent)` under `--import-mode=importlib`. That blocker
was measured by FORCING importlib on a probe child that then inherited whatever
import mode its config carried; the probe now pins the flag on its own command
line, so it is a hazard of the mode this plugin actually runs in rather than of a
mode it could be put into. The AST is what the interpreter executes: it cannot be
suppressed by the handler, and it names nothing the contract did not write.
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
# OPT-IN, deliberately, and the default is the whole reason it is a variable
# rather than a rule. `_supply_absent_attributes` installs a PEP 562
# `__getattr__`, which Python consults ONLY for a name the module does not have,
# so against code that EXISTS the three candidates reach nothing: the run would
# report `candidates 0 of 0` over a suite it never touched, which is a
# clean-looking nothing and the exact reading this standard exists to refuse.
# Replacement closes that. But every contract in this repository, and every one
# written before this switch, is probed BEFORE its implementation exists, where
# there is nothing present to replace and the absent-name path is the whole
# measurement. Turning replacement on by default would change what all of those
# measured to add a reading none of them needed, which is a migration wearing a
# slice's clothes.
#
# Any NON-EMPTY value arms it; the string is not parsed as a boolean. Two
# spellings of off ("" and "0") would be a second rule about one switch, and the
# only writer is `canopus_contract.run_pass_candidates`, which sets it to "1" or
# to the empty string explicitly on every candidate child. That explicit empty
# matters: `run_pytest_report` merges its extra environment OVER `os.environ`,
# so a value exported in the operator's own shell would otherwise arm every
# probe on the machine and no page would say so.
REPLACE_VAR = "CANOPUS_REPLACE_EXISTING"
# The greedy candidate's whole payload, already joined by the parent. ONE string
# rather than the literal set, deliberately: an environment value carrying a NUL
# raises `ValueError: embedded null byte` out of `subprocess`, which is not the
# `ContractError` the parent promises its callers, so a literal carrying one is
# dropped on the parent side before this is built. Joining there also keeps the
# rule for what crosses the boundary in ONE place, beside the claim set's.
GREEDY_PAYLOAD_VAR = "CANOPUS_CANDIDATE_PAYLOAD"

# The wire format between the parent and this child, defined HERE because this
# side is the one that has to parse it, and imported by the parent rather than
# spelled again there. Two definitions of one rule are a rename on one side away
# from a child that claims nothing, two runs that agree on a red the vacuity rule
# never fires over, and a suite that stays green while the verdict is silently
# always empty. That is the same argument that already makes the parent import
# MODULES_VAR from here, and it applies verbatim to both of these.
#
# The one character the claim set is joined on, and the one character a claim may
# therefore not contain. A comma cannot appear in an importable dotted name, so
# the parent drops any collected string carrying it rather than escaping it.
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
# It exists because the claim set is what the candidates were ARMED for, and
# only the child knows which of those names an import ever reached. A module
# named by a claim and imported by nothing is replaced by nothing, and a parent
# that prints the claim set as the replaced set tells its reader that a wrong
# implementation stood where none did. Written only when the REPLACING switch is
# armed: the absent-name path replaces nothing, so a line about what it replaced
# would be a line about nothing, and it would move the stderr of every ordinary
# probe run for a reading only `--after-build` prints.
REPLACED_REPORT = "replaced:"

# Modules compiled into the interpreter, which this plugin never claims. Read
# from the running interpreter rather than written down, so it is a PROPERTY of
# the process and not a list somebody has to maintain: enumerated names are the
# pattern five earlier revisions of `process_facts` were each defeated through.
#
# The reason is measured, not defensive. `_supply_absent_attributes` writes a
# module-level `__getattr__` onto a live module so its ABSENT names read as
# stubs, and `sys` is a live module the interpreter itself introspects.
# `traceback.TracebackException` reads `sys.tracebacklimit` through `getattr`
# with a default, gets a Stub instead of the AttributeError it is written
# against, and dies on `limit < 0` with "'<' not supported between instances of
# 'Stub' and 'int'". The child then reports NO test at all, and the parent
# correctly refuses the whole contract as unmeasurable. Measured on a contract
# whose only sin was `import sys` at module scope, which is an entirely ordinary
# thing for a contract to write.
#
# The cost, stated rather than hidden: a test whose ONLY dependency is a
# built-in module reads the same under both value sets and is therefore labelled
# VACUOUS. That is a false accusation, never an escape, and a false accusation
# is the direction this instrument is allowed to err in. A built-in module is C
# compiled into the interpreter, so no first-party code under test can live
# inside one, and nothing a contract exists to judge is hidden by sparing it.
BUILTIN_MODULE_NAMES = frozenset(sys.builtin_module_names)

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


# ============================================================
# Pass candidates: implementations that EXIST and are WRONG
# ============================================================
#
# The null stub above asks whether a contract test passes while the code under
# test is ABSENT. These ask the other question: whether it passes while the code
# is PRESENT and wrong. Both install through the same finder, over the same claim
# set; only the leaf object differs, which is what keeps the two probes measuring
# one import graph rather than two.
#
# THREE, and the design's fourth and fifth are deliberately absent because they
# already run. `constant-return` IS the null stub, which is two constant-return
# modules carrying deliberately disagreeing constants; `import-only` IS the null
# stub at import time, where the names resolve and nothing is called, and a test
# satisfied by that is already labelled vacuous. Shipping either would spend a
# whole pytest session per probe re-measuring what is measured. `echo` is not in
# the design and is here because a pass-through satisfies the "it did something
# to the input" assertion that neither of the other two touches.
CANDIDATES = ("none", "echo", "greedy")

# Prepended to the greedy payload so the joined string can never EQUAL any single
# literal the contract wrote. That is the property the whole candidate rests on:
# `assert "refused" in render()` is satisfied and `assert render() == "refused"`
# is not, which is exactly the difference between a substring grep and an
# assertion about a value. It also names the instrument, so a payload that leaks
# into a failure message tells its reader where it came from.
GREEDY_MARKER = "canopus-pass-candidate"


class Candidate:
    """A stand-in for an implementation that EXISTS and is wrong.

    Deliberately NOT a `Stub` subclass, and the separation is the point. `Stub`
    carries a differential value set, because the null stub's whole verdict is
    "did this outcome move when the value moved". A candidate carries no such
    axis: it is ONE wrong implementation and the question is simply whether the
    contract accepts it. Sharing a class would put a `_values` dict on an object
    that has nothing to vary, and the first reader to see it would wire a
    verdict to it.

    Dunder ATTRIBUTE access raises, exactly as `Stub` refuses it and for the
    identical reason: the import machinery reads dunders to decide HOW to
    import, so a stand-in answering `__path__` masquerades as a package and the
    candidate runs would resolve a different import graph from the stub runs
    they are compared against.

    Attribute access returns a SIBLING rather than the mode's value, so
    `module.thing.other()` reaches a call. Only a call answers, because a
    contract reads the subject's behaviour through calls; an attribute that
    already answered `None` would make `mod.CONST` unusable in the same
    expression that calls `mod.func()`.
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

    Built from the contract's OWN literals and nothing else. A candidate
    carrying an alphabet, or a random blob, would satisfy substring assertions
    the contract never wrote and manufacture refusals against honest tests; this
    one satisfies exactly the greps the contract itself performs.

    Sorted and de-duplicated so the payload is a function of the SET alone. Two
    runs of one contract that differed only in iteration order would otherwise be
    two different probes.
    """
    return "\n".join([GREEDY_MARKER, *sorted(set(literals))])


def candidate_value(name: str, literals):
    """The stand-in one named candidate installs. Raises on a name it lacks.

    A silent default would be a run that measured a candidate nobody chose: the
    child reads its candidate from the environment, so a typo on the parent side
    arrives here, and defaulting to `none` would produce a full green table under
    a candidate the report then names as something else.
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

    Two handlers below treat "resolving this name blew up" as "this name does
    not resolve", which is the safe over-claim direction but is also exactly the
    shape of a defect that hides. The probe child's stderr reaches the caller, so
    the alternative reading of a stubbed name stays visible in the run that made
    the decision.
    """
    sys.stderr.write(f"{NULLSTUB_STDERR_MARKER} {message}\n")


def _supply_absent_attributes(module):
    """Answer the names a real module lacks with a stub, keeping the ones it has.

    Used from two places that must behave identically: the wrapping loader, for a
    module this probe imported, and `pytest_configure`, for a module that was
    already in `sys.modules` before the plugin armed. One implementation, because
    two would drift and only one of them is covered by any given test.

    A module's own PEP 562 `__getattr__` still answers first and the stub catches
    only what it declines, so a real dynamic attribute is never replaced.

    Returns the pair `(existing, supply)`: what was there before, and what was
    installed. `pytest_unconfigure` needs both to give a live module its own
    surface back, and to leave alone one that somebody else has since rewritten.
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


# The attribute a REPLACING supplier carries its module's own values on. Written
# onto the supplier rather than into `_Installation`, so `pytest_unconfigure`
# keeps walking the ONE list it already walks and undoes both surfaces in the
# same pass, under the same identity guard. A second list would be a second
# teardown that only one test ever covers, and the surface that stops being
# undone is a module left answering `None` to every name in a process that keeps
# running.
_REPLACED_ATTRIBUTE = "canopus_replaced"


def replace_attributes(module, mode: str, payload: str):
    """Answer a module's OWN names with one candidate, and its absent ones too.

    A superset of `_supply_absent_attributes`, never a rival to it: the names a
    module lacks are still answered, and the names it HAS are answered as well.
    That second half is the whole of this function. Python consults a module's
    PEP 562 `__getattr__` only for a name the module does not have, so a probe
    that installs one and nothing else reaches exactly the code that has not
    been written yet. Pointed at shipped code it reports a clean page over a
    suite whose subject never changed, and a clean page nobody measured is the
    reading this instrument exists to refuse.

    The module's own `__getattr__` is NOT chained to, and this is the one place
    this function deliberately disagrees with the absent-name path. There the
    chain protects a real dynamic attribute from being swapped for a stub;
    here a real dynamic attribute is precisely what the candidate is standing
    in for, and deferring to it would leave one class of value answering
    honestly while every other value in the same module answered wrong. A
    module half-replaced is not a wrong implementation, it is a mixture, and no
    verdict can be read off a mixture.

    That choice leaves ONE asymmetry a contract author can meet, stated here
    rather than left to be guessed at. A name the module HAD answers the
    candidate's VALUE, so `subject.REAL` reads `None` under `none`. A name it
    lacks, which includes every name a real PEP 562 `__getattr__` used to
    serve, answers a `Candidate` OBJECT, so `subject.DYNAMIC` reads a stand-in
    that answers `None` only when it is CALLED. Measured on a module carrying
    its own `__getattr__`: `REAL` gave `None` and `DYNAMIC` gave a `Candidate`.
    The absent-name half cannot do better, because a name nobody ever bound has
    no recorded shape to imitate and a callable stand-in is the more useful of
    the two guesses; see `Candidate` for why only a call answers there.

    DUNDERS SURVIVE UNTOUCHED, on the rule `_stub_attribute` and both stand-in
    classes already follow. `__name__`, `__file__`, `__path__`, `__spec__`,
    `__loader__`, `__package__`, `__doc__` and `__builtins__` are read by the
    import machinery and by pytest itself to decide HOW to handle a module;
    replacing them does not produce a measurement, it produces a failure inside
    somebody else's library, and a reader who meets one diagnoses the tool
    rather than the contract.

    WHAT a name gets depends on whether it was CALLABLE, and the split is
    load-bearing rather than tidy. `Candidate` answers on a call and returns a
    sibling on attribute access, on its own stated rule that a contract reads
    the subject's behaviour through calls. So a function, a class or any other
    callable is replaced by a candidate, and `subject.render("x")` then answers
    the candidate's value instead of its own, which is the entire point of this
    function. A plain data name is replaced by what the candidate answers to a
    call taking no arguments: `None` for `none` and for `echo`, which has no
    argument to hand back, and the joined payload for `greedy`. Replacing a
    constant with the candidate OBJECT instead was measured against the
    contract and is wrong twice over: `subject.ANSWER is None` is then false,
    and a constant that has to be CALLED to answer is not a constant a wrong
    implementation could have. Replacing a callable with the candidate's answer
    is wrong the other way: EVERY call in the contract dies on
    `'NoneType' object is not callable`, every test goes red for a reason that
    is about this instrument rather than the contract, no candidate takes
    anything, and the probe reports a clean page for the second time by a new
    route.

    The split MOVES that instrument-shaped failure to the edges; it does not
    remove it, and the boundary is worth knowing before reading a candidate
    run. A class is callable, so it becomes a candidate, so calling it answers
    the mode's value, so the method call on the result dies: measured,
    `Widget().render()` raised `AttributeError: 'NoneType' object has no
    attribute 'render'`. A constant that a contract subscripts dies the
    matching way: measured, `CONFIG['a']` raised `TypeError: 'NoneType' object
    is not subscriptable`. Both are one call deeper than the plain
    `subject.render("x")` the split was chosen for, and both leave the test red
    rather than green, so a candidate takes FEWER tests and the probe claims
    FEWER gaps than the truth. That is the direction this instrument is allowed
    to err in, and a builder who meets one reads it as a contract the
    candidates could not measure rather than as a contract they cleared.

    ONE `Candidate` is built at the door and every callable name gets a sibling
    of it. Building it first is what validates *mode* even for a module with no
    non-dunder names to replace, on the rule `Candidate.__init__` already
    states: the child reads its candidate from the environment, so a typo on
    the parent side arrives here, and an unvalidated mode would fall through to
    the greedy branch and print a full table under a candidate nobody ran. A
    sibling per name rather than one shared object is not thrift in reverse:
    `Candidate` defines no `__eq__`, so sharing would make `module.f ==
    module.g` true for every pair of replaced callables, and a contract
    asserting an equivalence between two of the subject's names would be
    satisfied by an accident of this function instead of by the candidate's
    behaviour.

    The scope is the module's dict AT THIS INSTANT, and no later. A name bound
    after this call returns keeps whatever was bound: the name then EXISTS, so
    Python never consults `supply` for it, exactly as it never consults an
    absent-name supplier for a name that is present. Measured, a module given a
    new attribute after `pytest_configure` kept its real value. Correct and
    safe rather than a hole, on two counts. The subject's own module-scope code
    has already finished by the time this runs, so what binds a name afterwards
    is the contract or a fixture, and a value the CONTRACT itself wrote is not
    a value the candidate should be answering for. And the direction, as
    everywhere else here, is toward a test staying red and a candidate taking
    less.

    Returns `(existing, supply)`, the pair `_supply_absent_attributes` returns,
    so `pytest_unconfigure` gives a live module its surface back through one
    path whichever of the two installed it. The values this call overwrote ride
    along on `supply` under `_REPLACED_ATTRIBUTE`; see that constant.
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

    The ONE door both surfaces go through, because there are two of them and
    they must not disagree: the wrapping loader, for a module this probe
    imported, and `pytest_configure`, for a module that was already in
    `sys.modules` when the plugin armed. The second is the one that decides
    whether shipped code is measured at all, since an import of a module already
    in `sys.modules` short-circuits before `sys.meta_path` and the loader never
    runs for it. A switch honoured on only one of the two reads as closed while
    half the import graph keeps answering its real values.

    Read from the environment per call rather than captured at import, matching
    `_values` and `_stub_attribute`, so a test can arm the switch after this
    module is loaded.
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

    BY IDENTITY on the finder, never by position in `_INSTALLED`, and the
    difference is not bookkeeping. An earlier revision took `_INSTALLED[-1]` on
    the stated rule that "the finder that resolved this import is the one most
    recently installed"; a counter-case refuted it. With installation A claiming
    `mod_a` and a nested installation B claiming `mod_b`, importing `mod_a`
    runs A's wrapping loader, because B does not claim the name, and the record
    landed on B:

        installation[0] claims ('mod_a',) supplied=[]
        installation[1] claims ('mod_b',) supplied=['mod_a']

    B's teardown then restored `mod_a` in the middle of A's still-armed
    session. Measured, armed: `mod_a.VALUE` read `'a real'` again while A's page
    still said a candidate was armed, so A's remaining tests ran against real
    code and passed. That is the fail-open clean-page reading this whole
    instrument exists to refuse, arriving by the one door nobody watches.
    Unreachable in production, where a probe is one installation per
    subprocess; reachable in the unit tests, and `_INSTALLED` is a LIST
    precisely because nesting is contemplated.

    An unmatched finder records NOTHING, deliberately, and the asymmetry is the
    whole argument. Recording nothing leaks a mutated module into a process
    that is about to exit, which is the cost the old code paid unconditionally
    and which no verdict depends on. Recording against the wrong installation
    DISARMS a live probe mid-session and turns its remaining tests green. One
    of those two directions is survivable and the other is the failure this
    tool is built to make impossible, so the tie is broken toward silence. The
    unmatched case is what a caller constructing `_WrapLoader` directly hits,
    which the unit tests do.
    """
    for installation in _INSTALLED:
        if installation.finder is finder:
            existing, supply = installed
            installation.supplied.append((module, existing, supply))
            return


class _WrapLoader(Loader):
    """Runs the real loader, then supplies the names the module lacks.

    The catch-all `__getattr__` delegation is not decoration. A loader is read
    for far more than create/exec: `get_source`, `get_filename`, `is_package`,
    `get_data` and `get_resource_reader` are pulled off `spec.loader` by
    importlib.reload, importlib.resources, pkgutil and inspect.getsource. A
    wrapper answering only two of them narrows the real loader for the length of
    the probe, and the failure surfaces as an unrelated AttributeError inside
    somebody else's library.

    `finder` is the `_NamedFinder` that built this loader, carried so
    `exec_module` can file the module it wrapped against the ONE installation
    that owns it. It defaults to None for a caller that builds this loader
    directly, which records nothing; see `_record_installation`.
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
        # An earlier revision dropped this pair, defending it with the claim
        # that nothing outside the probe holds the module. `sys.modules` holds
        # it, and the claim was measured false after it was written: under an
        # armed candidate a module wrapped here still answered `None` to every
        # name AFTER `pytest_unconfigure`, was still in `sys.modules`, and a
        # re-import handed the same dead subject back. Under the absent-name
        # supplier alone that leak was additive and harmless, which is how the
        # false comment survived being read; replacement changes its class,
        # because the module is DESTROYED rather than extended, and every later
        # reader in that process gets a subject whose every value is None.
        #
        # What restoration hands back here is the module's REAL values, not a
        # stub's. The real loader ran on the line above, so the dict
        # `replace_attributes` snapshotted is the genuine one.
        #
        # Filed against the installation whose finder built this loader, by
        # identity. An earlier revision took `_INSTALLED[-1]` and asserted the
        # LIFO rule as fact; a nested counter-case refuted it and disarmed a
        # live probe mid-session. See `_record_installation` for the
        # measurement and for why an unmatched finder records nothing.
        _record_installation(self._finder, module, installed)


class _NamedFinder(MetaPathFinder):
    """Claims exactly the modules the contract's AST named, and no others.

    INSERTED at the front, because it must claim a named module before
    PathFinder resolves it unwrapped. It is safe there only because the claim is
    narrow: an earlier revision answered every otherwise-failing import and made
    a stub the parent package of the collected test module. That was measured
    under `--import-mode=importlib`, which this repository pins in
    `pyproject.toml` for the gate and which `canopus_contract.run_pytest_report`
    now pins explicitly on the probe child's own command line, so it is the mode
    this finder runs in and not merely one it could be run in.

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

    def _must_be_a_package(self, fullname: str) -> bool:
        """True when the claim set names something BELOW this name.

        The builder splitting `scripts/utils/foo.py` into a package
        `scripts/utils/foo/` writes `from scripts.utils.foo.api import build`
        while `foo` is still a plain module on disk. Python raises on the
        parent's missing `__path__` BEFORE `sys.meta_path` is consulted for the
        child, so the finder is never asked about `api` at all and the test stays
        red for its original reason: the escape this instrument exists to close.
        Claiming the prefix in `_expand_claims` is only half of it, measured; the
        real module still resolves here and would be wrapped, and the wrapped
        module is still not a package. This is the other half.

        Derived from the claim set rather than passed in, so the two halves
        cannot disagree. A claimed name with no claimed children is untouched by
        this: a contract importing a name FROM a real plain module wants that
        module's real values.

        The trade this makes, named plainly: a claimed plain module that ALSO
        has a claimed child loses its OWN real values, not only the child's
        absent name. `_stub_spec` keeps a package stub's real search locations
        when there are any, but a plain module has none to keep, so the
        terminal module becomes an empty package stub and every one of its own
        attributes reads a Stub too. Measured with claims `{flatc, flatc.child}`:
        `flatc.CONST` reads a stub, not its real value. The direction is mostly
        toward refusal - `assert CONST == 5` stays red under both value sets and
        is not called vacuous - but `assert CONST == OTHER_CONST` then reads
        stub against stub, True under both, and a genuine test is labelled
        vacuous. Accepted because the alternative - wrapping the terminal
        module instead of stubbing it - reopens the parent-`__path__` escape
        this method exists to close, and a contract importing both a plain
        module's own values and a name below it is the rarer shape.
        """
        prefix = f"{fullname}."
        return any(name.startswith(prefix) for name in self._names)

    def _stub_spec(self, fullname: str, real) -> ModuleSpec:
        """A package stub, carrying real search locations when there are any.

        A PEP 420 namespace package resolves to a spec whose loader is None and
        whose `submodule_search_locations` is a real directory list. Dropping
        those locations for an empty `__path__` replaces every ALREADY-WRITTEN
        module below it with a stub, and `assert helper.CONST == helper.CONST`
        then reads stub against stub: True under both value sets, a genuine test
        labelled vacuous, a good contract refused. Keeping them lets the real
        children resolve while the absent ones still reach this finder.
        """
        spec = ModuleSpec(fullname, _StubLoader(), is_package=True)
        locations = None if real is None else real.submodule_search_locations
        if locations:
            spec.submodule_search_locations = list(locations)
        return spec

    def find_spec(self, fullname, path=None, target=None):
        """Resolve a claimed name, stubbing anything that will not resolve.

        On an exception from the resolution the decision is deliberate: STUB and
        report, never propagate. `importlib.util.find_spec` EXECUTES the ancestor
        packages' `__init__.py`, so arbitrary first-party code runs inside this
        call and can raise anything. Letting it out turns one contract's defect
        into a crash in whatever import triggered the lookup, and a probe child
        that dies returns no report at all, which the caller cannot tell from any
        other crash. Stubbing keeps the claim, and a claim can only ever refuse a
        contract, never wave one through. That asymmetry is the whole argument:
        an unclaimed name leaves the test red for its original reason and the
        vacuity rule silently cannot fire.

        A first-party CIRCULAR import arrives by the same door: `find_spec` on a
        submodule reads the parent's `__path__`, and a parent still being
        initialised has none. That genuine defect is stubbed and its test can
        earn a vacuity label. The direction is toward REFUSAL, and the refusal
        text names the alternative readings.
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

    Measured: claiming `ghost.sub` ALONE makes `from ghost.sub import thing` die
    with `ModuleNotFoundError: No module named 'ghost'`, and the finder is never
    consulted for the child at all, because Python resolves the parent first. The
    test then stays red under both stubs and is never labelled, so a vacuous test
    escapes the verdict entirely. Claiming both names made the same import
    succeed.

    A prefix that RESOLVES TO A PACKAGE is deliberately NOT claimed: `PathFinder`
    handles it, and claiming it would wrap a real package for nothing. Measured
    on this repository, `brandnew.pkg.mod` expands to all three levels while a
    dotted name under `scripts.utils` claims only the full name, leaving
    `scripts` and `scripts.utils` untouched. This is what stops the previous
    design's blast radius returning through the prefix door.

    A prefix that resolves to a plain MODULE counts as not resolving, and is
    claimed. It cannot carry a child: measured, `from plain_d.child import thing`
    dies on `'plain_d' is not a package` before `sys.meta_path` is reached, so
    the finder is never asked about the child and the test stays red for its
    original reason. That is the builder splitting one module into a package, and
    it is the severe direction, because a name the finder fails to claim means
    the vacuity refusal silently cannot fire. `_NamedFinder._must_be_a_package`
    is the other half of this fix; neither half moves the import alone.

    An exception is treated the same way, and reported. `find_spec` EXECUTES the
    ancestor packages' `__init__.py`, so a `RuntimeError` from real first-party
    code reaches here; unhandled it takes `pytest_configure` down, and a pytest
    INTERNALERROR is a probe child that returns no test report at all.

    `SystemExit` is caught alongside `Exception`, deliberately, and the two are
    not the same hazard. `SystemExit` derives from `BaseException`, so an
    ordinary `sys.exit(0)` in an ancestor's `__init__.py` — a version check, a
    dependency bail-out — walks straight past a handler that only names
    `Exception`. Inside `pytest_configure` that was already contained: the
    escape crashes the probe CHILD, and this module's own caller reads a child
    that returns no JUnit report as a refusal, never a pass. `canopus_contract`
    also calls this function directly, in the PARENT process, to predict the
    child's claim before spawning it; there is no child boundary to contain an
    escape there, and `cmd_freeze` wraps that call in `except ContractError`,
    which `SystemExit` also walks past. Measured before this handler named it:
    a claim of `sneaky.mid.leaf` over an ancestor `sneaky/__init__.py` calling
    `sys.exit(0)` exited the CLI at 0, having written no manifest and printed
    nothing — a contract measured as nothing, read as a clean pass, which is
    the exact failure this instrument exists to refuse. `KeyboardInterrupt`
    keeps propagating; it is not caught here, only `Exception` and `SystemExit`.

    Resolution happens here, once, BEFORE the finder is installed. Doing it inside
    `find_spec` would re-enter the finder being constructed.
    """
    claimed = set()
    for name in names:
        parts = name.split(".")
        if parts[0] in BUILTIN_MODULE_NAMES:
            # Dropped HERE rather than in the finder, so the one exclusion covers
            # both surfaces that consult a claim: `_NamedFinder.find_spec`, and
            # the `pytest_configure` loop that supplies absent attributes onto
            # modules already in `sys.modules`. The second is the one that
            # actually broke the apparatus, and a fix applied only to the first
            # would have left it broken while looking closed -- the exact
            # one-of-two-surfaces defect the wire 3.1 review found at a task
            # seam. See BUILTIN_MODULE_NAMES for the measurement.
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

    Three things now need undoing rather than one: the finder on `sys.meta_path`,
    the attribute suppliers written onto modules that were already imported, and
    the empty `__path__` given to an already-imported PLAIN module that has to
    carry a claimed child. Recording them together keeps teardown an inverse of
    install rather than a filter over whatever the process happens to be
    carrying.
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

    The second half is not decoration. The finder only ever sees imports that
    reach `sys.meta_path`, and an import of a module already in `sys.modules`
    short-circuits there, so a perfectly good claim is never consulted. Measured
    on this repository: the root conftest imports `scripts.utils.venv` at module
    level and initial conftests load BEFORE a `-p` plugin's `pytest_configure`,
    so a contract naming that module stayed red for its original reason under
    BOTH value sets. Both runs agreeing "red" is the reading that never fires the
    vacuity rule, and a contract asserting nothing would have been frozen.

    Live modules are supplied in place, never evicted and never reloaded: a
    reload would re-run first-party module-level code in the middle of a session
    that has already bound names from it.

    A live PLAIN module that has to carry a claimed child is also given an empty
    `__path__`, which is the same fix `_NamedFinder._must_be_a_package` makes on
    the other surface, applied here because a module already in `sys.modules`
    never reaches the finder at all. Without it, `from plain.child import thing`
    resolves the parent out of `sys.modules`, reads no `__path__`, and raises
    `ModuleNotFoundError: ... is not a package` BEFORE `sys.meta_path` is
    consulted for the child. Measured through the CLI: the test then stayed red
    for its original reason under both value sets, the vacuity rule never fired,
    and a contract asserting nothing would have been frozen with no refusal, no
    diagnostic line and no unknown-vacuity stamp. The identical assertion behind
    an ABSENT parent was correctly refused, so the two shapes disagreed on one
    accident of import order.

    The `__path__` is EMPTY rather than the module's own directory, and never
    overwrites one the module already has. An empty list sends every name below
    it to `sys.meta_path`, where the finder answers the claim; a live PACKAGE
    already carries real search locations, and replacing them would hide the
    modules ALREADY WRITTEN below it behind stubs, which is the fail-open
    `_stub_spec` refuses on the finder's side.
    """
    names = [
        name
        for name in os.environ.get(MODULES_VAR, "").split(STUB_NAME_SEPARATOR)
        if name
    ]
    if not names:
        # RECORDED, never returned on silently. `pytest_unconfigure` pops one
        # record per call, so a configure that armed nothing and recorded
        # nothing left the pair unbalanced and the pop landed on somebody
        # else's installation. Measured, in process: an inner configure with an
        # empty claim set followed by its own unconfigure restored the OUTER
        # probe's replaced module and took the outer finder off `sys.meta_path`
        # while that probe was still running, so its remaining tests ran
        # against real code and passed. Same failure as the LIFO
        # misattribution `_record_installation` refuses, reached through the
        # one door that looked like it did nothing at all.
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

    By IDENTITY, not by type. Filtering every `_NamedFinder` off `sys.meta_path`
    reads as correct in a probe child that installs exactly one, and disarms the
    OTHER session's finder the moment there are two: a second registration, or a
    nested in-process probe. Every claimed import then resolves for real, which
    is the under-claim direction and silent.

    The live modules are given their own surface back for the same reason the
    finder is removed: the probe child exits straight after and nothing
    observable depends on it today, but a module mutated in place outlives any
    process boundary reasoning, and a supplier somebody else has since replaced
    is left alone rather than clobbered.

    The empty `__path__` given to a live plain module is taken back on the same
    rule, and by IDENTITY for the same reason: a plain module left carrying one
    is a plain module the rest of the session treats as a package, and a
    `__path__` somebody else has since written is not this installation's to
    remove.
    """
    if not _INSTALLED:
        return
    installation = _INSTALLED.pop()
    if os.environ.get(CANDIDATE_VAR, "") and os.environ.get(REPLACE_VAR, ""):
        # Said HERE rather than at install time, because the set is complete
        # only now: `pytest_configure` replaces the modules already imported and
        # the wrapping loader adds one per import for the rest of the session.
        # This is the whole record of what a wrong implementation actually stood
        # in for, and the parent has no other way to know it: a claim reaches
        # only the names an import reached, so a module named by the contract's
        # source and imported by nothing is replaced by nothing.
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
