"""`scripts/utils/checkpoint_paths.py` said "Stdlib only" and imported siblings.

Found by the 2026-08-24 engine audit campaign (shard `scripts-utils-00-p3`,
finding F2). The finding had two halves. The code half was fixed on 2026-08-30:
`handoff_dir`'s two in-tree imports are now wrapped, so a missing or raising
sibling redirects the handoff instead of losing it. The DOCSTRING half survived
until 2026-09-02, and it is the half that misleads: the module opened with a
flat "Stdlib only, and it stays that way" over three `scripts.utils` imports it
had been carrying since before those guards were written. Anyone weighing
whether a fourth was safe was reading a rule nobody was following rather than
the one the module actually keeps.

The rule it keeps, and which these tests pin, is narrower and checkable:

  - nothing non-stdlib is imported at MODULE level, because `checkpoint-save.py`
    imports this file after the session context is gone and an import it cannot
    satisfy costs a handoff nobody can regenerate;
  - every in-tree import sits inside a function body AND inside a `try`;
  - the docstring names each one, so a fourth cannot be added in silence.

The last of those is what makes the docstring load-bearing instead of decorative.
"""
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "scripts" / "utils" / "checkpoint_paths.py"


def _tree():
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _in_tree_imports(tree):
    """Every `from scripts...` / `import scripts...` in the file, with its names."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("scripts"):
                found.extend((node, alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend((node, alias.name) for alias in node.names
                         if alias.name.startswith("scripts"))
    return found


def test_nothing_outside_the_standard_library_is_imported_at_module_level():
    """The promise that actually protects the handoff.

    Run in a subprocess with an empty `sys.path` entry for the repo removed
    would prove too much; what matters is that importing the module pulls in no
    third-party distribution, so a fresh clone before `uv sync` can still run
    the hooks.
    """
    tree = _tree()
    module_level = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    names = set()
    for node in module_level:
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
        else:
            names.update(alias.name.split(".")[0] for alias in node.names)
    names.discard("__future__")
    non_stdlib = names - set(sys.stdlib_module_names)
    assert non_stdlib == set(), (
        f"{sorted(non_stdlib)} is imported at module level. checkpoint-save.py "
        "imports this file after the session context has been discarded, so an "
        "import it cannot satisfy costs a handoff nobody can regenerate.")


def test_importing_the_module_pulls_in_no_third_party_distribution():
    """The same claim, measured rather than parsed.

    An AST walk sees this file. A transitive import through a sibling would not
    show up there, and that is exactly how the guarantee would be lost.
    """
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "import scripts.utils.checkpoint_paths;"
         "print(sorted(n for n in sys.modules "
         "if n in ('yaml', 'requests', 'anthropic', 'numpy', 'exchangelib')))"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", (
        f"a third-party module now loads with this one: {out.stdout.strip()}")


def test_every_in_tree_import_is_deferred_and_guarded():
    """Deferred into a function body, and wrapped, at every single use.

    `handoff_dir` reached two of them bare until 2026-08-30, so a pinned
    `HEADING_OS_DATA` aimed at a moved directory made `data_overlay_present()`
    raise by design and took the handoff with it. `local_now` had guarded its
    own sibling import all along; the two disagreed for months.
    """
    tree = _tree()
    imports = _in_tree_imports(tree)
    assert imports, "no in-tree import found; the shape of this file changed"

    guarded = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for handler_owner in ast.walk(func):
            if not isinstance(handler_owner, ast.Try):
                continue
            for node in ast.walk(handler_owner):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(node))

    unguarded = sorted(
        name for node, name in imports if id(node) not in guarded)
    assert unguarded == [], (
        f"{unguarded} is imported without a try around it. A refusal that "
        "propagates out of this module costs a handoff nobody can regenerate.")


def test_the_module_docstring_names_every_sibling_it_reaches():
    """The docstring half of the finding, and the reason it is not decorative.

    A fourth in-tree import added without a line in the docstring fails here,
    which is the only mechanism that keeps the module's stated rule equal to the
    rule it follows. The flat "Stdlib only" sentence could never fail.
    """
    tree = _tree()
    doc = ast.get_docstring(tree) or ""
    assert doc, "the module lost its docstring"

    names = sorted({name for _node, name in _in_tree_imports(tree)})
    missing = [name for name in names if name not in doc]
    assert missing == [], (
        f"the module docstring does not mention {missing}, which it imports "
        "from a sibling. Name it there, and say it is deferred and guarded, or "
        "the next reader gets a rule the file does not follow.")

    assert "MODULE level" in doc, (
        "the stdlib claim has to be scoped to module level; unqualified, it "
        "described a file that has carried three sibling imports for months")
