"""A function that reads only DATA must not take two roots.

The shape, found across the 2026-08-23 audit's `scripts-01-p4`, `scripts-02-p1`
and `scripts-02-p2` shards and measured wider here:

    def f(workspace_root: Path, ..., data_root: "Path | None" = None):
        if data_root is None:
            data_root = get_data_root()
        ...only ever reads data_root...

Nothing in those bodies used `workspace_root`. It was a dead first slot, and
every caller passed the DATA root into it, which is why the tree worked. Three
separate audit findings called this HIGH -- "every mutation is silently written
where no reader looks", "data_root is silently discarded across the entire pulse
call chain" -- and all three were refuted at the call sites. The defect is not
the routing; it is the NAME.

What the name cost, concretely:

* It made every correct call look wrong, so three reviewers reported the same
  non-bug and nobody looked for the real one.
* It hid a genuinely wrong call. `pulse.py` held
  `list_active_tasks(data_root, today=..., data_root=data_root)` -- a TypeError
  on the one path no test covered.
* It makes the first genuinely-split deployment silently read the wrong tree,
  because `data_root=None` falls through to the global `get_data_root()` rather
  than to the root the caller named. The docstrings said the opposite: twenty of
  them promised a fallback to `workspace_root` that no code implements.

Five modules were collapsed to one honest `data_root` earlier on 2026-08-24:
`inbox`, `library`, `pulse`, `studio`, `threads`. The rest were named here
rather than left to be discovered -- a silent partial sweep reads as a finished
one -- and the sweep was finished the same day, after the class produced two
MORE false HIGH findings in the `scripts-01-p4` and `scripts-02-p1` shards:
`agenda.today_agenda`, `tasks.list_active_tasks`, `tribe.list_tribe`,
`tribe.read_contact`, `mail.read_email_state` and `send_email.send_drafted`
each dropped their dead first slot. `tasks.py`'s done-log family
(`mark_done`, `undo_done`, `done_log_recent`, `read_done_log`,
`_write_done_entry`) took ONE root under the wrong name, which this detector
could not see at all, and was renamed with them.

The list of names is gone, and a mechanical invariant replaced it: a function
may hold two roots only if its body READS both. That is checkable, it cannot go
stale, and it is what the name list was standing in for. `search.py` is the
shape worth copying -- it genuinely needs two roots (seven sources read DATA,
`list_capabilities` reads `.claude/skills`) and says so in its own docstring.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.repo_files import read_sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
PKG = ROOT / "scripts" / "bridge_daemon"

def _dual_root_functions() -> dict[str, list[str]]:
    """{module: [function names taking BOTH workspace_root and data_root]}."""
    out: dict[str, list[str]] = {}
    for rel, name, _reads in _dual_root_rows():
        out.setdefault(rel, []).append(name)
    return out


def _parameter_names(node) -> set[str]:
    """Every parameter name, INCLUDING positional-only ones.

    `node.args.posonlyargs` was omitted until 2026-08-30, so a function written
    `def f(workspace_root, data_root, /)` declared both roots and the detector
    could not see it at all -- the invariant simply did not apply to that
    spelling.
    """
    args = node.args
    return ({a.arg for a in args.posonlyargs}
            | {a.arg for a in args.args}
            | {a.arg for a in args.kwonlyargs})


def _reads_of(node, name: str) -> int:
    """How many times the BODY actually READS `name`.

    Counted as Load-context `ast.Name` nodes, not as occurrences of the
    substring. `src.count("workspace_root")` until 2026-08-30 was satisfied by
    any identifier or string that merely CONTAINED the word, so

        def example(workspace_root, data_root):
            workspace_root_marker = data_root
            return workspace_root_marker

    counted two reads of a parameter it never touches, and the dead first slot
    the invariant exists to catch went unreported. A Store-context binding of
    the same name does not count either: assigning over the parameter is not
    reading it.
    """
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]          # the docstring: prose is not a read
    total = 0
    for statement in body:
        for inner in ast.walk(statement):
            if (isinstance(inner, ast.Name) and inner.id == name
                    and isinstance(inner.ctx, ast.Load)):
                total += 1
    return total


def _dual_root_rows() -> list[tuple[str, str, int]]:
    """(module, function, times the BODY reads workspace_root).

    The docstring is excluded from the count on purpose: a function that only
    names the root in prose is not reading it, and prose is exactly where the
    twenty false fallback promises lived.
    """
    rows: list[tuple[str, str, int]] = []
    for path in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):      # pragma: no cover - another test's job
            continue
        rel = path.relative_to(PKG).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not ({"workspace_root", "data_root"} <= _parameter_names(node)):
                continue
            rows.append((rel, node.name, _reads_of(node, "workspace_root")))
    return rows


_DEAD_SUBSTRING_FIXTURE = '''
def example(workspace_root, data_root):
    workspace_root_marker = data_root
    return workspace_root_marker
'''

_POSONLY_FIXTURE = '''
def example(workspace_root, data_root, /):
    return data_root
'''

_LIVE_FIXTURE = '''
def example(workspace_root, data_root):
    return workspace_root / "a", data_root / "b"
'''


@pytest.mark.parametrize("source,expected", [
    (_DEAD_SUBSTRING_FIXTURE, [("example", 0)]),
    (_POSONLY_FIXTURE, [("example", 0)]),
    (_LIVE_FIXTURE, [("example", 1)]),
])
def test_the_detector_counts_reads_and_sees_positional_only_parameters(
        source: str, expected: list) -> None:
    """The negative case for the two widenings. NEW 2026-08-30.

    The sweep below runs over a clean tree, so it is green whatever the
    detector does -- a substring counter and a read counter score the same on
    zero offenders. These three fixtures are the only thing that tells them
    apart: a dead parameter hidden inside a longer identifier, a dead parameter
    declared positional-only, and a genuine read that must still count.
    """
    tree = ast.parse(source)
    got = [(n.name, _reads_of(n, "workspace_root"))
           for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and {"workspace_root", "data_root"} <= _parameter_names(n)]
    assert got == expected


def test_the_detector_still_sees_the_shape():
    """A scan that matches nothing passes everything."""
    assert _dual_root_functions(), (
        "no dual-root function left anywhere; if the sweep finished, delete "
        "KNOWN_DUAL_ROOT and assert the empty set instead of removing this file"
    )


def test_a_function_may_hold_two_roots_only_if_it_reads_both():
    """The invariant that replaced the name list.

    A dead first slot is the whole defect: it makes every correct call look
    wrong, and it hid one genuinely wrong call for months. A function that
    never touches `workspace_root` in its body must not declare it.
    """
    dead = [f"{rel}:{name}" for rel, name, reads in _dual_root_rows() if reads == 0]
    assert not dead, (
        "these functions declare `workspace_root` and never read it. Drop the "
        "parameter and let `data_root` lead; every caller is already passing "
        "the DATA root into that slot:\n  " + "\n  ".join(sorted(dead))
    )


def test_the_collapsed_modules_stay_collapsed():
    """Nothing that lost its second root may regrow one."""
    collapsed = {"sources/inbox.py", "sources/library.py", "sources/pulse.py",
                 "sources/studio.py", "sources/threads.py",
                 "sources/agenda.py", "sources/tasks.py", "sources/tribe.py",
                 "finalizers/send_email.py", "finalizers/crm_log.py",
                 "refreshers/mail.py"}
    regressed = collapsed & set(_dual_root_functions())
    assert not regressed, sorted(regressed)


def test_the_done_log_family_names_the_root_it_actually_writes():
    """The detector above is blind to a SINGLE root under the wrong name.

    `tasks.py`'s done-log writers took one `workspace_root` and every caller
    handed them the DATA root, which an audit shard read as "the writer and the
    reader point at different trees" and filed HIGH. They never did.
    """
    src = (PKG / "sources" / "tasks.py").read_text(encoding="utf-8")
    assert "workspace_root" not in src.replace(
        "behind a dead leading ``workspace_root``", ""), (
        "tasks.py names a workspace_root again; the whole module reads DATA")


def test_the_two_real_exceptions_each_explain_themselves():
    """Both are allowed to hold two roots because both state why. Copy this
    shape, not the silent one."""
    search_src = (PKG / "sources" / "search.py").read_text(encoding="utf-8")
    assert "TWO ROOTS" in search_src, (
        "search.py stopped explaining why it legitimately takes an engine root "
        "as well as a data root"
    )
    app_src = (PKG / "app.py").read_text(encoding="utf-8")
    assert "ENGINE sources keep" in app_src, (
        "app.py stopped listing which sources get data_root and which keep "
        "workspace_root; that list IS the reason it may hold both"
    )


def test_no_docstring_promises_a_fallback_to_the_engine_root():
    """Twenty of them did, and the code never implemented it: an unsupplied
    `data_root` resolves to the GLOBAL seam, never to the passed root."""
    paths = sorted(PKG.rglob("*.py"))
    # An empty offender list is green over zero files, so a renamed package or
    # a changed suffix would switch this scan off without failing anything.
    # 45 files matched on 2026-08-26.
    assert len(paths) >= 28, f"the scan collapsed to {len(paths)} files"
    offenders = []
    # SCAN: a file that vanished between the rglob and the read cannot be
    # carrying a false fallback promise, so skipping it is the right answer and
    # `read_sources` warns naming it rather than dropping it silently.
    vanished: list[Path] = []
    for path, text in read_sources(paths, vanished):
        for n, line in enumerate(text.splitlines(), 1):
            if "falls back to ``workspace_root``" in line and "NOT to" not in line:
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert not offenders, (
        "a docstring claims an unsupplied data_root falls back to "
        f"workspace_root; it falls back to get_data_root() "
        f"({len(vanished)} file(s) vanished mid-walk):\n  "
        + "\n  ".join(offenders)
    )


def test_no_function_can_be_handed_the_same_root_twice():
    """`f(data_root, ..., data_root=data_root)` is a TypeError on whichever
    branch no test covers. One was live in pulse.py and one in search.py."""
    import re
    bad = []
    paths = sorted(PKG.rglob("*.py"))
    # Same reason as the scan above: no offender is found in a corpus that has
    # silently become empty, and the test would still report green.
    # 45 files matched on 2026-08-26.
    assert len(paths) >= 28, f"the scan collapsed to {len(paths)} files"
    # SCAN, same reasoning as above: a file that is gone holds no double-root
    # call site. The warning from `read_sources` keeps the narrowing visible.
    vanished: list[Path] = []
    for path, text in read_sources(paths, vanished):
        rel = path.relative_to(PKG).as_posix()
        dual = _dual_root_functions().get(rel, [])
        for n, line in enumerate(text.splitlines(), 1):
            m = re.search(r"\b(\w+)\(\s*data_root\b[^)]*\bdata_root\s*=", line)
            if not m:
                continue
            # Legitimate when the CALLEE still takes both roots: the caller is
            # filling each slot on purpose. Resolve the callee across the pkg.
            callee = m.group(1)
            takes_both = any(callee in fns for fns in _dual_root_functions().values())
            if not takes_both and callee not in dual:
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not bad, (
        f"({len(vanished)} file(s) vanished mid-walk)\n  " + "\n  ".join(bad)
    )
