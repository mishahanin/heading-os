#!/usr/bin/env python3
"""
31C Intelligence Briefing - HTML Newsletter Generator (V3)

Generates branded HTML newsletters matching the V3 editorial design system:
light cream theme, Bebas Neue / Crimson Pro / IBM Plex Mono typography,
CSS-only data visualizations, and newspaper-style layout.

Usage:
    python scripts/generate-newsletter-html.py <input.json> [--output-dir DIR] [--images section=path ...]

If --output-dir is omitted, saves to outputs/intel/newsletters/YYYY-MM-DD/
"""

import json
import sys
import base64
import html
import re
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.image import load_logo_base64
from scripts.utils.workspace import get_outputs_dir


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
        else:
            current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))

    result = []
    for para in paragraphs:
        p = html.escape(para)
        # Bold
        p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
        # Links
        p = re.sub(
            r"\[(.+?)\]\((.+?)\)",
            r'<a href="\2" target="_blank">\1</a>',
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
    return """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}

:root {
  --bg:      #F4F2EC;
  --white:   #FDFCFA;
  --ink:     #0C0C0B;
  --ink60:   #525250;
  --ink35:   #A0A09E;
  --ink12:   #E0DDD6;
  --orange:  #D93D06;
  --orlight: #FBF0EB;
  --green:   #175C30;
  --red:     #AA2208;
}

body {
  background: var(--bg);
  color: var(--ink);
  font-family: 'Crimson Pro', Georgia, serif;
  -webkit-font-smoothing: antialiased;
}

/* PAGE FRAME */
.page {
  max-width: 700px;
  margin: 0 auto;
  background: var(--white);
  border-left: 1px solid var(--ink12);
  border-right: 1px solid var(--ink12);
  border-bottom: 1px solid var(--ink12);
}

/* TOP BAR */
.topbar {
  background: var(--ink);
  padding: 8px 28px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.4);
}
.pulse {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--orange);
  flex-shrink: 0;
  animation: blink 2s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.3}}
.topbar-right {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.35);
}

/* MASTHEAD */
.masthead {
  border-bottom: 3px solid var(--ink);
}
.mast-row1 {
  display: flex;
  border-bottom: 1px solid var(--ink12);
}
.mast-brand {
  flex: 1;
  padding: 22px 28px 20px;
  border-right: 1px solid var(--ink12);
}
.logo-img {
  height: 24px;
  display: block;
  filter: brightness(0);
  margin-bottom: 10px;
}
.brand-sub {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: var(--orange);
}
.mast-right {
  width: 192px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}
.mast-meta {
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--ink12);
  flex: 1;
}
.mast-date {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--ink60);
  line-height: 1.9;
}
.threat {
  background: var(--orlight);
  border-top: 2px solid var(--orange);
  padding: 11px 20px;
  text-align: center;
}
.threat-lbl {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 7.5px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--ink35);
  display: block;
  margin-bottom: 3px;
}
.threat-val {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 24px;
  letter-spacing: 4px;
  color: var(--orange);
  display: block;
  line-height: 1;
}

/* HERO */
.hero {
  padding: 38px 28px 32px;
  border-bottom: 1px solid var(--ink12);
  position: relative;
  overflow: hidden;
}
.hero-bg {
  position: absolute;
  inset: 0;
  background:
    repeating-linear-gradient(0deg, transparent, transparent 39px, var(--ink12) 39px, var(--ink12) 40px),
    repeating-linear-gradient(90deg, transparent, transparent 39px, var(--ink12) 39px, var(--ink12) 40px);
  opacity: 0.45;
}
.hero-content { position: relative; z-index: 1; }
.hero-kicker {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--orange);
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
}
.hero-kicker::before {
  content: "";
  display: block;
  width: 20px; height: 2px;
  background: var(--orange);
}
.hero-title {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 82px;
  letter-spacing: 1.5px;
  line-height: 0.9;
  color: var(--ink);
  margin-bottom: 24px;
}
.hero-title .accent { color: var(--orange); }
.hero-deck {
  font-family: 'Crimson Pro', serif;
  font-style: italic;
  font-weight: 300;
  font-size: 18.5px;
  line-height: 1.58;
  color: var(--ink60);
  max-width: 540px;
  border-left: 3px solid var(--ink);
  padding-left: 18px;
}

/* INDICATORS */
.indicators {
  display: flex;
  border-bottom: 3px solid var(--ink);
}
.ind {
  flex: 1;
  padding: 13px 6px;
  text-align: center;
  border-right: 1px solid var(--ink12);
}
.ind:last-child { border-right: none; }
.ind-val {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 22px;
  letter-spacing: 0.5px;
  display: block;
  line-height: 1.05;
  color: var(--ink);
}
.ind-val.up     { color: var(--green); }
.ind-val.danger { color: var(--red); }
.ind-lbl {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 7px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--ink35);
  display: block;
  margin-top: 4px;
  line-height: 1.3;
}

/* CONTENT */
.content { padding: 0 28px; }

/* VISUAL BANNERS */
.vis-banner {
  width: 100%;
  height: 176px;
  display: block;
  margin-bottom: 0;
  position: relative;
  overflow: hidden;
}

/* Sea State banner */
.vis-sea { background: #09111F; }
.vis-sea-inner {
  position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 80% 60% at 70% 50%, rgba(217,61,6,0.12) 0%, transparent 70%),
    repeating-linear-gradient(0deg, transparent, transparent 19px, rgba(255,255,255,0.04) 19px, rgba(255,255,255,0.04) 20px),
    repeating-linear-gradient(90deg, transparent, transparent 19px, rgba(255,255,255,0.04) 19px, rgba(255,255,255,0.04) 20px);
}
.vis-sea-radar {
  position: absolute;
  right: 48px; top: 50%;
  transform: translateY(-50%);
  width: 120px; height: 120px;
  border-radius: 50%;
  border: 1px solid rgba(217,61,6,0.3);
  box-shadow: 0 0 0 30px rgba(217,61,6,0.04), 0 0 0 60px rgba(217,61,6,0.02);
}
.vis-sea-radar::after {
  content: "";
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%,-50%);
  width: 60px; height: 60px;
  border-radius: 50%;
  border: 1px solid rgba(217,61,6,0.25);
}
.vis-sea-radar::before {
  content: "";
  position: absolute;
  top: 50%; left: 50%;
  width: 2px; height: 50%;
  background: linear-gradient(to top, rgba(217,61,6,0.8), transparent);
  transform-origin: bottom center;
  transform: translate(-50%, -100%) rotate(35deg);
}
.vis-sea-text {
  position: absolute;
  left: 28px; bottom: 18px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.35);
}
.vis-sea-text strong {
  display: block;
  font-size: 11px;
  color: rgba(217,61,6,0.9);
  letter-spacing: 3px;
  margin-bottom: 3px;
  font-weight: 500;
}

/* Sea State with image */
.vis-sea-img {
  background: #09111F;
}
.vis-sea-img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  opacity: 0.7;
}
.vis-sea-img .vis-sea-text {
  z-index: 1;
}

/* Cyber banner */
.vis-cyber { background: #060B10; }
.vis-cyber-inner {
  position: absolute; inset: 0;
  overflow: hidden;
}
.cyber-line {
  position: absolute;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(0,200,120,0.5), transparent);
  animation: scanline 4s linear infinite;
}
.cyber-line:nth-child(1) { top: 28%;  width: 60%; left: 10%;  animation-delay: 0s;    animation-duration: 5s; }
.cyber-line:nth-child(2) { top: 52%;  width: 40%; left: 30%;  animation-delay: 1.5s;  animation-duration: 4s; }
.cyber-line:nth-child(3) { top: 72%;  width: 50%; left: 20%;  animation-delay: 0.8s;  animation-duration: 6s; }
.cyber-line:nth-child(4) { top: 16%;  width: 30%; left: 50%;  animation-delay: 2.2s;  animation-duration: 3.5s; }
.cyber-line:nth-child(5) { top: 86%;  width: 45%; left: 15%;  animation-delay: 3s;    animation-duration: 5.5s; }
@keyframes scanline {
  0%   { opacity: 0; transform: scaleX(0); transform-origin: left; }
  30%  { opacity: 1; }
  70%  { opacity: 1; }
  100% { opacity: 0; transform: scaleX(1); transform-origin: left; }
}
.cyber-dots {
  position: absolute; inset: 0;
  background-image: radial-gradient(circle, rgba(0,200,120,0.2) 1px, transparent 1px);
  background-size: 28px 28px;
}
.vis-cyber-text {
  position: absolute;
  left: 28px; bottom: 18px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: rgba(0,200,120,0.4);
}
.vis-cyber-text strong {
  display: block;
  font-size: 11px;
  color: rgba(0,200,120,0.85);
  letter-spacing: 3px;
  margin-bottom: 3px;
  font-weight: 500;
}
.cyber-badge {
  position: absolute;
  top: 20px; right: 24px;
  border: 1px solid rgba(217,61,6,0.5);
  padding: 8px 14px;
  background: rgba(217,61,6,0.08);
}
.cyber-badge span {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 7.5px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: rgba(217,61,6,0.8);
  display: block;
  line-height: 1.7;
}
.cyber-badge span.big {
  font-size: 10px;
  font-weight: 500;
  color: rgba(217,61,6,1);
  letter-spacing: 3px;
}

/* Markets banner */
.vis-markets {
  background: #0A0A08;
  position: relative;
  padding: 0;
}
.chart-wrap {
  position: absolute; inset: 0;
  display: flex;
  align-items: flex-end;
  padding: 0 28px 0;
  gap: 3px;
}
.chart-bar {
  flex: 1;
  background: rgba(217,61,6,0.12);
  border-top: 1px solid rgba(217,61,6,0.3);
}
.chart-bar.hi {
  background: rgba(217,61,6,0.25);
  border-top: 1px solid rgba(217,61,6,0.7);
}
.market-overlay {
  position: absolute; inset: 0;
  background: linear-gradient(to bottom, transparent 60%, rgba(10,10,8,0.7));
}
.market-stats {
  position: absolute;
  bottom: 18px; left: 28px;
  display: flex;
  gap: 28px;
}
.mstat-val {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 20px;
  letter-spacing: 1px;
  display: block;
  line-height: 1;
}
.mstat-val.up { color: #4ADE80; }
.mstat-val.dn { color: var(--orange); }
.mstat-val.gold { color: #C8A84B; }
.mstat-lbl {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 7.5px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.3);
  display: block;
  margin-top: 2px;
}
.market-caption {
  position: absolute;
  right: 24px; bottom: 18px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.25);
  text-align: right;
  line-height: 1.6;
}

.img-cap {
  background: var(--ink);
  color: rgba(255,255,255,0.42);
  font-family: 'IBM Plex Mono', monospace;
  font-size: 7.5px;
  letter-spacing: 1.2px;
  text-transform: uppercase;
  padding: 6px 14px;
  margin-bottom: 22px;
}

/* SECTIONS */
.section {
  padding: 32px 0 30px;
  border-bottom: 1px solid var(--ink12);
}
.section:last-of-type { border-bottom: none; }

.sec-header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 22px;
}
.sec-num {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 52px;
  line-height: 1;
  color: var(--ink12);
  letter-spacing: 1px;
  flex-shrink: 0;
  user-select: none;
}
.sec-title-block {
  border-left: 2px solid var(--ink);
  padding-left: 14px;
  padding-top: 6px;
}
.sec-kicker {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: var(--orange);
  display: block;
  margin-bottom: 3px;
}
.sec-title {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 30px;
  letter-spacing: 1.5px;
  color: var(--ink);
  line-height: 1;
  display: block;
}

/* BODY TEXT */
p {
  font-family: 'Crimson Pro', serif;
  font-size: 16px;
  line-height: 1.76;
  color: var(--ink60);
  margin-bottom: 13px;
}
p:last-child { margin-bottom: 0; }
p strong { color: var(--ink); font-weight: 600; }
.lede {
  font-size: 18.5px !important;
  color: var(--ink) !important;
  font-weight: 300;
  line-height: 1.65 !important;
}
a { color: var(--orange); }

/* LISTS */
ul {
  margin: 0 0 14px 0;
  padding-left: 20px;
  list-style: none;
}
li {
  font-family: 'Crimson Pro', serif;
  font-size: 16px;
  line-height: 1.76;
  color: var(--ink60);
  margin-bottom: 6px;
}

/* BIG STAT */
.big-stat {
  display: flex;
  margin: 26px 0 4px;
  border: 1px solid var(--ink);
}
.stat-num-block {
  background: var(--ink);
  padding: 22px 26px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-num-block span {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 76px;
  line-height: 1;
  letter-spacing: -1px;
  color: var(--white);
}
.stat-text-block {
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
}
.stat-text-block strong {
  font-family: 'Crimson Pro', serif;
  font-weight: 600;
  font-size: 16.5px;
  color: var(--ink);
  display: block;
  line-height: 1.3;
}
.stat-text-block p {
  font-size: 14px !important;
  color: var(--ink35) !important;
  margin: 0 !important;
  line-height: 1.5 !important;
}

/* REGIONS */
.region-table {
  border: 1px solid var(--ink12);
  margin-top: 4px;
}
.region-row {
  display: grid;
  grid-template-columns: 90px 1fr;
  border-bottom: 1px solid var(--ink12);
}
.region-row:last-child { border-bottom: none; }
.region-left {
  padding: 16px 18px;
  border-right: 1px solid var(--ink12);
  background: #FAFAF6;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  padding-top: 18px;
}
.r-code {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 30px;
  letter-spacing: 1px;
  color: var(--ink);
  line-height: 1;
  display: block;
}
.r-name {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 7px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--ink35);
  display: block;
  margin-top: 4px;
  line-height: 1.5;
}
.region-right {
  padding: 16px 20px;
}
.region-right p {
  font-size: 14.5px;
  line-height: 1.7;
  margin-bottom: 9px;
  color: var(--ink60);
}
.region-right p:last-child { margin-bottom: 0; }

/* PULLQUOTE */
.pullquote {
  background: var(--ink);
  margin: 28px 0 4px;
  padding: 26px 28px;
  position: relative;
  overflow: hidden;
}
.pq-bg-num {
  position: absolute;
  right: -8px; top: -20px;
  font-family: 'Bebas Neue', sans-serif;
  font-size: 160px;
  line-height: 1;
  color: rgba(255,255,255,0.04);
  user-select: none;
  letter-spacing: -4px;
}
.pq-text {
  font-family: 'Crimson Pro', serif;
  font-style: italic;
  font-weight: 300;
  font-size: 20px;
  line-height: 1.55;
  color: rgba(255,255,255,0.92);
  margin-bottom: 16px;
  position: relative;
  z-index: 1;
}
.pq-attr {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: rgba(255,255,255,0.35);
  position: relative; z-index: 1;
  display: flex;
  align-items: center;
  gap: 10px;
}
.pq-attr::before {
  content: "";
  display: block;
  width: 16px; height: 1px;
  background: rgba(255,255,255,0.25);
}

/* SIGNALS */
.signal-table {
  border: 1px solid var(--ink12);
  margin-top: 4px;
}
.signal-row {
  display: flex;
  align-items: stretch;
  border-bottom: 1px solid var(--ink12);
}
.signal-row:last-child { border-bottom: none; }
.sig-idx {
  width: 46px;
  flex-shrink: 0;
  border-right: 1px solid var(--ink12);
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 1px;
  color: var(--ink35);
  display: flex;
  align-items: center;
  justify-content: center;
  background: #FAFAF6;
}
.sig-text {
  padding: 13px 18px;
  font-family: 'Crimson Pro', serif;
  font-size: 15.5px;
  line-height: 1.65;
  color: var(--ink60);
  flex: 1;
}
.sig-text strong { color: var(--ink); font-weight: 600; }
.sig-dot {
  width: 40px;
  flex-shrink: 0;
  border-left: 1px solid var(--ink12);
  display: flex;
  align-items: center;
  justify-content: center;
}
.sig-dot span {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--orange);
  display: block;
}

/* READING LIST */
.reading { margin-top: 4px; }
.read-item {
  display: flex;
  gap: 16px;
  padding: 15px 0;
  border-bottom: 1px solid var(--ink12);
  align-items: flex-start;
}
.read-item:last-child { border-bottom: none; }
.read-n {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 1px;
  color: var(--ink35);
  flex-shrink: 0;
  margin-top: 3px;
  min-width: 22px;
}
.read-a {
  font-family: 'Crimson Pro', serif;
  font-weight: 600;
  font-size: 16px;
  color: var(--ink);
  text-decoration: none;
  display: block;
  margin-bottom: 3px;
  line-height: 1.3;
}
.read-a:hover { color: var(--orange); text-decoration: underline; }
.read-src {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  letter-spacing: 1px;
  color: var(--ink35);
  text-transform: uppercase;
}

/* FOOTER */
.footer {
  border-top: 3px solid var(--ink);
  background: var(--white);
  padding: 22px 28px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
}
.logo-footer {
  height: 20px;
  display: block;
  filter: brightness(0);
  margin-bottom: 9px;
  opacity: 0.85;
}
.ft-copy {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--ink35);
}
.ft-copy a { color: var(--ink60); text-decoration: none; }
.ft-tag {
  font-family: 'Crimson Pro', serif;
  font-style: italic;
  font-size: 13px;
  color: var(--ink35);
  margin-top: 4px;
}
.ft-right { text-align: right; }
.ft-iss {
  font-family: 'Bebas Neue', sans-serif;
  font-size: 40px;
  letter-spacing: 2px;
  color: var(--ink12);
  line-height: 1;
  display: block;
}
.ft-date {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--ink35);
  margin-top: 2px;
}
"""


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
"""


def build_hero(hero_data):
    """Hero section with large title, accent word, and deck text."""
    if not hero_data:
        return ""

    kicker = esc(hero_data.get("kicker", "Intelligence Briefing"))
    title_raw = hero_data.get("title", "")
    accent_word = hero_data.get("accent_word", "")
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
    for key in ["gcc", "cis", "afr", "africa"]:
        region = data.get(key)
        if not region:
            continue
        if isinstance(region, str):
            code = key.upper()
            name = key.upper()
            body = region
        else:
            code = esc(region.get("code", key.upper()))
            name = nl2br(region.get("name", key.upper()))
            body = region.get("body", "")

        body_html = markdown_to_html(body)
        # Use smaller text for region content
        body_html = body_html.replace("<p>", '<p>', -1)

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
        for i, val in enumerate(bars):
            hi_class = " hi" if val > 60 else ""
            bar_items.append(f'        <div class="chart-bar{hi_class}" style="height:{val}%"></div>')
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

    body = data if isinstance(data, str) else data.get("body", data)
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
        item_html = html.escape(item)
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
        url = item.get("url", "#")
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
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    issue_num = data.get("issue_number", 1)
    threat_level = data.get("threat_level", "ELEVATED")
    regions = data.get("regions", ["GCC", "CIS", "Africa"])

    # Format display date
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        display_date = dt.strftime("%d %B %Y")
    except ValueError:
        display_date = date_str

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
</div>

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
        from urllib.parse import quote
    except ImportError:
        print("Warning: playwright not installed. Skipping PDF generation.")
        print("  Install with: pip install playwright && python -m playwright install chromium")
        return False

    # Build file URI with proper encoding for paths with special characters
    abs_path = str(Path(html_path).resolve()).replace("\\", "/")
    file_url = "file:///" + quote(abs_path, safe=":/")

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
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
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
