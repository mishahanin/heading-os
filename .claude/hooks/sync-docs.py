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

# Pairs that are ALSO published on the engine's public docs site, on top of the
# copy that feeds the corporate repo. Destination is a property of the FILE, not
# of where its template happens to live: anchoring every file to the template's
# own sibling docs/ was the fix for a CEO-only guide leaking into the engine tree,
# and it over-corrected. EMERGENCY-PROCEDURES is engine-routed and public, so
# sending it to the overlay alone froze docs/EMERGENCY-PROCEDURES.md at its
# 2026-06-26 content while the template moved on, in silence.
ENGINE_PUBLISHED = {
    "EMERGENCY-PROCEDURES.md",
    "EMERGENCY-PROCEDURES.html",
}

# The engine clone, anchored to this file rather than to the session's cwd, so a
# hook that fires while the cwd is the data overlay still finds the right tree.
ENGINE_ROOT = Path(__file__).resolve().parents[2]


def sync_targets(file_path: Path, engine_root: Path = ENGINE_ROOT) -> list:
    """Every docs/ path a template must be copied to, in publish order.

    The template's own sibling docs/ always receives the copy; an engine-published
    pair additionally receives one in the engine clone. Kept a pure function so
    tests can assert the destinations without running the hook.
    """
    targets = [file_path.resolve().parent.parent / "docs" / file_path.name]
    if file_path.name in ENGINE_PUBLISHED:
        engine_target = engine_root / "docs" / file_path.name
        if engine_target not in targets:
            targets.append(engine_target)
    return targets


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
    # templates/ and docs/ are siblings under one root; for a CEO-only guide that
    # root is the DATA overlay, even though the edit is made from the engine cwd.
    # Resolving from cwd wrote the data-overlay guide's docs copy into the engine
    # tree, which the push-time leak-wall then (correctly) refused — a silent push
    # failure. See sync_targets() for why a public page needs the engine copy too.
    targets = sync_targets(file_path)

    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)

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
        for t in targets:
            shutil.copy2(file_path, t)
        sync_msg = f"Auto-synced templates/{file_path.name} -> {len(targets)} docs/ copy(ies)"
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
                for md_target in [file_path, *targets]:
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
