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
    """Return all recall records (empty if none/unreadable)."""
    path = _recall_log_path()
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    except Exception:
        return out
    return out
