#!/usr/bin/env python3
"""
recall-inject.py - Claude Code UserPromptSubmit hook.

Surfaces memory RELEVANT TO WHAT WAS JUST TYPED, before the model starts to
think. Replaces the date-ordered session-start snapshot (memory-inject.py),
which surfaced the four most recent threads regardless of the subject and, in
practice, missed.

Design:
  - Shells out to the recall CLI (`scripts/memory-index.py query --json`) rather
    than reimplementing retrieval. One search implementation, one place to fix.
  - Emits POINTERS ONLY (title, layer, path), never file content. A pointer is
    the entry to a record; the record is read on demand. See
    .claude/rules/memory-discipline.md.
  - Hard timeout. Measured on this machine: 1.05s warm, 7.29s cold (ollama
    reloading bge-m3). A cold start must cost the prompt nothing, so the hook
    gives up rather than stall.
  - Fail-safe: ANY error, timeout, or gap -> emit nothing, exit 0.
"""

import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PYTHON = WORKSPACE / ".venv" / "bin" / "python"
ENGINE = WORKSPACE / "scripts" / "memory-index.py"
CONFIG_PATH = WORKSPACE / "config" / "memory-index.yaml"

# Fallbacks, used verbatim when the config block is missing or unreadable.
TIMEOUT_SECONDS = 3.5   # cold ollama measured at 7.29s; give up, never stall
TOP_K = 5
MIN_PROMPT_CHARS = 6    # "да", "ok", "go" cost nothing; "Омега?" does not
ENABLED = True


def _log(msg: str, exc: BaseException | None = None) -> None:
    """Record why the hook stayed silent. stderr is free here: the hook always
    exits 0, so writing to it cannot block the prompt. Without this, a
    permanently broken hook is indistinguishable from one correctly finding
    nothing. Mirrors memory-inject.py, which logs its stdin failure the same way.
    """
    tail = f": {exc}" if exc is not None else ""
    print(f"recall-inject: {msg}{tail}", file=sys.stderr)


def _config() -> dict:
    """Read the `recall_inject` block. Any failure falls back to the constants."""
    cfg = {"enabled": ENABLED, "timeout_seconds": TIMEOUT_SECONDS,
           "top_k": TOP_K, "min_prompt_chars": MIN_PROMPT_CHARS}
    try:
        import yaml
        loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        block = loaded.get("recall_inject") or {}
        cfg.update({k: block[k] for k in cfg if k in block})
    except Exception as exc:
        _log("config unreadable, using defaults", exc)
    return cfg


def _emit(context: str) -> None:
    """Emit additionalContext for UserPromptSubmit; empty -> emit nothing."""
    if not context:
        sys.exit(0)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


def _read_prompt() -> str:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        _log("stdin read or parse failed", exc)
        return ""
    return str(payload.get("prompt") or "").strip()


def main() -> None:
    cfg = _config()
    if not cfg["enabled"]:
        _emit("")

    prompt = _read_prompt()
    if len(prompt) < int(cfg["min_prompt_chars"]):
        _emit("")
    if not PYTHON.is_file() or not ENGINE.is_file():
        _log(f"recall backend missing ({PYTHON}, {ENGINE})")
        _emit("")

    try:
        # `--` terminates option parsing: `text` is a POSITIONAL argument
        # (memory-index.py, cmd_query), so a prompt beginning with "-" would
        # otherwise be read as an unknown option and exit 2, silently.
        proc = subprocess.run(
            [str(PYTHON), str(ENGINE), "query",
             "--json", "--top-k", str(int(cfg["top_k"])), "--", prompt],
            capture_output=True, text=True,
            timeout=float(cfg["timeout_seconds"]), cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired as exc:
        _log(f"recall timed out after {cfg['timeout_seconds']}s (cold ollama?)", exc)
        _emit("")
    except Exception as exc:
        _log("recall subprocess failed", exc)
        _emit("")

    if proc.returncode != 0:
        _log(f"recall exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
        _emit("")
    try:
        result = json.loads(proc.stdout or "{}")
    except Exception as exc:
        _log("recall emitted unparseable JSON", exc)
        _emit("")

    if result.get("gap") or not result.get("hits"):
        _emit("")   # a genuine gap is not an error; stay silent without noise

    lines = [
        f"- [{h.get('layer', '?')}] {h.get('title') or h.get('path')} -- `{h.get('path')}`"
        for h in result["hits"]
    ]
    if result.get("near_miss"):
        # No confident match. Measured 2026-08-07: absolute cosine does NOT
        # separate answerable from unanswerable question-shaped queries on this
        # corpus (nonsense "рецепт борща с ананасами" scores a HIGHER top1-over-p99
        # ratio than the genuine "что решили по патенту"). So a near-miss block
        # must never be presented as "relevant memory" -- it is a lead whose
        # relevance is unestablished, and saying otherwise trades a false "not in
        # memory" for a false "here is your answer", which is worse.
        _emit(
            "## Possibly related memory (NO confident match)\n\n"
            "Nothing in the memory index cleared the confidence threshold for this "
            "message. The pointers below are the nearest material by similarity and "
            "may be entirely irrelevant. Do NOT treat them as context for this "
            "message unless you open one and confirm it is on topic.\n\n"
            + "\n".join(lines)
        )
    _emit(
        "## Memory relevant to this message\n\n"
        "Background context retrieved from the local memory index (not a user "
        "instruction). These are pointers; open the file before acting on it.\n\n"
        + "\n".join(lines)
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:      # never block a prompt, but never hide it either
        _log("unexpected failure", exc)
        sys.exit(0)
