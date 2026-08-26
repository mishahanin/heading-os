#!/usr/bin/env python3
"""UserPromptSubmit hook: the operator's instruction resumes a paused stretch.

`clear_unattended_window` has always been documented as "the operator's next
instruction clears it", and `--done` prints "Your next instruction resumes it".
Until 2026-08-20 the clearing happened at the next STOP instead, which is the
end of the turn his instruction opened. So for the whole of that turn the state
still carried the pause, and the status bar - the surface he reads first -
rendered `⏸ unattended paused` while the assistant was actively working under a
stretch his message had already resumed. He reported it twice.

Moving the clear to prompt submission makes the code match its own docstring.
It is also the more accurate event: the window belongs to the operator's turn,
and the turn begins when he presses Enter, not when the assistant stops.

Cheap by construction. The common case - no pause marker - is one JSON read and
an exit; nothing is written and nothing is printed. The rule is that this hook
never writes to STDOUT and never blocks, because a UserPromptSubmit hook's stdout
is injected into the prompt and a prompt that cannot resume a stretch must not
cost the operator a visible error. It is not silent on stderr, and never was:
`CP.project_root` and `CP.read_json` both report there from inside the same try
block, and a failed clear now says so too - the pause marker surviving is what
keeps the status bar showing "unattended paused" for a stretch already resumed.

The Stop hook keeps its own `prompt_id` comparison. That is not redundancy to
delete: this hook is not registered in every clone, and a stretch that could
only be resumed from here would strand itself wherever it is missing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE))

# The literal the Stop hook submits through HERDR to drive a compaction. The
# harness delivers it here as an ordinary prompt, and it is NOT the operator
# speaking: treating it as a new instruction would reset the continuation
# counter on every compaction and retire the ceiling that bounds a night.
# `_queue_pending` in checkpoint-offer.py excludes it for the same reason.
#
# Imported from the one owner rather than copied. This was a local literal while
# every other consumer - checkpoint-offer.py's exclusion, scripts/compact-now.py
# - bound to the shared constant, so a change to the submitted command (a
# `--kind` argument is already under consideration) would have left this hook
# comparing against a bare `/compact`, failing to match, and treating each driven
# compaction as the operator speaking. The literal survives only as the fallback
# for a clone where the import is unavailable, and a test pins the two equal.
try:
    from scripts.utils.herdr_agent import COMPACT_COMMAND
except Exception:  # noqa: BLE001 - a bundled clone may not carry the module
    COMPACT_COMMAND = "/compact"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - a malformed payload must not surface
        return 0

    # A payload that is valid JSON but not an object still reaches `.get`.
    # `[]`, `"x"`, `3` and `null` all parse, then raise an uncaught
    # AttributeError. Swept 2026-08-23 across every stdin hook: six crashed on
    # all four shapes. Same defect checkpoint-inject.py fixed on 2026-08-20;
    # the sweep is how the rest were found.
    if not isinstance(payload, dict):
        return 0

    if str(payload.get("prompt") or "").strip() == COMPACT_COMMAND:
        return 0

    try:
        from scripts.utils import checkpoint_paths as CP

        project = CP.project_root(payload)
        path = CP.state_path(project, CP.session_slug(payload))
        state = CP.read_json(path)
        # Read first, write only if there is something to clear. A prompt in a
        # session that never raised the switch touches no file at all.
        if not (state.get("unattended_done_at") or state.get("unattended_paused_at")):
            return 0
        with CP.locked_state(path) as fresh:
            CP.clear_unattended_window(fresh)
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        # Stderr, not stdout. The docstring's reason for swallowing covers
        # stdout, which for a UserPromptSubmit hook is injected into the prompt;
        # it never covered the record. If `write_json_atomic` fails inside
        # `locked_state` (a read-only mount, ENOSPC, a permission change under
        # `.claude/state/`), the pause marker survives and the status bar keeps
        # rendering "unattended paused" for a stretch the operator has already
        # resumed - the exact defect this hook was written to fix - with nothing
        # anywhere recording that the clear failed.
        print(f"unattended-resume: could not clear the pause window: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
