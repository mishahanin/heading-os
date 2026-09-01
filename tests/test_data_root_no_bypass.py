#!/usr/bin/env python3
"""Enforcement guard: no code may join a workspace/engine root to a DATA directory.

HEADING OS engine/data separation invariant (spec
docs/superpowers/specs/2026-06-12-heading-os-engine-data-separation-design.md,
Section "regression guard"): after cutover the engine clone (.heading-os) holds
NO data; every data read/write must resolve under the DATA root via the
get_*_dir() helpers (which route through get_data_root() / get_personal_root() /
get_corporate_root()). Code that does `get_workspace_root() / "outputs"` or
`WORKSPACE_ROOT / "crm"` bypasses the seam and misroutes private data INTO the
engine clone -- both a correctness bug and a leak (no gitignore safety in the
engine for these dirs).

This test fails on any such bypass so the principle is enforced by default for all
NEW code: engine always clean, ALL data in the data root. The fix for a flagged
line is always to use the matching helper:

    get_workspace_root() / "outputs" / x   ->  get_outputs_dir() / x
    WORKSPACE_ROOT / "crm" / "contacts"    ->  get_crm_contacts_dir()
    ROOT / "threads" / t                   ->  get_threads_dir() / t
    workspace_root / "knowledge"           ->  get_knowledge_dir()
    ... / "plans" / p                      ->  get_plans_dir() / p
    ... / "datastore" / d                  ->  get_datastore_dir() / d

The helper DEFINITIONS in scripts/utils/workspace.py + paths.py are the only place
these literals legitimately sit next to a root, so those files are exempt. Engine
dirs (reference/, config/, scripts/, docs/, .claude/, examples/, tests/) are NOT
data and are not flagged.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# The bare __file__-parent idiom that resolves to the ENGINE root for a script in
# scripts/ -- `Path(__file__).resolve().parent.parent` (or without .resolve()) and
# `os.path.dirname(os.path.dirname(...))`. The original guard knew the named-root
# tokens below but NOT this idiom, so a script doing `BASE =
# Path(__file__).resolve().parent.parent` then `Path(BASE) / "outputs"` slipped a
# data write into the engine clone undetected (5 doc/deck generators did exactly
# this -- 2026-06-16 finding #3). Both producer lists now include it.
_FILE_PARENT_PRODUCER = (
    r"Path\(__file__\)(?:\.resolve\(\))?\.parent\.parent"
    r"|os\.path\.dirname\(\s*os\.path\.dirname\("
)
# Root identifiers that resolve to the ENGINE/workspace root (not the data root).
# Includes the module-const ALIASES (WORKSPACE, WS) that the original migration
# missed -- a file doing `WORKSPACE = get_workspace_root()` then `WORKSPACE /
# "outputs"` bypasses the seam exactly like the explicit-token form.
_ROOT_TOKENS = r"(?:get_workspace_root\(\)|WORKSPACE_ROOT|workspace_root|\bWORKSPACE\b|\bWS\b|PROJECT_ROOT|PROJECT_DIR|\bROOT|" + _FILE_PARENT_PRODUCER + r")"
# DATA directories that must be reached via a get_*_dir() helper, never joined to
# an engine root directly. context/ resolves under the data root for the CEO
# (get_personal_context_dir / get_context_dir), so it belongs here too.
#
# `auto-memory`, `chronicle`, `admin` and `personal` were added 2026-09-01.
# All four route `private` in `config/routing-map.yaml` for every rule that
# names them, all four live only in the DATA overlay, and none of them was in
# this alternation - so `get_workspace_root() / "auto-memory"` was a seam bypass
# this guard reported as clean. MEASURED that day: the shipped pattern returned
# False on `x = get_workspace_root() / "auto-memory" / "MEMORY.md"` and on the
# same line spelled with `chronicle`, while the `outputs` spelling one character
# away was caught. `auto-memory` is the memory store the workspace calls its own
# second brain; misrouting it into the engine clone puts operator memory in a
# public repository.
#
# Widening cost nothing: with all four added, the line scan and the cross-line
# binder both returned zero hits across 442 files, so none of this is a new
# false positive on existing code.
#
# NOT added, and the reason is worth writing down rather than leaving as a gap
# someone re-derives later. `templates/` also routes `private` and also lives
# only in the overlay, and adding it flags one real line -
# `scripts/regenerate-docs-html.py:71`, `dirs = [ROOT / "docs", ROOT /
# "templates"]`. That is a READ of a directory the engine tree does not contain
# (`git ls-files templates` returns nothing), so it leaks nothing and simply
# globs an absent path; the function adds `data_root / "templates"` separately
# two lines below. It is pre-existing and outside this guard's purpose, so it is
# surfaced here rather than silently forced green or silently deleted.
# `docs`, `config`, `scripts`, `reference` and `.claude` are deliberately absent
# for a different reason: each defaults to `engine` with per-file `private`
# carve-outs, so joining one to an engine root is ordinary correct code.
_DATA_DIRS = (r"(?:threads|crm|outputs|knowledge|plans|datastore|context"
              r"|auto-memory|chronicle|admin|personal)")
# Operator form: ROOT / "outputs"  or  ROOT + "outputs".
_BYPASS = re.compile(_ROOT_TOKENS + r"\s*(?:/|\+)\s*[\"']" + _DATA_DIRS + r"\b")
# os.path.join form: os.path.join(<...>, ROOT, "outputs", ...) -- the operator
# regex misses the comma separator, so this branch catches the join() bypass that
# previously slipped data writes into the engine tree (telegram download default,
# firecrawl cache, docx output).
_BYPASS_JOIN = re.compile(
    r"os\.path\.join\([^)]*" + _ROOT_TOKENS + r"\s*,\s*[\"']" + _DATA_DIRS + r"\b"
)
# joinpath() and f-string forms the operator/join regexes above miss. Restricted to
# UNAMBIGUOUS producer expressions -- a direct get_workspace_root() call or the
# __file__-parent idiom -- NOT the bare aliases (workspace_root/ROOT/WS/...). That
# restriction is deliberate: a benign INJECTED parameter like dead_letter.py's
# `workspace_root.joinpath("outputs")` (param default None; real path via
# get_outputs_dir()) must not be false-flagged. Alias vars that are PROVEN
# engine-bound are handled binding-aware in the cross-line test below, which adds
# the same two forms per collected var.
_DIRECT_PRODUCER = r"(?:get_workspace_root\(\)|" + _FILE_PARENT_PRODUCER + r")"
# get_workspace_root().joinpath("outputs")  /  (Path(__file__)...).joinpath("crm")
_BYPASS_JOINPATH = re.compile(_DIRECT_PRODUCER + r"\.joinpath\(\s*[\"']" + _DATA_DIRS + r"\b")
# f"{get_workspace_root()}/outputs/..."  (data dir interpolated under the engine root)
_BYPASS_FSTRING = re.compile(
    r"\{[^{}]*(?:" + _DIRECT_PRODUCER + r")[^{}]*\}/" + _DATA_DIRS + r"\b"
)

# Files allowed to contain the literal pattern: the helper definitions themselves,
# this guard, and dead archived code (never executed; not part of the live engine).
_EXEMPT_SUBSTRINGS = (
    "scripts/utils/workspace.py",
    "scripts/utils/paths.py",
    "tests/test_data_root_no_bypass.py",
    "/archive/",
    # NOTE: scripts/build_engine_repo.py was formerly blanket-exempt here, on the
    # premise that it "legitimately" joins `root / "outputs"` for classification.
    # That premise was false: classification runs on STRING tokens (_DATA_TOKENS,
    # _DATA_DIR_IGNORES), never on `root / "<datadir>"` path joins. The only real
    # `root / "outputs"` join in the file was a BUGGED manifest write that dropped
    # build provenance into the engine clone -- and the exemption hid it from this
    # guard (2026-06-28). The write now uses get_outputs_dir(); the file is guarded
    # like every other. Do NOT re-add it here -- a `root / "<datadir>"` reappearing
    # in this file is exactly the regression that must fail CI.
)

# Producers that yield the ENGINE root (NOT the data root). A var bound to one of
# these and later joined to a data dir is a cross-line seam bypass the line-based
# _BYPASS regex misses (it cannot tell a data-root `root` from an engine-root one).
# The __file__-parent idiom is included so `BASE =
# Path(__file__).resolve().parent.parent; Path(BASE) / "outputs"` is caught.
_ENGINE_PRODUCER = r"(?:get_workspace_root\(\)|WORKSPACE_ROOT|workspace_root|PROJECT_ROOT|PROJECT_DIR|\bWORKSPACE\b|\bWS\b|" + _FILE_PARENT_PRODUCER + r")"
# Anchor the binding to assignment-statement position (line start or after `;`),
# so a keyword argument like `load_entity(slug, workspace_root=workspace_root)` is
# NOT mistaken for `workspace_root = <producer>`. Without the anchor, the bare
# `workspace_root` producer token matched the kwarg `name=value` form and falsely
# collected the var (false positive on scripts/utils/crm.py, 2026-06-16).
_ENGINE_BIND_RE = re.compile(r"(?:^|;)\s*(\w+)\s*=\s*" + _ENGINE_PRODUCER, re.MULTILINE)

# Names a regex binder cannot recognise, whatever the producer alternation says.
# `_ENGINE_BIND_RE` requires the producer to sit IMMEDIATELY after the `=`, which
# is the anchor that stopped `load_entity(slug, workspace_root=workspace_root)`
# being read as an assignment (2026-06-16). The anchor is right and it is also
# blind, and on 2026-08-29 the blindness cost something real:
#
#     _ws_root = Path(workspace_root) if workspace_root else _WORKSPACE_ROOT
#     _stages = parse_pipeline_stages(_ws_root / "context" / "pipeline.md")
#
# in `scripts/utils/crm.py`. Two independent reasons it was invisible. The
# producer is behind a TERNARY, so nothing engine-shaped follows the `=`; and it
# carries a LEADING UNDERSCORE, so even matching after `else` fails, because
# `WORKSPACE_ROOT` cannot match starting at the `_` of `_WORKSPACE_ROOT`.
# Measured: both the shipped binder and a widened `else`-aware one returned []
# on that line.
#
# The consequence was not theoretical. `context/pipeline.md` and `crm/aliases.md`
# are operator data and do not exist in the engine clone, so both reads resolved
# to nothing, and stage-aware CRM cadence had never once applied in production.
#
# The AST does not care about ternaries or underscores. It is ADDED beside the
# regex rather than replacing it, so the existing coverage and its survivor floor
# are untouched, and a parse failure degrades to the regex rather than to silence.
# CONSTANTS only, plus the two direct producers. A lower-case `workspace_root` is
# excluded deliberately: as a parameter it is a value the CALLER chose, which is
# the sanctioned fixture and exec-repo override, not the frozen engine root. The
# first version of this binder included it and produced three false positives,
# all of them correct code following the very pattern this guard recommends:
# `crm_autolog._address_book_dir` and `_contacts_dir` (seam when the argument is
# None, explicit tree when it is given) and `aggregate-crm.py`, where `repo_path`
# is another executive's clone. `_WORKSPACE_ROOT` is a different thing entirely:
# a module constant computed from `__file__` that no caller can redirect.
_ENGINE_CONST_RE = re.compile(r"^_*(?:WORKSPACE_ROOT|PROJECT_ROOT|PROJECT_DIR|WORKSPACE|WS)$")


def _is_file_parent_idiom(node: "ast.AST") -> bool:
    """`Path(__file__)[.resolve()].parent.parent`, at two or more parents.

    The argument is checked, not just the shape: `Path(other).parent.parent` is
    somebody else's tree and must not be called an engine root. Two parents is
    the floor because one gets you `scripts/`, not the repository root.

    The `os.path.dirname(os.path.dirname(...))` spelling is NOT handled here.
    The regex binder already covers it and still runs beside this one; a second
    partial copy would be the very shape this whole shard is about.
    """
    depth = 0
    cur = node
    while True:
        if isinstance(cur, ast.Attribute):
            if cur.attr == "parent":
                depth += 1
            elif cur.attr != "resolve":
                return False
            cur = cur.value
        elif isinstance(cur, ast.Call):
            if (isinstance(cur.func, ast.Name) and cur.func.id == "Path"
                    and len(cur.args) == 1
                    and isinstance(cur.args[0], ast.Name)
                    and cur.args[0].id == "__file__"):
                return depth >= 2
            cur = cur.func
        else:
            return False


def _mentions_engine_producer(node: "ast.AST") -> bool:
    """True when an expression reads an engine-root value anywhere inside it."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and _ENGINE_CONST_RE.match(child.id):
            return True
        if isinstance(child, ast.Attribute) and (
                _ENGINE_CONST_RE.match(child.attr) or _is_file_parent_idiom(child)):
            return True
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                and child.func.id == "get_workspace_root"):
            return True
    return False


def _engine_bound_vars_ast(text: str) -> set[str]:
    """Local names assigned an expression that mentions an engine-root producer.

    Keyword arguments cannot reach this: `f(x, workspace_root=y)` is a `Call`,
    never an `Assign`, so the false positive the regex anchor exists to prevent
    is structurally impossible here rather than defended against.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()  # the regex binder still covers this file
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _mentions_engine_producer(value):
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    found.add(name.id)
    return found


def _bound_var_violations(text: str, rel: str) -> tuple[list[str], int]:
    """Engine-bound vars in `text` that are joined to a data directory.

    Extracted from the tree scan on 2026-08-29 so a unit test can drive the SAME
    code on a synthetic file. It was inline, and the tree is clean, so unwiring
    the AST binder from it changed nothing and the mutation survived: a guard is
    green over an empty corpus whether or not it works. Now the wiring has a
    case with a real violation in it.

    Returns `(violations, vars_checked)`; the count is the survivor floor's
    input.
    """
    # Union, not replacement. The regex still catches the
    # `os.path.dirname(os.path.dirname(...))` spelling the AST binder
    # deliberately leaves alone, and it keeps working on a file the AST cannot
    # parse. The AST adds the ternary and underscored-constant bindings the
    # regex cannot see. Measured 2026-08-29 over 430 files: 211 vars found by
    # both, 45 regex-only, 66 AST-only, and zero new violations once a
    # caller-supplied `workspace_root` was excluded from the AST producers.
    engine_vars = set(_ENGINE_BIND_RE.findall(text)) | _engine_bound_vars_ast(text)
    engine_vars.discard("")  # safety
    violations: list[str] = []
    for v in sorted(engine_vars):
        # \)? after the var name catches the `Path(BASE) / "outputs"` wrapper
        # form, not just the bare `BASE / "outputs"`.
        op = re.compile(r"\b" + re.escape(v) + r"\b\)?\s*(?:/|\+)\s*[\"']" + _DATA_DIRS + r"\b")
        join = re.compile(
            r"os\.path\.join\([^)]*\b" + re.escape(v) + r"\s*,\s*[\"']" + _DATA_DIRS + r"\b"
        )
        # v is PROVEN engine-bound here, so joinpath/f-string forms of the same
        # bypass are unambiguous, with no benign-param risk like the line-based
        # test guards against.
        jp = re.compile(r"\b" + re.escape(v) + r"\b\.joinpath\(\s*[\"']" + _DATA_DIRS + r"\b")
        fs = re.compile(r"\{[^{}]*\b" + re.escape(v) + r"\b[^{}]*\}/" + _DATA_DIRS + r"\b")
        for i, line in enumerate(text.splitlines(), 1):
            if op.search(line) or join.search(line) or jp.search(line) or fs.search(line):
                violations.append(f"{rel}:{i}: ({v} is engine-root-bound) {line.strip()}")
    return violations, len(engine_vars)


def _scan_roots() -> list[Path]:
    root = get_workspace_root()
    return [root / "scripts", root / ".claude"]


def _is_exempt(rel: str) -> bool:
    return any(s in rel for s in _EXEMPT_SUBSTRINGS)


def _nested_checkout_roots(base: Path) -> tuple[Path, ...]:
    """Directories under *base* that are separate git checkouts.

    A git worktree or submodule nested in the engine tree (Claude Code places
    them under `.claude/worktrees/`) is a checkout of this same repository, not
    engine source. Two reasons it must not be walked, and the second is the one
    that makes this a scope defect rather than a preference.

    Every file in it is a copy of a file this guard already scans in its own
    tree, so one real finding is reported twice, and a finding that belongs to
    the other checkout's BRANCH is reported as this tree's.

    And the copy is walked WIDER than the original. `_scan_roots` covers
    `scripts/` and `.claude/` only, so a worktree living under `.claude/` drags
    that checkout's entire `tests/` tree into a scan that deliberately excludes
    `tests/` here. Measured 2026-07-27 against a locked worktree: 22 violations
    across 16 files, every one of them a `tests/` file this guard does not scan
    in its own tree, and not one of them present in this tree.

    Skipping them loses no coverage. Git already excludes the worktree
    directory, and each checkout runs this same guard over itself.
    """
    return tuple(marker.parent for marker in base.rglob(".git"))


def _scan_files(base: Path, root: Path):
    """Yield `(path, relative posix path)` for every guarded `.py` file."""
    nested = _nested_checkout_roots(base)
    for py in base.rglob("*.py"):
        if any(py.is_relative_to(checkout) for checkout in nested):
            continue
        yield py, py.relative_to(root).as_posix()


def test_no_data_dir_joined_to_engine_root():
    root = get_workspace_root()
    violations: list[str] = []
    inspected = 0
    for base in _scan_roots():
        if not base.exists():
            continue
        for py, rel in _scan_files(base, root):
            if _is_exempt(rel):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            inspected += 1
            for i, line in enumerate(text.splitlines(), 1):
                if (_BYPASS.search(line) or _BYPASS_JOIN.search(line)
                        or _BYPASS_JOINPATH.search(line) or _BYPASS_FSTRING.search(line)):
                    violations.append(f"{rel}:{i}: {line.strip()}")
    # Survivor floor: an empty offender list only means "clean" if files actually
    # reached the line scan. Measured 427 files on 2026-08-26; floored well below
    # so retiring a script does not fail this test. If _scan_roots() stops
    # resolving, or _is_exempt() drifts to true for everything, the count collapses
    # and this fires instead of reporting a silent all-clear.
    assert inspected >= 256, f"only {inspected} files reached the bypass scan"
    assert not violations, (
        "Data directory joined directly to an engine root (bypasses the data-root "
        "seam -> misroutes private data into the engine clone). Use the matching "
        "get_*_dir() helper instead:\n  " + "\n  ".join(violations)
    )


def test_no_engine_root_alias_joined_to_data_dir():
    """Cross-line alias guard: catch `root = get_workspace_root()` (line A) then
    `root / "outputs"` (line B). The line-based _BYPASS regex misses this because
    `root` is ambiguous on its own line; this check first learns which local vars
    are bound to an engine-root producer, then flags any join of one to a data dir.
    """
    root = get_workspace_root()
    violations: list[str] = []
    inspected = 0
    for base in _scan_roots():
        if not base.exists():
            continue
        for py, rel in _scan_files(base, root):
            if _is_exempt(rel):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # Union, not replacement. The regex still catches the
            # `os.path.dirname(os.path.dirname(...))` spelling the AST binder
            # deliberately leaves alone, and it keeps working on a file the AST
            # cannot parse. The AST adds the ternary and underscored-constant
            # bindings the regex cannot see. Measured 2026-08-29 over 430 files:
            # 211 vars found by both, 45 regex-only, 66 AST-only, and zero new
            # violations once a caller-supplied `workspace_root` was excluded
            # from the AST producers.
            found, checked = _bound_var_violations(text, rel)
            violations.extend(found)
            inspected += checked
    # Survivor floor: counts engine-bound vars that actually reached the per-line
    # regex checks. Measured 258 on 2026-08-26; floored well below so retiring a
    # script does not fail this test. If _is_exempt() drifts to true for every file,
    # or _ENGINE_BIND_RE stops matching any binding, the count collapses to zero and
    # this fires instead of reporting a silent all-clear.
    assert inspected >= 154, f"only {inspected} engine-bound vars were checked"
    assert not violations, (
        "Engine-root variable joined to a data dir (cross-line seam bypass). Bind the "
        "data path from the matching get_*_dir() helper instead:\n  " + "\n  ".join(violations)
    )


def test_a_nested_checkout_is_skipped_and_its_twin_in_the_tree_is_not(tmp_path):
    """Both halves of the nested-checkout skip, so it cannot widen into a hole.

    Half one: an identical bypass planted inside a nested git checkout is
    skipped. Half two: the same bypass planted in ordinary scan scope is still
    found. Delete the skip in `_scan_files` and the first assertion fails;
    widen it to swallow the ordinary file and the second does.
    """
    bypass = 'x = get_workspace_root() / "outputs" / name\n'

    ordinary = tmp_path / "scripts" / "real.py"
    ordinary.parent.mkdir(parents=True)
    ordinary.write_text(bypass, encoding="utf-8")

    nested = tmp_path / "scripts" / "wt" / "copy.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(bypass, encoding="utf-8")
    # What `git worktree add` leaves behind: a FILE named .git, not a directory.
    (tmp_path / "scripts" / "wt" / ".git").write_text(
        "gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")

    scanned = {rel for _, rel in _scan_files(tmp_path / "scripts", tmp_path)}

    assert "scripts/wt/copy.py" not in scanned
    assert "scripts/real.py" in scanned


def test_engine_root_alias_regex_detects_synthetic_bypass():
    """Positive regression for the guard's own detection logic: a crafted
    engine-root-alias bypass must be caught by _ENGINE_BIND_RE + the data-dir
    pattern. Keeps the guard itself regression-proof (no file I/O)."""
    snippet = "r = get_workspace_root()\n...\nx = r / 'outputs' / name\n"
    engine_vars = set(_ENGINE_BIND_RE.findall(snippet))
    assert "r" in engine_vars
    op = re.compile(r"\br\s*(?:/|\+)\s*[\"']" + _DATA_DIRS + r"\b")
    assert any(op.search(line) for line in snippet.splitlines())
    # And a data-root-bound var must NOT be collected as an engine producer.
    clean = "r = get_data_root()\nx = r / 'outputs'\n"
    assert "r" not in set(_ENGINE_BIND_RE.findall(clean))


def test_file_parent_idiom_detected_as_engine_producer():
    """Positive regression for finding #3 (2026-06-16): the bare __file__-parent
    idiom must be collected as an engine-root producer, and the `Path(VAR) /
    "datadir"` wrapper form must be caught by the op regex."""
    # os.path.dirname(os.path.dirname(...)) idiom
    snippet1 = (
        "BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "OUTPUT = str(Path(BASE) / 'outputs' / 'x.docx')\n"
    )
    vars1 = set(_ENGINE_BIND_RE.findall(snippet1))
    assert "BASE" in vars1
    op = re.compile(r"\bBASE\b\)?\s*(?:/|\+)\s*[\"']" + _DATA_DIRS + r"\b")
    assert any(op.search(line) for line in snippet1.splitlines()), (
        "Path(BASE) / 'outputs' wrapper form must match the op regex"
    )

    # Path(__file__).resolve().parent.parent idiom, direct join
    snippet2 = (
        "base = Path(__file__).resolve().parent.parent\n"
        "p = str(base / 'outputs' / 'doc.md')\n"
    )
    vars2 = set(_ENGINE_BIND_RE.findall(snippet2))
    assert "base" in vars2
    op2 = re.compile(r"\bbase\b\)?\s*(?:/|\+)\s*[\"']" + _DATA_DIRS + r"\b")
    assert any(op2.search(line) for line in snippet2.splitlines())


def test_every_data_dir_this_guard_names_is_actually_caught():
    """One positive case per directory in `_DATA_DIRS`, in every join form.

    Written out rather than derived from `_DATA_DIRS`, deliberately. A test
    parametrized over the very alternation the defect shrinks deletes its own
    coverage: drop `auto-memory` from the pattern and a derived case list drops
    the `auto-memory` case with it, and the run stays green over a guard that
    stopped looking. This list has to be edited by hand when the pattern is,
    which is the point.
    """
    expected = ["threads", "crm", "outputs", "knowledge", "plans", "datastore",
                "context", "auto-memory", "chronicle", "admin", "personal"]
    missed = []
    for name in expected:
        if not _BYPASS.search(f'p = get_workspace_root() / "{name}" / "x.md"'):
            missed.append(f"{name} (operator form)")
        if not _BYPASS_JOIN.search(f'p = os.path.join(WORKSPACE_ROOT, "{name}", "x.md")'):
            missed.append(f"{name} (os.path.join form)")
        if not _BYPASS_JOINPATH.search(f'p = get_workspace_root().joinpath("{name}")'):
            missed.append(f"{name} (joinpath form)")
        if not _BYPASS_FSTRING.search(f'p = f"{{get_workspace_root()}}/{name}/x.md"'):
            missed.append(f"{name} (f-string form)")
    assert not missed, (
        "these data directories are named in _DATA_DIRS but a bypass spelled "
        "with them is not detected:\n  " + "\n  ".join(missed))

    # And the other direction, so this is not a pattern that matches everything:
    # an ENGINE directory joined to an engine root is ordinary correct code.
    for engine_dir in ("scripts", "config", "reference", "docs", "tests"):
        assert not _BYPASS.search(f'p = get_workspace_root() / "{engine_dir}"'), engine_dir


def test_joinpath_and_fstring_direct_producer_forms_detected():
    """Positive regression (2026-06-28 sweep): the line-based guard must catch the
    joinpath() and f-string forms of a direct-producer bypass, which the operator
    and os.path.join regexes miss."""
    assert _BYPASS_JOINPATH.search('m = get_workspace_root().joinpath("outputs")')
    assert _BYPASS_FSTRING.search('p = f"{get_workspace_root()}/outputs/x.json"')
    # __file__-parent idiom in both forms
    assert _BYPASS_JOINPATH.search('Path(__file__).resolve().parent.parent.joinpath("crm")')
    # A benign INJECTED param (not a producer expr) must NOT match the line-based
    # joinpath guard -- this is dead_letter.py's real shape.
    assert not _BYPASS_JOINPATH.search('outputs = workspace_root.joinpath("outputs")')
    # ...but a data-dir literal that isn't ours stays clean.
    assert not _BYPASS_JOINPATH.search('x = get_workspace_root().joinpath("scripts")')


def test_joinpath_and_fstring_alias_forms_detected_binding_aware():
    """Cross-line: an engine-bound alias var used via joinpath()/f-string must be
    caught, while the same var name used as a benign param default must not be
    collected as engine-bound in the first place."""
    snippet = "root = get_workspace_root()\nm = root.joinpath('outputs')\np = f'{root}/threads/t.md'\n"
    engine_vars = set(_ENGINE_BIND_RE.findall(snippet))
    assert "root" in engine_vars
    jp = re.compile(r"\broot\b\.joinpath\(\s*[\"']" + _DATA_DIRS + r"\b")
    fs = re.compile(r"\{[^{}]*\broot\b[^{}]*\}/" + _DATA_DIRS + r"\b")
    assert any(jp.search(line) for line in snippet.splitlines())
    assert any(fs.search(line) for line in snippet.splitlines())
    # Param default (= None) is not a producer -> var not collected -> joinpath safe.
    param = "def f(workspace_root=None):\n    o = workspace_root.joinpath('outputs')\n"
    assert "workspace_root" not in set(_ENGINE_BIND_RE.findall(param))


# ============================================================
# The AST binder, pinned against the shapes it exists for
# ============================================================
#
# Added 2026-08-29 after `scripts/utils/crm.py` joined a data directory to a
# module constant behind a ternary and the regex binder reported nothing. These
# run on literal snippets, never on the tree, so the guard stays regression-proof
# without file I/O.

def test_the_ast_binder_sees_a_producer_behind_a_ternary():
    """The exact line that got through, verbatim."""
    src = ('_ws_root = Path(workspace_root) if workspace_root else _WORKSPACE_ROOT\n'
           '_stages = parse_pipeline_stages(_ws_root / "context" / "pipeline.md")\n')
    assert "_ws_root" in _engine_bound_vars_ast(src)
    # And the reason a widened regex was not enough: the shipped one is blind to
    # it, so the AST binder is carrying this case alone.
    assert "_ws_root" not in set(_ENGINE_BIND_RE.findall(src))


def test_the_ast_binder_sees_an_underscored_module_constant():
    """`WORKSPACE_ROOT` cannot match starting at the `_` of `_WORKSPACE_ROOT`."""
    assert "p" in _engine_bound_vars_ast("p = _WORKSPACE_ROOT\n")
    assert "p" in _engine_bound_vars_ast("p = __WORKSPACE_ROOT\n")


def test_the_ast_binder_sees_the_file_parent_idiom_in_either_spelling():
    for src in ("BASE = Path(__file__).resolve().parent.parent\n",
                "BASE = Path(__file__).parent.parent\n",
                "BASE = override if override else Path(__file__).resolve().parent.parent\n"):
        assert "BASE" in _engine_bound_vars_ast(src), src


def test_the_ast_binder_spares_a_caller_supplied_root():
    """A parameter is the caller's choice, which is the sanctioned override.

    Three real call sites follow exactly this pattern and must stay clean:
    `crm_autolog._address_book_dir`, `crm_autolog._contacts_dir`, and
    `aggregate-crm.py`, where the root is another executive's clone. An earlier
    version of this binder flagged all three.
    """
    assert _engine_bound_vars_ast("ws = Path(workspace_root)\n") == set()
    assert _engine_bound_vars_ast("repo_path = clones[slug]\n") == set()
    assert _engine_bound_vars_ast("r = get_data_root()\n") == set()


def test_the_ast_binder_spares_someone_elses_parent_chain():
    """Shape alone is not enough; the argument decides whose tree it is."""
    assert _engine_bound_vars_ast("B = Path(other).resolve().parent.parent\n") == set()
    # One parent is `scripts/`, not the repository root.
    assert _engine_bound_vars_ast("B = Path(__file__).resolve().parent\n") == set()


def test_the_ast_binder_degrades_to_the_regex_on_unparseable_source():
    """A syntax error must narrow the claim, never fail the suite.

    `.claude/` carries hook scripts that are edited by hand; one broken file
    must not turn this guard into a red run about something it does not govern.
    """
    assert _engine_bound_vars_ast("def (:\n") == set()


def test_the_ast_binder_is_not_matching_everything():
    """Anti-vacuity from the other side: a binder that returned every name would
    pass every test above and flood the tree with false positives."""
    src = ("a = 1\nb = get_data_root()\nc = some_call(x, y)\n"
           "d = {'k': 'v'}\ne = [i for i in range(3)]\n")
    assert _engine_bound_vars_ast(src) == set()


def test_the_ast_binder_sees_an_annotated_assignment():
    """`x: Path = _WORKSPACE_ROOT` is an `AnnAssign`, a different node type.

    Missed by the first version of the binder, and mutation-caught: skipping
    `AnnAssign` changed nothing until this case existed.
    """
    assert "p" in _engine_bound_vars_ast("p: Path = _WORKSPACE_ROOT\n")
    assert "p" in _engine_bound_vars_ast(
        "p: Path = override if override else _WORKSPACE_ROOT\n")
    assert _engine_bound_vars_ast("p: Path\n") == set()  # no value, no binding


def test_the_scan_flags_a_ternary_bound_var_end_to_end():
    """The wiring, not just the binder.

    This drives the SAME function the tree scan calls, on a file that really
    does contain the defect. The tree itself is clean, so without this case
    unwiring the AST binder from the scan changed nothing and the mutation
    survived: green over an empty corpus is not evidence.
    """
    src = ('_ws_root = Path(workspace_root) if workspace_root else _WORKSPACE_ROOT\n'
           '_stages = parse_pipeline_stages(_ws_root / "context" / "pipeline.md")\n')
    violations, checked = _bound_var_violations(src, "scripts/utils/crm.py")

    assert checked >= 1, "no engine-bound var reached the per-line check"
    assert len(violations) == 1, violations
    assert "crm.py:2" in violations[0]
    assert "_ws_root is engine-root-bound" in violations[0]


def test_the_scan_spares_the_fixed_form():
    """The shape that replaced it must be clean, or the guard blocks its own fix."""
    src = ('    if workspace_root:\n'
           '        _ws_root = Path(workspace_root)\n'
           '        _pipeline_file = _ws_root / "context" / "pipeline.md"\n'
           '    else:\n'
           '        _pipeline_file = get_context_dir() / "pipeline.md"\n')
    violations, _ = _bound_var_violations(src, "scripts/utils/crm.py")
    assert violations == [], violations
