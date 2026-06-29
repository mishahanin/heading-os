#!/usr/bin/env python3
"""
checkpoint-offer.py - Claude Code Stop hook.

Reads .claude/state/checkpoint-state.json (written by checkpoint-statusline.py).
If the state indicates a checkpoint offer is due (soft or hard level, with
hysteresis bucket not yet announced), emits {"decision": "block", "reason": ...}
with bilingual RU+EN options. Otherwise exits silently.

Anti-loop: bails immediately if payload.stop_hook_active is true.

Auto-compact is NOT touched - this hook only surfaces the offer, never blocks
or invokes compact directly.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
STATE_PATH = WORKSPACE / ".claude" / "state" / "checkpoint-state.json"


SOFT_BODY_RU = """\
Контекст использован примерно на {used:.0f}% (~{remaining:.0f}% осталось).
Можно зафиксировать checkpoint, чтобы потом продолжить с чистым контекстом.

Варианты:
1. `/checkpoint` - сохранить summary + continuation prompt в outputs/operations/handoff-archive/, без compact.
2. `/compact` - запустить manual compact сейчас; post-compact hook сохранит summary автоматически.
3. продолжать без compact - работа идёт как есть."""

SOFT_BODY_EN = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining).
Consider checkpointing now so you can resume later with a fresh context.

Options:
1. `/checkpoint` - save a summary and continuation prompt under outputs/operations/handoff-archive/, no compact.
2. `/compact` - run a manual compact now; the post-compact hook will save the compact summary.
3. continue without compact - keep working as is."""


HARD_BODY_RU = """\
Контекст использован примерно на {used:.0f}% - достигнут жёсткий порог.
Настоятельно рекомендуется checkpoint или compact перед продолжением.

Рекомендуемые варианты:
1. `/checkpoint` - сохранить summary + continuation prompt (сохраняет работу; контекст НЕ освобождает).
2. `/compact` - запустить manual compact сейчас; post-compact hook сохранит summary, контекст освободится.

Опцию «продолжать без compact» не предлагать."""

HARD_BODY_EN = """\
Context is about {used:.0f}% used - hard threshold reached.
Strongly recommend a checkpoint or compact before continuing further.

Recommended options:
1. `/checkpoint` - save a summary and continuation prompt (preserves work; does not free context).
2. `/compact` - run a manual compact now; the post-compact hook will save the compact summary and free context.

Do not offer "continue without compact"."""


REASON_WRAPPER = """\
Использование контекста ~{used:.0f}%, достигнут порог checkpoint.

НЕ запускай /compact автоматически.
НЕ создавай файлы автоматически без явного одобрения пользователя.

Покажи пользователю варианты:

{body_ru}

Жди решения пользователя.

---

Context window usage is approximately {used:.0f}%, which crossed the project checkpoint threshold.

Do not run /compact automatically.
Do not create files automatically unless the user approves.

Ask the user, briefly, with these options:

{body_en}

Wait for the user's decision."""


def build_reason(level: str, used: float, remaining: float) -> str:
    """Render the offer reason. Each language section carries its OWN single-language
    body so the options block appears once per language (not the doubled bilingual
    body the old single {body} placeholder produced)."""
    if level == "hard":
        body_ru, body_en = HARD_BODY_RU, HARD_BODY_EN
    else:
        body_ru, body_en = SOFT_BODY_RU, SOFT_BODY_EN
    return REASON_WRAPPER.format(
        used=used,
        body_ru=body_ru.format(used=used, remaining=remaining),
        body_en=body_en.format(used=used, remaining=remaining),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    # Anti-loop guard - mandatory for Stop hooks
    if payload.get("stop_hook_active"):
        return 0

    state = read_json(STATE_PATH)
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
    state["last_offer_at"] = utc_now()
    try:
        write_json_atomic(STATE_PATH, state)
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

    reason = build_reason(level, used, remaining)

    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
