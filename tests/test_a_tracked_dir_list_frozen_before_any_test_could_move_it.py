"""A resolver called at import time freezes the operator's data path for the process.

`scripts/regenerate-docs-html.py` carried this at module scope::

    TRACKED_DIRS = [ROOT / "docs", ROOT / "templates"]
    try:
        _DATA_ROOT = get_data_root()
        if _DATA_ROOT != ROOT:
            TRACKED_DIRS += [_DATA_ROOT / "docs", _DATA_ROOT / "templates"]
    except Exception as exc:
        ...

`get_data_root()` reads `HEADING_OS_DATA` on every call, so it follows the
environment -- for a caller that asks after the environment changed. A module
that asks during its own import has already answered, and the answer is stored
in a list. A test that imports the module and THEN calls
`monkeypatch.setenv("HEADING_OS_DATA", tmp_path)` redirects nothing. This is the
recorded "a module default argument froze the real data path" shape with the
default argument replaced by a module-level constant; the mechanism is
identical, and the sibling file
`tests/test_defaults_that_froze_a_path_at_import.py` pins the default-argument
half. This file pins the import-time-call half.

MEASURED 2026-08-31, with the constant still on disk. After repointing
`HEADING_OS_DATA` at a scratch directory, `find_tracked_pairs()` still named six
markdown files under the operator's live overlay -- `docs/` and `templates/`
copies of CEO-ADMIN-GUIDE, EMERGENCY-PROCEDURES and GETTING-STARTED -- and
`regenerate()` writes the `.html` sibling of every path it is handed.

Also measured, and it is the reason this file does not read as an incident
report: all ten test files that reference the generator were run one at a time
under an audit-hook write probe (`sys.addaudithook`, which a monkeypatch cannot
remove) with the operator's overlay snapshotted by sha256 before and after each
run. Zero writes reached the overlay, zero `OverlayWriteRefused` refusals fired,
and the overlay was byte-identical afterwards. Four of the ten import the
generator at all (this file, plus the summary, credential-path and search-box
suites); of those, none drives `--all`, which is the only mode that calls
`find_tracked_pairs()`. The surface was open and unused. That is a reason to
close it, not a reason to relax about it: the distance between "unused" and
"used" is one `--all` in one new test.

The fix is `tracked_dirs()`, a function resolved at call time. Fail-soft is kept
deliberately -- a data root that cannot be resolved must not stop the engine's
own `docs/` from rendering -- and the handler logs to stderr rather than
swallowing, which `.claude/rules/security.md` forbids.

WHICH OF THE 43 COULD ACTUALLY BITE, measured 2026-08-31. A module-scope resolve
is harmless in a CLI, where the environment is set before launch. It is dangerous
only where a TEST can import the module and reach a write, so the 43 were split
by AST rather than by grep (a `_load(name, rel)` helper hides its target in a
string argument, and the argument ORDER differs from test file to test file).
Reachability was taken from the path expression handed to a loader call
(`spec_from_file_location`, `runpy.run_path`, `SourceFileLoader`), with
interprocedural substitution from each helper's own call sites, so a path that
only ever appears in a `subprocess` argv is excluded by construction rather than
by a blacklist. 40 of the 43 were reachable; 31 of the 40 could reach a write.

The severity split again on which PRIMITIVE the write used, and that decided the
order of work: the overlay write guard (`scripts/utils/overlay_write_guard.py`,
then part of `tests/conftest.py`) refuses `open`, `os.replace`, `os.rename`,
`os.remove` and `os.unlink` into the operator's overlay but did not, at the time,
wrap `os.mkdir`, `os.makedirs`, `Path.touch` or `os.rmdir`. A frozen root that
reached `write_text` failed LOUDLY; one that reached `mkdir` or `touch` landed a
stray directory in real private data in silence, invisible to `git status` as
well. 17 of the 31 reached an unwrapped primitive. Those eight went first.

ALL OF THEM ARE NOW OFF, on the same day, and the count below is zero. The rest
came off in eight parallel batches partitioned so no two batches shared a test
file. Three things are worth carrying forward, because none was in the plan:

  * THE SWEEP UNDER-REPORTED, and by a lot. It asks "is a resolver CALLED at
    module scope?" and is blind to `LOGO_PATH = BRAND_DIR / "logo.png"` and to
    `SIGNATURE = _resolve_asset(...)`, where the resolver sits one frame down
    inside the module's own function. 18 derived names across 6 of the 35
    modules were found by hand, one of them a WRITE target one CLI flag away
    from the operator's overlay (`knowledge-health.INDEX_FILE`). Then the rule
    written to catch that shape -- `frozen_module_names()` -- immediately named
    three modules that had never appeared on any list, including
    `scripts/send-email.py`, the workspace's only outbound mail path. The narrow
    rule is kept beside the wide one because it localises a hit to a line; the
    WIDE one is the gate.

  * A `__name__ == "__main__"` BODY COUNTS. It is skipped by a normal import,
    so it is not the freeze this file is named for, but `runpy.run_path(...,
    run_name="__main__")` is a loader this suite uses and it executes that block.
    Four files were fixed by moving the body into `main()`, which is the repo's
    own script standard anyway.

  * CONVERTING A CONSTANT CAN DROP A SIDE EFFECT. `bootcamp-roster.py` printed
    "reading the org chart from the shipped example" during its import; moving
    the resolution to call time meant main() refused before anything asked, and
    the notice vanished. A sibling test caught it. The lesson is that an
    import-time constant is sometimes doing two jobs, and only one of them is
    the path.

WHAT THE STRUCTURAL RULES DO NOT ESTABLISH, stated rather than left implied.
Absence of an import-time freeze does not prove a resolver FOLLOWS the
environment: a cache one layer down pins the value just as hard, which is the
shape `operator_identity._cached()` has. That is why the behavioural section
imports every module and calls every pure resolver before and after a repoint.
And that probe is narrow on purpose -- it calls only `def f(): return <expr>`
functions whose NAME is not an entry point, because the first version called
every zero-argument resolver. That version did two things, and this file recorded
only the first until 2026-08-31: it hung the run on `fireside-pulse._probe()`,
which reaches a subprocess, and it DESTROYED REAL DATA. At 09:46:37 +0400 that
day a scratch derivation of this selector (`.tmp/frozen/behaviour.py`, run as a
plain `.venv/bin/python` invocation -- no pytest, so no `tests/conftest.py`
overlay write guard was armed) selected `scripts/bootcamp-roster.py:main`, called
it blind against the operator's live `HEADING_OS_DATA`, and the run replaced an
18,857-byte private roster workbook with a 13,060-byte generated one. Restored
from git, byte-identical to HEAD. Both halves of the bound now have their own
failing case in `test_the_body_shape_bound_and_the_name_bound_are_independent`,
because a combined assertion stays green when either half is deleted.

WHY AST AND NOT GREP. A source-text search cannot tell a call at module scope
from the same call three lines lower inside a function, cannot see through
`from ... import get_data_root as gdr`, and matches the name inside a comment or
a docstring. All three cases are pinned below as tests. The dangerous resolver
set is not typed out either: it is derived by walking `scripts/utils/paths.py`
and `scripts/utils/workspace.py` and taking the transitive closure of everything
that reaches `get_data_root()` or `env_data_root()`, so a resolver added
tomorrow is in scope the same second. `get_workspace_root()` is deliberately
NOT in that closure -- the engine root is the repository, not the operator's
private data, and freezing it is not this defect.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SWEPT_ROOTS = (ROOT / "scripts", ROOT / ".claude" / "hooks")
RESOLVER_SOURCES = (ROOT / "scripts" / "utils" / "paths.py",
                    ROOT / "scripts" / "utils" / "workspace.py")

# The two functions that actually read HEADING_OS_DATA. Everything else in the
# dangerous set is derived from them.
SEEDS = frozenset({"get_data_root", "env_data_root"})

# A walk that silently matches nothing is green forever. 397 modules were under
# the two swept roots on 2026-08-31; the floor is set well below that so ordinary
# churn does not trip it, and well above zero so a broken glob does.
MIN_MODULES_SWEPT = 300


# ============================================================
# The rule
# ============================================================

def derived_resolvers(sources=RESOLVER_SOURCES) -> frozenset[str]:
    """Every function in the path modules that transitively reaches a seed.

    Derived, never typed. A hand-maintained list of dangerous names falls behind
    the code it is meant to describe; this closure cannot, because it is
    recomputed from the same files the callers import.
    """
    calls: dict[str, set[str]] = {}
    for src in sources:
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            named = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    called = _called_name(sub)
                    if called:
                        named.add(called)
            calls.setdefault(node.name, set()).update(named)

    reached = set(SEEDS)
    changed = True
    while changed:
        changed = False
        for func, named in calls.items():
            if func not in reached and (named & reached):
                reached.add(func)
                changed = True
    return frozenset(reached)


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _local_aliases(tree: ast.AST, resolvers: frozenset[str]) -> dict[str, str]:
    """`from scripts.utils.workspace import get_data_root as gdr` -> {gdr: get_data_root}."""
    alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for entry in node.names:
                if entry.name in resolvers:
                    alias[entry.asname or entry.name] = entry.name
    return alias


def import_time_resolver_calls(source: str, resolvers: frozenset[str],
                               label: str = "<source>") -> list[tuple[int, str, str]]:
    """Every call to a dangerous resolver that runs when `source` is imported.

    Four contexts count, and they are all evaluated by the `def`/`class`/module
    statement itself rather than by a later call:

      * module scope, including inside a module-level `try`, `if`, `for` or `with`
        (the incident shape was inside a `try`);
      * a class body, which executes at import;
      * a default argument expression;
      * a decorator expression.

    A call inside a function or lambda body does NOT count: it runs when the
    function runs, which is after any test has had its chance to set the
    environment. That distinction is the whole reason this is an AST rule.
    """
    tree = ast.parse(source, filename=label)
    alias = _local_aliases(tree, resolvers)
    found: list[tuple[int, str, str]] = []

    def canonical(node: ast.Call) -> str | None:
        name = _called_name(node)
        if name is None:
            return None
        name = alias.get(name, name)
        return name if name in resolvers else None

    def scan_expression(expr: ast.AST, context: str) -> None:
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call):
                name = canonical(sub)
                if name:
                    found.append((sub.lineno, name, context))

    def visit(node: ast.AST, at_import: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = list(child.args.defaults) + [d for d in child.args.kw_defaults if d]
                for default in defaults:
                    scan_expression(default, f"default argument of {child.name}()")
                for decorator in child.decorator_list:
                    scan_expression(decorator, f"decorator of {child.name}()")
                visit(child, False)
            elif isinstance(child, ast.ClassDef):
                for decorator in child.decorator_list:
                    scan_expression(decorator, f"decorator of class {child.name}")
                visit(child, at_import)
            elif isinstance(child, ast.Lambda):
                visit(child, False)
            else:
                if at_import and isinstance(child, ast.Call):
                    name = canonical(child)
                    if name:
                        found.append((child.lineno, name, "module scope"))
                visit(child, at_import)

    visit(tree, True)
    return sorted(set(found))


def _module_functions_reaching(tree: ast.AST, resolvers: frozenset[str],
                               alias: dict[str, str]) -> frozenset[str]:
    """This module's own functions that transitively reach a dangerous resolver."""
    local: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        named = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                called = _called_name(sub)
                if called:
                    named.add(alias.get(called, called))
        local[node.name] = named
    reached = {f for f, named in local.items() if named & resolvers}
    changed = True
    while changed:
        changed = False
        for func, named in local.items():
            if func not in reached and (named & reached):
                reached.add(func)
                changed = True
    return frozenset(reached)


def frozen_module_names(source: str, resolvers: frozenset[str],
                        label: str = "<source>") -> dict[str, int]:
    """Every module-level NAME whose value is frozen at import. name -> line.

    `import_time_resolver_calls()` above finds the CALL. This finds what the
    call was stored in, and then everything built from that. Three shapes, and
    the first is the only one the call sweep can see::

        BRAND_DIR = get_datastore_dir() / "brand"    # direct
        LOGO_PATH = BRAND_DIR / "logo.png"           # derived from a frozen name
        SIGNATURE  = _resolve_asset("sig.html")      # via this module's OWN function

    Converting only `BRAND_DIR` leaves `LOGO_PATH` frozen while every structural
    check prints clean, which is exactly how the 2026-08-31 campaign nearly
    shipped half a fix. Measured during it: 18 derived names across 6 of the 35
    modules, found by an agent checking by hand rather than by this rule, which
    did not exist yet. One was a WRITE target (`knowledge-health.INDEX_FILE`,
    written by `--update-index`), one flag away from writing to the operator's
    real overlay. The third shape then found three more modules nobody had
    listed at all, including `scripts/send-email.py` -- the only outbound mail
    path in the workspace, whose signature and brand-image paths resolved one
    frame down inside `_resolve_asset()`.

    This also subsumes the back-compat-alias check: `COUNCIL_DIR = council_dir()`
    re-freezes a converted module through its own new resolver, and the third
    shape catches it.
    """
    tree = ast.parse(source, filename=label)
    alias = _local_aliases(tree, resolvers)
    local_resolvers = _module_functions_reaching(tree, resolvers, alias)
    frozen: dict[str, int] = {}

    def reaches(expr: ast.AST) -> bool:
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call):
                called = _called_name(sub)
                if called and (alias.get(called, called) in resolvers
                               or called in local_resolvers):
                    return True
            if isinstance(sub, ast.Name) and sub.id in frozen:
                return True
        return False

    def scan(body) -> None:
        for node in body:
            # Module scope includes a module-level try/if/for/with body; the
            # incident shape was inside a `try`.
            if isinstance(node, (ast.Try, ast.If, ast.For, ast.While, ast.With)):
                scan(node.body)
                scan(getattr(node, "orelse", []) or [])
                scan(getattr(node, "finalbody", []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    scan(handler.body)
                continue
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            if not reaches(node.value):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        frozen.setdefault(sub.id, node.lineno)

    scan(tree.body)
    return frozen


# Names that mean "this function IS the program". Refused by name, on top of
# the body-shape bound below, because the shape bound is syntactic and the
# danger is semantic: `def main(): return _run_everything()` is a lone return
# with a zero-argument signature, so the shape bound passes it and
# `_snapshot()` then CALLS it -- with the operator's real `HEADING_OS_DATA`
# still live, because the `before` half of the repoint comparison runs before
# anything is monkeypatched.
#
# MEASURED 2026-08-31 09:46:37 +0400, from the session transcript and the
# `dcterms:created` stamp inside the wreckage. A scratch derivation of this
# selector (`.tmp/frozen/behaviour.py`, run as a plain `.venv/bin/python`
# invocation, so NO pytest and therefore no `tests/conftest.py` overlay write
# guard was armed) kept the zero-argument rule and dropped the lone-return
# rule. It selected `scripts/bootcamp-roster.py:main`, called it blind, and
# `main()` ran to completion: `write_excel()` reached `wb.save(out_xlsx())` and
# replaced a real 18,857-byte operator workbook with a
# 13,060-byte generated one. Restored from git; byte-identical to HEAD.
#
# The write guard is not the backstop it looks like here. It refuses WRITES,
# and it only exists inside a pytest session. An entry point called blind can
# also send mail, push a branch, or post to Telegram, and no wrapped primitive
# in that guard covers any of those. So the bound belongs on the CALLER: never
# blind-call something named like the program.
_ENTRY_POINT_NAMES = frozenset({"main", "run", "cli", "entry", "execute"})


def _safe_to_call_blind(node) -> bool:
    """True when this function may be invoked with no arguments and no consent.

    Two independent bounds, because each alone has been measured insufficient:

    * the NAME must not be an entry point (see `_ENTRY_POINT_NAMES` above);
    * the BODY must be a lone `return`, with a docstring allowed above it. The
      first version of this selector took every zero-argument function that
      reached a resolver and HUNG the run -- `scripts/fireside-pulse.py`'s
      `_probe()` qualifies and reaches a subprocess.

    Cost, stated rather than hidden: a legitimate `def run(): return
    get_outputs_dir()` loses its behavioural probe, as does a resolver with a
    try/except body or a cache (`bootcamp-roster._org_data()`). The structural
    rules above still cover all of those. Measured on 2026-08-31, the name
    bound removes nothing from the current population: no selected name in any
    of the 58 swept modules is entry-point-shaped, so this adds zero friction
    today and refuses the shape that destroyed real data.
    """
    if node.name.lower() in _ENTRY_POINT_NAMES:
        return False
    args = node.args
    required = len(args.posonlyargs) + len(args.args) - len(args.defaults)
    if required > 0 or args.kwonlyargs:
        return False
    body = [n for n in node.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    return len(body) == 1 and isinstance(body[0], ast.Return)


def call_time_resolvers(source: str, resolvers: frozenset[str],
                        label: str = "<source>") -> list[str]:
    """Module-level `def f(): return <expr>` functions that reach a resolver.

    The shape this campaign produced, and the only shape safe to call blind.
    What "safe" means, and the two measured incidents behind each half of it,
    live in `_safe_to_call_blind` -- every caller of this function invokes the
    names it returns, so that predicate is the whole safety argument.
    """
    tree = ast.parse(source, filename=label)
    alias = _local_aliases(tree, resolvers)
    reaching = _module_functions_reaching(tree, resolvers, alias)
    pure: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in reaching:
            continue
        if _safe_to_call_blind(node):
            pure.append(node.name)
    return pure


def swept_modules() -> list[Path]:
    files: list[Path] = []
    for root in SWEPT_ROOTS:
        files += sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def sweep_frozen_names() -> dict[str, dict[str, int]]:
    """relative path -> {frozen module-level name: line} over the swept roots."""
    resolvers = derived_resolvers()
    tally: dict[str, dict[str, int]] = {}
    for path in swept_modules():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            names = frozen_module_names(source, resolvers, str(path))
        except (OSError, SyntaxError):
            continue
        if names:
            tally[str(path.relative_to(ROOT))] = names
    return tally


def modules_with_call_time_resolvers() -> dict[str, list[str]]:
    """relative path -> the pure call-time resolvers it defines."""
    resolvers = derived_resolvers()
    found: dict[str, list[str]] = {}
    for path in swept_modules():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            names = call_time_resolvers(source, resolvers, str(path))
        except (OSError, SyntaxError):
            continue
        if names:
            found[str(path.relative_to(ROOT))] = names
    return found


def sweep() -> dict[tuple[str, str], list[tuple[int, str]]]:
    """(relative path, resolver) -> [(line, context), ...] over the swept roots."""
    resolvers = derived_resolvers()
    tally: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for path in swept_modules():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            hits = import_time_resolver_calls(source, resolvers, str(path))
        except SyntaxError:
            continue
        for lineno, name, context in hits:
            tally.setdefault((str(path.relative_to(ROOT)), name), []).append((lineno, context))
    return tally


# ============================================================
# The pinned set -- EMPTY, and that is the point
# ============================================================
#
# Every import-time resolver call still on disk. It is now NONE: the sweep finds
# zero call sites over the two swept roots.
#
# The tuple exists so a future exception can be recorded with a reason rather
# than by weakening the gate. It is a RATCHET, not an approval: a new entry fails
# `test_no_module_under_scripts_or_hooks_calls_a_new_resolver_at_import_time`
# until someone reads this and decides, in a commit, that the new one is
# acceptable. An entry whose count has DROPPED also fails, so the ratchet cannot
# be left slack.
#
# The arc, all of it on 2026-08-31: 65 keys over 43 files at the start (once
# `scripts/regenerate-docs-html.py` was fixed), then 57 keys over 35 files and 79
# call sites once eight came off, then zero. An empty tuple is not evidence that
# the sweep is working -- `MIN_MODULES_SWEPT` and the two negative-case tests
# below are what stop this file passing over a corpus it never read.
BASELINE: tuple[tuple[str, str, int], ...] = ()

# The derived-name half, same shape and same purpose. `frozen_module_names()`
# catches what the call sweep cannot: a name built from a frozen name, and a name
# built by calling one of this module's own resolvers. Also empty, also a place
# to record a deliberate exception rather than widen the rule.
FROZEN_NAME_BASELINE: tuple[tuple[str, str], ...] = ()


def baseline_map() -> dict[tuple[str, str], int]:
    return {(rel, name): count for rel, name, count in BASELINE}


# ============================================================
# 1. The generator itself: the value now follows the environment
# ============================================================

@pytest.fixture()
def regen():
    """A fresh import of the generator, isolated from sys.modules."""
    spec = importlib.util.spec_from_file_location(
        "regen_unfrozen", str(ROOT / "scripts" / "regenerate-docs-html.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _overlay(tmp_path: Path) -> Path:
    overlay = tmp_path / "overlay"
    (overlay / "docs").mkdir(parents=True)
    (overlay / "templates").mkdir()
    return overlay


def test_repointing_the_data_root_after_import_moves_the_tracked_dirs(regen, tmp_path, monkeypatch):
    """The regression in one line. The module is imported FIRST, exactly as a
    test module-scope `_load(...)` does, and the environment moves after."""
    before = [str(d) for d in regen.tracked_dirs()]
    overlay = _overlay(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    after = [str(d) for d in regen.tracked_dirs()]

    assert str(overlay / "docs") in after
    assert str(overlay / "templates") in after
    assert after != before, "tracked_dirs() ignored the environment it was given"


def test_no_overlay_path_from_before_the_repoint_survives(regen, tmp_path, monkeypatch):
    """Not just "the new root is there" -- the OLD one must be gone. A list that
    appended the scratch overlay beside the operator's would still hand
    `regenerate()` the operator's pages."""
    engine_dirs = {str(ROOT / "docs"), str(ROOT / "templates")}
    overlay = _overlay(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    resolved = {str(d) for d in regen.tracked_dirs()}

    assert resolved == engine_dirs | {str(overlay / "docs"), str(overlay / "templates")}


def test_find_tracked_pairs_follows_the_repoint_too(regen, tmp_path, monkeypatch):
    """The measured consequence. `find_tracked_pairs()` is what feeds
    `regenerate()`, and `regenerate()` writes."""
    overlay = _overlay(tmp_path)
    (overlay / "docs" / "GUIDE.md").write_text("# g\n", encoding="utf-8")
    (overlay / "docs" / "GUIDE.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    pairs = [str(p) for p in regen.find_tracked_pairs()]

    assert str(overlay / "docs" / "GUIDE.md") in pairs
    assert not [p for p in pairs if ".heading-os-data" in p], \
        "the operator's real overlay is still reachable after a repoint"


def test_the_engine_dirs_are_present_whatever_the_environment_says(regen, tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(_overlay(tmp_path)))
    resolved = [str(d) for d in regen.tracked_dirs()]
    assert resolved[:2] == [str(ROOT / "docs"), str(ROOT / "templates")]


def test_an_unresolvable_data_root_degrades_to_the_engine_dirs_and_says_so(
        regen, tmp_path, monkeypatch, capsys):
    """Fail-soft, preserved from the constant this replaced: a public clone with a
    broken override still renders its own docs. And it LOGS -- the handler must
    not swallow, per `.claude/rules/security.md`."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "not-a-real-directory"))

    resolved = [str(d) for d in regen.tracked_dirs()]

    assert resolved == [str(ROOT / "docs"), str(ROOT / "templates")]
    assert "data-overlay scan skipped" in capsys.readouterr().err


def test_the_generator_exposes_no_frozen_module_level_tracked_dirs(regen):
    """A back-compat alias `TRACKED_DIRS = tracked_dirs()` would reintroduce the
    exact defect while every test above still passed."""
    assert not hasattr(regen, "TRACKED_DIRS")
    assert not hasattr(regen, "_DATA_ROOT")
    assert callable(regen.tracked_dirs)


def test_the_generator_is_absent_from_the_baseline():
    assert not [key for key in baseline_map()
                if key[0] == "scripts/regenerate-docs-html.py"], \
        "the file this test exists for is back in the pinned set"


def test_the_sweep_finds_nothing_in_the_generator():
    source = (ROOT / "scripts" / "regenerate-docs-html.py").read_text(encoding="utf-8")
    assert import_time_resolver_calls(source, derived_resolvers()) == []


# ============================================================
# Every call-time resolver actually follows a repoint
# ============================================================
#
# Derived, never enumerated. Until 2026-08-31 this section named eight files and
# hand-mapped ONE resolver each; the map is gone, because a hand-written map of
# what to check is the same defect as a hand-written list of what is dangerous.
# The set below is recomputed from the tree, so a resolver written tomorrow is
# covered the same second.
#
# Computed once at import. It reads SOURCE files and never a data root, so it is
# not the freeze this file forbids -- but it IS a module-level computed value in
# the file that bans them, which is worth saying out loud rather than leaving for
# a reader to wonder about.
CALL_TIME_RESOLVERS = modules_with_call_time_resolvers()

# A derived set that silently collapses to nothing passes every test under it.
# 58 modules and 145 resolvers on 2026-08-31; the floors sit below that so
# ordinary churn does not trip them, and well above zero so a broken walk does.
MIN_RESOLVER_MODULES = 40
MIN_RESOLVERS = 100


def _original_data_root() -> str:
    from scripts.utils.workspace import get_data_root
    try:
        return str(get_data_root())
    except Exception as exc:                      # noqa: BLE001
        print(f"[pin] data root unresolvable: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return ""


def _snapshot(module, names: list[str]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for name in names:
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        try:
            out[name] = ("ok", str(fn()))
        except Exception as exc:                  # noqa: BLE001
            out[name] = ("raised", type(exc).__name__)
    return out


def frozen_after_repoint(before: dict, after: dict, old_root: str) -> list[str]:
    """The resolvers that did not follow. Separated so a test can drive it.

    An UNCHANGED value is not automatically a freeze, and calling it one would be
    this gate over-claiming. `github_org()` returns an org name and
    `_fireside_config_error()` returns None; neither should move when the data
    root moves. The signal that distinguishes them is whether the OLD data root
    is still sitting inside the value.
    """
    frozen = []
    for name, was in before.items():
        now = after.get(name)
        if now is None or was != now or was[0] != "ok":
            continue
        if old_root and old_root in was[1]:
            frozen.append(f"{name}() = {was[1]}")
    return frozen


def test_the_derived_resolver_population_is_not_empty():
    assert len(CALL_TIME_RESOLVERS) >= MIN_RESOLVER_MODULES, len(CALL_TIME_RESOLVERS)
    total = sum(len(v) for v in CALL_TIME_RESOLVERS.values())
    assert total >= MIN_RESOLVERS, total


@pytest.mark.parametrize("rel", sorted(CALL_TIME_RESOLVERS))
def test_a_call_time_resolver_follows_a_repoint_after_its_import(rel, tmp_path,
                                                                 monkeypatch):
    """The regression in one line, for every resolver in the tree.

    The module is imported FIRST, exactly as a test module-scope `_load(...)`
    does, and the environment moves after. Structural absence of an import-time
    call is necessary and NOT sufficient: a value can still be pinned one layer
    down by a cache, which is the shape `operator_identity._cached()` has and
    the reason this test is not redundant with the sweeps above.
    """
    old_root = _original_data_root()
    name = "repoint_probe_" + Path(rel).stem.replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)   # some modules use @dataclass
    spec.loader.exec_module(module)

    names = CALL_TIME_RESOLVERS[rel]
    before = _snapshot(module, names)
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    after = _snapshot(module, names)

    frozen = frozen_after_repoint(before, after, old_root)
    assert not frozen, (
        f"{rel}: these resolvers still answered with the OLD data root after a "
        f"repoint, so something below them cached the first answer:\n  "
        + "\n  ".join(frozen))


def test_an_entry_point_is_never_selected_for_a_blind_call(tmp_path):
    """The 2026-08-31 09:46:37 roster overwrite, in one assertion.

    `def main(): return _write_everything()` satisfies every BODY-shape bound
    this selector has ever had: zero required arguments, one lone `return`, a
    docstring above it. Only the name bound stops it. `_snapshot()` calls what
    this returns, and the `before` half of that comparison runs with the
    operator's real `HEADING_OS_DATA` still in place, so a selected `main()`
    executes against live private data.

    The pretend module is written under `tmp_path`. Nothing here imports it and
    nothing calls anything -- the assertion is over the AST alone, which is the
    only way to test this property without reproducing the incident.
    """
    resolvers = derived_resolvers()
    pretend = tmp_path / "overlay_writer.py"
    pretend.write_text(
        "from scripts.utils.workspace import get_datastore_dir\n"
        "\n"
        "\n"
        "def out_xlsx():\n"
        "    return get_datastore_dir() / 'roster.xlsx'\n"
        "\n"
        "\n"
        "def _write_everything():\n"
        "    path = out_xlsx()\n"
        "    path.write_bytes(b'wreckage')\n"
        "    return path\n"
        "\n"
        "\n"
        "def main():\n"
        '    """Zero args, one lone return. The shape bound cannot see this."""\n'
        "    return _write_everything()\n",
        encoding="utf-8",
    )
    names = call_time_resolvers(pretend.read_text(encoding="utf-8"), resolvers,
                                str(pretend))
    # The positive half, so the gate is not passing by selecting nothing at all.
    assert "out_xlsx" in names, (
        f"the resolver this gate exists for stopped being selected: {names}")
    assert "main" not in names, (
        "`main` was selected for a blind call. This is the exact selection that "
        "overwrote a real operator workbook on 2026-08-31 at "
        f"09:46:37 +0400. Selected: {names}")


def test_the_body_shape_bound_and_the_name_bound_are_independent():
    """Each bound alone lets a caller through, so both are asserted separately.

    Dropping either one has happened: the lone-return rule was the fix for the
    hung `_probe()` run, and its absence is what selected `bootcamp-roster.main`.
    A single combined assertion would stay green with one of them deleted.
    """
    def _fn(src):
        return ast.parse(src).body[0]

    # Refused on NAME alone: perfect body shape.
    assert not _safe_to_call_blind(_fn("def main():\n    return _run()\n"))
    assert not _safe_to_call_blind(_fn("def run():\n    return _go()\n"))
    assert not _safe_to_call_blind(_fn("def CLI():\n    return _go()\n"))
    # Refused on BODY alone: harmless name, multi-statement body that could do
    # anything at all (this is the `_probe()` shape that hung the run).
    assert not _safe_to_call_blind(
        _fn("def _probe():\n    x = 1\n    return x\n"))
    # Refused on SIGNATURE: a required argument means the caller must decide.
    assert not _safe_to_call_blind(_fn("def f(a):\n    return a\n"))
    assert not _safe_to_call_blind(_fn("def f(*, a):\n    return a\n"))
    # Allowed: the shape this whole campaign produces.
    assert _safe_to_call_blind(_fn("def out_dir():\n    return get_outputs_dir()\n"))
    assert _safe_to_call_blind(
        _fn('def out_dir():\n    """Doc."""\n    return get_outputs_dir()\n'))


def test_no_entry_point_reached_the_live_blind_call_population():
    """The bound above, asserted over the real tree rather than a fixture.

    `CALL_TIME_RESOLVERS` is what `_snapshot()` actually calls, module by
    module, against the operator's live data root. Nothing entry-point-shaped
    may be in it, and the population floors below cannot see this: widening the
    selector RAISES the count, so every floor in this file stays green while the
    set fills up with programs.
    """
    offenders = {
        rel: [n for n in names if n.lower() in _ENTRY_POINT_NAMES]
        for rel, names in CALL_TIME_RESOLVERS.items()
    }
    offenders = {rel: names for rel, names in offenders.items() if names}
    assert not offenders, (
        "these entry points are in the set that gets called blind against the "
        f"operator's live data root: {offenders}")


def test_the_repoint_check_fires_for_a_resolver_that_did_not_move():
    """The negative case for the check above. A comparison nobody has ever seen
    report a freeze is not a check."""
    old = "/home/op/.heading-os-data"
    stuck = {"out_dir": ("ok", f"{old}/outputs")}
    moved = {"out_dir": ("ok", "/scratch/overlay/outputs")}
    assert frozen_after_repoint(stuck, dict(stuck), old) == [f"out_dir() = {old}/outputs"]
    assert frozen_after_repoint(stuck, moved, old) == []


def test_an_unchanged_value_with_no_data_root_in_it_is_not_called_frozen():
    """The other direction, and it is the one that keeps the gate honest.
    `github_org()` returns the same string under any data root; reporting it
    would be the gate claiming more than its method establishes."""
    same = {"github_org": ("ok", "someorg"), "config_error": ("ok", "None")}
    assert frozen_after_repoint(same, dict(same), "/home/op/.heading-os-data") == []



# ============================================================
# 2. The rule itself -- positive, negative, and not-a-grep
# ============================================================

_INCIDENT = '''
from scripts.utils.workspace import get_data_root, get_workspace_root
ROOT = get_workspace_root()
TRACKED_DIRS = [ROOT / "docs"]
try:
    _DATA_ROOT = get_data_root()
    if _DATA_ROOT != ROOT:
        TRACKED_DIRS += [_DATA_ROOT / "docs"]
except Exception as exc:
    print(exc)
'''

_FIXED = '''
from scripts.utils.workspace import get_data_root, get_workspace_root
ROOT = get_workspace_root()

def tracked_dirs():
    dirs = [ROOT / "docs"]
    try:
        data_root = get_data_root()
    except Exception as exc:
        print(exc)
        return dirs
    if data_root != ROOT:
        dirs += [data_root / "docs"]
    return dirs
'''


def test_the_rule_flags_the_shape_that_caused_the_incident():
    hits = import_time_resolver_calls(_INCIDENT, derived_resolvers())
    assert [(name, context) for _, name, context in hits] == [("get_data_root", "module scope")]


def test_the_rule_accepts_the_shape_that_replaced_it():
    assert import_time_resolver_calls(_FIXED, derived_resolvers()) == []


def test_the_rule_flags_a_default_argument():
    source = ("from scripts.utils.workspace import get_outputs_dir\n"
              "def save(target=get_outputs_dir()):\n    return target\n")
    hits = import_time_resolver_calls(source, derived_resolvers())
    assert [(n, c) for _, n, c in hits] == [("get_outputs_dir", "default argument of save()")]


def test_the_rule_flags_a_class_body():
    source = ("from scripts.utils.workspace import get_outputs_dir\n"
              "class Writer:\n    OUT = get_outputs_dir()\n")
    hits = import_time_resolver_calls(source, derived_resolvers())
    assert [(n, c) for _, n, c in hits] == [("get_outputs_dir", "module scope")]


def test_the_rule_flags_a_decorator_expression():
    source = ("from scripts.utils.workspace import get_outputs_dir\n"
              "def deco(p):\n    return lambda f: f\n"
              "@deco(get_outputs_dir())\n"
              "def go():\n    pass\n")
    hits = import_time_resolver_calls(source, derived_resolvers())
    assert [(n, c) for _, n, c in hits] == [("get_outputs_dir", "decorator of go()")]


def test_the_rule_sees_through_an_import_alias():
    """A grep for `get_data_root(` finds nothing here. The call is real."""
    source = ("from scripts.utils.workspace import get_data_root as gdr\n"
              "BASE = gdr()\n")
    hits = import_time_resolver_calls(source, derived_resolvers())
    assert [(n, c) for _, n, c in hits] == [("get_data_root", "module scope")]


def test_the_rule_does_not_flag_a_call_inside_a_function_body():
    """The distinction a grep cannot draw, and the reason the fix is a fix."""
    source = ("from scripts.utils.workspace import get_data_root\n"
              "def later():\n    return get_data_root() / 'docs'\n")
    assert import_time_resolver_calls(source, derived_resolvers()) == []


def test_the_rule_does_not_flag_a_call_inside_a_lambda_or_a_comprehension_body():
    source = ("from scripts.utils.workspace import get_data_root\n"
              "f = lambda: get_data_root()\n")
    assert import_time_resolver_calls(source, derived_resolvers()) == []


def test_the_rule_does_not_flag_a_mention_in_a_comment_or_a_docstring():
    """The three false positives a source-text guard would report here."""
    source = ('"""Calls get_data_root() at import, allegedly."""\n'
              "# get_data_root()\n"
              "NOTE = 'get_data_root()'\n")
    assert import_time_resolver_calls(source, derived_resolvers()) == []


def test_the_rule_does_not_flag_the_workspace_root_resolver():
    source = ("from scripts.utils.workspace import get_workspace_root\n"
              "ROOT = get_workspace_root()\n")
    assert import_time_resolver_calls(source, derived_resolvers()) == []


# ============================================================
# 3. The derived resolver set
# ============================================================

def test_the_resolver_set_is_derived_and_contains_the_seeds():
    resolvers = derived_resolvers()
    assert resolvers >= SEEDS


def test_the_derivation_reaches_resolvers_two_hops_from_a_seed():
    """`get_outputs_dir` -> `get_personal_root` -> `get_data_root`. A one-hop
    derivation, or a typed list, would miss it."""
    resolvers = derived_resolvers()
    for name in ("get_personal_root", "get_outputs_dir", "get_datastore_dir",
                 "get_crm_contacts_dir", "get_auto_memory_dir"):
        assert name in resolvers, name


def test_the_derivation_excludes_the_engine_root_resolver():
    """`get_workspace_root()` resolves the repository, not the operator's data.
    Including it would put a hundred harmless `ROOT = get_workspace_root()`
    lines into the baseline and drown the finding this file is about."""
    assert "get_workspace_root" not in derived_resolvers()
    assert "home" not in derived_resolvers()


def test_the_derivation_would_pick_up_a_resolver_added_tomorrow(tmp_path):
    """The negative case for the derivation: feed it a module defining a NEW
    helper that calls a seed, and the helper must appear without anyone editing
    a list. This is what makes the set self-repairing."""
    fake = tmp_path / "extra_paths.py"
    fake.write_text("from x import get_data_root\n"
                    "def get_brand_new_dir():\n    return get_data_root() / 'brand'\n",
                    encoding="utf-8")
    resolvers = derived_resolvers(sources=(*RESOLVER_SOURCES, fake))
    assert "get_brand_new_dir" in resolvers
    assert "get_brand_new_dir" not in derived_resolvers()


# ============================================================
# 4. The sweep, its floor, and the ratchet
# ============================================================

def test_the_sweep_reaches_a_real_and_non_trivial_set_of_modules():
    """A walk that matches nothing is green forever."""
    modules = swept_modules()
    assert len(modules) >= MIN_MODULES_SWEPT, (
        f"only {len(modules)} modules walked under {[str(r) for r in SWEPT_ROOTS]}; "
        "the sweep is not reaching the tree")


def test_both_swept_roots_contribute_modules():
    """One root existing is not both roots existing. `.claude/hooks/` is the
    smaller of the two and the easier one to lose to a moved directory."""
    for root in SWEPT_ROOTS:
        assert root.is_dir(), root
        assert any(p.suffix == ".py" for p in root.rglob("*.py")), root


def test_the_sweep_reads_a_real_corpus_even_though_it_now_finds_nothing():
    """The floor that replaced "the baseline is non-empty".

    Until 2026-08-31 this test asserted `BASELINE` and `sweep()` were both
    non-empty, which was a fine proof of life while 79 call sites were still on
    disk and became a LIE the moment the last one came off. An empty result now
    means success, so the proof of life has to come from somewhere else: the
    walk must have read a real corpus, and the rule must still fire on the
    incident shape (pinned separately, below). Those two together are what stop
    this file passing over a directory it never opened.
    """
    modules = swept_modules()
    assert len(modules) >= MIN_MODULES_SWEPT, (
        f"the walk found {len(modules)} modules; the floor is {MIN_MODULES_SWEPT}. "
        "A sweep over nothing is green forever.")
    assert import_time_resolver_calls(_INCIDENT, derived_resolvers()), (
        "the rule no longer fires on the incident shape it was written for")


def test_no_module_under_scripts_or_hooks_freezes_a_name_at_import():
    """The stricter half of the ratchet, and the one that found the stragglers.

    The call sweep below asks "is a resolver CALLED at module scope?".  This asks
    "is any module-level NAME frozen?", which is the question that actually
    matters and covers two shapes the call sweep cannot see. On the day it was
    written it immediately named three modules nobody had listed, one of them
    `scripts/send-email.py`.
    """
    pinned = {(rel, name) for rel, name in FROZEN_NAME_BASELINE}
    novel = []
    for rel, names in sorted(sweep_frozen_names().items()):
        for name, lineno in sorted(names.items(), key=lambda kv: kv[1]):
            if (rel, name) not in pinned:
                novel.append(f"{rel}:{lineno} {name}")
    assert not novel, (
        "these module-level names are frozen when the module is imported, so a "
        "caller that repoints HEADING_OS_DATA afterwards still gets the first "
        "answer. Resolve at CALL time (a function), or add (path, name) to "
        "FROZEN_NAME_BASELINE with a reason:\n  " + "\n  ".join(novel))


def test_the_frozen_name_baseline_carries_no_entry_that_has_already_been_fixed():
    """The other direction, so this ratchet cannot be left slack either."""
    found = {(rel, name) for rel, names in sweep_frozen_names().items() for name in names}
    stale = [f"{rel}:{name}" for rel, name in FROZEN_NAME_BASELINE
             if (rel, name) not in found]
    assert not stale, ("the frozen-name baseline is behind the tree; drop:\n  "
                       + "\n  ".join(stale))


_DERIVED_INCIDENT = '''
from scripts.utils.workspace import get_datastore_dir, get_data_root

def _resolve_asset(rel):
    return get_data_root() / rel

BRAND_DIR = get_datastore_dir() / "brand"
LOGO_PATH = BRAND_DIR / "logo.png"
SIGNATURE = _resolve_asset("reference/signature.html")
SAFE = "datastore/brand"
'''


def test_the_frozen_name_rule_sees_all_three_shapes():
    """The positive case, and it is the whole reason this rule exists.

    `BRAND_DIR` is what the call sweep already found. `LOGO_PATH` names no
    resolver at all. `SIGNATURE` reaches one a frame down inside a function of
    the same module. `SAFE` is a plain string and must NOT be reported -- a rule
    that flags everything is a rule nobody keeps.
    """
    names = frozen_module_names(_DERIVED_INCIDENT, derived_resolvers(), "synthetic")
    assert sorted(names) == ["BRAND_DIR", "LOGO_PATH", "SIGNATURE"]


def test_the_call_sweep_alone_is_blind_to_two_of_those_three():
    """States the gap in measurable form, so nobody deletes the derived rule as
    a duplicate of the call sweep."""
    hits = import_time_resolver_calls(_DERIVED_INCIDENT, derived_resolvers(), "synthetic")
    seen = {name for _, name, _ in hits}
    assert seen == {"get_datastore_dir"}, seen


def test_the_frozen_name_rule_ignores_a_call_inside_a_function_body():
    """Same distinction the call sweep draws, and for the same reason: a name
    bound INSIDE a function is bound when the function runs."""
    source = ('from scripts.utils.workspace import get_data_root\n'
              'def f():\n'
              '    LATER = get_data_root() / "x"\n'
              '    return LATER\n')
    assert frozen_module_names(source, derived_resolvers(), "synthetic") == {}


def test_the_frozen_name_rule_catches_the_back_compat_alias():
    """`COUNCIL_DIR = council_dir()` restores the exact defect in a module that
    was already converted, and every other structural check still passes."""
    source = ('from scripts.utils.workspace import get_outputs_dir\n'
              'def council_dir():\n'
              '    return get_outputs_dir() / "council"\n'
              'COUNCIL_DIR = council_dir()\n')
    assert sorted(frozen_module_names(source, derived_resolvers(), "synthetic")) \
        == ["COUNCIL_DIR"]


def test_no_module_under_scripts_or_hooks_calls_a_new_resolver_at_import_time():
    """The ratchet. A NEW import-time resolver call fails here until someone
    adds it to BASELINE on purpose."""
    pinned = baseline_map()
    novel = []
    for key, locations in sorted(sweep().items()):
        rel, name = key
        allowed = pinned.get(key, 0)
        if len(locations) > allowed:
            lines = ", ".join(f"{rel}:{line} ({context})" for line, context in locations[allowed:])
            novel.append(f"{name}() at {lines}")
    assert not novel, (
        "these resolvers are called when the module is imported, so the value is "
        "frozen before any caller can set HEADING_OS_DATA. Resolve at CALL time "
        "(a function), or add the site to BASELINE with a reason:\n  "
        + "\n  ".join(novel))


def test_the_pinned_set_carries_no_entry_that_has_already_been_fixed():
    """The other direction, so the ratchet cannot be left slack: an entry whose
    file is gone, or whose count has dropped, must come off the list."""
    found = {key: len(locations) for key, locations in sweep().items()}
    stale = []
    for rel, name, count in BASELINE:
        if not (ROOT / rel).exists():
            stale.append(f"{rel} no longer exists; drop its {name}() entry")
        elif found.get((rel, name), 0) < count:
            stale.append(f"{rel} now has {found.get((rel, name), 0)} {name}() call(s), "
                         f"not {count}; lower the pinned count")
    assert not stale, "the pinned set is behind the tree:\n  " + "\n  ".join(stale)


def test_the_ratchet_fails_when_a_new_site_appears(tmp_path, monkeypatch):
    """The negative case for the gate itself: with a synthetic module carrying the
    incident shape placed under a swept root, the comparison must report it. A
    gate nobody has ever seen refuse is not a gate."""
    resolvers = derived_resolvers()
    hits = import_time_resolver_calls(_INCIDENT, resolvers, "synthetic")
    pinned: dict[tuple[str, str], int] = {}

    novel = [name for _, name, _ in hits if pinned.get(("synthetic.py", name), 0) < 1]

    assert novel == ["get_data_root"], "the comparison the ratchet runs does not fire"


def test_the_staleness_check_fires_for_a_count_that_dropped():
    """And the negative case for the staleness half."""
    found = {("scripts/x.py", "get_data_root"): 1}
    pinned = (("scripts/x.py", "get_data_root", 3),)
    behind = [f"{rel}:{name}" for rel, name, count in pinned
              if found.get((rel, name), 0) < count]
    assert behind == ["scripts/x.py:get_data_root"]


def test_this_process_never_pointed_the_data_root_at_the_operators_overlay():
    """A guard against this very file being the thing that writes. Every test
    above that touches a data root sets HEADING_OS_DATA to a tmp_path first;
    nothing here should leave the real overlay selected at teardown."""
    env = os.environ.get("HEADING_OS_DATA")
    if env is None:
        pytest.skip("no HEADING_OS_DATA in this environment")
    assert Path(env).exists(), env


def test_the_module_under_test_imports_without_touching_sys_modules_globally():
    """`_load`-style imports are how the ten sibling files reach this generator.
    If the fresh-import fixture leaked into sys.modules, a later test in the same
    session would silently share the first import's frozen state -- which is the
    class of bug this whole file is about."""
    assert "regen_unfrozen" not in sys.modules
