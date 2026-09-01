"""No APScheduler scheduler under scripts/ may be built without job_defaults.

The ratchet for the misfire defect of 2026-07-30. The four edits that shipped
with this file fixed the schedulers that existed; this guard is what makes the
property hold for the daemon nobody has written yet. It exists because the
correct value DID already exist in scripts/bridge_daemon/scheduler.py and did not
travel to the five jobs scripts/bridge-daemon.py adds to that same scheduler, so
the defect was a comment that failed to propagate rather than a value nobody
knew.

Source is parsed, never imported: importing a daemon module runs its top-level
side effects, and the invariant is a property of the source text anyway.

Reach, stated so a future reader is not misled. This checks CONSTRUCTION, and it
walks scripts/ only. These shapes pass here while behaving differently, and none
of them exists in the tree today:

  - a scheduler built correctly and then mutated at runtime;
  - the keyword's value is a CALL (``job_defaults=build()``, ``dict()``), which
    syntax cannot settle;
  - the keyword's value is a NAME this guard does not recognise
    (``job_defaults=SAFE``), for the same reason;
  - the class bound to a local variable first (``Cls = BackgroundScheduler`` then
    ``Cls()``), which needs dataflow rather than syntax to resolve;
  - a daemon that lands outside ``scripts/``.

Each of the middle three is pinned by a test below, so a future author who
closes one has to change an assertion deliberately rather than discover the gap
in production.

This list said "a call OR A SPREAD" until 2026-09-01, and the spread half was
false in both of its spellings. MEASURED: ``job_defaults={**base}`` is REFUSED,
because a ``**`` entry gives `ast.Dict` a None key that contributes no literal
key, so the required one is absent; and ``BackgroundScheduler(**opts)`` is
REFUSED, because a ``**`` argument has ``kw.arg is None`` and the keyword is
therefore simply missing. Both fail closed, which is the safe direction, but a
section written so a reader is not misled may not describe the guard as blinder
than it is. Both are pinned below too.

An ALIASED import (``import BackgroundScheduler as BS`` then ``BS()``) used to
evade this guard and no longer does: aliases are resolved per file from the
apscheduler import statements. That hole was found by attacking the guard after
it was written, which is the only way this kind of hole is ever found.
"""
import ast
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ENGINE))
from tests.repo_files import read_sources  # noqa: E402

# Every scheduler class APScheduler 3.x ships, not only the two in use, so a
# future daemon reaching for a different one is covered on the day it lands.
SCHEDULER_NAMES = frozenset({
    "AsyncIOScheduler", "BackgroundScheduler", "BlockingScheduler",
    "GeventScheduler", "QtScheduler", "TornadoScheduler", "TwistedScheduler",
})

REQUIRED_KEY = "misfire_grace_time"
CONSTANT_NAME = "JOB_DEFAULTS"


def _called_name(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", None)


def scheduler_objection(node: ast.Call) -> str | None:
    """Why this scheduler construction is unsafe, or None if it is fine."""
    keywords = {kw.arg: kw.value for kw in node.keywords}

    if "job_defaults" not in keywords:
        return (f"built without job_defaults, so every job on it inherits "
                f"APScheduler's 1 second {REQUIRED_KEY} and is discarded when a "
                f"tick slips. Pass job_defaults={CONSTANT_NAME} from "
                f"scripts/utils/scheduler_defaults.py")

    value = keywords["job_defaults"]

    if isinstance(value, ast.Name) and value.id == CONSTANT_NAME:
        return None

    if isinstance(value, ast.Dict):
        literal_keys = {k.value for k in value.keys if isinstance(k, ast.Constant)}
        if REQUIRED_KEY not in literal_keys:
            return (f"job_defaults carries no {REQUIRED_KEY}, so the one option "
                    f"this guard exists for is still at its 1 second default")
        return None

    # A spread, a call, or a name we do not recognise is beyond what source
    # inspection can settle. Named in the module docstring as a known limit.
    return None


def _watched_names(tree: ast.AST) -> frozenset[str]:
    """Scheduler class names as THIS file refers to them, aliases included.

    `from apscheduler.schedulers.background import BackgroundScheduler as BS`
    makes `BS()` a scheduler construction, and matching the canonical name alone
    would let it through. Measured: it did.
    """
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("apscheduler"):
            for alias in node.names:
                if alias.name in SCHEDULER_NAMES:
                    aliases.add(alias.asname or alias.name)
    return SCHEDULER_NAMES | aliases


def scheduler_constructions(src: str, filename: str = "<synthetic>"):
    """(lineno, objection) for every scheduler construction in `src`."""
    tree = ast.parse(src, filename=filename)
    watched = _watched_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) in watched:
            yield node.lineno, scheduler_objection(node)


def _objections(src: str) -> list[str]:
    return [o for _line, o in scheduler_constructions(src) if o]


# ============================================================
# The guard catches what it claims to catch
# ============================================================

def test_a_bare_construction_is_refused():
    objections = _objections("s = AsyncIOScheduler(timezone=tz)\n")
    assert len(objections) == 1
    assert "without job_defaults" in objections[0]
    assert "scheduler_defaults" in objections[0]


def test_the_constant_by_name_is_accepted():
    assert _objections(
        "s = AsyncIOScheduler(timezone=tz, job_defaults=JOB_DEFAULTS)\n") == []


def test_a_dict_literal_missing_the_key_is_refused():
    """The near-miss a future author is most likely to produce: job_defaults
    passed, carrying only the options they happened to be thinking about."""
    objections = _objections(
        "s = BackgroundScheduler(job_defaults={'coalesce': True})\n")
    assert len(objections) == 1
    assert f"no {REQUIRED_KEY}" in objections[0]


def test_a_dict_literal_carrying_the_key_is_accepted():
    assert _objections(
        "s = BackgroundScheduler(job_defaults={'misfire_grace_time': None})\n") == []


def test_every_scheduler_class_is_watched_not_only_the_two_in_use():
    for name in SCHEDULER_NAMES:
        assert _objections(f"s = {name}()\n"), f"{name} is not guarded"


def test_a_module_qualified_construction_is_watched():
    assert _objections("s = apscheduler.BackgroundScheduler()\n")


def test_an_aliased_import_is_watched_under_its_alias():
    """Found by attacking the guard after writing it: this evaded the first
    version, which matched the canonical class name only."""
    src = ("from apscheduler.schedulers.background import BackgroundScheduler as BS\n"
           "s = BS()\n")
    objections = _objections(src)
    assert len(objections) == 1
    assert "without job_defaults" in objections[0]


def test_an_aliased_import_carrying_the_constant_is_accepted():
    src = ("from apscheduler.schedulers.asyncio import AsyncIOScheduler as Sched\n"
           "s = Sched(job_defaults=JOB_DEFAULTS)\n")
    assert _objections(src) == []


def test_an_alias_of_something_else_is_not_watched():
    """The alias resolution must not turn any renamed import into a scheduler."""
    src = ("from mylib import Thing as BackgroundSchedulerish\n"
           "s = BackgroundSchedulerish()\n")
    assert _objections(src) == []


def test_a_class_held_in_a_variable_is_a_known_blind_spot():
    """Pinned rather than pretended away. Resolving this needs dataflow, not
    syntax. The module docstring names it; this test makes the limit measurable,
    so a future author who closes it has to change this assertion deliberately
    instead of discovering the gap in production.
    """
    assert _objections("Cls = BackgroundScheduler\ns = Cls()\n") == []


@pytest.mark.parametrize("value", ["build()", "dict()", "make_defaults(base)"])
def test_defaults_produced_by_a_call_are_a_known_blind_spot(value):
    """The second stated limit, made measurable.

    It was stated and unpinned until 2026-09-01: a mutation replacing the
    constant with ``job_defaults=dict()`` survived the whole suite, which is
    correct behaviour and exactly why it needs an assertion beside it. Syntax
    cannot say what a call returns, so the guard declines rather than guesses.
    """
    assert _objections(f"s = BackgroundScheduler(job_defaults={value})\n") == []


def test_an_unrecognised_name_is_a_known_blind_spot():
    """The third. `scheduler_objection` accepts any Name that is not
    CONSTANT_NAME without inspecting it, so the rule "pass the constant by name
    and nothing else" is a convention this guard cannot enforce."""
    assert _objections("s = BackgroundScheduler(job_defaults=SAFE_DEFAULTS)\n") == []


def test_a_spread_inside_job_defaults_is_refused_not_waved_through():
    """The docstring claimed this passed. It does not.

    A ``**`` entry gives `ast.Dict` a key of None, which contributes no literal
    key, so `misfire_grace_time` reads as absent and the construction is
    refused. Failing closed is the right direction; describing it as a blind
    spot sent the next reader looking for a hole that is not there.
    """
    objections = _objections("s = BackgroundScheduler(job_defaults={**base})\n")
    assert len(objections) == 1
    assert f"no {REQUIRED_KEY}" in objections[0]


def test_a_spread_carrying_the_key_explicitly_is_still_accepted():
    """Positive twin: the refusal above is about the absent key, not about the
    spread, so a spread that also names the key is fine."""
    assert _objections(
        "s = BackgroundScheduler(job_defaults={**base, 'misfire_grace_time': None})\n"
    ) == []


def test_kwargs_spread_on_the_constructor_is_refused_too():
    """`BackgroundScheduler(**opts)` has `kw.arg is None`, so `job_defaults` is
    simply missing and the first branch fires. The other spelling of the same
    corrected claim."""
    objections = _objections("s = BackgroundScheduler(**opts)\n")
    assert len(objections) == 1
    assert "without job_defaults" in objections[0]


def test_an_unrelated_call_is_not_flagged():
    assert _objections("s = SomeOtherThing(job_defaults=None)\n") == []


def test_the_objection_names_the_line():
    src = "x = 1\ny = 2\ns = AsyncIOScheduler()\n"
    lines = [line for line, objection in scheduler_constructions(src) if objection]
    assert lines == [3]


# ============================================================
# The assertion that actually holds the invariant
# ============================================================

def _tree_failures() -> list[str]:
    failures = []
    # Read through `read_sources`: the rglob lists the modules and this loop
    # reads them, and a module can be written and removed inside that window in
    # a checkout several agents share. A module that is gone builds no
    # scheduler, so skipping is the right answer for this scan and the helper
    # warns rather than dropping it in silence.
    for path, src in read_sources(sorted((ENGINE / "scripts").rglob("*.py"))):
        if not any(name in src for name in SCHEDULER_NAMES):
            continue
        rel = path.relative_to(ENGINE)
        for line, objection in scheduler_constructions(src, str(rel)):
            if objection:
                failures.append(f"{rel}:{line}: {objection}")
    return failures


def test_no_scheduler_in_the_tree_is_built_without_job_defaults():
    failures = _tree_failures()
    assert not failures, (
        "APScheduler scheduler(s) built without safe job defaults:\n  "
        + "\n  ".join(failures))


def test_the_tree_walk_actually_reaches_the_daemons():
    """A tree guard that silently matched nothing would pass forever. This
    asserts the walk really finds the schedulers it is supposed to hold.

    The floor is 3, lowered from 4 on 2026-08-03 when the eval-drift daemon and
    its scheduler were retired. It is a floor and not an equality on purpose: it
    exists to catch the walk matching NOTHING, and a number that has to be
    updated every time a daemon lands would be raised without thought.
    """
    found = 0
    # Same walk-then-read race. This is a FLOOR, so a module the race skipped can
    # only lower the count and make the assertion fail loudly - it can never turn
    # a missing scheduler into a pass - and the skip count is reported with it.
    vanished: list[Path] = []
    for path, src in read_sources(sorted((ENGINE / "scripts").rglob("*.py")),
                                  vanished):
        if not any(name in src for name in SCHEDULER_NAMES):
            continue
        found += sum(1 for _ in scheduler_constructions(src, path.name))

    assert found >= 3, (
        f"expected at least 3 scheduler constructions, saw {found} "
        f"({len(vanished)} file(s) vanished mid-walk)")
