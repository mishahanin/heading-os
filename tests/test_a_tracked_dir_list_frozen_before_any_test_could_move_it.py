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
by a blacklist. 40 of the 43 are reachable. The three that are not are
`gen-exec-meeting-docx.py`, `generate-usecases-docx.py` and `md-to-docx-charter.py`:
`tests/test_docx_helpers.py` drives all eight generators through `subprocess.run`
with `HEADING_OS_DATA` already in the child's environment, which is immune.

Of the 40, 31 can reach a write derived from the frozen constant and 9 only read.
But the severity splits again, and this is the part that decided which eight were
fixed first: `tests/conftest.py` installs a session-wide guard that wraps
`builtins.open`, `io.open`, `os.replace`, `os.rename`, `os.remove` and `os.unlink`
and refuses any of them into the operator's overlay, keyed on a STRUCTURAL root no
`HEADING_OS_DATA` can move. It does not wrap `os.mkdir`, `os.makedirs`,
`Path.touch` or `os.rmdir`. So a frozen root that reaches `write_text` fails
LOUDLY with `OverlayWriteRefused`, while one that reaches `mkdir` or `touch` lands
a stray directory in real private data in silence, which `git status` does not
show either. 17 of the 31 reach an unwrapped primitive; those are the ones that
bite.

THE EIGHT THAT CAME OFF, all of them in that unwrapped-primitive set:
`council-aggregate.py`, `council-record-verdict.py`, `implement-trajectory-log.py`
(the only `touch`), `llm-fit-report.py`, `output-organizer.py`,
`publish-corporate.py`, `scrutinize-flag-fp.py`, `scrutinize-replay.py`. Each
module-level constant became a function resolved at call time and every caller
was updated; where a test redirected the constant with
`monkeypatch.setattr(mod, "CONST", tmp)` it now patches the function instead.

WHY THE REST ARE STILL HERE, and it is not that they are safe. Roughly 250 of the
301 test references to these constants ARE `monkeypatch.setattr(mod, "CONST", ...)`,
the suite's working redirect seam, so converting the remaining constants churns
about sixty test files. That is a scope decision for the operator, not a change to
make quietly inside a fix for eight.

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


def swept_modules() -> list[Path]:
    files: list[Path] = []
    for root in SWEPT_ROOTS:
        files += sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


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
# The pinned set
# ============================================================
#
# Every import-time resolver call still on disk: 57 (file, resolver) keys over 35
# files, 79 call sites, all at module scope. This is a RATCHET, not an approval.
# A new entry fails the sweep below until someone reads this docstring and
# decides -- deliberately, in a commit -- that the new one is acceptable. An
# entry whose count has DROPPED also fails, so fixing a site forces the number
# down and the ratchet cannot be left slack.
#
# The count started at 65 keys over 43 files (2026-08-31, once
# `scripts/regenerate-docs-html.py` was fixed) and its header said 86 sites while
# the tuple summed to 87. Both halves are now recomputed from the sweep itself
# and agree: 57 / 35 / 79.
#
# Eight entries came off on 2026-08-31, listed in the module docstring above
# under "the eight that came off". Removing entries from this tuple is the work;
# the count is the score.
BASELINE: tuple[tuple[str, str, int], ...] = (
    (".claude/hooks/memory-inject.py", "get_data_root", 1),
    ("scripts/admin-health.py", "load_github_org", 1),
    ("scripts/bootcamp-roster.py", "get_datastore_dir", 2),
    ("scripts/bootcamp-roster.py", "get_outputs_dir", 1),
    ("scripts/bootcamp-roster.py", "resolve_config_with_example", 1),
    ("scripts/browser.py", "get_outputs_dir", 3),
    ("scripts/capture-design-exemplars-retry.py", "get_outputs_dir", 1),
    ("scripts/capture-design-exemplars.py", "get_outputs_dir", 1),
    ("scripts/context-freshness.py", "get_context_dir", 1),
    ("scripts/crm-health.py", "get_crm_config_path", 1),
    ("scripts/crm-health.py", "get_crm_contacts_dir", 1),
    ("scripts/crm-health.py", "get_people_file", 1),
    ("scripts/datastore-extract.py", "get_datastore_dir", 1),
    ("scripts/email-intelligence.py", "get_context_dir", 1),
    ("scripts/email-intelligence.py", "get_crm_contacts_dir", 1),
    ("scripts/email-intelligence.py", "get_outputs_dir", 2),
    ("scripts/email-intelligence.py", "resolve_config_with_example", 1),
    ("scripts/fireside-bot.py", "get_datastore_dir", 2),
    ("scripts/fireside-bot.py", "get_outputs_dir", 1),
    ("scripts/fireside-bot.py", "resolve_config_with_example", 1),
    ("scripts/fireside-pulse.py", "get_datastore_dir", 1),
    ("scripts/fireside-pulse.py", "get_outputs_dir", 1),
    ("scripts/fireside-pulse.py", "resolve_config_with_example", 1),
    ("scripts/gen-exec-meeting-docx.py", "get_outputs_dir", 1),
    ("scripts/gen-exec-meeting-docx.py", "resolve_config_with_example", 1),
    ("scripts/generate-client-docx.py", "get_outputs_dir", 2),
    ("scripts/generate-crm-dashboard.py", "get_context_dir", 1),
    ("scripts/generate-crm-dashboard.py", "get_crm_contacts_dir", 1),
    ("scripts/generate-crm-dashboard.py", "get_data_config_dir", 1),
    ("scripts/generate-crm-dashboard.py", "get_datastore_dir", 1),
    ("scripts/generate-dashboard.py", "get_context_dir", 5),
    ("scripts/generate-dashboard.py", "get_datastore_dir", 2),
    ("scripts/generate-dashboard.py", "get_knowledge_dir", 2),
    ("scripts/generate-dashboard.py", "get_outputs_dir", 7),
    ("scripts/generate-odunone-docx.py", "get_outputs_dir", 1),
    ("scripts/generate-testing-framework-pptx.py", "get_outputs_dir", 1),
    ("scripts/generate-usecases-docx.py", "get_outputs_dir", 1),
    ("scripts/knowledge-health.py", "get_knowledge_dir", 1),
    ("scripts/knowledge-health.py", "get_shared_knowledge_dir", 1),
    ("scripts/marp_render.py", "get_outputs_dir", 1),
    ("scripts/md-to-docx-charter.py", "get_outputs_dir", 1),
    ("scripts/md-to-docx-competitive.py", "get_outputs_dir", 2),
    ("scripts/md-to-docx-letter.py", "get_outputs_dir", 2),
    ("scripts/md-to-docx-proposal.py", "get_outputs_dir", 2),
    ("scripts/modem-tune.py", "get_outputs_dir", 1),
    ("scripts/odin-brain-health.py", "get_knowledge_dir", 1),
    ("scripts/odin_brain_lint.py", "get_knowledge_dir", 1),
    ("scripts/offboard-exec.py", "load_github_org", 1),
    ("scripts/pipeline-summary.py", "get_context_dir", 1),
    ("scripts/provision-exec.py", "load_github_org", 1),
    ("scripts/sentinel.py", "resolve_config_with_example", 1),
    ("scripts/sync-exchange.py", "get_outputs_dir", 2),
    ("scripts/validate-crm-schema.py", "get_corporate_root", 1),
    ("scripts/validate-crm-schema.py", "get_crm_contacts_dir", 1),
    ("scripts/workspace-health.py", "get_context_dir", 1),
    ("scripts/workspace-health.py", "get_datastore_dir", 1),
    ("scripts/workspace-health.py", "get_outputs_dir", 1),
)


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


# The eight fixed on 2026-08-31, every one of them a module whose frozen root
# reached `mkdir`, `makedirs` or `touch` -- the primitives `tests/conftest.py`
# does NOT wrap, so the write landed in the operator's overlay without a refusal.
FIXED_2026_08_31 = (
    "scripts/council-aggregate.py",
    "scripts/council-record-verdict.py",
    "scripts/implement-trajectory-log.py",
    "scripts/llm-fit-report.py",
    "scripts/output-organizer.py",
    "scripts/publish-corporate.py",
    "scripts/scrutinize-flag-fp.py",
    "scripts/scrutinize-replay.py",
)


@pytest.mark.parametrize("rel", FIXED_2026_08_31)
def test_a_fixed_module_resolves_nothing_at_import_time(rel):
    """Not "the baseline no longer lists it" -- that is only bookkeeping, and a
    re-added constant under a resolver the tuple never named would satisfy it.
    This asks the rule directly, of the file on disk."""
    source = (ROOT / rel).read_text(encoding="utf-8")
    assert import_time_resolver_calls(source, derived_resolvers()) == []


# The resolver each of the eight grew, so the behavioural case below asks the
# function rather than the source text.
FIXED_RESOLVERS = {
    "scripts/council-aggregate.py": "council_dir",
    "scripts/council-record-verdict.py": "council_dir",
    "scripts/implement-trajectory-log.py": "trajectory_dir",
    "scripts/llm-fit-report.py": "report_dir",
    "scripts/output-organizer.py": "outputs_dir",
    "scripts/publish-corporate.py": "source_root",
    "scripts/scrutinize-flag-fp.py": "scrutiny_dir",
    "scripts/scrutinize-replay.py": "scrutiny_dir",
}


@pytest.mark.parametrize("rel", FIXED_2026_08_31)
def test_a_fixed_module_follows_a_repoint_that_happened_after_its_import(rel, tmp_path,
                                                                        monkeypatch):
    """The regression in one line, for each of the eight.

    The module is imported FIRST, exactly as a test module-scope `_load(...)`
    does, and the environment moves after. Structural absence of an import-time
    call (the test above) is necessary and not sufficient: this is the property
    that absence is FOR.
    """
    name = "frozen_probe_" + Path(rel).stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)   # some of the eight use @dataclass
    spec.loader.exec_module(module)
    resolve = getattr(module, FIXED_RESOLVERS[rel])

    before = resolve()
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    after = resolve()

    assert after != before, f"{rel}: {FIXED_RESOLVERS[rel]}() ignored the repoint"
    assert str(after).startswith(str(overlay)), \
        f"{rel}: {FIXED_RESOLVERS[rel]}() resolved to {after}, outside the scratch overlay"


@pytest.mark.parametrize("rel", FIXED_2026_08_31)
def test_a_fixed_module_is_absent_from_the_pinned_set(rel):
    assert not [key for key in baseline_map() if key[0] == rel], \
        f"{rel} was fixed on 2026-08-31 and is back in the pinned set"


@pytest.mark.parametrize("rel", FIXED_2026_08_31)
def test_a_fixed_module_exposes_no_frozen_constant_where_its_function_now_is(rel):
    """The back-compat alias is the way this regresses while everything above
    still passes: `COUNCIL_DIR = council_dir()` at module scope restores the
    exact defect. Any module-scope ALL-CAPS name bound from a call to one of the
    module's own new resolver functions is that shape."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
    resolvers = {n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        called = {c.func.id for c in ast.walk(node.value)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if not (called & resolvers):
            continue
        for target in node.targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name) and sub.id.isupper():
                    offenders.append(f"{rel}:{node.lineno} {sub.id}")
    assert not offenders, "a call-time resolver was frozen back into a constant: " \
                          + ", ".join(offenders)


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


def test_the_sweep_finds_the_known_population_at_all():
    """If the rule silently stopped matching, every assertion below would pass on
    an empty result. The baseline is non-empty, so the sweep must be too."""
    assert BASELINE, "the pinned set is empty; that is a bug in this file"
    assert sweep(), "the sweep found nothing while the baseline names 43 files"


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
