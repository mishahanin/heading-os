#!/usr/bin/env python3
"""
visual-discipline-check.py - Mechanical audit for AI-default visual tells.

Visual-design counterpart to scripts/humanization-check.py. Where the
humanisation check scans prose for AI-text fingerprints, this scans visual
artifacts for the AI-default design tells named in
.claude/rules/visual-design-discipline.md.

TWO ENGINES, and the difference between them decides what each can be trusted
to say:

  regex (default, always runs)
      Matches patterns against file contents: forbidden fonts, the purple->pink
      hero gradient, oversized Tailwind radii, Lucide/Heroicons icon defaults,
      the ChatGPT-emerald and captured-pastel hero colors, plus heuristic layout
      and copy tells (advisory). Reads HTML, SVG and PPTX. It sees what is
      WRITTEN DOWN.

  deep (--deep, optional)
      The impeccable CLI (github.com/pbakaus/impeccable, Apache 2.0, pinned in
      scripts/.impeccable-version). Parses the HTML, resolves the CSS cascade,
      and computes real values: text contrast against the surface actually
      behind it, heading hierarchy, accent stripes on rounded corners, type
      floors. Reads HTML and SVG only - PPTX and PDF stay regex-only. It sees
      what RENDERS.

Deep findings carry an `impeccable:` type prefix so a reader can always tell
which engine made a claim. Calibration (print vs screen vs locked doctype),
plausibility bounds, and the per-(file, rule) baseline live in
scripts/utils/impeccable_engine.py and config/visual-check-profiles.json.

Neither engine judges the rule's first three fundamentals - specificity density,
committed stance, hierarchy by intent. Those stay human, against the exemplar
shelf.

Usage:
  python scripts/visual-discipline-check.py <file>            # one HTML/SVG/PPTX file
  python scripts/visual-discipline-check.py <dir>             # recurse for .html/.svg/.pptx
  python scripts/visual-discipline-check.py --deep <path>     # add the cascade-resolving engine
  python scripts/visual-discipline-check.py --strict <path>   # fail on warnings too
  python scripts/visual-discipline-check.py --json <path>     # JSON output
  python scripts/visual-discipline-check.py --profile print <path>   # force a calibration profile
  python scripts/visual-discipline-check.py --no-baseline <path>     # report frozen findings too
  python scripts/visual-discipline-check.py --include-internal <dir>  # do not skip out-of-scope dirs

  python scripts/visual-discipline-check.py baseline record --deep docs/  # freeze what exists
  python scripts/visual-discipline-check.py baseline check --deep docs/   # fail on regressions only

The baseline is a ratchet, same shape as .lint-baseline.json: `record` freezes
the findings present on existing artifacts, `check` fails only on findings ABOVE
those counts, and `check` never rewrites the file. Existing artifacts are not
remediated by this tool; new ones are held to the standard.

Severity:
  error   - high-confidence AI-default tell (forbidden font, purple->pink
            gradient, rounded-2xl/3xl, Lucide/Heroicons, banned hero color, or
            any non-advisory deep finding)
  warning - advisory / heuristic tell (neutral-stack pairing, indigo-violet
            primary, three-up cards, centered hero, Title Case heading, copy
            register, or an advisory deep finding). May false-positive; human
            review decides.

Exit codes:
  0 - clean (or strict-mode pass)
  1 - findings present (errors, or in strict mode warnings)
  2 - script error

An unavailable deep engine NEVER changes the exit code: it prints one
degradation line to stderr and the run completes on the regex verdict alone.
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

from scripts.utils import impeccable_engine

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
# `/vendor/` joined the list with the deep engine: impeccable is handed a directory
# and walks it itself, reaching third-party bundles nobody here designed.
OUT_OF_SCOPE = ("/outputs/operations/", "/outputs/clipboard/", "/outputs/browser/",
                "/_archive/", "/archive/", "/node_modules/", "/.git/", "/vendor/")

# Minified bundles are not a design surface. The regex walk never visits them
# (they are not in SCAN_EXTENSIONS), but the deep engine does, so the suffix list
# is enforced there too - see impeccable_engine.is_out_of_scope.
OUT_OF_SCOPE_SUFFIXES = (".min.js", ".min.css", ".min.mjs")

SCAN_EXTENSIONS = (".html", ".htm", ".svg", ".pptx")

# Documentation that describes this rule has to name what the rule forbids.
# Same marker and same purpose as the one scripts/humanization-check.py honours.
_AUDIT_SKIP = re.compile(
    r"<!--\s*audit-skip-start\s*-->[\s\S]*?<!--\s*audit-skip-end\s*-->",
    re.IGNORECASE,
)


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

    Content between `<!-- audit-skip-start -->` and `<!-- audit-skip-end -->` is
    blanked first, mirroring what scripts/humanization-check.py does for prose.
    Documentation about this rule legitimately NAMES the things the rule forbids:
    docs/DESIGN-CHECK.md was flagged for an icon library it only mentions in a
    sentence explaining that the library is a tell. Blanking rather than deleting
    keeps every line number downstream correct.
    """
    text = _AUDIT_SKIP.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
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

def audit_file(path, strict=False, deep_findings=None):
    """Audit a single artifact file; return {source, findings, summary, passed}.

    `deep_findings` is an ALREADY-COMPUTED list for this file, merged into the
    regex findings before the error/warning partition so `passed`, `--strict`
    and the exit codes keep their existing meaning with no special-casing.

    The deep engine is invoked ONCE per run, in main(), not once per file: it is
    handed a directory and walks it itself, and spawning `npx` per file would
    turn a 38-file scan into 38 cold starts. Passing the findings in also keeps
    this function pure and makes the default path provably untouched - fifteen
    skills already call this CLI, and none of them asked for a behaviour change.
    """
    path = Path(path)
    if path.suffix.lower() == ".pptx":
        findings = scan_pptx(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        findings = scan_text(text)

    if deep_findings:
        findings = findings + list(deep_findings)

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
        if p.name.lower().endswith(OUT_OF_SCOPE_SUFFIXES):
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

def _rebuild_result(result, findings, strict):
    """Recompute a result's summary and verdict after findings were filtered."""
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {
        "source": result["source"],
        "findings": findings,
        "summary": {
            "total_findings": len(findings),
            "errors": len(errors),
            "warnings": len(warnings),
            "by_type": dict(Counter(f["type"] for f in findings)),
        },
        "passed": len(errors) == 0 and (not strict or len(warnings) == 0),
    }


def _collect_deep(root, profile):
    """Run the deep engine ONCE and return {relative_path: [findings]}.

    Returns ({}, note) on any degradation. The caller reports the note and
    proceeds on the regex verdict - a missing Node must not invent a failure,
    and must not turn a real regex failure into a pass.
    """
    findings, note = impeccable_engine.deep_findings(root, profile_override=profile)
    if note:
        print(f"  {YELLOW}deep engine{RESET}: {note}", file=sys.stderr)

    grouped = {}
    for finding in findings:
        grouped.setdefault(finding.get("file", ""), []).append(finding)
    return grouped, note


def _run_audit(root, *, strict, deep, profile, use_baseline, include_internal):
    """Shared scan path for the default run and for `baseline check`.

    The baseline covers BOTH engines. Freezing only the deep findings would
    leave the gate permanently red on pre-existing regex debt (36 `Inter`
    declarations across docs/ alone), and a gate that is always red is a gate
    nobody reads. `--deep` decides which engines RUN; the baseline decides which
    findings are already accounted for.
    """
    files = list(_iter_files(root, include_internal))
    deep_map = {}
    if deep:
        deep_map, _ = _collect_deep(root, profile)

    # Collect every finding from both engines first, stamped with its file, then
    # apply the baseline ONCE to the whole set, then group for reporting.
    #
    # Filtering per file inside the walk was the first shape and it was wrong:
    # impeccable reads .css and .jsx, which SCAN_EXTENSIONS never walks, so those
    # findings arrived after the loop and skipped the baseline entirely. Two
    # frozen `side-tab` hits in docs/assets/docs.css failed a check that had just
    # recorded them. One filter over one list cannot drift that way.
    collected = []
    any_fail = False
    for f in files:
        key = impeccable_engine.relative_path(f)
        try:
            res = audit_file(f, strict=strict, deep_findings=deep_map.pop(key, None))
        except RuntimeError as exc:
            print(f"  {RED}error{RESET}: {exc}", file=sys.stderr)
            any_fail = True
            continue
        for finding in res["findings"]:
            collected.append(dict(finding, file=finding.get("file") or key))
        if not res["findings"]:
            collected.append({"_empty": key})

    # Deep findings on files the regex walk never visits. Reporting them is the
    # honest option: dropping them would silently narrow the gate to the
    # intersection of the two engines' file types.
    for key, extra in deep_map.items():
        for finding in extra:
            collected.append(dict(finding, file=finding.get("file") or key))

    real = [f for f in collected if "_empty" not in f]
    empties = [f["_empty"] for f in collected if "_empty" in f]
    if use_baseline:
        real = impeccable_engine.apply_baseline(real, impeccable_engine.load_baseline())

    grouped = {}
    for finding in real:
        grouped.setdefault(finding["file"], []).append(finding)
    for key in empties:
        grouped.setdefault(key, [])

    results = []
    for key in sorted(grouped):
        res = _rebuild_result({"source": key}, grouped[key], strict)
        results.append(res)
        if not res["passed"]:
            any_fail = True

    return results, any_fail


def _cmd_baseline(args):
    """`baseline record` freezes what exists; `baseline check` gates regressions."""
    root = Path(args.path)
    if not root.exists():
        print(f"Error: {root} does not exist", file=sys.stderr)
        return 2

    if args.action == "record":
        if args.deep and impeccable_engine.resolve_cli() is None:
            print(f"  {RED}refusing to record a baseline from a degraded run{RESET} "
                  f"(--deep was asked for and the CLI is unresolvable)", file=sys.stderr)
            return 2

        # Freeze what BOTH engines see, so the recorded line matches what a
        # later `check` will compare against. A record that captured only one
        # engine would leave the other's pre-existing debt failing forever.
        results, _ = _run_audit(
            root, strict=args.strict, deep=args.deep, profile=args.profile,
            use_baseline=False, include_internal=args.include_internal,
        )
        findings = []
        for res in results:
            for finding in res["findings"]:
                findings.append(dict(finding, file=finding.get("file", res["source"])))

        frozen = impeccable_engine.record_baseline(findings)
        total = sum(sum(rules.values()) for rules in frozen.values())
        print(f"  {GREEN}Baseline recorded{RESET}: {total} finding(s) across {len(frozen)} file(s).")
        print(f"  {GRAY}These are frozen, not fixed. The gate now fires only above them.{RESET}")
        return 0

    results, any_fail = _run_audit(
        root, strict=args.strict, deep=True, profile=args.profile,
        use_baseline=True, include_internal=args.include_internal,
    )
    above = sum(r["summary"]["total_findings"] for r in results)
    if any_fail:
        for res in results:
            print_report(res)
        print(f"\n  {RED}{above} finding(s) above the baseline.{RESET}")
        return 1
    print(f"  {GREEN}No findings above the baseline.{RESET}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Mechanical audit for AI-default visual tells in HTML/SVG/PPTX."
    )
    parser.add_argument("path", help="File or directory to audit")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    parser.add_argument("--include-internal", action="store_true",
                        help="Do not skip out-of-scope (internal/utility) directories")
    parser.add_argument("--deep", action="store_true",
                        help="Also run the impeccable engine (resolves the CSS cascade)")
    parser.add_argument("--profile", choices=("screen", "print", "doctype"),
                        help="Force a calibration profile instead of deriving it from the path")
    parser.add_argument("--no-baseline", action="store_true",
                        help="Report frozen findings too (do not apply .visual-baseline.json)")

    # The `baseline record|check <path>` form is peeled off before argparse.
    # Declaring it as extra optional positionals does not work: once argparse
    # has consumed an option it treats the remaining positionals as one closed
    # group, so `baseline record --deep docs/` loses the path. Peeling keeps the
    # long-standing `visual-discipline-check.py <path>` form byte-identical for
    # the fifteen skills that already call it.
    argv = sys.argv[1:]
    baseline_action = None
    if argv and argv[0] == "baseline":
        if len(argv) < 2 or argv[1] not in ("record", "check"):
            print("Error: baseline takes 'record' or 'check'", file=sys.stderr)
            sys.exit(2)
        baseline_action = argv[1]
        argv = argv[2:]

    args = parser.parse_args(argv)
    args.action = baseline_action

    if baseline_action:
        sys.exit(_cmd_baseline(args))

    root = Path(args.path)
    if not root.exists():
        print(f"Error: {root} does not exist", file=sys.stderr)
        sys.exit(2)

    files = list(_iter_files(root, args.include_internal))
    if not files and not args.deep:
        print(f"  {GRAY}No HTML/SVG/PPTX artifacts found under {root}.{RESET}")
        sys.exit(0)

    results, any_fail = _run_audit(
        root, strict=args.strict, deep=args.deep, profile=args.profile,
        use_baseline=not args.no_baseline, include_internal=args.include_internal,
    )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        total_e = sum(r["summary"]["errors"] for r in results)
        total_w = sum(r["summary"]["warnings"] for r in results)
        for res in results:
            print_report(res)
        engines = "regex + deep" if args.deep else "regex"
        print(f"\n  {BOLD}{len(results)} file(s) scanned ({engines}): "
              f"{total_e} error(s), {total_w} warning(s).{RESET}")

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
