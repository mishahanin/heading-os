#!/usr/bin/env python3
"""One-time splitter: break the 191 KB skills catalog into per-category pages (F-8.2).

`docs/skills-mcp-plugins.html` was a single hand-authored page carrying the full
per-skill reference (rich cards: What it is / What it does / How to use / example /
Customize) plus the MCP servers and Plugins sections. This tool cuts the reference
body at each `<h3 class="cat">` divider into 8 category pages, preserving every card
verbatim, and rebuilds `skills-mcp-plugins.html` as the index (intro + quick-index +
category cards + the preserved MCP servers / Plugins sections).

Two content-faithful transforms are applied to the cards on the way out:
  * the anchor id moves from `<section class="skill" id="s-x">` onto its `<h3>` so the
    site search indexes a per-skill anchor (the search index keys sections on the
    heading id), giving real deep-links like `skills-intel.html#s-osint`;
  * the quick-index links (`href="#s-x"`) are rewritten to the cross-page form
    `href="<category-page>#s-x"` so they still resolve after the split.

Kept for provenance like `scripts/dev/extract-router-rows.py`; it is NOT wired into the
drift guard - the category pages are hand-authored HTML from here on (their body is the
source of truth). It refuses to run once the monolith has already been split (no
`<h2 id="reference">` marker), so it cannot double-apply.

Usage:
    python scripts/dev/split-skills-catalog.py --dry-run   # report the split, write nothing
    python scripts/dev/split-skills-catalog.py             # write the 8 pages + rebuilt index
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.colors import BOLD, GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
DOCS = ROOT / "docs"
SRC = DOCS / "skills-mcp-plugins.html"

# Category divider id -> (output page, page title). Order = display order on the index.
CAT_PAGES: list[tuple[str, str, str]] = [
    ("cat-intel", "skills-intel.html", "Intel skills"),
    ("cat-communication", "skills-communication.html", "Communication skills"),
    ("cat-content", "skills-content-design.html", "Content &amp; design skills"),
    ("cat-crm", "skills-crm.html", "CRM skills"),
    ("cat-strategy", "skills-strategy.html", "Strategy skills"),
    ("cat-ops-daily", "skills-operations-daily.html", "Operations: daily drivers"),
    ("cat-ops-quality", "skills-operations-quality.html", "Operations: planning &amp; review"),
    ("cat-ops-infra", "skills-operations-infra.html", "Operations: publishing, infra &amp; tooling"),
]

MAIN_OPEN = '<main class="content">'
INTRO_START = "  <h2>How skills are invoked</h2>"
REF_START = '  <h2 id="reference">Skill reference: every skill in detail</h2>'
TAIL_START = "  <h2>MCP servers</h2>"
MAIN_CLOSE = "</main>"

CAT_DIVIDER_RE = re.compile(r'<h3 class="cat" id="(cat-[a-z-]+)">')
SECTION_ID_RE = re.compile(r'<section class="skill" id="(s-[a-z0-9-]+)">\n<h3>')
SECTION_COUNT_RE = re.compile(r'<section class="skill"')

PAGE_FOOTER = (
    '  <footer class="foot">\n'
    '    <p>HEADING OS — operations engine for an AI executive assistant. '
    'Licensed Apache-2.0. © 2026 Misha Hanin. · '
    '<a href="index.html">Docs home</a> · '
    '<a href="skills-mcp-plugins.html">Skill catalog</a></p>\n'
    '  </footer>\n'
)
PAGE_CLOSE = "</main>\n</div>\n<script src=\"assets/search.js\" defer></script>\n</body>\n</html>\n"


def _slice(text: str, start_marker: str, end_marker: str) -> str:
    i = text.index(start_marker)
    j = text.index(end_marker, i)
    return text[i:j]


def build_category_page(head_prefix: str, page_title: str, page_file: str, cards_html: str) -> str:
    """Assemble one category page from the shared head + this category's cards."""
    head = head_prefix.replace(
        "<title>Skills, MCP &amp; plugins — HEADING OS</title>",
        f"<title>{page_title} — HEADING OS</title>",
    )
    head = re.sub(
        r'<meta name="description" content="[^"]*">',
        f'<meta name="description" content="{re.sub(r"&amp;", "and", page_title)} in HEADING OS: '
        f'what each skill is, what it does, how to invoke it, and what you can customize.">',
        head,
        count=1,
    )
    meta = (
        '  <p class="page-meta"><a href="skills-mcp-plugins.html">Back to the full skill '
        'catalog</a>. Each entry states what the skill is, what it does under the hood, how to '
        'invoke it, and what you can customize. Commands marked \U0001f512 are '
        'explicit-invocation only.</p>\n'
    )
    return (
        head
        + f"\n  <h1>{page_title}</h1>\n"
        + meta
        + cards_html.rstrip()
        + "\n\n"
        + PAGE_FOOTER
        + PAGE_CLOSE
    )


def build_index(head_prefix: str, h1_block: str, intro: str, tail: str, counts: dict[str, int]) -> str:
    """Rebuild skills-mcp-plugins.html as the catalog index."""
    cards = ['  <h2 id="reference">Skill reference by category</h2>',
             '  <p class="page-meta">Every skill is documented in full, with a usage example and '
             'customization notes, on its category page below.</p>',
             '  <div class="cards">']
    for cat_id, page, title in CAT_PAGES:
        n = counts.get(cat_id, 0)
        cards.append(
            f'    <a class="card" href="{page}">\n'
            f'      <h3>{title}</h3>\n'
            f'      <p>{n} skill{"s" if n != 1 else ""}, documented in full.</p>\n'
            f'    </a>'
        )
    cards.append('  </div>\n')
    cards_html = "\n".join(cards)
    return (
        head_prefix
        + h1_block
        + intro
        + cards_html
        + "\n"
        + tail
        + PAGE_CLOSE
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Split the skills catalog into per-category pages")
    parser.add_argument("--dry-run", action="store_true", help="Report the split, write nothing")
    args = parser.parse_args()

    text = SRC.read_text(encoding="utf-8")
    if REF_START not in text:
        print(f"{YELLOW}skills-mcp-plugins.html has no '{REF_START.strip()}' marker; "
              f"it looks already split. Nothing to do.{RESET}")
        return 0

    head_prefix = text[: text.index(MAIN_OPEN) + len(MAIN_OPEN)] + "\n"
    h1_block = _slice(text, MAIN_OPEN, INTRO_START)[len(MAIN_OPEN):].lstrip("\n")
    h1_block = "  " + h1_block if not h1_block.startswith("  ") else h1_block
    intro = _slice(text, INTRO_START, REF_START)
    detail = _slice(text, REF_START, TAIL_START)
    tail = _slice(text, TAIL_START, MAIN_CLOSE)

    # Move each card's anchor id from the <section> onto its <h3> so search indexes it.
    detail = SECTION_ID_RE.sub(r'<section class="skill">\n<h3 id="\1">', detail)

    # Split the detail body into per-category chunks and build skill -> page map + counts.
    dividers = list(CAT_DIVIDER_RE.finditer(detail))
    if len(dividers) != len(CAT_PAGES):
        print(f"{YELLOW}expected {len(CAT_PAGES)} category dividers, found {len(dividers)}. "
              f"Aborting to avoid a bad split.{RESET}")
        return 1
    cat_chunks: dict[str, str] = {}
    for idx, m in enumerate(dividers):
        cat_id = m.group(1)
        start = m.end()  # drop the divider line itself; h1 names the category
        end = dividers[idx + 1].start() if idx + 1 < len(dividers) else len(detail)
        cat_chunks[cat_id] = detail[start:end].strip("\n")

    page_by_cat = {cat_id: page for cat_id, page, _ in CAT_PAGES}
    skill_to_page: dict[str, str] = {}
    counts: dict[str, int] = {}
    for cat_id, chunk in cat_chunks.items():
        counts[cat_id] = len(SECTION_COUNT_RE.findall(chunk))
        for sid in re.findall(r'<h3 id="(s-[a-z0-9-]+)">', chunk):
            skill_to_page[sid] = page_by_cat[cat_id]

    # Rewrite the quick-index links to cross-page form, and fix the "further down this page"
    # sentence that no longer holds after the split.
    def _xref(match: re.Match[str]) -> str:
        sid = match.group(1)
        page = skill_to_page.get(sid)
        return f'href="{page}#{sid}"' if page else match.group(0)

    intro = re.sub(r'href="#(s-[a-z0-9-]+)"', _xref, intro)
    intro = intro.replace(
        'in <a href="#reference">Skill reference: every skill in detail</a> further down this page.',
        'on the per-category pages linked from <a href="#reference">Skill reference by category</a> below.',
    )

    # Assemble outputs.
    outputs: dict[Path, str] = {}
    for cat_id, page, title in CAT_PAGES:
        outputs[DOCS / page] = build_category_page(head_prefix, title, page, cat_chunks[cat_id])
    outputs[SRC] = build_index(head_prefix, h1_block, intro, tail, counts)

    if args.dry_run:
        print(f"{BOLD}Split plan (dry run):{RESET}")
        for cat_id, page, _title in CAT_PAGES:
            size = len(outputs[DOCS / page].encode("utf-8"))
            print(f"  {page:<34} {counts[cat_id]:>2} skills  {size/1024:5.1f} KB")
        idx_size = len(outputs[SRC].encode("utf-8"))
        print(f"  {'skills-mcp-plugins.html (index)':<34} {'':>2}          {idx_size/1024:5.1f} KB")
        print(f"  mapped {len(skill_to_page)} quick-index anchors to category pages")
        return 0

    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
    print(f"{GREEN}Split complete:{RESET}")
    for cat_id, page, _ in CAT_PAGES:
        size = len(outputs[DOCS / page].encode("utf-8"))
        flag = "" if size <= 60 * 1024 else f"  {YELLOW}OVER 60 KB{RESET}"
        print(f"  {page:<34} {counts[cat_id]:>2} skills  {size/1024:5.1f} KB{flag}")
    print(f"  skills-mcp-plugins.html rebuilt as index "
          f"({len(outputs[SRC].encode('utf-8'))/1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
