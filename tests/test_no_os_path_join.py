"""Regression: live scripts/ use pathlib, not os.path.join (F-L2).

The data-root seam and general code-quality both favour pathlib over the
os.path join helper. CEO commit e180220 migrated the data-root-bypass cases;
Phase 3 F-L2 finished the remaining 8 files. This guard keeps the helper out of
live scripts/ so new code follows the pathlib convention. archive/ is dead code
(never executed) and exempt.

AST, not a substring sweep, since 2026-08-29. The line-by-line version flagged
its own name written inside a COMMENT: `scripts/utils/repo_files.py` explains
why it does NOT use the helper, and the guard read that explanation as a
violation. A rule that punishes a file for documenting the trap teaches people
to stop documenting it, and the same false-positive shape was fixed the same day
in the frontmatter-coercion sweep. A call is a call; prose about a call is not.
"""
import ast
from pathlib import Path

from tests.repo_files import read_sources, tracked_paths

ROOT = Path(__file__).resolve().parent.parent


_PATH_MODULES = ("os.path", "posixpath", "ntpath", "genericpath")


def join_call_lines(source: str) -> list[int]:
    """Line numbers of `os.path.join(...)` CALLS in `source`.

    Pure, so both directions are measurable on synthetic input. It matches
    neither a mention in a comment nor one in a docstring.

    Four spellings reach the same helper, and the rule reads all four. Only the
    first two were matched until 2026-09-01, which left `import posixpath` and
    `from os import path` as ways to call the forbidden helper with the guard
    green. Measured that day: zero live sites used either, so the widening
    reports no pre-existing violation and cannot be mistaken for one.

      os.path.join(a, b)                  the dotted attribute call
      from os.path import join            a direct alias of the function
      import posixpath   -> posixpath.join(a, b)
      from os import path -> path.join(a, b)
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    func_aliases = set()      # names bound to the join FUNCTION
    module_aliases = set()    # names bound to a path MODULE
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in _PATH_MODULES:
                func_aliases.update(a.asname or a.name
                                    for a in node.names if a.name == "join")
            elif node.module == "os":
                module_aliases.update(a.asname or a.name
                                      for a in node.names if a.name == "path")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _PATH_MODULES:
                    # `import os.path` binds `os`, which the dotted branch below
                    # already covers; `import os.path as p` binds `p`.
                    module_aliases.add(a.asname or a.name.split(".")[0])
    module_aliases.discard("os")   # bare `os` is the dotted case, not an alias

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join":
            value = func.value
            if (isinstance(value, ast.Attribute) and value.attr == "path"
                    and isinstance(value.value, ast.Name) and value.value.id == "os"):
                hits.append(node.lineno)          # os.path.join(...)
            elif isinstance(value, ast.Name) and value.id in module_aliases:
                hits.append(node.lineno)          # posixpath.join(...) / path.join(...)
        elif isinstance(func, ast.Name) and func.id in func_aliases:
            hits.append(node.lineno)
    return hits


VIOLATING = [
    "import os\np = os.path.join(a, b)",
    "import os\nreturn os.path.join(root, *parts)",
    "from os.path import join\np = join(a, b)",
    "from os.path import join as j\np = j(a, b)",
    # The four spellings the first cut of the rule could not see.
    "import posixpath\np = posixpath.join(a, b)",
    "import ntpath\np = ntpath.join(a, b)",
    "import os.path as osp\np = osp.join(a, b)",
    "from os import path\np = path.join(a, b)",
    "from os import path as p_\nq = p_.join(a, b)",
    "from posixpath import join\np = join(a, b)",
]
CLEAN = [
    "# `os.path.join` is forbidden here; see the note above",
    '"""Explains why os.path.join is not used."""',
    "p = Path(a) / b",
    "s = ', '.join(parts)",
    "import os\np = os.path.dirname(a)",
    "x = 'os.path.join'",
    # Realistic near-misses, not obviously invalid input: a path module used for
    # something else, a locally defined `join`, and `os.sep.join`.
    "import posixpath\np = posixpath.basename(a)",
    "def join(*parts): return '/'.join(parts)\np = join(a, b)",
    "from pathlib import Path\np = Path(a).joinpath(b)",
    "import os\np = os.sep.join(parts)",
]


def test_the_rule_sees_a_real_call():
    for snippet in VIOLATING:
        assert join_call_lines(snippet), snippet


def test_the_rule_leaves_prose_and_pathlib_alone():
    for snippet in CLEAN:
        assert join_call_lines(snippet) == [], snippet


def test_no_os_path_join_in_live_scripts():
    """No live script under scripts/ may call the os.path join helper.

    Scope is `scripts/` only, and that is deliberate rather than an oversight:
    `.claude/hooks/` carries 26 calls of its own (measured 2026-09-01) under a
    different convention, and widening this rule is a separate decision.

    The corpus comes through git, not a bare `rglob`, so an ignored scratch copy
    under `scripts/` cannot join it.
    """
    paths = sorted(p for p in tracked_paths(("scripts/**/*.py", "scripts/*.py"))
                   if "archive" not in p.parts)
    # An empty violations list is green over zero files, so a renamed scripts/
    # directory or a changed suffix would switch this guard off without a failure.
    # 386 tracked files survived the archive filter on 2026-09-01.
    assert len(paths) >= 220, f"the scan collapsed to {len(paths)} files"
    violations = []
    # SCAN: a script that vanished between the walk and the read calls nothing,
    # so skipping it is the right answer. `read_sources` warns naming it, and
    # the count rides the message so the narrowing is never invisible.
    vanished: list[Path] = []
    for py, text in read_sources(paths, vanished):
        for lineno in join_call_lines(text):
            violations.append(f"{py.relative_to(ROOT).as_posix()}:{lineno}")
    assert not violations, (
        "the os.path join helper is called in live scripts/ - use pathlib.Path "
        f"instead (F-L2) ({len(vanished)} file(s) vanished mid-walk):\n  "
        + "\n  ".join(violations)
    )
