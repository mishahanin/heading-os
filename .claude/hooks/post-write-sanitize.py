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

    # A payload that is valid JSON but not an object still has `.get` called on
    # it. `[]`, `"x"` and `3` all parse, then raise an uncaught AttributeError.
    # Measured 2026-08-23 with `echo '[]' | python <hook>`: exit 1, traceback.
    # `.claude/hooks/checkpoint-inject.py` fixed this shape on 2026-08-20 and
    # these were missed. Degrade to the empty dict, which every path below
    # already handles, rather than dropping the hook's whole job.
    if not isinstance(input_data, dict):
        print(f"[post-write-sanitize] payload was {type(input_data).__name__}, "
              "not an object", file=sys.stderr)
        sys.exit(0)

    # `.get("tool_input", {})` returns the STORED value when the key is present,
    # so a `null`, a list or a string reached `.get` and raised an uncaught
    # AttributeError one level below the guard above. `_dispatch.py` and
    # `data-path-redirect.py` both already guard this nested shape.
    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        print(f"[post-write-sanitize] tool_input was {type(tool_input).__name__}, "
              "not an object", file=sys.stderr)
        sys.exit(0)
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

    # In-process import (Phase 2.1). The engine root is derived from THIS FILE,
    # not from the harness cwd.
    #
    # "Workspace root is the harness-provided cwd" was asserted by the old
    # comment and established by nothing: the Bash tool's cwd drifts, and a
    # session started in any engine subdirectory made the scanner import fail,
    # print one stderr line nobody reads, and exit 0 having scanned NOTHING.
    # Reproduced 2026-08-25: the same file carrying U+200B reported "HIDDEN
    # CHARACTER CONTAMINATION" from the engine root and nothing at all from a
    # subdirectory. This hook is the mechanical half of the always-on
    # `.claude/rules/hidden-chars.md` policy, so it was switching itself off.
    # The docs-sync hook beside it already anchors this way; it is not named by
    # path here, because the plugin bundler's completeness gate reads a path
    # token as a bundled dependency.
    engine_root = Path(__file__).resolve().parents[2]
    for candidate in (str(engine_root), str(Path(input_data.get("cwd") or os.getcwd()))):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        from scripts.utils.sanitize_text import scan_file
    except Exception as e:
        # Say it IN CONTEXT, not only on a stream nothing reads. A guard that
        # cannot run must report that it did not run, or its silence is read as
        # a clean file (`.claude/rules/scope-claims.md`, obligation 3).
        print(f"[post-write-sanitize] could not import scan_file: {e}", file=sys.stderr)
        # The workspace sanitizer is deliberately not named by path here: the
        # plugin bundler's completeness gate reads a path token as a bundled
        # dependency, and this hook ships in bundles that do not carry it.
        json.dump({
            "additionalContext": (
                f"HIDDEN CHARACTER SCAN DID NOT RUN on "
                f"{os.path.basename(file_path)}: the scanner could not be "
                f"imported ({type(e).__name__}: {e}). The file is UNVERIFIED, "
                f"not clean. Scan it with the workspace sanitizer before "
                f"treating it as checked."
            )
        }, sys.stdout)
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
