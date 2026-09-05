#!/usr/bin/env python3
"""Skip a test file only when nothing it can read has changed.

The operator's question, 2026-09-04: "если у нас есть что-то, что уже
построено, прошло все тесты и работает, зачем нам гонять тесты каждый раз".

The answer this module implements: because most of the suite reads the engine
checkout and nothing else, so when the checkout has not changed, most of the
suite cannot produce a different verdict. The rest -- the files that reach the
private data overlay, the machine, or the clock -- can change verdict with the
tree untouched, so they run every time.

MEASURED 2026-09-04 in HELM, `-n auto`, sixteen cores, on a settled box with the
PROCESS TABLE checked rather than the load average (a `--durations` entry from a
contended run measures contention, not code):

    all 155 core files          258.3 s   5369 tests
    26 "must always run" files   98.0 s    626 tests   at one-minute load 0.60
    the leak gate alone          61.0 s     32 tests   at load 1.10

Every pytest invocation also pays 16-19 s of xdist startup, sixteen workers each
importing a 1877-line conftest, so nothing here can go below roughly twenty
seconds. This module must not be described as making it faster than that.

THREE THINGS IT IS NOT
----------------------

It is not a default. `scripts/run-tests.py`, the pre-push gate and CI are
untouched, and must stay untouched: a cache is a claim about what could have
changed, and a gate that ships code is the wrong place to accept such a claim.

It is not a substitute for the night contract. The full suite runs nightly, day
mode's `mark-green` moves the base, and a nightly failure is LOUD. Every verdict
here is stored against that base, so moving it discards them all.

It is not a list. The set of files that must always run is DERIVED from the tree
on every call. A hand-maintained list of "these must always run" falls behind
silently and the day it matters is the day nobody notices; that happened in this
repository on 2026-09-04, when `STOPWORDS` in the content denylist carried a
comment reading "this is the ONLY such collision in the tree" -- true when
written and false the moment a CRM row landed. So a NEW test file that reaches
the data root lands in the must-run set with nothing edited, and
`tests/test_a_verdict_cache_that_skipped_on_doubt.py` fails if it does not.

FAIL CLOSED, ALWAYS
-------------------

Every uncertainty runs the test. No cache entry, an unreadable store, a file the
classifier could not parse, a hash it could not compute, a git command that
failed: each means RUN. A cache that skips on doubt is a hole with a speed-up
painted on it, and there is a test for this direction rather than a sentence.

STORAGE
-------

One SQLite file, `.cache/test-verdicts.db`, joining `.memory-index/index.db` and
`.codegraph/codegraph.db` as the workspace's existing embedded stores per
`.claude/rules/persistence.md`. No daemon, no port, no server database.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.repo_files import (  # noqa: E402
    IndexUnreadable, content_digest, working_tree_paths,
)
from scripts.utils.verdict_store import SqliteVerdictStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
STORE_PATH = ROOT / ".cache" / "test-verdicts.db"
SCHEMA_VERSION = "1"

# ============================================================
# Buckets
# ============================================================

ENGINE = "engine"
DATA_OVERLAY = "data_overlay"
ENVIRONMENT = "environment"
CLOCK = "clock"
UNSURE = "unsure"

#: Every bucket other than ENGINE means "run this file every time". Written as
#: the complement of the one cacheable bucket rather than as a list of the
#: uncacheable ones, so a bucket added later is uncacheable until somebody
#: deliberately says otherwise. The other direction -- a list of uncacheable
#: buckets -- makes a new bucket silently cacheable, which is the failure this
#: whole module is built to avoid.
CACHEABLE_BUCKETS = frozenset({ENGINE})


# ============================================================
# The signals
# ============================================================

#: `get_data_root`, `get_workspace_root`, `get_crm_contacts_dir`,
#: `get_auto_memory_dir` ... every accessor in `scripts/utils/workspace.py` that
#: resolves a location outside the engine checkout is spelled this way. Matched
#: as a PATTERN and not as a list, because the list is the thing that falls
#: behind: a `get_whatever_dir` added to that module tomorrow is caught tonight.
#: It over-matches on purpose (`get_config_dir` resolves inside the engine and
#: is caught anyway), and over-matching runs a test that need not have run.
_DATA_CALL_RE = re.compile(r"^get_[A-Za-z0-9_]*_(root|dir|path)$")

#: Environment names that repoint the data root. `WORKSPACE_ROOT` is here
#: because `.env` carries it and a copied `.env` repoints `get_workspace_root()`
#: and with it every guard downstream (CLAUDE.md, "Guards must be armed inside
#: the task").
_DATA_ENV_NAMES = frozenset({"HEADING_OS_DATA", "WORKSPACE_ROOT"})

#: A literal naming the private overlay directly.
_DATA_LITERALS = (".heading-os-data",)

#: Calls that ask the MACHINE rather than the checkout.
_ENV_CALL_NAMES = frozenset({
    "home", "expanduser", "which", "gethostname", "getfqdn", "uname",
    "getlogin", "getuser", "cpu_count", "getloadavg", "loadavg",
    # The checkout's own git identity: which clone this is, and where the main
    # clone lives. Both read state that is not in any tracked file -- the
    # worktree registration under `.git/worktrees` -- so both can change verdict
    # with the tree byte-identical.
    "main_clone_path", "is_main_clone", "require_main_clone",
    # Reading `.env`. The file is GITIGNORED, so it is not in the corpus the
    # cache key measures and a change to it cannot move that key. A test whose
    # behaviour depends on it must therefore run every time. This is not
    # theoretical here: `tests/integration/conftest.py` records that importing
    # `scripts.sentinel` runs a module body ending in `load_env(WORKSPACE_ROOT)`,
    # which puts roughly 70 real credential names into `os.environ` for the rest
    # of the pytest session.
    "load_env", "load_api_key", "load_dotenv",
})

#: Literals that name something outside the checkout.
#:
#: The absolute ones are anchored with a lookbehind, and that lookbehind is not
#: decoration. This suite is full of path-traversal fixtures -- `"../etc/passwd"`
#: handed to a guard that must refuse it -- and a bare `/etc/` substring matched
#: every one of them. Measured 2026-09-04: eleven files, none of which reads
#: anything outside the checkout, landed in the environment bucket on that string
#: alone. A preceding `.` or word character means the path is relative, so it
#: names something inside whatever tree the test built.
_ENV_LITERAL_RES = tuple(re.compile(p) for p in (
    r"(?<![\w.])/proc/", r"(?<![\w.])/sys/", r"(?<![\w.])/etc/",
    r"(?<![\w.])/var/run", r"(?<![\w.])/run/user", r"(?<![\w.])/mnt/c",
    r"~/\.claude", r"\.git/worktrees",
))

#: Modules whose whole point is to ask the machine.
_ENV_MODULES = frozenset({"socket", "platform", "psutil"})

#: Spawning a child. The dependency is not the spawn; it is WHICH executable a
#: BARE tool name resolves to, which is decided by `PATH` and by what is
#: installed, and neither is in the corpus the key measures. `git` upgraded or
#: removed changes such a test's verdict with the tree byte-identical.
#:
#: MEASURED 2026-09-04: 83 of the 864 test files this classifier otherwise
#: called cacheable spawn a bare-name tool. Counting them costs 7.7 points of
#: cacheable share (80.1% -> 72.4%) and removes that whole class of false green,
#: which is the trade this module's fail-closed rule settles in advance. It is a
#: DISAGREEMENT with the hand classification in the task brief, which counted 9
#: of 155 core files as machine-dependent and did not count this route.
#:
#: THE LIMIT, stated rather than left to be discovered: `sys.executable` is NOT
#: flagged. It names the pinned `.venv` interpreter, whose dependency set is
#: pinned by `uv.lock` and `pyproject.toml`, and both of those ARE in the
#: corpus. An install performed without re-locking would slip past that, and
#: nothing here catches it.
_SPAWN_CALL_NAMES = frozenset({
    "run", "Popen", "call", "check_call", "check_output",
    "execvp", "execvpe", "spawnvp", "system",
})

#: Reading the live clock. On its own this is not a dependency -- a test that
#: stamps `datetime.now()` into a file in `tmp_path` and reads it back is
#: hermetic. It becomes one when the reading is measured against a THRESHOLD,
#: which is what the second tuple looks for in the same function.
_CLOCK_CALL_NAMES = frozenset({"now", "utcnow", "today", "time", "monotonic"})
_CLOCK_THRESHOLD_NAMES = frozenset({"timedelta", "st_mtime", "getmtime", "st_ctime"})

#: Calls whose leading arguments DESIGNATE a target rather than read it. The
#: brief's own distinction: `monkeypatch.setenv` is the test CONTROLLING its
#: input, not depending on the outside. The count is how many leading positional
#: arguments are designation; everything after is still a value and is still
#: walked, so `monkeypatch.setenv("HEADING_OS_DATA", str(get_data_root()))` is
#: caught on `get_data_root` even though the NAME is carved out.
_CONTROL_CALLS = {
    "setenv": 1, "delenv": 1, "setitem": 2, "delitem": 2, "chdir": 1,
    "patch": 1, "object": 2, "dict": 1,
}

#: `setattr` takes either `(target, name, value)` or `("dotted.name", value)`,
#: so its designation width depends on the call. Handled separately below.


@dataclass
class Finding:
    """One reason a file cannot be cached, with the line that says so."""
    bucket: str
    signal: str
    line: int

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.bucket}:{self.signal}@{self.line}"


@dataclass
class Classification:
    """What one file reads, and therefore whether its verdict can be reused."""
    path: str
    bucket: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def cacheable(self) -> bool:
        return self.bucket in CACHEABLE_BUCKETS

    @property
    def why(self) -> str:
        if not self.findings:
            return "reads only the engine checkout"
        return ", ".join(str(f) for f in self.findings[:4])


def _worst(buckets) -> str:
    """The bucket a file lands in when its findings disagree.

    Order is by how loudly the bucket says "do not cache me", and ENGINE is last
    so that any finding at all beats it.
    """
    for candidate in (UNSURE, DATA_OVERLAY, ENVIRONMENT, CLOCK):
        if candidate in buckets:
            return candidate
    return ENGINE


class _SourceScanner(ast.NodeVisitor):
    """Walk one module and record every read of something outside the checkout.

    Asked of the AST rather than of the text, per
    `.claude/rules/development-standards.md` obligation 8: a substring scan goes
    red the moment a fix quotes the bad pattern to explain it, which teaches
    people to stop explaining. This module's own docstring names
    `HEADING_OS_DATA` four times and is not itself a dependency.
    """

    def __init__(self) -> None:
        self.findings: list[Finding] = []
        # Environment names this file SETS. A file that sets a name and then
        # reads it is reading its own value, so the read is not a dependency.
        self.controlled_env: set[str] = set()
        self.fixture_names: set[str] = set()
        self._clock_calls: list[Finding] = []
        self._threshold_lines: set[int] = set()
        self._pending_env: list[tuple[str, int]] = []

    # -- helpers ------------------------------------------------------

    def _add(self, bucket: str, signal: str, node: ast.AST) -> None:
        self.findings.append(Finding(bucket, signal, getattr(node, "lineno", 0)))

    @staticmethod
    def _called_name(node: ast.Call) -> str:
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return ""

    @staticmethod
    def _designation_width(name: str, node: ast.Call) -> int:
        if name == "setattr":
            # `setattr(target, "name", value)` designates two; the dotted-string
            # form `setattr("a.b.c", value)` designates one.
            return 2 if len(node.args) >= 3 else 1
        return _CONTROL_CALLS.get(name, 0)

    # -- visits -------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        name = self._called_name(node)

        if _DATA_CALL_RE.match(name):
            self._add(DATA_OVERLAY, name, node)
        elif name in _ENV_CALL_NAMES:
            self._add(ENVIRONMENT, name, node)
        elif name in _CLOCK_CALL_NAMES:
            self._clock_calls.append(
                Finding(CLOCK, name, getattr(node, "lineno", 0)))
        elif name in _CLOCK_THRESHOLD_NAMES:
            self._threshold_lines.add(getattr(node, "lineno", 0))

        if name in _SPAWN_CALL_NAMES and node.args:
            tool = _bare_tool_name(node.args[0])
            if tool is not None:
                self._add(ENVIRONMENT, f"spawn:{tool}", node)

        # An environment READ. `os.getenv(NAME)` / `os.environ.get(NAME)`.
        #
        # The receiver is checked for `.get`, and that check is the whole
        # difference between a detector and a nuisance. Without it every
        # `payload.get("studio")`, `row.get("inbox")` and `result.get("ok")` in
        # the suite reads as an environment dependency: measured 2026-09-04
        # before the check went in, 308 of 1077 test files landed in the
        # environment bucket and the great majority were dict lookups.
        is_environ_get = (
            name == "get"
            and isinstance(node.func, ast.Attribute)
            and _is_environ(node.func.value)
        )
        if (name == "getenv" or is_environ_get) and node.args:
            literal = _string_of(node.args[0])
            if literal is not None:
                self._env_read(literal, node)

        width = self._designation_width(name, node)
        if width:
            for index, arg in enumerate(node.args):
                if index < width:
                    # Designation. Record what it CONTROLS, then do not read it.
                    literal = _string_of(arg)
                    if literal is not None:
                        self.controlled_env.add(literal)
                    continue
                self.visit(arg)
            for keyword in node.keywords:
                self.visit(keyword.value)
            return

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # `os.environ["NAME"]`, in either a read or a write position. A write is
        # recorded as control by `visit_Assign` below before this ever runs.
        if _is_environ(node.value):
            literal = _string_of(node.slice)
            if literal is not None:
                self._env_read(literal, node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript) and _is_environ(target.value):
                literal = _string_of(target.slice)
                if literal is not None:
                    self.controlled_env.add(literal)
        self.generic_visit(node)

    # A DOCSTRING is not a dependency. It explains, it does not read -- the same
    # carve-out `.claude/rules/scope-claims.md` makes for its own scanner. This
    # module's docstring names the private overlay four times and
    # `tests/integration/conftest.py`'s names it once, in a paragraph recording
    # a measurement; without this, that paragraph put every file in that
    # directory into the must-run set for the crime of being documented.
    def visit_Module(self, node: ast.Module) -> None:
        self._visit_body(node.body)

    def visit_FunctionDef(self, node) -> None:
        self._visit_body(node.body)
        for decorator in node.decorator_list:
            self.visit(decorator)
        if node.returns is not None:
            self.visit(node.returns)
        self.visit(node.args)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Every child except the docstring, spelled out rather than left to
        # `generic_visit`. These four overrides exist only to drop docstrings,
        # and an override that forgets a branch is an unvisited subtree, which
        # in a dependency scanner is a false GREEN rather than a missing warning.
        self._visit_body(node.body)
        for child in node.bases + node.decorator_list:
            self.visit(child)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def _visit_body(self, body) -> None:
        for statement in _without_docstring(body):
            self.visit(statement)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            text = node.value
            for needle in _DATA_LITERALS:
                if needle in text:
                    self._add(DATA_OVERLAY, needle, node)
            for pattern in _ENV_LITERAL_RES:
                if pattern.search(text):
                    self._add(ENVIRONMENT, pattern.pattern, node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] in _ENV_MODULES:
                self._add(ENVIRONMENT, f"import {alias.name}", node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.split(".")[0] in _ENV_MODULES:
            self._add(ENVIRONMENT, f"from {node.module}", node)
        for alias in node.names:
            if _DATA_CALL_RE.match(alias.name):
                self._add(DATA_OVERLAY, alias.name, node)
        self.generic_visit(node)

    # -- internals ----------------------------------------------------

    def _env_read(self, name: str, node: ast.AST) -> None:
        if name in _DATA_ENV_NAMES:
            self._add(DATA_OVERLAY, name, node)
        else:
            # Deferred: a name the file sets ITSELF is not an outside read, and
            # the set may appear after the read in source order. Resolved once
            # the whole module has been walked, in `finish()`.
            self._pending_env.append((name, getattr(node, "lineno", 0)))

    def finish(self) -> list[Finding]:
        """Resolve the findings that need the whole module to decide."""
        for name, line in self._pending_env:
            if name not in self.controlled_env:
                self.findings.append(Finding(ENVIRONMENT, f"env:{name}", line))
        # A live-clock read counts only where a THRESHOLD is measured against
        # it. `datetime.now()` written into a `tmp_path` file and read back is
        # hermetic; `datetime.now() - timedelta(days=30)` compared to a file's
        # mtime is not. The window is the enclosing three lines rather than the
        # enclosing function, because a threshold and its clock read are written
        # together and a function-wide window swept in every incidental
        # `time.time()` in a long test module.
        for finding in self._clock_calls:
            if any(abs(finding.line - line) <= 3 for line in self._threshold_lines):
                self.findings.append(finding)
        return self.findings


def _bare_tool_name(node: ast.AST) -> str | None:
    """The tool a spawn resolves through ``PATH``, or None if it does not.

    An ABSOLUTE path (`/usr/bin/git`) and a path built from a variable
    (`sys.executable`, `tmp_path / "stub"`) are both excluded: the first names
    the file directly and the second is not a literal this can read. Only a bare
    name is a `PATH` lookup.
    """
    first = node
    if isinstance(node, (ast.List, ast.Tuple)):
        if not node.elts:
            return None
        first = node.elts[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    value = first.value
    if not value or value.startswith(("/", ".")):
        return None
    return value


def _without_docstring(body):
    """``body`` with a leading string-expression statement dropped."""
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return list(body)


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return True
    return isinstance(node, ast.Name) and node.id == "environ"


def _string_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _fixture_names_used(tree: ast.AST) -> set[str]:
    """Fixture names a module ASKS FOR, by every route pytest offers.

    The indirect case this exists for: a fixture in `tests/conftest.py` can pull
    the data root in for a file whose own text mentions none of it. `helm_root`
    is the live example -- it calls `main_clone_path` and returns a path outside
    this checkout, and a file that merely names it in a signature inherits that.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (node.name.startswith("test_") or _has_fixture_decorator(node))):
            args = node.args
            for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                names.add(arg.arg)
        if isinstance(node, ast.Call):
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            if attr == "usefixtures":
                for arg in node.args:
                    literal = _string_of(arg)
                    if literal is not None:
                        names.add(literal)
    return names


def _has_fixture_decorator(node) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


# ============================================================
# Fixtures declared by the conftest chain
# ============================================================

@dataclass
class _Fixture:
    name: str
    bucket: str
    requests: set[str]
    autouse: bool
    scope_dir: str


def _scan_conftest(path: Path, root: Path) -> list[_Fixture]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="surrogateescape"))
    except (OSError, SyntaxError, ValueError):
        # A conftest that cannot be parsed governs files whose dependencies
        # cannot be known. Reported as one unparseable fixture named for the
        # directory, autouse, UNSURE: everything under it runs.
        return [_Fixture("<unparseable conftest>", UNSURE, set(), True,
                         path.parent.relative_to(root).as_posix())]

    scope_dir = path.parent.relative_to(root).as_posix()
    fixtures: list[_Fixture] = []

    # Module-level code in a conftest runs for every file it governs, so it is
    # modelled as one always-on autouse "fixture".
    module_body = [n for n in _without_docstring(tree.body)
                   if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef))]
    module_scanner = _SourceScanner()
    for node in module_body:
        module_scanner.visit(node)
    module_findings = module_scanner.finish()
    if module_findings:
        fixtures.append(_Fixture(
            f"<{scope_dir or '.'} conftest module body>",
            _worst({f.bucket for f in module_findings}), set(), True, scope_dir))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _has_fixture_decorator(node):
            continue
        scanner = _SourceScanner()
        for child in _without_docstring(node.body):
            scanner.visit(child)
        findings = scanner.finish()
        args = node.args
        requests = {a.arg for a in
                    list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)}
        fixtures.append(_Fixture(
            node.name,
            _worst({f.bucket for f in findings}),
            requests,
            _is_autouse(node),
            scope_dir,
        ))
    return fixtures


def _is_autouse(node) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "autouse":
                return bool(getattr(keyword.value, "value", False))
    return False


def _resolve_fixture_buckets(fixtures: list[_Fixture]) -> dict[str, str]:
    """Each fixture's bucket, widened by the buckets of the fixtures it requests.

    A fixture that asks for `helm_root` is as machine-dependent as `helm_root`.
    Iterated to a fixed point rather than recursed, so a cycle in the graph
    terminates instead of overflowing the stack.
    """
    buckets = {f.name: f.bucket for f in fixtures}
    by_name = {f.name: f for f in fixtures}
    for _ in range(len(fixtures) + 1):
        changed = False
        for fixture in fixtures:
            widened = {buckets[fixture.name]}
            for requested in fixture.requests:
                if requested in by_name:
                    widened.add(buckets[requested])
            resolved = _worst(widened)
            if resolved != buckets[fixture.name]:
                buckets[fixture.name] = resolved
                changed = True
        if not changed:
            break
    return buckets


class Classifier:
    """Bucket every test file by what it can read, derived from the tree."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = ROOT if root is None else Path(root)
        self._fixtures: list[_Fixture] | None = None
        self._buckets: dict[str, str] = {}

    def _load_fixtures(self) -> None:
        if self._fixtures is not None:
            return
        fixtures: list[_Fixture] = []
        for conftest in sorted((self.root / "tests").rglob("conftest.py")):
            fixtures.extend(_scan_conftest(conftest, self.root))
        self._fixtures = fixtures
        self._buckets = _resolve_fixture_buckets(fixtures)

    def _inherited(self, rel: str) -> list[Finding]:
        """Findings a file inherits from AUTOUSE fixtures above it."""
        assert self._fixtures is not None
        out: list[Finding] = []
        for fixture in self._fixtures:
            if not fixture.autouse:
                continue
            prefix = f"{fixture.scope_dir}/" if fixture.scope_dir else ""
            if not rel.startswith(prefix):
                continue
            bucket = self._buckets.get(fixture.name, fixture.bucket)
            if bucket != ENGINE:
                out.append(Finding(bucket, f"autouse:{fixture.name}", 0))
        return out

    def classify(self, path) -> Classification:
        self._load_fixtures()
        path = Path(path)
        absolute = path if path.is_absolute() else self.root / path
        try:
            rel = absolute.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            rel = absolute.as_posix()

        try:
            source = absolute.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError as exc:
            return Classification(rel, UNSURE, [Finding(UNSURE, f"unreadable: {exc}", 0)])
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            return Classification(rel, UNSURE, [Finding(UNSURE, f"unparseable: {exc}", 0)])

        scanner = _SourceScanner()
        scanner.visit(tree)
        findings = list(scanner.finish())

        for name in sorted(_fixture_names_used(tree)):
            bucket = self._buckets.get(name)
            if bucket is not None and bucket != ENGINE:
                findings.append(Finding(bucket, f"fixture:{name}", 0))

        findings.extend(self._inherited(rel))
        return Classification(rel, _worst({f.bucket for f in findings}), findings)


# ============================================================
# The cache key
# ============================================================

class KeyUnavailable(Exception):
    """The corpus could not be enumerated or hashed. Nothing may be skipped."""


def corpus_key(root: Path | None = None) -> str:
    """A digest of the WORKING TREE MINUS GITIGNORED PATHS.

    That corpus, and not the git index, because `repo_files.tracked_paths` --
    which roughly a hundred sweeps in this suite read the tree through -- globs
    the working tree and subtracts only what git ignores. An UNTRACKED,
    non-ignored file is therefore IN what those sweeps see. A key built from
    `git ls-files` would hand back yesterday's green for a tree that is not
    yesterday's tree: a scratch `.py` a parallel agent dropped under `scripts/`,
    a half-written test, a `.md` a crashed tool left behind. Not hypothetical
    here -- `read_sources`' own docstring records 2026-08-30, when exactly such a
    file appeared and vanished mid-walk and a guard reported a violation that had
    not occurred.

    Derived from `repo_files.working_tree_paths` rather than from a second
    spelling of the rule, so the corpus the key measures and the corpus the
    sweeps read cannot drift apart.

    Both the NAME and the CONTENT of every file go into the digest, so a rename,
    a deletion and an edit each move the key. A file that vanishes between the
    listing and the read moves it too, by being absent from the digest -- which
    is the safe direction, since the next call sees a different tree and runs.
    """
    repo = ROOT if root is None else Path(root)
    try:
        names = working_tree_paths(repo)
    except IndexUnreadable as exc:
        raise KeyUnavailable(f"cannot enumerate the working tree: {exc}") from exc

    digest = hashlib.sha256()
    digest.update(f"v{SCHEMA_VERSION}\0".encode())
    for name in names:
        try:
            blob = (repo / name).read_bytes()
        except FileNotFoundError:
            # Vanished between the listing and the read. Its absence changes the
            # digest, which is the direction that runs rather than skips.
            continue
        except OSError as exc:
            raise KeyUnavailable(f"cannot hash {name}: {exc}") from exc
        digest.update(name.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(content_digest(blob).encode("ascii"))
    return digest.hexdigest()


# ============================================================
# The verdict store
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts (
    base        TEXT NOT NULL,
    corpus_key  TEXT NOT NULL,
    test_file   TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (base, corpus_key, test_file)
);
"""


class VerdictStore(SqliteVerdictStore):
    """Which test files passed, against which base and which corpus key.

    A row exists only for a file that PASSED. There is no failure row and no
    "unknown" row, because the only question asked of this store is "may this be
    skipped", and every answer other than a present row is no.

    The connect-and-fail-closed half moved to `scripts/utils/verdict_store.py`
    on 2026-09-05, when the content-leak gate's own verdict cache needed the
    same twelve lines with a different table.
    """

    SCHEMA = _SCHEMA
    # The module constant, which `corpus_key` also stamps into the key, so the
    # two cannot name different generations of the same store.
    SCHEMA_VERSION = globals()["SCHEMA_VERSION"]

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(STORE_PATH if path is None else Path(path))

    def passed_files(self, base: str, key: str) -> set[str] | None:
        """The files recorded green for this base and key, or None if unreadable.

        `None` and the empty set are the whole difference between "the store
        says nothing passed" and "the store could not be read", and a caller
        that reads the second as the first is still correct HERE only because
        both run everything. It is returned distinctly anyway so the caller can
        say which happened, per `.claude/rules/scope-claims.md`.
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            rows = conn.execute(
                "SELECT test_file FROM verdicts WHERE base = ? AND corpus_key = ?",
                (base, key)).fetchall()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return None
        finally:
            conn.close()
        return {row[0] for row in rows}

    def record(self, base: str, key: str, files) -> bool:
        """Record these files as passed. False when the store could not be written."""
        from datetime import datetime, timezone
        conn = self._connect()
        if conn is None:
            return False
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO verdicts "
                "(base, corpus_key, test_file, recorded_at) VALUES (?, ?, ?, ?)",
                [(base, key, str(f), stamp) for f in files])
            conn.commit()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return False
        finally:
            conn.close()
        return True

    def revoke(self, base: str, key: str, files) -> bool:
        """Drop these files' verdicts. The night run's hand on the switch.

        A nightly failure must be LOUD and must not leave a green behind it. The
        tree is often byte-identical between the run that passed and the run that
        failed, so the key alone cannot retract anything; this can.
        """
        conn = self._connect()
        if conn is None:
            return False
        try:
            conn.executemany(
                "DELETE FROM verdicts WHERE base = ? AND corpus_key = ? "
                "AND test_file = ?",
                [(base, key, str(f)) for f in files])
            conn.commit()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return False
        finally:
            conn.close()
        return True

    def clear(self) -> bool:
        conn = self._connect()
        if conn is None:
            # Nothing readable is already nothing cached, and the caller's next
            # `plan` runs everything either way.
            return False
        try:
            conn.execute("DELETE FROM verdicts")
            conn.commit()
        except sqlite3.DatabaseError as exc:
            self.corrupt_reason = str(exc)
            return False
        finally:
            conn.close()
        return True

    def rows(self) -> int:
        conn = self._connect()
        if conn is None:
            return 0
        try:
            return conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
        except sqlite3.DatabaseError:
            return 0
        finally:
            conn.close()


# ============================================================
# The selector
# ============================================================

@dataclass
class Plan:
    """What to run, what to skip, and why -- every file accounted for."""
    run: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)
    reasons: dict = field(default_factory=dict)
    key: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def must_run(self) -> list[str]:
        return [f for f in self.run
                if self.reasons.get(f, "").startswith("must-run")]


def plan_run(files, base: str, *, root: Path | None = None,
             store: VerdictStore | None = None,
             classifier: Classifier | None = None) -> Plan:
    """Partition ``files`` into what must run and what may be skipped.

    Every branch that cannot establish "this file's inputs are unchanged" puts
    the file in `run`. There is no branch that skips on doubt.
    """
    repo = ROOT if root is None else Path(root)
    store = VerdictStore() if store is None else store
    classifier = Classifier(repo) if classifier is None else classifier
    plan = Plan()

    files = [str(f) for f in files]

    try:
        plan.key = corpus_key(repo)
    except KeyUnavailable as exc:
        plan.warnings.append(
            f"cache DISABLED: the corpus key could not be computed ({exc}). "
            f"Running all {len(files)} file(s).")
        plan.run = list(files)
        plan.reasons = dict.fromkeys(files, "no-key")
        return plan

    passed = store.passed_files(base, plan.key)
    if passed is None:
        plan.warnings.append(
            f"cache DISABLED: the verdict store at {store.path} could not be "
            f"read ({store.corrupt_reason}). Running all {len(files)} file(s).")
        plan.run = list(files)
        plan.reasons = dict.fromkeys(files, "store-unreadable")
        return plan

    for name in files:
        verdict = classifier.classify(name)
        if not verdict.cacheable:
            plan.run.append(name)
            plan.reasons[name] = f"must-run ({verdict.bucket}: {verdict.why})"
        elif verdict.path in passed or name in passed:
            plan.skip.append(name)
            plan.reasons[name] = "cached green at this key"
        else:
            plan.run.append(name)
            plan.reasons[name] = "no verdict at this key"
    return plan
