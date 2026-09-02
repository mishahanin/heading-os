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
from collections import namedtuple
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES = ROOT / "scripts" / "bridge_daemon" / "sources"

sys.path.insert(0, str(ROOT))
from scripts.bridge_daemon._safepath import contains_symlink  # noqa: E402
from tests.repo_files import read_sources  # noqa: E402


def _read_or_fail(path: Path) -> str:
    """Read a path a COMPLETENESS claim depends on, retrying once first.

    Skipping is the right answer for a scan - a file that vanished cannot
    violate anything. It is the wrong answer where the corpus IS the claim:
    dropping one file under `sources/` would delete its guarded functions from
    a set that is compared for exact equality against `REFUSAL_CASES`, and the
    failure would then name the registry rather than the race. One retry
    absorbs the mid-walk window; a file that is still gone fails by name.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise AssertionError(
                f"{path} vanished between the walk and the read and was still "
                f"gone one retry later. This scan claims to name EXACTLY the "
                f"guarded functions under sources/, so a skipped file would "
                f"report a wrong set as fact."
            ) from exc


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


def _guard_bindings(vanished: list | None = None) -> list[tuple[str, int, str, str | None]]:
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
    # A SCAN: this collects offending guards, and a file that disappeared between
    # the glob and the read carries no guard to offend with. `read_sources` skips
    # it and warns by name, so the narrowing is visible instead of silent; the
    # count floor below carries the same number into its message.
    for path, source in read_sources(sorted(SOURCES.glob("*.py")), vanished):
        tree = ast.parse(source)
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
    vanished: list[Path] = []
    sites = _guard_bindings(vanished)
    assert len(sites) >= 5, (
        f"only {len(sites)} symlink guard(s) found under sources/ "
        f"({len(vanished)} file(s) vanished mid-walk), which is "
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
    # A SCAN: a file that vanished between the glob and the read makes no promise
    # to break, so skipping it is the correct answer. `read_sources` names what it
    # skipped in a warning, and the floor below reports the same count, so the
    # corpus cannot shrink underneath the verdict without saying so.
    vanished: list[Path] = []
    for path, src in read_sources(paths, vanished):
        if "symlinks not allowed" not in src:
            continue
        promising += 1
        if "contains_symlink(" not in src:
            missing.append(path.name)
    # An empty `missing` list is green over zero readers, so a renamed package,
    # a moved sources/ directory, or a changed suffix would turn this check off
    # without failing anything. 8 of the 19 files under sources/ carried the
    # "symlinks not allowed" string on 2026-08-26.
    assert promising >= 5, (
        f"the scan collapsed to {promising} files "
        f"({len(vanished)} vanished mid-walk)")
    assert not missing, (
        "these files still return 'symlinks not allowed' from a check that "
        "cannot reach it: " + ", ".join(missing)
    )


def test_the_promise_is_written_where_the_guard_runs():
    """Four readers advertise 'No symlinks' in their validation list. Pin that
    those files are the ones carrying the live guard."""
    promising = []
    # A SCAN: a file that vanished mid-walk documents no promise, so skipping it
    # is correct and `read_sources` warns by name. The source text is carried
    # forward rather than re-read below, so the same corpus is not raced twice.
    vanished: list[Path] = []
    for path, src in read_sources(sorted(SOURCES.glob("*.py")), vanished):
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            doc = ast.get_docstring(node) or ""
            if re.search(r"No symlinks", doc, re.IGNORECASE):
                promising.append((path.name, node.name, src))
    assert promising, (
        f"the documented promise vanished; retarget this test "
        f"({len(vanished)} file(s) vanished mid-walk)")
    for fname, func, src in promising:
        assert "contains_symlink(" in src, (
            f"{fname}:{func} documents 'No symlinks' and the file has no live "
            "guard behind it"
        )


# =============================================================================
# EVERY guarded reader refuses, and the list of them is derived (2026-08-31)
# =============================================================================
#
# Until today this file was named for a claim it established on two readers out
# of thirteen. `read_skill` and `read_one_contact` had a real refusal case; the
# other eleven had only the two checks above, and both of those ask about SOURCE
# TEXT: does the file contain `contains_symlink(`, and was the argument already
# `.resolve()`d. Neither can see a guard that is present, unresolved, and unable
# to fire.
#
# Measured by neutering one of the eleven without touching either thing those
# checks look at:
#
#     scripts/bridge_daemon/sources/library.py
#     -        if contains_symlink(data_root / "knowledge", target_raw):
#     +        if False and contains_symlink(data_root / "knowledge", target_raw):
#
#     tests/bridge tests/inbox_pulse tests/contract
#       + tests/test_a_list_scan_that_published_what_its_drilldown_refused.py
#         ->  1716 passed, 1 skipped
#
# Byte-identical to the baseline. `read_note` now serves a symlinked note, the
# string `contains_symlink(` is still in the file, the AST still finds the call
# with an unresolved argument, and the file whose whole subject is "a documented
# control that cannot fire is worse than no control" says nothing.
#
# `test_every_reader_that_promises_no_symlinks_calls_the_live_guard` could not
# have seen it either, for a second reason worth stating: its floor is
# `promising >= 5` over eight files, so deleting a reader's guard AND its
# "symlinks not allowed" string together drops the count to seven and passes.
#
# The registry below is checked against the AST both ways, so a new guarded
# function fails here until someone writes its refusal case, and an entry whose
# guard was removed fails too rather than sitting on as decoration.

Verdict = namedtuple("Verdict", "served refused detail")


def _skills_tree(tmp_path: Path) -> Path:
    root = tmp_path / ".claude" / "skills"
    (root / "real").mkdir(parents=True)
    (root / "real" / "SKILL.md").write_text("---\nname: real\n---\nbody\n",
                                            encoding="utf-8")
    (root / "clone").mkdir()
    os.symlink(root / "real" / "SKILL.md", root / "clone" / "SKILL.md")
    return tmp_path


def _case_list_capabilities(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.capabilities import list_capabilities
    got = list_capabilities(_skills_tree(tmp_path))
    slugs = {s["slug"] for s in got["skills"]}
    return Verdict("real" in slugs, "clone" not in slugs, slugs)


def _case_read_skill(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.capabilities import read_skill
    root = _skills_tree(tmp_path)
    return _dict_verdict(read_skill(root, "real"), read_skill(root, "clone"))


def _contacts_tree(tmp_path: Path) -> Path:
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "real-person.md").write_text("# Real Person\n", encoding="utf-8")
    os.symlink(contacts / "real-person.md", contacts / "shadow-person.md")
    return tmp_path


def _case_read_one_contact(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.contacts import read_one_contact
    root = _contacts_tree(tmp_path)
    return _dict_verdict(
        read_one_contact(root, "ceo", "real-person", data_root=root),
        read_one_contact(root, "ceo", "shadow-person", data_root=root))


def _case_read_contact(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.tribe import read_contact
    root = _contacts_tree(tmp_path)
    return _dict_verdict(read_contact(root, "real-person"),
                         read_contact(root, "shadow-person"))


def _drafts_tree(tmp_path: Path) -> tuple[Path, str, str]:
    from scripts.bridge_daemon.sources.approvals import EMAIL_DRAFTS_DIR
    drafts = tmp_path / EMAIL_DRAFTS_DIR
    drafts.mkdir(parents=True)
    (drafts / "real.md").write_text("**To:** a@b.test\n**Subject:** x\n\nbody\n",
                                    encoding="utf-8")
    os.symlink(drafts / "real.md", drafts / "planted.md")
    return tmp_path, f"{EMAIL_DRAFTS_DIR}/real.md", f"{EMAIL_DRAFTS_DIR}/planted.md"


def _case_list_approvals(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.approvals import list_approvals
    root, _, _ = _drafts_tree(tmp_path)
    names = {i["filename"] for i in list_approvals(root)["items"]}
    return Verdict("real.md" in names, "planted.md" not in names, names)


def _case_read_draft(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.approvals import read_draft
    root, honest, linked = _drafts_tree(tmp_path)
    return _dict_verdict(read_draft(root, honest), read_draft(root, linked))


def _case_read_note(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.library import read_note
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "real.md").write_text("# note\n", encoding="utf-8")
    os.symlink(knowledge / "real.md", knowledge / "link.md")
    return _dict_verdict(read_note(tmp_path, "knowledge/real.md"),
                         read_note(tmp_path, "knowledge/link.md"))


def _case_read_thread(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.threads import (
        THREADS_BUSINESS_DIR,
        read_thread,
    )
    d = tmp_path / THREADS_BUSINESS_DIR
    d.mkdir(parents=True)
    (d / "real.md").write_text("# thread\n", encoding="utf-8")
    os.symlink(d / "real.md", d / "link.md")
    return _dict_verdict(read_thread(tmp_path, f"{THREADS_BUSINESS_DIR}/real.md"),
                         read_thread(tmp_path, f"{THREADS_BUSINESS_DIR}/link.md"))


def _case_list_active_threads(tmp_path: Path) -> Verdict:
    """The LISTING half of the same files `read_thread` guards.

    Found by the 2026-08-24 campaign (shard `scripts-02-p2`, finding 4): the
    listing had no guard at all, so a link planted in `threads/business/`
    published its title and frontmatter through `/threads` while the detail
    view answered "symlinks not allowed" for the same row. Both threads here
    carry active frontmatter, so the only difference between them is the link.
    """
    from scripts.bridge_daemon.sources.threads import (
        THREADS_BUSINESS_DIR,
        list_active_threads,
    )
    d = tmp_path / THREADS_BUSINESS_DIR
    d.mkdir(parents=True)
    body = "---\nid: {0}\ntitle: {0}\nstatus: active\ntype: deal\n---\n"
    (d / "real.md").write_text(body.format("real"), encoding="utf-8")
    (d / "planted-target.md").write_text(body.format("planted"), encoding="utf-8")
    os.symlink(d / "planted-target.md", d / "link.md")
    ids = {t["id"] for t in list_active_threads(tmp_path)["threads"]}
    # `planted-target.md` is itself a real file in the served directory and is
    # LISTED; what must not appear is the row the symlink `link.md` would add.
    # Asserting on the id would pass while the link was still being read, so the
    # path is what gets checked.
    paths = {t["path"] for t in list_active_threads(tmp_path)["threads"]}
    return Verdict("real" in ids,
                   f"{THREADS_BUSINESS_DIR}/link.md" not in paths,
                   (ids, paths))


def _case_list_tribe(tmp_path: Path) -> Verdict:
    """The LISTING half of the files `read_contact` guards.

    The same defect as `list_active_threads` above and found the same way, one
    file later: `threads.py` took the fix on 2026-08-31 and `tribe.py` took
    only the `UnicodeDecodeError` half of it, so until 2026-09-02 a link
    planted in `crm/contacts/` was published on `/tribe` with its display name,
    role and frontmatter while clicking the row answered "symlinks not
    allowed". Both contacts here are ordinary tribe rows, so the only
    difference between them is the link.
    """
    from scripts.bridge_daemon.sources.tribe import list_tribe
    d = tmp_path / "crm" / "contacts"
    d.mkdir(parents=True)
    body = "---\nrelationship_type: tribe\nlast_touch: 2026-05-15\n---\n\n# {0}\n"
    (d / "real.md").write_text(body.format("Real Person"), encoding="utf-8")
    (d / "planted-target.md").write_text(body.format("Planted"), encoding="utf-8")
    os.symlink(d / "planted-target.md", d / "link.md")
    slugs = {m["slug"] for m in list_tribe(data_root=tmp_path)["members"]}
    # `planted-target.md` is a real file in the served directory and IS listed;
    # what must not appear is the row `link.md` would add. The slug is derived
    # from the file name, so the link contributes a distinct one.
    return Verdict("real" in slugs, "link" not in slugs, slugs)


def _case_read_dossier(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.investors import PROGRAM_DIR, read_dossier
    d = tmp_path / PROGRAM_DIR
    d.mkdir(parents=True)
    (d / "real.md").write_text("# dossier\n", encoding="utf-8")
    os.symlink(d / "real.md", d / "link.md")
    return _dict_verdict(read_dossier(tmp_path, f"{PROGRAM_DIR}/real.md"),
                         read_dossier(tmp_path, f"{PROGRAM_DIR}/link.md"))


def _case_read_inflight(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.pulse import IN_FLIGHT_DIRS
    from scripts.bridge_daemon.sources.studio import read_inflight
    rel = IN_FLIGHT_DIRS[0]
    d = tmp_path / rel
    d.mkdir(parents=True)
    (d / "real.md").write_text("# in flight\n", encoding="utf-8")
    os.symlink(d / "real.md", d / "link.md")
    return _dict_verdict(read_inflight(tmp_path, f"{rel}/real.md"),
                         read_inflight(tmp_path, f"{rel}/link.md"))


def _archive_tree(tmp_path: Path) -> tuple[Path, Path]:
    """`<archive>/posts/{real,link}` where `link` is a symlink to `real`."""
    from scripts.bridge_daemon.sources.studio import ARTIFACT_ROOT
    posts = tmp_path / ARTIFACT_ROOT / "posts"
    (posts / "real").mkdir(parents=True)
    (posts / "real" / "post.md").write_text("# post\n", encoding="utf-8")
    (posts / "real" / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    os.symlink(posts / "real", posts / "link")
    return tmp_path, posts


def _case_artifact_md_is_readable(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.studio import _artifact_md_is_readable
    _, posts = _archive_tree(tmp_path)
    return Verdict(_artifact_md_is_readable(posts, posts / "real" / "post.md"),
                   _artifact_md_is_readable(posts, posts / "link" / "post.md")
                   is False, None)


def _case_artifact_folder(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.studio import _artifact_folder
    root, _ = _archive_tree(tmp_path)
    return Verdict(_artifact_folder(root, "post", "real") is not None,
                   _artifact_folder(root, "post", "link") is None, None)


def _case_resolve_artifact_image(tmp_path: Path) -> Verdict:
    from scripts.bridge_daemon.sources.studio import (
        ARTIFACT_ROOT,
        resolve_artifact_image,
    )
    root, _ = _archive_tree(tmp_path)
    honest = f"{ARTIFACT_ROOT}/posts/real/cover.png"
    linked = f"{ARTIFACT_ROOT}/posts/link/cover.png"
    return Verdict(resolve_artifact_image(root, honest) is not None,
                   resolve_artifact_image(root, linked) is None, None)


def _dict_verdict(honest: dict, linked: dict) -> Verdict:
    """The `{"ok": ..., "error": ...}` readers, all of which share one wording.

    The refusal REASON is asserted, not just the refusal. Every one of these
    functions has four or five other ways to answer `ok: False` - not found,
    not a file, path escapes, over the byte cap - and a guard that stopped
    running while some earlier check happened to reject the same fixture would
    otherwise read as a pass. That is the straw-man negative case.
    """
    return Verdict(honest.get("ok") is True,
                   linked.get("ok") is False
                   and linked.get("error") == "symlinks not allowed",
                   (honest, linked))


# (module, function) -> the case that makes THAT function refuse.
REFUSAL_CASES = {
    ("approvals", "list_approvals"): _case_list_approvals,
    ("approvals", "read_draft"): _case_read_draft,
    ("capabilities", "list_capabilities"): _case_list_capabilities,
    ("capabilities", "read_skill"): _case_read_skill,
    ("contacts", "read_one_contact"): _case_read_one_contact,
    ("investors", "read_dossier"): _case_read_dossier,
    ("library", "read_note"): _case_read_note,
    ("studio", "read_inflight"): _case_read_inflight,
    ("studio", "_artifact_md_is_readable"): _case_artifact_md_is_readable,
    ("studio", "_artifact_folder"): _case_artifact_folder,
    ("studio", "resolve_artifact_image"): _case_resolve_artifact_image,
    ("threads", "list_active_threads"): _case_list_active_threads,
    ("threads", "read_thread"): _case_read_thread,
    ("tribe", "list_tribe"): _case_list_tribe,
    ("tribe", "read_contact"): _case_read_contact,
}


def _guarded_functions() -> set[tuple[str, str]]:
    """(module, function) for every function under `sources/` that calls the
    live guard in its OWN body. `_own_nodes` excludes nested scopes, so an
    inner helper is credited to itself rather than to the function around it."""
    out: set[tuple[str, str]] = set()
    # NOT a scan: this set is compared for exact equality against REFUSAL_CASES,
    # so a silently dropped file would delete real entries from it and the test
    # would blame the registry for a mid-walk race. `_read_or_fail` retries once
    # and then fails naming the file.
    for path in sorted(SOURCES.glob("*.py")):
        tree = ast.parse(_read_or_fail(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "contains_symlink"
                   for n in _own_nodes(node)):
                out.add((path.stem, node.name))
    return out


def test_the_registry_names_exactly_the_functions_that_carry_a_guard():
    """Both directions, so neither list can quietly stop describing the other.

    Thirteen functions under `sources/` called `contains_symlink` on
    2026-08-31 and two of them had a refusal case. A new guarded reader now
    fails here until its author writes one, and a registry entry whose guard
    was deleted fails too - an entry guarding nothing is the shape this whole
    file is about.
    """
    guarded = _guarded_functions()
    assert len(guarded) >= 10, (
        f"only {len(guarded)} guarded function(s) found under sources/, far "
        f"fewer than this daemon has ever had; retarget the AST scan rather "
        f"than trimming the registry: {sorted(guarded)}")
    missing = sorted(guarded - set(REFUSAL_CASES))
    stale = sorted(set(REFUSAL_CASES) - guarded)
    assert not missing, (
        "these functions call the live symlink guard and no test here ever "
        f"makes them refuse: {missing}")
    assert not stale, (
        "these registry entries name a function that no longer calls the "
        f"guard, so they measure nothing: {stale}")


@pytest.mark.parametrize("key", sorted(REFUSAL_CASES),
                         ids=lambda k: f"{k[0]}.{k[1]}")
def test_the_guarded_reader_actually_refuses_a_symlink(tmp_path, key):
    """The behaviour, one reader at a time.

    Both jaws in every case. `refused` is the control; `served` is the anchor
    that stops a reader which refuses everything - a broken fixture, a renamed
    directory, a guard inverted to `if not contains_symlink` - from reading as
    a pass.
    """
    verdict = REFUSAL_CASES[key](tmp_path)
    assert verdict.served, (
        f"{key[0]}.{key[1]}: the honest, non-symlinked path stopped working, so "
        f"the refusal below proves nothing: {verdict.detail}")
    assert verdict.refused, (
        f"{key[0]}.{key[1]}: a symlink was served. The workspace bans symlinks "
        f"outright and this function's own code asks the question: "
        f"{verdict.detail}")
