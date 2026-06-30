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
import html as html_stdlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import markdown  # noqa: E402
except ImportError:
    print("ERROR: markdown library not installed. Run: pip install markdown pymdown-extensions", file=sys.stderr)
    sys.exit(2)

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
        ("MAKE-IT-YOURS.html", "Make it yours"),
    ]),
    ("Operate", [
        ("daemons.html", "Daemons &amp; scheduled tasks"),
        ("skills-mcp-plugins.html", "Skills, MCP &amp; plugins"),
        ("memory-odin.html", "Memory &amp; ODIN"),
        ("MODELS-SETUP.html", "AI models"),
        ("INTEGRATIONS-SETUP.html", "Integrations &amp; credentials"),
    ]),
    ("Reference", [
        ("ARCHITECTURE.html", "Architecture"),
        ("data-structure.html", "Data overlay structure"),
        ("SECURITY-MODEL.html", "Security model"),
        ("EXTENDING.html", "Extending the engine"),
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
    <p>HEADING OS — operations engine for an AI executive assistant. Licensed Apache-2.0. © 2026 Misha Hanin / 31 Concept. · <a href="index.html">Docs home</a> · <a href="https://github.com/mishahanin/heading-os">GitHub</a></p>
  </footer>
</main>
</div>
</body>
</html>
"""


def load_css() -> str:
    if not CSS_PATH.exists():
        print(f"ERROR: CSS template missing: {CSS_PATH}", file=sys.stderr)
        sys.exit(2)
    return CSS_PATH.read_text(encoding="utf-8")


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
            # Look for first non-empty line after the H1 as subtitle
            for j in range(i + 1, min(i + 10, len(lines))):
                candidate = lines[j].strip()
                if not candidate or candidate.startswith(("#", "---")):
                    continue
                subtitle = candidate.split("\n")[0][:200]
                # Strip markdown formatting for the subtitle
                subtitle = re.sub(r"[*_`]", "", subtitle)
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


def md_to_html(md_text: str) -> str:
    md = markdown.Markdown(extensions=MD_EXTENSIONS, extension_configs=MD_EXT_CONFIGS)
    return md.convert(md_text)


def regenerate(md_path: Path, quiet: bool = False) -> bool:
    html_path = md_path.with_suffix(".html")
    if not md_path.exists():
        print(f"ERROR: MD file not found: {md_path}", file=sys.stderr)
        return False

    md_text = md_path.read_text(encoding="utf-8")
    display_title, subtitle = extract_title(md_text, fallback=md_path.stem)
    body_md = strip_first_h1(md_text)
    body_html = md_to_html(body_md)

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
            nav=_site_nav(html_path.name),
            body=body_html,
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

    html_path.write_text(full_html, encoding="utf-8")
    if not quiet:
        print(f"  {_display_path(md_path)} -> {_display_path(html_path)}")
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


def main():
    parser = argparse.ArgumentParser(description="Regenerate HTML docs from MD sources")
    parser.add_argument("md_file", nargs="?", help="Path to MD file to regenerate")
    parser.add_argument("--all", action="store_true", help="Regenerate every tracked HTML/MD pair")
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

    if args.all:
        pairs = find_tracked_pairs()
        if not args.quiet:
            print(f"Regenerating {len(pairs)} HTML file(s)...")
        ok = all(regenerate(md, quiet=args.quiet) for md in pairs)
        sys.exit(0 if ok else 1)

    if not args.md_file:
        parser.error("provide an MD path, or use --all / --check")

    md_path = Path(args.md_file)
    if not md_path.is_absolute():
        md_path = ROOT / md_path
    ok = regenerate(md_path, quiet=args.quiet)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
