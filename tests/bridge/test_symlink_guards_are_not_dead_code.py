"""A documented control that cannot fire is worse than no control.

Found by the 2026-08-23 engine audit (findings 3 and 6, on two files). Measured
across ``scripts/bridge_daemon/sources/`` on 2026-08-24: NINE of the ten
symlink guards were unreachable, all the same way.

    target = (base / rel_path).resolve()
    target.relative_to(base_resolved)      # containment: works
    if target.is_symlink():                # symlink ban: can never be True
        return {"ok": False, "error": "symlinks not allowed"}

``Path.resolve()`` dereferences every link in the path, so the question was
being asked of the file the link POINTS AT. ``library.py`` even carried a
comment reasoning it through and reaching the wrong conclusion: "the resolve()
above already follows symlinks, then our relative_to check would catch any
escape. Still, explicit is good." Explicit, and inert.

This is not a traversal hole -- containment still held, so nothing outside the
served directory was ever reachable. What was lost is the workspace's
no-symlinks-ever policy INSIDE the tree, while four of these readers list
"No symlinks" among their documented validations. The next author budgets for a
control that is not running.

Two guards, then: the behavioural one (a link is refused) and the structural
one (nobody re-binds the check to a resolved path).
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES = ROOT / "scripts" / "bridge_daemon" / "sources"

sys.path.insert(0, str(ROOT))
from scripts.bridge_daemon._safepath import contains_symlink  # noqa: E402


# --- the helper answers the question the guards meant to ask -----------------

def test_a_plain_file_is_not_flagged(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    assert contains_symlink(tmp_path, tmp_path / "a.md") is False


def test_a_symlinked_file_is_flagged(tmp_path):
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    os.symlink(tmp_path / "real.md", tmp_path / "link.md")
    assert contains_symlink(tmp_path, tmp_path / "link.md") is True


def test_a_symlinked_PARENT_is_flagged(tmp_path):
    """The file itself is real; the directory above it is the link. `.resolve()`
    on the target hid this case as thoroughly as the direct one."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "a.md").write_text("x", encoding="utf-8")
    os.symlink(real_dir, tmp_path / "linked_dir")
    assert contains_symlink(tmp_path, tmp_path / "linked_dir" / "a.md") is True


def test_a_link_ABOVE_the_root_is_not_the_bans_business(tmp_path):
    """The workspace may legitimately sit under a linked mount."""
    real_root = tmp_path / "real_root"
    (real_root / "sub").mkdir(parents=True)
    (real_root / "sub" / "a.md").write_text("x", encoding="utf-8")
    os.symlink(real_root, tmp_path / "linked_root")
    root = tmp_path / "linked_root"
    assert contains_symlink(root, root / "sub" / "a.md") is False


def test_a_target_outside_the_root_is_refused(tmp_path):
    assert contains_symlink(tmp_path / "inside", tmp_path / "elsewhere" / "a.md") is True


# --- the readers actually refuse ---------------------------------------------

def test_read_skill_refuses_a_symlinked_skill_md(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    (skills / "real").mkdir(parents=True)
    (skills / "real" / "SKILL.md").write_text("---\nname: real\n---\nbody\n",
                                              encoding="utf-8")
    (skills / "clone").mkdir()
    os.symlink(skills / "real" / "SKILL.md", skills / "clone" / "SKILL.md")

    from scripts.bridge_daemon.sources.capabilities import read_skill
    assert read_skill(tmp_path, "real")["ok"] is True, "the honest path broke"
    got = read_skill(tmp_path, "clone")
    assert got["ok"] is False and got["error"] == "symlinks not allowed", got


def test_read_one_contact_refuses_a_symlinked_contact(tmp_path):
    data_root = tmp_path / "data"
    contacts = data_root / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "real-person.md").write_text("# Real Person\n", encoding="utf-8")
    os.symlink(contacts / "real-person.md", contacts / "shadow-person.md")

    from scripts.bridge_daemon.sources.contacts import read_one_contact
    ok = read_one_contact(tmp_path, "ceo", "real-person", data_root=data_root)
    assert ok["ok"] is True, ok
    got = read_one_contact(tmp_path, "ceo", "shadow-person", data_root=data_root)
    assert got["ok"] is False and got["error"] == "symlinks not allowed", got


# --- nobody rebinds the check to a resolved path -----------------------------

def _own_nodes(scope) -> list[ast.AST]:
    """Every node belonging to `scope` itself, nested scopes excluded.

    A nested `def` handles its own nodes, so an inner `target = ...` can never
    be read as the binding for an outer guard, and vice versa.
    """
    out: list[ast.AST] = []
    stack = list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _assignments(scope) -> list[tuple[str, int, ast.AST]]:
    """(variable, lineno, value node) for every binding inside one scope."""
    found = []
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.append((target.id, node.lineno, node.value))
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and node.value is not None) or (
                isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name)):
            found.append((node.target.id, node.lineno, node.value))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    found.append((item.optional_vars.id, node.lineno, item.context_expr))
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
            found.append((node.target.id, node.lineno, node.iter))
    return found


def _dereferences(node: ast.AST) -> bool:
    """True when this value expression calls `.resolve()` anywhere inside it."""
    return any(isinstance(n, ast.Attribute) and n.attr == "resolve"
               for n in ast.walk(node))


def _describe(value, lineno, bindings, label, params) -> tuple[str, str | None]:
    """Report the expression a guard is applied to, resolve-flagged.

    A module-level function rather than a closure over the per-file loop: a
    nested `def` capturing `module_bindings` binds the NAME, not the value, so
    it would read whichever file the loop had reached last (ruff B023).
    """
    if isinstance(value, ast.Name):
        candidates = [b for b in bindings if b[0] == value.id and b[1] <= lineno]
        if not candidates:
            # A parameter is a real, decidable answer: nothing in THIS function
            # resolved it, and what the caller passed is the caller's contract.
            # `None` is reserved for "could not tell", which must never be
            # filtered away as clean.
            if value.id in params:
                return label, f"<parameter {value.id}>"
            return label, None
        _, _, bound = max(candidates, key=lambda b: b[1])
    else:
        bound = value            # an inline expression binds itself
    return label, ast.unparse(bound) + (_RESOLVED if _dereferences(bound) else "")


_RESOLVED = "  # RESOLVED before the guard"


def _guard_bindings() -> list[tuple[str, int, str, str | None]]:
    """(file, line, what is guarded, the expression that bound it), per guard.

    Two things changed here, and the second is the bigger one.

    FIRST, the extraction was two regexes over raw lines:
    `(\\w+)\\.is_symlink\\(\\)` to find a guard, then `\\s*VAR\\s*=[^=]`
    scanning up to 60 lines back for a SINGLE-LINE, unannotated assignment,
    with `.resolve()` required on that same physical line. Every one of these
    reintroduced the dead-guard pattern and passed:

        target = (
            base / rel_path
        ).resolve()                     # binding line is `target = (`
        target: Path = (base / rel).resolve()   # annotated: no match at all
        # ...and any binding more than 60 lines above the guard

    Worse, an unparsed binding came back as `None`, and the consumer's
    `if binding and ".resolve()" in binding` treated `None` as CLEAN. The one
    answer the regex could produce that meant "I could not tell" was read as
    "nothing wrong". `test_every_guard_binding_was_actually_resolved` now
    refuses that silence.

    SECOND, the detector was pointed at a shape that no longer exists.
    `is_symlink()` appears exactly twice under `sources/` today, and BOTH are
    inside comments: studio.py's "It was `md.is_symlink()`" (past tense) and
    library.py's note about Windows junctions. A line regex cannot tell code
    from prose, so `test_the_detector_still_finds_a_guard` was satisfied by
    two pieces of documentation while the real guards went unexamined. Its own
    failure message asked for exactly this: "if the guards were replaced
    wholesale, retarget this detector rather than deleting it."

    The live guard is `contains_symlink(root, target)`, whose docstring states
    the invariant the original defect broke: "Both paths are taken UNRESOLVED
    -- passing a ``.resolve()``d target is the original bug." So the second
    argument of every call site is what gets checked, plus any surviving
    `X.is_symlink()` in real code.
    """
    out: list[tuple[str, int, str, str | None]] = []
    for path in sorted(SOURCES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes = [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        module_bindings = _assignments(tree)
        for scope in scopes:
            scope_bindings = _assignments(scope)
            args = getattr(scope, "args", None)
            params = set()
            if args is not None:
                for group in (args.posonlyargs, args.args, args.kwonlyargs):
                    params.update(a.arg for a in group)
                for extra in (args.vararg, args.kwarg):
                    if extra is not None:
                        params.add(extra.arg)
            for node in _own_nodes(scope):
                if not isinstance(node, ast.Call):
                    continue
                visible = scope_bindings + module_bindings
                if isinstance(node.func, ast.Name) and node.func.id == "contains_symlink" \
                        and len(node.args) >= 2:
                    label, binding = _describe(
                        node.args[1], node.lineno, visible,
                        f"contains_symlink(..., {ast.unparse(node.args[1])})", params)
                elif isinstance(node.func, ast.Attribute) and node.func.attr == "is_symlink" \
                        and isinstance(node.func.value, ast.Name):
                    label, binding = _describe(
                        node.func.value, node.lineno, visible,
                        f"{node.func.value.id}.is_symlink()", params)
                else:
                    continue
                out.append((path.name, node.lineno, label, binding))
    return out


def test_every_guard_binding_was_actually_resolved():
    """"I could not find the binding" must not read as "the binding is clean".

    The old line-regex returned `None` for every shape it could not parse, and
    the dead-guard consumer filtered `None` out as fine. So the exact cases
    most likely to hide a defect were the ones reported as healthy.
    """
    unknown = [f"{f}:{n}  {what}"
               for f, n, what, binding in _guard_bindings() if binding is None]
    assert not unknown, (
        "the argument these symlink guards are applied to could not be traced "
        "to a binding, so whether it was already resolved is UNKNOWN, not "
        "clean:\n  " + "\n  ".join(unknown))


def test_the_detector_still_finds_a_guard():
    """A scan that matches nothing passes everything.

    The floor is a COUNT, not truthiness: the previous version was satisfied
    by two matches that were both comments, so "the detector found something"
    and "the detector found a guard" were not the same statement.
    """
    sites = _guard_bindings()
    assert len(sites) >= 5, (
        f"only {len(sites)} symlink guard(s) found under sources/, which is "
        f"fewer than this daemon has ever had; if the guards were replaced "
        f"wholesale, retarget this detector rather than deleting it: {sites}"
    )


def test_no_symlink_check_is_asked_of_a_resolved_path():
    # `binding is None` is handled by test_every_guard_binding_was_actually_
    # resolved, which fails on it rather than filtering it out as clean.
    dead = [f"{f}:{n}  {what}  <- {binding}"
            for f, n, what, binding in _guard_bindings()
            if binding and binding.endswith(_RESOLVED)]
    assert not dead, (
        "these guards test a path that resolve() already dereferenced, so they "
        "can never fire; use _safepath.contains_symlink on the UNRESOLVED "
        "path:\n  " + "\n  ".join(dead)
    )


def test_every_reader_that_promises_no_symlinks_calls_the_live_guard():
    """The docstring and the code must agree in the same file."""
    paths = sorted(SOURCES.glob("*.py"))
    missing = []
    promising = 0
    for path in paths:
        src = path.read_text(encoding="utf-8")
        if "symlinks not allowed" not in src:
            continue
        promising += 1
        if "contains_symlink(" not in src:
            missing.append(path.name)
    # An empty `missing` list is green over zero readers, so a renamed package,
    # a moved sources/ directory, or a changed suffix would turn this check off
    # without failing anything. 8 of the 19 files under sources/ carried the
    # "symlinks not allowed" string on 2026-08-26.
    assert promising >= 5, f"the scan collapsed to {promising} files"
    assert not missing, (
        "these files still return 'symlinks not allowed' from a check that "
        "cannot reach it: " + ", ".join(missing)
    )


def test_the_promise_is_written_where_the_guard_runs():
    """Four readers advertise 'No symlinks' in their validation list. Pin that
    those files are the ones carrying the live guard."""
    promising = []
    for path in sorted(SOURCES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            doc = ast.get_docstring(node) or ""
            if re.search(r"No symlinks", doc, re.IGNORECASE):
                promising.append((path.name, node.name))
    assert promising, "the documented promise vanished; retarget this test"
    for fname, func in promising:
        src = (SOURCES / fname).read_text(encoding="utf-8")
        assert "contains_symlink(" in src, (
            f"{fname}:{func} documents 'No symlinks' and the file has no live "
            "guard behind it"
        )
