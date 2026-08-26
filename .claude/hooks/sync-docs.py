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


def is_real_template(file_path: Path) -> bool:
    """True only for a file directly inside a `templates/` directory.

    The trigger was the unanchored substring `"/templates/" in str(path)`, and
    it was wrong in both directions. Found by the 2026-08-23 audit and
    reproduced:

      * FALSE POSITIVE, the dangerous one. A write to
        `outputs/scratch/templates/EMERGENCY-PROCEDURES.md` matched. Because
        that name is in ENGINE_PUBLISHED, `sync_targets` then returned the real
        `<engine>/docs/EMERGENCY-PROCEDURES.md` and `shutil.copy2` overwrote the
        published document with scratch content. `REQUIRED_ANCHORS` only covers
        GETTING-STARTED, so nothing shouted. It also slipped past the
        `check_protect_docs` wall in `.claude/hooks/_dispatch.py`, which exists
        to stop exactly this file being clobbered: the copy happens inside this
        hook, not through a tool call, so the wall never sees it.

      * FALSE NEGATIVE. A RELATIVE path, `templates/GETTING-STARTED.md`, has no
        leading slash, so the substring did not match and an ordinary edit was
        silently not synced.

    Resolving first fixes the false negative. Three structural tests fix the
    false positive, in increasing strictness:

      1. the parent directory must BE named `templates`;
      2. its root must not be a strict DESCENDANT of the engine or the data
         root, which is what `outputs/scratch/templates/` is and what a real
         `<root>/templates/` never is;
      3. for a name in ENGINE_PUBLISHED, whose sync reaches the engine's
         published `docs/`, the root must be the engine root or the data root
         exactly. That one destination is public, so it earns identity rather
         than shape.

    Rule 2 is deliberately shape-based, not an identity check, so a synthetic
    root in a test tree still exercises the sync. Rule 3 is the identity check,
    scoped to the only destination where a wrong write is published.
    """
    try:
        resolved = file_path.resolve()
    except OSError:
        return False
    if resolved.parent.name != "templates":
        return False
    root = resolved.parent.parent

    known = {ENGINE_ROOT}
    # The data overlay holds the CEO-only guides; resolve it the same way the
    # engine does, and treat its absence as "engine-only layout".
    try:
        sys.path.insert(0, str(ENGINE_ROOT))
        from scripts.utils.workspace import get_data_root  # noqa: PLC0415
        known.add(Path(get_data_root()).resolve())
    except Exception as exc:  # noqa: BLE001 — never let path resolution break a write
        print(f"[sync-docs] data-root lookup skipped ({exc})", file=sys.stderr)

    if any(k in root.parents for k in known):
        print(f"[sync-docs] refusing {resolved}: a templates/ directory nested "
              "inside a workspace root is not a template source", file=sys.stderr)
        return False

    if resolved.name in ENGINE_PUBLISHED and root not in known:
        print(f"[sync-docs] refusing {resolved}: {resolved.name} publishes to the "
              "engine docs site, so its template must live in a workspace root",
              file=sys.stderr)
        return False

    return True


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

    # A payload that is valid JSON but not an object still reaches `.get`.
    # `[]`, `"x"`, `3` and `null` all parse, then raise an uncaught
    # AttributeError. Swept 2026-08-23 across every stdin hook: six crashed on
    # all four shapes. Same defect checkpoint-inject.py fixed on 2026-08-20;
    # the sweep is how the rest were found.
    if not isinstance(input_data, dict):
        sys.exit(0)

    # `.get("tool_input", {})` returns the STORED value when the key is present,
    # so `null`, a list or a string reached `.get` and raised an uncaught
    # AttributeError one line below the guard that had just handled the same
    # shape at the top level. `_dispatch.py` and `data-path-redirect.py` both
    # already guard the nested value.
    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        print(f"[sync-docs] tool_input was {type(tool_input).__name__}, "
              "not an object", file=sys.stderr)
        sys.exit(0)
    file_path_str = tool_input.get("file_path", "")

    if not file_path_str:
        sys.exit(0)

    file_path = Path(file_path_str)

    if file_path.name not in SYNC_FILES:
        sys.exit(0)

    if not is_real_template(file_path):
        sys.exit(0)

    # The payload cwd is read by nothing here any more: the renderer resolves
    # from ENGINE_ROOT and the targets from the template's own tree, so no path
    # in this hook depends on where the shell happens to be parked.
    #
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
        # ENGINE_ROOT, not the payload cwd. The comment above already said the
        # renderer "lives in the engine clone" while the code looked for it under
        # the session's cwd, so a session started in any engine subdirectory - or
        # in the data overlay, the case ENGINE_ROOT was introduced for - found no
        # script, skipped regeneration, and still returned "Auto-synced ... ->
        # N docs/ copy(ies)". The `.html` twin is itself in SYNC_FILES and is
        # distributed, so it drifted silently, which is the exact staleness the
        # note at the top of this file records happening once already.
        regen_script = ENGINE_ROOT / "scripts" / "regenerate-docs-html.py"
        if regen_script.exists():
            try:
                # The result used to be discarded, so a renderer exiting 1
                # (a missing pygments, a template error) left stale HTML while
                # this hook appended "+ regenerated HTML" — a success claim on a
                # distribution pipeline that had not run. Found by the
                # 2026-08-23 audit. Non-zero stays non-blocking, per the module
                # docstring's promise of a warning, but it must now be SAID.
                failures = []
                for md_target in [file_path, *targets]:
                    proc = subprocess.run(
                        [sys.executable, str(regen_script), "--quiet", str(md_target)],
                        cwd=ENGINE_ROOT,
                        timeout=30,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if proc.returncode != 0:
                        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                        failures.append(f"{md_target.name} (exit {proc.returncode}"
                                        + (f": {detail[-1]}" if detail else "") + ")")
                if failures:
                    print(f"[sync-docs] HTML regen FAILED: {'; '.join(failures)}",
                          file=sys.stderr)
                    regen_msg = (f" (HTML regen FAILED for {len(failures)} target(s): "
                                 f"{'; '.join(failures)}. The HTML is STALE.)")
                else:
                    regen_msg = f" + regenerated HTML for {file_path.name}"
            except Exception as e:
                print(f"[sync-docs] HTML regen warning for {file_path.name}: {e}", file=sys.stderr)
                regen_msg = f" (HTML regen warning: {e})"
        else:
            # The missing-script branch had no `else` at all: nothing on stderr,
            # nothing in the message, and a success line claiming a complete
            # sync. A renderer that is not there is the same "the HTML is STALE"
            # outcome as one that exits non-zero, so it is reported the same way.
            print(f"[sync-docs] HTML NOT regenerated: no renderer at "
                  f"{regen_script}", file=sys.stderr)
            regen_msg = (f" (HTML NOT regenerated: no renderer at "
                         f"{regen_script}. The HTML is STALE.)")

    json.dump({
        "additionalContext": sync_msg + regen_msg
    }, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
