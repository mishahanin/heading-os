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
    except Exception:
        return


def read_recall_log():
    """Return all recall records (empty if none/unreadable).

    A corrupt line skips ITSELF and nothing else. The `json.loads` used to sit
    inside a `try` that wrapped the whole loop, so one torn line - `log_recall`
    appends while a reader reads, or a hand edit - aborted the iteration and
    every record after it was dropped without a word, leaving the deferred-memory
    metrics computed over a silently truncated log. MEASURED 2026-08-30 with
    three lines and an invalid middle one: one record came back instead of two.
    """
    path = _recall_log_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
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
