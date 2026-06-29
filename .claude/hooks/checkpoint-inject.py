#!/usr/bin/env python3
"""
checkpoint-inject.py - Claude Code SessionStart hook (matcher: compact|clear|resume).

Reads the latest handoff pointer files from
outputs/operations/handoff-archive/.latest/ and prints them to stdout, which
Claude Code injects into the first user turn of the new session.

Silent on fresh sessions (matcher excludes 'startup' in settings registration).
Silent if pointer files do not exist.

Truncates to bounded sizes so a very large handoff cannot dominate context.
"""

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE))
from scripts.utils.workspace import get_outputs_dir  # noqa: E402

# Handoff archive is DATA -> resolves under the data root (sibling), not the engine.
LATEST_DIR = get_outputs_dir() / "operations" / "handoff-archive" / ".latest"
SUMMARY_PATH = LATEST_DIR / "summary.md"
PROMPT_PATH = LATEST_DIR / "prompt.md"

MAX_SUMMARY_CHARS = 8000
MAX_PROMPT_CHARS = 4000


def read_limited(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[Truncated by checkpoint-inject]\n"


def main() -> int:
    # Best-effort consume stdin so the hook contract is respected
    try:
        sys.stdin.read()
    except Exception as exc:
        print(f"checkpoint-inject: stdin read failed: {exc}", file=sys.stderr)

    if not SUMMARY_PATH.exists() and not PROMPT_PATH.exists():
        return 0

    summary = read_limited(SUMMARY_PATH, MAX_SUMMARY_CHARS)
    prompt = read_limited(PROMPT_PATH, MAX_PROMPT_CHARS)

    sections: list[str] = []
    if summary:
        sections.append(f"## Latest summary\n\n{summary}")
    if prompt:
        sections.append(f"## Continuation prompt\n\n{prompt}")

    if not sections:
        return 0

    body = "\n\n".join(sections)
    print(
        f"""# Auto-injected handoff / Авто-инжект handoff

Найден предыдущий checkpoint в `outputs/operations/handoff-archive/`.
A previous checkpoint was found in `outputs/operations/handoff-archive/`.

{body}

---

Используй handoff для продолжения работы. Repository state is authoritative.
"""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
