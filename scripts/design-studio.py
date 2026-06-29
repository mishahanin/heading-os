#!/usr/bin/env python3
"""
design-studio.py -- HTML-to-image renderer using Playwright.

Renders HTML designs at exact pixel dimensions with optional 31C brand CSS
injection. Supports PNG screenshots, multi-format export, and PDF output.

Usage:
  python scripts/design-studio.py render --html "<div>Hello</div>" --width 1080 --height 1080
  python scripts/design-studio.py render --file design.html --width 1920 --height 1080 --brand 31c
  python scripts/design-studio.py render --html "<h1>Post</h1>" -o outputs/design/post.png --scale 3
  python scripts/design-studio.py export --file design.html --formats "1080x1080,1200x628,1920x1080"
  python scripts/design-studio.py pdf --html "<div>Report</div>" --brand 31c
  python scripts/design-studio.py pdf --file report.html -o outputs/design/report.pdf

Commands:
  render  - Screenshot HTML at exact viewport dimensions (PNG)
  export  - Render HTML at multiple dimensions in one pass
  pdf     - Generate PDF from HTML via Playwright
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.workspace import get_outputs_dir, get_workspace_root

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def ensure_playwright():
    """Exit with error if Playwright is not available."""
    if not HAS_PLAYWRIGHT:
        print(f"{RED}[ERROR] Playwright not installed. Run: pip install playwright && playwright install chromium{RESET}")
        sys.exit(1)


def get_output_dir() -> Path:
    """Return the default output directory, creating it if needed."""
    out = get_outputs_dir() / "design"
    out.mkdir(parents=True, exist_ok=True)
    return out


def get_tmp_dir() -> Path:
    """Return the temp directory for intermediate HTML files."""
    tmp = get_outputs_dir() / "design" / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def timestamp() -> str:
    """Return a compact UTC timestamp for default filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def inject_brand_css(html_content: str, brand: str) -> str:
    """Inject brand CSS into HTML content."""
    if brand != "31c":
        print(f"{YELLOW}[WARN] Unknown brand '{brand}', skipping CSS injection{RESET}")
        return html_content

    css_path = get_workspace_root() / ".claude" / "skills" / "design" / "references" / "brand.css"
    if not css_path.exists():
        print(f"{YELLOW}[WARN] Brand CSS not found: {css_path}{RESET}")
        return html_content

    css = css_path.read_text(encoding="utf-8")
    style_tag = f"<style>\n{css}\n</style>"

    if "</head>" in html_content.lower():
        return re.sub(r'</head>', f'{style_tag}\n</head>', html_content, count=1, flags=re.IGNORECASE)
    else:
        return (
            f'<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
            f'{style_tag}\n</head>\n<body>\n{html_content}\n</body>\n</html>'
        )


def resolve_html(args) -> str:
    """Get HTML content from --html or --file, validating exactly one is provided."""
    has_html = getattr(args, "html", None) is not None
    has_file = getattr(args, "file", None) is not None

    if has_html and has_file:
        print(f"{RED}[ERROR] Specify --html or --file, not both{RESET}")
        sys.exit(1)
    if not has_html and not has_file:
        print(f"{RED}[ERROR] Specify --html or --file{RESET}")
        sys.exit(1)

    if has_file:
        file_path = Path(args.file).resolve()
        if not file_path.exists():
            print(f"{RED}[ERROR] File not found: {file_path}{RESET}")
            sys.exit(1)
        return file_path.read_text(encoding="utf-8")

    return args.html


def render_screenshot(html: str, width: int, height: int, scale: int, output_path: Path) -> Path:
    """Render HTML to PNG at exact viewport dimensions using Playwright."""
    tmp_path = get_tmp_dir() / f"render-{timestamp()}.html"
    try:
        tmp_path.write_text(html, encoding="utf-8")
        file_url = f"file:///{tmp_path.as_posix()}"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=scale,
            )
            page.goto(file_url, wait_until="networkidle", timeout=30000)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output_path), full_page=False)
            browser.close()

        return output_path
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def render_pdf(html: str, output_path: Path) -> Path:
    """Render HTML to PDF using Playwright."""
    tmp_path = get_tmp_dir() / f"pdf-{timestamp()}.html"
    try:
        tmp_path.write_text(html, encoding="utf-8")
        file_url = f"file:///{tmp_path.as_posix()}"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(file_url, wait_until="networkidle", timeout=30000)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(output_path), print_background=True)
            browser.close()

        return output_path
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def save_source_html(html: str, output_path: Path, from_inline: bool) -> Path | None:
    """Save source HTML alongside the output if it came from --html."""
    if not from_inline:
        return None
    source_path = output_path.with_suffix(".html")
    source_path.write_text(html, encoding="utf-8")
    return source_path


# -- Subcommand handlers ------------------------------------------------------

def cmd_render(args):
    """Handle the 'render' subcommand."""
    ensure_playwright()

    html = resolve_html(args)
    from_inline = getattr(args, "html", None) is not None
    brand = getattr(args, "brand", None)

    if brand:
        html = inject_brand_css(html, brand)

    width = args.width
    height = args.height
    scale = args.scale

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = get_output_dir() / f"render-{timestamp()}.png"

    print(f"{CYAN}[INFO] Rendering {width}x{height} @{scale}x ...{RESET}")

    render_screenshot(html, width, height, scale, output_path)
    print(f"{GREEN}[OK] Screenshot saved: {output_path}{RESET}")
    print(f"     Dimensions: {width}x{height} (output {width * scale}x{height * scale}px @{scale}x)")

    source_path = save_source_html(html, output_path, from_inline)
    if source_path:
        print(f"     Source HTML: {source_path}")


def cmd_export(args):
    """Handle the 'export' subcommand."""
    ensure_playwright()

    file_path = Path(args.file).resolve()
    if not file_path.exists():
        print(f"{RED}[ERROR] File not found: {file_path}{RESET}")
        sys.exit(1)

    html = file_path.read_text(encoding="utf-8")
    brand = getattr(args, "brand", None)

    if brand:
        html = inject_brand_css(html, brand)

    if args.output:
        out_dir = Path(args.output).resolve()
    else:
        out_dir = get_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    scale = args.scale
    basename = file_path.stem
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    saved = []

    for fmt in formats:
        match = re.match(r'^(\d+)x(\d+)$', fmt)
        if not match:
            print(f"{YELLOW}[WARN] Skipping invalid format '{fmt}' (expected WxH){RESET}")
            continue

        w, h = int(match.group(1)), int(match.group(2))
        out_path = out_dir / f"{basename}-{w}x{h}.png"

        print(f"{CYAN}[INFO] Rendering {w}x{h} @{scale}x ...{RESET}")
        render_screenshot(html, w, h, scale, out_path)
        print(f"{GREEN}[OK] {out_path.name}{RESET}")
        saved.append(out_path)

    # Copy source HTML to output directory
    source_dest = out_dir / file_path.name
    if source_dest != file_path:
        source_dest.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
        saved.append(source_dest)

    print(f"\n{GREEN}[OK] Exported {len(saved)} file(s) to: {out_dir}{RESET}")
    for s in saved:
        print(f"     {s}")


def cmd_pdf(args):
    """Handle the 'pdf' subcommand."""
    ensure_playwright()

    html = resolve_html(args)
    brand = getattr(args, "brand", None)

    if brand:
        html = inject_brand_css(html, brand)

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = get_output_dir() / f"render-{timestamp()}.pdf"

    print(f"{CYAN}[INFO] Generating PDF ...{RESET}")

    render_pdf(html, output_path)
    size = output_path.stat().st_size
    print(f"{GREEN}[OK] PDF saved: {output_path}{RESET}")
    print(f"     Size: {size:,} bytes")


# -- CLI setup ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="design-studio.py",
        description="HTML-to-image renderer with 31C brand CSS injection.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # -- render ----------------------------------------------------------------
    p_render = subs.add_parser("render", help="Screenshot HTML at exact viewport dimensions")
    p_render.add_argument("--html", type=str, help="Inline HTML string to render")
    p_render.add_argument("--file", type=str, help="Path to HTML file to render")
    p_render.add_argument("--width", type=int, default=1200, help="Viewport width (default: 1200)")
    p_render.add_argument("--height", type=int, default=628, help="Viewport height (default: 628)")
    p_render.add_argument("--scale", type=int, default=2, help="Device scale factor (default: 2)")
    p_render.add_argument("--brand", type=str, help="Brand CSS to inject (e.g. 31c)")
    p_render.add_argument("-o", "--output", type=str, help="Output file path")

    # -- export ----------------------------------------------------------------
    p_export = subs.add_parser("export", help="Render HTML at multiple dimensions")
    p_export.add_argument("--file", type=str, required=True, help="Path to HTML file")
    p_export.add_argument("--formats", type=str, required=True, help='Comma-separated WxH formats (e.g. "1080x1080,1200x628")')
    p_export.add_argument("--scale", type=int, default=2, help="Device scale factor (default: 2)")
    p_export.add_argument("--brand", type=str, help="Brand CSS to inject (e.g. 31c)")
    p_export.add_argument("-o", "--output", type=str, help="Output directory")

    # -- pdf -------------------------------------------------------------------
    p_pdf = subs.add_parser("pdf", help="Generate PDF from HTML")
    p_pdf.add_argument("--html", type=str, help="Inline HTML string")
    p_pdf.add_argument("--file", type=str, help="Path to HTML file")
    p_pdf.add_argument("--brand", type=str, help="Brand CSS to inject (e.g. 31c)")
    p_pdf.add_argument("-o", "--output", type=str, help="Output file path")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "render": cmd_render,
        "export": cmd_export,
        "pdf": cmd_pdf,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
