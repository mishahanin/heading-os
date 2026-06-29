#!/usr/bin/env python3
"""31C Document Parser with Spatial Bounding Boxes (LiteParse wrapper).

Parse documents (PDF, DOCX, PPTX, XLSX) extracting text with bounding box
coordinates. Generate visual citation reports with 31C branding.

Usage:
    python scripts/docparse.py setup --check
    python scripts/docparse.py setup --install
    python scripts/docparse.py parse --files doc.pdf [--pages 1-5] [--dpi 150] --output-json out.json
    python scripts/docparse.py report --parse-json parsed.json --citations citations.json [--output-dir DIR]
    python scripts/docparse.py status
    python scripts/docparse.py clear-cache [--force] [--file doc.pdf]

Prerequisites:
    Node.js 18+  (https://nodejs.org/)
    npm install -g @llamaindex/liteparse
    pip install liteparse==2.0.0
"""

import argparse
import base64
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import BOLD, CYAN, GREEN, GRAY, RED, RESET, YELLOW
from scripts.utils.workspace import get_workspace_root, get_outputs_dir

WORKSPACE = get_workspace_root()
CACHE_DIR = WORKSPACE / ".cache" / "docparse"
DEFAULT_DPI = 150
DEFAULT_OUTPUT_DIR = get_outputs_dir() / "intel" / "docparse"
CACHE_TTL_HOURS = 168  # 7 days
MAX_REPORT_PAGES = 20

# Supported file extensions for auto-discovery
LITEPARSE_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".doc", ".odt", ".rtf", ".odp", ".ods",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
}
PLAINTEXT_EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".tsv"}


# ============================================================
# Cache Helpers
# ============================================================

def _cache_key(file_path: Path, password: str = "") -> str:
    """Compute cache key from resolved path, size, mtime, and optional password.

    Uses Path.resolve() which normalizes case on Windows NTFS.
    """
    stat = file_path.stat()
    raw = f"{file_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    if password:
        raw += f":{hashlib.sha256(password.encode()).hexdigest()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    """Return cached parse result if exists and not expired."""
    cache_file = CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        parsed_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01T00:00:00+00:00"))
        age_hours = (datetime.now(timezone.utc) - parsed_at).total_seconds() / 3600
        if age_hours > CACHE_TTL_HOURS:
            print(f"  {GRAY}Cache expired ({age_hours:.0f}h old), regenerating{RESET}", file=sys.stderr)
            cache_file.unlink(missing_ok=True)
            return None
        return data
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"  {YELLOW}Warning:{RESET} Cache entry corrupt, regenerating: {e}", file=sys.stderr)
        cache_file.unlink(missing_ok=True)
        return None


def _cache_put(key: str, data: dict) -> None:
    """Write parse result to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["_cached_at"] = datetime.now(timezone.utc).isoformat()
    (CACHE_DIR / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ============================================================
# Core Functions (importable by other scripts)
# ============================================================

def parse_document(
    file_path: Path | str,
    pages: str | None = None,
    dpi: int = DEFAULT_DPI,
    password: str | None = None,
    no_cache: bool = False,
) -> dict:
    """Parse a document and return structured dict. Handles caching internally.

    Args:
        file_path: Path to the document file.
        pages: Page range string, e.g. "1-5,10". None = all pages.
        dpi: Render resolution (default 150).
        password: Document password if encrypted.
        no_cache: Skip cache lookup if True.

    Returns:
        Dict with keys: file, file_name, parsed_at, dpi, pages (list of page dicts).
        Each page dict has: page_num, width_pt, height_pt, text, text_items.
    """
    from liteparse import LiteParse

    fp = Path(file_path).resolve()
    if not fp.exists():
        raise FileNotFoundError(f"File not found: {fp}")

    pwd = password or ""
    key = _cache_key(fp, pwd)

    if not no_cache:
        cached = _cache_get(key)
        if cached:
            cached["_cache_hit"] = True
            return cached

    # liteparse 2.0: dpi/target_pages/password moved into constructor;
    # parse() takes only the file path; cli_path keyword removed (bindings
    # locate the CLI internally). Page attrs are snake_case: page_num,
    # text_items; dimensions are .width / .height (no _pt suffix).
    parser_kwargs: dict[str, object] = {"dpi": float(dpi)}
    if pages:
        parser_kwargs["target_pages"] = pages
    if password:
        parser_kwargs["password"] = password
    parser = LiteParse(**parser_kwargs)

    result = parser.parse(str(fp))

    doc = {
        "file": str(fp),
        "file_name": fp.name,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "dpi": dpi,
        "pages": [],
        "_cache_hit": False,
    }

    for page in result.pages:
        items = []
        for ti in page.text_items:
            items.append({
                "text": ti.text,
                "x": ti.x,
                "y": ti.y,
                "width": ti.width,
                "height": ti.height,
            })
        doc["pages"].append({
            "page_num": page.page_num,
            "width_pt": page.width,
            "height_pt": page.height,
            "text": page.text,
            "text_items": items,
        })

    _cache_put(key, doc)
    return doc


def find_boxes_for_quote(
    text_items: list[dict],
    quote: str,
    dpi: int = DEFAULT_DPI,
) -> list[dict]:
    """Find bounding boxes for a verbatim quote by string matching against textItems.

    Concatenates all textItem texts, does case-insensitive substring search,
    maps matched character range back to source textItems, and converts
    PDF points to pixels.

    Args:
        text_items: List of dicts with keys: text, x, y, width, height.
        quote: The verbatim quote to find.
        dpi: DPI for pixel coordinate conversion.

    Returns:
        List of dicts with keys: x, y, width, height (in pixels).
        Empty list if quote not found.
    """
    if not text_items or not quote:
        return []

    scale = dpi / 72.0

    # Build concatenated text with character-to-item index mapping.
    # Preserve raw text (including trailing spaces) during concatenation,
    # then normalize the full string and the quote identically.
    raw_chars = []
    char_to_item = []  # maps each char index to (item_index)
    for idx, item in enumerate(text_items):
        raw = item.get("text", "")
        for ch in raw:
            raw_chars.append(ch)
            char_to_item.append(idx)

    raw_concat = "".join(raw_chars)
    # Apply normalization to both the full text and the quote
    norm_concat = _normalize_text(raw_concat).lower()
    norm_quote = _normalize_text(quote).lower()

    # Rebuild char_to_item mapping after normalization (whitespace collapsing
    # can shift indices). We re-walk the raw text applying the same transforms.
    norm_chars = []
    norm_char_to_item = []
    prev_space = False
    for i, ch in enumerate(raw_chars):
        # Apply same normalization as _normalize_text inline
        nch = ch
        for old, new in _REPLACEMENTS.items():
            if ch == old:
                nch = new
                break
        if nch == "":
            continue  # soft hyphen removed
        for c in nch:  # ligatures expand to multiple chars
            if c in " \t\n\r":
                if not prev_space:
                    norm_chars.append(" ")
                    norm_char_to_item.append(char_to_item[i])
                prev_space = True
            else:
                norm_chars.append(c)
                norm_char_to_item.append(char_to_item[i])
                prev_space = False

    # Strip leading/trailing spaces from the normalized sequence
    while norm_chars and norm_chars[0] == " ":
        norm_chars.pop(0)
        norm_char_to_item.pop(0)
    while norm_chars and norm_chars[-1] == " ":
        norm_chars.pop()
        norm_char_to_item.pop()

    concat = "".join(norm_chars).lower()
    pos = concat.find(norm_quote)
    if pos == -1:
        return []

    # Find which items are involved in the match
    matched_items = set()
    for i in range(pos, pos + len(norm_quote)):
        if i < len(norm_char_to_item):
            matched_items.add(norm_char_to_item[i])

    # Collect and merge bounding boxes
    boxes = []
    for item_idx in sorted(matched_items):
        item = text_items[item_idx]
        boxes.append({
            "x": item["x"] * scale,
            "y": item["y"] * scale,
            "width": item["width"] * scale,
            "height": item["height"] * scale,
        })

    return _merge_adjacent_boxes(boxes)


_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'",  # smart single quotes
    "\u201c": '"', "\u201d": '"',  # smart double quotes
    "\u2013": "-", "\u2014": "-",  # en/em dashes
    "\u00ad": "",                   # soft hyphen
    "\u00a0": " ",                  # non-breaking space
    "\ufb01": "fi", "\ufb02": "fl", # ligatures
    "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl",
}


def _normalize_text(text: str) -> str:
    """Normalize typographic variations for matching."""
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _merge_adjacent_boxes(boxes: list[dict], threshold: float = 5.0) -> list[dict]:
    """Merge boxes that are on the same line (similar y) into wider boxes."""
    if not boxes:
        return []

    # Group by approximate y position
    lines: dict[int, list[dict]] = {}
    for box in boxes:
        y_key = round(box["y"] / threshold)
        lines.setdefault(y_key, []).append(box)

    merged = []
    for _y_key, line_boxes in sorted(lines.items()):
        line_boxes.sort(key=lambda b: b["x"])
        current = dict(line_boxes[0])
        for box in line_boxes[1:]:
            gap = box["x"] - (current["x"] + current["width"])
            if gap < threshold * 2:
                # Extend current box
                new_right = box["x"] + box["width"]
                current["width"] = new_right - current["x"]
                current["height"] = max(current["height"], box["height"])
            else:
                merged.append(current)
                current = dict(box)
        merged.append(current)

    return merged


# ============================================================
# Rendering / HTML Report Generation
# ============================================================

def _generate_report_html(
    question: str,
    answer_md: str,
    citations: list[dict],
    page_screenshots: dict,  # {(file, page_num): bytes}
    parse_data: dict,        # full parse JSON
    title: str = "Document Analysis Report",
) -> str:
    """Generate self-contained 31C-branded HTML report with visual citations."""
    from scripts.utils.image import load_logo_base64

    logo_b64 = load_logo_base64()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    files_str = ", ".join(set(c.get("file", "?") for c in citations))
    pages_cited = len(page_screenshots)
    answer_html = _markdown_to_html(answer_md)

    # Build citation cards HTML
    cards_html = []
    for cit in citations:
        cit_id = cit.get("id", "?")
        file_name = cit.get("file", "unknown")
        page_num = cit.get("page", 0)
        quote_text = cit.get("quote", "")
        relevance = cit.get("relevance", "")
        screenshot_key = (file_name, page_num)

        # Find bounding boxes for this quote
        page_data = _find_page_in_parse(parse_data, file_name, page_num)
        dpi = DEFAULT_DPI
        if page_data:
            boxes = find_boxes_for_quote(page_data.get("text_items", []), quote_text, dpi)
            page_w_px = page_data["width_pt"] * (dpi / 72.0)
            page_h_px = page_data["height_pt"] * (dpi / 72.0)
        else:
            boxes = []
            page_w_px = 800
            page_h_px = 600

        # Screenshot image
        img_bytes = page_screenshots.get(screenshot_key)
        if img_bytes:
            img_b64 = base64.b64encode(img_bytes).decode("ascii")
            img_src = f"data:image/jpeg;base64,{img_b64}"
        else:
            img_src = ""

        # SVG overlay for highlight boxes
        svg_rects = ""
        for box in boxes:
            svg_rects += (
                f'<rect x="{box["x"]:.1f}" y="{box["y"]:.1f}" '
                f'width="{box["width"]:.1f}" height="{box["height"]:.1f}" '
                f'fill="rgba(91,95,255,0.2)" stroke="#5B5FFF" stroke-width="2"/>\n'
            )

        card = f"""
    <section class="citation-card" id="cite-{cit_id}">
      <div class="card-header">
        <span class="cite-num">[{cit_id}]</span>
        <span class="cite-source">{html.escape(file_name)} - Page {page_num}</span>
      </div>
      <div class="card-body">
        <div class="page-view">
          {"" if not img_src else f'''<div class="page-image-container">
            <img src="{img_src}" class="page-image" alt="Page {page_num}">
            <svg class="highlight-overlay" viewBox="0 0 {page_w_px:.0f} {page_h_px:.0f}"
                 preserveAspectRatio="none">
              {svg_rects}
            </svg>
          </div>'''}
          <div class="page-label">Page {page_num}</div>
        </div>
        <div class="finding-panel">
          <div class="quote-block">
            <div class="quote-label">Cited Text</div>
            <blockquote>{html.escape(quote_text)}</blockquote>
          </div>
          <div class="relevance-block">
            <div class="relevance-label">Relevance</div>
            <p>{html.escape(relevance)}</p>
          </div>
        </div>
      </div>
    </section>"""
        cards_html.append(card)

    citations_block = "\n".join(cards_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - 31C DocParse</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

  :root {{
    --bg: #0C0C0F;
    --surface: #16161A;
    --surface-2: #1E1E24;
    --border: #2A2A32;
    --text: #E8E8ED;
    --text-muted: #8B8B96;
    --accent: #5B5FFF;
    --accent-light: rgba(91, 95, 255, 0.15);
    --accent-border: rgba(91, 95, 255, 0.4);
    --orange: #E8872A;
    --radius: 8px;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 0;
  }}

  .container {{ max-width: 1100px; margin: 0 auto; padding: 40px 24px; }}

  header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 24px;
    margin-bottom: 32px;
  }}
  header .logo {{ height: 28px; margin-bottom: 16px; }}
  header h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 8px; }}
  header .meta {{ color: var(--text-muted); font-size: 13px; }}

  .question-box {{
    background: var(--surface);
    border-left: 3px solid var(--accent);
    padding: 16px 20px;
    border-radius: 0 var(--radius) var(--radius) 0;
    margin-bottom: 24px;
  }}
  .question-box .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-muted); margin-bottom: 6px; }}
  .question-box .text {{ font-size: 16px; font-style: italic; }}

  .answer-section {{
    background: var(--surface);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 40px;
  }}
  .answer-section h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-muted); margin-bottom: 12px; }}
  .answer-section .answer-body {{ font-size: 15px; }}
  .answer-section .answer-body p {{ margin-bottom: 12px; }}
  .answer-section .answer-body strong {{ color: var(--orange); }}

  .citations-header {{
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text-muted);
    margin-bottom: 20px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}

  .citation-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 20px;
    overflow: hidden;
  }}
  .card-header {{
    background: var(--surface-2);
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }}
  .cite-num {{
    display: inline-block;
    background: var(--accent);
    color: #fff;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 12px;
    margin-right: 8px;
  }}
  .cite-source {{ color: var(--text-muted); }}

  .card-body {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
  }}
  @media (max-width: 800px) {{
    .card-body {{ grid-template-columns: 1fr; }}
  }}

  .page-view {{
    padding: 16px;
    border-right: 1px solid var(--border);
  }}
  .page-image-container {{
    position: relative;
    width: 100%;
    background: #222;
    border-radius: 4px;
    overflow: hidden;
  }}
  .page-image {{
    width: 100%;
    height: auto;
    display: block;
  }}
  .highlight-overlay {{
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
  }}
  .page-label {{
    text-align: center;
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 8px;
  }}

  .finding-panel {{ padding: 16px; }}
  .quote-block, .relevance-block {{ margin-bottom: 16px; }}
  .quote-label, .relevance-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text-muted);
    margin-bottom: 6px;
  }}
  blockquote {{
    background: var(--accent-light);
    border-left: 2px solid var(--accent);
    padding: 10px 14px;
    border-radius: 0 4px 4px 0;
    font-size: 14px;
    font-style: italic;
  }}
  .relevance-block p {{ font-size: 13px; color: var(--text-muted); }}

  footer {{
    border-top: 1px solid var(--border);
    padding-top: 16px;
    margin-top: 40px;
    text-align: center;
    font-size: 11px;
    color: var(--text-muted);
  }}
</style>
</head>
<body>
<div class="container">
  <header>
    {"" if not logo_b64 else f'<img src="{logo_b64}" class="logo" alt="31C">'}
    <h1>{html.escape(title)}</h1>
    <div class="meta">{html.escape(files_str)} | {pages_cited} pages cited | {len(citations)} citations | {now}</div>
  </header>

  <div class="question-box">
    <div class="label">Question</div>
    <div class="text">{html.escape(question)}</div>
  </div>

  <div class="answer-section">
    <h2>Answer</h2>
    <div class="answer-body">{answer_html}</div>
  </div>

  <div class="citations-header">Sources ({len(citations)} citations)</div>
  {citations_block}

  <footer>
    Generated by 31C DocParse | {now}
  </footer>
</div>
</body>
</html>"""


def _find_page_in_parse(parse_data: dict, file_name: str, page_num: int) -> dict | None:
    """Find a specific page in the parse data by file name and page number."""
    for f in parse_data.get("files", []):
        if f.get("file_name") == file_name or Path(f.get("file", "")).name == file_name:
            for p in f.get("pages", []):
                if p.get("page_num") == page_num:
                    return p
    return None


def _markdown_to_html(md: str) -> str:
    """Minimal markdown to HTML conversion for answer text."""
    text = html.escape(md)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline citation markers [N] -> linked anchors (only 1-3 digit numbers
    # to avoid matching unrelated bracketed numbers like version [2048])
    text = re.sub(r"\[(\d{1,3})\]", r'<a href="#cite-\1" class="cite-ref">[\1]</a>', text)
    # Paragraphs
    paragraphs = text.split("\n\n")
    if len(paragraphs) > 1:
        text = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())
    else:
        text = f"<p>{text}</p>"
    return text


# ============================================================
# Subcommand: setup
# ============================================================

def cmd_setup(args):
    """Check or install prerequisites."""
    if args.install:
        _setup_install()
    else:
        sys.exit(0 if _setup_check() else 1)


def _setup_check() -> bool:
    """Verify all prerequisites. Returns True if all pass."""
    all_ok = True

    # Node.js
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10
        )
        version = result.stdout.strip().lstrip("v")
        major = int(version.split(".")[0])
        if major >= 18:
            print(f"  {GREEN}OK{RESET}  Node.js {version}")
        else:
            print(f"  {RED}FAIL{RESET}  Node.js {version} (need 18+)")
            all_ok = False
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        print(f"  {RED}FAIL{RESET}  Node.js not found")
        print(f"         Install: https://nodejs.org/")
        all_ok = False

    # LiteParse CLI
    cli = shutil.which("liteparse")
    if cli:
        print(f"  {GREEN}OK{RESET}  LiteParse CLI: {cli}")
    else:
        print(f"  {RED}FAIL{RESET}  LiteParse CLI not found")
        print(f"         Install: npm install -g @llamaindex/liteparse")
        all_ok = False

    # Python package
    try:
        import liteparse
        print(f"  {GREEN}OK{RESET}  liteparse Python package installed")
    except ImportError:
        print(f"  {RED}FAIL{RESET}  liteparse Python package not installed")
        print(f"         Install: pip install liteparse==2.0.0")
        all_ok = False

    if all_ok:
        print(f"\n{GREEN}All prerequisites met.{RESET}")
    else:
        print(f"\n{RED}Some prerequisites missing. Run: python scripts/docparse.py setup --install{RESET}")

    return all_ok


def _setup_install():
    """Install missing prerequisites (idempotent)."""
    # LiteParse CLI
    cli = shutil.which("liteparse")
    if cli:
        print(f"  {GREEN}OK{RESET}  LiteParse CLI already installed: {cli}")
    else:
        print(f"  {CYAN}Installing{RESET} @llamaindex/liteparse globally...")
        result = subprocess.run(
            ["npm", "install", "-g", "@llamaindex/liteparse"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f"  {GREEN}OK{RESET}  LiteParse CLI installed")
        else:
            print(f"  {RED}FAIL{RESET}  npm install failed: {result.stderr.strip()}")
            sys.exit(1)

    # Python package
    try:
        import liteparse
        print(f"  {GREEN}OK{RESET}  liteparse Python package already installed")
    except ImportError:
        print(f"  {CYAN}Installing{RESET} liteparse Python package...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "liteparse==1.2.1"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f"  {GREEN}OK{RESET}  liteparse Python package installed")
        else:
            print(f"  {RED}FAIL{RESET}  pip install failed: {result.stderr.strip()}")
            sys.exit(1)

    print(f"\n{GREEN}Setup complete.{RESET} Run: python scripts/docparse.py setup --check")


# ============================================================
# Subcommand: parse
# ============================================================

def cmd_parse(args):
    """Parse one or more documents."""
    results = {"files": [], "summary": {}}
    t0 = time.time()
    cache_hits = 0

    for file_str in args.files:
        fp = Path(file_str).resolve()
        if not fp.exists():
            print(f"  {RED}SKIP{RESET}  {file_str} (not found)", file=sys.stderr)
            continue

        # Auto-discover if directory
        if fp.is_dir():
            files = [
                f for f in sorted(fp.iterdir())
                if f.suffix.lower() in LITEPARSE_EXTENSIONS
            ]
        else:
            files = [fp]

        for f in files:
            try:
                doc = parse_document(
                    f, pages=args.pages, dpi=args.dpi,
                    password=args.password, no_cache=args.no_cache,
                )
                hit = doc.pop("_cache_hit", False)
                doc.pop("_cached_at", None)
                if hit:
                    cache_hits += 1
                results["files"].append(doc)
                status = f"{GREEN}CACHE HIT{RESET}" if hit else f"{CYAN}PARSED{RESET}"
                n_pages = len(doc.get("pages", []))
                n_items = sum(len(p.get("text_items", [])) for p in doc.get("pages", []))
                print(
                    f"  {status}  {doc['file_name']} ({n_pages} pages, {n_items} items)",
                    file=sys.stderr,
                )
            except FileNotFoundError:
                print(f"  {RED}SKIP{RESET}  {f.name} (not found)", file=sys.stderr)
            except Exception as e:
                print(f"  {RED}ERROR{RESET}  {f.name}: {e}", file=sys.stderr)

    elapsed = time.time() - t0
    results["summary"] = {
        "total_files": len(results["files"]),
        "total_pages": sum(len(f.get("pages", [])) for f in results["files"]),
        "cache_hits": cache_hits,
        "elapsed_seconds": round(elapsed, 2),
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    s = results["summary"]
    print(
        f"\n{BOLD}{s['total_files']} files, {s['total_pages']} pages, "
        f"{s['cache_hits']} cache hits, {s['elapsed_seconds']}s{RESET}",
        file=sys.stderr,
    )
    print(f"Output: {output_path}", file=sys.stderr)

    if s["total_files"] == 0:
        print(f"\n{RED}Error: No files were successfully parsed.{RESET}", file=sys.stderr)
        sys.exit(2)


# ============================================================
# Subcommand: report
# ============================================================

def cmd_report(args):
    """Generate visual citation HTML report."""
    from liteparse import LiteParse

    parse_path = Path(args.parse_json)
    cit_path = Path(args.citations)

    if not parse_path.exists():
        print(f"{RED}Error:{RESET} Parse JSON not found: {parse_path}", file=sys.stderr)
        sys.exit(2)
    if not cit_path.exists():
        print(f"{RED}Error:{RESET} Citations JSON not found: {cit_path}", file=sys.stderr)
        sys.exit(2)

    parse_data = json.loads(parse_path.read_text(encoding="utf-8"))
    cit_data = json.loads(cit_path.read_text(encoding="utf-8"))

    question = cit_data.get("question", "")
    answer_md = cit_data.get("answer_md", "")
    citations = cit_data.get("citations", [])

    if not citations:
        print(f"{YELLOW}Warning:{RESET} No citations to render.", file=sys.stderr)

    # Collect unique (file, page) pairs to screenshot
    pages_to_screenshot: dict[tuple[str, int], str] = {}  # (file_name, page) -> file_path
    for cit in citations:
        fname = cit.get("file", "")
        page = cit.get("page", 0)
        if page <= 0:
            continue
        for f in parse_data.get("files", []):
            if f.get("file_name") == fname or Path(f.get("file", "")).name == fname:
                pages_to_screenshot[(fname, page)] = f["file"]
                break

    # Limit screenshots
    max_pages = getattr(args, "max_pages", MAX_REPORT_PAGES)
    if len(pages_to_screenshot) > max_pages:
        print(
            f"{YELLOW}Warning:{RESET} Limiting to {max_pages} cited pages "
            f"(requested {len(pages_to_screenshot)})",
            file=sys.stderr,
        )
        pages_to_screenshot = dict(list(pages_to_screenshot.items())[:max_pages])

    # Take screenshots
    cli = shutil.which("liteparse")
    parser = LiteParse(cli_path=cli) if cli else LiteParse()
    page_screenshots: dict[tuple[str, int], bytes] = {}

    # Group by file for efficient screenshotting
    file_pages: dict[str, list[int]] = {}
    for (fname, page), fpath in pages_to_screenshot.items():
        file_pages.setdefault(fpath, []).append(page)

    for fpath, page_nums in file_pages.items():
        page_str = ",".join(str(p) for p in sorted(set(page_nums)))
        try:
            shots = parser.screenshot(
                fpath, target_pages=page_str, dpi=DEFAULT_DPI, load_bytes=True
            )
            for shot in shots.screenshots:
                file_name = Path(fpath).name
                # Convert PNG to JPEG for size reduction
                img_bytes = _png_to_jpeg(shot.image_bytes)
                page_screenshots[(file_name, shot.page_num)] = img_bytes
                print(
                    f"  {GREEN}SCREENSHOT{RESET}  {file_name} p{shot.page_num} "
                    f"({len(img_bytes)} bytes)",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"  {RED}ERROR{RESET}  Screenshot {fpath}: {e}", file=sys.stderr)

    # Generate HTML
    report_html = _generate_report_html(
        question=question,
        answer_md=answer_md,
        citations=citations,
        page_screenshots=page_screenshots,
        parse_data=parse_data,
        title=args.title,
    )

    # Write output
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_dir = DEFAULT_OUTPUT_DIR / date_str

    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "docparse-report.html"
    html_path.write_text(report_html, encoding="utf-8")

    print(f"\n{GREEN}Report:{RESET} {html_path}", file=sys.stderr)
    print(f"Size: {html_path.stat().st_size:,} bytes", file=sys.stderr)

    # Optional PDF conversion
    if not args.no_pdf:
        pdf_script = WORKSPACE / "scripts" / "html-to-pdf.py"
        if pdf_script.exists():
            pdf_path = html_path.with_suffix(".pdf")
            try:
                subprocess.run(
                    [sys.executable, str(pdf_script), str(html_path), str(pdf_path)],
                    timeout=60, capture_output=True,
                )
                if pdf_path.exists():
                    print(f"{GREEN}PDF:{RESET}    {pdf_path}", file=sys.stderr)
            except (subprocess.TimeoutExpired, OSError) as e:
                print(f"{YELLOW}PDF conversion skipped:{RESET} {e}", file=sys.stderr)


def _png_to_jpeg(png_bytes: bytes, quality: int = 85) -> bytes:
    """Convert PNG bytes to JPEG for smaller report files."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except ImportError:
        return png_bytes  # fallback to PNG if Pillow unavailable


# ============================================================
# Subcommand: status
# ============================================================

def cmd_status(_args):
    """Show cache statistics."""
    if not CACHE_DIR.exists():
        print("Cache directory does not exist yet (no documents parsed).")
        return

    entries = list(CACHE_DIR.glob("*.json"))
    if not entries:
        print("Cache is empty.")
        return

    total_size = sum(f.stat().st_size for f in entries)
    oldest = min(entries, key=lambda f: f.stat().st_mtime)
    newest = max(entries, key=lambda f: f.stat().st_mtime)

    print(f"  Cache dir:   {CACHE_DIR}")
    print(f"  Entries:     {len(entries)}")
    print(f"  Total size:  {total_size:,} bytes ({total_size / 1024:.1f} KB)")
    print(f"  Oldest:      {datetime.fromtimestamp(oldest.stat().st_mtime).isoformat()}")
    print(f"  Newest:      {datetime.fromtimestamp(newest.stat().st_mtime).isoformat()}")


# ============================================================
# Subcommand: clear-cache
# ============================================================

def cmd_clear_cache(args):
    """Clear parse cache."""
    if not CACHE_DIR.exists():
        print("Cache is already empty.")
        return

    if args.file:
        fp = Path(args.file).resolve()
        # Try to find matching cache entries by reading them
        removed = 0
        for entry in CACHE_DIR.glob("*.json"):
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
                if data.get("file") == str(fp) or Path(data.get("file", "")).name == fp.name:
                    entry.unlink()
                    removed += 1
            except (json.JSONDecodeError, OSError):
                pass
        print(f"Removed {removed} cache entries for {fp.name}")
    else:
        if not args.force:
            entries = list(CACHE_DIR.glob("*.json"))
            print(f"This will delete {len(entries)} cached parse results.")
            print(f"Use --force to confirm, or --file to clear a specific file.")
            sys.exit(1)

        entries = list(CACHE_DIR.glob("*.json"))
        for entry in entries:
            entry.unlink(missing_ok=True)
        print(f"Cleared {len(entries)} cache entries.")


# ============================================================
# CLI / Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="31C Document Parser (LiteParse wrapper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # setup
    sp_setup = subparsers.add_parser("setup", help="Check or install prerequisites")
    sp_setup.add_argument("--check", action="store_true", help="Verify prerequisites")
    sp_setup.add_argument("--install", action="store_true", help="Install missing prerequisites")

    # parse
    sp_parse = subparsers.add_parser("parse", help="Parse documents")
    sp_parse.add_argument("--files", nargs="+", required=True, help="File paths or directories")
    sp_parse.add_argument("--pages", default=None, help="Page range, e.g. '1-5,10'")
    sp_parse.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="Render DPI (default: 150)")
    sp_parse.add_argument("--password", default=None, help="Document password")
    sp_parse.add_argument("--no-cache", action="store_true", help="Skip cache")
    sp_parse.add_argument("--output-json", required=True, help="Output JSON path")

    # report
    sp_report = subparsers.add_parser("report", help="Generate visual citation report")
    sp_report.add_argument("--parse-json", required=True, help="Parse output JSON")
    sp_report.add_argument("--citations", required=True, help="Citations JSON")
    sp_report.add_argument("--output-dir", default=None, help="Output directory")
    sp_report.add_argument("--title", default="Document Analysis Report", help="Report title")
    sp_report.add_argument("--max-pages", type=int, default=MAX_REPORT_PAGES, help=f"Max cited pages (default: {MAX_REPORT_PAGES})")
    sp_report.add_argument("--no-pdf", action="store_true", help="Skip PDF conversion")

    # status
    subparsers.add_parser("status", help="Show cache statistics")

    # clear-cache
    sp_clear = subparsers.add_parser("clear-cache", help="Clear parse cache")
    sp_clear.add_argument("--file", default=None, help="Clear cache for specific file")
    sp_clear.add_argument("--force", action="store_true", help="Confirm clearing all cache")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "setup": cmd_setup,
        "parse": cmd_parse,
        "report": cmd_report,
        "status": cmd_status,
        "clear-cache": cmd_clear_cache,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
