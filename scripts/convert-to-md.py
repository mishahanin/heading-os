#!/usr/bin/env python3
"""Convert office documents to Markdown via markitdown.

CEO-only standalone CLI. Coexists with scripts/docparse.py and scripts/datastore-extract.py.

Usage:
    python scripts/convert-to-md.py <input>                    # stdout (pipe-friendly)
    python scripts/convert-to-md.py <input> -o output.md       # file output
    python scripts/convert-to-md.py <input> --no-sanitize      # skip hidden-character strip

Supported input formats (lean install: markitdown[pdf,docx,pptx,xlsx,outlook]==0.1.6):
    PDF, DOCX, PPTX, XLSX, Outlook MSG, plus markitdown built-ins (CSV, JSON, XML, HTML, plain text).

Spec: docs/superpowers/specs/2026-04-27-markitdown-integration-design.md
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Workspace imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, RED, RESET
from scripts.utils.workspace import get_workspace_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert office documents to Markdown via markitdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Path to the input file")
    parser.add_argument("-o", "--output", help="Write markdown to this file (default: stdout)")
    parser.add_argument(
        "--no-sanitize",
        action="store_true",
        help="Skip the invisible-Unicode strip step (debug only)",
    )
    return parser.parse_args()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()

    # Verify markitdown is importable
    try:
        from markitdown import (
            MarkItDown,
            UnsupportedFormatException,
            MissingDependencyException,
            FileConversionException,
            MarkItDownException,
        )
    except ImportError:
        print(
            f"{RED}markitdown not installed.{RESET} "
            f"Run: pip install 'markitdown[pdf,docx,pptx,xlsx,outlook]==0.1.6'",
            file=sys.stderr,
        )
        return 1

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"{RED}File not found:{RESET} {input_path}", file=sys.stderr)
        return 1
    if not input_path.is_file():
        print(f"{RED}Not a regular file:{RESET} {input_path}", file=sys.stderr)
        return 1

    md = MarkItDown(enable_plugins=False)
    try:
        result = md.convert_local(str(input_path))
    except UnsupportedFormatException as e:
        print(f"{RED}Unsupported format:{RESET} {e}", file=sys.stderr)
        return 1
    except MissingDependencyException as e:
        print(
            f"{RED}Missing dependency:{RESET} {e}. "
            f"Run: pip install 'markitdown[pdf,docx,pptx,xlsx,outlook]==0.1.6'",
            file=sys.stderr,
        )
        return 1
    except FileConversionException as e:
        print(f"{RED}Conversion failed:{RESET} {e}", file=sys.stderr)
        return 1
    except MarkItDownException as e:
        print(f"{RED}Markitdown error:{RESET} {e}", file=sys.stderr)
        return 1
    # NOTE: deliberately NOT catching bare `Exception` - let unrelated errors raise.

    text = result.text_content

    # Sanitize unless --no-sanitize
    if not args.no_sanitize:
        sanitizer_path = get_workspace_root() / "scripts" / "sanitize-text.py"
        if not sanitizer_path.exists():
            print(
                f"{RED}Sanitizer missing{RESET} at {sanitizer_path}. "
                f"Run with --no-sanitize to bypass.",
                file=sys.stderr,
            )
            return 1
        try:
            sanitize_result = subprocess.run(
                [sys.executable, str(sanitizer_path), "-"],
                input=text,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
                timeout=30,
            )
            text = sanitize_result.stdout
        except subprocess.CalledProcessError as e:
            print(f"{RED}Sanitizer failed:{RESET} {e.stderr}", file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print(
                f"{RED}Sanitizer timed out after 30s.{RESET} "
                f"Run with --no-sanitize to bypass.",
                file=sys.stderr,
            )
            return 1

    # Write output
    if args.output:
        out_path = Path(args.output)
        if not out_path.parent.exists():
            print(
                f"{RED}Output directory does not exist:{RESET} {out_path.parent}",
                file=sys.stderr,
            )
            return 1
        out_path.write_text(text, encoding="utf-8", newline="\n")
        size = out_path.stat().st_size
        print(f"{GREEN}Wrote{RESET} {size} bytes to {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
