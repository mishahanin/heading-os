#!/usr/bin/env python3
"""Regenerate HTML documentation from Markdown source using the 31C light theme.

Usage:
    python scripts/regenerate-docs-html.py <md_file>         Regenerate one file
    python scripts/regenerate-docs-html.py --all             Regenerate all tracked pairs
    python scripts/regenerate-docs-html.py --check           List stale HTML/MD pairs (no changes)
    python scripts/regenerate-docs-html.py --quiet <md>      Suppress non-error output (hook mode)

Tracked pairs: for every *.md in docs/ and templates/ with a matching *.html,
this tool regenerates the HTML to match the MD. CSS source of truth lives in
reference/31c-docs-light-theme.css (the single canonical docs theme; the former
dark theme was retired 2026-06-27 — the CEO standardized all documentation on the
light theme).
"""

import argparse
import functools
import html as html_stdlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import markdown  # noqa: E402
except ImportError:
    print("ERROR: markdown library not installed. Run: pip install markdown pymdown-extensions", file=sys.stderr)
    sys.exit(2)

from scripts.utils.atomic import atomic_write_text  # noqa: E402
from scripts.utils.workspace import get_data_root, get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
CSS_PATH = ROOT / "reference" / "31c-docs-light-theme.css"

# Tracked HTML/MD pairs -- only these get regenerated. Post engine/data split the
# CEO-only guides (CEO-ADMIN-GUIDE) and ALL templates live in the
# DATA overlay, not the engine clone; include its docs/ + templates/ so --all and
# --check don't blind-spot them (an edited guide whose HTML was never regenerated
# would otherwise read fresh to the health check). Engine-only layouts (data root
# == engine root) keep the original two dirs.
TRACKED_DIRS = [ROOT / "docs", ROOT / "templates"]
try:
    _DATA_ROOT = get_data_root()
    if _DATA_ROOT != ROOT:
        TRACKED_DIRS += [_DATA_ROOT / "docs", _DATA_ROOT / "templates"]
except Exception as exc:  # noqa: BLE001 — never let path resolution break the renderer
    print(f"regenerate-docs-html: data-overlay scan skipped ({exc})", file=sys.stderr)

# Stems with a DEDICATED renderer that must not be clobbered by this generic
# renderer. Empty since 2026-06-27: the old SETUP-GUIDE light builder and the
# guide it produced were retired in the documentation consolidation; everything
# now renders through this one light-themed path.
EXCLUDE_STEMS: set[str] = set()

# Markdown extensions -- cover the full feature set used in workspace docs
MD_EXTENSIONS = [
    "extra",              # tables, fenced_code, attr_list, footnotes, abbr, def_list
    "toc",                # table of contents [TOC]
    "sane_lists",         # stricter list parsing
    "smarty",             # smart quotes, em-dashes
    "admonition",         # !!! note / warning blocks
    "codehilite",         # syntax highlighting classes on code
    "pymdownx.tilde",     # ~~strikethrough~~ and H~2~O subscripts
    "pymdownx.mark",      # ==highlight==
    "pymdownx.tasklist",  # - [x] checklist items
    "pymdownx.superfences",  # nested fenced blocks
]

MD_EXT_CONFIGS = {
    "codehilite": {"guess_lang": False, "css_class": "codehilite"},
    "pymdownx.tasklist": {"custom_checkbox": True},
    "toc": {"permalink": False, "toc_depth": "2-4"},
}


HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{css}

/* Regen-specific overrides for generic markdown-rendered content */
.container {{
  max-width: 1100px;
  margin: 0 auto;
}}
.doc-header {{
  padding: 3rem 2.5rem 2rem;
  border-radius: var(--radius);
  background: var(--gradient-subtle);
  border: 1px solid var(--border-color);
  margin-bottom: 2rem;
}}
.doc-header h1 {{
  font-size: 2.25rem;
  margin: 0;
  background: var(--gradient-header);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}}
.doc-body {{
  padding: 2rem 2.5rem;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}}
.doc-body h1, .doc-body h2, .doc-body h3, .doc-body h4 {{
  margin-top: 2rem;
  margin-bottom: 0.75rem;
  color: var(--text-primary);
}}
.doc-body h1:first-child {{ margin-top: 0; }}
.doc-body h1 {{ font-size: 1.75rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.5rem; }}
.doc-body h2 {{ font-size: 1.4rem; color: var(--accent-blue); }}
.doc-body h3 {{ font-size: 1.15rem; color: var(--accent-cyan); }}
.doc-body h4 {{ font-size: 1rem; color: var(--accent-purple); }}
.doc-body p, .doc-body li {{ line-height: 1.7; }}
.doc-body p {{ margin: 0.75rem 0; }}
.doc-body ul, .doc-body ol {{ margin: 0.75rem 0 0.75rem 1.5rem; }}
.doc-body a {{ color: var(--accent-blue); text-decoration: none; }}
.doc-body a:hover {{ color: var(--accent-cyan); text-decoration: underline; }}
.doc-body code {{
  background: var(--bg-secondary);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 0.9em;
  color: var(--accent-deep);
}}
.doc-body pre {{
  background: var(--bg-secondary);
  padding: 1rem 1.25rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  overflow-x: auto;
  margin: 1rem 0;
}}
.doc-body pre code {{
  background: transparent;
  padding: 0;
  color: var(--text-primary);
  font-size: 0.875rem;
}}
.doc-body table {{
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  overflow: hidden;
}}
.doc-body th, .doc-body td {{
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}}
.doc-body th {{
  background: var(--bg-card-hover);
  color: var(--accent-cyan);
  font-weight: 600;
}}
.doc-body tr:last-child td {{ border-bottom: none; }}
.doc-body blockquote {{
  border-left: 3px solid var(--accent-blue);
  padding: 0.5rem 1rem;
  margin: 1rem 0;
  background: var(--gradient-subtle);
  color: var(--text-secondary);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}}
.doc-body hr {{
  border: none;
  height: 1px;
  background: var(--border-color);
  margin: 2rem 0;
}}
.doc-footer {{
  text-align: center;
  padding: 2rem 0;
  color: var(--text-muted);
  font-size: 0.85rem;
}}
/* Syntax highlighting (codehilite) -- minimal readable palette */
.codehilite .k, .codehilite .kn {{ color: var(--accent-purple); }}
.codehilite .s, .codehilite .s1, .codehilite .s2 {{ color: var(--accent-green); }}
.codehilite .c, .codehilite .c1 {{ color: var(--text-muted); font-style: italic; }}
.codehilite .nb {{ color: var(--accent-cyan); }}
.codehilite .mi, .codehilite .mf {{ color: var(--accent-amber); }}
</style>
</head>
<body>
<div class="container">
<header class="doc-header">
<h1>{display_title}</h1>
<p style="color: var(--text-secondary); margin-top: 0.5rem;">{subtitle}</p>
</header>
<main class="doc-body">
{body}
</main>
<footer class="doc-footer">
Generated from <code>{source_name}</code> via <code>scripts/regenerate-docs-html.py</code>
</footer>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Docs-site shell: the public GitHub Pages site (docs/*.html) shares ONE light
# stylesheet (assets/docs.css) and ONE sidebar nav with the hand-authored pages
# (index, prerequisites, daemons, ...), so navigation is identical and present on
# every page. Markdown-generated site pages (DEPLOYMENT, QUICKSTART, EMERGENCY)
# render through SITE_SHELL; non-site docs (templates/, CEO guides) keep the
# portable self-contained HTML_SHELL above.
# ---------------------------------------------------------------------------
SITE_DIR = ROOT / "docs"
SITE_NAV_GROUPS = [
    ("Get started", [
        ("index.html", "Overview"),
        ("prerequisites.html", "Prerequisites &amp; install"),
        ("DEPLOYMENT.html", "Full deployment guide"),
        ("QUICKSTART.html", "Quickstart"),
        ("PLUGINS.html", "Install as a plugin"),
        ("MAKE-IT-YOURS.html", "Make it yours"),
    ]),
    ("Operate", [
        ("daemons.html", "Daemons &amp; scheduled tasks"),
        ("memory-odin.html", "Memory &amp; ODIN"),
        ("memory-lifecycle.html", "Memory lifecycle"),
        ("MODELS-SETUP.html", "AI models"),
        ("INTEGRATIONS-SETUP.html", "Integrations &amp; credentials"),
        ("TELEGRAM-AND-ALERTS.html", "Telegram &amp; alerts"),
        ("TROUBLESHOOTING.html", "Troubleshooting"),
        ("EMERGENCY-PROCEDURES.html", "Emergency procedures"),
    ]),
    ("Skills catalog", [
        ("skills-mcp-plugins.html", "Catalog index · MCP &amp; plugins"),
        ("skills-intel.html", "Intel"),
        ("skills-communication.html", "Communication"),
        ("skills-content-design.html", "Content &amp; design"),
        ("skills-crm.html", "CRM"),
        ("skills-strategy.html", "Strategy"),
        ("skills-operations-daily.html", "Operations: daily"),
        ("skills-operations-quality.html", "Operations: review"),
        ("skills-operations-infra.html", "Operations: infra"),
    ]),
    ("Reference", [
        ("ARCHITECTURE.html", "Architecture"),
        ("data-structure.html", "Data overlay structure"),
        ("RULES-REFERENCE.html", "Rules reference"),
        ("HOOKS-REFERENCE.html", "Hooks reference"),
        ("CONFIGURATION.html", "Configuration"),
        ("SECURITY-MODEL.html", "Security model"),
        ("THREAT-MODEL.html", "Threat model"),
        ("engine-data-segregation-contract.html", "Engine/data contract"),
        ("EXTENDING.html", "Extending the engine"),
        ("CANOPUS.html", "Canopus: the build standard"),
        ("DESIGN-CHECK.html", "Design check"),
        ("DOCS-PIPELINE.html", "Docs pipeline"),
        ("GLOSSARY.html", "Glossary"),
        ("RELEASE-NOTES.html", "Release notes"),
        ("https://github.com/mishahanin/heading-os/blob/main/ROADMAP.md", "Roadmap"),
        ("https://github.com/mishahanin/heading-os", "GitHub repository"),
    ]),
]


def _site_nav(active: str) -> str:
    groups = []
    for label, links in SITE_NAV_GROUPS:
        items = []
        for href, text in links:
            active_cls = ' class="active"' if href == active else ""
            items.append(f'      <a href="{href}"{active_cls}>{text}</a>')
        groups.append(
            f'    <div class="nav-group">\n      <div class="label">{label}</div>\n'
            + "\n".join(items)
            + "\n    </div>"
        )
    return "\n".join(groups)


NAV_BLOCK_RE = re.compile(
    r'(<div class="nav-body" id="navbody">).*?(</div>\s*</aside>)',
    re.DOTALL,
)


def sync_nav(html_path: Path, quiet: bool = False) -> bool:
    """Rewrite ONLY the sidebar nav block of a hand-authored site page so its
    navigation stays identical to SITE_NAV_GROUPS. The HTML-only pages (index,
    prerequisites, daemons, ...) have no MD source, so regenerate() never touches
    them; without this their baked-in nav silently drifts from the generated
    pages as the nav grows."""
    if not html_path.exists():
        print(f"ERROR: HTML not found: {html_path}", file=sys.stderr)
        return False
    text = html_path.read_text(encoding="utf-8")
    if not NAV_BLOCK_RE.search(text):
        if not quiet:
            print(f"  (no nav block in {html_path.name}, skipped)")
        return True
    replacement = (
        '<div class="nav-body" id="navbody">\n'
        + _site_nav(html_path.name)
        + "\n  </div>\n</aside>"
    )
    new_text = NAV_BLOCK_RE.sub(lambda _m: replacement, text, count=1)
    if new_text != text:
        atomic_write_text(html_path, new_text)
        if not quiet:
            print(f"  nav-synced {_display_path(html_path)}")
    return True


def sync_all_navs(quiet: bool = False) -> bool:
    """Nav-sync AND search-inject every docs-site HTML page that has NO MD source
    (md-sourced pages already get both the nav and the search box from
    regenerate())."""
    ok = True
    for html in sorted(SITE_DIR.glob("*.html")):
        if html.with_suffix(".md").exists():
            continue
        ok = sync_nav(html, quiet=quiet) and ok
        ok = inject_search(html, quiet=quiet) and ok
    return ok


SITE_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{subtitle_attr}">
<link rel="icon" type="image/webp" href="assets/logo.webp">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="assets/docs.css">
</head>
<body>
<div class="layout">
<aside class="sidebar">
  <a class="brand" href="index.html"><img class="brand-logo" src="assets/logo.webp" alt=""> HEADING OS</a>
  <p class="tagline">Operations engine for an AI executive assistant</p>
{search_box}
  <button class="menu-toggle" onclick="document.getElementById('navbody').classList.toggle('open')">Menu</button>
  <div class="nav-body" id="navbody">
{nav}
  </div>
</aside>
<main class="content">
  <h1>{display_title}</h1>
  {subtitle_block}
  {body}
  <footer class="foot">
    <p>HEADING OS — operations engine for an AI executive assistant. Licensed Apache-2.0. © 2026 Misha Hanin. · <a href="index.html">Docs home</a> · <a href="https://github.com/mishahanin/heading-os">GitHub</a></p>
  </footer>
</main>
</div>
{search_script}
</body>
</html>
"""

# Site-wide search: a dependency-free client-side search box (injected into the
# sidebar) backed by a prebuilt JSON index of every docs/*.html section. The box
# markup and the loader <script> live in these two constants so SITE_SHELL and the
# hand-authored pages (via inject_search) stay byte-identical.
SEARCH_BOX = (
    '  <div class="search-box">\n'
    '    <input type="search" id="doc-search" class="search-input" '
    'placeholder="Search docs" autocomplete="off" spellcheck="false" '
    'aria-label="Search the documentation">\n'
    '    <div class="search-results" id="search-results" role="listbox" hidden></div>\n'
    '  </div>'
)
SEARCH_SCRIPT = '<script src="assets/search.js" defer></script>'
SEARCH_INDEX_PATH = SITE_DIR / "assets" / "search-index.json"
SEARCH_TEXT_CAP = 1600  # chars of body text stored per section (keeps the index small)


class _SectionExtractor(HTMLParser):
    """Walk a rendered docs-site page and split its <main class="content"> body
    into sections keyed by the h1/h2/h3 that opens each one, capturing the heading
    id (the in-page anchor) and the plain text until the next heading. Only content
    inside <main> is read, so the shared sidebar nav never pollutes the index."""

    HEADINGS = {"h1", "h2", "h3"}
    SKIP = {"script", "style"}
    BLOCK = {"p", "li", "tr", "div", "h1", "h2", "h3", "h4", "br", "pre", "td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.skip_depth = 0
        self.in_heading = False
        self.head_parts: list[str] = []
        self.cur: dict | None = None
        self.sections: list[dict] = []
        self.page_title: str | None = None

    def _open_section(self, sec_id: str | None) -> None:
        if self.cur is not None:
            self.sections.append(self.cur)
        self.cur = {"id": sec_id or "", "heading": "", "text": []}

    def handle_starttag(self, tag, attrs):  # noqa: D102
        ad = dict(attrs)
        if tag == "main" and "content" in (ad.get("class") or "").split():
            self.in_main = True
            return
        if not self.in_main:
            return
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if tag in self.HEADINGS:
            self._open_section(ad.get("id"))
            self.in_heading = True
            self.head_parts = []
            return
        if tag in self.BLOCK and self.cur is not None:
            self.cur["text"].append(" ")

    def handle_endtag(self, tag):  # noqa: D102
        if tag == "main":
            self.in_main = False
            self.in_heading = False
            return
        if not self.in_main:
            return
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in self.HEADINGS and self.in_heading:
            self.in_heading = False
            heading = "".join(self.head_parts).strip()
            if self.cur is not None:
                self.cur["heading"] = heading
            if self.page_title is None and tag == "h1":
                self.page_title = heading

    def handle_data(self, data):  # noqa: D102
        if not self.in_main or self.skip_depth:
            return
        if self.in_heading:
            self.head_parts.append(data)
        elif self.cur is not None:
            self.cur["text"].append(data)


def _extract_sections(html_text: str, fallback_title: str) -> tuple[str, list[dict]]:
    parser = _SectionExtractor()
    parser.feed(html_text)
    parser.close()
    if parser.cur is not None:
        parser.sections.append(parser.cur)
    title = parser.page_title or fallback_title
    out = []
    for sec in parser.sections:
        text = re.sub(r"\s+", " ", "".join(sec["text"])).strip()
        heading = re.sub(r"\s+", " ", sec["heading"]).strip()
        if not text and not heading:
            continue
        # `truncated` is decided BEFORE the slice, from the full length. Deciding
        # it afterwards by `len(stored) == SEARCH_TEXT_CAP` cannot tell a section
        # that happens to be exactly 1600 characters from one that was cut, and a
        # count that is sometimes wrong by one is not a count.
        out.append({
            "id": sec["id"],
            "heading": heading,
            "text": text[:SEARCH_TEXT_CAP],
            "truncated": len(text) > SEARCH_TEXT_CAP,
        })
    return title, out


def build_search_index(quiet: bool = False) -> int:
    """Build docs/assets/search-index.json from every rendered docs/*.html page.
    One record per section: {u:file, a:anchor, p:page title, h:heading, t:text}."""
    pages = sorted(SITE_DIR.glob("*.html"))
    records = []
    truncated = 0
    for html_path in pages:
        title, sections = _extract_sections(html_path.read_text(encoding="utf-8"), html_path.stem)
        for sec in sections:
            truncated += bool(sec["truncated"])
            records.append({
                "u": html_path.name,
                "a": sec["id"] or "",
                "p": title,
                "h": sec["heading"] or title,
                "t": sec["text"],
            })
    SEARCH_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic. This index is served live on the public docs site, so an
    # interrupted --all used to leave a truncated JSON that broke search
    # site-wide until the next successful run.
    atomic_write_text(
        SEARCH_INDEX_PATH,
        json.dumps(records, ensure_ascii=False, separators=(",", ":")),
    )
    if not quiet:
        kb = SEARCH_INDEX_PATH.stat().st_size / 1024
        print(f"  search index: {len(records)} sections across {len(pages)} pages "
              f"-> assets/search-index.json ({kb:.0f} KB)")
        if truncated:
            # The cap is a deliberate size trade-off; the silence about it was
            # not. Measured 2026-08-27: 51 of 506 sections were cut, so a tenth
            # of the site's prose could not be found by searching for a phrase
            # inside it, and nothing anywhere said so. A docs author who sees
            # this line can split the section; one who sees nothing cannot.
            print(f"  search index: {truncated} of {len(records)} sections were cut "
                  f"at {SEARCH_TEXT_CAP} chars; text past that is NOT searchable")
    return len(records)


def inject_search(html_path: Path, quiet: bool = False) -> bool:
    """Idempotently add the sidebar search box and the loader <script> to a
    hand-authored site page. Md-sourced pages get both from SITE_SHELL on
    regenerate(); this covers index.html, daemons.html, and the other hand-authored
    pages that regenerate() never rewrites.

    The guard and the insertion read the SAME anchor, and they did not until
    2026-08-30: the guard asked whether `<button class="menu-toggle"` appeared
    anywhere, while the insertion was a `str.replace` on `'  <button ...'` with
    exactly two leading spaces. On a page indented any other way the guard
    passed, the replace matched nothing, the loader `<script>` below was still
    appended, and the page shipped `search.js` with no `#doc-search` element for
    it to bind to -- reported as a success, since this function returns True
    either way. All 37 site pages happen to use two spaces today, so the defect
    was latent rather than live; it is one hand-authored page away from real.
    The regex carries the page's own indentation through, so those 37 pages stay
    byte-identical.
    """
    if not html_path.exists():
        print(f"ERROR: HTML not found: {html_path}", file=sys.stderr)
        return False
    text = html_path.read_text(encoding="utf-8")
    orig = text
    if 'id="doc-search"' not in text:
        anchor = re.search(r'^([ \t]*)<button class="menu-toggle"', text,
                           flags=re.MULTILINE)
        if anchor:
            indent = anchor.group(1)
            box = "\n".join(
                (indent + ln[2:]) if ln.startswith("  ") else ln
                for ln in SEARCH_BOX.split("\n")
            )
            text = text[:anchor.start()] + box + "\n" + text[anchor.start():]
    if "assets/search.js" not in text and "</body>" in text:
        text = text.replace("</body>", SEARCH_SCRIPT + "\n</body>", 1)
    if text != orig:
        atomic_write_text(html_path, text)
        if not quiet:
            print(f"  search-injected {_display_path(html_path)}")
    return True


@functools.lru_cache(maxsize=1)
def load_css() -> str:
    if not CSS_PATH.exists():
        print(f"ERROR: CSS template missing: {CSS_PATH}", file=sys.stderr)
        sys.exit(2)
    return CSS_PATH.read_text(encoding="utf-8")


_SUBTITLE_LIMIT = 200

_METADATA_LINE = re.compile(
    r"^\**\s*(last (updated|verified)|consumed by|status|version|owner|"
    r"classification|audience)\s*[:\*]", re.I)


def _clean_subtitle(raw: str) -> str:
    """A page subtitle fit for `<meta name="description">`.

    Two defects it replaces, measured across the site on 2026-08-23 (7 of the
    generated pages): a blind `[:200]` cut mid-word or mid-clause, and a strip
    that removed only `*_\\`` so link syntax, brackets and image markup reached
    the attribute verbatim.

    Markdown is unwrapped rather than deleted -- `[docs](x.md)` becomes `docs`,
    not nothing -- and the cut lands on the last sentence end inside the limit,
    falling back to the last word boundary with an ellipsis when the first
    sentence is itself longer than the limit.
    """
    s = raw.strip()
    s = re.sub(r"^\s*(?:>\s*)+", "", s)                     # blockquote marker
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", s)            # list bullet
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)        # images -> alt text
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)         # links  -> label
    s = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", s)  # wiki-links
    s = re.sub(r"[*_`]", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    if len(s) <= _SUBTITLE_LIMIT:
        return s
    window = s[:_SUBTITLE_LIMIT]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > 60:
        return window[:cut + 1]
    space = window.rfind(" ")
    return (window[:space] if space > 60 else window).rstrip(" ,;:-") + "…"


def _join_paragraph(lines: list[str], start: int) -> str:
    """The whole lead paragraph, unwrapped, starting at `lines[start]`.

    Markdown prose here is hard-wrapped at ~80 columns, and reading one
    physical line published a sentence that stops mid-clause. Measured across
    `docs/*.md` on 2026-08-23: 14 of the generated pages carried a subtitle cut
    at the wrap point -- "reads as designed or", "so you can try the engine's",
    "That means the" -- in the visible standfirst under the H1, in the
    `<meta name="description">` that search engines quote, and in every
    `docs/assets/search-index.json` record built from those pages.

    The paragraph ends at a blank line or at the first structural line (a
    heading, a rule, a table row, a list bullet, a blockquote), so a lead
    paragraph immediately followed by a list does not swallow the list.
    """
    out: list[str] = []
    for k in range(start, len(lines)):
        s = lines[k].strip()
        if not s:
            break
        if k > start and s.startswith(("#", "---", "|", ">", "- ", "* ", "+ ")):
            break
        out.append(s)
    return " ".join(out)


def extract_title(md_text: str, fallback: str) -> tuple[str, str]:
    """Return (display_title, subtitle) extracted from MD, or fallbacks."""
    lines = md_text.splitlines()
    title = fallback
    subtitle = ""
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            title = s[2:].strip()
            # The first PROSE paragraph after the H1 is the subtitle.
            #
            # A metadata block ("Last Updated:", "Consumed by:") is not prose,
            # and skipping only its first LINE is not enough: those blocks wrap,
            # so the next line is a continuation and reads worse than the
            # original. ARCHITECTURE published "Last Updated: 2026-08-20.
            # Consumed by: readers of the docs site, and" as its search snippet,
            # then ".claude/rules/console-first.md. That rule keeps the ..." once
            # only the first line was skipped. Skip the whole block, to the blank
            # line that ends it.
            skipping_metadata = False
            for j in range(i + 1, min(i + 20, len(lines))):
                candidate = lines[j].strip()
                if not candidate:
                    skipping_metadata = False
                    continue
                if candidate.startswith(("#", "---")):
                    continue
                if _METADATA_LINE.match(candidate):
                    skipping_metadata = True
                    continue
                if skipping_metadata:
                    continue
                subtitle = _clean_subtitle(_join_paragraph(lines, j))
                break
            break
    return title, subtitle


def strip_first_h1(md_text: str) -> str:
    """Remove the first H1 from MD since it becomes the page header."""
    lines = md_text.splitlines()
    out = []
    seen = False
    for line in lines:
        if not seen and line.strip().startswith("# "):
            seen = True
            continue
        out.append(line)
    return "\n".join(out)


# Mermaid diagrams: the docs site renders ```mermaid fences client-side via a
# vendored assets/mermaid.min.js. Each fence is extracted to a placeholder BEFORE
# markdown runs (so codehilite never touches it), then restored as a
# <pre class="mermaid"> block the mermaid runtime picks up. The runtime script is
# injected per-page (only when a fence is present), so non-diagram pages stay
# byte-identical and zero-JS.
_MERMAID_FENCE_RE = re.compile(r"(?ms)^```mermaid[ \t]*\n(.*?)\n```[ \t]*$")
_MERMAID_PLACEHOLDER = "xmermaidblock{}x"
MERMAID_SCRIPT = (
    '\n<script src="assets/mermaid.min.js"></script>'
    '\n<script>mermaid.initialize({ startOnLoad: true });</script>'
)


def _extract_mermaid(md_text: str) -> tuple[str, list[str]]:
    """Pull every ```mermaid fence out to a bare placeholder token, returning the
    stripped text and the list of raw diagram sources in order."""
    blocks: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        blocks.append(m.group(1))
        return f"\n\n{_MERMAID_PLACEHOLDER.format(len(blocks) - 1)}\n\n"

    return _MERMAID_FENCE_RE.sub(_sub, md_text), blocks


def _restore_mermaid(html: str, blocks: list[str]) -> str:
    """Swap each placeholder token back to a <pre class="mermaid"> block carrying the
    HTML-escaped diagram source (the mermaid runtime reads its textContent)."""
    for i, src in enumerate(blocks):
        token = _MERMAID_PLACEHOLDER.format(i)
        pre = f'<pre class="mermaid">{html_stdlib.escape(src)}</pre>'
        html = html.replace(f"<p>{token}</p>", pre).replace(token, pre)
    return html


_LOCAL_MD_HREF = re.compile(r'(href=")(?!https?://|mailto:|#)([^"]+?\.md)((?:#[^"]*)?")')


def _point_md_links_at_the_rendered_page(html: str, link_base: Path) -> tuple[str, list[str]]:
    """Rewrite `X.md` links to `X.html` when the rendered sibling exists.

    A markdown source links to its neighbours as `.md`, which is right in the
    repository and wrong on the published site: a browser gets raw markdown, or
    a 404. Measured 2026-08-23: 14 such links across the generated pages, two of
    them climbing out of `docs/` to `../.devcontainer/README.md`, a file the site
    does not publish at all.

    `link_base` is the directory of the page being rendered, NOT the site
    directory. It used to be the module-level SITE_DIR for every page, while
    `TRACKED_DIRS` also renders `templates/` and the DATA overlay's `docs/` and
    `templates/`: a relative link in one of those was resolved against a
    directory it has nothing to do with. The dangerous half is not the link left
    unrewritten, it is the coincidence -- a `templates/X.md` link where
    `docs/X.html` happens to exist rewrote to a confident, wrong href.

    Returns the rewritten HTML AND the targets that resolved to nothing. A link
    whose rendered sibling does not exist is left alone, because silently
    pointing it at a second missing page would just move the 404 -- and the
    caller now actually receives the list this docstring used to say it did.
    Before that, `str` was the whole return type, so no caller COULD learn which
    targets missed, and every wrong-directory resolution was invisible at
    generation time.
    """
    unresolved: list[str] = []

    def _sub(m):
        target = link_base / m.group(2)
        if target.with_suffix(".html").is_file():
            return f"{m.group(1)}{m.group(2)[:-3]}.html{m.group(3)}"
        unresolved.append(m.group(2))
        return m.group(0)

    return _LOCAL_MD_HREF.sub(_sub, html), unresolved


def md_to_html(md_text: str, link_base: Path | None = None) -> tuple[str, list[str]]:
    md = markdown.Markdown(extensions=MD_EXTENSIONS, extension_configs=MD_EXT_CONFIGS)
    return _point_md_links_at_the_rendered_page(md.convert(md_text),
                                                link_base or SITE_DIR)


def regenerate(md_path: Path, quiet: bool = False) -> bool:
    html_path = md_path.with_suffix(".html")
    if not md_path.exists():
        print(f"ERROR: MD file not found: {md_path}", file=sys.stderr)
        return False

    md_text = md_path.read_text(encoding="utf-8")
    display_title, subtitle = extract_title(md_text, fallback=md_path.stem)
    body_md = strip_first_h1(md_text)
    body_md, mermaid_blocks = _extract_mermaid(body_md)
    body_html, unresolved_links = md_to_html(body_md, md_path.parent)
    body_html = _restore_mermaid(body_html, mermaid_blocks)

    if md_path.parent == SITE_DIR:
        # Public docs-site page: shared sidebar + assets/docs.css (light).
        subtitle_block = (
            f'<p class="page-meta">{html_stdlib.escape(subtitle)}</p>' if subtitle else ""
        )
        full_html = SITE_SHELL.format(
            title=html_stdlib.escape(display_title),
            subtitle_attr=html_stdlib.escape(subtitle) if subtitle else "",
            display_title=html_stdlib.escape(display_title),
            subtitle_block=subtitle_block,
            search_box=SEARCH_BOX,
            nav=_site_nav(html_path.name),
            body=body_html,
            search_script=SEARCH_SCRIPT + (MERMAID_SCRIPT if mermaid_blocks else ""),
        )
    else:
        # Portable self-contained guide (templates/, CEO guides): inline theme.
        css = load_css()
        full_html = HTML_SHELL.format(
            title=html_stdlib.escape(display_title),
            display_title=html_stdlib.escape(display_title),
            subtitle=html_stdlib.escape(subtitle) if subtitle else "",
            css=css,
            body=body_html,
            source_name=html_stdlib.escape(md_path.name),
        )

    atomic_write_text(html_path, full_html)
    if not quiet:
        print(f"  {_display_path(md_path)} -> {_display_path(html_path)}")
    # Named on STDERR whether or not --quiet was passed. A markdown link that
    # resolves to no rendered page is a 404 the reader finds and the generator
    # already knew about; staying silent is the "looks like coverage" failure
    # `.claude/rules/scope-claims.md` is about. It is not a hard error: some of
    # these point at repository files the site deliberately does not publish.
    for target in unresolved_links:
        print(f"  [unresolved] {_display_path(md_path)} -> {target} "
              f"(no rendered page beside it; link left as .md)", file=sys.stderr)
    return True


def _display_path(p: Path) -> str:
    """Render a path relative to the engine ROOT when it lives there, else as-is.
    Audit/handoff artifacts resolve under the DATA root (a sibling of ROOT), so an
    unconditional relative_to(ROOT) raised ValueError after the HTML was already
    written -- same engine/data-separation crash class as checkpoint-save.py."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def find_tracked_pairs() -> list[Path]:
    pairs = []
    for d in TRACKED_DIRS:
        if not d.exists():
            continue
        for md in d.glob("*.md"):
            if md.stem in EXCLUDE_STEMS:
                continue
            html = md.with_suffix(".html")
            if html.exists():
                pairs.append(md)
    return pairs


def check_stale(pairs: list[Path]) -> list[tuple[Path, Path, float]]:
    """Return list of (md_path, html_path, age_seconds) where MD is newer."""
    stale = []
    for md in pairs:
        html = md.with_suffix(".html")
        md_mtime = md.stat().st_mtime
        html_mtime = html.stat().st_mtime
        if md_mtime > html_mtime:
            stale.append((md, html, md_mtime - html_mtime))
    return stale


def _report_design(target):
    """Print the deep design verdict for the regenerated site, above the baseline.

    Reports only. The docs drift guard is what gates this tree in CI; a generator
    that refused to write HTML over a contrast finding would block the very edit
    that fixes it. Import is local so a checkout without the engine module still
    regenerates.
    """
    try:
        from scripts.utils import impeccable_engine
    except ImportError:
        return
    findings, note = impeccable_engine.deep_findings(target)
    if note:
        print(f"[design] {note}")
        return
    above = impeccable_engine.apply_baseline(findings)
    if not above:
        print(f"[design] clean - {len(findings)} finding(s), all within the recorded baseline.")
        return
    print(f"[design] {len(above)} finding(s) ABOVE the baseline:")
    for finding in above[:10]:
        print(f"  {finding['file']}: {finding['type']} - {finding['context'][:70]}")
    if len(above) > 10:
        print(f"  ...and {len(above) - 10} more")


def main():
    parser = argparse.ArgumentParser(description="Regenerate HTML docs from MD sources")
    parser.add_argument("md_file", nargs="?", help="Path to MD file to regenerate")
    parser.add_argument("--all", action="store_true", help="Regenerate every tracked HTML/MD pair (also nav-syncs hand-authored site pages)")
    parser.add_argument("--nav-sync", action="store_true", help="Rewrite the sidebar nav + inject the search box on hand-authored site pages, then rebuild the search index")
    parser.add_argument("--search-index", action="store_true", help="Only rebuild docs/assets/search-index.json from the current docs/*.html pages")
    parser.add_argument("--check", action="store_true", help="List stale pairs without regenerating")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")
    args = parser.parse_args()

    if args.check:
        pairs = find_tracked_pairs()
        stale = check_stale(pairs)
        if not stale:
            print("All tracked HTML files are up to date.")
            return
        print(f"{len(stale)} stale HTML file(s):")
        for md, html, age in stale:
            days = age / 86400
            print(f"  {_display_path(md)} is {days:.1f} days newer than {html.name}")
        sys.exit(1 if stale else 0)

    if args.search_index:
        build_search_index(quiet=args.quiet)
        sys.exit(0)

    if args.nav_sync:
        ok = sync_all_navs(quiet=args.quiet)
        build_search_index(quiet=args.quiet)
        sys.exit(0 if ok else 1)

    if args.all:
        pairs = find_tracked_pairs()
        if not args.quiet:
            print(f"Regenerating {len(pairs)} HTML file(s)...")
        # A LIST, then all() -- not a generator. `all()` short-circuits, so the
        # first unreadable markdown file stopped every later page from being
        # regenerated at all, while --check and the drift guard then flagged a
        # tree this run never attempted. The search index was rebuilt from that
        # partially-stale HTML.
        results = [regenerate(md, quiet=args.quiet) for md in pairs]
        ok = all(results)
        if not ok:
            failed = [str(md) for md, good in zip(pairs, results, strict=True) if not good]
            print(f"{len(failed)} page(s) failed to regenerate:", file=sys.stderr)
            for path in failed:
                print(f"  {path}", file=sys.stderr)
        if not args.quiet:
            print("Syncing nav + search box on hand-authored site pages...")
        ok = sync_all_navs(quiet=args.quiet) and ok
        build_search_index(quiet=args.quiet)
        if not args.quiet:
            _report_design(ROOT / "docs")
        sys.exit(0 if ok else 1)

    if not args.md_file:
        parser.error("provide an MD path, or use --all / --check")

    md_path = Path(args.md_file)
    if not md_path.is_absolute():
        md_path = ROOT / md_path
    ok = regenerate(md_path, quiet=args.quiet)
    # The search index is rebuilt here too, for a docs-site page. `--all`,
    # `--nav-sync` and `--search-index` all rebuild it and this path did not, so
    # the one mode the module docstring calls "hook mode" was the one mode that
    # left `docs/assets/search-index.json` describing the PREVIOUS version of the
    # page it had just rewritten: missing new sections, snippets from deleted
    # prose, anchors that no longer resolve. `--check` compares MD/HTML mtimes
    # only, so nothing surfaced the drift; the docs-html-drift pre-commit hook
    # eventually caught it by running `--all` and diffing, which reports the
    # index as an unexplained change to a file the author never edited.
    # `md_path.parent == SITE_DIR` is regenerate()'s own test for a site page;
    # templates/ pages are not in the index and must not trigger a rebuild.
    if ok and md_path.parent == SITE_DIR:
        build_search_index(quiet=args.quiet)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
