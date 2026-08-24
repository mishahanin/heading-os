#!/usr/bin/env python3
"""
31C Intelligence Briefing - HTML Newsletter Generator (V3)

Generates branded HTML newsletters matching the V3 editorial design system:
light cream theme, Bebas Neue / Crimson Pro / IBM Plex Mono typography,
CSS-only data visualizations, and newspaper-style layout.

Usage:
    python scripts/generate-newsletter-html.py <input.json> [--output-dir DIR] [--images section=path ...]

If --output-dir is omitted, saves to outputs/intel/newsletters/YYYY-MM-DD/

Tests: tests/test_a_morning_calendar_shifted_by_its_own_timezone.py, tests/test_a_table_that_lost_a_deal_and_a_revert_that_froze_the_source.py
"""

import json
import sys
import base64
import html
import re
import argparse
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.html_templates import load_template
from scripts.utils.image import load_logo_base64
from scripts.utils.workspace import get_default_tz, get_outputs_dir


# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent

LOGO_PATH = (
    WORKSPACE_ROOT
    / ".claude"
    / "skills"
    / "pptx-generator"
    / "brands"
    / "31c"
    / "assets"
    / "31C_Logo_Black_Color.png"
)


# ============================================================
# Utilities
# ============================================================
def embed_image(file_path):
    """Read an image file and return a base64 data URI string."""
    path = Path(file_path)
    if not path.exists():
        return ""
    suffix = path.suffix.lower().lstrip(".")
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    mime = mime_map.get(suffix, "image/png")
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def esc(text):
    """HTML-escape text content."""
    if not text:
        return ""
    return html.escape(str(text))


SAFE_URL_SCHEMES = ("http://", "https://", "mailto:")


def safe_url(url, fallback="#"):
    """An href value that cannot execute and cannot leave its attribute.

    The Further Reading block interpolated `item["url"]` raw, with no escaping
    and no scheme check, into `href="{url}"`. A url of `javascript:alert(1)`
    ran on click, and one containing a double quote escaped the attribute
    entirely and could add its own. The section is by design a list of EXTERNAL
    links carried in from scraped intel, which is exactly the channel an
    attacker-influenced string arrives through.

    Anything that is not http, https or mailto becomes `fallback`. A relative
    path is refused too: this document is mailed and rendered to PDF, where a
    relative link resolves against nothing useful.
    """
    if not url:
        return fallback
    candidate = str(url).strip()
    if not url_scheme_ok(candidate):
        return fallback
    return html.escape(candidate, quote=True)


def url_scheme_ok(url) -> bool:
    """True when the url starts with a scheme that cannot execute.

    Split out for the markdown link renderer, whose input has ALREADY been
    html-escaped. Escaping it a second time would turn `&` into `&amp;amp;`
    and corrupt every query string, so that path checks the scheme and leaves
    the text alone. A scheme name contains nothing html.escape touches, so
    testing the escaped string is the same test.
    """
    return bool(url) and str(url).strip().lower().startswith(SAFE_URL_SCHEMES)


def nl2br(text):
    """Convert newlines in a string to <br/> tags (for region names, titles)."""
    if not text:
        return ""
    return esc(text).replace("\n", "<br/>")


def markdown_to_html(text):
    """Minimal markdown-to-HTML for newsletter body content.

    Supports: paragraphs, **bold**, [links](url), bullet lists (- item).
    """
    if not text:
        return ""

    lines = text.strip().split("\n")
    paragraphs = []
    current = []

    for line in lines:
        stripped = line.strip()
        if stripped == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
        elif stripped.startswith("- "):
            # A bullet is its own paragraph. Every non-blank line used to be
            # joined with a space, so "- alpha\n- beta\n- gamma" became one
            # paragraph and then one <li> reading "alpha - beta - gamma". A
            # list only rendered correctly with a blank line between every
            # item, which is the opposite of how markdown is written.
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(stripped)
        else:
            current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))

    result = []
    for para in paragraphs:
        p = html.escape(para)
        # Bold
        p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
        # Links. The href used to be the captured group verbatim, so
        # `[x](javascript:alert(1))` in any body field became a live
        # javascript: link. Escaping upstream contained the quote breakout;
        # it never touched the scheme.
        p = re.sub(
            r"\[(.+?)\]\((.+?)\)",
            lambda m: (f'<a href="{m.group(2)}" target="_blank">{m.group(1)}</a>'
                       if url_scheme_ok(m.group(2)) else m.group(1)),
            p,
        )
        # Bullet items
        if p.startswith("- "):
            p = f"<li>{p[2:]}</li>"
        else:
            p = f"<p>{p}</p>"
        result.append(p)

    # Wrap consecutive <li> in <ul>
    final = []
    in_list = False
    for item in result:
        if item.startswith("<li"):
            if not in_list:
                final.append("<ul>")
                in_list = True
            final.append(item)
        else:
            if in_list:
                final.append("</ul>")
                in_list = False
            final.append(item)
    if in_list:
        final.append("</ul>")

    return "\n".join(final)


# ============================================================
# CSS
# ============================================================
def build_css():
    """Return the complete <style> block content matching V3 design."""
    return load_template("newsletter.css")


# ============================================================
# Rendering / Builders
# ============================================================
def build_top_bar(regions):
    """Black bar with animated pulse dot, regions, classification."""
    region_str = " &middot; ".join(esc(r) for r in regions) if regions else "GCC &middot; CIS &middot; Africa"
    return f"""
<div class="topbar">
  <div class="topbar-left"><div class="pulse"></div>Live Intelligence Feed &nbsp;&middot;&nbsp; {region_str}</div>
  <div class="topbar-right">31C&thinsp;/&thinsp;INT &nbsp;&middot;&nbsp; Unrestricted</div>
</div>
"""


def build_masthead(logo_uri, display_date, issue_num, regions, threat_level):
    """Two-column masthead with logo, date, issue, threat level."""
    region_str = " &middot; ".join(esc(r) for r in regions) if regions else "GCC &middot; CIS &middot; Africa"
    issue_str = f"Issue #{issue_num:03d}" if isinstance(issue_num, int) else f"Issue #{issue_num}"

    logo_html = ""
    if logo_uri:
        logo_html = f'<img src="{logo_uri}" alt="31C" class="logo-img"/>'

    return f"""
<div class="masthead">
  <div class="mast-row1">
    <div class="mast-brand">
      {logo_html}
      <div class="brand-sub">Sovereign Deep Packet Intelligence</div>
    </div>
    <div class="mast-right">
      <div class="mast-meta">
        <div class="mast-date">
          {esc(display_date)}<br/>
          {issue_str}<br/>
          {region_str}
        </div>
      </div>
      <div class="threat">
        <span class="threat-lbl">Threat Level</span>
        <span class="threat-val">{esc(threat_level)}</span>
      </div>
    </div>
  </div>
</div>
"""


def build_hero(hero_data):
    """Hero section with large title, accent word, and deck text."""
    if not hero_data:
        return ""

    kicker = esc(hero_data.get("kicker", "Intelligence Briefing"))
    # `str(... or "")`, matching the guards this file already carries for
    # `date` ("a JSON number reached fromisoformat and the render died") and
    # for `the_heading`'s body. The hero title had none: `"title": 42` made
    # `.split` raise AttributeError, uncaught, and produced no newsletter.
    # The input JSON is an external document, so its types are not ours.
    title_raw = str(hero_data.get("title") or "")
    # Same reason: a non-string here made `accent_word in line` raise
    # TypeError one loop below.
    accent_word = str(hero_data.get("accent_word") or "")
    deck = esc(hero_data.get("deck", ""))

    # Build title with accent word highlighted and line breaks
    title_lines = title_raw.split("\n")
    title_parts = []
    for line in title_lines:
        line_esc = esc(line.strip())
        if accent_word and accent_word in line:
            line_esc = line_esc.replace(esc(accent_word), f'<span class="accent">{esc(accent_word)}</span>')
        title_parts.append(line_esc)
    title_html = "<br/>".join(title_parts)

    return f"""
  <div class="hero">
    <div class="hero-bg"></div>
    <div class="hero-content">
      <div class="hero-kicker">{kicker}</div>
      <div class="hero-title">{title_html}</div>
      <div class="hero-deck">{deck}</div>
    </div>
  </div>
"""


def build_indicators(items):
    """Indicator bar with 5 equal columns."""
    if not items:
        return ""

    cols = []
    for item in items:
        val = esc(item.get("value", ""))
        label = esc(item.get("label", ""))
        style = item.get("style", "neutral")
        css_class = "ind-val"
        if style == "up":
            css_class += " up"
        elif style == "danger":
            css_class += " danger"
        cols.append(f'    <div class="ind"><span class="{css_class}">{val}</span><span class="ind-lbl">{label}</span></div>')

    return f"""
  <div class="indicators">
{"".join(cols)}
  </div>
"""


def build_section_header(number, kicker, title):
    """Reusable section header with large number, kicker, and title."""
    num_str = f"{number:02d}" if isinstance(number, int) else str(number)
    return f"""
    <div class="sec-header">
      <span class="sec-num">{num_str}</span>
      <div class="sec-title-block">
        <span class="sec-kicker">{esc(kicker)}</span>
        <span class="sec-title">{esc(title)}</span>
      </div>
    </div>
"""


def build_sea_state(data, section_num=1, image_uri=None):
    """Sea State section with radar banner or image, caption, and body."""
    if not data:
        return ""

    body = data if isinstance(data, str) else data.get("body", "")
    banner_title = data.get("banner_title", "") if isinstance(data, dict) else ""
    banner_detail = data.get("banner_detail", "") if isinstance(data, dict) else ""
    caption = data.get("caption", "") if isinstance(data, dict) else ""

    header = build_section_header(section_num, "Kinetic Conflict", "Sea State")

    # Banner: use image if provided, otherwise CSS radar
    if image_uri:
        banner = f"""
    <div class="vis-banner vis-sea-img">
      <img src="{image_uri}" alt="Sea State"/>
      <div class="vis-sea-text">
        <strong>{esc(banner_title)}</strong>
        {esc(banner_detail)}
      </div>
    </div>
"""
    else:
        banner = f"""
    <div class="vis-banner vis-sea">
      <div class="vis-sea-inner"></div>
      <div class="vis-sea-radar"></div>
      <div class="vis-sea-text">
        <strong>{esc(banner_title)}</strong>
        {esc(banner_detail)}
      </div>
    </div>
"""

    caption_html = f'    <div class="img-cap">{esc(caption)}</div>\n' if caption else ""

    # First paragraph gets .lede class
    body_html = markdown_to_html(body)
    body_html = body_html.replace("<p>", '<p class="lede">', 1)

    return f"""
  <div class="section">
{header}
{banner}
{caption_html}
    {body_html}
  </div>
"""


def build_cyber_front(data, section_num=2):
    """Cyber Front section with scanline banner, APT badge, big stat, and body."""
    if not data:
        return ""

    body = data if isinstance(data, str) else data.get("body", "")
    banner_title = data.get("banner_title", "") if isinstance(data, dict) else ""
    banner_detail = data.get("banner_detail", "") if isinstance(data, dict) else ""
    caption = data.get("caption", "") if isinstance(data, dict) else ""
    badge = data.get("badge", {}) if isinstance(data, dict) else {}
    big_stat = data.get("big_stat", {}) if isinstance(data, dict) else {}

    header = build_section_header(section_num, "Cyber Operations", "The Cyber Front")

    # APT badge
    badge_html = ""
    if badge:
        badge_top = esc(badge.get("top", ""))
        badge_name = esc(badge.get("name", ""))
        badge_bottom = esc(badge.get("bottom", ""))
        badge_html = f"""
      <div class="cyber-badge">
        <span>{badge_top}</span>
        <span class="big">{badge_name}</span>
        <span>{badge_bottom}</span>
      </div>
"""

    banner = f"""
    <div class="vis-banner vis-cyber">
      <div class="vis-cyber-inner">
        <div class="cyber-dots"></div>
        <div class="cyber-line"></div>
        <div class="cyber-line"></div>
        <div class="cyber-line"></div>
        <div class="cyber-line"></div>
        <div class="cyber-line"></div>
      </div>
{badge_html}
      <div class="vis-cyber-text">
        <strong>{esc(banner_title)}</strong>
        {esc(banner_detail)}
      </div>
    </div>
"""

    caption_html = f'    <div class="img-cap">{esc(caption)}</div>\n' if caption else ""

    body_html = markdown_to_html(body)

    # Big stat callout
    stat_html = ""
    if big_stat:
        stat_val = esc(big_stat.get("value", ""))
        stat_title = esc(big_stat.get("title", ""))
        stat_desc = esc(big_stat.get("description", ""))
        stat_html = f"""
    <div class="big-stat">
      <div class="stat-num-block"><span>{stat_val}</span></div>
      <div class="stat-text-block">
        <strong>{stat_title}</strong>
        <p>{stat_desc}</p>
      </div>
    </div>
"""

    return f"""
  <div class="section">
{header}
{banner}
{caption_html}
    {body_html}
{stat_html}
  </div>
"""


def build_navigation_chart(data, section_num=3):
    """Navigation Chart section with region grid."""
    if not data:
        return ""

    header = build_section_header(section_num, "Regional Intelligence", "Navigation Chart")

    rows = []
    # "afr" and "africa" are aliases, so a document carrying both used to
    # render Africa twice. First one present wins.
    seen_regions = set()
    for key in ["gcc", "cis", "afr", "africa"]:
        region = data.get(key)
        if not region:
            continue
        canonical = "afr" if key in ("afr", "africa") else key
        if canonical in seen_regions:
            continue
        seen_regions.add(canonical)
        if isinstance(region, str):
            code = key.upper()
            name = key.upper()
            body = region
        else:
            code = esc(region.get("code", key.upper()))
            name = nl2br(region.get("name", key.upper()))
            body = region.get("body", "")

        body_html = markdown_to_html(body)
        # A `body_html.replace("<p>", '<p>', -1)` sat here under a comment
        # promising smaller text for region content. It replaced the string
        # with itself. The class it was meant to add was never written, so
        # removing the call changes no output; the promise stays unkept and
        # is now visible instead of hidden behind a line that looks like work.

        rows.append(f"""
      <div class="region-row">
        <div class="region-left">
          <span class="r-code">{code}</span>
          <span class="r-name">{name}</span>
        </div>
        <div class="region-right">
          {body_html}
        </div>
      </div>
""")

    return f"""
  <div class="section">
{header}
    <div class="region-table">
{"".join(rows)}
    </div>
  </div>
"""


def build_market_depth(data, section_num=4):
    """Market Depth section with bar chart, stats overlay, body, and pullquote."""
    if not data:
        return ""

    body = data if isinstance(data, str) else data.get("body", "")
    bars = data.get("bars", []) if isinstance(data, dict) else []
    stats = data.get("stats", []) if isinstance(data, dict) else []
    caption = data.get("caption", "") if isinstance(data, dict) else ""
    pullquote = data.get("pullquote", {}) if isinstance(data, dict) else {}
    market_caption_text = data.get("market_caption", "") if isinstance(data, dict) else ""

    header = build_section_header(section_num, "Capital Markets", "Market Depth")

    # Bar chart
    bar_html = ""
    if bars:
        bar_items = []
        for val in bars:
            # `val > 60` on a string raised TypeError, and a string like
            # "50;position:fixed" went into the style attribute verbatim.
            # A bar is a percentage or it is not drawn.
            try:
                pct = float(val)
            except (TypeError, ValueError):
                print(f"Warning: market_depth bar {val!r} is not a number; skipped",
                      file=sys.stderr)
                continue
            pct = max(0.0, min(100.0, pct))
            hi_class = " hi" if pct > 60 else ""
            bar_items.append(f'        <div class="chart-bar{hi_class}" '
                             f'style="height:{pct:g}%"></div>')
        bars_joined = "\n".join(bar_items)

        # Stats overlay
        stat_items = []
        for s in stats:
            style_class = s.get("style", "")
            stat_items.append(f"""
        <div class="mstat">
          <span class="mstat-val {esc(style_class)}">{esc(s.get("value", ""))}</span>
          <span class="mstat-lbl">{esc(s.get("label", ""))}</span>
        </div>""")
        stats_html = "\n".join(stat_items)

        mc_html = ""
        if market_caption_text:
            mc_html = f"""
      <div class="market-caption">
        {esc(market_caption_text).replace(chr(10), "<br/>")}
      </div>
"""

        bar_html = f"""
    <div class="vis-banner vis-markets">
      <div class="chart-wrap">
{bars_joined}
      </div>
      <div class="market-overlay"></div>
      <div class="market-stats">
{stats_html}
      </div>
{mc_html}
    </div>
"""

    caption_html = f'    <div class="img-cap">{esc(caption)}</div>\n' if caption else ""

    body_html = markdown_to_html(body)

    # Pullquote
    pq_html = ""
    if pullquote:
        pq_text = esc(pullquote.get("text", ""))
        pq_attr = esc(pullquote.get("attribution", ""))
        pq_html = f"""
    <div class="pullquote">
      <div class="pq-bg-num">&ldquo;</div>
      <div class="pq-text">&ldquo;{pq_text}&rdquo;</div>
      <div class="pq-attr">{pq_attr}</div>
    </div>
"""

    return f"""
  <div class="section">
{header}
{bar_html}
{caption_html}
    {body_html}
{pq_html}
  </div>
"""


def build_the_heading(data, section_num=5):
    """The Heading section - 31C perspective."""
    if not data:
        return ""

    # The fallback used to be `data` itself, so `the_heading: {"kicker": "x"}`
    # handed a dict to markdown_to_html, which called .strip() on it and
    # raised AttributeError with nothing catching it: no newsletter at all.
    body = data if isinstance(data, str) else str(data.get("body", "") or "")
    header = build_section_header(section_num, "31C Perspective", "The Heading")

    body_html = markdown_to_html(body)
    # First paragraph gets .lede class
    body_html = body_html.replace("<p>", '<p class="lede">', 1)

    return f"""
  <div class="section">
{header}
    {body_html}
  </div>
"""


def build_signal_watch(items, section_num=6):
    """Signal Watch section with numbered table."""
    if not items:
        return ""

    header = build_section_header(section_num, "Forward Indicators", "Signal Watch")

    rows = []
    for i, item in enumerate(items, 1):
        # Support markdown bold in signal items
        # `esc`, not `html.escape`. Every other text path in this file
        # coerces with `str()` first; this one line did not, and
        # `html.escape` calls `.replace` on what it is given -- so a bare
        # number in the JSON list ("signal_watch": ["...", 2026]) raised
        # AttributeError and no newsletter was produced at all.
        item_html = esc(item)
        item_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item_html)
        rows.append(f"""
      <div class="signal-row">
        <div class="sig-idx">{i:02d}</div>
        <div class="sig-text">{item_html}</div>
        <div class="sig-dot"><span></span></div>
      </div>
""")

    return f"""
  <div class="section">
{header}
    <div class="signal-table">
{"".join(rows)}
    </div>
  </div>
"""


def build_recommended_reading(items, section_num=7):
    """Recommended reading section with numbered list."""
    if not items:
        return ""

    header = build_section_header(section_num, "Further Reading", "Recommended")

    read_items = []
    for i, item in enumerate(items, 1):
        title = esc(item.get("title", ""))
        url = safe_url(item.get("url"))
        source = esc(item.get("source", ""))
        desc = esc(item.get("description", ""))
        source_line = source
        if desc:
            source_line += f" &nbsp;&middot;&nbsp; {desc}"
        read_items.append(f"""
      <div class="read-item">
        <span class="read-n">{i:02d}</span>
        <div>
          <a class="read-a" href="{url}" target="_blank">{title}</a>
          <div class="read-src">{source_line}</div>
        </div>
      </div>
""")

    return f"""
  <div class="section">
{header}
    <div class="reading">
{"".join(read_items)}
    </div>
  </div>
"""


def build_footer(logo_uri, issue_num, display_date):
    """Footer with logo, copyright, tagline, and issue number."""
    logo_html = ""
    if logo_uri:
        logo_html = f'<img src="{logo_uri}" alt="31C" class="logo-footer"/>'

    issue_str = f"{issue_num:03d}" if isinstance(issue_num, int) else str(issue_num)

    return f"""
<div class="footer">
  <div>
    {logo_html}
    <div class="ft-copy">&copy; 2026 31 Concept &nbsp;&middot;&nbsp; <a href="https://31c.io">31c.io</a></div>
    <div class="ft-tag">From Deep Packet Inspection to Deep Packet Intelligence</div>
  </div>
  <div class="ft-right">
    <span class="ft-iss">{issue_str}</span>
    <div class="ft-date">{esc(display_date)}</div>
  </div>
</div>
"""


# ============================================================
# CLI / Main
# ============================================================
def generate_newsletter(data, image_paths=None):
    """Generate the complete HTML newsletter from structured JSON data."""
    image_paths = image_paths or {}
    logo_uri = load_logo_base64(LOGO_PATH)
    date_str = data.get("date", datetime.now(get_default_tz()).strftime("%Y-%m-%d"))
    issue_num = data.get("issue_number", 1)
    threat_level = data.get("threat_level", "ELEVATED")
    regions = data.get("regions", ["GCC", "CIS", "Africa"])

    # Format display date
    try:
        # str() is the fix: a JSON number in `date` used to reach
        # fromisoformat as an int and raise TypeError, which nothing caught,
        # so the render died. With the coercion, TypeError is unreachable and
        # catching it would be dead code.
        dt = date.fromisoformat(str(date_str))
        display_date = dt.strftime("%d %B %Y")
    except ValueError:
        display_date = str(date_str)

    css = build_css()
    top_bar = build_top_bar(regions)
    masthead = build_masthead(logo_uri, display_date, issue_num, regions, threat_level)
    hero = build_hero(data.get("hero"))
    indicators = build_indicators(data.get("indicators"))

    # Resolve sea state image
    sea_image_uri = None
    if "sea_state" in image_paths:
        sea_image_uri = embed_image(image_paths["sea_state"])

    # Build content sections
    section_num = 1
    sections = []

    if data.get("sea_state"):
        sections.append(build_sea_state(data["sea_state"], section_num, sea_image_uri))
        section_num += 1

    if data.get("cyber_front"):
        sections.append(build_cyber_front(data["cyber_front"], section_num))
        section_num += 1

    if data.get("navigation_chart"):
        sections.append(build_navigation_chart(data["navigation_chart"], section_num))
        section_num += 1

    if data.get("market_depth"):
        sections.append(build_market_depth(data["market_depth"], section_num))
        section_num += 1

    if data.get("the_heading"):
        sections.append(build_the_heading(data["the_heading"], section_num))
        section_num += 1

    if data.get("signal_watch"):
        sections.append(build_signal_watch(data["signal_watch"], section_num))
        section_num += 1

    if data.get("recommended_reading"):
        sections.append(build_recommended_reading(data["recommended_reading"], section_num))
        section_num += 1

    sections_html = "\n".join(sections)

    footer = build_footer(logo_uri, issue_num, display_date)

    newsletter_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>31C Intelligence Briefing &mdash; {esc(display_date)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300;1,400;1,600&family=IBM+Plex+Mono:wght@300;400;500&display=swap" rel="stylesheet"/>
<style>
{css}
</style>
</head>
<body>
<div class="page">

<!-- TOP BAR -->
{top_bar}

<!-- MASTHEAD -->
{masthead}

  <!-- HERO -->
{hero}

  <!-- INDICATORS -->
{indicators}

<!-- CONTENT -->
<div class="content">
{sections_html}
</div><!-- /content -->

<!-- FOOTER -->
{footer}

</div><!-- /page -->
</body>
</html>"""

    return newsletter_html


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def safe_date_segment(raw):
    """An ISO date safe to use as one path segment. Missing -> today.

    `date.fromisoformat` alone is not enough: on 3.11 it also accepts the
    compact "20260824" form, which is a different string and a different
    directory. The shape is checked first, then the calendar.
    """
    if raw is None:
        return datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    text = str(raw).strip()
    if not _ISO_DATE_RE.match(text):
        raise SystemExit(f"date {raw!r} is not YYYY-MM-DD; refusing to use it "
                         f"as an output directory")
    try:
        date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError as exc:
        raise SystemExit(f"date {raw!r} is not a real date: {exc}") from exc
    return text


def count_words(html_text):
    """Rough word count from HTML by stripping tags."""
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = re.sub(r"&[a-z]+;", " ", text)
    words = text.split()
    return len(words)


def generate_pdf(html_path, pdf_path):
    """Generate a single-page PDF from the HTML newsletter using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Warning: playwright not installed. Skipping PDF generation.")
        print("  Install with: pip install playwright && python -m playwright install chromium")
        return False

    # Build file URI with proper encoding for paths with special characters
    # Path.as_uri() builds this correctly on both platforms. The old
    # "file:///" + abs_path produced file://// on POSIX, where abs_path
    # already starts with a slash. Chromium normalises it today.
    file_url = Path(html_path).resolve().as_uri()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(file_url)
            page.wait_for_load_state("networkidle")

            # Measure full page height for single-page output
            height = page.evaluate("document.documentElement.scrollHeight")

            page.pdf(
                path=str(pdf_path),
                width="750px",
                height=f"{height + 40}px",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            browser.close()
        print(f"PDF generated:  {pdf_path}")
        print(f"PDF size:       {Path(pdf_path).stat().st_size:,} bytes")
        return True
    except Exception as e:
        print(f"Warning: PDF generation failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="31C Intelligence Briefing HTML Generator (V3)")
    parser.add_argument("input_json", help="Path to input JSON file")
    parser.add_argument("--output-dir", help="Output directory (default: outputs/intel/newsletters/YYYY-MM-DD/)")
    parser.add_argument("--images", nargs="*", help="Image mappings: section=path (e.g. sea_state=/path/to/img.png)")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF generation")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Parse image paths
    image_paths = {}
    if args.images:
        for mapping in args.images:
            if "=" in mapping:
                section, path = mapping.split("=", 1)
                image_paths[section] = path

    # Determine output directory
    # `date` is a field of the input document, and it used to become a path
    # segment verbatim: "../../tmp/escape" wrote the briefing and its PDF
    # outside the newsletters tree, and mkdir(parents=True) created the way
    # there. A date is a date or the run stops.
    date_str = safe_date_segment(data.get("date"))
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = get_outputs_dir() / "intel" / "newsletters" / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "intelligence-briefing.html"

    newsletter_html = generate_newsletter(data, image_paths)
    output_path.write_text(newsletter_html, encoding="utf-8")

    word_count = count_words(newsletter_html)
    print(f"Newsletter generated: {output_path}")
    print(f"Word count: ~{word_count}")
    print(f"File size: {output_path.stat().st_size:,} bytes")

    # Generate PDF (single continuous page)
    if not args.no_pdf:
        pdf_path = output_dir / "intelligence-briefing.pdf"
        generate_pdf(output_path, pdf_path)


if __name__ == "__main__":
    main()
