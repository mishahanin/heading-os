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

Tests: tests/test_a_citation_that_pointed_at_the_wrong_document.py
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
from scripts.utils.workspace import (
    get_default_tz,
    get_outputs_dir,
    get_workspace_root,
    private_cache_dir,
)

WORKSPACE = get_workspace_root()
DEFAULT_DPI = 150
CACHE_TTL_HOURS = 168  # 7 days

# One constant, because three copies disagreed. Until 2026-08-23 the installer
# pinned 1.2.1 while `parse_document` was written against the 2.0 constructor
# (`dpi`, `target_pages` and `password` moved into `LiteParse(...)` there), so
# running the documented `setup --install` produced a package whose constructor
# rejects every one of those keywords -- a "successful" setup followed by a
# TypeError on the first document. `tests/test_docparse_liteparse_pin.py` fails
# if the three places drift apart again.
LITEPARSE_VERSION = "2.0.0"
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

def _cache_key(
    file_path: Path,
    password: str = "",
    pages: str | None = None,
    dpi: int = DEFAULT_DPI,
) -> str:
    """Compute the cache key for one parse REQUEST, not just for the file.

    `pages` and `dpi` are part of the key because they change the content of
    the returned document. Keyed on the file alone, `--pages 1-2` wrote a
    two-page result that every later full parse of the same file was served as
    if it were the whole document -- and the cached dict carries `"dpi": dpi`
    from whichever run populated it, so the substitution labelled itself
    truthfully while being wrong. `scrape_cache_key` in `firecrawl.py` carries
    the same fix for the same reason.

    `Path.resolve()` case-normalizes on Windows NTFS, and that guarantee is
    real HERE specifically: `stat()` on the line below establishes the file
    exists, so `realpath` reaches `_getfinalpathname`
    (`GetFinalPathNameByHandle`), which returns the casing as stored on disk.
    The non-strict fallback, which does NOT canonicalize a missing tail, is
    unreachable for a file that just stat-ed.
    """
    stat = file_path.stat()
    raw = (f"{file_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
           f":pages={pages or 'all'}:dpi={dpi}")
    if password:
        raw += f":{hashlib.sha256(password.encode()).hexdigest()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def cache_dir() -> Path:
    """Where parsed documents are cached.

    A cache entry here holds the full extracted TEXT of the document that was
    parsed, so it is private material even though it is rebuildable. It used to
    be `<workspace_root>/.cache/docparse`, which is INSIDE the engine clone, on
    a workspace whose whole premise is that the engine carries code and nothing
    else. It is gitignored, so it could never be committed and this was never a
    leak; it was simply on the wrong side of the seam, in a tree no content wall
    looks at (`repo_carried_paths` passes `--exclude-standard`, and rightly so).
    MEASURED 2026-08-28 on the operator's own machine: five parsed documents.

    `private_cache_dir` puts it under the data overlay when there is one, and
    leaves it under the workspace root when there is not, which is where a
    standalone clone's own documents belong. Both destinations are already
    covered by their repository's gitignore, so this needs no new rule.

    A FUNCTION, not the module constant it replaces. The constant resolved at
    import time, which would have made `import scripts.docparse` raise on a
    stale HEADING_OS_DATA. Deleting the name rather than reassigning it is also
    deliberate: a test still doing `monkeypatch.setattr(dp, "CACHE_DIR", ...)`
    now fails loudly instead of binding nothing and quietly writing into the
    operator's live cache.
    """
    return private_cache_dir("docparse")


def _cache_get(key: str) -> dict | None:
    """Return cached parse result if exists and not expired."""
    cache_file = cache_dir() / f"{key}.json"
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
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        # TypeError and ValueError joined the tuple on 2026-08-24. This handler
        # exists so a corrupt entry REGENERATES cleanly, and the two things a
        # corrupt `_cached_at` actually raises were both outside it:
        # `fromisoformat` raises ValueError on a malformed string and TypeError
        # on a non-string, and a naive datetime stored there raises TypeError
        # at the subtraction below. Any of them crashed the whole parse run —
        # the precise scenario the handler was built to absorb.
        print(f"  {YELLOW}Warning:{RESET} Cache entry corrupt, regenerating: {e}", file=sys.stderr)
        cache_file.unlink(missing_ok=True)
        return None


def _cache_put(key: str, data: dict) -> None:
    """Write parse result to cache."""
    cdir = cache_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    data["_cached_at"] = datetime.now(timezone.utc).isoformat()
    (cdir / f"{key}.json").write_text(
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
    key = _cache_key(fp, pwd, pages, dpi)

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
    # Compared BEFORE lowering, because lowering is not part of
    # `_normalize_text` and comparing after it hid a real desync (see below).
    norm_concat = _normalize_text(raw_concat)

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
            if _WS_CHAR_RE.fullmatch(c):
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

    if "".join(norm_chars) != norm_concat:
        # `norm_concat` was computed and never used. That is not harmless here:
        # the loop above RE-IMPLEMENTS `_normalize_text` inline, because it has
        # to carry the char-to-item mapping along, and the two copies must
        # agree for the box lookup to point at the right text. With the
        # variable unused, nothing compared them, so an edit to
        # `_normalize_text` would desync the matcher silently. Comparing is the
        # whole reason to keep it.
        print(f"  {YELLOW}Warning:{RESET} the inline normalization in "
              f"find_boxes_for_quote has drifted from _normalize_text; "
              f"bounding boxes may point at the wrong text.", file=sys.stderr)
    # Lower PER CHARACTER, growing the mapping with it. Lowering the joined
    # string left the mapping at its pre-lowering length, so a character whose
    # lowercase is longer than itself (U+0130, capital I with dot above,
    # lowercases to two code points) shifted every index after it and the
    # boxes landed on neighbouring text. It was silent: the drift guard
    # lowercased both sides identically, so it never saw a difference. The
    # quote is lowered the same way, which keeps the two sides consistent even
    # where per-character and whole-string lowercasing legitimately differ
    # (Greek final sigma).
    lowered = []
    lowered_to_item = []
    # strict=True: the two lists are appended in lockstep just above, so a
    # length mismatch is an internal bug, and silently truncating to the
    # shorter one is how a char-to-item map goes quietly wrong.
    for ch, item_idx in zip(norm_chars, norm_char_to_item, strict=True):
        low = ch.lower()
        lowered.append(low)
        lowered_to_item.extend([item_idx] * len(low))
    concat = "".join(lowered)
    norm_quote = "".join(c.lower() for c in _normalize_text(quote))

    pos = concat.find(norm_quote)
    if pos == -1:
        return []

    # Find which items are involved in the match
    matched_items = set()
    for i in range(pos, pos + len(norm_quote)):
        if i < len(lowered_to_item):
            matched_items.add(lowered_to_item[i])

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


# ONE definition of "whitespace", compiled two ways. `_normalize_text` used
# `\s+` while the inline walk in `find_boxes_for_quote` tested `c in " \t\n\r"`,
# and `\s` matches more than those four -- `\x0b`, `\x0c` (form feed, common in
# extracted PDF text), `U+0085`, `U+2028`, `U+2029`. A quote spanning a form
# feed normalized to a space on one side and stayed a form feed on the other,
# so the lookup missed, the citation rendered with no highlight, and the drift
# guard fired about the very desync it was added to catch.
_WS_PATTERN = r"\s"
_WS_CHAR_RE = re.compile(_WS_PATTERN)
_WS_RUN_RE = re.compile(_WS_PATTERN + "+")

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
    # Collapse whitespace. Same `_WS_PATTERN` the inline walk in
    # `find_boxes_for_quote` tests against, so the two cannot disagree.
    text = _WS_RUN_RE.sub(" ", text).strip()
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
    capped_pages: int = 0,   # cited pages dropped by --max-pages, 0 if none
) -> str:
    """Generate self-contained 31C-branded HTML report with visual citations."""
    from scripts.utils.image import load_logo_base64

    logo_b64 = load_logo_base64()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # The parse this report is built on may have been partial. `cmd_parse` now
    # records every document it could not read under `failures`, and this is
    # where that reaches the reader: a report over three of five documents used
    # to look exactly like a report over all five, and the answer above it was
    # written against the three.
    parse_failures = parse_data.get("failures") or []
    parse_failure_note = ""
    if parse_failures:
        _names = ", ".join(sorted(html.escape(Path(f.get("file", "?")).name)
                                  for f in parse_failures))
        parse_failure_note = f'''
    <div class="cite-caveat">The parse behind this report did not cover every
    document it was given. {len(parse_failures)} failed and are absent from the
    evidence below: {_names}.</div>'''
    # `sorted`, not bare `set`: the header is part of a document the operator
    # keeps, and two runs over identical input printed the file names in
    # different orders.
    files_str = ", ".join(sorted({c.get("file", "?") for c in citations}))
    # Counted from the CITATIONS, which is what the label says. It was
    # `len(page_screenshots)`, the number of screenshots the run managed to
    # take: a screenshot that failed (`cmd_report` logs the error and carries
    # on) or two documents sharing a basename (that dict is keyed by basename)
    # both made the header print fewer "pages cited" than the cards below it
    # showed. A screenshot count under a citation label is a measurement
    # answering a different question than its own caption.
    pages_cited = len({(c.get("file", "?"), c.get("page", 0)) for c in citations})
    answer_html = _markdown_to_html(answer_md)

    # Build citation cards HTML
    cards_html = []
    for cit in citations:
        cit_id = cit.get("id", "?")
        file_name = cit.get("file", "unknown")
        page_num = cit.get("page", 0)
        quote_text = cit.get("quote", "")
        relevance = cit.get("relevance", "")
        # Resolve the file first, so the page and the screenshot are looked up
        # against the SAME document. `_find_page_in_parse` collapses "no such
        # document" and "no such page" into one None, and the card has to tell
        # a reader which of the two happened.
        parse_file, ambiguous = _resolve_parse_file(parse_data, file_name)
        page_data = None
        if parse_file is not None:
            for _p in parse_file.get("pages", []):
                if _p.get("page_num") == page_num:
                    page_data = _p
                    break

        # `cmd_report` keys `page_screenshots` by BASENAME, so the key is built
        # from the RESOLVED file and never from the citation's raw string.
        # Citing the full path is the remedy the ambiguity note below tells the
        # operator to use, and it was the one spelling that matched no
        # screenshot: the fix for the ambiguous name landed in
        # `_resolve_parse_file` and never reached this lookup, so following the
        # advice silently cost the reader the page image and the highlight.
        if parse_file is not None:
            shot_name = parse_file.get("file_name") or Path(parse_file.get("file", "")).name
        else:
            shot_name = Path(file_name).name
        screenshot_key = (shot_name, page_num)

        dpi = DEFAULT_DPI
        if page_data:
            boxes = find_boxes_for_quote(page_data.get("text_items", []), quote_text, dpi)
            page_w_px = page_data["width_pt"] * (dpi / 72.0)
            page_h_px = page_data["height_pt"] * (dpi / 72.0)
        else:
            boxes = []
            page_w_px = 800
            page_h_px = 600

        # Screenshot image. Withheld when the name resolves to more than one
        # document: showing SOME page under an unresolved name is the defect.
        # Withheld for the same reason when the parse data holds no such page:
        # the only page size available then is the invented 800x600 above, so
        # any highlight drawn over the image would be in a made-up coordinate
        # space.
        img_bytes = (None if (ambiguous or page_data is None)
                     else page_screenshots.get(screenshot_key))
        if img_bytes:
            img_b64 = base64.b64encode(img_bytes).decode("ascii")
            img_src = f"data:{_image_mime(img_bytes)};base64,{img_b64}"
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

        # One note channel, every degraded state. There used to be exactly one
        # state that said anything -- the ambiguous name -- and four that did
        # not: a document absent from the parse data, a page absent from it, a
        # quote the matcher PROVED is not in the page's extracted text, and a
        # page whose screenshot was never captured. All four rendered as an
        # ordinary, confident card. The worst of them is the quote: the report
        # exists to show a reader WHERE a document says something, and the one
        # case where the tool established that it does not say it there was the
        # case it kept to itself.
        #
        # A list, not a single message. The states are independent, and a
        # second problem hidden behind the first is the same defect again.
        notes = []
        if ambiguous:
            notes.append(
                f'More than one parsed document is named {html.escape(file_name)}. '
                f'This citation does not say which, so no page image or highlight '
                f'is shown. Cite the full path to resolve it.')
        elif parse_file is None:
            notes.append(
                f'No parsed document named {html.escape(file_name)} is in this '
                f'report, so the quote below was never checked against a source '
                f'and no page image is shown.')
        elif page_data is None:
            notes.append(
                f'Page {html.escape(str(page_num))} is not among the parsed pages '
                f'of {html.escape(file_name)}, so the quote below was never '
                f'checked against a source and no page image is shown.')
        else:
            if not quote_text.strip():
                notes.append(
                    'This citation carries no quote, so there is nothing to '
                    'locate on the page.')
            elif not boxes:
                notes.append(
                    f'The quote below was NOT found in the extracted text of page '
                    f'{html.escape(str(page_num))} of {html.escape(file_name)}. '
                    f'The page is shown, but nothing on it is highlighted because '
                    f'the quote could not be located.')
            if img_bytes is None:
                notes.append(
                    f'No page image was captured for page '
                    f'{html.escape(str(page_num))} of {html.escape(file_name)}.')

        ambiguity_note = "".join(
            f'\n      <div class="{"cite-ambiguous" if ambiguous else "cite-caveat"}">'
            f'{n}</div>'
            for n in notes)

        card = f"""
    <section class="citation-card" id="cite-{html.escape(str(cit_id), quote=True)}">
      <div class="card-header">
        <span class="cite-num">[{html.escape(str(cit_id))}]</span>
        <span class="cite-source">{html.escape(file_name)} - Page {html.escape(str(page_num))}</span>
      </div>{ambiguity_note}
      <div class="card-body">
        <div class="page-view">
          {"" if not img_src else f'''<div class="page-image-container">
            <img src="{img_src}" class="page-image" alt="Page {html.escape(str(page_num))}">
            <svg class="highlight-overlay" viewBox="0 0 {page_w_px:.0f} {page_h_px:.0f}"
                 preserveAspectRatio="none">
              {svg_rects}
            </svg>
          </div>'''}
          <div class="page-label">Page {html.escape(str(page_num))}</div>
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
  .cite-ambiguous {{
    margin: 0 1.25rem 0.75rem;
    padding: 0.6rem 0.85rem;
    border-left: 3px solid #F5922B;
    background: rgba(245,146,43,0.08);
    color: var(--text-muted);
    font-size: 0.85rem;
  }}
  /* Same look, separate rule. Written out rather than added to the selector
     above, because `test_the_ambiguous_style_is_defined` matches the literal
     `.cite-ambiguous {{`, and a grouped selector would silently stop matching
     it while the style still worked. */
  .cite-caveat {{
    margin: 0 1.25rem 0.75rem;
    padding: 0.6rem 0.85rem;
    border-left: 3px solid #F5922B;
    background: rgba(245,146,43,0.08);
    color: var(--text-muted);
    font-size: 0.85rem;
  }}

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
    <div class="meta">{html.escape(files_str)} | {pages_cited} pages cited | {len(citations)} citations | {now}</div>{"" if not capped_pages else f'''
    <div class="cite-caveat">This report was limited to a maximum number of
    cited pages, so {capped_pages} cited page(s) below carry no page image. The
    limit is set by --max-pages.</div>'''}{parse_failure_note}
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


def _resolve_parse_file(parse_data: dict, file_name: str) -> tuple[dict | None, bool]:
    """Resolve a citation's `file` to EXACTLY one parsed file.

    Returns `(file_dict, ambiguous)`. Matching was on basename alone, and
    `parse --files dirA dirB` auto-discovers both, so two different documents
    called `report.pdf` collided: the first one found answered for both, and a
    citation about the second rendered the first document's page and the first
    document's highlight boxes with no visible sign of the substitution. A
    mis-citation shown with full confidence is worse than a missing one, so
    where the name does not identify one file this refuses to pick.

    `cmd_clear_cache` already treats the shared-basename case as a hazard; this
    is the same hazard on the reporting side.
    """
    files = parse_data.get("files", [])
    for f in files:
        if f.get("file") == file_name:      # a full path identifies one file
            return f, False
    matches = [f for f in files
               if f.get("file_name") == file_name
               or Path(f.get("file", "")).name == file_name]
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _find_page_in_parse(
    parse_data: dict, file_name: str, page_num: int
) -> tuple[dict | None, bool]:
    """Find one page by file name and number. Returns `(page, ambiguous)`."""
    parse_file, ambiguous = _resolve_parse_file(parse_data, file_name)
    if parse_file is None:
        return None, ambiguous
    for p in parse_file.get("pages", []):
        if p.get("page_num") == page_num:
            return p, False
    return None, False


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

    # Python package. The version is checked, not merely the import, because
    # `setup --install` pins `liteparse=={LITEPARSE_VERSION}` and a drifted
    # install is exactly what this command exists to catch. MEASURED
    # 2026-08-28 on the operator's machine: LITEPARSE_VERSION is "2.0.0", the
    # installed package is 2.9.0, and this printed "All prerequisites met".
    version_unknown = False
    try:
        import liteparse
        installed = getattr(liteparse, "__version__", None)
        if installed is None:
            print(f"  {YELLOW}WARN{RESET}  liteparse installed, version not "
                  f"reported by the package (expected {LITEPARSE_VERSION})")
            version_unknown = True
        elif installed != LITEPARSE_VERSION:
            print(f"  {YELLOW}WARN{RESET}  liteparse {installed} installed, "
                  f"this tool is written against {LITEPARSE_VERSION}")
            version_unknown = True
        else:
            print(f"  {GREEN}OK{RESET}  liteparse {installed}")
    except ImportError:
        print(f"  {RED}FAIL{RESET}  liteparse Python package not installed")
        print(f"         Install: pip install liteparse=={LITEPARSE_VERSION}")
        all_ok = False

    # What the sentence may claim is what the three checks above established:
    # that node, the CLI and the package are PRESENT. It said "All
    # prerequisites met", which reads as a verdict on the whole setup, over a
    # method that never compared a single version.
    if all_ok and not version_unknown:
        print(f"\n{GREEN}Node.js, the LiteParse CLI and the Python package are "
              f"present, at the versions this tool expects.{RESET}")
    elif all_ok:
        print(f"\n{YELLOW}Node.js, the LiteParse CLI and the Python package are "
              f"present. The package version was not confirmed as "
              f"{LITEPARSE_VERSION}; see the WARN above.{RESET}")
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
            [sys.executable, "-m", "pip", "install", f"liteparse=={LITEPARSE_VERSION}"],
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

def _password(args) -> str | None:
    """The document password, preferring the environment over argv.

    `--password` puts the secret in the process table for the life of the run
    and in the operator's shell history for ever. `DOCPARSE_PASSWORD` does
    neither. The flag stays, because a caller may already depend on it, but it
    now says what it costs and the environment wins when both are set.
    """
    import os
    from_env = os.environ.get("DOCPARSE_PASSWORD")
    if from_env:
        if getattr(args, "password", None):
            print(f"  {GRAY}Both --password and DOCPARSE_PASSWORD are set; "
                  f"using the environment.{RESET}", file=sys.stderr)
        return from_env
    if getattr(args, "password", None):
        print(f"  {YELLOW}Warning:{RESET} --password is visible to any local "
              f"account via `ps` and is written to shell history. Prefer "
              f"DOCPARSE_PASSWORD.", file=sys.stderr)
    return getattr(args, "password", None)


def cmd_parse(args):
    """Parse one or more documents."""
    results = {"files": [], "failures": [], "summary": {}}
    failures = results["failures"]
    t0 = time.time()
    cache_hits = 0

    for file_str in args.files:
        fp = Path(file_str).resolve()
        if not fp.exists():
            print(f"  {RED}SKIP{RESET}  {file_str} (not found)", file=sys.stderr)
            # A path the operator NAMED and that does not exist is the loudest
            # failure of the three, and it was the one nothing recorded.
            failures.append({"file": str(fp), "error": "not found"})
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
                    password=_password(args), no_cache=args.no_cache,
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
                failures.append({"file": str(f), "error": "not found"})
            except Exception as e:
                print(f"  {RED}ERROR{RESET}  {f.name}: {e}", file=sys.stderr)
                failures.append({"file": str(f), "error": f"{type(e).__name__}: {e}"})

    elapsed = time.time() - t0
    results["failures"] = failures
    results["summary"] = {
        "total_files": len(results["files"]),
        # `total_files` counts successes, and it was the ONLY count written.
        # MEASURED on five documents of which two raised: the archived JSON
        # said `total_files: 3`, carried no record of the other two, and the
        # errors went to stderr where they scroll away. `cmd_report` then read
        # that file and had no way to know the sweep was partial, so a report
        # built over three fifths of a corpus looked exactly like one built
        # over all of it.
        "total_failed": len(failures),
        "total_requested": len(results["files"]) + len(failures),
        "total_pages": sum(len(f.get("pages", [])) for f in results["files"]),
        "cache_hits": cache_hits,
        "elapsed_seconds": round(elapsed, 2),
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    s = results["summary"]
    # `N files` alone reads as "N is what there was". Say what was asked for
    # whenever the two numbers differ, so a partial sweep cannot be mistaken
    # for a complete one at a glance.
    scope = (f"{s['total_files']} of {s['total_requested']} files"
             if s["total_failed"] else f"{s['total_files']} files")
    print(
        f"\n{BOLD}{scope}, {s['total_pages']} pages, "
        f"{s['cache_hits']} cache hits, {s['elapsed_seconds']}s{RESET}",
        file=sys.stderr,
    )
    print(f"Output: {output_path}", file=sys.stderr)

    if s["total_failed"]:
        print(f"{RED}{s['total_failed']} file(s) failed{RESET} and are recorded "
              f"under `failures` in {output_path.name}:", file=sys.stderr)
        for fail in results["failures"]:
            print(f"  {Path(fail['file']).name}: {fail['error']}", file=sys.stderr)

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
        parse_file, ambiguous = _resolve_parse_file(parse_data, fname)
        if ambiguous:
            print(f"  {YELLOW}AMBIGUOUS{RESET}  more than one parsed document is "
                  f"named {fname}; citation p{page} rendered without an image",
                  file=sys.stderr)
            continue
        if parse_file is not None:
            pages_to_screenshot[(fname, page)] = parse_file["file"]

    # Limit screenshots
    max_pages = getattr(args, "max_pages", MAX_REPORT_PAGES)
    # The count travels into the report. It used to be a stderr warning and
    # nothing else, so the HTML the operator keeps and forwards recorded no
    # trace of the cut: a reader opening it later saw citation cards with no
    # page image and had no way to learn that the tool chose not to capture
    # them rather than failed to.
    capped_pages = 0
    if len(pages_to_screenshot) > max_pages:
        capped_pages = len(pages_to_screenshot) - max_pages
        print(
            f"{YELLOW}Warning:{RESET} Limiting to {max_pages} cited pages "
            f"(requested {len(pages_to_screenshot)})",
            file=sys.stderr,
        )
        pages_to_screenshot = dict(list(pages_to_screenshot.items())[:max_pages])

    # Take screenshots.
    #
    # `LiteParse()`, with no `cli_path`. The note at the top of
    # `parse_document` says plainly that liteparse 2.0 REMOVED that keyword
    # (the bindings locate the CLI themselves), and this line passed it
    # whenever the CLI was on PATH — which is the documented, prerequisite
    # setup. So `report` raised TypeError before taking a single screenshot
    # while `parse` worked fine, which is the hardest kind of breakage to
    # place. `shutil.which` is kept as the availability check it now is.
    if not shutil.which("liteparse"):
        print(f"{YELLOW}Warning:{RESET} the liteparse CLI is not on PATH; "
              f"screenshots may fail.", file=sys.stderr)
    parser = LiteParse()
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
        capped_pages=capped_pages,
    )

    # Write output
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        date_str = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
        # Resolved HERE, not at import. As a module constant this called
        # `get_outputs_dir()` the moment anything imported docparse, so a
        # HEADING_OS_DATA naming a directory that had since moved raised
        # DataRootError out of the import itself: no argparse, no usage line, a
        # traceback from `--help`. Only the `report` subcommand needs it, and
        # only when `--output-dir` was not given.
        output_dir = get_outputs_dir() / "intel" / "docparse" / date_str

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
                proc = subprocess.run(
                    [sys.executable, str(pdf_script), str(html_path), str(pdf_path)],
                    timeout=60, capture_output=True, text=True,
                )
                if pdf_path.exists():
                    print(f"{GREEN}PDF:{RESET}    {pdf_path}", file=sys.stderr)
                else:
                    # There was no `else`, and the return code was never read,
                    # so a converter that exited non-zero without writing the
                    # file produced no PDF and no message: the run "completed"
                    # and the operator had no flag that would have told them.
                    detail = (proc.stderr or proc.stdout or "").strip()
                    print(f"{YELLOW}PDF conversion produced no file{RESET} "
                          f"(html-to-pdf.py exited {proc.returncode})"
                          + (f": {detail[:300]}" if detail else "."),
                          file=sys.stderr)
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


def _image_mime(data: bytes) -> str:
    """The MIME type these bytes actually are.

    `_png_to_jpeg` returns the ORIGINAL PNG when Pillow is absent, and the
    report embedded whatever came back under a hardcoded
    `data:image/jpeg;base64,` label. Rendering then relied on browser
    content-sniffing, and a strict consumer — this repo pipes the HTML through
    `html-to-pdf.py` — can drop the image entirely. Cheap to be honest: the PNG
    signature is eight fixed bytes.
    """
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


# ============================================================
# Subcommand: status
# ============================================================

def cmd_status(_args):
    """Show cache statistics."""
    cdir = cache_dir()
    if not cdir.exists():
        print("Cache directory does not exist yet (no documents parsed).")
        return

    entries = list(cdir.glob("*.json"))
    if not entries:
        print("Cache is empty.")
        return

    total_size = sum(f.stat().st_size for f in entries)
    oldest = min(entries, key=lambda f: f.stat().st_mtime)
    newest = max(entries, key=lambda f: f.stat().st_mtime)

    print(f"  Cache dir:   {cdir}")
    print(f"  Entries:     {len(entries)}")
    print(f"  Total size:  {total_size:,} bytes ({total_size / 1024:.1f} KB)")
    print(f"  Oldest:      {datetime.fromtimestamp(oldest.stat().st_mtime, tz=get_default_tz()).isoformat()}")
    print(f"  Newest:      {datetime.fromtimestamp(newest.stat().st_mtime, tz=get_default_tz()).isoformat()}")


# ============================================================
# Subcommand: clear-cache
# ============================================================

def cmd_clear_cache(args):
    """Clear parse cache."""
    cdir = cache_dir()
    if not cdir.exists():
        print("Cache is already empty.")
        return

    if args.file:
        fp = Path(args.file).resolve()
        # Try to find matching cache entries by reading them
        removed = 0
        unreadable = []
        undeletable = []
        for entry in cdir.glob("*.json"):
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                # This used to be `pass`, under a handler that also covered the
                # unlink below. An entry this loop cannot read is an entry it
                # cannot rule out: it may be the cache of exactly the file the
                # operator asked to clear, and the count printed afterwards
                # said nothing about it either way.
                unreadable.append((entry.name, str(e)))
                continue
            # Exact path only. The basename fallback deleted the cache of
            # every document sharing a filename across directories — asking
            # to clear `~/drafts/q3.pdf` also cleared `~/contracts/q3.pdf`.
            # The cost is only recompute, but the deletion was broader than
            # what was asked for, and the exact match already sufficed.
            if data.get("file") != str(fp):
                continue
            try:
                entry.unlink()
            except OSError as e:
                # The sharper half of the same swallow. MEASURED: one matching
                # entry that raises EACCES on unlink printed "Removed 0 cache
                # entries for q3.pdf" and stopped there, which an operator
                # reads as "there was no cache for that file" — the exact
                # opposite of what happened. The entry is still on disk and the
                # next parse still reads it.
                undeletable.append((entry.name, str(e)))
                continue
            removed += 1
        print(f"Removed {removed} cache entries for {fp.name}")
        for name, err in unreadable:
            print(f"{YELLOW}Skipped{RESET} {name}: unreadable ({err}). "
                  f"It may or may not belong to {fp.name}.", file=sys.stderr)
        for name, err in undeletable:
            print(f"{RED}Failed to remove{RESET} {name}, which does belong to "
                  f"{fp.name}: {err}", file=sys.stderr)
        if undeletable:
            # An entry that matched and survived means the command did not do
            # what it was asked to do, so it must not report success.
            sys.exit(1)
    else:
        if not args.force:
            entries = list(cdir.glob("*.json"))
            print(f"This will delete {len(entries)} cached parse results.")
            print(f"Use --force to confirm, or --file to clear a specific file.")
            sys.exit(1)

        entries = list(cdir.glob("*.json"))
        # The same defect as the --file branch, in its other copy: this counted
        # the entries it FOUND and called them cleared, and an OSError on any
        # one of them aborted the sweep with a traceback and no summary at all,
        # leaving the operator with no idea how many had gone.
        cleared = 0
        undeletable = []
        for entry in entries:
            try:
                entry.unlink(missing_ok=True)
            except OSError as e:
                undeletable.append((entry.name, str(e)))
                continue
            cleared += 1
        print(f"Cleared {cleared} of {len(entries)} cache entries.")
        for name, err in undeletable:
            print(f"{RED}Failed to remove{RESET} {name}: {err}", file=sys.stderr)
        if undeletable:
            sys.exit(1)


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
    sp_parse.add_argument(
        "--password", default=None,
        help="Document password. Prefer DOCPARSE_PASSWORD in the environment: "
             "an argv element is readable by any local account through `ps` "
             "for the life of the run, and lands in shell history.")
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
