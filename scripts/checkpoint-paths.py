#!/usr/bin/env python3
"""Print this session's checkpoint paths, ready to write.

The hooks get a payload and know which session they are. `/checkpoint` is
model-driven and gets nothing, so before this script it CONSTRUCTED its own
paths and its SKILL.md told it to fall back to the literal slug "session" when
it could not derive one. That is how a manual checkpoint lands in the wrong
place: every path below the workspace root is keyed by session id now, and
guessing the key defeats the keying.

The session id comes from CLAUDE_CODE_SESSION_ID, which Claude Code exports to
child processes (verified on 2.1.228).

Usage:
  python scripts/checkpoint-paths.py           # key=value lines
  python scripts/checkpoint-paths.py --json    # the same, as JSON

Archive paths are DATA-root-relative (`outputs/...`), which is the form the
@-reference and the inject hook resolve. The state path is project-relative.
Write them as printed; do not rebuild them by hand.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


def collect() -> dict:
    project = CP.project_root()
    handoff = CP.handoff_dir(project, CP.engine_root())
    sid = CP.session_id()
    slug = CP.safe_slug(sid)
    stamp = CP.utc_now().strftime("%Y-%m-%d-%H%M%S")

    root = CP.engine_root()
    if CP.is_engine_tree(root):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.utils.workspace import get_data_root

        base = get_data_root()
    else:
        base = project

    def ref(path: Path) -> str:
        """Relative when it can be, absolute when it cannot. Total on purpose:
        a path the skill cannot resolve is worse than an ugly one."""
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            return path.as_posix()

    latest = CP.latest_dir(handoff, slug)
    shared = CP.latest_root(handoff)
    state = CP.state_path(project, slug)
    return {
        "session_id": sid,
        "session_slug": slug,
        "stamp": stamp,
        "project_root": str(project),
        "data_root": str(base),
        "archive": ref(handoff / f"{stamp}_handoff_manual_{slug}.md"),
        "summary_pointer": ref(latest / "summary.md"),
        "prompt_pointer": ref(latest / "prompt.md"),
        "shared_summary_pointer": ref(shared / "summary.md"),
        "shared_prompt_pointer": ref(shared / "prompt.md"),
        "state": (
            state.relative_to(project).as_posix()
            if project in state.parents
            else state.as_posix()
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print this session's checkpoint paths.")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of key=value")
    args = ap.parse_args(argv)

    paths = collect()
    if args.json:
        print(json.dumps(paths, indent=2))
    else:
        for key, value in paths.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
