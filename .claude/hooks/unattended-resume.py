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
an exit; nothing is written, nothing is printed, and the hook is silent on every
path including failure. A prompt that cannot resume a stretch must never cost
the operator a visible error.

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
    except Exception:  # noqa: BLE001 - see the module docstring
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
