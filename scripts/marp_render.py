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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_outputs_dir, get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils.markdown import parse_frontmatter as _parse_frontmatter_text

# ============================================================
# Configuration
# ============================================================

# --- Constants ---

WORKSPACE_ROOT = get_workspace_root()
SKILL_DIR = WORKSPACE_ROOT / ".claude" / "skills" / "marp"
THEME_TEMPLATE = SKILL_DIR / "themes" / "31c.css.tmpl"
FONTS_DIR = SKILL_DIR / "themes" / "fonts"
SAMPLE_DECK = SKILL_DIR / "examples" / "sample-deck.md"
VERSION_PIN_FILE = WORKSPACE_ROOT / "scripts" / ".marp-version"
MARP_SOURCE_DIR = get_outputs_dir() / "deliverables" / "presentations" / "marp-source"
DEFAULT_OUTPUT_DIR = get_outputs_dir() / "deliverables" / "presentations"
WATCH_STATE_FILE = Path.home() / ".marp" / "watch.json"
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


def check_version_match(installed_version: str) -> bool:
    """Check if installed version matches pinned version."""
    pinned = get_pinned_version()
    # Extract version number from pin (e.g., "@marp-team/marp-cli@4.1.1" -> "4.1.1")
    pinned_num = pinned.rsplit("@", 1)[-1] if "@" in pinned else pinned
    return pinned_num in installed_version


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
        # Candidate order: distro-named binaries first (apt/dnf), then snap
        # paths (chromium snap), then brave-browser (now standard on the CEO
        # machine and other Chromium-derivatives).
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


def prepare_theme() -> Path:
    """Substitute {FONTS_DIR} in theme template and write to temp file. Returns path."""
    template_text = THEME_TEMPLATE.read_text(encoding="utf-8")
    # Path.as_uri() properly percent-encodes spaces and special characters
    # (e.g., a space becomes "%20", a parenthesis becomes "%28"), which is
    # critical since marp-cli renders via Chromium which needs valid file:// URIs.
    fonts_uri = FONTS_DIR.resolve().as_uri()
    resolved = template_text.replace("{FONTS_DIR}", fonts_uri)

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
    count = 0
    clean = text
    for char in hidden_chars:
        found = clean.count(char)
        if found > 0:
            count += found
            clean = clean.replace(char, "")
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


def inject_frontmatter(source_text: str, title: str = "", mode: str = "dark",
                       subtitle: str = "", classification: str = "ceo-only") -> str:
    """Inject MARP frontmatter if missing, or merge with existing."""
    existing_fm, body = parse_frontmatter(source_text)

    fm = {
        "marp": True,
        "theme": "31c",
        "paginate": True,
        "size": "16:9",
        "class": mode,
        "title": title or "Untitled",
        "author": "Misha Hanin",
        "date": datetime.now().strftime("%Y-%m-%d"),
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
        if isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, str) and (" " in value or ":" in value):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")

    return "\n".join(lines) + "\n" + body


def strip_wiki_links(text: str) -> str:
    """Convert [[id]] to id and [[id|Display]] to Display."""
    text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    return text


def auto_slide_breaks(body: str, break_at: str = "h2") -> str:
    """Insert slide breaks at heading level if no manual breaks exist.
    If the body already contains standalone '---' lines (slide breaks),
    respect them and return unchanged."""
    lines = body.split("\n")

    # Check for existing manual slide breaks (standalone --- lines)
    for line in lines:
        if line.strip() == "---":
            return body

    heading_pattern = {"h2": r"^## ", "h3": r"^### "}
    pattern = heading_pattern.get(break_at, r"^## ")

    result = []
    for i, line in enumerate(lines):
        if i > 0 and re.match(pattern, line):
            result.append("")
            result.append("---")
            result.append("")
        result.append(line)
    return "\n".join(result)


# ============================================================
# Workspace Defaults & Slide Helpers
# ============================================================


def get_workspace_defaults(source_path: Path) -> dict:
    """Determine mode and subtitle defaults based on source directory."""
    try:
        rel = source_path.resolve().relative_to(WORKSPACE_ROOT).as_posix()
    except ValueError:
        rel = str(source_path)

    for prefix, defaults in WORKSPACE_DEFAULTS.items():
        if rel.startswith(prefix):
            subtitle = defaults["subtitle"]
            subtitle = subtitle.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
            subtitle = subtitle.replace("{filename}", source_path.stem)
            return {"mode": defaults["mode"], "subtitle": subtitle}

    return {"mode": "mixed", "subtitle": source_path.stem}


def generate_slug(topic: str) -> str:
    """Generate a URL-safe slug from a topic string."""
    slug = topic.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:60]


def check_overflow(source_text: str) -> list[dict]:
    """Check for slides exceeding word threshold. Returns list of warnings."""
    _, body = parse_frontmatter(source_text)
    slides = re.split(r"\n---\n", body)
    warnings = []
    for i, slide in enumerate(slides):
        words = len(slide.split())
        if words > WORD_OVERFLOW_THRESHOLD:
            warnings.append({"slide": i + 1, "words": words})
    return warnings


def paginate_heavy(source_text: str) -> str:
    """Sub-break heavy slides on paragraph boundaries."""
    fm, body = parse_frontmatter(source_text)
    slides = re.split(r"\n---\n", body)
    new_slides = []
    for slide in slides:
        words = len(slide.split())
        if words > WORD_OVERFLOW_THRESHOLD:
            paragraphs = re.split(r"\n\n+", slide)
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
            if isinstance(value, bool):
                fm_lines.append(f"{key}: {str(value).lower()}")
            elif isinstance(value, str) and (" " in value or ":" in value):
                fm_lines.append(f'{key}: "{value}"')
            else:
                fm_lines.append(f"{key}: {value}")
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
    theme_path = prepare_theme()

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
        if browser_path:
            base_cmd.extend(["--browser-path", browser_path])

        # Render PDF
        if not html_only:
            pdf_out = out_dir / f"{stem}.pdf"
            cmd = base_cmd + ["--pdf", "-o", str(pdf_out)]
            if verbose:
                print(f"{GRAY}Running: {' '.join(cmd)}{RESET}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and pdf_out.exists():
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and html_out.exists():
                outputs.append({"type": "html", "path": str(html_out), "size": html_out.stat().st_size})
            else:
                err_msg = result.stderr.strip() if result.stderr else "Unknown error"
                errors.append({"type": "html", "error": "render-failed",
                               "message": f"Render failed. marp-cli output:\n{err_msg}"})

        # Render PNG images
        if images_png:
            img_out = out_dir / f"{stem}.001.png"
            cmd = base_cmd + ["--images", "png", "-o", str(out_dir / f"{stem}.png")]
            if verbose:
                print(f"{GRAY}Running: {' '.join(cmd)}{RESET}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                # Count generated PNGs
                pngs = list(out_dir.glob(f"{stem}.*.png"))
                for png in pngs:
                    outputs.append({"type": "png", "path": str(png), "size": png.stat().st_size})

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
            f"{datetime.now().strftime('%Y-%m-%d')}"
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

    # Inject frontmatter
    full_source = inject_frontmatter(full_body, title=doc_title, mode=slide_mode,
                                     classification="ceo-only")

    # Paginate heavy if requested
    if do_paginate_heavy:
        full_source = paginate_heavy(full_source)

    # Write to temp file and render
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="marp-from-",
        delete=False, encoding="utf-8"
    )
    tmp.write(full_source)
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        # Build a meaningful output stem from title and date
        date_str = datetime.now().strftime("%d-%b-%Y")
        title_slug = generate_slug(doc_title)
        meaningful_stem = f"31C-{title_slug}-{date_str}"

        result = render(tmp_path, output_dir=output_dir or DEFAULT_OUTPUT_DIR,
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
        except (json.JSONDecodeError, KeyError):
            pass

    # Check marp-cli
    marp_bin = _resolve_marp_bin()
    if not marp_bin:
        pinned = get_pinned_version()
        return {"ok": False, "error": "marp-not-installed",
                "message": f"marp-cli not installed. Run: npm install -g {pinned}"}

    # Prepare theme
    theme_path = prepare_theme()

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
        "started_at": datetime.now().isoformat(),
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
    except (json.JSONDecodeError, KeyError):
        WATCH_STATE_FILE.unlink(missing_ok=True)
        return {"ok": False, "message": "Corrupt watch state file removed."}

    pid = state.get("pid")
    theme_path = state.get("theme_path")

    if pid:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    # Clean up theme temp file
    if theme_path:
        Path(theme_path).unlink(missing_ok=True)

    WATCH_STATE_FILE.unlink(missing_ok=True)

    return {"ok": True, "message": f"Watch stopped (PID {pid})."}


def watch_status() -> dict:
    """Report watch mode status."""
    if not WATCH_STATE_FILE.exists():
        return {"running": False, "message": "No active watch session."}

    try:
        state = json.loads(WATCH_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
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
    """Check if a process is still running."""
    if platform.system() == "Windows":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=10
        )
        return str(pid) in result.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


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

    # 3. Check sample deck exists
    if not SAMPLE_DECK.exists():
        results["checks"].append({"name": "sample deck", "ok": False, "detail": "NOT FOUND"})
        results["ok"] = False
        return results
    results["checks"].append({"name": "sample deck", "ok": True, "detail": str(SAMPLE_DECK)})

    # 4. Render to temp dir
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
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    subparsers = parser.add_subparsers(dest="command")

    # render
    render_p = subparsers.add_parser("render", help="Render a MARP .md file")
    render_p.add_argument("source", type=Path, help="Source .md file")
    render_p.add_argument("--pdf-only", action="store_true")
    render_p.add_argument("--html-only", action="store_true")
    render_p.add_argument("--images", choices=["png"], help="Also generate PNG images")
    render_p.add_argument("--output", type=Path, help="Output directory")
    render_p.add_argument("--auto-sanitize", action="store_true", default=False,
                           help="Render from sanitized copy instead of blocking on hidden chars")
    render_p.add_argument("--verbose", action="store_true")

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
    from_p.add_argument("--verbose", action="store_true")

    # watch
    watch_p = subparsers.add_parser("watch", help="Watch mode")
    watch_p.add_argument("action", nargs="?", default=None, help="Path to watch, or 'stop'/'status'")

    args = parser.parse_args()

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
            verbose=args.verbose or getattr(args, "verbose", False),
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
            verbose=args.verbose or getattr(args, "verbose", False),
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
