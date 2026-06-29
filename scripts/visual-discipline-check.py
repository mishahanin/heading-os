#!/usr/bin/env python3
"""
visual-discipline-check.py - Mechanical audit for AI-default visual tells.

Visual-design counterpart to scripts/humanization-check.py. Where the
humanisation check scans prose for AI-text fingerprints, this scans visual
artifacts (HTML, SVG, PPTX) for the AI-default design tells named in
.claude/rules/visual-design-discipline.md: forbidden fonts, the purple->pink
hero gradient, oversized Tailwind radii, Lucide/Heroicons icon defaults, the
ChatGPT-emerald and captured-pastel hero colors, and a few heuristic layout
and copy tells (advisory).

It is mechanical, not a designer: it catches the textual, regex-detectable
tells. Hierarchy, specificity density, and committed-stance (the rule's first
three fundamentals) still need human judgement against the exemplar shelf.

Usage:
  python scripts/visual-discipline-check.py <file>            # one HTML/SVG/PPTX file
  python scripts/visual-discipline-check.py <dir>             # recurse for .html/.svg/.pptx
  python scripts/visual-discipline-check.py --strict <path>   # fail on warnings too
  python scripts/visual-discipline-check.py --json <path>     # JSON output
  python scripts/visual-discipline-check.py --include-internal <dir>  # do not skip out-of-scope dirs

Severity:
  error   - high-confidence AI-default tell (forbidden font, purple->pink
            gradient, rounded-2xl/3xl, Lucide/Heroicons, banned hero color)
  warning - advisory / heuristic tell (neutral-stack pairing, indigo-violet
            primary, three-up cards, centered hero, Title Case heading, copy
            register). May false-positive; human review decides.

Exit codes:
  0 - clean (or strict-mode pass)
  1 - findings present (errors, or in strict mode warnings)
  2 - script error
"""

import sys
import re
import json
import argparse
import zipfile
from pathlib import Path
from collections import Counter

# ============================================================
# Workspace utility imports
# ============================================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
except ImportError:
    GREEN = YELLOW = RED = CYAN = GRAY = BOLD = RESET = ""

# ============================================================
# Configuration - tell definitions
# ============================================================

# Fonts forbidden as primary (rule section 5 + vocabulary table).
# GT Standard, Geist, IBM Plex, and custom commissions are permitted.
FORBIDDEN_FONTS = [
    "Inter", "Roboto", "Open Sans", "Lato", "Poppins", "Montserrat", "Space Grotesk",
]
# Context-dependent: flagged advisory (fine on devtool products, a tell elsewhere).
ADVISORY_FONTS = ["JetBrains Mono"]

# Font declaration contexts - we only match font names inside these, to avoid
# matching the word elsewhere in body copy ("the analyst interface").
_FONT_CONTEXTS = [
    re.compile(r"font-family\s*:\s*([^;{}]+)", re.IGNORECASE),
    re.compile(r"\bfamily=([^&\"';]+)", re.IGNORECASE),          # Google Fonts URL
    re.compile(r'typeface\s*=\s*"([^"]+)"', re.IGNORECASE),      # PPTX/OOXML
    re.compile(r"--[\w-]*font[\w-]*\s*:\s*([^;{}]+)", re.IGNORECASE),  # CSS var
]

# Banned hero/accent colors (rule section 4 + vocabulary table). Matches both
# CSS `#RRGGBB` and OOXML `val="RRGGBB"`.
BANNED_COLORS = {
    "10A37F": "legacy ChatGPT emerald as primary",
    "E8DDF4": "Material-3 captured pastel (violet) at hero density",
    "D0E4F5": "Gamma captured pastel (blue) at hero density",
    "F5EEF8": "Tabler captured pastel (lavender) at hero density",
}

# Tailwind purple->pink gradient: the single most-cited tell.
_GRAD_FROM = re.compile(r"\b(?:bg-gradient-to-\w+\s+)?from-(purple|violet|fuchsia)-\d{2,3}\b", re.IGNORECASE)
_GRAD_TO = re.compile(r"\bto-(pink|fuchsia|rose|purple)-\d{2,3}\b", re.IGNORECASE)
# CSS linear-gradient containing both a purple-ish and a pink-ish stop.
_CSS_GRADIENT = re.compile(r"linear-gradient\([^)]*\)", re.IGNORECASE)
_PURPLEISH = re.compile(r"(purple|violet|#a855f7|#9333ea|#8b5cf6|#7c3aed|#6d28d9)", re.IGNORECASE)
_PINKISH = re.compile(r"(\bpink\b|fuchsia|#ec4899|#db2777|#f472b6|#e879f9)", re.IGNORECASE)

# Oversized Tailwind radius (rounded-2xl is the named tell; 3xl is its sibling).
_ROUNDED = re.compile(r"\brounded-(?:2xl|3xl|\[?\d{2,}px\]?)\b", re.IGNORECASE)

# Default icon libraries.
_ICON_LIB = re.compile(r"\b(lucide-react|data-lucide|lucide|heroicons)\b", re.IGNORECASE)

# Indigo/violet Tailwind primary accents (advisory - common, FP-prone).
_INDIGO_VIOLET = re.compile(r"\b(?:bg|text|from|to|border|ring)-(indigo|violet)-(?:500|600|700)\b", re.IGNORECASE)

# Neutral-stack pairing (advisory): slate-50 + zinc-900 unmodified.
_SLATE_50 = re.compile(r"\bslate-50\b", re.IGNORECASE)
_ZINC_900 = re.compile(r"\bzinc-900\b", re.IGNORECASE)

# Three-up cards (advisory): grid-cols-3 alongside rounded cards.
_GRID_COLS_3 = re.compile(r"\bgrid-cols-3\b", re.IGNORECASE)
_ROUNDED_CARD = re.compile(r"\brounded-(xl|2xl|3xl)\b", re.IGNORECASE)

# Centered hero (advisory): a "hero" class with text-center.
_HERO_CLASS = re.compile(r"class\s*=\s*\"[^\"]*\bhero\b[^\"]*\"", re.IGNORECASE)
_TEXT_CENTER = re.compile(r"\btext-center\b", re.IGNORECASE)

# Copy-register tells (advisory).
COPY_TELLS = [
    "build the future", "ai-powered", "reimagine", "supercharge your",
    "unlock the power", "next-generation platform",
]

# HTML heading capture for Title Case check.
_HTML_HEADING = re.compile(r"<h([1-3])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP = re.compile(r"<[^>]+>")
_TITLE_STOP = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
               "of", "on", "or", "the", "to", "via", "with"}

# Directories skipped in recursion mode (rule carve-out: internal/utility surfaces).
OUT_OF_SCOPE = ("/outputs/operations/", "/outputs/clipboard/", "/outputs/browser/",
                "/_archive/", "/archive/", "/node_modules/", "/.git/")

SCAN_EXTENSIONS = (".html", ".htm", ".svg", ".pptx")


# ============================================================
# Helpers
# ============================================================

def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _snippet(text, start, end, before=12, after=30):
    s = max(0, start - before)
    e = min(len(text), end + after)
    out = ("..." if s > 0 else "") + text[s:e] + ("..." if e < len(text) else "")
    return out.replace("\n", " ").strip()


def _add(findings, ftype, severity, tell, text, pos, has_lines=True):
    findings.append({
        "type": ftype,
        "severity": severity,
        "tell": tell,
        "line": _line_of(text, pos) if has_lines else None,
        "context": _snippet(text, pos, pos + len(str(tell))),
    })


# ============================================================
# Individual checks
# ============================================================

def _check_fonts(text, findings, has_lines):
    for ctx in _FONT_CONTEXTS:
        for m in ctx.finditer(text):
            value = m.group(1)
            for font in FORBIDDEN_FONTS:
                if re.search(r"\b" + re.escape(font) + r"\b", value, re.IGNORECASE):
                    _add(findings, "forbidden_font", "error", font, text, m.start(1), has_lines)
            for font in ADVISORY_FONTS:
                if re.search(r"\b" + re.escape(font) + r"\b", value, re.IGNORECASE):
                    _add(findings, "forbidden_font", "warning", font, text, m.start(1), has_lines)


def _check_colors(text, findings, has_lines):
    for hexcode, why in BANNED_COLORS.items():
        pat = re.compile(r"(?:#|val=\")" + hexcode + r"\b", re.IGNORECASE)
        for m in pat.finditer(text):
            _add(findings, "banned_color", "error", f"#{hexcode} ({why})", text, m.start(), has_lines)


def _check_gradient(text, findings, has_lines):
    # Tailwind from-purple/violet/fuchsia + to-pink/fuchsia/rose on the same line.
    for line_match in re.finditer(r"[^\n]+", text):
        seg = line_match.group()
        if _GRAD_FROM.search(seg) and _GRAD_TO.search(seg):
            _add(findings, "gradient_purple_pink", "error",
                 "Tailwind purple->pink gradient", text, line_match.start(), has_lines)
    # CSS linear-gradient spanning a purple stop and a pink stop.
    for m in _CSS_GRADIENT.finditer(text):
        span = m.group()
        if _PURPLEISH.search(span) and _PINKISH.search(span):
            _add(findings, "gradient_purple_pink", "error",
                 "CSS purple->pink linear-gradient", text, m.start(), has_lines)


def _check_radius(text, findings, has_lines):
    for m in _ROUNDED.finditer(text):
        _add(findings, "rounded_oversized", "error", m.group(), text, m.start(), has_lines)


def _check_icons(text, findings, has_lines):
    seen = set()
    for m in _ICON_LIB.finditer(text):
        key = m.group().lower()
        if key in seen:
            continue
        seen.add(key)
        _add(findings, "icon_library", "error", m.group(), text, m.start(), has_lines)


def _check_advisory_palette(text, findings, has_lines):
    for m in _INDIGO_VIOLET.finditer(text):
        _add(findings, "indigo_violet_primary", "warning", m.group(), text, m.start(), has_lines)
    if _SLATE_50.search(text) and _ZINC_900.search(text):
        m = _SLATE_50.search(text)
        _add(findings, "neutral_stack", "warning",
             "slate-50 + zinc-900 unmodified Tailwind neutral stack", text, m.start(), has_lines)


def _check_layout(text, findings, has_lines):
    if _GRID_COLS_3.search(text) and _ROUNDED_CARD.search(text):
        m = _GRID_COLS_3.search(text)
        _add(findings, "three_up_cards", "warning",
             "grid-cols-3 + rounded cards (possible three-up feature row)", text, m.start(), has_lines)
    if _HERO_CLASS.search(text) and _TEXT_CENTER.search(text):
        m = _HERO_CLASS.search(text)
        _add(findings, "centered_hero", "warning",
             "centered hero stack (hero class + text-center)", text, m.start(), has_lines)


def _check_copy(text, findings, has_lines):
    for phrase in COPY_TELLS:
        for m in re.finditer(re.escape(phrase), text, re.IGNORECASE):
            _add(findings, "copy_register", "warning", phrase, text, m.start(), has_lines)


def _check_title_case_headings(text, findings, has_lines):
    for m in _HTML_HEADING.finditer(text):
        heading = _TAG_STRIP.sub("", m.group(2)).strip()
        words = re.findall(r"\b[A-Za-z][a-zA-Z]*\b", heading)
        non_stop = [w for w in words if w.lower() not in _TITLE_STOP]
        if len(non_stop) < 3:
            continue
        cap_non_stop = sum(1 for w in non_stop if w[0].isupper())
        if cap_non_stop / len(non_stop) >= 0.8:
            _add(findings, "title_case_heading", "warning", heading, text, m.start(2), has_lines)


# Text-based checks that apply to HTML/SVG (and harmlessly to PPTX XML).
_TEXT_CHECKS = [
    _check_fonts, _check_colors, _check_gradient, _check_radius, _check_icons,
    _check_advisory_palette, _check_layout, _check_copy, _check_title_case_headings,
]


def scan_text(text, has_lines=True):
    """Run all text-based checks against an HTML/SVG/CSS string.

    Returns a list of finding dicts: {type, severity, tell, line, context}.
    """
    findings = []
    for check in _TEXT_CHECKS:
        check(text, findings, has_lines)
    return findings


# ============================================================
# PPTX
# ============================================================

def scan_pptx(path):
    """Scan a .pptx (a zip of OOXML parts) for font/color tells.

    Concatenates theme + slide + master XML and runs the text checks. Line
    numbers are meaningless across concatenated parts, so they are suppressed.
    """
    findings = []
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.startswith(("ppt/theme/", "ppt/slides/", "ppt/slideMasters/",
                                      "ppt/slideLayouts/")) and n.endswith(".xml")]
            blob = "\n".join(z.read(n).decode("utf-8", errors="replace") for n in names)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RuntimeError(f"cannot read pptx {path}: {exc}") from exc
    # Only font + color tells are meaningful in OOXML; class-based checks won't match.
    _check_fonts(blob, findings, has_lines=False)
    _check_colors(blob, findings, has_lines=False)
    return findings


# ============================================================
# Aggregation
# ============================================================

def audit_file(path, strict=False):
    """Audit a single artifact file; return {source, findings, summary, passed}."""
    path = Path(path)
    if path.suffix.lower() == ".pptx":
        findings = scan_pptx(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        findings = scan_text(text)

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {
        "source": str(path),
        "findings": findings,
        "summary": {
            "total_findings": len(findings),
            "errors": len(errors),
            "warnings": len(warnings),
            "by_type": dict(Counter(f["type"] for f in findings)),
        },
        "passed": len(errors) == 0 and (not strict or len(warnings) == 0),
    }


def _iter_files(root, include_internal):
    root = Path(root)
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SCAN_EXTENSIONS:
            continue
        rel = "/" + str(p).replace("\\", "/").strip("/") + "/"
        if not include_internal and any(skip in rel for skip in OUT_OF_SCOPE):
            continue
        yield p


# ============================================================
# Reporting
# ============================================================

def print_report(result):
    s = result["summary"]
    src = result["source"]
    if not result["findings"]:
        print(f"  {GREEN}{src}: clean - no visual AI-default tells found.{RESET}")
        return
    errs = [f for f in result["findings"] if f["severity"] == "error"]
    warns = [f for f in result["findings"] if f["severity"] == "warning"]
    print(f"\n  {BOLD}{src}: {s['errors']} error(s), {s['warnings']} warning(s).{RESET}")
    for e in errs[:25]:
        loc = f"L{e['line']}" if e["line"] else "part"
        print(f"    {RED}{e['type']}{RESET} ({loc}): {e['tell']}  {GRAY}{e['context']}{RESET}")
    if len(errs) > 25:
        print(f"    ...and {len(errs) - 25} more errors")
    for w in warns[:15]:
        loc = f"L{w['line']}" if w["line"] else "part"
        print(f"    {YELLOW}{w['type']}{RESET} ({loc}): {w['tell']}")
    if len(warns) > 15:
        print(f"    ...and {len(warns) - 15} more warnings")
    print(f"  {GRAY}Type summary: {s['by_type']}{RESET}")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Mechanical audit for AI-default visual tells in HTML/SVG/PPTX."
    )
    parser.add_argument("path", help="File or directory to audit")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    parser.add_argument("--include-internal", action="store_true",
                        help="Do not skip out-of-scope (internal/utility) directories")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"Error: {root} does not exist", file=sys.stderr)
        sys.exit(2)

    files = list(_iter_files(root, args.include_internal))
    if not files:
        print(f"  {GRAY}No HTML/SVG/PPTX artifacts found under {root}.{RESET}")
        sys.exit(0)

    results = []
    any_fail = False
    for f in files:
        try:
            res = audit_file(f, strict=args.strict)
        except RuntimeError as exc:
            print(f"  {RED}error{RESET}: {exc}", file=sys.stderr)
            any_fail = True
            continue
        results.append(res)
        if not res["passed"]:
            any_fail = True

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        total_e = sum(r["summary"]["errors"] for r in results)
        total_w = sum(r["summary"]["warnings"] for r in results)
        for res in results:
            print_report(res)
        print(f"\n  {BOLD}{len(results)} file(s) scanned: {total_e} error(s), {total_w} warning(s).{RESET}")

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
