#!/usr/bin/env python3
"""Convert an HTML file to PDF using Playwright.

Every failure goes to STDERR; stdout carries the result and nothing else. All
four exit-1 paths used to print to stdout, so `render-doctype.py`, which runs
this as a subprocess and reads its stdout for the generated path, was handed
`[ERROR] PDF generation failed: ...` on the same stream it reads a filename
from. A caller cannot tell those apart by channel, which is the whole reason the
two channels exist.
"""
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python html-to-pdf.py <input.html> [output.pdf]",
              file=sys.stderr)
        sys.exit(1)

    html_path = Path(sys.argv[1]).resolve()
    if not html_path.exists():
        print(f"[ERROR] Input file not found: {html_path}", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) >= 3:
        pdf_path = sys.argv[2]
    else:
        pdf_path = str(html_path.with_suffix(".pdf"))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"[ERROR] Cannot import playwright.sync_api: {e}", file=sys.stderr)
        print("[HINT] If 'playwright' itself is missing: pip install playwright && playwright install chromium")
        print("[HINT] If a transitive dep (e.g. 'greenlet') is missing: pip install <name> -- check requirements.txt")
        sys.exit(1)

    # `Path.as_uri()`, not a hand-built string. The old form replaced
    # backslashes and prefixed `file:///`, so a Windows UNC input
    # `\\server\share\page.html` became `file://///server/share/page.html` --
    # an empty host and four spare slashes, which Chromium cannot load.
    file_url = html_path.resolve().as_uri()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(file_url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)

            page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            browser.close()
    except Exception as e:
        print(f"[ERROR] PDF generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    size = Path(pdf_path).stat().st_size
    print(f"PDF generated: {pdf_path}")
    print(f"Size: {size:,} bytes")

if __name__ == "__main__":
    main()
