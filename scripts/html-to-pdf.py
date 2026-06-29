#!/usr/bin/env python3
"""Convert an HTML file to PDF using Playwright."""
import sys
from pathlib import Path
from urllib.parse import quote

def main():
    if len(sys.argv) < 2:
        print("Usage: python html-to-pdf.py <input.html> [output.pdf]")
        sys.exit(1)

    html_path = Path(sys.argv[1]).resolve()
    if not html_path.exists():
        print(f"[ERROR] Input file not found: {html_path}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        pdf_path = sys.argv[2]
    else:
        pdf_path = str(html_path.with_suffix(".pdf"))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"[ERROR] Cannot import playwright.sync_api: {e}")
        print("[HINT] If 'playwright' itself is missing: pip install playwright && playwright install chromium")
        print("[HINT] If a transitive dep (e.g. 'greenlet') is missing: pip install <name> -- check requirements.txt")
        sys.exit(1)

    abs_path = str(html_path).replace("\\", "/")
    file_url = "file:///" + quote(abs_path, safe=":/")

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
        print(f"[ERROR] PDF generation failed: {e}")
        sys.exit(1)

    size = Path(pdf_path).stat().st_size
    print(f"PDF generated: {pdf_path}")
    print(f"Size: {size:,} bytes")

if __name__ == "__main__":
    main()
