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
_DATA_DIRS = r"(?:threads|crm|outputs|knowledge|plans|datastore|context)"
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


def _scan_roots() -> list[Path]:
    root = get_workspace_root()
    return [root / "scripts", root / ".claude"]


def _is_exempt(rel: str) -> bool:
    return any(s in rel for s in _EXEMPT_SUBSTRINGS)


def test_no_data_dir_joined_to_engine_root():
    root = get_workspace_root()
    violations: list[str] = []
    for base in _scan_roots():
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            rel = py.relative_to(root).as_posix()
            if _is_exempt(rel):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if (_BYPASS.search(line) or _BYPASS_JOIN.search(line)
                        or _BYPASS_JOINPATH.search(line) or _BYPASS_FSTRING.search(line)):
                    violations.append(f"{rel}:{i}: {line.strip()}")
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
    for base in _scan_roots():
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            rel = py.relative_to(root).as_posix()
            if _is_exempt(rel):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            engine_vars = set(_ENGINE_BIND_RE.findall(text))
            engine_vars.discard("")  # safety
            for v in engine_vars:
                # \)? after the var name catches the `Path(BASE) / "outputs"`
                # wrapper form, not just the bare `BASE / "outputs"`.
                op = re.compile(r"\b" + re.escape(v) + r"\b\)?\s*(?:/|\+)\s*[\"']" + _DATA_DIRS + r"\b")
                join = re.compile(
                    r"os\.path\.join\([^)]*\b" + re.escape(v) + r"\s*,\s*[\"']" + _DATA_DIRS + r"\b"
                )
                # v is PROVEN engine-bound here, so joinpath/f-string forms of the
                # same bypass are unambiguous -- no benign-param risk like the
                # line-based test guards against.
                jp = re.compile(r"\b" + re.escape(v) + r"\b\.joinpath\(\s*[\"']" + _DATA_DIRS + r"\b")
                fs = re.compile(r"\{[^{}]*\b" + re.escape(v) + r"\b[^{}]*\}/" + _DATA_DIRS + r"\b")
                for i, line in enumerate(text.splitlines(), 1):
                    if op.search(line) or join.search(line) or jp.search(line) or fs.search(line):
                        violations.append(f"{rel}:{i}: ({v} is engine-root-bound) {line.strip()}")
    assert not violations, (
        "Engine-root variable joined to a data dir (cross-line seam bypass). Bind the "
        "data path from the matching get_*_dir() helper instead:\n  " + "\n  ".join(violations)
    )


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
