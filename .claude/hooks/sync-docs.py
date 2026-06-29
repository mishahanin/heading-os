#!/usr/bin/env python3
"""PostToolUse hook: auto-sync templates/ -> docs/ when documentation files change.

templates/ is the source of truth for shared documentation.
docs/ is the distribution directory (synced to corporate repo and exec workspaces).
This hook auto-copies changed files to keep them in sync.

Only syncs the 6 shared documentation files (MD + HTML versions).

When a templates/*.md file is edited, the matching HTML is regenerated via
scripts/regenerate-docs-html.py (non-blocking -- failures produce warnings,
never abort the write).
"""
import sys
import json
import shutil
import subprocess
from pathlib import Path


# Files that should be synced from templates/ to docs/
SYNC_FILES = {
    "GETTING-STARTED.md",
    "GETTING-STARTED.html",
    "CEO-ADMIN-GUIDE.md",
    "CEO-ADMIN-GUIDE.html",
    "EMERGENCY-PROCEDURES.md",
    "EMERGENCY-PROCEDURES.html",
}

# Load-bearing substrings that MUST survive in a synced file. The sync blindly
# copies templates/ -> docs/, so an edit that silently drops a section would
# faithfully propagate the deletion into the distributed docs (this has
# recurred — e.g. the uv-dependency docs vanishing from GETTING-STARTED). If a
# template is missing any anchor for its name, the hook REFUSES to propagate and
# shouts, leaving the last-good docs/ copy intact for a human to reconcile.
# Keyed by filename; add anchors as load-bearing sections are identified.
REQUIRED_ANCHORS = {
    "GETTING-STARTED.md": ["uv sync", "DEPENDENCY-POLICY"],
}


def _missing_anchors(file_path: Path) -> list:
    """Return required anchors absent from the file's text (empty list = OK)."""
    anchors = REQUIRED_ANCHORS.get(file_path.name)
    if not anchors:
        return []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []  # unreadable source — let the normal copy path surface the error
    return [a for a in anchors if a not in text]


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        print(f"[sync-docs] failed to parse input: {e}", file=sys.stderr)
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    file_path_str = tool_input.get("file_path", "")

    if not file_path_str:
        sys.exit(0)

    file_path = Path(file_path_str)

    # Normalize path separators for template detection
    norm_path = str(file_path).replace("\\", "/")

    # Check if the written file is in templates/ and is a sync target
    if "/templates/" not in norm_path:
        sys.exit(0)

    if file_path.name not in SYNC_FILES:
        sys.exit(0)

    # Determine project directory (for the HTML renderer, which lives in the
    # engine clone) and the docs/ target.
    project_dir = Path(input_data.get("cwd") or Path.cwd())
    # The docs/ target is resolved from the TEMPLATE's own location, NOT the cwd.
    # templates/ and docs/ are siblings under one root; for a CEO-only guide that
    # root is the DATA overlay, even though the edit is made from the engine cwd.
    # Resolving from cwd wrote the data-overlay guide's docs copy into the engine
    # tree, which the push-time leak-wall then (correctly) refused — a silent push
    # failure. Anchor it to the template instead.
    docs_dir = file_path.resolve().parent.parent / "docs"
    target = docs_dir / file_path.name

    docs_dir.mkdir(parents=True, exist_ok=True)

    # Anchor guard: refuse to propagate a template that lost a load-bearing
    # section, rather than faithfully copying the deletion into docs/.
    missing = _missing_anchors(file_path)
    if missing:
        warn = (
            f"BLOCKED sync of {file_path.name}: required content missing "
            f"({', '.join(missing)}). The docs/ copy was left unchanged. Restore "
            f"the dropped section in templates/{file_path.name} and re-save."
        )
        print(f"[sync-docs] {warn}", file=sys.stderr)
        json.dump({"additionalContext": f"Warning: {warn}"}, sys.stdout)
        sys.exit(0)

    # Copy the file
    sync_msg = ""
    try:
        shutil.copy2(file_path, target)
        sync_msg = f"Auto-synced templates/{file_path.name} -> docs/{file_path.name}"
    except Exception as e:
        print(f"[sync-docs] failed to copy {file_path.name}: {e}", file=sys.stderr)
        json.dump({
            "additionalContext": f"Warning: Failed to sync {file_path.name} to docs/: {e}"
        }, sys.stdout)
        sys.exit(0)

    # If MD was edited, regenerate the matching HTML in both templates/ and docs/.
    # Non-blocking: regen failure produces a warning but never aborts.
    regen_msg = ""
    if file_path.suffix.lower() == ".md":
        regen_script = project_dir / "scripts" / "regenerate-docs-html.py"
        if regen_script.exists():
            try:
                for md_target in (file_path, target):
                    subprocess.run(
                        [sys.executable, str(regen_script), "--quiet", str(md_target)],
                        cwd=project_dir,
                        timeout=30,
                        capture_output=True,
                        check=False,
                    )
                regen_msg = f" + regenerated HTML for {file_path.name}"
            except Exception as e:
                print(f"[sync-docs] HTML regen warning for {file_path.name}: {e}", file=sys.stderr)
                regen_msg = f" (HTML regen warning: {e})"

    json.dump({
        "additionalContext": sync_msg + regen_msg
    }, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
