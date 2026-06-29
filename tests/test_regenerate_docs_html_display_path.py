"""Regression: regenerate-docs-html must not crash rendering a path outside the engine ROOT.

Bug (2026-06-16): the post-write log line did `md_path.relative_to(ROOT)`
unconditionally. Audit/handoff artifacts resolve under the DATA root (a sibling of
the engine ROOT), so the call raised ValueError AFTER the HTML was already written
-- same engine/data-separation crash class as checkpoint-save.py. The fix renders
a relative path only when the file lives under ROOT, else the absolute string.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "regen_docs_html", str(ROOT / "scripts" / "regenerate-docs-html.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_display_path_under_root_is_relative():
    p = _mod.ROOT / "outputs" / "x.md"
    assert _mod._display_path(p) == "outputs/x.md"


def test_display_path_outside_root_does_not_raise():
    # A sibling DATA-root path is outside the engine ROOT; must fall back to the
    # absolute string instead of raising ValueError.
    sibling = _mod.ROOT.parent / ".heading-os-data" / "outputs" / "audit.md"
    out = _mod._display_path(sibling)
    assert out == str(sibling)
    assert ".heading-os-data" in out
