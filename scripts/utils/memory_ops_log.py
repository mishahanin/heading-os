#!/usr/bin/env python3
"""Append-only log of recall queries for deferred memory metrics.

One JSON object per line at log_dir("memory-ops")/recall.jsonl. Local-only (never
sent anywhere), so it writes in the default posture; under SENSITIVE_MODE the query
TEXT is redacted while numeric metrics are kept. Never raises to its caller.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.paths import log_dir
from scripts.utils.sensitive import is_sensitive


def _recall_log_path() -> Path:
    return log_dir("memory-ops") / "recall.jsonl"


def log_recall(*, query_snippet, collection, layer, top_score, gap, n_hits,
               threshold, latency_ms, hit_paths=None):
    """Append one recall record. Local-only; redacts query text under SENSITIVE_MODE;
    keeps numeric metrics. Never raises."""
    try:
        snippet = None if is_sensitive() else (query_snippet or "")[:200]
        payload = {
            "ts": time.time(),
            "query_snippet": snippet,
            "collection": collection,
            "layer": layer,
            "top_score": top_score,
            "gap": bool(gap),
            "n_hits": int(n_hits),
            "threshold": threshold,
            "latency_ms": latency_ms,
            "hit_paths": list(hit_paths or []),
        }
        path = _recall_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception as exc:  # noqa: BLE001 - boundary; recall must still answer
        # Reported, THEN swallowed. `cmd_query._emit` wraps this call in its own
        # `except Exception` and writes "recall ops-log write failed" there,
        # under a comment saying a silent return meant a broken ops log was
        # noticed only when somebody went to read it and found nothing. That
        # handler cannot fire: this function is the one that catches, and it
        # never re-raises, so the fix landed one frame above the failure it was
        # written for. MEASURED 2026-09-01 by making the append raise -- the
        # caller's message did not appear, and neither did any other.
        # Reporting here keeps the "never raises" contract the module docstring
        # states while ending the silence, and matches `read_recall_log` below,
        # which already names an unreadable log on stderr.
        print(f"memory-ops: recall log write failed ({exc}); this recall was "
              f"NOT counted in the deferred memory metrics", file=sys.stderr)
        return


def read_recall_log():
    """Return all recall records (empty if none/unreadable).

    A corrupt line skips ITSELF and nothing else. The `json.loads` used to sit
    inside a `try` that wrapped the whole loop, so one torn line - `log_recall`
    appends while a reader reads, or a hand edit - aborted the iteration and
    every record after it was dropped without a word, leaving the deferred-memory
    metrics computed over a silently truncated log. MEASURED 2026-08-30 with
    three lines and an invalid middle one: one record came back instead of two.

    A file that will not DECODE costs the whole log, not one line, because the
    decode happens before there are any lines to skip. That case is named on
    stderr: `[]` from this function is read downstream as "no recalls have
    happened", and a metrics denominator of zero that nothing reported is worse
    than a crash.
    """
    path = _recall_log_path()
    try:
        raw = path.read_text(encoding="utf-8")
    # An ABSENT log is silent, exactly as it was before this widening: no
    # recall has been logged yet is the ordinary first-run state, and a
    # reader that cannot tell absent from corrupt reports both or neither.
    except FileNotFoundError:
        return []
    # `UnicodeDecodeError` is a `ValueError`, not an `OSError`, and it is
    # raised inside `read_text` -- before the per-line `except ValueError`
    # below, which cannot help because there are no lines yet. MEASURED
    # 2026-09-01 against a log holding one 0xe9 byte: `UnicodeDecodeError:
    # invalid continuation byte` raised out of a function whose docstring
    # opens "empty if none/unreadable" and whose module docstring says it
    # "never raises to its caller".
    except (OSError, UnicodeDecodeError) as exc:
        print(f"memory-ops: recall log at {path} is unreadable ({exc}); "
              f"reporting ZERO recall records, which is not the same as none "
              f"having been logged", file=sys.stderr)
        return []
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # this line only; the rest of the log still counts
    return out
