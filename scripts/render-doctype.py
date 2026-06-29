#!/usr/bin/env python3
"""Render a 31C corporate document from a locked template and JSON data.

Usage:
    python scripts/render-doctype.py --type letter --data path/to/data.json \
        --out outputs/documents/misha-hanin/letter/ --formats pdf,docx

Doctypes:
    letter       PDF + DOCX
    proposal     PDF + DOCX
    partnership  PDF + DOCX
    official     PDF + DOCX
    xpager       PDF + HTML

The renderer reads the locked HTML template from
`datastore/brand/templates/doctypes/{type}.html`, substitutes placeholders
from the JSON data file, embeds brand CSS / fonts / logos inline, writes
the self-contained HTML, then produces the requested output formats.

Output filename follows: {date}_{doctype}_{recipient-slug}_{subject-slug}.{ext}
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.workspace import get_workspace_root
from scripts.utils.doctype_renderer import (
    TEMPLATE_REGISTRY,
    render_html,
    build_docx,
    validate_required_fields,
    load_data,
)


def slugify(text: str, max_len: int = 40) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len]


def build_filename(data: dict, doctype: str, ext: str) -> str:
    date = (data.get("DATE") or data.get("EFFECTIVE_DATE") or "").strip() or "undated"
    # Date may be in "21 April 2026" or "2026-04-21"; normalise to ISO-ish for filename.
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date)
    if iso_match:
        date_part = iso_match.group(0)
    else:
        date_part = slugify(date, 10)

    if doctype == "letter":
        recipient_slug = slugify(f"{data.get('RECIPIENT_ORG', 'recipient')}")
        subject_slug = slugify(data.get("SUBJECT", "letter"))
    elif doctype == "proposal":
        recipient_slug = slugify(f"{data.get('RECIPIENT_ORG', 'prospect')}-{data.get('RECIPIENT_COUNTRY', '')}")
        subject_slug = slugify(data.get("SUBJECT", "proposal"))
    elif doctype == "partnership":
        recipient_slug = slugify(f"{data.get('PARTY_B_SHORT', 'partner')}")
        subject_slug = slugify(f"{data.get('SUBTYPE', 'mou')}-{data.get('SUBJECT', '')}")
    elif doctype == "official":
        recipient_slug = slugify(data.get("CLASS", "official"))
        subject_slug = slugify(data.get("SUBJECT", "document"))
    elif doctype == "xpager":
        recipient_slug = slugify(data.get("PRODUCT_NAME", "xpager"))
        subject_slug = "xpager"
    else:
        recipient_slug = "recipient"
        subject_slug = slugify(data.get("SUBJECT", doctype))

    return f"{date_part}_{doctype}_{recipient_slug}_{subject_slug}.{ext}"


def render_pdf(html_path: Path, pdf_path: Path, workspace_root: Path) -> None:
    html_to_pdf = workspace_root / "scripts" / "html-to-pdf.py"
    result = subprocess.run(
        [sys.executable, str(html_to_pdf), str(html_path), str(pdf_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"{RED}[PDF ERROR]{RESET} {result.stderr}")
        raise RuntimeError(f"PDF generation failed for {html_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a 31C corporate document.")
    parser.add_argument(
        "--type",
        required=True,
        choices=list(TEMPLATE_REGISTRY.keys()),
        help="Document type",
    )
    parser.add_argument("--data", required=True, type=Path, help="JSON data file path")
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    parser.add_argument(
        "--formats",
        default=None,
        help="Comma-separated list of formats (e.g. 'pdf,docx'). Defaults to the doctype's standard formats.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the data file without rendering",
    )
    args = parser.parse_args()

    workspace_root = get_workspace_root()

    if not args.data.exists():
        print(f"{RED}[ERROR]{RESET} Data file not found: {args.data}")
        return 1

    data = load_data(args.data)

    missing = validate_required_fields(args.type, data)
    if missing:
        print(f"{RED}[ERROR]{RESET} Missing required fields: {', '.join(missing)}")
        return 1

    if args.check_only:
        print(f"{GREEN}[OK]{RESET} Data file is valid for doctype '{args.type}'.")
        return 0

    default_formats = TEMPLATE_REGISTRY[args.type]["formats"]
    if args.formats:
        formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    else:
        formats = default_formats

    for fmt in formats:
        if fmt not in ("pdf", "docx", "html"):
            print(f"{RED}[ERROR]{RESET} Unsupported format: {fmt}")
            return 1
        if fmt == "docx" and "docx" not in default_formats:
            print(f"{RED}[ERROR]{RESET} DOCX not supported for doctype '{args.type}'")
            return 1

    args.out.mkdir(parents=True, exist_ok=True)

    html = render_html(args.type, data, workspace_root)

    # Always write HTML to a working path inside out dir (used for PDF rendering).
    html_name = build_filename(data, args.type, "html")
    html_path = args.out / html_name
    html_path.write_text(html, encoding="utf-8")
    print(f"{GREEN}[WROTE]{RESET} {html_path}")

    outputs: list[Path] = []

    if "pdf" in formats:
        pdf_path = args.out / build_filename(data, args.type, "pdf")
        render_pdf(html_path, pdf_path, workspace_root)
        outputs.append(pdf_path)
        print(f"{GREEN}[WROTE]{RESET} {pdf_path}")

    if "docx" in formats:
        docx_path = args.out / build_filename(data, args.type, "docx")
        build_docx(args.type, data, docx_path, workspace_root)
        outputs.append(docx_path)
        print(f"{GREEN}[WROTE]{RESET} {docx_path}")

    # For xpager, the HTML file IS an output (keep it). For other types, optionally
    # leave the HTML as a build artefact (useful for review/preview).
    if "html" in formats:
        outputs.append(html_path)

    # Sanitize text scan on HTML (best-effort).
    sanitize = workspace_root / "scripts" / "sanitize-text.py"
    if sanitize.exists():
        scan = subprocess.run(
            [sys.executable, str(sanitize), str(html_path), "--scan"],
            capture_output=True,
            text=True,
        )
        if scan.returncode != 0:
            print(f"{YELLOW}[WARN]{RESET} Hidden-character scan: {scan.stdout.strip() or scan.stderr.strip()}")
        else:
            print(f"{GREEN}[CLEAN]{RESET} Hidden-character scan passed.")

    print(f"\n{BOLD}{CYAN}Rendered '{args.type}' document.{RESET}")
    for path in outputs:
        print(f"  {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
