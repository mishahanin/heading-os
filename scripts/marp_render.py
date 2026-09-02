#!/usr/bin/env python3
"""MARP rendering shim for 31C workspace.

Wraps marp-cli with brand theme injection, hidden-character sanitization,
workspace-aware defaults, frontmatter management, and watch-mode lifecycle.

Usage:
    python scripts/marp_render.py render <source.md> [--pdf-only] [--html-only] [--images png] [--output <dir>] [--verbose]
    python scripts/marp_render.py from <workspace-path.md> [--break-at h2|h3|manual] [--mode dark|light|mixed] [--title "..."] [--subtitle "..."] [--no-auto-cover] [--no-auto-closing] [--output <dir>] [--paginate-heavy] [--verbose]
    python scripts/marp_render.py watch <source.md>
    python scripts/marp_render.py watch stop
    python scripts/marp_render.py watch status
    python scripts/marp_render.py --self-test

Tests: tests/test_a_commit_that_swept_up_the_bystanders.py
"""

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.paths import DataRootError
from scripts.utils.workspace import (
    get_data_root,
    get_default_tz,
    get_outputs_dir,
    get_workspace_root,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils.markdown import parse_frontmatter as _parse_frontmatter_text
from scripts.utils.pid_liveness import pid_is_running
from scripts.utils.slugs import stable_suffix, transliterate

# ============================================================
# Configuration
# ============================================================

# --- Constants ---

WORKSPACE_ROOT = get_workspace_root()
SKILL_DIR = WORKSPACE_ROOT / ".claude" / "skills" / "marp"
THEME_TEMPLATE = SKILL_DIR / "themes" / "31c.css.tmpl"
# Selectable theme variants. Default "31c" is the internal-deck theme; "31c-investor"
# is the brand-forward investor-deck master (palette #423BFF/#FF9235, GT Standard L).
# THEME_TEMPLATE / THEME_NAME are reassigned in main() when --theme-variant is given,
# so prepare_theme() (which reads the module global) and inject_frontmatter() follow.
THEME_NAME = "31c"
THEME_VARIANTS = {
    "31c": (SKILL_DIR / "themes" / "31c.css.tmpl", "31c"),
    "31c-investor": (SKILL_DIR / "themes" / "31c-investor.css.tmpl", "31c-investor"),
}
FONTS_DIR = SKILL_DIR / "themes" / "fonts"
SAMPLE_DECK = SKILL_DIR / "examples" / "sample-deck.md"
VERSION_PIN_FILE = WORKSPACE_ROOT / "scripts" / ".marp-version"
WATCH_STATE_FILE = Path.home() / ".marp" / "watch.json"

# Everything reading the watch state can hit. All three readers listed only
# `(json.JSONDecodeError, KeyError)`, and the state file is the residue of a
# killed watch process, so a half-written or wrongly-encoded one is the ordinary
# case. `UnicodeDecodeError` is a ValueError and a SIBLING of JSONDecodeError,
# not a subclass, so `read_text(encoding="utf-8")` over a file saved as UTF-16
# went straight past the handler. MEASURED 2026-09-01: a watch.json written as
# UTF-16 raised UnicodeDecodeError out of `watch_status()`, which is documented
# to REPORT status and answers "Corrupt watch state file." for every other
# unreadable shape. OSError joins it because a directory, a permission change or
# a vanished home all reach the same read.
_WATCH_STATE_UNREADABLE = (json.JSONDecodeError, KeyError, OSError,
                           UnicodeDecodeError)
WORD_OVERFLOW_THRESHOLD = 150
DEFAULT_FOOTER = "(C) 2025-2026 - 31 Concept - 31C.io - Proprietary & Confidential"

# Workspace-aware defaults for /marp from
WORKSPACE_DEFAULTS = {
    "context/": {"mode": "light", "subtitle": "Operating Context - 31 Concept"},
    "knowledge/": {"mode": "light", "subtitle": "From the brain - {date}"},
    "reference/": {"mode": "light", "subtitle": "Reference - 31 Concept"},
    "outputs/intel/": {"mode": "dark", "subtitle": "Intelligence - Classified"},  # leak-guard: ok (workspace-path prefix-match key)
    "outputs/operations/": {"mode": "dark", "subtitle": "Operations - 31 Concept"},  # leak-guard: ok (workspace-path prefix-match key)
    "outputs/proposals/": {"mode": "mixed", "subtitle": "{filename} - Proposal"},  # leak-guard: ok (workspace-path prefix-match key)
}


def default_output_dir() -> Path:
    """Resolved at call time, never at import.

    `get_outputs_dir()` reads `HEADING_OS_DATA` on every call, so it follows
    the environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the data
    root still rendered decks into the operator's real overlay.
    """
    return get_outputs_dir() / "deliverables" / "presentations"


# ============================================================
# Marp CLI Detection
# ============================================================


def get_pinned_version() -> str:
    """Read the pinned marp-cli version string."""
    if VERSION_PIN_FILE.exists():
        return VERSION_PIN_FILE.read_text(encoding="utf-8").strip()
    return "@marp-team/marp-cli@4.4.0"


def _resolve_marp_bin() -> str | None:
    """Resolve the marp executable path. On Windows, npm installs .cmd wrappers
    that subprocess can't find without shell=True. shutil.which handles this."""
    return shutil.which("marp")


def check_marp_installed() -> tuple[bool, str]:
    """Check if marp-cli is installed and return (ok, version_string)."""
    marp_bin = _resolve_marp_bin()
    if not marp_bin:
        return False, ""
    try:
        result = subprocess.run(
            [marp_bin, "--version"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            return True, version
        return False, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


# The first version-looking token in `marp --version` output belongs to
# marp-cli itself; anything after it names a bundled package.
_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?")


def check_version_match(installed_version: str) -> bool:
    """Check if installed version matches pinned version.

    Compares the FIRST version token exactly. The test was
    `pinned_num in installed_version`, and a substring match is not a version
    match. MEASURED 2026-08-29 on this machine, where the pin reads
    `@marp-team/marp-cli@4.4.0` and marp prints
    `@marp-team/marp-cli v4.5.0 (w/ @marp-team/marp-core v4.4.0)`: the pin
    matched the bundled marp-core, so `render` and `--self-test` both reported
    the version as matching while a different marp-cli did the rendering. Same
    hole for `14.4.0` and `4.4.0-beta.1`, which also answered True.

    An unparsable version string answers False, so an unrecognised format warns
    rather than claims a match it never established.
    """
    pinned = get_pinned_version()
    # Extract version number from pin (e.g., "@marp-team/marp-cli@4.1.1" -> "4.1.1")
    pinned_num = pinned.rsplit("@", 1)[-1] if "@" in pinned else pinned
    m = _VERSION_TOKEN_RE.search(installed_version)
    if not m:
        return False
    return m.group(0) == pinned_num


# ============================================================
# Browser & Theme Setup
# ============================================================


def probe_browser() -> str | None:
    """Detect system browser for PDF rendering. Returns path or None."""
    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    else:
        # Linux: check PATH using shutil.which (avoids subprocess for portability).
        # Marp uses Puppeteer under the hood, which requires a Chromium-family
        # browser (Chrome DevTools Protocol). Firefox does NOT work here.
        # Candidate order: Google's own packages first, then the distro
        # chromium package names, then brave and edge. All of them are looked
        # up on PATH by `shutil.which`; no snap-specific path is probed, and a
        # snap only shows up here if its wrapper is on PATH under one of these
        # names. The comment used to claim distro-first and snap paths, and was
        # wrong about both the order and the mechanism.
        for name in [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",  # Debian/Ubuntu package name
            "brave-browser",
            "brave-browser-stable",
            "brave",
            "microsoft-edge",
            "microsoft-edge-stable",
        ]:
            path = shutil.which(name)
            if path:
                return path
        return None

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


MARP_TIMEOUT_S = 120


def _run_marp(cmd: list):
    """Run one marp-cli invocation. Returns the CompletedProcess, or None on timeout.

    Every other failure in `render` becomes a structured `{"ok": False, ...}`
    result, but `subprocess.run(..., timeout=120)` RAISES on a hang, and that
    exception walked straight out of `render` and killed the CLI with a
    traceback. The hang is not hypothetical: this file's own PDF error branch
    exists because the first run downloads ~150 MB of Chromium.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=MARP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None


def theme_missing_result() -> dict:
    """The structured result for an unreadable theme template.

    `prepare_theme` reads `THEME_TEMPLATE` and raises `OSError` when it is
    absent, and that exception used to walk straight out of `render`,
    `transform_workspace_md` and `watch_start`. MEASURED 2026-08-29 by pointing
    `THEME_TEMPLATE` at a path that does not exist: `render` died with a raw
    `FileNotFoundError` traceback instead of the `{"ok": False, ...}` every
    other failure in this file returns. The template can genuinely be absent on
    a partial skill install, or when `--theme-variant` names a variant whose
    file was never added.
    """
    return {"ok": False, "error": "theme-missing",
            "message": f"Theme template not readable: {THEME_TEMPLATE}. "
                       f"Check the marp skill install, or the --theme-variant name."}


# A theme template names its faces as `{FONT_<KEY>}`, resolved against the
# private brand manifest by lowercasing the token into `font_<key>`. One
# mechanism, no per-template table: a new face is a placeholder in the CSS and an
# entry in the manifest, and nothing in this file has to learn about it.
_FONT_TOKEN_RE = re.compile(r"\{FONT_([A-Z0-9_]+)\}")

# What a font placeholder becomes when the manifest cannot answer. It is not a
# real filename and is not meant to resolve: the `src: url(...)` names a file
# that is not there, so Chromium falls back to a system sans. That is EXACTLY
# the state a public clone is already in, because the licensed faces in
# `themes/fonts/` are gitignored and `themes/fonts/README.md` documents the
# fallback as normal. Refusing to render instead would take a working deck away
# from a clone that never had the fonts.
_UNRESOLVED_FONT = "unresolved-brand-face.woff2"


def substitute_theme_fonts(template_text: str) -> str:
    """Replace every `{FONT_<KEY>}` in a theme template with a real filename.

    The filenames were written into the two `.css.tmpl` themes until 2026-09-02,
    when the operator ruled that a datastore filename is itself private and this
    repository is public. `scripts/utils/brand_assets.py` carries the reasoning;
    this is the substitution end of it, extending the `{FONTS_DIR}` mechanism
    that was already here rather than inventing a second one.

    Degrades rather than raises, and says so once on stderr. See `_UNRESOLVED_FONT`.
    """
    from scripts.utils.brand_assets import BrandAssetError, brand_asset_name

    # `DataRootError` is deliberately NOT an OSError (scripts/utils/paths.py), so
    # a set-but-missing `HEADING_OS_DATA` would otherwise walk out of here as a
    # traceback and kill a render that used to succeed. `workspace_relative` in
    # this same file already catches it and says so; this matches that, because
    # resolving a font NAME is a read and cannot write anything anywhere wrong.
    try:
        manifest = load_brand_manifest()
    except (BrandAssetError, DataRootError) as exc:
        manifest = None
        print(f"{GRAY}marp: {exc} Rendering with system fallback faces.{RESET}",
              file=sys.stderr)

    missing: list[str] = []

    def resolve(match: re.Match) -> str:
        key = f"font_{match.group(1).lower()}"
        if manifest is None:
            return _UNRESOLVED_FONT
        try:
            return brand_asset_name(key, manifest)
        except BrandAssetError:
            missing.append(key)
            return _UNRESOLVED_FONT

    resolved = _FONT_TOKEN_RE.sub(resolve, template_text)
    if missing:
        print(f"{GRAY}marp: the brand manifest has no entry for "
              f"{', '.join(sorted(set(missing)))}; those faces fall back to a "
              f"system sans.{RESET}", file=sys.stderr)
    return resolved


def load_brand_manifest() -> dict:
    """Read the brand manifest once per render.

    A thin seam of its own so a test can drive `substitute_theme_fonts` against a
    scratch manifest without a data overlay, and so the read happens once rather
    than once per placeholder.
    """
    from scripts.utils.brand_assets import load_manifest

    return load_manifest()


def prepare_theme() -> Path:
    """Substitute {FONTS_DIR} and the font names in the theme template.

    Writes the result to a temp file and returns its path. Raises OSError when
    the template is missing; callers turn that into `theme_missing_result()`.
    """
    template_text = THEME_TEMPLATE.read_text(encoding="utf-8")
    # Path.as_uri() properly percent-encodes spaces and special characters
    # (e.g., a space becomes "%20", a parenthesis becomes "%28"), which is
    # critical since marp-cli renders via Chromium which needs valid file:// URIs.
    fonts_uri = FONTS_DIR.resolve().as_uri()
    resolved = template_text.replace("{FONTS_DIR}", fonts_uri)
    resolved = substitute_theme_fonts(resolved)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".css", prefix="31c-marp-",
        delete=False, encoding="utf-8"
    )
    tmp.write(resolved)
    tmp.close()
    return Path(tmp.name)


# ============================================================
# Text Processing & Frontmatter
# ============================================================


def run_sanitizer(text: str) -> tuple[str, int]:
    """Run hidden-character sanitization in-memory. Returns (clean_text, count_found)."""
    # Inline detection of common hidden Unicode characters
    hidden_chars = {
        "\u200b": "ZERO WIDTH SPACE",
        "\u200c": "ZERO WIDTH NON-JOINER",
        "\u200d": "ZERO WIDTH JOINER",
        "\u00ad": "SOFT HYPHEN",
        "\u00a0": "NO-BREAK SPACE",
        "\u200e": "LEFT-TO-RIGHT MARK",
        "\u200f": "RIGHT-TO-LEFT MARK",
        "\u2060": "WORD JOINER",
        "\ufeff": "BOM",
        "\u2061": "FUNCTION APPLICATION",
        "\u2062": "INVISIBLE TIMES",
        "\u2063": "INVISIBLE SEPARATOR",
        "\u2064": "INVISIBLE PLUS",
        "\u180e": "MONGOLIAN VOWEL SEPARATOR",
        "\u202a": "LEFT-TO-RIGHT EMBEDDING",
        "\u202b": "RIGHT-TO-LEFT EMBEDDING",
        "\u202c": "POP DIRECTIONAL FORMATTING",
        "\u202d": "LEFT-TO-RIGHT OVERRIDE",
        "\u202e": "RIGHT-TO-LEFT OVERRIDE",
    }
    # A NO-BREAK SPACE is a SPACE. Deleting it joined the words around it, so
    # `10 pages` came out `10pages` -- silently, in rendered deck text, from a
    # character Option+Space types on macOS. Every other entry above is
    # zero-width and correctly disappears.
    replacements = {"\u00a0": " "}
    count = 0
    clean = text
    for char in hidden_chars:
        found = clean.count(char)
        if found > 0:
            count += found
            clean = clean.replace(char, replacements.get(char, ""))
    return clean, count


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Parse YAML frontmatter from markdown text. Returns (metadata, body).

    Thin wrapper around ``scripts.utils.markdown.parse_frontmatter`` that
    returns ``None`` (instead of an empty dict) when no frontmatter is
    present and ``.strip()``s the body, matching the legacy contract that
    ``inject_frontmatter`` and ``paginate_heavy`` callers depend on.

    The shared util uses ``yaml.safe_load`` (when PyYAML is installed) so
    booleans, lists, and quoted strings parse natively - the local bool
    coercion is no longer needed.
    """
    if not text.startswith("---"):
        return None, text
    data, body = _parse_frontmatter_text(text)
    if not data:
        return None, text
    return data, body.strip()


def yaml_scalar(value) -> str:
    """Render one frontmatter value as a YAML scalar.

    One emitter for both frontmatter writers (`inject_frontmatter` and
    `paginate_heavy`), which carried the same three-branch f-string. Values
    merged in from an existing deck come from `yaml.safe_load`, so a key with
    no value arrives as `None`, and `f"{key}: {value}"` wrote `reviewed_by:
    None`. MEASURED 2026-08-29 on a deck whose frontmatter reads
    `reviewed_by:`: the round trip through `inject_frontmatter` turned a YAML
    null into the four-letter STRING "None", silently, on every render.
    """
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "null"
    if isinstance(value, str) and (" " in value or ":" in value):
        # json.dumps, not an f-string. A double-quoted YAML scalar uses the
        # same escaping as JSON, so this handles an embedded `"` or newline,
        # which the bare interpolation did not: a title of Says "hello"
        # emitted `title: "Says "hello""`, and marp then failed to parse the
        # frontmatter or rendered garbled metadata.
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def inject_frontmatter(source_text: str, title: str = "", mode: str = "dark",
                       subtitle: str = "", classification: str = "ceo-only") -> str:
    """Inject MARP frontmatter if missing, or merge with existing."""
    existing_fm, body = parse_frontmatter(source_text)

    fm = {
        "marp": True,
        "theme": THEME_NAME,
        "paginate": True,
        "size": "16:9",
        "class": mode,
        "title": title or "Untitled",
        "author": "Misha Hanin",
        "date": datetime.now(get_default_tz()).strftime("%Y-%m-%d"),
        "classification": classification,
        "footer": DEFAULT_FOOTER,
    }

    if existing_fm:
        # Preserve existing values, only fill missing
        for key, value in fm.items():
            if key not in existing_fm:
                existing_fm[key] = value
        fm = existing_fm

    if title:
        fm["title"] = title
    if subtitle:
        fm["subtitle"] = subtitle

    # Build frontmatter string
    lines = ["---"]
    for key, value in fm.items():
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    lines.append("")

    return "\n".join(lines) + "\n" + body


def strip_wiki_links(text: str) -> str:
    """Convert [[id]] to id and [[id|Display]] to Display."""
    text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    return text


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def fence_mask(lines: list) -> list:
    """One bool per line: True when that line sits INSIDE a fenced code block.

    The fence delimiters themselves count as inside, so nothing is ever
    inserted next to one.

    Every splitter and scanner in this file used to be blind to fences, and a
    deck ABOUT markdown is the ordinary case here. A `## Example` inside a
    ```markdown fence got a `---` slide break pushed in front of it, splitting
    the fence across two slides; a literal `---` inside a fence made
    `auto_slide_breaks` decide manual breaks already existed and do nothing at
    all, and made `check_overflow` and `paginate_heavy` count one slide as two.

    The fence LENGTH is tracked alongside its character, because CommonMark
    (and Marpit) close a fence only on a run at least as long as the opener,
    and a four-backtick outer fence wrapping a three-backtick sample is the
    canonical way to show a code block inside a deck about markdown. MEASURED
    2026-08-29 on that exact deck: the mask went False at the inner three
    backticks, `auto_slide_breaks` read the `## Example` after it as a real H2,
    and pushed a `---` slide break into the middle of the outer fence.
    """
    mask = []
    fence = None  # (character, length) of the open fence
    for line in lines:
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
            mask.append(m is not None)
            continue
        mask.append(True)
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
            fence = None
    return mask


def split_slides(body: str) -> list:
    """Split on standalone `---` lines that are NOT inside a code fence."""
    lines = body.split("\n")
    mask = fence_mask(lines)
    slides, current = [], []
    for line, inside in zip(lines, mask, strict=True):
        if not inside and line.strip() == "---":
            slides.append("\n".join(current))
            current = []
            continue
        current.append(line)
    slides.append("\n".join(current))
    return slides


def auto_slide_breaks(body: str, break_at: str = "h2") -> str:
    """Insert slide breaks at heading level if no manual breaks exist.
    If the body already contains standalone '---' lines (slide breaks),
    respect them and return unchanged. A `---` inside a code fence is content,
    not a break, and does not count as a manual one."""
    lines = body.split("\n")
    mask = fence_mask(lines)

    # Check for existing manual slide breaks (standalone --- lines)
    for line, inside in zip(lines, mask, strict=True):
        if not inside and line.strip() == "---":
            return body

    heading_pattern = {"h2": r"^## ", "h3": r"^### "}
    pattern = heading_pattern.get(break_at, r"^## ")

    result = []
    for i, (line, inside) in enumerate(zip(lines, mask, strict=True)):
        if i > 0 and not inside and re.match(pattern, line):
            result.append("")
            result.append("---")
            result.append("")
        result.append(line)
    return "\n".join(result)


# ============================================================
# Workspace Defaults & Slide Helpers
# ============================================================


def workspace_relative(source_path: Path) -> str:
    """The path as this workspace names it, resolved against EITHER root.

    Four of the five prefixes in `WORKSPACE_DEFAULTS` -- `context/`,
    `knowledge/`, `outputs/intel/`, `outputs/operations/` -- exist only in the
    private DATA overlay on the two-part topology, never in the engine clone.
    Resolving against `WORKSPACE_ROOT` alone therefore made the whole table
    dead: `relative_to` raised, `rel` became an absolute path, no prefix could
    match it, and every real `/marp from` fell through to "mixed" with the bare
    filename as its subtitle. Measured 2026-08-26 on this workspace, where the
    engine clone holds none of the four.

    `reference/` is the one key present in both, and it is read from whichever
    root the caller's own file is under -- the first match wins, engine first.
    """
    resolved = source_path.resolve()
    roots = [WORKSPACE_ROOT]
    try:
        data_root = get_data_root()
    except DataRootError as exc:
        # Named, never swallowed: a misconfigured overlay silently halving the
        # lookup is the failure this function exists to end.
        print(f"marp: cannot resolve the data root ({exc}); "
              f"workspace defaults will only match the engine tree", file=sys.stderr)
    else:
        if data_root != WORKSPACE_ROOT:
            roots.append(data_root)

    for root in roots:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(source_path)


def get_workspace_defaults(source_path: Path) -> dict:
    """Determine mode and subtitle defaults based on source directory."""
    rel = workspace_relative(source_path)

    for prefix, defaults in WORKSPACE_DEFAULTS.items():
        if rel.startswith(prefix):
            subtitle = defaults["subtitle"]
            subtitle = subtitle.replace("{date}", datetime.now(get_default_tz()).strftime("%Y-%m-%d"))
            subtitle = subtitle.replace("{filename}", source_path.stem)
            return {"mode": defaults["mode"], "subtitle": subtitle}

    return {"mode": "mixed", "subtitle": source_path.stem}


def generate_slug(topic: str) -> str:
    """URL-safe slug from a topic, with a Cyrillic title kept rather than erased.

    Without the transliteration pass this returned '' for any Cyrillic input, so
    the deck stem `31C-{title}-{date}` collapsed to `31C--27-Aug-2026` and every
    Russian-titled deck rendered that day shared one name. Measured 2026-08-27:
    `generate_slug('Стратегия развития') == ''`.
    """
    slug = transliterate(topic).lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug and topic.strip():
        return stable_suffix(topic)
    return slug[:60]


def check_overflow(source_text: str) -> list[dict]:
    """Check for slides exceeding word threshold. Returns list of warnings."""
    _, body = parse_frontmatter(source_text)
    slides = split_slides(body)
    warnings = []
    for i, slide in enumerate(slides):
        words = len(slide.split())
        if words > WORD_OVERFLOW_THRESHOLD:
            warnings.append({"slide": i + 1, "words": words})
    return warnings


def fence_aware_paragraphs(slide: str) -> list:
    """Split a slide on blank lines that are NOT inside a code fence.

    `split_slides` was taught about fences; this second splitter was not, and
    `re.split(r"\\n\\n+", slide)` chops on any blank line. A blank line inside a
    fenced code block is legal and ordinary. MEASURED 2026-08-29 on a 181-word
    slide holding one ```python block with a blank line in it: `paginate_heavy`
    split on that blank line and produced two slides carrying one fence
    delimiter each, so the opener never closed and the rest of the deck
    rendered as code.
    """
    lines = slide.split("\n")
    mask = fence_mask(lines)
    paragraphs, current = [], []
    for line, inside in zip(lines, mask, strict=True):
        if not inside and not line.strip():
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def paginate_heavy(source_text: str) -> str:
    """Sub-break heavy slides on paragraph boundaries."""
    fm, body = parse_frontmatter(source_text)
    slides = split_slides(body)
    new_slides = []
    for slide in slides:
        words = len(slide.split())
        if words > WORD_OVERFLOW_THRESHOLD:
            paragraphs = fence_aware_paragraphs(slide)
            if len(paragraphs) > 1:
                current = []
                current_words = 0
                for para in paragraphs:
                    para_words = len(para.split())
                    if current_words + para_words > WORD_OVERFLOW_THRESHOLD and current:
                        new_slides.append("\n\n".join(current))
                        current = [para]
                        current_words = para_words
                    else:
                        current.append(para)
                        current_words += para_words
                if current:
                    new_slides.append("\n\n".join(current))
            else:
                new_slides.append(slide)
        else:
            new_slides.append(slide)

    # Reconstruct with frontmatter
    if fm is not None:
        fm_lines = ["---"]
        for key, value in fm.items():
            fm_lines.append(f"{key}: {yaml_scalar(value)}")
        fm_lines.append("---")
        return "\n".join(fm_lines) + "\n\n" + "\n\n---\n\n".join(new_slides)
    return "\n\n---\n\n".join(new_slides)


# ============================================================
# Rendering / Core Logic
# ============================================================


def render(source: Path, output_dir: Path = None, pdf_only: bool = False,
           html_only: bool = False, images_png: bool = False,
           auto_sanitize: bool = True, verbose: bool = False,
           output_stem: str = None) -> dict:
    """Render a MARP markdown file to PDF and/or HTML. Returns result dict.
    output_stem overrides the filename stem (without extension) for output files."""
    if not source.exists():
        return {"ok": False, "error": "source-not-found",
                "message": f"Source not found: {source}. Check the path and try again."}

    # `--pdf-only --html-only` skips the PDF branch AND the HTML branch, and
    # neither branch records an error when it is skipped. MEASURED 2026-08-29:
    # the call answered {"ok": True, "outputs": [], "errors": []}, so the CLI
    # printed "Render successful" and exited 0 having written no file at all.
    # A scripted pipeline reading that exit code ships nothing and says it won.
    if pdf_only and html_only:
        return {"ok": False, "error": "conflicting-format-flags",
                "message": "--pdf-only and --html-only exclude each other; "
                           "passing both would render nothing. Pass one, or neither."}

    # Check marp-cli
    installed, version_str = check_marp_installed()
    if not installed:
        pinned = get_pinned_version()
        return {"ok": False, "error": "marp-not-installed",
                "message": f"marp-cli not installed. Run: npm install -g {pinned}"}

    if not check_version_match(version_str):
        pinned = get_pinned_version()
        pinned_num = pinned.rsplit("@", 1)[-1]
        print(f"{YELLOW}Warning: marp-cli version {version_str} differs from pinned {pinned_num}. "
              f"Renders may differ. Continuing.{RESET}")

    # Read source
    source_text = source.read_text(encoding="utf-8")

    # Sanitize
    clean_text, hidden_count = run_sanitizer(source_text)
    if hidden_count > 0:
        if not auto_sanitize:
            sanitized_path = source.with_suffix(".sanitized.md")
            sanitized_path.write_text(clean_text, encoding="utf-8")
            return {"ok": False, "error": "hidden-chars-detected",
                    "message": f"Hidden characters detected ({hidden_count}). "
                               f"Sanitized copy written: {sanitized_path}. "
                               f"Re-run with --auto-sanitize to render."}
        print(f"{YELLOW}Hidden characters: {hidden_count} found, rendering from sanitized copy.{RESET}")
        source_text = clean_text

    # Prepare theme
    try:
        theme_path = prepare_theme()
    except OSError:
        return theme_missing_result()

    # Write temp source (never mutate original). Place it next to the
    # original so relative image paths (e.g. `![bg](_assets/foo.png)`)
    # resolve from the same directory as the source MD.
    tmp_source = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix=f".{source.stem}.marp-src-",
        dir=str(source.parent),
        delete=False, encoding="utf-8"
    )
    tmp_source.write(source_text)
    tmp_source.close()
    tmp_source_path = Path(tmp_source.name)

    out_dir = output_dir or source.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem or source.stem

    outputs = []
    errors = []

    try:
        # Probe browser
        browser_path = probe_browser()

        # Build base command
        marp_bin = _resolve_marp_bin()
        base_cmd = [
            marp_bin,
            str(tmp_source_path),
            "--theme", str(theme_path),
            "--allow-local-files",
        ]
        # The investor master expresses multi-element grid layouts (stat rows, feature
        # cards, comparison panels) with inline HTML, so enable HTML for that variant
        # only. Internal decks keep HTML disabled (default) for safety.
        if THEME_NAME == "31c-investor":
            base_cmd.append("--html")
        if browser_path:
            base_cmd.extend(["--browser-path", browser_path])

        # Render PDF
        if not html_only:
            pdf_out = out_dir / f"{stem}.pdf"
            cmd = base_cmd + ["--pdf", "-o", str(pdf_out)]
            if verbose:
                print(f"{GRAY}Running: {' '.join(cmd)}{RESET}")
            result = _run_marp(cmd)
            if result is None:
                errors.append({"type": "pdf", "error": "timeout",
                               "message": f"marp-cli did not finish within "
                                          f"{MARP_TIMEOUT_S}s. The first PDF downloads "
                                          f"Chromium (~150MB); re-run once it completes."})
            elif result.returncode == 0 and pdf_out.exists():
                outputs.append({"type": "pdf", "path": str(pdf_out), "size": pdf_out.stat().st_size})
            else:
                err_msg = result.stderr.strip() if result.stderr else "Unknown error"
                if "Could not find" in err_msg or "browser" in err_msg.lower():
                    errors.append({"type": "pdf", "error": "chromium-missing",
                                   "message": "First PDF needs Chromium. marp-cli is downloading now "
                                              "(~150MB, one-time). Re-run after it completes."})
                else:
                    errors.append({"type": "pdf", "error": "render-failed",
                                   "message": f"Render failed. marp-cli output:\n{err_msg}"})
                    if verbose:
                        print(f"{RED}STDERR: {result.stderr}{RESET}")
                        print(f"{GRAY}STDOUT: {result.stdout}{RESET}")

        # Render HTML (output format determined by -o extension, not a flag)
        if not pdf_only:
            html_out = out_dir / f"{stem}.html"
            cmd = base_cmd + ["-o", str(html_out)]
            if verbose:
                print(f"{GRAY}Running: {' '.join(cmd)}{RESET}")
            result = _run_marp(cmd)
            if result is None:
                errors.append({"type": "html", "error": "timeout",
                               "message": f"marp-cli did not finish within {MARP_TIMEOUT_S}s."})
            elif result.returncode == 0 and html_out.exists():
                outputs.append({"type": "html", "path": str(html_out), "size": html_out.stat().st_size})
                # Deep design verdict on the deck we just rendered. Slides are
                # read on a screen, so the screen profile applies. Reports only:
                # a design finding must never cost a rendered deck.
                try:
                    from scripts.utils import impeccable_engine
                    impeccable_engine.report_for_artifact(html_out, profile="screen")
                except ImportError:
                    pass
            else:
                err_msg = result.stderr.strip() if result.stderr else "Unknown error"
                errors.append({"type": "html", "error": "render-failed",
                               "message": f"Render failed. marp-cli output:\n{err_msg}"})

        # Render PNG images
        if images_png:
            img_out = out_dir / f"{stem}.001.png"
            # Fingerprint what is already on disk under this stem. The glob
            # after the run matches every `{stem}.*.png` in the directory, not
            # what this invocation wrote, and output directories are reused.
            # MEASURED 2026-08-29 with a stub marp that exits 0 and writes
            # nothing: a `deck.004.png` left by an earlier run was reported as
            # a fresh output of this one, and its presence satisfied the
            # no-output check below, which exists to catch exactly that.
            # Size rides along with mtime so a same-second rewrite of a
            # different length still reads as new.
            before = {}
            for png in out_dir.glob(f"{stem}.*.png"):
                st = png.stat()
                before[png] = (st.st_mtime_ns, st.st_size)
            cmd = base_cmd + ["--images", "png", "-o", str(out_dir / f"{stem}.png")]
            if verbose:
                print(f"{GRAY}Running: {' '.join(cmd)}{RESET}")
            result = _run_marp(cmd)
            if result is None:
                errors.append({"type": "png", "error": "timeout",
                               "message": "marp-cli did not finish within 120s."})
            elif result.returncode == 0:
                # Count the PNGs THIS run wrote: new files, plus ones whose
                # fingerprint moved.
                pngs = []
                for png in sorted(out_dir.glob(f"{stem}.*.png")):
                    st = png.stat()
                    if before.get(png) != (st.st_mtime_ns, st.st_size):
                        pngs.append(png)
                for png in pngs:
                    outputs.append({"type": "png", "path": str(png), "size": png.stat().st_size})
                # A zero exit with no files is still a failure. The PDF and HTML
                # paths both record their failures; this one recorded none, so
                # "Render successful" printed with the requested PNGs simply
                # absent and `ok` stayed True.
                if not pngs:
                    errors.append({"type": "png", "error": "no-output",
                                   "message": f"marp-cli exited 0 but wrote no new "
                                              f"{stem}.*.png in {out_dir}."})
            else:
                err_msg = result.stderr.strip() if result.stderr else "Unknown error"
                errors.append({"type": "png", "error": "render-failed",
                               "message": f"Render failed. marp-cli output:\n{err_msg}"})

        # Check overflow
        overflow_warnings = check_overflow(source_text)

    finally:
        # Clean up temp files
        tmp_source_path.unlink(missing_ok=True)
        theme_path.unlink(missing_ok=True)

    sanitizer_status = "clean" if hidden_count == 0 else f"{hidden_count} found, rendered from sanitized copy"

    return {
        "ok": len(errors) == 0,
        "outputs": outputs,
        "errors": errors,
        "overflow_warnings": overflow_warnings,
        "hidden_characters": sanitizer_status,
        "marp_version": version_str,
    }


# ============================================================
# Workspace Markdown Transform
# ============================================================


def transform_workspace_md(source: Path, break_at: str = "h2", mode: str = None,
                           title: str = None, subtitle: str = None,
                           no_auto_cover: bool = False, no_auto_closing: bool = False,
                           do_paginate_heavy: bool = False, output_dir: Path = None,
                           verbose: bool = False) -> dict:
    """Transform a workspace markdown doc into MARP slides without modifying source."""
    if not source.exists():
        return {"ok": False, "error": "source-not-found",
                "message": f"Source not found: {source}"}

    source_text = source.read_text(encoding="utf-8")

    # Parse existing frontmatter
    existing_fm, body = parse_frontmatter(source_text)

    # Determine title
    doc_title = title
    if not doc_title and existing_fm and "title" in existing_fm:
        doc_title = existing_fm["title"]
    if not doc_title:
        # Use first H1
        h1_match = re.search(r"^# (.+)$", body, re.MULTILINE)
        if h1_match:
            doc_title = h1_match.group(1).strip()
    if not doc_title:
        doc_title = source.stem

    # Workspace-aware defaults
    ws_defaults = get_workspace_defaults(source)
    slide_mode = mode or ws_defaults["mode"]
    slide_subtitle = subtitle or ws_defaults["subtitle"]

    # Strip wiki-links
    body = strip_wiki_links(body)

    # Insert slide breaks
    if break_at != "manual":
        body = auto_slide_breaks(body, break_at)

    # Build slides
    slides = []

    # Auto cover slide
    if not no_auto_cover:
        try:
            rel_path = source.resolve().relative_to(WORKSPACE_ROOT).as_posix()
        except ValueError:
            rel_path = source.name
        cover_mode = "dark" if slide_mode in ("dark", "mixed") else "light"
        cover = (
            f'<!-- _class: "title {cover_mode}" -->\n'
            f"<!-- _paginate: false -->\n"
            f'<!-- _footer: "" -->\n\n'
            f"# {doc_title}\n"
            f"## {slide_subtitle}\n\n"
            f"Source: {rel_path}\n"
            f"{datetime.now(get_default_tz()).strftime('%Y-%m-%d')}"
        )
        slides.append(cover)

    # Content slides
    slides.append(body)

    # Auto closing slide
    if not no_auto_closing:
        close_mode = "dark" if slide_mode in ("dark", "mixed") else "light"
        closing = (
            f'<!-- _class: "closing {close_mode}" -->\n'
            f"<!-- _paginate: false -->\n"
            f'<!-- _footer: "" -->\n\n'
            f"# End\n\n"
            f"31 Concept"
        )
        slides.append(closing)

    # Assemble full source
    full_body = "\n\n---\n\n".join(slides)

    # Inject frontmatter. `subtitle=` was omitted, so `slide_subtitle` reached
    # the cover slide and nothing else. MEASURED 2026-08-29 by walking the AST
    # of this function: the single `inject_frontmatter` call passed title, mode
    # and classification only, which left the `subtitle` parameter of
    # `inject_frontmatter` with no caller anywhere in the file and every deck
    # generated by `from` without a `subtitle:` key, whatever `--subtitle` or
    # the WORKSPACE_DEFAULTS table said.
    full_source = inject_frontmatter(full_body, title=doc_title, mode=slide_mode,
                                     subtitle=slide_subtitle,
                                     classification="ceo-only")

    # Paginate heavy if requested
    if do_paginate_heavy:
        full_source = paginate_heavy(full_source)

    # Write to temp file and render
    # `dir=source.parent`, for the reason `render` states about its own temp
    # copy: relative image paths resolve from the directory of the file being
    # rendered. This one landed in the system temp dir, `render` then put ITS
    # copy beside it, and every `![x](assets/a.png)` in a workspace document
    # rendered as a missing image -- the exact failure the comment in `render`
    # says must not happen.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix=f".{source.stem}.marp-from-",
        dir=str(source.parent),
        delete=False, encoding="utf-8"
    )
    tmp.write(full_source)
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        # Build a meaningful output stem from title and date
        date_str = datetime.now(get_default_tz()).strftime("%d-%b-%Y")
        title_slug = generate_slug(doc_title)
        meaningful_stem = f"31C-{title_slug}-{date_str}"

        result = render(tmp_path, output_dir=output_dir or default_output_dir(),
                        verbose=verbose, output_stem=meaningful_stem)
        result["source_title"] = doc_title
        result["source_mode"] = slide_mode
    finally:
        tmp_path.unlink(missing_ok=True)

    return result


# ============================================================
# Watch Mode
# ============================================================


def watch_start(source: Path) -> dict:
    """Start marp-cli watch mode for live preview."""
    if not source.exists():
        return {"ok": False, "error": "source-not-found",
                "message": f"Source not found: {source}"}

    # Check for existing watch
    if WATCH_STATE_FILE.exists():
        try:
            state = json.loads(WATCH_STATE_FILE.read_text(encoding="utf-8"))
            pid = state.get("pid")
            if pid and _is_process_running(pid):
                return {"ok": False, "error": "watch-active",
                        "message": f"Watch already active (PID {pid}, source: {state.get('source_path')}). "
                                   f"Run '/marp watch stop' first."}
        except _WATCH_STATE_UNREADABLE:
            pass

    # Check marp-cli
    marp_bin = _resolve_marp_bin()
    if not marp_bin:
        pinned = get_pinned_version()
        return {"ok": False, "error": "marp-not-installed",
                "message": f"marp-cli not installed. Run: npm install -g {pinned}"}

    # Prepare theme
    try:
        theme_path = prepare_theme()
    except OSError:
        return theme_missing_result()

    # Build command
    cmd = [
        marp_bin,
        str(source),
        "--theme", str(theme_path),
        "--allow-local-files",
        "--watch",
        "--server",
    ]

    browser_path = probe_browser()
    if browser_path:
        cmd.extend(["--browser-path", browser_path])

    # Start detached process
    if platform.system() == "Windows":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        proc = subprocess.Popen(
            cmd,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Write state file
    WATCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "pid": proc.pid,
        "url": "http://localhost:8080",
        "source_path": str(source),
        "theme_path": str(theme_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    WATCH_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "pid": proc.pid,
        "url": "http://localhost:8080",
        "source_path": str(source),
        "message": f"Watch started (PID {proc.pid}). Preview at http://localhost:8080",
    }


def watch_stop() -> dict:
    """Stop active watch mode."""
    if not WATCH_STATE_FILE.exists():
        return {"ok": False, "message": "No active watch session."}

    try:
        state = json.loads(WATCH_STATE_FILE.read_text(encoding="utf-8"))
    except _WATCH_STATE_UNREADABLE:
        WATCH_STATE_FILE.unlink(missing_ok=True)
        return {"ok": False, "message": "Corrupt watch state file removed."}

    pid = state.get("pid")
    theme_path = state.get("theme_path")

    # Liveness is checked BEFORE signalling. The recorded PID belongs to a marp
    # process that may have died long ago, and PIDs are reused: signalling one
    # blind meant sending SIGTERM to whatever unrelated process now holds that
    # number. `_is_process_running` is not proof of identity -- only that the
    # number is live -- so a stale state file whose process is gone now cleans
    # up and says so instead of firing into the dark.
    signalled = False
    if pid and _is_process_running(pid):
        signalled = True
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                signalled = False

    # Clean up theme temp file
    if theme_path:
        Path(theme_path).unlink(missing_ok=True)

    WATCH_STATE_FILE.unlink(missing_ok=True)

    if not signalled:
        return {"ok": True, "signalled": False,
                "message": f"No live process for PID {pid}; stale watch state removed."}
    return {"ok": True, "signalled": True, "message": f"Watch stopped (PID {pid})."}


def watch_status() -> dict:
    """Report watch mode status."""
    if not WATCH_STATE_FILE.exists():
        return {"running": False, "message": "No active watch session."}

    try:
        state = json.loads(WATCH_STATE_FILE.read_text(encoding="utf-8"))
    except _WATCH_STATE_UNREADABLE:
        return {"running": False, "message": "Corrupt watch state file."}

    pid = state.get("pid")
    running = _is_process_running(pid) if pid else False

    if not running:
        WATCH_STATE_FILE.unlink(missing_ok=True)
        if state.get("theme_path"):
            Path(state["theme_path"]).unlink(missing_ok=True)
        return {"running": False, "message": "Watch process no longer running. State cleaned up."}

    started = state.get("started_at", "unknown")
    return {
        "running": True,
        "pid": pid,
        "url": state.get("url", "http://localhost:8080"),
        "source_path": state.get("source_path"),
        "started_at": started,
    }


def _is_process_running(pid: int) -> bool:
    """Check if a process is still running.

    On Windows the PID is read out of `tasklist`'s CSV, field by field. It used
    to be `str(pid) in result.stdout` -- a substring search over the whole
    output, so PID 808 also matched 8080 and 18080. A dead watch then reported
    as running and `watch_start` refused to start with "watch-active".
    """
    if platform.system() == "Windows":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        import csv
        import io
        return any(len(row) >= 2 and row[1].strip() == str(pid)
                   for row in csv.reader(io.StringIO(result.stdout)))
    # POSIX asks the shared implementation. Inline, this caught `OSError`, so
    # `PermissionError` -- which means the process EXISTS and belongs to another
    # user -- read as dead. MEASURED 2026-08-29 against PID 1: `watch_status()`
    # then reported "Watch process no longer running. State cleaned up." and
    # deleted BOTH the watch state file and the generated theme file, from a
    # command whose whole job is to report.
    return pid_is_running(pid)


# ============================================================
# Self-Test
# ============================================================


def self_test() -> dict:
    """Run self-test: render the sample deck and validate output."""
    print(f"{BOLD}MARP Self-Test{RESET}")
    print(f"{GRAY}{'=' * 40}{RESET}")

    results = {"checks": [], "ok": True}

    # 1. Check marp-cli
    installed, version_str = check_marp_installed()
    if installed:
        results["checks"].append({"name": "marp-cli installed", "ok": True, "detail": version_str})
        version_ok = check_version_match(version_str)
        results["checks"].append({
            "name": "version match",
            "ok": version_ok,
            "detail": f"{'matches' if version_ok else 'MISMATCH with'} pinned {get_pinned_version()}"
        })
    else:
        results["checks"].append({"name": "marp-cli installed", "ok": False, "detail": "NOT FOUND"})
        results["ok"] = False
        return results

    # 2. Check browser
    browser = probe_browser()
    results["checks"].append({
        "name": "system browser",
        "ok": browser is not None,
        "detail": browser or "Not found (Chromium will be downloaded)"
    })

    # 3. Check the theme template exists. Every render reads it, and until now
    # the self-test checked the sample deck and the browser but not this file,
    # so a missing template killed `--self-test` itself with a traceback at
    # step 4 rather than reporting a failed check.
    if not THEME_TEMPLATE.exists():
        results["checks"].append({"name": "theme template", "ok": False,
                                  "detail": f"NOT FOUND: {THEME_TEMPLATE}"})
        results["ok"] = False
        return results
    results["checks"].append({"name": "theme template", "ok": True, "detail": str(THEME_TEMPLATE)})

    # 4. Check sample deck exists
    if not SAMPLE_DECK.exists():
        results["checks"].append({"name": "sample deck", "ok": False, "detail": "NOT FOUND"})
        results["ok"] = False
        return results
    results["checks"].append({"name": "sample deck", "ok": True, "detail": str(SAMPLE_DECK)})

    # 5. Render to temp dir
    with tempfile.TemporaryDirectory(prefix="marp-test-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        start_time = time.time()

        result = render(SAMPLE_DECK, output_dir=tmp_path, verbose=True)
        elapsed = time.time() - start_time

        results["checks"].append({
            "name": "render success",
            "ok": result.get("ok", False),
            "detail": f"{elapsed:.1f}s"
        })

        if result.get("ok"):
            for output in result.get("outputs", []):
                size = output.get("size", 0)
                ok = True
                if output["type"] == "pdf" and size < 50_000:
                    ok = False
                if output["type"] == "html" and size < 20_000:
                    ok = False
                results["checks"].append({
                    "name": f"{output['type']} size",
                    "ok": ok,
                    "detail": f"{size:,} bytes"
                })

            # Check for hidden chars in HTML
            html_outputs = [o for o in result.get("outputs", []) if o["type"] == "html"]
            if html_outputs:
                html_text = Path(html_outputs[0]["path"]).read_text(encoding="utf-8")
                _, hidden = run_sanitizer(html_text)
                results["checks"].append({
                    "name": "hidden chars in HTML",
                    "ok": hidden == 0,
                    "detail": f"{hidden} found" if hidden > 0 else "clean"
                })

            results["checks"].append({
                "name": "duration under 60s",
                "ok": elapsed < 60,
                "detail": f"{elapsed:.1f}s"
            })
        else:
            for err in result.get("errors", []):
                results["checks"].append({
                    "name": f"{err['type']} render",
                    "ok": False,
                    "detail": err.get("message", "Unknown error")
                })

        results["hidden_characters"] = result.get("hidden_characters", "unknown")

    # Print results
    print()
    for check in results["checks"]:
        status = f"{GREEN}PASS{RESET}" if check["ok"] else f"{RED}FAIL{RESET}"
        print(f"  {status}  {check['name']}: {check['detail']}")
        if not check["ok"]:
            results["ok"] = False

    print()
    overall = f"{GREEN}ALL CHECKS PASSED{RESET}" if results["ok"] else f"{RED}SOME CHECKS FAILED{RESET}"
    print(f"  {BOLD}{overall}{RESET}")

    return results


# ============================================================
# Output / Result Printing
# ============================================================


def print_result(result: dict) -> None:
    """Pretty-print a render result."""
    if result.get("ok"):
        print(f"\n{GREEN}{BOLD}Render successful{RESET}")
    else:
        print(f"\n{RED}{BOLD}Render failed{RESET}")

    for output in result.get("outputs", []):
        size_kb = output.get("size", 0) / 1024
        print(f"  {CYAN}{output['type'].upper()}{RESET}: {output['path']} ({size_kb:.1f} KB)")

    for error in result.get("errors", []):
        print(f"  {RED}ERROR ({error.get('type', '?')}){RESET}: {error.get('message', '')}")

    for warning in result.get("overflow_warnings", []):
        print(f"  {YELLOW}WARNING{RESET}: Slide {warning['slide']} has {warning['words']} words "
              f"(threshold: {WORD_OVERFLOW_THRESHOLD})")

    hidden = result.get("hidden_characters", "unknown")
    print(f"  Hidden characters: {hidden}")

    if result.get("source_title"):
        print(f"  Title: {result['source_title']}")
    if result.get("source_mode"):
        print(f"  Mode: {result['source_mode']}")


# ============================================================
# Main / CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="MARP rendering shim for 31C workspace",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--self-test", action="store_true", help="Run self-test")
    # ONE declaration. `render` and `from` each redeclared --verbose on their
    # own subparser, and a subparser's store_true default of False OVERWRITES
    # the top-level value in the same namespace, so `marp_render.py --verbose
    # render deck.md` reported verbose=False - the flag was accepted, echoed in
    # --help, and discarded. Measured with argparse directly on 2026-08-27.
    # It is now accepted in EITHER position because each sub-declaration below
    # inherits this one's value through `default=argparse.SUPPRESS`. That has
    # to hold for EVERY subcommand: `watch` had no redeclaration at all, so
    # `marp_render.py watch deck.md --verbose` exited on "unrecognized
    # arguments: --verbose" while the comment claimed either position worked.
    # Measured on the CLI, 2026-08-29.
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--theme-variant", choices=sorted(THEME_VARIANTS),
                        default="31c",
                        help="Theme variant: '31c' (internal decks, default) or "
                             "'31c-investor' (brand-forward investor master).")

    subparsers = parser.add_subparsers(dest="command")

    # render
    render_p = subparsers.add_parser("render", help="Render a MARP .md file")
    render_p.add_argument("source", type=Path, help="Source .md file")
    # Mutually exclusive at the CLI so argparse names the conflict, and guarded
    # again inside `render` for programmatic callers. See the note there.
    formats = render_p.add_mutually_exclusive_group()
    formats.add_argument("--pdf-only", action="store_true")
    formats.add_argument("--html-only", action="store_true")
    render_p.add_argument("--images", choices=["png"], help="Also generate PNG images")
    render_p.add_argument("--output", type=Path, help="Output directory")
    render_p.add_argument("--auto-sanitize", action="store_true", default=False,
                           help="Render from sanitized copy instead of blocking on hidden chars")
    render_p.add_argument("--verbose", action="store_true",
                        default=argparse.SUPPRESS)

    # from
    from_p = subparsers.add_parser("from", help="Transform workspace markdown into slides")
    from_p.add_argument("source", type=Path, help="Source workspace .md file")
    from_p.add_argument("--break-at", choices=["h2", "h3", "manual"], default="h2")
    from_p.add_argument("--mode", choices=["dark", "light", "mixed"])
    from_p.add_argument("--title", type=str)
    from_p.add_argument("--subtitle", type=str)
    from_p.add_argument("--no-auto-cover", action="store_true")
    from_p.add_argument("--no-auto-closing", action="store_true")
    from_p.add_argument("--paginate-heavy", action="store_true")
    from_p.add_argument("--output", type=Path)
    from_p.add_argument("--verbose", action="store_true",
                        default=argparse.SUPPRESS)

    # watch
    watch_p = subparsers.add_parser("watch", help="Watch mode")
    watch_p.add_argument("action", nargs="?", default=None, help="Path to watch, or 'stop'/'status'")
    watch_p.add_argument("--verbose", action="store_true",
                         default=argparse.SUPPRESS)

    args = parser.parse_args()

    # Select theme variant (default "31c" keeps all existing behavior). prepare_theme()
    # and inject_frontmatter() read these module globals, so reassigning here is enough.
    variant = getattr(args, "theme_variant", "31c")
    if variant != "31c":
        global THEME_TEMPLATE, THEME_NAME
        THEME_TEMPLATE, THEME_NAME = THEME_VARIANTS[variant]

    if args.self_test:
        result = self_test()
        sys.exit(0 if result["ok"] else 1)

    if args.command == "render":
        result = render(
            source=args.source.resolve(),
            output_dir=args.output,
            pdf_only=args.pdf_only,
            html_only=args.html_only,
            images_png=args.images == "png",
            auto_sanitize=args.auto_sanitize,
            verbose=args.verbose,
        )
        print_result(result)
        sys.exit(0 if result["ok"] else 1)

    elif args.command == "from":
        result = transform_workspace_md(
            source=args.source.resolve(),
            break_at=args.break_at,
            mode=args.mode,
            title=args.title,
            subtitle=args.subtitle,
            no_auto_cover=args.no_auto_cover,
            no_auto_closing=args.no_auto_closing,
            do_paginate_heavy=args.paginate_heavy,
            output_dir=args.output,
            verbose=args.verbose,
        )
        print_result(result)
        sys.exit(0 if result["ok"] else 1)

    elif args.command == "watch":
        action = args.action
        if action == "stop":
            result = watch_stop()
            print(result["message"])
            sys.exit(0 if result.get("ok", True) else 1)
        elif action == "status":
            result = watch_status()
            if result.get("running"):
                print(f"{GREEN}Watch active{RESET} (PID {result['pid']})")
                print(f"  URL: {result['url']}")
                print(f"  Source: {result['source_path']}")
                print(f"  Started: {result['started_at']}")
            else:
                print(result["message"])
            sys.exit(0)
        elif action:
            source = Path(action).resolve()
            result = watch_start(source)
            if result.get("ok"):
                print(f"{GREEN}{result['message']}{RESET}")
            else:
                print(f"{RED}{result.get('message', 'Watch failed')}{RESET}")
            sys.exit(0 if result.get("ok") else 1)
        else:
            parser.print_help()
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
