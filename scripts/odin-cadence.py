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
                             sharing >= CLUSTER_MIN_SHARED tags, counted only where
                             they hold an episode logged after `.last-reflect`. A
                             cluster whose oldest unreviewed episode has waited
                             >= STALE_CLUSTER_DAYS escalates as "stale", so material
                             you keep passing over surfaces distinctly from material
                             that arrived yesterday.

Both markers are version-controlled in the data overlay, and that symmetry is
load-bearing rather than incidental. Until 2026-08-12 `.last-collect` was
gitignored while `.last-reflect` was tracked, so a second machine that pulled the
overlay read `last_collect: null` and counted every dated entry in the allowlist
as un-harvested. The markers hold one ISO date each and no content, so tracking
them costs nothing and keeps every clone answering the same question the same way.

The counted source set MUST equal /odin collect's allowlist (mode-catalog.md):
threads/business/*.md, crm/contacts/*.md (excluding .migration-backup/ + aggregated/),
outputs/operations/viraid/state.json -- same globs, same exclusions, same gate.
If mode-catalog's allowlist or detection regexes change, change this script too.

Needs no ollama (pure counting). Exit 0 on every COUNTING outcome --
including "nothing due", which is not a failure. An OSError from the
filesystem (permissions, a full disk, a dropped mount) still propagates
and exits non-zero: that is not a cadence verdict and must not be
reported as one. The claim used to be a flat "Exit 0 always", which the
notifier then read as "up to date" whenever this crashed.

Gap #5 enrichment: when reflect-ready clusters exist, `main()` also writes a
dated report (episode membership + shared tags per cluster) under
outputs/operations/odin-cadence/ -- never to the brain. This is the ONE
side effect this otherwise read-only script has; `compute()` itself still
performs no writes (existing tests call `compute()` directly and remain
read-only).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_data_root, get_default_tz  # noqa: E402
from scripts.utils.air_gap import is_denied  # noqa: E402
from scripts.utils import viraid_counterpart  # noqa: E402

# ============================================================
# Configuration
# ============================================================

DAYS_THRESHOLD = 7          # nudge if collect last ran >= this many days ago
DEFAULT_MIN_ENTRIES = 5     # nudge if un-harvested entries reach this count
STALE_CLUSTER_DAYS = 14     # unreviewed cluster material waiting this long escalates as "stale"

# Shared tags required to join two raw episodes into one reflect cluster.
#
# It was 1 until 2026-08-11, and 1 does not survive contact with a real brain: a
# single shared tag is a topic coincidence, and with union-find the coincidences
# chain. Measured over the 15 raw episodes standing that day, a threshold of 1
# produced ONE component of 12 — a set of deal episodes welded to a set of
# internal-tooling episodes through the workspace's own organisation tag, carried
# by 6 of the 15, and then through generic domain labels such as `go-to-market`,
# `ownership` and `governance`. Those two themes have nothing to do with each
# other, so "1 cluster" told the reflect pass nothing it could act on.
#
# The alternatives were measured on the same set. Dropping high-frequency tags
# instead is worse: the cutoff that breaks the weld also discards the slug of a
# recurring counterpart, and a counterpart who keeps reappearing is the most
# meaningful clustering signal there is. A threshold of 2 still merged the two
# themes (via `go-to-market`+`positioning` and `governance`+`tooling`). Three
# independently agreeing tags is the point where the components matched what a
# human would call a theme: two clusters of three, one per theme.
#
# The failure direction is deliberate. Under-clustering delays a reflect pass;
# over-clustering produces a nudge that is always on, which costs the signal.
CLUSTER_MIN_SHARED = 3

MARKER = "knowledge/odin-brain/.last-collect"  # leak-guard: ok (relative suffix rooted by caller)
REFLECT_MARKER = "knowledge/odin-brain/.last-reflect"  # leak-guard: ok (relative suffix rooted by caller)
VIRAID_STATE = "outputs/operations/viraid/state.json"  # leak-guard: ok (relative suffix rooted by caller)
EPISODES_DIR = "knowledge/odin-brain/episodes"  # leak-guard: ok (relative suffix rooted by caller)

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

def read_reflect_marker(root: Path):
    """Return the ISO date of the last CEO-confirmed reflect pass, or None.

    `.last-reflect` is written by `/odin reflect` on a maturation pass, exactly as
    `.last-collect` is written by `/odin collect`. Until 2026-08-11 nothing read
    it, so the cluster signal below could not tell reviewed material from new.
    """
    p = root / REFLECT_MARKER
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return None
    try:
        date.fromisoformat(raw[:10])
    except ValueError:
        return None
    return raw[:10]


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
        # A corrupt marker returned as TRUTHY made `compute` use the garbage
        # string as its lexicographic `since` floor, so `"2026-..." >= "garbage"`
        # was False for every entry and all un-harvested counts silently read 0
        # -- while the reason said "never collected" beside a marker file that
        # plainly exists. None here falls the caller through to EPOCH_FLOOR,
        # which counts everything: the safe direction for an unreadable marker.
        return None, None
    return raw, (datetime.now(get_default_tz()).date() - d).days


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
    """Parse a frontmatter list field in EITHER YAML form.

    Inline, `key: [a, b, c]`, or a block list::

        key:
          - a
          - b

    Only the inline form was matched. `odin-brain-health.py` reads both (it goes
    through `yaml.safe_load` and its docstring says so), so the two tools
    disagreed about the same file: a block-form episode came back with an EMPTY
    tag set, joined no cluster, and was still counted as scanned -- `compute()`
    appends nothing to its `skipped` list for this case, so the JSON asserted a
    complete pass it had not made.

    Latent for episodes today (90 of 90 use the inline form), not hypothetical
    for the brain: block-list frontmatter is already on disk under `sources/`.
    """
    m = re.search(rf"^{re.escape(key)}:\s*\[(.*)\]\s*$", block, re.MULTILINE)
    if m:
        inner = m.group(1).strip()
        parts = inner.split(",") if inner else []
    else:
        lines = block.splitlines()
        key_re = re.compile(rf"^{re.escape(key)}:[ \t]*$")
        parts = None
        for idx, line in enumerate(lines):
            if key_re.match(line):
                parts = []
                for nxt in lines[idx + 1:]:
                    stripped = nxt.lstrip()
                    if not stripped.startswith("- "):
                        break
                    parts.append(stripped[2:])
                break
        if parts is None:
            return []
    out = []
    for part in parts:
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


def _is_unreviewed(block: str, last_reflect: str | None) -> bool:
    """True when this episode was logged after the last CEO-confirmed reflect pass.

    No marker means nothing has ever been reviewed, so every episode counts. An
    episode whose date fields do not parse also counts: an undatable episode that
    went silent would be material lost from the signal, and the safe direction for
    a nudge is to surface it.
    """
    if not last_reflect:
        return True
    for key in ("created", "date"):
        raw = _fm_scalar(block, key)
        if raw:
            return raw[:10] > last_reflect
    return True


def analyze_reflect_clusters(root: Path, today: date | None = None) -> dict[str, Any]:
    """Connected components (size >= 2) of raw-status episodes sharing at least
    CLUSTER_MIN_SHARED tags, counted only where they hold material the CEO has not
    reviewed. Transitive A-B-C membership is intended. Returns:

      count            -- clusters carrying at least one UNREVIEWED episode
      stale_count      -- of those, the ones whose oldest unreviewed episode has
                          waited >= STALE_CLUSTER_DAYS
      oldest_age_days  -- longest such wait (None when none datable)
      ages             -- per-counted-cluster wait in days (None where undatable)
      clusters         -- per-counted-cluster {episodes, shared_tags, age_days}
                          detail, same order as `ages`

    **Unreviewed** means logged strictly after `.last-reflect`, the marker
    `/odin reflect` writes on a CEO-confirmed maturation pass. This is what makes
    the signal clearable: before 2026-08-11 the count was every cluster that
    existed, so a reflect pass could graduate ten episodes and the nudge stayed on
    exactly as before, which is a nudge that doing the work cannot answer. A
    cluster the CEO looked at and deliberately did not graduate now goes quiet
    until it is fed again.

    An episode logged the same day as the pass waits until tomorrow, because the
    marker carries a date and not a time. That is a one-day latency on a
    weekly-cadence signal, and the alternative (>=) restores the un-clearable
    behaviour for the whole day of the pass.

    A cluster's age is the wait of its OLDEST unreviewed episode: how long the
    thing you have not looked at has been sitting. The pre-2026-08-11 reading was
    days since the cluster's NEWEST episode, which measured the opposite thing and
    reset every time the cluster grew."""
    if today is None:
        today = datetime.now(get_default_tz()).date()
    base = root / EPISODES_DIR
    empty = {"count": 0, "stale_count": 0, "oldest_age_days": None, "ages": [], "clusters": []}
    if not base.is_dir():
        return empty

    last_reflect = read_reflect_marker(root)

    nodes = []  # list of (set_of_tags, age_days|None, filename, unreviewed)
    for p in sorted(base.glob("*.md")):
        block = _frontmatter_block(p.read_text(encoding="utf-8", errors="replace"))
        if _fm_scalar(block, "status") != "raw":
            continue
        tags = set(_fm_list(block, "entities")) | set(_fm_list(block, "keywords"))
        nodes.append((tags, _episode_age_days(block, today), p.name,
                      _is_unreviewed(block, last_reflect)))

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
            if len(nodes[i][0] & nodes[j][0]) >= CLUSTER_MIN_SHARED:
                union(i, j)

    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(find(i), []).append(i)

    ages: list[int | None] = []
    clusters: list[dict[str, Any]] = []
    for idxs in members.values():
        if len(idxs) < 2:
            continue
        unreviewed = [i for i in idxs if nodes[i][3]]
        if not unreviewed:
            continue  # the CEO has already seen every member; not a nudge
        waits = [nodes[i][1] for i in unreviewed if nodes[i][1] is not None]
        age = max(waits) if waits else None  # the oldest thing not yet looked at
        ages.append(age)
        # `shared_tags` KEEPS its union semantics: `tests/test_odin_cadence.py`
        # pins it ("shared_tags is the tag union"), so it is a deliberate,
        # consumed shape and not a slip. The defect the audit found is real but
        # it is in the NAME and the report label, which both claimed the tags
        # were common to every member -- with transitive A-B-C membership,
        # usually none of them are. `common_tags` carries the honest
        # intersection alongside it, and the report says which is which.
        tag_sets = [set(nodes[i][0]) for i in idxs]
        shared_tags = sorted(set().union(*tag_sets)) if tag_sets else []
        common_tags = sorted(set.intersection(*tag_sets)) if tag_sets else []
        clusters.append({
            "episodes": [nodes[i][2] for i in idxs],
            "shared_tags": shared_tags,
            "common_tags": common_tags,
            "age_days": age,
        })

    datable = [a for a in ages if a is not None]
    return {
        "count": len(ages),
        "stale_count": sum(1 for a in datable if a >= STALE_CLUSTER_DAYS),
        "oldest_age_days": max(datable) if datable else None,
        "ages": ages,
        "clusters": clusters,
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
        "last_reflect": read_reflect_marker(root),
        "days_since": days_since,
        "unharvested_total": total,
        "by_source": {"thread": thread_n, "crm": crm_n, "viraid": viraid_n},
        "reflect_clusters": clusters,
        "stale_clusters": ca["stale_count"],
        "oldest_cluster_age_days": ca["oldest_age_days"],
        "cluster_detail": ca["clusters"],
        "min_entries": min_entries,
        "nudge": nudge,
        "reasons": reasons,
        "skipped": skipped,
    }


def write_cadence_report(root: Path, result: dict[str, Any], today: date) -> Path | None:
    """Write a dated report of cluster membership when `cluster_detail` is
    non-empty; return the path, or None (writes nothing) on a clean week.

    Deliberately different from dream-shadow's always-write posture: this
    report PREVIEWS content (which episodes cluster, on what tags) rather
    than gating a mechanical defect, so an empty week writes nothing."""
    clusters = result.get("cluster_detail") or []
    if not clusters:
        return None
    report_dir = root / "outputs" / "operations" / "odin-cadence"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{today.isoformat()}_odin-cadence_report.md"
    lines = [
        "# Odin Cadence Report", "",
        f"**Generated:** {today.isoformat()}", "",
        f"## Reflect-Ready Clusters: {len(clusters)}", "",
    ]
    for c in clusters:
        tags = ", ".join(c["shared_tags"]) if c["shared_tags"] else "(none)"
        common = ", ".join(c.get("common_tags") or []) or "(none in common)"
        age = f"{c['age_days']}d" if c["age_days"] is not None else "unknown age"
        # "oldest unreviewed", not "newest". `age_days` is max(waits) -- the wait
        # of the OLDEST thing not yet looked at -- and the compute docstring
        # spends a paragraph on why, condemning the newest reading as "measured
        # the opposite thing". The report label reintroduced it for the CEO.
        lines.append(f"- Episodes: {', '.join(c['episodes'])} "
                     f"| tags across the cluster: {tags} "
                     f"| in every member: {common} "
                     f"| oldest unreviewed {age}")
    lines.append("")
    text = "\n".join(lines)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def suggestion_line(r: dict[str, Any], report_rel: str | None = None) -> str:
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
    # A days-only nudge (no new entries, no clusters) left `tail` empty and this
    # line, sent verbatim to Telegram, ended "Run .".
    line = f"Odin cadence: {when} — {detail}."
    if tail:
        line += f" Run {' or '.join(tail)}."
    if report_rel:
        line += f" (report: {report_rel})"
    return line


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
    today = datetime.now(get_default_tz()).date()
    r = compute(root, args.min_entries)
    report_path = write_cadence_report(root, r, today)
    report_rel = str(report_path.relative_to(root)) if report_path else None

    if args.json:
        out = dict(r)
        out["report_path"] = report_rel
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.quiet and not r["nudge"]:
        return 0
    print(suggestion_line(r, report_rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
