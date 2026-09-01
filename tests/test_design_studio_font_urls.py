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

Two of the three tests below used to skip on any checkout without the private
brand tree, which is every public clone and CI. MEASURED 2026-09-01: replacing
`target.as_uri()` with `str(target)` - which drops the `file://` scheme and the
percent-encoding that a path like `GT Standard/` needs - failed 2 tests on the
operator's machine and passed 3 with `HEADING_OS_DATA` pinned at an empty
directory. The guard was running only where the defect was least likely to be
introduced.

The rewrite below builds the font files the CSS itself declares under a
temporary data root, so the resolver's behaviour is pinned on any checkout. The
one claim that genuinely needs the real tree - that the CSS names files 31C
actually ships - keeps its skip and says so.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

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


def _declared_tails(css: str) -> list[str]:
    """The path each `@font-face` declares, below `datastore/brand/fonts/`.

    Read with the resolver's OWN regex rather than a second one written here. A
    fixture built from a private copy of the pattern would keep passing after
    the resolver stopped recognising the CSS it is handed, which is the only
    way this whole file can fail silently.
    """
    return [match.group(1).split("datastore/brand/fonts/", 1)[1]
            for match in ds._FONT_URL_RE.finditer(css)]


@pytest.fixture
def synthetic_brand_fonts(tmp_path, monkeypatch) -> Path:
    """A data root holding exactly the font files this CSS declares.

    Stubs, not real fonts: `_resolve_font_urls` asks `is_file()` and nothing
    reads the bytes, so a stub exercises the same branch. The point is that the
    resolver is measured on a public clone and in CI, where the private brand
    tree does not exist and two of these tests used to skip.
    """
    css = _CSS.read_text(encoding="utf-8")
    tails = _declared_tails(css)
    assert tails, (
        "the resolver's own regex matched no @font-face source in brand.css, "
        "so every test built on this fixture would be scanning an empty world")

    fonts_root = tmp_path / "datastore" / "brand" / "fonts"
    for tail in tails:
        target = fonts_root / tail
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub")
    monkeypatch.setattr(ds, "get_data_root", lambda: tmp_path)
    return tmp_path


def test_no_relative_font_url_survives_injection(synthetic_brand_fonts):
    """Whatever the CSS says, what reaches the document must not be relative."""
    out = ds._resolve_font_urls(_CSS.read_text(encoding="utf-8"))
    assert "url('../" not in out and 'url("../' not in out, \
        "a relative font url reached the inlined stylesheet"
    assert "file://" in out, "no font resolved to an absolute path"


def test_every_rewritten_url_points_at_the_file_it_names(synthetic_brand_fonts):
    """A rewritten URL that points at nothing is no better than a relative one.

    Run against the synthetic root so it binds on any checkout. The URL is
    parsed back to a path, which is what makes this a check on the ENCODING as
    well as on the target: `GT Standard/` carries a space, and a rewrite that
    dropped `as_uri()` would produce a string a browser cannot resolve.
    """
    out = ds._resolve_font_urls(_CSS.read_text(encoding="utf-8"))
    urls = re.findall(r"url\('(file://[^']+)'\)", out)
    assert len(urls) == len(_declared_tails(_CSS.read_text(encoding="utf-8"))), (
        "not every declared @font-face was rewritten to a file:// URL")
    for u in urls:
        p = Path(unquote(urlparse(u).path))
        assert p.is_file(), f"font URL points at nothing: {u}"
        assert p.is_relative_to(synthetic_brand_fonts), (
            f"the URL escaped the data root it was resolved against: {u}")


def test_every_declared_font_file_ships_in_the_real_brand_tree():
    """The one claim that needs the operator's overlay: the CSS names files 31C
    actually ships. Skipped on a public clone, where there is nothing to check
    against; the resolver's own behaviour is pinned by the two tests above,
    which do NOT skip."""
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
