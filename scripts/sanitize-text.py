#!/usr/bin/env python3
"""
sanitize-text.py - CLI wrapper around scripts.utils.sanitize_text.

Library logic lives in scripts/utils/sanitize_text.py since the 2026-05-12
perf v2 sprint (Phase 2.1). This file remains the CLI entry point used by
hooks, pre-commit chains, and on-demand scans.

Usage:
  python sanitize-text.py <file>              # Sanitize a file in place
  python sanitize-text.py <file> -o <output>  # Sanitize to a new file
  python sanitize-text.py --scan <file>       # Scan and report hidden chars (no changes)
  python sanitize-text.py --text "string"     # Sanitize inline text (prints to stdout)
  python sanitize-text.py --scan --text "str" # Scan inline text for hidden chars
  echo "text" | python sanitize-text.py -     # Sanitize from stdin
"""

import argparse
import sys
from pathlib import Path

# Workspace import boilerplate
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.sanitize_text import sanitize, scan


def main():
    parser = argparse.ArgumentParser(
        description="Strip invisible Unicode characters from AI-generated text."
    )
    parser.add_argument("file", nargs="?", help="File to sanitize (use '-' for stdin)")
    parser.add_argument("-o", "--output", help="Output file (default: overwrite input)")
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan and report hidden characters without modifying"
    )
    parser.add_argument(
        "--text", help="Inline text to sanitize or scan (instead of a file)"
    )
    args = parser.parse_args()

    if not args.text and not args.file:
        parser.error("either a file or --text is required")

    if args.text:
        text = args.text
        source = "inline text"
    elif args.file == "-":
        text = sys.stdin.read()
        source = "stdin"
    else:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
        source = args.file

    if args.scan:
        count = scan(text, source)
        sys.exit(1 if count > 0 else 0)

    clean = sanitize(text)
    removed = len(text) - len(clean)

    if args.text or args.file == "-":
        sys.stdout.write(clean)
    else:
        output_path = args.output or args.file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(clean)

    if removed > 0:
        print(f"  Removed {removed} hidden character(s) from {source}", file=sys.stderr)
    else:
        print(f"  {source}: already clean", file=sys.stderr)


if __name__ == "__main__":
    main()
