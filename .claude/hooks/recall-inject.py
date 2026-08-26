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
  - Fail-closed on the switch: a config that cannot be read means the hook
    cannot confirm it was enabled, so it stays silent rather than run anyway.
"""

import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
ENGINE = WORKSPACE / "scripts" / "memory-index.py"
CONFIG_PATH = WORKSPACE / "config" / "memory-index.yaml"

# The venv interpreter, laid out differently per platform: POSIX puts it in
# `bin/`, Windows in `Scripts/` with an `.exe` suffix. Both are probed at run
# time rather than assumed, because assuming `bin/python` made the hook
# permanently silent under the shipped windows settings template.
INTERPRETERS = (
    WORKSPACE / ".venv" / "bin" / "python",
    WORKSPACE / ".venv" / "Scripts" / "python.exe",
)

# Fallbacks, used verbatim when the config block omits a key.
TIMEOUT_SECONDS = 3.5   # cold ollama measured at 7.29s; give up, never stall
TOP_K = 5
NEAR_MISS_MAX = 3       # a below-threshold lead is cheaper wrong than right
# Length heuristic separating conversational filler from a question aimed at
# memory. Measured on real messages, with the subject replaced by the neutral
# placeholder "Омега" at the same length, so the counts below are the measured
# ones: "продолжай" is 9 characters and "спасибо, давай дальше" is 21, against
# "почему мы не пошли в Омегу" at 26 and "что мы решили по Омеге и почему" at
# 31. A cut at 25 splits those measurements. It is only a length heuristic and
# it is not exact: a short prompt can be a genuine question ("Омега?") and a
# long one can be filler. It is tuned to fail toward silence, because the cost
# of missing one pointer is lower than the cost of roughly 250 tokens and a
# backend round trip on every conversational reply.
MIN_PROMPT_CHARS = 25
ENABLED = True


def _log(msg: str, exc: BaseException | None = None) -> None:
    """Record why the hook stayed silent. stderr is free here: the hook always
    exits 0, so writing to it cannot block the prompt. Without this, a
    permanently broken hook is indistinguishable from one correctly finding
    nothing. Mirrors memory-inject.py, which logs its stdin failure the same way.
    """
    tail = f": {exc}" if exc is not None else ""
    print(f"recall-inject: {msg}{tail}", file=sys.stderr)


def _config() -> dict | None:
    """Read the `recall_inject` block, or None when the config cannot be read.

    Fail-closed on the switch. The harness launches this hook with the system
    `python3`, which need not have PyYAML installed, so `import yaml` fails on
    real machines rather than in theory. The earlier version swallowed that into
    a default of `enabled: True`, which meant `recall_inject.enabled: false` was
    silently ignored on exactly the machines least able to notice, while the
    docs promised the flag turned the hook off entirely. A hook that cannot
    confirm it was switched on must not run.

    Only the switch is fail-closed. The remaining knobs keep their constant
    defaults for a config that parses but omits a key; when the config does not
    parse at all, their values are moot because the hook emits nothing.
    """
    try:
        import yaml
        loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        _log(f"config unreadable ({CONFIG_PATH}), staying silent: cannot "
             "confirm recall_inject.enabled", exc)
        return None
    block = loaded.get("recall_inject") or {}
    cfg = {"enabled": ENABLED, "timeout_seconds": TIMEOUT_SECONDS,
           "top_k": TOP_K, "min_prompt_chars": MIN_PROMPT_CHARS}
    cfg.update({k: block[k] for k in cfg if k in block})
    return cfg


def _interpreter() -> Path | None:
    """First venv interpreter that exists, or None. See INTERPRETERS."""
    for candidate in INTERPRETERS:
        if candidate.is_file():
            return candidate
    return None


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
    if cfg is None or not cfg["enabled"]:
        _emit("")

    prompt = _read_prompt()
    if len(prompt) < int(cfg["min_prompt_chars"]):
        _emit("")
    python_bin = _interpreter()
    if python_bin is None or not ENGINE.is_file():
        _log(f"recall backend missing (tried {', '.join(str(p) for p in INTERPRETERS)}"
             f"; engine {ENGINE})")
        _emit("")

    try:
        # `--` terminates option parsing: `text` is a POSITIONAL argument
        # (memory-index.py, cmd_query), so a prompt beginning with "-" would
        # otherwise be read as an unknown option and exit 2, silently.
        proc = subprocess.run(
            [str(python_bin), str(ENGINE), "query",
             "--json", "--touch", "--top-k", str(int(cfg["top_k"])), "--", prompt],
            capture_output=True, text=True,
            timeout=float(cfg["timeout_seconds"]), cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired as exc:
        _log(f"recall timed out after {cfg['timeout_seconds']}s (cold ollama?)", exc)
        _emit("")
    except Exception as exc:
        _log("recall subprocess failed", exc)
        _emit("")

    # Parse BEFORE judging the exit code. A non-zero exit still carries JSON when
    # the backend refused to embed, and that refusal is the one failure the
    # operator must see rather than mistake for an empty memory.
    try:
        result = json.loads(proc.stdout or "{}")
    except Exception as exc:  # noqa: BLE001 - the hook must not break the turn
        # Log it WHERE it happens. The exception was discarded, and the message
        # further down then named "unparseable JSON" for three states this code
        # never distinguishes: stdout that truly failed to parse, stdout that was
        # empty (the `or "{}"` default parses fine), and stdout that parsed to a
        # valid but empty object. Only the first matched the sentence.
        _log("recall stdout did not parse as JSON", exc)
        result = {}

    down = result.get("embed_unavailable") or {}
    if down:
        _emit(
            "## WARNING: memory did NOT run — the embedder is down\n\n"
            f"{down.get('reason')}\n\n"
            "Recall answered nothing for this message, and that is an outage, not "
            "an empty memory. Tell the operator in your reply, at the top, before "
            "anything else. Embedding is pinned to the Windows-side ollama: start "
            "the Ollama tray app on Windows. Do not treat any 'not in memory' "
            "conclusion in this session as established until it is back.\n\n"
        )

    if proc.returncode != 0:
        _log(f"recall exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
        _emit("")
    if not result:
        # State what was observed, not a cause this branch never established.
        _log(f"recall returned no usable payload "
             f"({len(proc.stdout or '')} bytes on stdout)")
        _emit("")

    # The backend prints its red banner on stderr, which this hook captures and
    # discards on a zero exit -- so without this the session, the surface the
    # operator actually reads, would never learn the pinned GPU host was asleep.
    # Operator directive, 2026-08-21: say it at once, loudly.
    fallback = result.get("embed_fallback") or {}
    alert = (
        "## WARNING: memory ran on the FALLBACK embedder\n\n"
        f"The pinned host `{fallback.get('wanted')}` did not answer; this recall "
        f"used `{fallback.get('got')}` instead. Tell the operator in your reply, "
        "at the top, before anything else. Recall results below are still usable "
        "(both hosts run the same bge-m3 digest), but no INDEX BUILD may run until "
        "the pinned host is back.\n\n"
    ) if fallback else ""

    if result.get("gap") or not result.get("hits"):
        _emit(alert)   # a genuine gap is not an error; stay silent without noise

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
            alert
            + "## Possibly related memory (NO confident match)\n\n"
            "Nothing in the memory index cleared the confidence threshold for this "
            "message. The pointers below are the nearest material by similarity and "
            "may be entirely irrelevant. Do NOT treat them as context for this "
            "message unless you open one and confirm it is on topic.\n\n"
            # Capped at NEAR_MISS_MAX, shorter than the confident block. A
            # below-threshold result is noise by default, so its budget must be
            # smaller than the budget for a result that cleared the threshold.
            + "\n".join(lines[:NEAR_MISS_MAX])
        )
    _emit(
        alert
        + "## Memory relevant to this message\n\n"
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
