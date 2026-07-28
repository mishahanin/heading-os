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


def test_a_resolvable_prefix_is_left_to_the_real_finder(clean_imports):
    """Minimal expansion, or the previous design's reach returns by another door.

    `scripts` and `scripts.utils` both resolve in this repository, so claiming
    them would wrap two real packages for no reason.
    """
    from scripts.utils.canopus_nullstub import _expand_claims

    assert _expand_claims({"scripts.utils.canopus_contract"}) == {
        "scripts.utils.canopus_contract"
    }


def test_the_plugin_installs_nothing_when_unconfigured(clean_imports, monkeypatch):
    """An accidental load outside a probe must change nothing.

    The plugin is passed with `-p` by the probe alone, but a plugin that arms
    itself on import would arm itself in any session that ever names it.
    """
    from scripts.utils.canopus_nullstub import MODULES_VAR, pytest_configure

    monkeypatch.delenv(MODULES_VAR, raising=False)
    before = list(sys.meta_path)
    pytest_configure(config=None)

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
    """Removal must be exactly the inverse of install.

    Both directions are wrong in a way the other cannot see: leaving the finder
    behind outlives the probe, and filtering the wrong way round takes the real
    importers out with it.
    """
    from scripts.utils.canopus_nullstub import (
        MODULES_VAR,
        _NamedFinder,
        pytest_configure,
        pytest_unconfigure,
    )

    monkeypatch.setenv(MODULES_VAR, "unconfigure_fixture")
    before = list(sys.meta_path)
    pytest_configure(config=None)

    assert any(isinstance(entry, _NamedFinder) for entry in sys.meta_path)

    pytest_unconfigure(config=None)

    assert sys.meta_path == before
