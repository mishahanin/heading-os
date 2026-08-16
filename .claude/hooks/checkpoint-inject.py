#!/usr/bin/env python3
"""
checkpoint-inject.py - Claude Code SessionStart hook (matcher: compact|clear|resume).

Reads THIS session's handoff pointer files from
outputs/operations/handoff-archive/.latest/<session-slug>/ and prints them to
stdout, which Claude Code injects into the first user turn of the new session.

Two rules keep the injection honest:

  - Own session only. Until 2026-08-16 the pointer was one shared pair of files
    for the whole workspace, so a resumed session could be handed the handoff of
    a DIFFERENT session while this text asserted that a previous checkpoint had
    been found - a claim nothing in the hook had established
    (.claude/rules/scope-claims.md). There is deliberately no fallback to
    "the newest handoff on disk": with several sessions open, the newest is
    usually somebody else's work, and no handoff beats a foreign one.
  - No body on `compact`. The harness has just put a summary of THIS session
    into context; re-injecting a pointer only competes with it. The shared
    `.latest/summary.md` still exists for scripts/next-signal.py, which asks a
    different question ("the newest handoff in this workspace") where
    last-writer-wins is the right answer.

Silent on fresh sessions (matcher excludes 'startup' in settings registration).
Silent when this session has no pointer files.

Truncates to bounded sizes so a very large handoff cannot dominate context.
"""

import json
import sys
from pathlib import Path

_BOOT = Path(__file__).resolve()
_ROOT = _BOOT.parent.parent.parent
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        _ROOT = _candidate
        sys.path.insert(0, str(_candidate))
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402

CP.force_utf8()

MAX_SUMMARY_CHARS = 8000
MAX_PROMPT_CHARS = 4000

AUTO_CLOSING = (
    "AUTO MODE: continue the latest unfinished task from this handoff without "
    "asking for confirmation. Briefly restate the objective and the next "
    "concrete action, then proceed. Repository state is authoritative."
)

MANUAL_CLOSING = (
    "Use the handoff to continue the latest unfinished task. Repository state "
    "is authoritative."
)

AUTO_AFTER_COMPACT = """# Checkpoint

AUTO MODE: continue the latest unfinished task from the compaction summary
above, without asking for confirmation. Briefly restate the objective and the
next concrete action, then proceed. Repository state is authoritative.

This session's saved handoffs are under the handoff archive; the compaction
summary already in context is the fresher of the two, so none is re-injected.
"""


def read_limited(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - an unreadable pointer is not fatal
        print(f"checkpoint-inject: unreadable pointer {path.name}: {exc}", file=sys.stderr)
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[Truncated by checkpoint-inject]\n"


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        print(f"checkpoint-inject: stdin read failed: {exc}", file=sys.stderr)

    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

    auto = CP.config()["auto"]

    if (payload.get("source") or "").strip() == "compact":
        if auto:
            print(AUTO_AFTER_COMPACT)
        return 0

    project = CP.project_root(payload)
    slug = CP.session_slug(payload)
    latest = CP.latest_dir(CP.handoff_dir(project, _ROOT), slug)

    summary = read_limited(latest / "summary.md", MAX_SUMMARY_CHARS)
    prompt = read_limited(latest / "prompt.md", MAX_PROMPT_CHARS)

    sections: list[str] = []
    if summary:
        sections.append(f"## Latest summary\n\n{summary}")
    if prompt:
        sections.append(f"## Continuation prompt\n\n{prompt}")
    if not sections:
        return 0

    body = "\n\n".join(sections)
    closing = AUTO_CLOSING if auto else MANUAL_CLOSING

    print(
        f"""# Auto-injected handoff

A handoff saved by this session ({slug}) was found in the handoff archive. Check
its Generated timestamp before trusting it over the repository.

{body}

---

{closing}
"""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
