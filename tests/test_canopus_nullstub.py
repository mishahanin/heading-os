"""The stub object and the finder, tested at the mechanism.

Each test drives the piece directly rather than launching pytest, so a failure
names the mechanism rather than a child process's exit code. The end-to-end path
is Task 4's.
"""
import sys
from types import ModuleType

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


def test_a_stub_has_no_dict_because_slots_is_pinned():
    """`__slots__` is what makes this refusal true, not `__getattr__`.

    Normal attribute lookup resolves `__dict__` before `__getattr__` is ever
    consulted, so the dunder guard above cannot protect this one. Only
    `__slots__ = ("_values",)` keeps `stub.__dict__` from existing at all; drop
    it and the stub starts exposing `{'_values': ...}` to anything that
    introspects it.
    """
    from scripts.utils.canopus_nullstub import Stub

    stub = Stub({"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"})

    assert not hasattr(stub, "__dict__")


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


def test_stub_equality_is_not_a_differential_channel():
    """Equality must agree under both value sets, on purpose: a differential
    `__eq__` would let `assert thing() == thing()` escape vacuity detection
    instead of being counted vacuous like every other value-less assertion.
    """
    from scripts.utils.canopus_nullstub import STUB_VALUES, Stub

    stub_a = Stub(STUB_VALUES["A"])
    stub_b = Stub(STUB_VALUES["B"])

    assert stub_a == stub_b
    assert stub_b == stub_a


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


@pytest.fixture
def clean_imports():
    """Restore sys.meta_path, sys.modules AND the plugin's install ledger.

    sys.modules is half the fixture, not tidiness: without it the session keeps a
    stubbed module under a plain name for every later test in the run, and the
    next reader who reuses that name gets a stub and an order-dependent failure a
    long way from here.

    The ledger is the third half, added when `pytest_configure` started
    supplying absent attributes on modules that were ALREADY imported. Those
    modules are mutated in place, so resetting the ledger record alone is not
    enough: a test that configures and never unconfigures would leave the
    mutation itself standing on any module the `sys.modules` diff below does
    not delete (every module that was already imported before the test ran),
    leaking a supplied `__getattr__` into every later test in the run. So
    before the ledger is reset, each entry the test added is torn down through
    `pytest_unconfigure` itself - the same call the test's own teardown would
    make - and only then is the ledger snapshot restored underneath it.
    """
    from scripts.utils import canopus_nullstub

    saved_meta_path = list(sys.meta_path)
    saved_modules = dict(sys.modules)
    saved_installed = list(canopus_nullstub._INSTALLED)
    yield
    while len(canopus_nullstub._INSTALLED) > len(saved_installed):
        canopus_nullstub.pytest_unconfigure(config=None)
    canopus_nullstub._INSTALLED[:] = saved_installed
    sys.meta_path[:] = saved_meta_path
    for name in set(sys.modules) - set(saved_modules):
        del sys.modules[name]
    sys.modules.update(saved_modules)


def test_a_named_module_that_does_not_resolve_becomes_a_stub(
    clean_imports, monkeypatch
):
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"canopus_absent_fixture"}))

    from canopus_absent_fixture import answer

    assert len(answer()) == 7


def test_a_named_module_that_resolves_supplies_only_what_it_lacks(
    clean_imports, tmp_path, monkeypatch
):
    """The half a sink cannot see, and the state a retake is taken in.

    Where the module EXISTS and the name does not, no finder is consulted at all
    under ordinary import: the module was found. Both assertions matter. The
    first proves the absent name is supplied; the second proves the present one
    is NOT replaced, which is what stops a good contract being mislabelled.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "halfbuilt_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"halfbuilt_fixture"}))

    import halfbuilt_fixture

    assert halfbuilt_fixture.EXISTS == 1
    assert len(halfbuilt_fixture.NOT_THERE_YET) == 7


def test_a_module_the_contract_did_not_name_is_never_claimed(
    clean_imports, tmp_path, monkeypatch
):
    """The blocker that killed the previous design, pinned as a test.

    An earlier revision answered EVERY otherwise-failing import. Under this
    repository's `--import-mode=importlib`, pytest builds the parent packages of
    each collected test module inside `try: importlib.import_module(parent) /
    except ModuleNotFoundError`, so a global answer made a stub the parent
    package of the collected module.

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

    with pytest.raises(AttributeError):
        unnamed_fixture.no_such_attribute  # noqa: B018 - the access is the assertion


def test_the_finder_survives_a_named_package_chain(
    clean_imports, tmp_path, monkeypatch
):
    """Resolving a spec re-walks sys.meta_path and reaches this finder again.

    Without the re-entrancy guard the resolution recurses until the interpreter
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


def test_a_wholly_absent_dotted_import_is_stubbed_not_killed_on_its_parent(
    clean_imports, monkeypatch
):
    """The escape the prefix expansion closes, watched escaping first.

    Python resolves `brandnew_fixture` before it ever asks about
    `brandnew_fixture.sub`, so a finder claiming only the full dotted name is
    never consulted for the child and the import dies on the parent. The test
    then stays red under both stubs and is never labelled, which is an escape,
    not a false accusation.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder, _expand_claims

    monkeypatch.setenv(VALUES_VAR, "B")
    claims = _expand_claims({"brandnew_fixture.sub"})

    assert claims == {"brandnew_fixture", "brandnew_fixture.sub"}

    sys.meta_path.insert(0, _NamedFinder(claims))
    from brandnew_fixture.sub import thing

    assert len(thing()) == 7


def test_a_resolvable_prefix_is_left_to_the_real_finder(
    clean_imports, tmp_path, monkeypatch
):
    """Minimal expansion, or the previous design's reach returns by another door.

    The resolvable prefix is a package written here rather than one of this
    repository's own files. The earlier version read
    `scripts.utils.canopus_contract`, which made the test's whole discriminating
    power depend on that file continuing to exist under that name: in a tree
    where it had been renamed the prefix stopped resolving, the expansion
    legitimately grew, and the mutation this test exists to kill would have
    survived while the test still looked like it was watching for it. A package
    created inside `tmp_path` cannot be renamed out from under it.
    """
    from scripts.utils.canopus_nullstub import _expand_claims

    package = tmp_path / "realpkg_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    assert _expand_claims({"realpkg_fixture.absent_child"}) == {
        "realpkg_fixture.absent_child"
    }


def test_the_plugin_installs_nothing_when_unconfigured(clean_imports, monkeypatch):
    """An accidental load outside a probe must change nothing.

    The plugin is passed with `-p` by the probe alone, but a plugin that arms
    itself on import would arm itself in any session that ever names it.

    Also covers `pytest_unconfigure` on a session that never configured:
    pytest always calls `pytest_unconfigure` at session end, even one that
    returned early here, so dropping its `if not _INSTALLED: return` guard
    makes `.pop()` raise `IndexError` on the empty ledger - a pytest
    INTERNALERROR that takes the whole session down.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        pytest_configure,
        pytest_unconfigure,
    )

    monkeypatch.delenv(MODULES_VAR, raising=False)
    before = list(sys.meta_path)
    pytest_configure(config=None)

    assert sys.meta_path == before

    pytest_unconfigure(config=None)  # must survive an empty ledger

    assert sys.meta_path == before


# The eight tests above are the brief's exact corpus. The eight below were added
# during this task's own mutation-matrix pass, each one closing a branch that
# survived deletion: the stub module's dunder guard, the wrap loader's catch-all
# delegation and its create_module, the two halves of the existing-`__getattr__`
# chain, the `_claims` prefix arm, the release half of the re-entrancy guard, the
# `loader is None` namespace-package arm, and both directions of install/remove.


def test_a_stub_module_refuses_dunder_attributes(clean_imports, monkeypatch):
    """A stub module answering `__all__` would change how Python imports it.

    The guard lives in `_stub_attribute`, and PEP 562 module `__getattr__` is
    consulted for dunders too, so without it `from mod import *` reads a Stub as
    the export list instead of raising.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    monkeypatch.setenv(VALUES_VAR, "A")
    sys.meta_path.insert(0, _NamedFinder({"dunder_absent_fixture"}))

    import dunder_absent_fixture

    with pytest.raises(AttributeError):
        dunder_absent_fixture.__all__  # noqa: B018 - the access is the assertion


def test_a_wrapped_loader_keeps_the_real_loaders_whole_surface(
    clean_imports, tmp_path, monkeypatch
):
    """`get_filename`, `is_package` and `get_source` are read off `spec.loader`.

    importlib.reload, importlib.resources, pkgutil and inspect.getsource all pull
    methods the wrapper does not define, so dropping the catch-all delegation
    narrows the real loader for the length of the probe.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    (tmp_path / "surface_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    finder = _NamedFinder({"surface_fixture"})

    spec = finder.find_spec("surface_fixture")

    assert spec.loader.get_filename("surface_fixture").endswith("surface_fixture.py")
    assert spec.loader.is_package("surface_fixture") is False
    assert "EXISTS" in spec.loader.get_source("surface_fixture")


def test_a_wrapped_loader_delegates_module_creation(clean_imports):
    """`create_module` must hand back what the real loader built.

    A source loader returns None here and lets the machinery build the module, so
    the delegation is invisible for plain `.py` files; a loader with a real
    `create_module` (an extension module, or a loader of somebody else's) is the
    case that breaks when the wrapper answers for itself.
    """
    from scripts.utils.canopus_nullstub import _WrapLoader

    built = ModuleType("built_fixture")

    class RealLoader:
        def create_module(self, spec):
            return built

        def exec_module(self, module):
            return None

    assert _WrapLoader(RealLoader()).create_module(spec=None) is built


def test_a_wrapped_module_keeps_its_own_getattr_and_still_gains_a_stub(
    clean_imports, tmp_path, monkeypatch
):
    """Both halves of the chain, in one module.

    `OWN` proves the module's own PEP 562 `__getattr__` still answers first, so
    the wrapper does not silently replace a real dynamic attribute with a stub.
    `NOT_THERE_YET` proves the AttributeError fallback still reaches the stub, so
    a module that declines a name is not left declining it.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "ownattr_fixture.py").write_text(
        "def __getattr__(name):\n"
        "    if name == 'OWN':\n"
        "        return 'own'\n"
        "    raise AttributeError(name)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"ownattr_fixture"}))

    import ownattr_fixture

    assert ownattr_fixture.OWN == "own"
    assert len(ownattr_fixture.NOT_THERE_YET) == 7


def test_a_submodule_below_a_claimed_name_is_claimed_too(clean_imports, monkeypatch):
    """The prefix arm of `_claims`, which `_expand_claims` cannot stand in for.

    Expansion only walks the names the AST saw. A contract naming `pkg` and then
    reaching `pkg.deep` at runtime gets a stub package whose `__path__` is empty,
    so unless `_claims` also matches everything BELOW a claimed name the child
    import dies and the test stays red for its original reason.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"branchy_fixture"}))

    from branchy_fixture.deep import thing

    assert len(thing()) == 7


def test_the_re_entrancy_guard_is_released_after_each_lookup(clean_imports):
    """The guard has two halves and only one of them is the recursion stop.

    Marking a name busy without discarding it again leaves the finder permanently
    deaf to that name, so the second import of the same module in one session
    escapes the claim entirely.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    finder = _NamedFinder({"twice_absent_fixture"})

    assert finder.find_spec("twice_absent_fixture") is not None
    assert finder.find_spec("twice_absent_fixture") is not None


def test_a_claimed_namespace_directory_is_stubbed_not_wrapped(
    clean_imports, tmp_path, monkeypatch
):
    """A directory with no `__init__.py` resolves to a spec whose loader is None.

    Wrapping that None produces a loader that raises AttributeError on the first
    `create_module`, which surfaces as an unrelated crash rather than a stub, so
    the `loader is None` arm sends it down the stub path instead.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "nsdir_fixture").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"nsdir_fixture"}))

    import nsdir_fixture

    assert len(nsdir_fixture.NOT_THERE_YET) == 7


def test_a_claimed_name_whose_resolution_raises_is_stubbed_not_propagated(
    clean_imports,
):
    """`find_spec` does not only return None for an absent name, it also raises.

    A module already in `sys.modules` carrying `__spec__ = None` makes
    `importlib.util.find_spec` raise ValueError, and a circular first-party
    import reaches the same handler by a different door. Both must land on the
    stub path, because letting the exception out turns a contract defect into an
    unrelated crash inside the probe.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    specless = ModuleType("specless_fixture")
    specless.__spec__ = None
    sys.modules["specless_fixture"] = specless

    assert _NamedFinder({"specless_fixture"}).find_spec("specless_fixture") is not None


def test_expanding_a_deep_absent_name_claims_every_level(clean_imports):
    """Resolving an intermediate prefix raises when ITS parent is absent too.

    `find_spec("ghostly_fixture.mid")` does not return None when
    `ghostly_fixture` does not exist, it raises ModuleNotFoundError, so the
    expansion has to catch as well as test. Unhandled, a three-level contract
    import would take the whole probe down.
    """
    from scripts.utils.canopus_nullstub import _expand_claims

    assert _expand_claims({"ghostly_fixture.mid.leaf"}) == {
        "ghostly_fixture",
        "ghostly_fixture.mid",
        "ghostly_fixture.mid.leaf",
    }


def test_the_plugin_installs_the_expanded_claim_at_the_front(
    clean_imports, monkeypatch
):
    """The positive half of the unconfigured test, which alone pins nothing.

    Position 0 is the assertion, not tidiness: behind PathFinder the finder never
    sees a name that resolves, so a named-but-real module is never wrapped.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        _NamedFinder,
        pytest_configure,
    )

    monkeypatch.setenv(MODULES_VAR, "configured_fixture.sub")
    pytest_configure(config=None)
    installed = sys.meta_path[0]

    assert isinstance(installed, _NamedFinder)
    assert installed.find_spec("configured_fixture") is not None
    assert installed.find_spec("configured_fixture.sub") is not None


def test_unconfigure_removes_this_finder_and_leaves_the_rest(
    clean_imports, monkeypatch
):
    """Removal must be exactly the inverse of install, by IDENTITY.

    Both directions are wrong in a way the other cannot see: leaving the finder
    behind outlives the probe, and filtering the wrong way round takes the real
    importers out with it.

    The foreign finder is the third way to be wrong, and the reason this test
    installs one it did not ask for. Removing every `_NamedFinder` on
    `sys.meta_path` by type reads as correct in a probe child that installs
    exactly one, and disarms the OTHER session's finder the moment there are
    two: a second registration, or a nested in-process probe. Every claimed
    import then resolves for real, which is the under-claim direction, silently.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        _NamedFinder,
        pytest_configure,
        pytest_unconfigure,
    )

    foreign = _NamedFinder({"foreign_fixture"})
    sys.meta_path.insert(0, foreign)
    monkeypatch.setenv(MODULES_VAR, "unconfigure_fixture")
    before = list(sys.meta_path)
    pytest_configure(config=None)
    installed = sys.meta_path[0]

    assert installed is not foreign
    assert any(entry is installed for entry in sys.meta_path)

    pytest_unconfigure(config=None)

    assert sys.meta_path == before
    assert any(entry is foreign for entry in sys.meta_path)
    assert all(entry is not installed for entry in sys.meta_path)


# The tests below were added by the fix round that answered this task's review.
# Each pins one escape a reviewer measured on the shipped code: a claimed module
# already imported before the plugin armed, a dotted name whose resolvable prefix
# is a plain module rather than a package, an exception thrown by real ancestor
# code during resolution, a real PEP 420 namespace package losing its real
# children, and the live `__spec__` of an imported module being mutated in place.
# The last three pin branches that are correct today and that nothing killed.


def test_a_module_already_imported_when_the_plugin_arms_is_supplied_too(
    clean_imports, tmp_path, monkeypatch
):
    """The finder cannot see an import that never reaches `sys.meta_path`.

    An import of a module already in `sys.modules` short-circuits there, so the
    claim is real and the wrap never happens. Measured on this repository: the
    root conftest imports `scripts.utils.venv` at module level, initial conftests
    load BEFORE a `-p` plugin's `pytest_configure`, and a contract naming that
    module therefore stayed red for its original reason under BOTH value sets.
    Both runs agreeing "red" is the reading that never fires the vacuity rule, so
    a contract asserting nothing would have been frozen.

    Both assertions matter, and in this order. The absent name proves the stub is
    supplied through the module that was already there; the present one proves
    the real module was neither evicted nor reloaded, which is what keeps a
    genuine assertion measurable.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
    )

    (tmp_path / "preimported_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import preimported_fixture

    monkeypatch.setenv(MODULES_VAR, "preimported_fixture")
    pytest_configure(config=None)

    assert preimported_fixture.EXISTS == 1
    assert len(preimported_fixture.NOT_THERE_YET) == 7


def test_an_already_imported_module_keeps_its_own_getattr_when_supplied(
    clean_imports, tmp_path, monkeypatch
):
    """The live-module path must chain exactly as the loader path does.

    A module carrying its own PEP 562 `__getattr__` answers first and the stub
    only catches what it declines; replacing it instead would swap a real dynamic
    attribute for a stub and turn a genuine assertion into a vacuous-looking one.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
    )

    (tmp_path / "liveattr_fixture.py").write_text(
        "def __getattr__(name):\n"
        "    if name == 'OWN':\n"
        "        return 'own'\n"
        "    raise AttributeError(name)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import liveattr_fixture

    monkeypatch.setenv(MODULES_VAR, "liveattr_fixture")
    pytest_configure(config=None)

    assert liveattr_fixture.OWN == "own"
    assert len(liveattr_fixture.NOT_THERE_YET) == 7


def test_unconfigure_gives_an_already_imported_module_its_surface_back(
    clean_imports, tmp_path, monkeypatch
):
    """Mutating a live module makes teardown a real obligation, not tidiness.

    The finder can be lifted off `sys.meta_path` and the session is clean again;
    an attribute supplier written onto a module that outlives the probe is not
    undone by anything, and the next reader of that module sees a stub where an
    AttributeError belongs.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
        pytest_unconfigure,
    )

    (tmp_path / "restored_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import restored_fixture

    monkeypatch.setenv(MODULES_VAR, "restored_fixture")
    pytest_configure(config=None)

    assert len(restored_fixture.NOT_THERE_YET) == 7

    pytest_unconfigure(config=None)

    assert restored_fixture.EXISTS == 1
    with pytest.raises(AttributeError):
        restored_fixture.NOT_THERE_YET  # noqa: B018 - the access is the assertion


def test_a_prefix_that_is_a_plain_module_is_claimed_and_stubbed_as_a_package(
    clean_imports, tmp_path, monkeypatch
):
    """The builder splitting one module into a package, watched escaping first.

    `from plain.child import thing` dies on the PARENT's missing `__path__`
    before `sys.meta_path` is consulted at all, so the finder is never asked
    about the child even though it would have answered. Two halves are needed and
    each is inert alone: the expansion has to CLAIM a prefix that resolves to a
    plain module, and the finder has to answer that claim with a package stub
    rather than wrapping the real module it found. Measured with only the first
    half in place, the import still died on `'plain_d' is not a package`.
    """
    from scripts.utils.canopus_nullstub import (
        VALUES_VAR,
        _NamedFinder,
        _expand_claims,
    )

    (tmp_path / "flat_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    claims = _expand_claims({"flat_fixture.child"})

    assert claims == {"flat_fixture", "flat_fixture.child"}

    sys.meta_path.insert(0, _NamedFinder(claims))
    from flat_fixture.child import thing

    assert len(thing()) == 7


def test_a_claimed_plain_module_with_no_claimed_children_is_still_wrapped(
    clean_imports, tmp_path, monkeypatch
):
    """The other side of the package rule, or the claim widens for nothing.

    A contract that imports a name FROM a real plain module wants that module's
    real values, with only the absent name supplied. Turning every claimed plain
    module into an empty package stub would replace those values with stubs and
    label a genuine assertion vacuous, which refuses a good contract.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "terminal_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"terminal_fixture"}))

    import terminal_fixture

    assert terminal_fixture.EXISTS == 1
    assert len(terminal_fixture.NOT_THERE_YET) == 7


def test_expanding_a_prefix_whose_ancestor_code_raises_does_not_kill_the_probe(
    clean_imports, tmp_path, monkeypatch, capsys
):
    """Resolving a dotted name EXECUTES the ancestor packages' `__init__`.

    A `RuntimeError` from real first-party code therefore reaches this expansion,
    and it is neither an ImportError, an AttributeError nor a ValueError. Unhandled
    it takes `pytest_configure` down, which is a pytest INTERNALERROR and a probe
    child that returns no test report at all: worse than a wrong answer, because
    the caller cannot tell it from a crash. Treating the prefix as unresolved
    claims it, which is the over-claim direction, and the name and the exception
    are reported rather than swallowed.
    """
    from scripts.utils.canopus_nullstub import _expand_claims

    package = tmp_path / "blowup_fixture"
    (package / "mid").mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (package / "mid" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    assert _expand_claims({"blowup_fixture.mid.leaf"}) == {
        "blowup_fixture.mid",
        "blowup_fixture.mid.leaf",
    }
    assert "blowup_fixture.mid" in capsys.readouterr().err


def test_find_spec_stubs_a_name_whose_ancestor_code_raises(
    clean_imports, tmp_path, monkeypatch, capsys
):
    """The same door in the finder, where the decision is deliberate.

    Letting the exception out of `find_spec` propagates it into whatever import
    triggered the lookup, so a contract naming a module below a package whose
    `__init__` throws takes the probe down instead of producing a verdict.
    Stubbing keeps the claim, which can only refuse a contract, never wave one
    through; the report is what stops the swallow being silent.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    package = tmp_path / "raiser_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    spec = _NamedFinder({"raiser_fixture.child"}).find_spec("raiser_fixture.child")

    assert spec is not None
    assert "raiser_fixture.child" in capsys.readouterr().err


def test_find_spec_stubs_a_claimed_name_whose_parent_is_absent(clean_imports):
    """The ImportError door of the same handler, reached by a real shape.

    `importlib.util.find_spec` raises ModuleNotFoundError, not None, when the
    parent of a dotted name does not exist, so this is the ordinary case for a
    contract naming code nobody has written yet. It sits beside the ValueError
    door (a `__spec__` of None) and the arbitrary-exception door above; between
    the three, narrowing the handler back to any tuple of exception types is
    caught by at least one of them.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    finder = _NamedFinder({"absentparent_fixture.child"})

    assert finder.find_spec("absentparent_fixture.child") is not None


def test_a_claimed_namespace_package_keeps_its_real_children(
    clean_imports, tmp_path, monkeypatch
):
    """A PEP 420 directory has `loader is None` AND real search locations.

    Sending it down the stub path with an EMPTY `__path__` replaces every real
    module below it with a stub, so a package holding one already-written module
    loses it the moment the contract names the package. `assert helper.CONST ==
    helper.CONST` then reads stub against stub, True under both value sets, and a
    genuine test is labelled vacuous: a good contract refused.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    package = tmp_path / "nspkg_fixture"
    package.mkdir()
    (package / "written.py").write_text("VALUE = 42\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"nspkg_fixture"}))

    import nspkg_fixture
    from nspkg_fixture.written import VALUE

    assert VALUE == 42
    assert len(nspkg_fixture.NOT_THERE_YET) == 7


def test_find_spec_does_not_mutate_the_live_spec_of_an_imported_module(
    clean_imports, tmp_path, monkeypatch
):
    """For an imported module, the spec `find_spec` returns IS `module.__spec__`.

    Assigning the wrapper onto it therefore edits the live module's own spec, and
    the module keeps a wrapping loader for the rest of the process: reachable
    through `importlib.reload` and through any direct `find_spec` call, long after
    the probe that installed it is over. Building a fresh spec keeps the wrap
    local to the answer.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder, _WrapLoader

    (tmp_path / "livespec_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    import livespec_fixture

    real_loader = livespec_fixture.__spec__.loader
    spec = _NamedFinder({"livespec_fixture"}).find_spec("livespec_fixture")

    assert isinstance(spec.loader, _WrapLoader)
    assert spec is not livespec_fixture.__spec__
    assert livespec_fixture.__spec__.loader is real_loader
    assert spec.origin == livespec_fixture.__spec__.origin


def test_a_name_that_merely_starts_with_a_claim_is_not_claimed(clean_imports):
    """The dot boundary in `_claims`, which nothing else pins.

    Dropping it (`startswith(name)` for `startswith(name + ".")`) makes a claimed
    `foo` swallow a real, unrelated `foobar`, and the module the contract never
    named comes back as a stub.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    finder = _NamedFinder({"boundary_fixture"})

    assert finder.find_spec("boundary_fixture") is not None
    assert finder.find_spec("boundary_fixture.child") is not None
    assert finder.find_spec("boundary_fixture_sibling") is None


def test_a_wrapped_module_refuses_dunder_attributes(
    clean_imports, tmp_path, monkeypatch
):
    """Dunder refusal was pinned on the stub path only, never on the wrap path.

    The two paths reach `_stub_attribute` by different routes, so a supplier that
    stopped guarding dunders on a WRAPPED module would pass every existing test.
    `__path__` is the dangerous one: a plain module answering it is treated as a
    package by the import machinery, which changes how Python imports rather than
    measuring what the contract asserts.
    """
    from scripts.utils.canopus_nullstub import VALUES_VAR, _NamedFinder

    (tmp_path / "wrapdunder_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    sys.meta_path.insert(0, _NamedFinder({"wrapdunder_fixture"}))

    import wrapdunder_fixture

    assert wrapdunder_fixture.EXISTS == 1
    with pytest.raises(AttributeError):
        wrapdunder_fixture.__path__  # noqa: B018 - the access is the assertion
    with pytest.raises(AttributeError):
        wrapdunder_fixture.__all__  # noqa: B018 - the access is the assertion


# The tests below were added by round 2 of the fix, closing seven branches a
# mutation run found correct today and unpinned: the already-imported-module
# walk narrowed to the claim set, a claimed plain module's own real values
# lost to a claimed sibling below it, survival of a non-module entry in
# sys.modules, the dot boundary in `_must_be_a_package`, `pytest_unconfigure`
# on a session that never configured, the two teardown promises in its
# docstring, and the `clean_imports` fixture undoing the mutation its ledger
# records rather than only the ledger itself.


def test_pytest_configure_does_not_supply_a_module_outside_the_claim(
    clean_imports, tmp_path, monkeypatch
):
    """The already-imported-module walk must stay inside the claim set.

    Deleting the `finder._claims(name)` half of its guard supplies EVERY
    already-imported module, not only the claimed ones - the exact blast
    radius of the design withdrawn at this module's own approval gate: any
    contract test reading an absent attribute from a module the contract never
    named would then pass under both value sets, and a good contract would be
    refused wholesale.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
    )

    (tmp_path / "claimed_preexisting_fixture.py").write_text(
        "EXISTS = 1\n", encoding="utf-8"
    )
    (tmp_path / "unclaimed_preexisting_fixture.py").write_text(
        "EXISTS = 1\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "A")

    import claimed_preexisting_fixture  # noqa: F401
    import unclaimed_preexisting_fixture

    monkeypatch.setenv(MODULES_VAR, "claimed_preexisting_fixture")
    pytest_configure(config=None)

    with pytest.raises(AttributeError):
        unclaimed_preexisting_fixture.NOT_THERE_YET  # noqa: B018 - the access is the assertion


def test_a_claimed_plain_module_with_a_claimed_child_loses_its_own_real_values(
    clean_imports, tmp_path, monkeypatch
):
    """The trade `_must_be_a_package` makes, pinned so it reads as a decision.

    A claim set naming both `flatc_fixture` and `flatc_fixture.child` makes the
    terminal plain module `flatc_fixture` become an empty package stub, so its
    own real attribute reads a Stub rather than the module's actual value. This
    is the documented cost of closing the parent-`__path__` escape: without it,
    a contract that also imports a name below the plain module would stay red
    for the wrong reason and never earn a vacuity verdict.
    """
    from scripts.utils.canopus_nullstub import (
        VALUES_VAR,
        Stub,
        _NamedFinder,
        _expand_claims,
    )

    (tmp_path / "flatc_fixture.py").write_text("CONST = 5\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")
    claims = _expand_claims({"flatc_fixture.child"})

    assert claims == {"flatc_fixture", "flatc_fixture.child"}

    sys.meta_path.insert(0, _NamedFinder(claims))
    import flatc_fixture

    assert isinstance(flatc_fixture.CONST, Stub)


def test_pytest_configure_survives_a_non_module_entry_in_sys_modules(
    clean_imports, monkeypatch
):
    """`sys.modules` can hold entries that are not modules, `None` included.

    Removing the `isinstance(module, ModuleType)` guard from the
    already-imported-module walk crashes on `module.__dict__` for a `None`
    entry, an `AttributeError` that turns `pytest_configure` into a pytest
    INTERNALERROR - a probe child returning no test report at all, which is
    worse than a wrong answer because the caller cannot tell it from a crash.
    """
    from scripts.utils.canopus_nullstub import MODULES_VAR, pytest_configure

    sys.modules["nonemod_walk_fixture"] = None
    monkeypatch.setenv(MODULES_VAR, "nonemod_walk_fixture")

    pytest_configure(config=None)  # must not raise


def test_a_name_that_merely_starts_with_a_claim_is_not_a_package_trigger(
    clean_imports,
):
    """The dot boundary in `_must_be_a_package`, the twin of `_claims`'s own.

    Comparing `name.startswith(fullname)` instead of
    `name.startswith(f"{fullname}.")` would make a claim on
    `boundary_pkg_fixture_sibling.child` also mark the unrelated
    `boundary_pkg_fixture` as needing to become a package stub, purely because
    the sibling's dotted name shares a CHARACTER prefix with it - no dot
    follows `boundary_pkg_fixture` in the sibling's own name, so the two are
    unrelated names, not parent and child.
    """
    from scripts.utils.canopus_nullstub import _NamedFinder

    finder = _NamedFinder(
        {"boundary_pkg_fixture", "boundary_pkg_fixture_sibling.child"}
    )

    assert finder._must_be_a_package("boundary_pkg_fixture") is False
    assert finder._must_be_a_package("boundary_pkg_fixture_sibling") is True


def test_unconfigure_restores_an_already_imported_modules_own_getattr(
    clean_imports, tmp_path, monkeypatch
):
    """"Always delete `__getattr__` on teardown" survives every existing test.

    Every test that already exercises the teardown restores a module whose
    `existing` supplier was `None`, so deleting `module.__getattr__`
    unconditionally looks identical to restoring it there. A live module
    carrying its OWN PEP 562 `__getattr__` before the probe ever armed - a
    lazy-import shim - must get exactly that function back, not have its
    dynamic attribute surface erased outright.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
        pytest_unconfigure,
    )

    (tmp_path / "pep562_walk_fixture.py").write_text(
        "def __getattr__(name):\n"
        "    if name == 'OWN':\n"
        "        return 'own'\n"
        "    raise AttributeError(name)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import pep562_walk_fixture

    original_getattr = pep562_walk_fixture.__getattr__
    monkeypatch.setenv(MODULES_VAR, "pep562_walk_fixture")
    pytest_configure(config=None)

    assert len(pep562_walk_fixture.NOT_THERE_YET) == 7  # the supplied stub is live

    pytest_unconfigure(config=None)

    assert pep562_walk_fixture.__getattr__ is original_getattr
    assert pep562_walk_fixture.OWN == "own"


def test_unconfigure_leaves_a_supplier_someone_else_replaced_alone(
    clean_imports, tmp_path, monkeypatch
):
    """The identity guard `if ... is not supply: continue`, unpinned until now.

    If something else has since replaced this plugin's supplier on the module -
    another tool reconfiguring its dynamic-attribute hook mid-session -
    restoring whatever this plugin remembers would clobber state that is no
    longer this plugin's to manage.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
        pytest_unconfigure,
    )

    (tmp_path / "clobber_walk_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import clobber_walk_fixture

    monkeypatch.setenv(MODULES_VAR, "clobber_walk_fixture")
    pytest_configure(config=None)

    def someone_elses_supplier(name):
        if name == "REPLACED":
            return "replaced"
        raise AttributeError(name)

    clobber_walk_fixture.__getattr__ = someone_elses_supplier

    pytest_unconfigure(config=None)

    assert clobber_walk_fixture.__getattr__ is someone_elses_supplier
    assert clobber_walk_fixture.REPLACED == "replaced"


def test_clean_imports_teardown_undoes_the_mutation_it_records(tmp_path, monkeypatch):
    """The fixture restores the ledger; it must also undo what the ledger
    records, not only reset the record of it.

    Every OTHER configuring test claims a module written under `tmp_path`,
    which the fixture's own `sys.modules` diff deletes outright on teardown, so
    none of them exercises this path. This test needs a module that survives
    that diff on purpose: one imported BEFORE the fixture's setup snapshot is
    taken, so the diff sees it in both snapshots and never touches it. Driving
    the fixture's generator by hand, rather than requesting it as a pytest
    fixture, is what makes the assertion possible at all: the fixture's own
    teardown otherwise runs after the test function returns, where nothing can
    observe it.
    """
    from scripts.utils.canopus_nullstub import MODULES_VAR, VALUES_VAR, pytest_configure

    (tmp_path / "leak_walk_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import leak_walk_fixture  # imported before the fixture's own snapshot

    gen = clean_imports.__wrapped__()
    next(gen)  # run the fixture's setup half
    try:
        monkeypatch.setenv(MODULES_VAR, "leak_walk_fixture")
        pytest_configure(config=None)

        assert len(leak_walk_fixture.NOT_THERE_YET) == 7  # the mutation is live
    finally:
        with pytest.raises(StopIteration):
            next(gen)  # run the fixture's teardown half

    try:
        with pytest.raises(AttributeError):
            leak_walk_fixture.NOT_THERE_YET  # noqa: B018 - the access is the assertion
    finally:
        del sys.modules["leak_walk_fixture"]


def test_the_child_splits_the_claim_set_on_the_shared_separator(
    clean_imports, monkeypatch
):
    """The child's half of the wire format, read from the constant it publishes.

    The parent joins the claim set on `STUB_NAME_SEPARATOR` and the child splits
    on it, so the two are one rule. Written here as a literal `","` on the
    child's side and imported on the parent's, a change to either would be
    caught only by luck, and the failure is silent in the dangerous direction:
    the child claims nothing, both runs agree on a red the vacuity rule never
    fires over, and the suite stays green.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        STUB_NAME_SEPARATOR,
        _NamedFinder,
        pytest_configure,
    )

    monkeypatch.setenv(
        MODULES_VAR,
        STUB_NAME_SEPARATOR.join(["alfa_split_fixture", "bravo_split_fixture"]),
    )
    pytest_configure(config=None)
    installed = sys.meta_path[0]

    assert isinstance(installed, _NamedFinder)
    assert installed.find_spec("alfa_split_fixture") is not None
    assert installed.find_spec("bravo_split_fixture") is not None


def test_the_childs_diagnostic_carries_the_shared_marker(capsys):
    """The marker is the child's format too, and the parent greps for it.

    `run_pytest_report` forwards only the child's stderr lines that START with
    this marker, so a marker spelled differently on the two sides drops the one
    line that explains a swallowed exception, and a first-party module that blows
    up on import reaches the operator as a bare vacuity refusal.
    """
    from scripts.utils.canopus_nullstub import NULLSTUB_STDERR_MARKER, _report

    _report("resolving ghost_fixture raised RuntimeError(); stubbing it instead")

    assert capsys.readouterr().err.startswith(NULLSTUB_STDERR_MARKER)


# The test below crosses BOTH surfaces that stand a stub in for absent code in
# one run, which is the crossing whose absence let a measured escape through:
# every existing test drives either the finder or the already-imported supply
# loop, never a claim that needs both at once.


def test_an_already_imported_plain_module_can_still_carry_a_claimed_child(
    clean_imports, tmp_path, monkeypatch
):
    """The parent-`__path__` escape, on the surface that never got the fix.

    `_NamedFinder.find_spec` answers a claimed plain module with a PACKAGE stub
    so `from plain.child import thing` reaches `sys.meta_path` at all. A module
    ALREADY in `sys.modules` when the plugin arms never reaches the finder: it is
    supplied in place, keeps its real values, and still has no `__path__`, so
    Python reads the parent out of `sys.modules`, finds no `__path__`, and raises
    `ModuleNotFoundError: ... is not a package` before the finder is consulted
    for the child. Measured through the CLI before the fix, on a contract whose
    one test asserted nothing behind an already-imported plain parent: no
    refusal, no vacuity label, and the freeze would have proceeded, while the
    identical assertion behind an ABSENT parent was correctly refused.

    The three assertions are the three halves this crossing needs: the child
    resolves through the finder, the live parent keeps its OWN real value
    (never evicted, never reloaded), and the absent attribute is supplied.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
    )

    (tmp_path / "crossing_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import crossing_fixture

    monkeypatch.setenv(MODULES_VAR, "crossing_fixture.child")
    pytest_configure(config=None)

    from crossing_fixture.child import thing

    assert len(thing()) == 7
    assert crossing_fixture.EXISTS == 1
    assert len(crossing_fixture.NOT_THERE_YET) == 7


def test_unconfigure_takes_back_the_path_it_gave_a_live_plain_module(
    clean_imports, tmp_path, monkeypatch
):
    """A `__path__` written onto a live module outlives the probe unless undone.

    The supply loop mutates a module the process already holds, so teardown is an
    obligation rather than tidiness: a plain module left carrying `__path__` is a
    plain module the rest of the session treats as a package, and every later
    `from it.anything import x` fails somewhere else entirely. Removed by
    IDENTITY, like the attribute supplier beside it, so a `__path__` somebody
    else has since written is left alone rather than clobbered.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
        pytest_unconfigure,
    )

    (tmp_path / "pathback_fixture.py").write_text("EXISTS = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import pathback_fixture

    monkeypatch.setenv(MODULES_VAR, "pathback_fixture.child")
    pytest_configure(config=None)

    assert pathback_fixture.__path__ == []

    pytest_unconfigure(config=None)

    assert pathback_fixture.EXISTS == 1
    with pytest.raises(AttributeError):
        pathback_fixture.__path__  # noqa: B018 - the access is the assertion


def test_a_live_package_keeps_the_path_it_already_had(
    clean_imports, tmp_path, monkeypatch
):
    """The supply loop must give a `__path__` only to a module that lacks one.

    A claimed package that is already imported carries a real
    `__path__`, and overwriting it with an empty list would hide every module
    ALREADY WRITTEN below it behind a stub - the same fail-open `_stub_spec`
    refuses on the finder's side, arriving through the other door.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        VALUES_VAR,
        pytest_configure,
        pytest_unconfigure,
    )

    package = tmp_path / "livepkg_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("EXISTS = 1\n", encoding="utf-8")
    (package / "real.py").write_text("VALUE = 3\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(VALUES_VAR, "B")

    import livepkg_fixture

    original = list(livepkg_fixture.__path__)
    monkeypatch.setenv(MODULES_VAR, "livepkg_fixture.absent")
    pytest_configure(config=None)

    assert list(livepkg_fixture.__path__) == original

    from livepkg_fixture.real import VALUE

    assert VALUE == 3

    pytest_unconfigure(config=None)

    assert list(livepkg_fixture.__path__) == original
