#!/usr/bin/env python3
"""
DataStore Extraction Script for 31C Workspace

Converts binary files (PPTX, XLSX) in the datastore/ to readable
markdown companion files (-extract.md).

Usage:
    python scripts/datastore-extract.py                        # scan and extract all new files
    python scripts/datastore-extract.py datastore/investment/ceo-only/    # extract from specific folder
    python scripts/datastore-extract.py --update-index          # also update INDEX.md
    python scripts/datastore-extract.py --force                 # re-extract even if companion exists

Prerequisites:
    pip install openpyxl python-pptx
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET
from scripts.utils.workspace import get_workspace_root, get_datastore_dir, get_default_tz

WORKSPACE = get_workspace_root()

DATASTORE_DIR = get_datastore_dir()
INDEX_FILE = DATASTORE_DIR / "INDEX.md"


def extract_xlsx(filepath):
    """Extract XLSX content to markdown."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        print(f"{RED}Error: openpyxl not installed. Run: pip install openpyxl{RESET}")
        return None

    wb = load_workbook(filepath, data_only=True)
    lines = []
    lines.append(f"# Extract: {filepath.name}")
    lines.append(f"")
    lines.append(f"> Auto-extracted from `{filepath.name}` on {datetime.now(get_default_tz()).strftime('%Y-%m-%d')}")
    lines.append(f"> This is a companion file for Claude to read. The original XLSX is the source of truth.")
    lines.append(f"")

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        lines.append(f"## Sheet: {sheet_name}")
        lines.append(f"")

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            lines.append("*(empty sheet)*")
            lines.append("")
            continue

        # Find header row (first non-empty row)
        header_row = None
        data_start = 0
        for i, row in enumerate(rows):
            if any(cell is not None for cell in row):
                header_row = row
                data_start = i + 1
                break

        if header_row is None:
            lines.append("*(empty sheet)*")
            lines.append("")
            continue

        # Build markdown table
        headers = [_cell(h) for h in header_row]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for row in rows[data_start:]:
            cells = [_cell(c) for c in row]
            # Truncate long cells
            cells = [c[:100] + "..." if len(c) > 100 else c for c in cells]
            # Pad to header length
            while len(cells) < len(headers):
                cells.append("")
            lines.append("| " + " | ".join(cells[:len(headers)]) + " |")

        lines.append("")

    return "\n".join(lines)


def _cell(value) -> str:
    """One spreadsheet cell, safe inside a markdown table row.

    Cells were joined straight into `" | "`, so any value containing a pipe
    added a phantom column and any value containing a newline ended the row
    early — and the companion `-extract.md` stopped representing the sheet it
    claims to be an extract of.
    """
    if value is None:
        return ""
    text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def extract_pptx(filepath):
    """Extract PPTX text content to markdown."""
    try:
        from pptx import Presentation
    except ImportError:
        print(f"{RED}Error: python-pptx not installed. Run: pip install python-pptx{RESET}")
        return None

    prs = Presentation(str(filepath))
    lines = []
    lines.append(f"# Extract: {filepath.name}")
    lines.append(f"")
    lines.append(f"> Auto-extracted from `{filepath.name}` on {datetime.now(get_default_tz()).strftime('%Y-%m-%d')}")
    lines.append(f"> This is a companion file for Claude to read. The original PPTX is the source of truth.")
    lines.append(f"")
    lines.append(f"Total slides: {len(prs.slides)}")
    lines.append(f"")

    for i, slide in enumerate(prs.slides, 1):
        lines.append(f"## Slide {i}")
        lines.append(f"")

        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        texts.append(text)

        if texts:
            for text in texts:
                lines.append(f"- {text}")
        else:
            lines.append("*(no text content - may be an image/diagram slide)*")

        lines.append("")

    return "\n".join(lines)


def get_companion_path(filepath):
    """Get the -extract.md companion path for a binary file."""
    return filepath.with_name(filepath.stem + "-extract.md")


def scan_and_extract(target_dir=None, force=False):
    """Scan for binary files and create companion extracts."""
    scan_dir = Path(target_dir) if target_dir else DATASTORE_DIR

    if not scan_dir.exists():
        print(f"{RED}Directory not found: {scan_dir}{RESET}")
        return []

    # Find XLSX and PPTX files
    binary_files = list(scan_dir.rglob("*.xlsx")) + list(scan_dir.rglob("*.pptx"))

    if not binary_files:
        print(f"{YELLOW}No XLSX or PPTX files found in {scan_dir}{RESET}")
        return []

    extracted = []
    failures: list[tuple[Path, str]] = []
    for filepath in sorted(binary_files):
        companion = get_companion_path(filepath)

        if companion.exists() and not force:
            print(f"  {GREEN}Skip{RESET}  {filepath.name} (companion already exists)")
            continue

        print(f"  {BOLD}Extracting{RESET}  {filepath.name}...")

        # Per file, not per batch. `load_workbook`/`Presentation` raise on a
        # corrupt zip, an encrypted file or a parser error, and with nothing
        # catching them here the exception left the LOOP: one bad binary in a
        # datastore folder meant every later extractable file was never
        # processed, and the run ended on a traceback rather than a report.
        try:
            if filepath.suffix.lower() == ".xlsx":
                content = extract_xlsx(filepath)
            elif filepath.suffix.lower() == ".pptx":
                content = extract_pptx(filepath)
            else:
                continue
        except Exception as exc:  # noqa: BLE001 - one file's parser, not the batch
            print(f"  {RED}Failed{RESET}  {filepath.name}: "
                  f"{type(exc).__name__}: {exc}")
            failures.append((filepath, f"{type(exc).__name__}: {exc}"))
            continue

        if content:
            companion.write_text(content, encoding="utf-8")
            print(f"  {GREEN}Created{RESET}  {companion.name}")
            extracted.append((filepath, companion))
        else:
            print(f"  {RED}Failed{RESET}  Could not extract {filepath.name}")
            failures.append((filepath, "extractor returned nothing"))

    if failures:
        # Named, not swallowed. The batch surviving one bad file is only half
        # the fix; the other half is that the operator can tell a run of 40
        # that produced 40 from a run of 40 that produced 38.
        print(f"\n{YELLOW}{len(failures)} file(s) could not be extracted:{RESET}")
        for path, why in failures:
            print(f"  {YELLOW}{path.name}{RESET}: {why}")

    return extracted


def update_index(extracted_files):
    """Add newly extracted files to INDEX.md."""
    if not INDEX_FILE.exists():
        print(f"{YELLOW}INDEX.md not found - skipping index update{RESET}")
        return

    content = INDEX_FILE.read_text(encoding="utf-8")
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")

    new_rows = []
    for orig, companion in extracted_files:
        # A target outside `datastore/` is allowed on the CLI, and
        # `relative_to` raised ValueError for it — AFTER every file had already
        # been extracted, so the work was done and the index update died.
        try:
            rel_path = orig.relative_to(DATASTORE_DIR)
        except ValueError:
            print(f"{YELLOW}Skipping index row for {orig}: it is outside "
                  f"{DATASTORE_DIR}, which INDEX.md rows are relative to.{RESET}")
            continue
        domain = rel_path.parts[0] if rel_path.parts else "unknown"
        # Check if already in index
        if str(rel_path) in content:
            continue
        new_rows.append(f"| `{rel_path}` | {domain.title()} | {orig.stem} (auto-extracted) | {today} | *Review and update validates column* |")

    if not new_rows:
        print(f"{GREEN}INDEX.md already up to date{RESET}")
        return

    # Insert before the LAST HTML comment, not the first one anywhere in the
    # file. The comment above this said "before the closing comment", and the
    # code replaced the first `<!--` it found — so an INDEX.md opening with a
    # header note or a licence comment got data rows injected above it, before
    # the table they belong to.
    marker = content.rfind("<!--")
    block = "\n".join(new_rows)
    if marker != -1:
        content = content[:marker] + block + "\n\n" + content[marker:]
    else:
        content = content.rstrip("\n") + "\n" + block + "\n"

    # Rewrite the whole "Last updated" LINE, and carry its old value into a
    # single Previous line. Replacing only the literal prefix left the old date
    # dangling after `> Previous: `, and the next run did it again — nesting
    # one more stale Previous into the block every time.
    content = re.sub(
        r"^> Last updated:[^\n]*(?:\n> Previous:[^\n]*)?",
        lambda m: (f"> Last updated: {today}  \n> Previous: "
                   f"{m.group(0).split(':', 1)[1].split(chr(10))[0].strip()}"),
        content,
        count=1,
        flags=re.MULTILINE,
    )

    INDEX_FILE.write_text(content, encoding="utf-8")
    print(f"{GREEN}Added {len(new_rows)} entries to INDEX.md{RESET}")


def main():
    parser = argparse.ArgumentParser(description="31C DataStore Extraction")
    parser.add_argument("target", nargs="?", default=None,
                        help="Specific directory to scan (default: entire datastore/)")
    parser.add_argument("--update-index", action="store_true",
                        help="Update INDEX.md with new entries")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract even if companion file exists")
    args = parser.parse_args()

    print(f"\n{BOLD}31C DataStore Extraction{RESET}")
    print(f"DataStore: {DATASTORE_DIR}\n")

    extracted = scan_and_extract(args.target, force=args.force)

    if extracted and args.update_index:
        update_index(extracted)

    print(f"\n{BOLD}Done.{RESET} Extracted {len(extracted)} file(s).")


if __name__ == "__main__":
    main()
