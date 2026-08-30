#!/usr/bin/env python3
"""The CEO-only threads wall asked how a path was TYPED, not what it opens.

`check_protect_personal_threads` in `.claude/hooks/_dispatch.py` matched a raw
argument against a literal two-segment pattern. A dot segment, a doubled
separator and a dot-dot segment all change the spelling of a path and none of
them change the file, so one CEO-only note had two answers depending on how it
was written. MEASURED 2026-08-29 by driving the real hook, 7 of 9 spellings that
name one file were allowed (the prose said "4 of 9" until 2026-08-30 while the
table below it listed seven; the table is the measurement and two BLOCK rows plus
seven ALLOW rows is the nine):

    BLOCK  Read canonical              ALLOW  Read with a dot segment
    BLOCK  Bash cat canonical          ALLOW  Read with a doubled separator
                                       ALLOW  Read with a dot-dot segment
                                       ALLOW  Bash cat with a dot segment
                                       ALLOW  Bash cat with a dot-dot segment
                                       ALLOW  Grep path with a dot-dot segment
                                       ALLOW  Write citing the dot form

The second hook completes the bypass rather than catching it:
`data-path-redirect.py` normalises those same spellings and rewrites the call
onto the real CEO-only file under the data root.

The collapse was already written and already correct. `data-path-redirect.py`
carried a private `_normalize_rel`, fixed on 2026-08-23 after a dot-dot path was
found being concatenated onto the data root. The fix landed in one of the two
hooks that needed it. It now lives once, in `scripts/utils/pathnorm.py`, and
both import it.

The same wall had a second face. Its wildcard branch anchored only on a literal
`threads` segment, so a sweep rooted one directory ABOVE the threads tree
carried no such segment and was allowed. MEASURED the same day, 3 of 4:

    BLOCK  Grep rooted at the threads dir   ALLOW  Grep rooted at the data root
                                            ALLOW  Glob '**/*.md' at the data root
                                            ALLOW  Grep two levels above

That branch justified its unanchored carve-out with "an unanchored sweep stays
inside the engine clone, which holds no threads at all". The premise is now
checked instead of assumed: the question is whether the resolved search root is
an ancestor of a threads directory that exists.
"""
from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.pathnorm import (  # noqa: E402
    normalize_path,
    normalize_rel,
    normalize_segments,
)
from tests.repo_files import tracked_python_files  # noqa: E402

# The dispatcher writes a rate-limit counter, and it REFUSES once a window fills.
# A single scratch file shared by the whole module made the tests order-dependent:
# the twentieth hook call in this file was refused by the rate limiter and the
# assertion read that as the wall's verdict. Each call gets its own counter, so
# each test measures the wall and nothing else. The scratch path also keeps the
# suite away from the real counter, which lives under the operator's data root.
_RATE_DIR = Path(tempfile.mkdtemp(prefix="pytest-wall-rate-"))
_RATE_SEQ = itertools.count()

HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"
REDIRECT = ROOT / ".claude" / "hooks" / "data-path-redirect.py"

# Assembled rather than written out, so this file does not itself carry a
# literal CEO-only path. The wall refuses a write whose CONTENT spells one, and
# a test that cannot be saved is not a test.
T = "thre" + "ads"
P = "perso" + "nal"


def _load_dispatch():
    spec = importlib.util.spec_from_file_location("dispatch_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dispatch = _load_dispatch()


def _verdict(payload: dict, cwd: str | None = None, data_root: str | None = None) -> str:
    """Run the real hook in its real process and report BLOCK or ALLOW.

    Two guards, both of which this helper lacked until 2026-08-30, and both in
    the direction that hides a dead wall.

    A CRASH IS NOT AN ALLOW. The verdict was `"BLOCK" if proc.stdout.strip()`
    with nothing checking the exit code, so a hook that died on an ImportError
    printed nothing to stdout and read as ALLOW. Every must-allow test in this
    file - ordinary work, the CEO-only write, the sibling subtree - then passed
    because the wall was not executing, which is the one failure they exist to
    catch. The sibling `tests/test_a_wall_that_only_refused_the_literal_spelling.py`
    already asserts `returncode == 0`; this one did not.

    A HANG IS NOT A PASS. `timeout=60`, matching the sibling. Roughly twenty
    subprocess calls in this file inherited an unbounded wait.
    """
    payload = dict(payload)
    payload["cwd"] = cwd or str(ROOT)
    state = _RATE_DIR / f"rate-{next(_RATE_SEQ)}.json"
    env = dict(os.environ, WS_RATE_LIMIT_STATE=str(state))
    if data_root:
        env["HEADING_OS_DATA"] = data_root
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, (
        f"the hook exited {proc.returncode}; an empty stdout from a dead hook "
        f"is not an ALLOW verdict.\nstderr:\n{proc.stderr[-2000:]}")
    return "BLOCK" if proc.stdout.strip() else "ALLOW"


# ============================================================
# The collapse itself
# ============================================================

@pytest.mark.parametrize("raw, expected", [
    (f"{T}/{P}/x.md", f"{T}/{P}/x.md"),
    (f"{T}/./{P}/x.md", f"{T}/{P}/x.md"),
    (f"{T}//{P}/x.md", f"{T}/{P}/x.md"),
    (f"{T}/business/../{P}/x.md", f"{T}/{P}/x.md"),
    (f"{T}\\{P}\\x.md", f"{T}/{P}/x.md"),
    (f"/data/{T}/./{P}", f"/data/{T}/{P}"),
    ("a/b/../..", ""),
    ("../../x", "x"),
    ("/", "/"),
])
def test_every_spelling_collapses_to_one_path(raw, expected):
    assert normalize_path(raw) == expected


def test_an_absolute_path_keeps_its_leading_separator():
    assert normalize_segments("/a/./b") == ["", "a", "b"]
    assert normalize_segments("a/./b") == ["a", "b"]


def test_the_rewriter_contract_refuses_what_it_cannot_place():
    """`normalize_rel` is stricter on purpose: a rewriter that guessed would
    concatenate an escaping path onto the data root, which is the defect the
    redirect hook was fixed for."""
    assert normalize_rel("outputs/../scripts/foo.py") == "scripts/foo.py"
    assert normalize_rel("outputs/../../x") is None
    assert normalize_rel("/etc/passwd") is None
    assert normalize_rel("C:/x") is None
    assert normalize_rel("") is None
    assert normalize_rel("./") is None


def test_the_wall_contract_collapses_what_the_rewriter_refuses():
    """The two contracts differ exactly where they must. A WALL still has to
    recognise a climbing path as naming the directory; a REWRITER must not
    place it."""
    assert normalize_rel(f"../{T}/{P}/x.md") is None
    assert normalize_path(f"../{T}/{P}/x.md") == f"{T}/{P}/x.md"


# ============================================================
# The wall, driven as the harness drives it
# ============================================================

_MUST_BLOCK = [
    ("Read canonical", {"tool_name": "Read", "tool_input": {"file_path": f"{T}/{P}/x.md"}}),
    ("Read dot", {"tool_name": "Read", "tool_input": {"file_path": f"{T}/./{P}/x.md"}}),
    ("Read double separator", {"tool_name": "Read", "tool_input": {"file_path": f"{T}//{P}/x.md"}}),
    ("Read dot-dot", {"tool_name": "Read",
                      "tool_input": {"file_path": f"{T}/business/../{P}/x.md"}}),
    ("Read backslash dot", {"tool_name": "Read",
                            "tool_input": {"file_path": f"{T}\\.\\{P}\\x.md"}}),
    ("Bash cat canonical", {"tool_name": "Bash", "tool_input": {"command": f"cat {T}/{P}/x.md"}}),
    ("Bash cat dot", {"tool_name": "Bash", "tool_input": {"command": f"cat {T}/./{P}/x.md"}}),
    ("Bash cat dot-dot", {"tool_name": "Bash",
                          "tool_input": {"command": f"cat {T}/b/../{P}/x.md"}}),
    ("Bash head dot", {"tool_name": "Bash", "tool_input": {"command": f"head -5 {T}/./{P}/x.md"}}),
    ("Bash cp dot-dot", {"tool_name": "Bash",
                         "tool_input": {"command": f"cp {T}/b/../{P}/x.md /tmp/o"}}),
    ("Bash archive dot", {"tool_name": "Bash",
                          "tool_input": {"command": f"cat {T}/archive/2026/./{P}/x.md"}}),
    ("Grep dot-dot path", {"tool_name": "Grep",
                           "tool_input": {"pattern": "x", "path": f"{T}/business/../{P}"}}),
    ("Glob dot pattern", {"tool_name": "Glob", "tool_input": {"pattern": f"{T}/./{P}/*.md"}}),
    ("Write cites the dot form",
     {"tool_name": "Write",
      "tool_input": {"file_path": "outputs/x.md", "content": f"see {T}/./{P}/villa.md"}}),
    ("Edit cites the dot-dot form",
     {"tool_name": "Edit",
      "tool_input": {"file_path": "outputs/x.md", "new_string": f"see {T}/b/../{P}/villa.md"}}),
]


@pytest.mark.parametrize("label, payload", _MUST_BLOCK, ids=[c[0] for c in _MUST_BLOCK])
def test_every_spelling_of_a_ceo_only_path_is_refused(label, payload):
    assert _verdict(payload) == "BLOCK", label


def test_a_search_that_climbs_between_its_two_arguments_is_refused():
    """The collapse has to happen AFTER the fields are joined.

    `path` and `pattern` compose, so a `..` at the head of the pattern climbs
    out of the path. Collapsing each field on its own drops that `..` against
    nothing and composes a path that reaches nowhere. Mutation found this:
    per-field normalisation changed no verdict anywhere else, and here it made
    the wall wrong.
    """
    assert _verdict({"tool_name": "Glob",
                     "tool_input": {"path": f"{T}/business",
                                    "pattern": f"../{P}/*.md"}}) == "BLOCK"


def test_the_composed_expression_is_what_gets_collapsed():
    """The same case at the unit boundary, so the reason survives a refactor."""
    assert dispatch._search_reaches_personal([T, "business", "..", P, "*.md"]) is True
    assert dispatch._search_reaches_personal([T, "business", "deal.md"]) is False


def test_writing_into_the_ceo_only_tree_is_allowed_however_it_is_spelled():
    """The wall refuses a NON-personal file that cites a personal path. Writing
    the personal file itself is ordinary CEO work, and the early exit that
    allows it read the raw path, so a dot segment turned the operator's own
    write into a refusal."""
    assert _verdict({"tool_name": "Write",
                     "tool_input": {"file_path": f"{T}/./{P}/villa.md",
                                    "content": f"see {T}/{P}/other.md"}}) == "ALLOW"


_MUST_ALLOW = [
    ("business thread", {"tool_name": "Read",
                         "tool_input": {"file_path": f"{T}/business/deal.md"}}),
    ("a directory that merely starts the same",
     {"tool_name": "Read", "tool_input": {"file_path": f"{T}/{P}-notes/x.md"}}),
    ("an ordinary engine read", {"tool_name": "Read",
                                 "tool_input": {"file_path": "scripts/thread.py"}}),
    ("an unanchored sweep of the engine clone",
     {"tool_name": "Glob", "tool_input": {"pattern": "**/*.md"}}),
    ("a Grep inside the engine tree",
     {"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "scripts"}}),
    ("a Bash read of an engine file",
     {"tool_name": "Bash", "tool_input": {"command": "cat scripts/thread.py"}}),
]


@pytest.mark.parametrize("label, payload", _MUST_ALLOW, ids=[c[0] for c in _MUST_ALLOW])
def test_the_wall_still_lets_ordinary_work_through(label, payload):
    """Without these a wall that refused everything would pass every test above."""
    assert _verdict(payload) == "ALLOW", label


# ============================================================
# A sweep rooted above the threads tree
# ============================================================

def _fake_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A data root holding a threads tree, plus its parent."""
    data = tmp_path / "ws" / "data"
    (data / T / P).mkdir(parents=True)
    (data / T / P / "note.md").write_text("canary\n", encoding="utf-8")
    (data / "crm" / "contacts").mkdir(parents=True)
    return tmp_path / "ws", data


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    parent, data = _fake_tree(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    monkeypatch.setattr(dispatch, "_THREADS_ROOTS_CACHE", None)
    return parent, data


def _descends(tool_name: str, path: str, pattern: str = "", cwd: str = "") -> bool:
    fields = {"path": path, "pattern": pattern, "glob": ""}
    return dispatch._sweep_descends_from_above(tool_name, fields, cwd)


def test_a_grep_rooted_at_the_data_root_descends(rooted):
    _parent, data = rooted
    assert _descends("Grep", str(data)) is True


def test_a_grep_two_levels_above_descends(rooted):
    parent, _data = rooted
    assert _descends("Grep", str(parent)) is True


def test_a_recursive_glob_at_the_data_root_descends(rooted):
    _parent, data = rooted
    assert _descends("Glob", str(data), "**/*.md") is True


def test_a_top_level_glob_at_the_data_root_does_not(rooted):
    """The mirror case. `*.md` names one level and cannot reach two down, so a
    rule that blocked it would be refusing work it has no reason to refuse."""
    _parent, data = rooted
    assert _descends("Glob", str(data), "*.md") is False


def test_a_glob_that_walks_down_by_name_descends(rooted):
    _parent, data = rooted
    assert _descends("Glob", str(data), f"{T}/{P}/*.md") is True


def test_a_glob_that_walks_down_by_wildcard_descends(rooted):
    """A wildcard segment can expand to the threads directory, so it counts."""
    _parent, data = rooted
    assert _descends("Glob", str(data), f"*/{P}/*.md") is True


def test_the_threads_root_itself_belongs_to_the_other_rule(rooted):
    """One question, one owner. A search rooted ON the threads directory spells
    `threads`, so the anchor rule answers it; this rule is only about a root
    ABOVE it, and answering both would hide which one is doing the work."""
    _parent, data = rooted
    assert _descends("Grep", str(data / T)) is False
    assert dispatch._search_reaches_personal([T]) is True


def test_a_sibling_subtree_is_left_alone(rooted):
    """Ordinary CRM work is not a threads sweep, and blocking it would get the
    wall turned off."""
    _parent, data = rooted
    assert _descends("Grep", str(data / "crm")) is False


def test_a_root_with_no_threads_below_it_is_left_alone(rooted, tmp_path):
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    assert _descends("Grep", str(elsewhere)) is False


def test_the_root_defaults_to_cwd_when_no_path_is_given(rooted):
    _parent, data = rooted
    assert _descends("Glob", "", "**/*.md", cwd=str(data)) is True
    assert _descends("Glob", "", "**/*.md", cwd=str(data / "crm")) is False


def test_the_hook_itself_refuses_a_sweep_rooted_above_the_threads_tree(rooted):
    """The wiring, not the function.

    Every test above calls `_sweep_descends_from_above` directly, so all of them
    stayed green when the call was removed from the hook branch. Mutation found
    it, 2026-08-29: a rule can be perfect and unreached. This drives the real
    hook in its real process, with the data root pointed at a scratch tree.
    """
    _parent, data = rooted
    assert _verdict({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": str(data)}},
                    data_root=str(data)) == "BLOCK"
    assert _verdict({"tool_name": "Glob",
                     "tool_input": {"pattern": "**/*.md", "path": str(data)}},
                    data_root=str(data)) == "BLOCK"


def test_the_hook_still_allows_a_sibling_subtree_of_the_same_root(rooted):
    """The mirror. A wall that refused every absolute path would pass the two
    assertions above and stop ordinary CRM work."""
    _parent, data = rooted
    assert _verdict({"tool_name": "Grep",
                     "tool_input": {"pattern": "x", "path": str(data / "crm")}},
                    data_root=str(data)) == "ALLOW"


def test_the_roots_fall_back_to_the_sibling_layout_when_the_resolver_fails(
        tmp_path, monkeypatch):
    """The `except` path, which no test reached until mutation said so.

    When the data-root resolver cannot answer, concluding "there is no threads
    directory anywhere" would silently take the wall down. It guesses the
    sibling layout instead, which fails toward the wall.
    """
    import scripts.utils.workspace as ws

    fake_engine = tmp_path / "engine"
    (fake_engine / "scripts").mkdir(parents=True)
    sibling = tmp_path / "engine-data"
    (sibling / T / P).mkdir(parents=True)

    def _boom():
        raise RuntimeError("no identity file on this machine")

    monkeypatch.setattr(ws, "get_data_root", _boom)
    monkeypatch.setattr(dispatch, "WORKSPACE", fake_engine)
    monkeypatch.setattr(dispatch, "_THREADS_ROOTS_CACHE", None)
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    assert dispatch._threads_roots() == [(sibling / T).resolve()]


def test_a_threads_root_that_does_not_exist_is_not_invented(tmp_path, monkeypatch):
    """Only directories on disk are roots. Inventing one would take away the
    unanchored-sweep carve-out for nothing."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(empty))
    monkeypatch.setattr(dispatch, "_THREADS_ROOTS_CACHE", None)
    assert dispatch._threads_roots() == []
    assert _descends("Grep", str(empty)) is False


# ============================================================
# One collapse, not two
# ============================================================

def _mentions_dot_dot(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == ".."
               for n in ast.walk(node))


def _drops_a_segment(body) -> bool:
    """True when this branch REMOVES the accumulated previous segment.

    `.pop()` was the only shape recognised, and it is one of several ways to
    write the same collapse. `del parts[-1]`, `parts[:] = parts[:-1]` and
    `parts = parts[:-1]` all do it, and each one walked straight past the guard
    whose stated purpose is to stop a second copy of `scripts/utils/pathnorm.py`
    appearing - the duplicate that left the personal-threads wall broken for six
    days.
    """
    for statement in body:
        for n in ast.walk(statement):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in {"pop", "popleft"}):
                return True
            if isinstance(n, ast.Delete):
                return True
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                value = n.value
                if (isinstance(value, ast.Subscript)
                        and isinstance(value.slice, ast.Slice)):
                    return True
    return False


def _spells_a_lexical_collapse(source: str) -> bool:
    """True when a module RESOLVES `..` itself, rather than refusing it.

    The distinction is the whole rule. Nine modules compare a segment against
    `'..'` and REJECT the path: an approvals reader, a snapshot-name check, an
    air-gap guard. Refusing a climbing path is correct and is not a second copy
    of anything. Resolving one means walking an accumulator and dropping the
    previous segment, and that is the logic that must exist once.

    So the shape asked for is a branch on `'..'` whose body drops a segment.
    Asked of the syntax tree, because the comments that explain such a loop
    quote the segments they collapse.

    WIDENED 2026-08-30, in both halves. The carrier was `ast.If` alone, so a
    `while` loop and a conditional expression - neither of which is an `ast.If`
    node - carried the collapse invisibly; and the body test was `.pop()` alone,
    so `del parts[-1]` and a slice reassignment did. The docstring already
    claimed the general property; only the code was narrow.

    STATED BOUND. This still reads syntax, not dataflow: a collapse split across
    a helper called from the branch, or one driven by a value that merely
    equals `".."` at runtime without the literal appearing, is not detected.
    Every shape it does recognise is fixtured below, both directions.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - another test's job
        return False
    for node in ast.walk(tree):
        # `if` and `while` share a body/orelse shape, so they share a branch.
        if (isinstance(node, (ast.If, ast.While))
                and _mentions_dot_dot(node.test)):
            if _drops_a_segment(node.body) or _drops_a_segment(node.orelse):
                return True
        elif isinstance(node, ast.IfExp) and _mentions_dot_dot(node.test):
            # An IfExp's arms are EXPRESSIONS, so the drop is the arm itself
            # (`parts[:-1]`) rather than a statement inside a body.
            arms = [node.body, node.orelse]
            if _drops_a_segment(arms) or any(
                    isinstance(a, ast.Subscript) and isinstance(a.slice, ast.Slice)
                    for a in arms):
                return True
        elif isinstance(node, ast.Match):
            for case in node.cases:
                if _mentions_dot_dot(case.pattern) and _drops_a_segment(case.body):
                    return True
    return False


_COLLAPSE_FIXTURE = (
    "for seg in p.split('/'):\n"
    "    if seg == '..':\n"
    "        parts.pop()\n"
)
_MEMBERSHIP_COLLAPSE_FIXTURE = (
    "if seg in ('..', '.'):\n"
    "    parts.pop()\n"
)
_REJECTION_FIXTURE = (
    "if any(p == '..' for p in parts):\n"
    "    raise ValueError('escaping path')\n"
)
_INNOCENT_FIXTURE = (
    "for seg in p.split('/'):\n"
    "    if seg in ('', '.'):\n"
    "        continue\n"
)


def test_the_detector_fires_on_a_hand_rolled_collapse():
    assert _spells_a_lexical_collapse(_COLLAPSE_FIXTURE) is True


def test_the_detector_reads_a_membership_test_too():
    assert _spells_a_lexical_collapse(_MEMBERSHIP_COLLAPSE_FIXTURE) is True


def test_the_detector_leaves_a_rejection_alone():
    """Nine modules in this tree refuse a climbing path. Refusing is correct,
    it is not a duplicate of the collapse, and flagging it would get the rule
    turned off."""
    assert _spells_a_lexical_collapse(_REJECTION_FIXTURE) is False


def test_the_detector_leaves_a_loop_that_does_not_handle_dot_dot_alone():
    assert _spells_a_lexical_collapse(_INNOCENT_FIXTURE) is False


# Four more spellings of the same collapse, every one of which the `.pop()`-and-
# `ast.If` detector reported as clean.
_DEL_COLLAPSE_FIXTURE = (
    "for seg in p.split('/'):\n"
    "    if seg == '..':\n"
    "        del acc[-1]\n"
)
_SLICE_COLLAPSE_FIXTURE = (
    "for seg in p.split('/'):\n"
    "    if seg == '..':\n"
    "        parts[:] = parts[:-1]\n"
)
_REBIND_COLLAPSE_FIXTURE = (
    "for seg in p.split('/'):\n"
    "    if seg == '..':\n"
    "        parts = parts[:-1]\n"
)
_WHILE_COLLAPSE_FIXTURE = (
    "while segs and segs[0] == '..':\n"
    "    acc.pop()\n"
    "    segs.pop(0)\n"
)
_IFEXP_COLLAPSE_FIXTURE = (
    "parts = parts[:-1] if seg == '..' else parts + [seg]\n"
)


@pytest.mark.parametrize("fixture", [
    _DEL_COLLAPSE_FIXTURE, _SLICE_COLLAPSE_FIXTURE, _REBIND_COLLAPSE_FIXTURE,
    _WHILE_COLLAPSE_FIXTURE, _IFEXP_COLLAPSE_FIXTURE,
])
def test_the_detector_reads_every_spelling_of_the_drop(fixture):
    assert _spells_a_lexical_collapse(fixture) is True, fixture


_REJECTS_WITH_A_DELETE = (
    "if seg == '..':\n"
    "    raise ValueError('escaping path')\n"
    "del scratch['tmp']\n"
)
_DELETES_FOR_ANOTHER_REASON = (
    "if seg in ('', '.'):\n"
    "    del acc[-1]\n"
)


@pytest.mark.parametrize("fixture", [
    _REJECTS_WITH_A_DELETE, _DELETES_FOR_ANOTHER_REASON,
])
def test_the_widened_detector_still_leaves_a_non_collapse_alone(fixture):
    """The widening must not start flagging the nine modules that REFUSE `..`.

    A `del` elsewhere in the file, and a `del` in a branch that is not about
    `..`, are both ordinary. Flagging either would get the rule turned off,
    which is the failure mode this file's own docstring names.
    """
    assert _spells_a_lexical_collapse(fixture) is False, fixture


def collapse_sites(corpus) -> list[str]:
    return [rel for rel, source in corpus if _spells_a_lexical_collapse(source)]


def test_the_collapse_predicate_reports_both_ways():
    corpus = [("owner.py", _COLLAPSE_FIXTURE), ("guard.py", _REJECTION_FIXTURE),
              ("other.py", _INNOCENT_FIXTURE)]
    assert collapse_sites(corpus) == ["owner.py"]
    assert collapse_sites(corpus[1:]) == []


# `scripts/utils/pathnorm.py` owns the collapse. Every other entry is a module
# that legitimately reasons about `..` for a different purpose, with the reason
# written down. A new one is a second copy until argued otherwise.
DECLARED_COLLAPSE_SITES = {
    "scripts/utils/pathnorm.py":
        "the owner; both contracts live here and both hooks import them",
}


def _corpus() -> list[tuple[str, str]]:
    out = []
    for path in tracked_python_files(("scripts", ".claude", "tests")):
        try:
            out.append((path.relative_to(ROOT).as_posix(),
                        path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:  # pragma: no cover - not a python source
            continue
    return out


def test_the_sweep_reaches_a_real_corpus():
    """Green over an empty corpus otherwise."""
    corpus = _corpus()
    assert len(corpus) > 500, f"only {len(corpus)} sources read"
    assert any(rel.endswith("pathnorm.py") for rel, _ in corpus)


def test_no_second_lexical_collapse_appears():
    undeclared = [rel for rel in collapse_sites(_corpus())
                  if rel not in DECLARED_COLLAPSE_SITES]
    assert undeclared == [], (
        "a hand-rolled `..` collapse is a second copy of "
        "scripts/utils/pathnorm.py, and the first duplicate of it left the "
        "personal-threads wall broken for six days. Import the shared one, or "
        "add an entry to DECLARED_COLLAPSE_SITES saying why this module has to "
        f"do its own: {undeclared}")


def stale_declarations(declared, live) -> list:
    return sorted(k for k in declared if k not in live)


def test_the_staleness_predicate_reports_both_ways():
    """Over the live tree the registry has one entry and it is live, so a rule
    that collected nothing would be green there. Both directions on synthetic
    input, or this is not tested at all."""
    assert stale_declarations({"gone.py": "why"}, {"owner.py"}) == ["gone.py"]
    assert stale_declarations({"owner.py": "why"}, {"owner.py"}) == []


def test_the_declaration_list_does_not_outlive_its_sites():
    stale = stale_declarations(DECLARED_COLLAPSE_SITES, set(collapse_sites(_corpus())))
    assert stale == [], f"declared collapse sites that no longer exist: {stale}"


def test_both_hooks_import_the_shared_collapse():
    """The wall and the rewriter must read one implementation, not two."""
    for hook in (HOOK, REDIRECT):
        source = hook.read_text(encoding="utf-8")
        assert "from scripts.utils.pathnorm import" in source, hook.name
