#!/usr/bin/env python3
"""
checkpoint-offer.py - Claude Code Stop hook.

Reads THIS session's .claude/state/checkpoint-<session-slug>.json (written by
checkpoint-statusline.py). If the state indicates a checkpoint offer is due
(soft or hard level, with hysteresis bucket not yet announced), emits
{"decision": "block", "reason": ...}. Otherwise exits silently.

The state file is per session. It was shared until 2026-08-16, and a shared file
means a sibling session's context usage blocks this session's turns: measured
with session A at 46% and session B idle, B got the offer and A got nothing.

Two behaviours, selected by CLAUDE_HANDOFF_AUTO:
  - off (the default): surface the /checkpoint vs /compact vs continue choice
    and wait for the operator. Nothing is written automatically.
  - on: drive the assistant to save the checkpoint silently and resume the task.
    The capability ships; the operator switches it on.

Anti-loop: bails immediately if payload.stop_hook_active is true.

Auto-compact is NOT touched - this hook only surfaces the offer, never blocks
or invokes compact directly.
"""

import json
import sys
from pathlib import Path

_BOOT = Path(__file__).resolve()
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402

CP.force_utf8()

SKILL_REF = ".claude/skills/checkpoint/SKILL.md"


SOFT_BODY = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining).
Consider checkpointing now so you can resume later with a fresh context.

Options:
1. `/checkpoint` - save a summary and continuation prompt under outputs/operations/handoff-archive/, no compact.
2. `/compact` - run a manual compact now; the post-compact hook will save the compact summary.
3. continue without compact - keep working as is."""


HARD_BODY = """\
Context is about {used:.0f}% used - hard threshold reached.
Strongly recommend a checkpoint or compact before continuing further.

Recommended options:
1. `/checkpoint` - save a summary and continuation prompt (preserves work; does not free context).
2. `/compact` - run a manual compact now; the post-compact hook will save the compact summary and free context.

Do not offer "continue without compact"."""


REASON_WRAPPER = """\
Context window usage is approximately {used:.0f}%, which crossed the project checkpoint threshold.

Do not run /compact automatically.
Do not create files automatically unless the user approves.

Ask the user, briefly, with these options, in the language the user is speaking:

{body}

Wait for the user's decision."""


AUTO_WRAPPER = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining), which crossed the {level} checkpoint threshold. AUTO MODE is on.

Do this now, without asking:
1. Save a checkpoint silently, following @{skill} exactly: run `python scripts/checkpoint-paths.py` for this session's stamp and paths, write the archive it names, then update the two pointer files it names. Those paths are scoped to this session - never write into another session's pointer directory.
2. Print ONE line naming the archive path written.
3. If you were mid-task, resume it where you left off. If you had finished and were waiting for the user, stop after that line.

Do NOT run /compact yourself. {compaction}"""


def _compaction_sentence() -> str:
    """What this hook actually knows about where compaction fires.

    Nothing here can trigger compaction, and nothing here can observe the
    harness default either. Naming a percentage the environment never set is an
    assertion the method does not support, so an unconfigured environment gets
    said so instead (.claude/rules/scope-claims.md).
    """
    point = CP.compact_point()
    if point is None:
        return (
            "Native compaction is not configured here (neither "
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE nor CLAUDE_CODE_AUTO_COMPACT_WINDOW "
            "is set), so this hook cannot say when it will fire; the checkpoint "
            "on disk is what survives either way."
        )
    kind, value = point
    if kind == "percent":
        return (
            f"Native auto-compact is configured to fire near {value}% used, and "
            "its PostCompact hook saves and re-injects the handoff automatically."
        )
    return (
        f"Native auto-compact is configured at {value} tokens (measured against "
        "the smaller of that and the model's own window), and its PostCompact "
        "hook saves and re-injects the handoff automatically."
    )


def build_reason(level: str, used: float, remaining: float) -> str:
    """Render the offer reason, in English only.

    The reason text is emitted on stderr and the operator sees it, so every
    duplicate is a duplicate the operator reads. It carried a full Russian
    section beside the English one, and the assistant's own answer made a third
    rendering of the same three lines. English alone is the right single
    language here: this hook ships in a public engine, and the wrapper asks for
    the reply in whatever language the operator is actually speaking.
    """
    body = HARD_BODY if level == "hard" else SOFT_BODY
    return REASON_WRAPPER.format(
        used=used,
        body=body.format(used=used, remaining=remaining),
    )


def build_auto_reason(level: str, used: float, remaining: float) -> str:
    """The hands-off variant: save, say where, carry on.

    It POINTS AT the skill rather than restating its section list. The nexi
    plugin inlined the whole contract into the hook text, which is a second copy
    of a format that only one file should define; the copy that stops being
    updated is the one the model reads.
    """
    return AUTO_WRAPPER.format(
        used=used,
        remaining=remaining,
        level=level,
        skill=SKILL_REF,
        compaction=_compaction_sentence(),
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    # Anti-loop guard - mandatory for Stop hooks
    if payload.get("stop_hook_active"):
        return 0

    project = CP.project_root(payload)
    state_path = CP.state_path(project, CP.session_slug(payload))
    state = CP.read_json(state_path)
    if not state.get("needs_compact_offer"):
        return 0

    used_raw = state.get("used_percentage")
    try:
        used = float(used_raw) if used_raw is not None else 0.0
    except (TypeError, ValueError):
        return 0

    level = state.get("offer_level")
    if level not in ("soft", "hard"):
        # Statusline always sets a valid level when needs_compact_offer=True;
        # missing here means stale state from before the contract - skip.
        return 0

    bucket = int(state.get("offer_bucket") or state.get("current_bucket") or 0)

    # Mark offer as delivered (hysteresis)
    state["needs_compact_offer"] = False
    state["offer_level"] = None
    state["last_offered_bucket"] = bucket
    state["last_offer_at"] = CP.utc_now().isoformat()
    try:
        CP.write_json_atomic(state_path, state)
    except Exception as exc:
        # If state write fails, still deliver the offer this turn
        print(f"checkpoint-offer: state write failed: {exc}", file=sys.stderr)

    raw_remaining = state.get("remaining_percentage")
    try:
        remaining = (
            float(raw_remaining) if raw_remaining is not None else 100.0 - used
        )
    except (TypeError, ValueError):
        remaining = 100.0 - used
    if remaining < 0:
        remaining = 0.0

    build = build_auto_reason if CP.config()["auto"] else build_reason
    reason = build(level, used, remaining)

    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
