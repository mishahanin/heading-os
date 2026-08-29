#!/usr/bin/env python3
"""The CEO-only threads wall asked whether an argument SPELLED the directory.

A wildcard never spells it. Measured on 2026-08-29 against the real
`check_protect_personal_threads` in `.claude/hooks/_dispatch.py`, seven of
thirteen verdicts were wrong:

    Glob(pattern="threads/**/*.md")                       -> allowed
    Glob(pattern="**/*.md", path="threads")               -> allowed
    Glob(pattern="*/*.md",  path="threads")               -> allowed
    Glob(pattern="threads/archive/**/*.md")               -> allowed
    Grep(path="threads")                                  -> allowed
    Grep(path="threads", glob="personal/*.md")            -> allowed
    Grep(path="threads", glob="*.md")                     -> allowed

Every one of those returns CEO-only thread filenames, and the Grep ones return
matching LINES out of CEO-only bodies. `data-path-redirect.py` anchors any
`threads`-prefixed argument at the data root before the tool runs, so these are
not hypothetical spellings: they are the natural way to sweep the tree.

Two shapes of the same mistake were fixed together.

1. The three argument fields were each tested alone. They COMPOSE:
   `path="threads"` with `glob="personal/*.md"` names the subtree in neither
   field. The wall now builds the expression the tool will actually expand and
   asks where that can land.
2. The archived subtree was invisible. `scripts/thread.py` closes a thread into
   `threads/archive/<year>/<type>/` and `personal` is one of the types, so the
   same bodies live one directory deeper than `_PERSONAL_DIR_RE` can see. The
   Bash branch has covered that shape since it was written, which left the wall
   contradicting itself on ONE file: `cp threads/archive/2026/personal/x.md`
   was refused while `Read` and `cat` of it went through.

What this file will not let regress: the refusals above, the ordinary searches
that must keep working, the two boundary directories that only look private
(`threads/personal-notes/`, `threads/archive/<year>/business/`), and the
structure of the check itself, so a later edit cannot quietly go back to
testing one field at a time.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root

_ROOT = get_workspace_root()
_HOOK_PATH = _ROOT / ".claude" / "hooks" / "_dispatch.py"
_SETTINGS_TEMPLATES = [
    _ROOT / ".claude" / "settings.local.linux.json",
    _ROOT / ".claude" / "settings.local.macos.json",
    _ROOT / ".claude" / "settings.local.windows.json",
]

LIVE = "threads/personal"
ARCHIVED = "threads/archive/2026/personal"


@pytest.fixture(scope="module")
def dispatch():
    spec = importlib.util.spec_from_file_location("_dispatch_wall", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verdict(dispatch, tool, tool_input):
    return dispatch.check_protect_personal_threads(
        {"tool_name": tool, "tool_input": tool_input})


# ============================================================
# 1. The spellings that reached CEO-only content
# ============================================================

# Each entry is (tool, tool_input, why). Kept as a module constant so the
# vacuity test below can count how many of them carry no literal spelling.
REFUSED = [
    ("Glob", {"pattern": "threads/**/*.md"}, "a wildcard sweep of the whole tree"),
    ("Glob", {"pattern": "**/*.md", "path": "threads"}, "the same sweep over two fields"),
    ("Glob", {"pattern": "*/*.md", "path": "threads"}, "one star still crosses it"),
    ("Glob", {"pattern": "*", "path": "threads"}, "the bare listing of the tree"),
    ("Glob", {"pattern": "threads/*"}, "the same listing in one field"),
    ("Glob", {"pattern": "threads/?ersonal/*.md"}, "a single-character wildcard"),
    ("Glob", {"pattern": "threads/[pb]ersonal/*.md"}, "a character class"),
    ("Glob", {"pattern": "threads/archive/**/*.md"}, "any depth of the archive"),
    ("Glob", {"pattern": "threads/archive/*/*/*.md"}, "the archive by shape"),
    ("Glob", {"pattern": "threads/archive"}, "the archive root itself"),
    ("Glob", {"pattern": "threads/archive/2026"}, "one archived year, read whole"),
    ("Grep", {"pattern": "ceiling", "path": "threads"}, "recursive read of the tree"),
    ("Grep", {"pattern": "ceiling", "path": "threads", "glob": "*.md"}, "rg globs match at any depth"),
    ("Grep", {"pattern": "ceiling", "path": "threads", "glob": "personal/*.md"}, "split over two fields"),
    ("Grep", {"pattern": "ceiling", "path": "threads/archive"}, "recursive read of the archive"),
    ("Grep", {"pattern": "ceiling", "path": "threads", "glob": "*/*.md"}, "a filter that crosses it"),
    # The literal spellings, which were already refused and must stay refused.
    ("Glob", {"pattern": LIVE + "/*.md"}, "names the live subtree"),
    ("Glob", {"pattern": "*.md", "path": LIVE}, "names it in the path field"),
    ("Grep", {"pattern": "ceiling", "path": LIVE}, "names it as a Grep root"),
    ("Grep", {"pattern": "ceiling", "path": ".", "glob": LIVE + "/*.md"}, "names it in the filter"),
    ("Glob", {"pattern": ARCHIVED + "/*.md"}, "names the archived subtree"),
    ("Grep", {"pattern": "ceiling", "path": ARCHIVED}, "the archived subtree as a root"),
    # Windows separators reach the same directories.
    ("Glob", {"pattern": r"threads\\personal\\*.md"}, "backslash separators"),
    ("Grep", {"pattern": "ceiling", "path": r"threads\\archive\\2026\\personal"}, "backslashes, archived"),
    # Case is not a defence: the paths come from an operator's keyboard.
    ("Glob", {"pattern": "Threads/Personal/*.md"}, "mixed case"),
    # Mixed case AND no literal directory name, so the literal test cannot see
    # it and only the anchor in the reachability walk can. Without this case a
    # case-sensitive anchor passes every other test in this file.
    ("Glob", {"pattern": "Threads/**/*.md"}, "mixed case, wildcard only"),
    ("Grep", {"pattern": "ceiling", "path": "THREADS"}, "an upper-case root"),
    # A Grep `pattern` is a regex, not a path, so the reachability walk does not
    # read it. The per-field literal test is the only thing covering this, and
    # it has to cover the archived subtree too.
    ("Grep", {"pattern": LIVE + "/.*"}, "the live subtree named in the regex"),
    ("Grep", {"pattern": ARCHIVED + "/.*"}, "the archived subtree named in the regex"),
    # An absolute path into the data overlay is the same directory.
    ("Read", {"file_path": "/home/op/data/threads/personal/a.md"}, "absolute, live"),
    ("Read", {"file_path": "/home/op/data/" + ARCHIVED + "/a.md"}, "absolute, archived"),
    ("Read", {"file_path": LIVE + "/a.md"}, "relative, live"),
    ("Read", {"file_path": ARCHIVED + "/a.md"}, "relative, archived"),
]


@pytest.mark.parametrize("tool,tool_input,why", REFUSED,
                         ids=[f"{t}-{w}" for t, _, w in REFUSED])
def test_a_search_that_can_reach_the_ceo_only_threads_is_refused(
        dispatch, tool, tool_input, why):
    verdict = _verdict(dispatch, tool, tool_input)
    assert verdict is not None, f"{tool} {tool_input} was allowed ({why})"
    assert verdict["decision"] == "block"
    assert verdict["_policy_deny"] is True
    assert verdict["reason"], "a policy block has to say why"


# ============================================================
# 2. The searches that must keep working
# ============================================================

ALLOWED = [
    ("Glob", {"pattern": "threads/business/*.md"}, "business threads by name"),
    ("Glob", {"pattern": "**/*.md", "path": "threads/business"}, "any depth of business"),
    ("Grep", {"pattern": "harbour", "path": "threads/business"}, "business as a Grep root"),
    ("Grep", {"pattern": "harbour", "path": "threads/business", "glob": "*.md"}, "business with a filter"),
    ("Glob", {"pattern": "threads/archive/2026/business/*.md"}, "archived business threads"),
    ("Grep", {"pattern": "harbour", "path": "threads/archive/2026/business"}, "archived business root"),
    ("Glob", {"pattern": "threads/personal-notes/*.md"}, "a different directory entirely"),
    ("Grep", {"pattern": "x", "path": "threads/personal-notes"}, "that directory as a root"),
    ("Glob", {"pattern": "**/*.py"}, "an ordinary engine sweep"),
    ("Glob", {"pattern": "scripts/**/*.py"}, "an ordinary scoped sweep"),
    ("Grep", {"pattern": "def main", "path": "scripts"}, "an ordinary engine search"),
    ("Grep", {"pattern": "def main"}, "an engine search with no path at all"),
    ("Glob", {"pattern": "*.md", "path": "docs"}, "the docs tree"),
    ("Read", {"file_path": "threads/business/port-call.md"}, "reading a business thread"),
    ("Read", {"file_path": "threads/archive/2026/business/old.md"}, "reading an archived business thread"),
]


@pytest.mark.parametrize("tool,tool_input,why", ALLOWED,
                         ids=[f"{t}-{w}" for t, _, w in ALLOWED])
def test_an_ordinary_search_is_left_alone(dispatch, tool, tool_input, why):
    assert _verdict(dispatch, tool, tool_input) is None, \
        f"{tool} {tool_input} was refused, and it should not be ({why})"


def test_the_refusal_table_is_not_carried_by_the_literal_spellings_alone():
    """The whole point is the spellings that never write the directory name.

    Without this, someone could delete every wildcard case and the parametrized
    test above would still be green over the literals the old wall already
    caught.
    """
    def spells_it(tool_input):
        return any("personal" in str(v).lower() for v in tool_input.values())

    wildcard_only = [(t, i) for t, i, _ in REFUSED if not spells_it(i)]
    assert len(wildcard_only) >= 8, (
        "the refusal table has to carry the wildcard routes, not just the "
        f"literal ones; found {len(wildcard_only)}")
    assert len(ALLOWED) >= 10, "an allow table this small cannot catch over-blocking"


# ============================================================
# 3. The archived subtree, and the self-contradiction it caused
# ============================================================

@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": ARCHIVED + "/villa.md"}),
    ("Grep", {"pattern": "ceiling", "path": ARCHIVED}),
    ("Glob", {"pattern": ARCHIVED + "/*.md"}),
    ("Bash", {"command": f"cat {ARCHIVED}/villa.md"}),
    ("Bash", {"command": f"cp {ARCHIVED}/villa.md /tmp/x"}),
    ("Bash", {"command": f"head -5 {ARCHIVED}/villa.md"}),
])
def test_every_tool_treats_an_archived_private_thread_the_same_way(
        dispatch, tool, tool_input):
    """Copying it was refused while reading it was not. One file, two answers."""
    verdict = _verdict(dispatch, tool, tool_input)
    assert verdict is not None, f"{tool} reached the archived subtree: {tool_input}"
    assert verdict["decision"] == "block"


@pytest.mark.parametrize("body", [
    "see " + LIVE + "/villa.md for the ceiling",
    "see " + ARCHIVED + "/villa.md for the ceiling",
])
def test_an_ordinary_file_may_not_quote_a_path_into_either_subtree(dispatch, body):
    """The write branch of the same wall, which had the same archive blind spot.

    Copying a CEO-only path into a file that is not itself CEO-only is how the
    path reaches a repository, a push, or another reader. The live subtree was
    covered; the archived one was not.
    """
    verdict = _verdict(dispatch, "Write",
                       {"file_path": "outputs/notes/summary.md", "content": body})
    assert verdict is not None, f"an ordinary file was allowed to quote: {body}"
    assert verdict["decision"] == "block"


@pytest.mark.parametrize("target", [
    LIVE + "/villa.md",
    ARCHIVED + "/villa.md",
])
def test_writing_the_thread_itself_is_still_allowed(dispatch, target):
    """`scripts/thread.py` has to be able to write and to archive a thread."""
    assert _verdict(dispatch, "Write",
                    {"file_path": target, "content": "body\n"}) is None


def test_an_archived_business_thread_is_not_swept_up_with_it(dispatch):
    """The archive is not private; the `personal` directory inside it is."""
    for tool, tool_input in [
        ("Read", {"file_path": "threads/archive/2026/business/port-call.md"}),
        ("Grep", {"pattern": "port", "path": "threads/archive/2026/business"}),
        ("Glob", {"pattern": "threads/archive/2026/business/*.md"}),
    ]:
        assert _verdict(dispatch, tool, tool_input) is None, tool_input


# ============================================================
# 4. The reachability helpers, on their own
# ============================================================

@pytest.mark.parametrize("segment,name,expected", [
    ("personal", "personal", True),
    ("PERSONAL", "personal", True),
    ("business", "personal", False),
    ("personal-notes", "personal", False),
    ("*", "personal", True),
    ("**", "personal", True),
    ("?ersonal", "personal", True),
    ("[pb]ersonal", "personal", True),
    ("p*", "personal", True),
    ("b*", "personal", False),
    ("*-notes", "personal", False),
    ("archive", "archive", True),
    ("2026", "archive", False),
])
def test_one_segment_is_judged_by_what_it_can_expand_to(
        dispatch, segment, name, expected):
    assert dispatch._segment_can_be(segment, name) is expected


@pytest.mark.parametrize("tail,expected", [
    ([], True),                                   # the threads root, read whole
    (["**"], True),
    (["personal"], True),
    (["personal", "*.md"], True),
    (["*"], True),
    (["archive"], True),                          # the whole archive
    (["archive", "**"], True),
    (["archive", "2026"], True),                  # one year, read whole
    (["archive", "2026", "personal"], True),
    (["archive", "2026", "*", "x.md"], True),
    (["archive", "2026", "business"], False),
    (["archive", "2026", "business", "*.md"], False),
    (["business"], False),
    (["business", "**", "*.md"], False),
    (["personal-notes", "*.md"], False),
])
def test_the_tail_below_the_threads_root_is_judged_by_where_it_can_land(
        dispatch, tail, expected):
    assert dispatch._tail_reaches_personal(list(tail)) is expected


@pytest.mark.parametrize("segments,expected", [
    (["threads", "**", "*.md"], True),
    (["threads"], True),
    (["", "threads", "personal"], True),
    ([".", "threads", "*"], True),
    (["home", "op", "data", "threads", "personal", "a.md"], True),
    (["threads", "business", "*.md"], False),
    (["scripts", "**", "*.py"], False),
    (["**", "*.py"], False),
    ([], False),
])
def test_only_a_literal_threads_segment_anchors_the_search(
        dispatch, segments, expected):
    assert dispatch._search_reaches_personal(list(segments)) is expected


def test_an_unanchored_sweep_is_deliberately_not_refused(dispatch):
    """Documented limit, kept visible so nobody 'fixes' it by accident.

    A wildcard that could itself expand to `threads` is not treated as an
    anchor. Treating it as one refuses `Glob("**/*.py")`, which is every
    ordinary sweep in the engine. What makes the limit safe is the redirect:
    only a `threads`-prefixed argument is re-anchored at the data root, so an
    unanchored sweep stays inside the engine clone, and the engine clone holds
    no threads.
    """
    assert dispatch._search_reaches_personal(["**", "personal", "*.md"]) is False
    assert dispatch._search_reaches_personal(["*", "personal"]) is False
    assert (_ROOT / "threads").exists() is False, (
        "the engine clone grew a threads/ directory, which is what made the "
        "unanchored-sweep carve-out safe; re-check that carve-out")


# ============================================================
# 5. The shape of the check, so it cannot go back to one field at a time
# ============================================================

def _check_function() -> ast.FunctionDef:
    tree = ast.parse(_HOOK_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == "check_protect_personal_threads":
            return node
    raise AssertionError("check_protect_personal_threads is gone from the hook")


def test_the_check_still_asks_the_composed_question():
    """A grep for the helper name would match this docstring. The AST will not."""
    called = {n.func.id for n in ast.walk(_check_function())
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_search_reaches_personal" in called, (
        "the composed-expression test was dropped; the wall is back to judging "
        "each argument on its own")
    assert "_names_personal_threads" in called, (
        "the literal test covering both subtrees was dropped")


def test_the_archived_subtree_is_matched_from_one_place():
    """Two spellings of the same rule is how the archive half drifted before."""
    source = _HOOK_PATH.read_text(encoding="utf-8")
    assert source.count("_PERSONAL_ARCHIVE_RE = re.compile") == 1


# ============================================================
# 6. End to end, the way Claude Code runs it
# ============================================================

def _run_hook(payload: dict, tmp_path: Path) -> dict:
    env = dict(os.environ)
    # The dispatcher records every refusal. Keep that write inside the test.
    env["WORKSPACE_LOG_DIR"] = str(tmp_path / "logs")
    proc = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(_ROOT), env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def test_the_wildcard_sweep_is_denied_by_the_hook_process_itself(tmp_path):
    """The function returning a dict is not the same fact as the CLI refusing."""
    out = _run_hook({"tool_name": "Glob",
                     "tool_input": {"pattern": "threads/**/*.md"}}, tmp_path)
    decision = out.get("hookSpecificOutput", {})
    assert decision.get("permissionDecision") == "deny", out
    assert "threads" in decision.get("permissionDecisionReason", "")


def test_an_ordinary_sweep_is_not_denied_by_the_hook_process(tmp_path):
    out = _run_hook({"tool_name": "Glob",
                     "tool_input": {"pattern": "scripts/**/*.py"}}, tmp_path)
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny", out


# ============================================================
# 7. The wall has to be wired, not merely written
# ============================================================

def test_the_dispatcher_is_registered_for_the_native_search_tools():
    """On 2026-08-28 the check existed and the tools were not dispatched to it.

    A correct rule nobody calls refuses nothing, so the wiring is part of the
    guarantee and is asserted from the tracked templates, which are what a
    fresh clone actually installs.
    """
    for template in _SETTINGS_TEMPLATES:
        assert template.is_file(), template
        cfg = json.loads(template.read_text(encoding="utf-8"))
        matchers = [
            entry.get("matcher", "")
            for entry in (cfg.get("hooks") or {}).get("PreToolUse", [])
            if any("_dispatch.py" in h.get("command", "")
                   for h in entry.get("hooks", []))
        ]
        joined = " ".join(matchers)
        for tool in ("Read", "Grep", "Glob", "Bash", "Write", "Edit"):
            assert tool in joined, f"{template.name} does not dispatch {tool}"
