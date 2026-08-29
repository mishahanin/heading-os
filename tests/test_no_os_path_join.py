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

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"


def join_call_lines(source: str) -> list[int]:
    """Line numbers of `os.path.join(...)` CALLS in `source`.

    Pure, so both directions are measurable on synthetic input. Matches the
    dotted attribute call and a `from os.path import join` alias, and matches
    neither a mention in a comment nor one in a docstring.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    aliased = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("os.path", "posixpath"):
            aliased.update(a.asname or a.name for a in node.names if a.name == "join")

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join":
            value = func.value
            if (isinstance(value, ast.Attribute) and value.attr == "path"
                    and isinstance(value.value, ast.Name) and value.value.id == "os"):
                hits.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id in aliased:
            hits.append(node.lineno)
    return hits


VIOLATING = [
    "import os\np = os.path.join(a, b)",
    "import os\nreturn os.path.join(root, *parts)",
    "from os.path import join\np = join(a, b)",
    "from os.path import join as j\np = j(a, b)",
]
CLEAN = [
    "# `os.path.join` is forbidden here; see the note above",
    '"""Explains why os.path.join is not used."""',
    "p = Path(a) / b",
    "s = ', '.join(parts)",
    "import os\np = os.path.dirname(a)",
    "x = 'os.path.join'",
]


def test_the_rule_sees_a_real_call():
    for snippet in VIOLATING:
        assert join_call_lines(snippet), snippet


def test_the_rule_leaves_prose_and_pathlib_alone():
    for snippet in CLEAN:
        assert join_call_lines(snippet) == [], snippet


def test_no_os_path_join_in_live_scripts():
    """No live script under scripts/ may call the os.path join helper."""
    paths = sorted(p for p in SCRIPTS_DIR.rglob("*.py") if "archive" not in p.parts)
    # An empty violations list is green over zero files, so a renamed scripts/
    # directory or a changed suffix would switch this guard off without a failure.
    # 371 files survived the archive filter on 2026-08-26.
    assert len(paths) >= 220, f"the scan collapsed to {len(paths)} files"
    violations = []
    for py in paths:
        for lineno in join_call_lines(py.read_text(encoding="utf-8")):
            violations.append(f"{py.relative_to(ROOT).as_posix()}:{lineno}")
    assert not violations, (
        "the os.path join helper is called in live scripts/ - use pathlib.Path "
        "instead (F-L2):\n  " + "\n  ".join(violations)
    )
