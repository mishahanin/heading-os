"""A default argument that captured a path is frozen at import, and no patch reaches it.

Python evaluates a default expression ONCE, when the `def` statement runs, and
stores the result in the function object's `__defaults__` tuple. A default
written as `def __init__(self, path: Path = STATE_FILE)` therefore captures
whatever `STATE_FILE` pointed at during import. Rebinding the module global
afterwards rebinds the global and nothing else: `__defaults__` still holds the
original object, so a no-argument construction still opens the original file.

On 2026-08-29 that turned an audit run into a write against the operator's real
data. `scripts/email-intelligence.py` carried
`def __init__(self, path: Path = STATE_FILE)`, a test set the module global
`STATE_FILE` to a temporary file, and the no-argument `StateManager()` inside
the module resolved the live data overlay anyway. The run loaded the real
state, applied its retention caps, and saved. Two message ids and two
conversation keys were evicted. The file is gitignored runtime state, so there
was no copy to restore from.

`scripts/sentinel.py` carried the identical shape at its own `StateManager`,
and an AST sweep of `scripts/` and `.claude/` found six more, in five files.
All eight now read the same way:

    def __init__(self, path: Path | None = None):
        self.path = STATE_FILE if path is None else path

That fixed the default. The module constant on the right of it was the SAME
freeze one level up - `STATE_FILE = get_outputs_dir() / ...` also asked the
data root once, during import - so the constants that a data-root resolver
produced have since become call-time resolvers of their own
(`state_file()`, `config_file()`), and the tests below patch the resolver
rather than a constant. Where a constant is anchored to the workspace root and
not the data root, it stays a constant: `scripts/sentinel.py`'s `STATE_FILE`
is still one, and is still patched as one here.

This file pins two things. Per fixed site, that patching the module global now
redirects a no-argument call, that an explicit argument still wins, that a
positional caller still works, and that `__defaults__` holds no path. Then the
part that matters more: a repository-wide AST rule that walks every tracked
Python file under `scripts/` and `.claude/` and fails on this shape wherever it
appears, so the ninth one cannot be written.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.repo_files import (  # noqa: E402
    tracked_python_files as _shared_tracked_python_files,
)


# ============================================================
# The AST rule. Shared by the repository sweep and by its own self-test.
# ============================================================

SCAN_DIRS = ("scripts", ".claude")

# Callables that return a path anchored to the runtime data root, the workspace
# root, or the filesystem. A module global assigned from one of these is exactly
# the value a caller or a test legitimately wants to redirect.
PATH_FACTORIES = frozenset({
    "get_data_root", "get_outputs_dir", "get_workspace_root", "get_crm_contacts_dir",
    "get_context_dir", "get_knowledge_dir", "get_threads_dir", "get_plans_dir",
    "get_datastore_dir", "get_data_config_dir", "get_auto_memory_dir",
    "get_reference_dir", "get_records_dir", "get_sessions_dir", "get_runtime_dir",
    "get_state_dir", "get_cache_dir", "get_logs_dir", "get_brand_dir",
    "get_engine_root", "resolve_config_with_example",
    "Path", "PurePath",
})

# Defaults that legitimately capture a module constant, each with the reason it
# is safe. A plain string, an int, or a tuple is not this defect: nothing about
# it is a path a test needs to redirect. Entries are
# (relative path, qualified function name, parameter name, reason).
#
# Empty is the honest state right now: the sweep found eight sites of this shape
# and all eight were fixed rather than exempted. The list stays because the next
# genuinely safe case should be recorded here with its reason instead of
# silently weakening the rule, and the staleness test below refuses an entry
# that no longer names a real function.
ALLOWED: tuple[tuple[str, str, str, str], ...] = ()


def _root_name(node: ast.AST) -> str | None:
    """The module-level name a default expression is anchored to, if any."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            node = node.left
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value
        else:
            return None


def _is_pathish(node: ast.AST) -> tuple[bool, str | None]:
    """(produces a path, name of the constant it is built from).

    The second element lets the caller resolve a chain such as
    `RUNTIME_DIR = WORKSPACE_ROOT / ".sentinel"` and drop the candidate when the
    root of the chain is not itself a path.
    """
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in PATH_FACTORIES:
            return True, None
        if isinstance(fn, ast.Attribute):      # Path(...).resolve()
            return _is_pathish(fn.value)
        return False, None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_pathish(node.left)          # BASE / "sub" / "file.json"
    if isinstance(node, ast.Subscript):
        return _is_pathish(node.value)         # Path(f).resolve().parents[2]
    if isinstance(node, ast.Attribute):
        return _is_pathish(node.value)         # Path(f).resolve().parent
    if isinstance(node, ast.Name):
        return True, node.id
    return False, None


def module_path_constants(tree: ast.Module) -> set[str]:
    """Module-level names bound to a path-valued expression."""
    consts: set[str] = set()
    for stmt in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(stmt, ast.Assign):
            targets, value = list(stmt.targets), stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            ok, dependency = _is_pathish(value)
            if not ok:
                continue
            if dependency is not None and dependency not in consts:
                continue
            consts.add(target.id)
    return consts


def frozen_path_defaults(source: str) -> list[tuple[int, str, str, str]]:
    """Every default in `source` that captures a module-level path constant.

    Returns (line number, qualified function name, parameter, default source).
    """
    tree = ast.parse(source)
    consts = module_path_constants(tree)
    if not consts:
        return []
    found: list[tuple[int, str, str, str]] = []

    class Walker(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def _function(self, node) -> None:
            args = node.args
            positional = args.posonlyargs + args.args
            pad: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
            pairs = list(zip(positional, pad + list(args.defaults), strict=True))
            pairs += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
            qualified = ".".join(self.scope + [node.name])
            for parameter, default in pairs:
                if default is None:
                    continue
                root = _root_name(default)
                if root is not None and root in consts:
                    found.append((node.lineno, qualified, parameter.arg,
                                  ast.unparse(default)))
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

    Walker().visit(tree)
    return found


def allow_list_defects(entries) -> list[str]:
    """Why each allow-list entry cannot be trusted, if anything.

    A stale exemption is an exemption nobody is watching: the function it names
    is renamed or deleted, the rule stops looking at it, and the exemption keeps
    reading as a deliberate decision about code that is gone.
    """
    problems: list[str] = []
    for relative, qualified, parameter, reason in entries:
        if not reason.strip():
            problems.append(f"{relative}::{qualified} is exempt with no reason written down")
            continue
        path = ROOT / relative
        if not path.is_file():
            problems.append(f"allow-list entry {relative} no longer exists")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        signatures = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for arg in args.posonlyargs + args.args + args.kwonlyargs:
                    signatures.add((node.name, arg.arg))
        if (qualified.split(".")[-1], parameter) not in signatures:
            problems.append(
                f"allow-list entry {relative}::{qualified}({parameter}) is stale: "
                "the function or the parameter is gone")
    return problems


def tracked_python_files() -> list[Path]:
    """Every Python file under the scanned directories that git does not ignore.

    This function used to spell `git check-ignore` here. It was the FIRST place
    in the suite to ask git rather than keep a hand-written skip list, and it
    stayed the only one, so sixteen other sweeps went on walking the tree blind
    until 2026-08-29. The implementation moved to `tests/repo_files.py` so there
    is one of it; the reasoning and the measurement live in that module and in
    `tests/test_a_walker_that_never_asked_git.py`.
    """
    return _shared_tracked_python_files(SCAN_DIRS)


# ============================================================
# Loading the fixed modules
# ============================================================

def _load_by_path(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def email_intel():
    return _load_by_path("frozen_defaults_email_intel", "scripts/email-intelligence.py")


@pytest.fixture(scope="module")
def sentinel():
    return _load_by_path("frozen_defaults_sentinel", "scripts/sentinel.py")


@pytest.fixture(scope="module")
def bridge_entry():
    return _load_by_path("frozen_defaults_bridge", "scripts/bridge-daemon.py")


@pytest.fixture(scope="module")
def sync_docs():
    # Imported rather than run through runpy: run_path hands back a COPY of the
    # namespace, so writing to that copy leaves the function's own __globals__
    # untouched and the redirect would be measuring nothing. The file guards on
    # __name__, so importing it runs no hook.
    return _load_by_path("frozen_defaults_sync_docs", ".claude/hooks/sync-docs.py")


def _captured_paths(function) -> list[object]:
    defaults = list(function.__defaults__ or ())
    defaults += list((function.__kwdefaults__ or {}).values())
    return [d for d in defaults if isinstance(d, Path)]


# ============================================================
# scripts/email-intelligence.py :: StateManager - the site of the incident
# ============================================================

def test_patching_the_email_intelligence_state_file_now_redirects_a_no_argument_state_manager(
    email_intel, monkeypatch, tmp_path
):
    redirected = tmp_path / "redirected-state.json"
    monkeypatch.setattr(email_intel, "state_file", lambda p=redirected: p)
    assert email_intel.StateManager().path == redirected


def test_an_explicit_path_still_beats_the_email_intelligence_module_global(
    email_intel, monkeypatch, tmp_path
):
    monkeypatch.setattr(email_intel, "state_file", lambda p=tmp_path / "ignored.json": p)
    explicit = tmp_path / "explicit.json"
    assert email_intel.StateManager(path=explicit).path == explicit


def test_a_positional_caller_of_the_email_intelligence_state_manager_still_works(
    email_intel, tmp_path
):
    positional = tmp_path / "positional.json"
    assert email_intel.StateManager(positional).path == positional


def test_the_email_intelligence_state_manager_captures_no_path_in_its_defaults(email_intel):
    assert _captured_paths(email_intel.StateManager.__init__) == []


# ============================================================
# scripts/sentinel.py :: StateManager, SentinelConfig, Sentinel
# ============================================================

def test_patching_the_sentinel_state_file_now_redirects_a_no_argument_state_manager(
    sentinel, monkeypatch, tmp_path
):
    redirected = tmp_path / "sentinel-state.json"
    monkeypatch.setattr(sentinel, "STATE_FILE", redirected)
    assert sentinel.StateManager().path == redirected


def test_an_explicit_state_path_still_beats_the_sentinel_module_global(
    sentinel, monkeypatch, tmp_path
):
    monkeypatch.setattr(sentinel, "STATE_FILE", tmp_path / "ignored.json")
    explicit = tmp_path / "explicit.json"
    assert sentinel.StateManager(state_path=explicit).path == explicit


def test_a_positional_caller_of_the_sentinel_state_manager_still_works(sentinel, tmp_path):
    positional = tmp_path / "positional.json"
    manager = sentinel.StateManager(positional, True)
    assert manager.path == positional
    assert manager.read_only is True


def test_the_sentinel_state_manager_captures_no_path_in_its_defaults(sentinel):
    assert _captured_paths(sentinel.StateManager.__init__) == []
    # read_only keeps its literal default; only the path was the problem.
    assert sentinel.StateManager.__init__.__defaults__ == (None, False)


def _write_minimal_sentinel_config(directory: Path) -> Path:
    config = directory / "sentinel_config.yaml"
    config.write_text("general:\n  enabled: true\n", encoding="utf-8")
    return config


def test_patching_the_sentinel_config_file_now_redirects_a_no_argument_sentinel_config(
    sentinel, monkeypatch, tmp_path
):
    config = _write_minimal_sentinel_config(tmp_path)
    monkeypatch.setattr(sentinel, "config_file", lambda p=config: p)
    assert sentinel.SentinelConfig() is not None


def test_a_missing_patched_sentinel_config_is_reported_against_the_patched_path(
    sentinel, monkeypatch, tmp_path
):
    absent = tmp_path / "absent.yaml"
    monkeypatch.setattr(sentinel, "config_file", lambda p=absent: p)
    with pytest.raises(FileNotFoundError) as excinfo:
        sentinel.SentinelConfig()
    assert str(absent) in str(excinfo.value)


def test_a_positional_caller_of_sentinel_config_still_works(sentinel, tmp_path):
    config = _write_minimal_sentinel_config(tmp_path)
    assert sentinel.SentinelConfig(config) is not None


def test_neither_sentinel_config_nor_the_sentinel_orchestrator_captures_a_path(sentinel):
    assert _captured_paths(sentinel.SentinelConfig.__init__) == []
    assert _captured_paths(sentinel.Sentinel.__init__) == []


def test_the_sentinel_orchestrator_reads_the_patched_config_file_when_given_none(
    sentinel, monkeypatch, tmp_path
):
    """Sentinel() with no config_path must reach the CURRENT module global.

    Constructing the orchestrator is heavy, so the assertion is on the path
    SentinelConfig is handed: that is the value the frozen default used to
    override.
    """
    config = _write_minimal_sentinel_config(tmp_path)
    monkeypatch.setattr(sentinel, "config_file", lambda p=config: p)
    seen: list[Path] = []

    class Recorder:
        def __init__(self, path):
            seen.append(path)
            raise RuntimeError("stop here, the recorded path is what matters")

    monkeypatch.setattr(sentinel, "SentinelConfig", Recorder)
    with pytest.raises(RuntimeError):
        sentinel.Sentinel()
    assert seen == [config]


# ============================================================
# scripts/bridge-daemon.py :: _configure_logging
# ============================================================

def test_patching_the_bridge_log_path_now_redirects_a_no_argument_configure_logging(
    bridge_entry, monkeypatch, tmp_path
):
    from scripts.bridge_daemon.error_tracker import _reset_for_tests

    redirected = tmp_path / "logs" / "bridge.log"
    monkeypatch.setattr(bridge_entry, "LOG_PATH", redirected)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_apscheduler = logging.getLogger("apscheduler").level
    saved_factory = logging.getLogRecordFactory()
    _reset_for_tests()
    try:
        bridge_entry._configure_logging()
        logging.getLogger().warning("redirected write")
        logging.shutdown()
        assert redirected.exists()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        logging.getLogger("apscheduler").setLevel(saved_apscheduler)
        logging.setLogRecordFactory(saved_factory)
        _reset_for_tests()


def test_the_bridge_configure_logging_captures_no_path_in_its_defaults(bridge_entry):
    assert _captured_paths(bridge_entry._configure_logging) == []


# ============================================================
# scripts/rule_split_check.py :: check_inventories, _rule_union_sentences
# ============================================================

def test_patching_the_rule_split_inventory_dir_now_redirects_a_no_argument_check(
    monkeypatch, tmp_path
):
    from scripts import rule_split_check

    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "navigation.md").write_text("Always set the heading.\n", encoding="utf-8")
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    (inventory / "navigation.md.txt").write_text(
        "This directive was dropped from the rule.\n", encoding="utf-8")

    monkeypatch.setattr(rule_split_check, "INVENTORY_DIR", inventory)
    dropped = rule_split_check.check_inventories(rules_dir=str(rules))
    assert [stem for stem, _ in dropped] == ["navigation.md"]


def test_an_explicit_inventory_dir_still_beats_the_rule_split_module_global(
    monkeypatch, tmp_path
):
    from scripts import rule_split_check

    monkeypatch.setattr(rule_split_check, "INVENTORY_DIR", tmp_path / "never-read")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rule_split_check.check_inventories(inventory_dir=empty,
                                              rules_dir=str(tmp_path)) == []


def test_a_positional_caller_of_the_rule_split_helpers_still_works(tmp_path):
    from scripts import rule_split_check

    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "navigation.md").write_text("Always set the heading.\n", encoding="utf-8")
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    sentences = rule_split_check._rule_union_sentences(
        "navigation.md", str(rules), inventory)
    assert any("heading" in s for s in sentences)


def test_the_rule_split_helpers_capture_no_path_in_their_defaults():
    from scripts import rule_split_check

    assert _captured_paths(rule_split_check.check_inventories) == []
    assert _captured_paths(rule_split_check._rule_union_sentences) == []


# ============================================================
# .claude/hooks/sync-docs.py :: sync_targets
# ============================================================

def _published_template(overlay: Path, name: str) -> Path:
    path = overlay / "templates" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def test_patching_the_sync_docs_engine_root_now_redirects_a_no_argument_sync_targets(
    sync_docs, monkeypatch, tmp_path
):
    name = sorted(sync_docs.ENGINE_PUBLISHED)[0]
    template = _published_template(tmp_path / "overlay", name)
    redirected = tmp_path / "redirected-engine"
    monkeypatch.setattr(sync_docs, "ENGINE_ROOT", redirected)
    assert redirected / "docs" / name in sync_docs.sync_targets(template)


def test_an_explicit_engine_root_still_beats_the_sync_docs_module_global(
    sync_docs, monkeypatch, tmp_path
):
    name = sorted(sync_docs.ENGINE_PUBLISHED)[0]
    template = _published_template(tmp_path / "overlay", name)
    monkeypatch.setattr(sync_docs, "ENGINE_ROOT", tmp_path / "never-read")
    explicit = tmp_path / "explicit-engine"
    assert explicit / "docs" / name in sync_docs.sync_targets(template, engine_root=explicit)


def test_a_positional_caller_of_sync_targets_still_works(sync_docs, tmp_path):
    name = sorted(sync_docs.ENGINE_PUBLISHED)[0]
    template = _published_template(tmp_path / "overlay", name)
    explicit = tmp_path / "explicit-engine"
    assert explicit / "docs" / name in sync_docs.sync_targets(template, explicit)


def test_sync_targets_captures_no_path_in_its_defaults(sync_docs):
    assert _captured_paths(sync_docs.sync_targets) == []


# ============================================================
# The repository-wide rule
# ============================================================

_DEFECT_FIXTURE = '''
from pathlib import Path
from scripts.utils.workspace import get_data_root

DATA_ROOT = get_data_root()
STATE_FILE = DATA_ROOT / "state" / "ledger.json"


class Ledger:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
'''

_FIXED_FIXTURE = '''
from pathlib import Path
from scripts.utils.workspace import get_data_root

DATA_ROOT = get_data_root()
STATE_FILE = DATA_ROOT / "state" / "ledger.json"


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = STATE_FILE if path is None else path
'''

_HARMLESS_FIXTURE = '''
DEFAULT_TIMEOUT = 30
STORE_REL = ".memory-index/index.db"
DEFAULT_PATTERNS = ("*.py", "*.md")


def fetch(timeout: int = DEFAULT_TIMEOUT, store: str = STORE_REL,
          patterns: tuple = DEFAULT_PATTERNS, retries: int = 0,
          marker=None):
    return timeout, store, patterns, retries, marker
'''


def test_the_ast_rule_flags_the_shape_that_caused_the_incident():
    """Anti-vacuity: the rule must still fire on a file that has the defect."""
    found = frozen_path_defaults(_DEFECT_FIXTURE)
    assert [(qualified, parameter) for _, qualified, parameter, _ in found] == [
        ("Ledger.__init__", "path")
    ]


def test_the_ast_rule_accepts_the_shape_that_replaced_it():
    assert frozen_path_defaults(_FIXED_FIXTURE) == []


def test_the_ast_rule_does_not_flag_a_timeout_a_relative_string_or_a_tuple():
    """The defect is a captured PATH, not any captured constant."""
    assert frozen_path_defaults(_HARMLESS_FIXTURE) == []


def test_the_ast_rule_follows_a_chain_of_module_level_path_constants():
    """The sentinel case: STATE_FILE is two hops from get_workspace_root()."""
    source = '''
from pathlib import Path
from scripts.utils.workspace import get_workspace_root

WORKSPACE_ROOT = get_workspace_root()
RUNTIME_DIR = WORKSPACE_ROOT / ".sentinel"
STATE_FILE = RUNTIME_DIR / "state.json"


def load(state_path: Path = STATE_FILE):
    return state_path
'''
    assert [q for _, q, _, _ in frozen_path_defaults(source)] == ["load"]


def test_the_repository_sweep_reaches_a_real_and_non_trivial_set_of_files():
    """Anti-vacuity: a sweep over nothing proves nothing."""
    files = tracked_python_files()
    assert len(files) > 100, f"only {len(files)} files walked; the sweep is not reaching the tree"
    names = {p.relative_to(ROOT).as_posix() for p in files}
    for expected in ("scripts/sentinel.py", "scripts/email-intelligence.py",
                     "scripts/bridge-daemon.py", "scripts/rule_split_check.py",
                     ".claude/hooks/sync-docs.py"):
        assert expected in names, f"{expected} is missing from the sweep"


def test_the_repository_sweep_finds_functions_that_carry_defaults_at_all():
    """Anti-vacuity: confirm the walker parses real code and sees real defaults.

    Without this, a walker that silently returned nothing for every file would
    make the rule below pass over an empty world.
    """
    with_defaults = 0
    for path in tracked_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    node.args.defaults
                    or any(d is not None for d in node.args.kw_defaults)):
                with_defaults += 1
    assert with_defaults > 200, (
        f"only {with_defaults} functions with defaults found; the AST walk is not working")


def test_the_allow_list_currently_on_disk_carries_no_stale_entry():
    assert allow_list_defects(ALLOWED) == []


def test_the_staleness_check_accepts_an_exemption_that_still_names_a_real_parameter():
    live = (("tests/test_defaults_that_froze_a_path_at_import.py",
             "frozen_path_defaults", "source",
             "self-reference used only to exercise this check"),)
    assert allow_list_defects(live) == []


def test_the_staleness_check_rejects_an_exemption_whose_function_is_gone():
    stale = (("tests/test_defaults_that_froze_a_path_at_import.py",
              "a_function_that_was_deleted_long_ago", "path", "a reason"),)
    defects = allow_list_defects(stale)
    assert len(defects) == 1 and "stale" in defects[0]


def test_the_staleness_check_rejects_an_exemption_for_a_file_that_is_gone():
    missing = (("scripts/a-script-that-no-longer-exists.py", "load", "path", "a reason"),)
    defects = allow_list_defects(missing)
    assert len(defects) == 1 and "no longer exists" in defects[0]


def test_the_staleness_check_rejects_an_exemption_with_no_reason_written_down():
    unexplained = (("tests/test_defaults_that_froze_a_path_at_import.py",
                    "frozen_path_defaults", "source", "   "),)
    defects = allow_list_defects(unexplained)
    assert len(defects) == 1 and "no reason" in defects[0]


def test_no_function_under_scripts_or_claude_freezes_a_path_into_its_defaults():
    """The rule that stops the ninth one.

    A default evaluated at import cannot be redirected by patching the module
    global it was copied from. Give the parameter a `None` default and resolve
    the constant in the body:

        def __init__(self, path: Path | None = None):
            self.path = STATE_FILE if path is None else path
    """
    exempt = {(relative, qualified, parameter)
              for relative, qualified, parameter, _ in ALLOWED}
    violations: list[str] = []
    for path in tracked_python_files():
        relative = path.relative_to(ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            hits = frozen_path_defaults(source)
        except SyntaxError:
            continue
        for lineno, qualified, parameter, default in hits:
            if (relative, qualified, parameter) in exempt:
                continue
            violations.append(
                f"{relative}:{lineno}  {qualified}({parameter}={default})")
    assert not violations, (
        "these defaults capture a module-level path at import time, so patching "
        "the global does not redirect them:\n  " + "\n  ".join(violations))
