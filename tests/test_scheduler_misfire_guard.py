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
walks scripts/ only. A scheduler built correctly and then mutated at runtime, one
whose defaults are assembled dynamically, or a daemon that lands outside
scripts/, passes here while behaving differently. No such site exists today.
"""
import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent

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


def scheduler_constructions(src: str, filename: str = "<synthetic>"):
    """(lineno, objection) for every scheduler construction in `src`."""
    for node in ast.walk(ast.parse(src, filename=filename)):
        if isinstance(node, ast.Call) and _called_name(node) in SCHEDULER_NAMES:
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
    for path in sorted((ENGINE / "scripts").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
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
    asserts the walk really finds the schedulers it is supposed to hold."""
    found = 0
    for path in sorted((ENGINE / "scripts").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if not any(name in src for name in SCHEDULER_NAMES):
            continue
        found += sum(1 for _ in scheduler_constructions(src, path.name))

    assert found >= 4, f"expected at least 4 scheduler constructions, saw {found}"
