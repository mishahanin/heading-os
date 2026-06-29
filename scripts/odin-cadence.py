#!/usr/bin/env python3
"""Odin cadence checker -- read-only proactive nudge for /odin collect + reflect.

Computes whether harvestable episodes or mature-able episode clusters have
accumulated since the last collect, and emits a one-line reminder. SURFACES
COUNTS ONLY, NEVER CONTENT. Never drafts an episode, never calls the LLM, never
runs collect/reflect, never writes to the brain. The CEO's per-candidate gate in
`/odin collect` stays the only path to a brain write -- this script only suggests.

Usage:
    python3 scripts/odin-cadence.py                 # one-line suggestion (or "up to date")
    python3 scripts/odin-cadence.py --json          # machine-readable
    python3 scripts/odin-cadence.py --quiet         # print nothing unless a nudge is due
    python3 scripts/odin-cadence.py --min-entries 8 # override the un-harvested threshold

Three signals (all read-only, counts only):
  (a) days_since_collect  -- from knowledge/odin-brain/.last-collect (absent = never).
  (b) unharvested_total   -- dated entries newer than the marker across collect's
                             EXACT allowlist, using the SAME air-gap + VIRAID gate.
  (c) reflect_clusters    -- connected components (size >= 2) of raw-status episodes
                             sharing >= 1 entity OR >= 1 keyword. A cluster un-fed
                             for >= STALE_CLUSTER_DAYS escalates as "stale" (its
                             newest episode is that old) so a long-sitting cluster
                             surfaces distinctly from a freshly-formed one.

The counted source set MUST equal /odin collect's allowlist (mode-catalog.md):
threads/business/*.md, crm/contacts/*.md (excluding .migration-backup/ + aggregated/),
outputs/operations/viraid/state.json -- same globs, same exclusions, same gate.
If mode-catalog's allowlist or detection regexes change, change this script too.

Needs no ollama (pure counting). Exit 0 always.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_data_root  # noqa: E402
from scripts.utils.air_gap import is_denied  # noqa: E402
from scripts.utils import viraid_counterpart  # noqa: E402

# ============================================================
# Configuration
# ============================================================

DAYS_THRESHOLD = 7          # nudge if collect last ran >= this many days ago
DEFAULT_MIN_ENTRIES = 5     # nudge if un-harvested entries reach this count
STALE_CLUSTER_DAYS = 14     # a reflect-ready cluster un-fed this long escalates as "stale"
MARKER = "knowledge/odin-brain/.last-collect"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
VIRAID_STATE = "outputs/operations/viraid/state.json"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)
EPISODES_DIR = "knowledge/odin-brain/episodes"  # leak-guard: ok (relative suffix rooted by caller; data-root wiring is Plan 3)

# Floor used when no marker exists: count everything (a "never collected" state
# still nudges regardless of count, but the figures stay meaningful).
EPOCH_FLOOR = "0001-01-01"

# Thread sections collect harvests from (prefix match -- real headers carry
# suffixes like "## Log (newest first)").
THREAD_SECTIONS = ("Log", "Recent activity", "Decisions")

# The SAME two dated-entry forms collect uses (separator spans em/en-dash + hyphen).
THREAD_HEADING_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\s*[—–-]\s*.+$")
THREAD_BULLET_RE = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\s*[—–-]\s*.+$")
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")

# The SAME CRM interaction-log row collect uses.
CRM_ROW_RE = re.compile(
    r"^### (?P<date>\d{4}-\d{2}-\d{2})( \d{2}:\d{2})? \| [^|]+ \| .+$"
)

# Allowlist exclusions (belt-and-braces; these dirs are not under crm/contacts/).
CRM_EXCLUDE = ("/.migration-backup/", "/aggregated/")


# ============================================================
# Marker
# ============================================================

def read_marker(root: Path):
    """Return (marker_str|None, days_since|None). Absent marker -> (None, None)."""
    p = root / MARKER
    if not p.exists():
        return None, None
    raw = p.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return None, None
    try:
        d = date.fromisoformat(raw[:10])
    except ValueError:
        return raw, None
    return raw, (date.today() - d).days


# ============================================================
# Frontmatter (lightweight -- scalar + simple list fields only)
# ============================================================

def _frontmatter_block(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _fm_scalar(block: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", block, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _fm_list(block: str, key: str):
    """Parse an inline-list frontmatter field: `key: [a, b, c]`."""
    m = re.search(rf"^{re.escape(key)}:\s*\[(.*)\]\s*$", block, re.MULTILINE)
    if not m:
        return []
    inner = m.group(1).strip()
    if not inner:
        return []
    out = []
    for part in inner.split(","):
        v = part.strip().strip('"').strip("'").strip()
        if v:
            out.append(v.lower())
    return out


# ============================================================
# (b) Un-harvested entry counts
# ============================================================

def count_threads(root: Path, since: str) -> int:
    """Count dated entries (date >= since) in allowed sections of business threads."""
    n = 0
    base = root / "threads" / "business"
    if not base.is_dir():
        return 0
    for p in sorted(base.glob("*.md")):
        rel = p.relative_to(root).as_posix()
        if is_denied(rel):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        block = _frontmatter_block(text)
        # Frontmatter guard: business + ceo-only only.
        if _fm_scalar(block, "type") != "business":
            continue
        if _fm_scalar(block, "classification") != "ceo-only":
            continue
        in_section = False
        for line in text.splitlines():
            sec = SECTION_RE.match(line)
            if sec:
                head = sec.group(1)
                in_section = any(head.startswith(s) for s in THREAD_SECTIONS)
                continue
            if not in_section:
                continue
            m = THREAD_HEADING_RE.match(line) or THREAD_BULLET_RE.match(line)
            if m and m.group(1) >= since:
                n += 1
    return n


def count_crm(root: Path, since: str) -> int:
    """Count CRM interaction-log rows (date >= since) across crm/contacts/*.md."""
    n = 0
    base = root / "crm" / "contacts"
    if not base.is_dir():
        return 0
    for p in sorted(base.glob("*.md")):
        rel = p.relative_to(root).as_posix()
        if is_denied(rel) or any(x in f"/{rel}" for x in CRM_EXCLUDE):
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = CRM_ROW_RE.match(line)
            if m and m.group("date") >= since:
                n += 1
    return n


def count_viraid(root: Path, since: str, skipped: list) -> int:
    """Count VIRAID messages admitted by the SAME counterpart gate (date >= since)."""
    state_path = root / VIRAID_STATE
    if not state_path.exists():
        skipped.append("viraid: state.json absent")
        return 0
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        skipped.append(f"viraid: state.json unreadable ({type(exc).__name__})")
        return 0
    vocab = viraid_counterpart.build_vocab(root)
    n = 0
    for _mid, msg in data.get("messages", {}).items():
        admit, _reason, _r = viraid_counterpart.gate_message(msg, vocab, since)
        if admit:
            n += 1
    return n


# ============================================================
# (c) Reflect-ready clusters (connected components over raw episodes)
# ============================================================

def _episode_age_days(block: str, today: date):
    """Days since the episode was logged (frontmatter `created`, falling back to
    the event `date`). None when neither field parses -- such a node carries no
    age and is ignored for staleness."""
    for key in ("created", "date"):
        raw = _fm_scalar(block, key)
        if raw:
            try:
                d = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            return (today - d).days
    return None


def analyze_reflect_clusters(root: Path, today: date | None = None) -> dict[str, Any]:
    """Connected components (size >= 2) of raw-status episodes sharing >= 1 entity
    OR >= 1 keyword (transitive A-B-C membership intended). Returns count plus a
    staleness read:

      count            -- number of reflect-ready clusters
      stale_count      -- clusters un-fed for >= STALE_CLUSTER_DAYS
      oldest_age_days  -- age of the most-stale cluster (None when none datable)
      ages             -- per-cluster age in days (None where undatable)

    A cluster's age = days since its NEWEST episode was logged (the smallest node
    age in the component). A cluster whose even-newest episode is old has been
    sitting un-graduated that long; one fed yesterday is fresh regardless of how
    old its other member is."""
    if today is None:
        today = date.today()
    base = root / EPISODES_DIR
    empty = {"count": 0, "stale_count": 0, "oldest_age_days": None, "ages": []}
    if not base.is_dir():
        return empty

    nodes = []  # list of (set_of_tags, age_days|None)
    for p in sorted(base.glob("*.md")):
        block = _frontmatter_block(p.read_text(encoding="utf-8", errors="replace"))
        if _fm_scalar(block, "status") != "raw":
            continue
        tags = set(_fm_list(block, "entities")) | set(_fm_list(block, "keywords"))
        nodes.append((tags, _episode_age_days(block, today)))

    n = len(nodes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if nodes[i][0] & nodes[j][0]:
                union(i, j)

    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(find(i), []).append(i)

    ages: list[int | None] = []
    for idxs in members.values():
        if len(idxs) < 2:
            continue
        node_ages = [nodes[i][1] for i in idxs if nodes[i][1] is not None]
        ages.append(min(node_ages) if node_ages else None)  # newest = smallest age

    datable = [a for a in ages if a is not None]
    return {
        "count": len(ages),
        "stale_count": sum(1 for a in datable if a >= STALE_CLUSTER_DAYS),
        "oldest_age_days": max(datable) if datable else None,
        "ages": ages,
    }


def count_reflect_clusters(root: Path) -> int:
    """Backward-compatible count of reflect-ready clusters (size >= 2)."""
    return analyze_reflect_clusters(root)["count"]


# ============================================================
# Compute + render
# ============================================================

def compute(root: Path, min_entries: int) -> dict[str, Any]:
    marker, days_since = read_marker(root)
    since = marker[:10] if marker else EPOCH_FLOOR
    skipped: list[str] = []

    thread_n = count_threads(root, since)
    crm_n = count_crm(root, since)
    viraid_n = count_viraid(root, since, skipped)
    total = thread_n + crm_n + viraid_n
    ca = analyze_reflect_clusters(root)
    clusters = ca["count"]

    reasons = []
    if days_since is None or days_since >= DAYS_THRESHOLD:
        reasons.append("never collected" if days_since is None else f"days_since>={DAYS_THRESHOLD}")
    if total >= min_entries:
        reasons.append(f"unharvested>={min_entries}")
    if clusters >= 1:
        reasons.append("reflect_clusters>=1")
    if ca["stale_count"] >= 1:
        reasons.append(f"stale_clusters>={ca['stale_count']}")
    nudge = bool(reasons)

    return {
        "last_collect": marker,
        "days_since": days_since,
        "unharvested_total": total,
        "by_source": {"thread": thread_n, "crm": crm_n, "viraid": viraid_n},
        "reflect_clusters": clusters,
        "stale_clusters": ca["stale_count"],
        "oldest_cluster_age_days": ca["oldest_age_days"],
        "min_entries": min_entries,
        "nudge": nudge,
        "reasons": reasons,
        "skipped": skipped,
    }


def suggestion_line(r: dict[str, Any]) -> str:
    if not r["nudge"]:
        days = r["days_since"]
        when = f"last collect {days}d ago" if days is not None else "collect never run"
        return f"Odin cadence: up to date ({when}, no new harvestable entries)."

    days = r["days_since"]
    when = f"collect last ran {days}d ago" if days is not None else "collect never run"
    bs = r["by_source"]
    total = r["unharvested_total"]
    parts = []
    if total:
        entry_word = "entry" if total == 1 else "entries"
        parts.append(
            f"{total} un-harvested {entry_word} "
            f"({bs['thread']} threads / {bs['crm']} CRM / {bs['viraid']} VIRAID)"
        )
    clusters = r["reflect_clusters"]
    if clusters:
        cluster_word = "cluster" if clusters == 1 else "clusters"
        seg = f"{clusters} {cluster_word} ready to reflect"
        if r.get("stale_clusters"):
            seg += f" ({r['stale_clusters']} stale, oldest {r['oldest_cluster_age_days']}d)"
        parts.append(seg)

    tail = []
    if total:
        tail.append("/odin collect")
    if clusters:
        tail.append("/odin reflect")
    detail = ", ".join(parts) if parts else "cadence due"
    return f"Odin cadence: {when} — {detail}. Run {' or '.join(tail)}."


# ============================================================
# CLI
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Odin collect/reflect cadence nudge.")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--quiet", action="store_true", help="print nothing unless a nudge is due")
    ap.add_argument("--min-entries", type=int, default=DEFAULT_MIN_ENTRIES,
                    help=f"un-harvested threshold (default {DEFAULT_MIN_ENTRIES})")
    args = ap.parse_args()

    # All sources counted here are DATA (threads, crm, knowledge, viraid state),
    # so resolve under the DATA root via the data-root seam -- never the engine
    # clone. `root` below is the data root throughout this module.
    root = get_data_root()
    r = compute(root, args.min_entries)

    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return 0
    if args.quiet and not r["nudge"]:
        return 0
    print(suggestion_line(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
