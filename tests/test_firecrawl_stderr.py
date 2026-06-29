"""Regression: firecrawl.py must not permanently replace sys.stderr (F-L8).

The bare `sys.stderr = open(os.devnull, "w")` never restored stderr and leaked the
/dev/null handle for the process lifetime. The fix routes quiet-mode stderr through
a context manager (contextlib.redirect_stderr inside an ExitStack) that both closes
the handle and restores stderr on exit. We assert via AST that no bare assignment to
sys.stderr survives (running firecrawl would require network/API access).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRECRAWL_SRC = ROOT / "scripts" / "firecrawl.py"


def _stderr_assignments(tree: ast.AST):
    """All nodes assigning to sys.stderr (the leak pattern)."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "sys"
                    and target.attr == "stderr"
                ):
                    hits.append(node)
    return hits


def test_no_bare_stderr_assignment():
    """`sys.stderr = ...` must not appear (leaked handle / unrestored stderr)."""
    tree = ast.parse(FIRECRAWL_SRC.read_text(encoding="utf-8"), filename=str(FIRECRAWL_SRC))
    hits = _stderr_assignments(tree)
    assert not hits, (
        f"Bare sys.stderr assignment in firecrawl.py at lines {[h.lineno for h in hits]}. "
        f"Use contextlib.redirect_stderr in a context manager instead (F-L8)."
    )


def test_redirect_stderr_used_when_quiet_supported():
    """If --quiet still suppresses stderr, it must go through contextlib.redirect_stderr."""
    src = FIRECRAWL_SRC.read_text(encoding="utf-8")
    if "quiet" in src:
        assert "redirect_stderr" in src, (
            "firecrawl.py suppresses stderr via --quiet but does not use "
            "contextlib.redirect_stderr (F-L8)"
        )
