"""The brand fonts must actually load, not fall back to Inter in silence.

`.claude/skills/design/references/brand.css` declared its `@font-face` sources as
`url('../../../datastore/brand/fonts/...')`. Two things were wrong with that, and
either one alone was enough:

  * `../../../` from `.claude/skills/design/references/` lands on `.claude/`, not
    the workspace root, so the path pointed at nothing;
  * the CSS is INLINED into a document written under `outputs/`, and a browser
    resolves a relative `url()` against the DOCUMENT, not against the file the
    CSS came from -- so no relative form could ever have worked here.

GT Standard and 31C TypeFace therefore fell back to Inter on every render, which
is exactly the silent fallback the dashboard skill says to surface.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "design_studio", ROOT / "scripts" / "design-studio.py")
ds = importlib.util.module_from_spec(_spec)
sys.modules["design_studio"] = ds
_spec.loader.exec_module(ds)

_CSS = ROOT / ".claude" / "skills" / "design" / "references" / "brand.css"


def _fonts_present() -> bool:
    from scripts.utils.workspace import get_data_root
    return (get_data_root() / "datastore" / "brand" / "fonts").is_dir()


def test_no_relative_font_url_survives_injection():
    """Whatever the CSS says, what reaches the document must not be relative."""
    import pytest
    if not _fonts_present():
        pytest.skip("no brand fonts in this checkout (public clone)")
    out = ds._resolve_font_urls(_CSS.read_text(encoding="utf-8"))
    assert "url('../" not in out and 'url("../' not in out, \
        "a relative font url reached the inlined stylesheet"
    assert "file://" in out, "no font resolved to an absolute path"


def test_every_declared_font_file_exists():
    """A rewritten URL that points at nothing is no better than a relative one."""
    import re
    import pytest
    from urllib.parse import unquote, urlparse
    if not _fonts_present():
        pytest.skip("no brand fonts in this checkout (public clone)")

    out = ds._resolve_font_urls(_CSS.read_text(encoding="utf-8"))
    urls = re.findall(r"url\('(file://[^']+)'\)", out)
    assert urls, "the CSS declared no font faces at all"
    for u in urls:
        p = Path(unquote(urlparse(u).path))
        assert p.is_file(), f"font URL points at nothing: {u}"


def test_a_missing_font_is_left_alone_rather_than_rewritten(tmp_path, monkeypatch):
    """Rewriting to a second path that also does not exist helps nobody, and it
    would hide the fallback behind a plausible-looking absolute URL."""
    monkeypatch.setattr(ds, "get_data_root", lambda: tmp_path)
    css = "@font-face { src: url('../../../datastore/brand/fonts/Nope/x.woff2') format('woff2'); }"
    assert ds._resolve_font_urls(css) == css
