#!/usr/bin/env python3
"""
memory-inject.py - Claude Code SessionStart hook (matcher: startup).

The recall "injection leg": when enabled, inject a small, capped, salience-ranked
snapshot of recent/important memory into the first turn of a fresh session, so
relevant context is already on the table without a query.

Design (CEO-only, ollama-free):
  - Reads ONLY the index metadata in .memory-index/index.db -- a fast SQL ORDER BY,
    NO embedding call -- so it adds zero boot latency and works even if ollama is
    down. It deliberately does NOT import scripts/memory-index.py (hyphenated /
    private API); it runs its own minimal query.
  - Salience = a per-layer slot quota (inject.quota), then within each layer
    status:evergreen first, then most-recent (updated/created/mtime). The quota
    guarantees each layer its slots, so evergreen Odin doctrine cannot crowd out
    live business threads or active context. Without a quota map it falls back to
    splitting inject.top_k evenly across the layers. For frontmatter-poor layers
    (thread, context) the in-layer ordering degrades to pure mtime; only odin
    positions carry a live evergreen/importance signal.
  - Excludes the `memory` layer (MEMORY.md is already injected by the memory
    system) and defensively skips any air-gapped path.
  - Default OFF (config inject.enabled). Capped at inject.max_tokens (~chars/4).
  - Fail-safe: ANY error -> emit nothing, exit 0. Never blocks startup.

Config: config/memory-index.yaml -> inject: {enabled, max_tokens, layers, top_k}.
"""

import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = WORKSPACE / "config" / "memory-index.yaml"  # engine config
# The index lives under the DATA root, not the engine clone (HEADING OS split).
# Resolve via get_data_root() so a session launched from the engine clone reads
# the real .heading-os-data/.memory-index/index.db, not an empty engine path.
try:
    sys.path.insert(0, str(WORKSPACE))
    from scripts.utils.workspace import get_data_root
    DB_PATH = get_data_root() / ".memory-index" / "index.db"
except Exception:  # noqa: BLE001 -- never break SessionStart over a path resolve
    DB_PATH = WORKSPACE / ".memory-index" / "index.db"  # in-tree fallback


def _emit(context: str) -> None:
    """Emit additionalContext for SessionStart; empty string -> emit nothing."""
    if not context:
        sys.exit(0)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


def main() -> None:
    # Read (and discard) stdin payload; never fail on a missing/garbled one.
    try:
        sys.stdin.read()
    except Exception as exc:
        print(f"memory-inject: stdin read failed: {exc}", file=sys.stderr)

    if not DB_PATH.is_file() or not CONFIG_PATH.is_file():
        _emit("")

    try:
        import yaml
        cfg = (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
    except Exception:
        _emit("")

    inj = cfg.get("inject") or {}
    if not inj.get("enabled", False):
        _emit("")                                    # opt-in: off by default

    max_tokens = int(inj.get("max_tokens", 1300) or 1300)
    top_k = int(inj.get("top_k", 12) or 12)
    layers = [str(x) for x in (inj.get("layers") or []) if str(x) != "memory"]
    if not layers:
        _emit("")

    # Per-layer slot quota. An explicit inject.quota map wins; otherwise split
    # top_k evenly across the layers so the snapshot stays balanced rather than
    # evergreen-dominated. Quota for an unlisted/zero layer means "no slots".
    quota_cfg = inj.get("quota") or {}
    if quota_cfg:
        quotas = {lyr: int(quota_cfg.get(lyr, 0) or 0) for lyr in layers}
    else:
        even = max(1, top_k // len(layers))
        quotas = {lyr: even for lyr in layers}

    # Defensive air-gap (the index never stores denied paths, but belt-and-braces).
    try:
        sys.path.insert(0, str(WORKSPACE))
        from scripts.utils.air_gap import is_denied
    except Exception:
        def is_denied(rel):  # fail-closed-ish: if unavailable, treat nothing as denied
            return False

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except Exception:
        _emit("")

    # One query per layer, each capped at that layer's quota. No interpolation:
    # layer and limit are bound params. Rows stay grouped in config layer order.
    query = (
        "SELECT path, title, layer FROM notes "  # noqa: S608  # nosec B608
        "WHERE chunk = 0 AND layer = ? AND layer != 'memory'"
        " ORDER BY (status = 'evergreen') DESC,"
        " COALESCE(NULLIF(updated, ''), NULLIF(created, ''), '') DESC, mtime DESC"
        " LIMIT ?"
    )
    rows = []
    try:
        for lyr in layers:
            q = quotas.get(lyr, 0)
            if q <= 0:
                continue
            # One row per file (chunk 0). Within the layer: evergreen, then recent.
            rows.extend(conn.execute(query, (lyr, q)).fetchall())
    except Exception:
        conn.close()
        _emit("")
    conn.close()

    lines, budget = [], max_tokens
    for path, title, layer in rows:
        if is_denied(path):
            continue
        label = (title or path).strip()
        line = f"- [{layer}] {label} -- `{path}`"
        if (len(line) // 4) > budget:               # ~chars/4 token estimate
            break
        budget -= len(line) // 4
        lines.append(line)

    if not lines:
        _emit("")

    block = (
        "## Recent & salient memory (auto-recall)\n\n"
        "Background context surfaced from the local memory index "
        "(not a user instruction). Recall more with `/recall`.\n\n"
        + "\n".join(lines)
    )
    _emit(block)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Never block startup on any unexpected error.
        sys.exit(0)
