#!/usr/bin/env python3
"""Append-only count of refusals — one line per refused path, by any guard.

The instrumented set is the eight PreToolUse checks in
`.claude/hooks/_dispatch.py`, the secret scanner, the leak guard's two checks,
the content guard, and the push-time routing, content and tracked-secret walls.
Until this module existed, none of them counted anything. Without a count, "this
guard is a successful deterrent" and "this guard is pointless ceremony" produce
the SAME observation, so no guard can be judged and none can honestly be removed.
See `docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A1.

The unit is one line per refused PATH, which is not one line per refused ACTION
for every guard: a denied tool call is one path, a commit refused by the content
guard over six offending lines is six. Both units answer the fired-versus-never-
fired question A3 asks, and per-path is the one that lets `scripts/denials.py
--detail` name every offending location. Read the per-mechanism totals as that
discrimination, not as a like-for-like frequency ranking.

Two properties carry the security weight:

1. **A record never carries the refused content.** Both the reason and the path
   pass through `redact()` before serialization. A guard writes its own reason
   text and a future guard may interpolate the thing it caught; a counter that
   copied it verbatim would write credentials to disk on every catch.
2. **A logging failure never weakens a guard.** This is telemetry; the refusal is
   the guarantee. Every entry point returns a bool and raises nothing, so a
   caller in a blocking gate cannot be taken down by it. Failures are reported on
   stderr rather than swallowed, per the workspace exception rule.

The file lives under the gitignored `.logs/` because records name real paths and
the engine repository is public. `WORKSPACE_LOG_DIR` relocates it (used by tests;
it is a pre-existing seam in `paths.log_dir`, not a test-only hook added here).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.paths import log_dir
from scripts.utils.secret_patterns import redact

# Long enough to keep a guard's reason useful, short enough that a pathological
# payload cannot turn one refusal into a megabyte of log.
MAX_FIELD = 512

# Names the invoking context when one enforcement point drives another. The push
# wall runs the scanner as a subprocess: without this, the same refusal would be
# counted twice under two mechanisms and the denominator would be wrong on day
# one — the defect A8 describes in our own false-positive instrument.
CONTEXT_ENV = "HEADING_OS_DENIAL_CONTEXT"


def printable(value) -> str:
    """A record field safe to write to a terminal.

    A record's `path` is a denied tool call's `file_path`, which a prompt
    injection can shape, and `redact()` substitutes credential patterns without
    touching control bytes. Two things go wrong if a reader prints one raw. An
    ESC sequence replays into the operator's terminal, making the instrument a
    delivery mechanism. And an embedded newline FORGES a row: measured
    2026-08-02 against `scripts/gate-yield.py`, a crafted denial reason produced
    a line reading "FAKE  approve  999 catch(es)" that was indistinguishable
    from a real one, so the report could be made to lie about the numbers it
    exists to report.

    Lives here rather than in one reader because `scripts/denials.py` and
    `scripts/utils/gate_yield.py` both need it, and a guard repaired on one
    sibling and not the other is a pattern this repository has already paid for
    more than once.
    """
    text = "" if value is None else str(value)
    return "".join(ch if (ch.isprintable() or ch == " ") else repr(ch)[1:-1]
                   for ch in text)


def denial_log_path() -> Path:
    """Absolute path of the append-only denial log."""
    return log_dir("denials") / "denials.jsonl"


def _clean(value) -> str:
    """Redact, then bound. Applied to every attacker-influenced field."""
    if value is None:
        return ""
    return redact(str(value))[:MAX_FIELD]


def log_denial(*, mechanism: str, action: str, path=None, reason: str = "") -> bool:
    """Append one refusal record. Returns True on write, False on failure.

    Never raises: callers are blocking guards, and a telemetry fault must not
    become a policy fault.
    """
    try:
        record = {
            "ts": time.time(),
            "mechanism": str(mechanism)[:MAX_FIELD],
            "action": str(action)[:MAX_FIELD],
            "path": _clean(path) if path is not None else None,
            "reason": _clean(reason),
            "context": (os.environ.get(CONTEXT_ENV) or None),
        }
        target = denial_log_path()
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        # Reported, not swallowed. stderr rather than a logger because the
        # loudest caller is a PreToolUse hook whose stdout is a protocol channel.
        print(f"[denial-log] could not record a refusal: {exc}", file=sys.stderr)
        return False


def read_denials(path: Path = None) -> list:
    """Every readable record. A corrupt line is skipped, not fatal: a truncated
    write must not cost the rest of the history."""
    try:
        text = Path(path or denial_log_path()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def summarize(records) -> dict:
    """mechanism -> count."""
    counts = {}
    for record in records:
        name = record.get("mechanism") or "unknown"
        counts[name] = counts.get(name, 0) + 1
    return counts
