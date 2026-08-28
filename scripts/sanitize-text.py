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
from scripts.utils.atomic import atomic_write_text
from scripts.utils.sanitize_text import sanitize_report, scan, word_count

# The definition moved to `scripts/utils/sanitize_text.py` so the rest of the
# workspace can reach it. It lived here, inside a kebab-case CLI that no module
# can import, and four other word counters were written rather than shared this
# one. The alias keeps this file's own reference short.
_word_count = word_count


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

    # `-o` is only ever consulted on the write-a-file path below. Given with
    # --text or with `-`, it was accepted, silently ignored, and the output went
    # to stdout: the operator names a destination, sees no error, and finds no
    # file. Refusing beats writing to a path the branch never reaches.
    # --scan belongs in this list and was missing from it. The scan branch below
    # returns before `output_path = args.output or args.file` is ever reached,
    # so it is a THIRD path where `-o` is dead, and the guard enumerated two of
    # three. Measured 2026-08-26: `sanitize-text.py README.md --scan -o out.md`
    # printed a clean report, exited 0, and wrote no file at the named path -
    # which is the exact failure this guard exists to refuse.
    if args.output and (args.text or args.file == "-" or args.scan):
        parser.error("-o/--output writes the sanitized FILE back; it does nothing "
                     "for --scan, --text or stdin, which only print")

    if args.text:
        text = args.text
        source = "inline text"
    elif args.file == "-":
        text = sys.stdin.read()
        source = "stdin"
    else:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            # A hook chain surfaces a traceback as "the hook failed", with
            # nothing naming the path that was wrong.
            print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
            return 2
        source = args.file

    if args.scan:
        count = scan(text, source)
        # The word count belongs here because `.claude/rules/hidden-chars.md`
        # requires every deliverable to carry "Word count: X. Hidden characters:
        # clean." and nothing computed the X -- so the number was estimated by
        # whoever wrote the line. A validation line with a guessed figure in it
        # is the exact over-claim `.claude/rules/scope-claims.md` forbids.
        print(f"  Word count: {_word_count(text)}", file=sys.stderr)
        sys.exit(1 if count > 0 else 0)

    clean, removed, replaced = sanitize_report(text)

    if args.text or args.file == "-":
        sys.stdout.write(clean)
    else:
        output_path = args.output or args.file
        # Atomic. The in-place form truncated the target first, so an interrupt
        # mid-write destroyed the original with no copy left -- and hooks and
        # pre-commit chains run this over source files.
        atomic_write_text(Path(output_path), clean)

    # Both numbers, because the file is rewritten for either one. The old line
    # reported deletions only, so a replaced non-breaking space was a silent
    # rewrite under the word "clean".
    if removed or replaced:
        parts = []
        if removed:
            parts.append(f"removed {removed}")
        if replaced:
            parts.append(f"replaced {replaced}")
        print(f"  {source}: {', '.join(parts)} hidden character(s)", file=sys.stderr)
    else:
        print(f"  {source}: already clean", file=sys.stderr)


if __name__ == "__main__":
    # `sys.exit(main())`, not a bare `main()`. The bare call DISCARDED the
    # return value, so the `return 2` for an unreadable file left the process
    # exiting 0 - and this script's exit code is what four callers read as the
    # verdict: artifact-evaluator prints "Clean" on 0, render-doctype prints
    # "[CLEAN] Hidden-character scan passed.", inbox-pulse-report prints
    # "Hidden char scan: clean", and crm_migrate_to_entity_model carries on with
    # the apply. A path that was never opened was reported as scanned and clean.
    sys.exit(main())
