#!/usr/bin/env python3
"""PostToolUse hook: scan written/edited files for hidden Unicode characters.

Triggers after Write or Edit tool calls. If hidden characters are detected,
returns feedback to Claude so it can fix the contamination immediately.

Phase 2.1 (2026-05-12 perf v2): in-process scan via scripts.utils.sanitize_text
instead of subprocess fan-out. Saves ~150-200ms per Write/Edit by eliminating
the Python interpreter spawn that the old shell-out incurred.
"""
import json
import os
import sys
from pathlib import Path


# Binary/non-text extensions to skip
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dll", ".so",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".webm",
    ".pyc", ".pyo", ".class", ".o", ".a", ".lib",
    ".bin", ".dat", ".db", ".sqlite",
    ".pptx", ".docx", ".xlsx", ".dotx", ".potx",
    ".pen",
}

# Files that legitimately embed invisible characters as data (the sanitizer
# itself). Scanning them produces a false-positive contamination warning on
# every edit. Match on suffix to be path-agnostic.
SKIP_BASENAMES = {
    "sanitize_text.py",  # scripts/utils/sanitize_text.py
}


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        print(f"[post-write-sanitize] failed to parse input: {e}", file=sys.stderr)
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    # Write/Edit/MultiEdit carry file_path; NotebookEdit carries notebook_path.
    # We scan the on-disk result, so the same post-write scan covers all four.
    file_path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")

    if not file_path or not os.path.isfile(file_path):
        sys.exit(0)

    ext = os.path.splitext(file_path)[1].lower()
    if ext in SKIP_EXTENSIONS:
        sys.exit(0)

    if os.path.basename(file_path) in SKIP_BASENAMES:
        sys.exit(0)

    # In-process import (Phase 2.1). Workspace root is the harness-provided
    # cwd. Fall back to silent exit if the import fails — losing the scan
    # signal is preferable to crashing the hook chain on an env edge-case.
    project_dir = input_data.get("cwd", os.getcwd())
    sys.path.insert(0, str(Path(project_dir)))
    try:
        from scripts.utils.sanitize_text import scan_file
    except Exception as e:
        print(f"[post-write-sanitize] could not import scan_file: {e}", file=sys.stderr)
        sys.exit(0)

    try:
        count, report = scan_file(file_path)
        if count > 0:
            basename = os.path.basename(file_path)
            json.dump({
                "additionalContext": (
                    f"HIDDEN CHARACTER CONTAMINATION in {basename}. "
                    f"{report}. "
                    f"The file has already been written with hidden characters. "
                    f"Fix immediately: re-edit the file to remove the hidden characters."
                )
            }, sys.stdout)
    except Exception as e:
        print(f"[post-write-sanitize] Error scanning {file_path}: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
