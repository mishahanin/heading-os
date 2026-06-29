#!/usr/bin/env python3
"""Regression tests for the Odin cadence checker (scripts/odin-cadence.py).

Synthetic fixtures in temp dirs, plain asserts, standalone-runnable. Anchored to
the invariants the cadence nudge must never break:
  - read-only (no file ever created or modified by a run)
  - air-gap (personal / _secure never counted; business is)
  - allowlist scope bound to collect's allowlist
  - counts, never content (no fixture body text in any output)
  - reflect clustering = connected components (entity OR keyword, transitive)
  - threshold boundaries flip the nudge at exactly the right point
"""

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "odin_cadence", ROOT / "scripts" / "odin-cadence.py"
)
oc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oc)

from scripts.utils.air_gap import is_denied

SENTINEL = "ZZSENTINELZZ-do-not-leak-9173"


def _check(name, cond):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    return bool(cond)


def _write(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _biz_thread(name, entries, *, type_="business", classification="ceo-only"):
    rows = "\n".join(f"### {d} — {txt}" for d, txt in entries)
    return f"""---
id: {name}
title: {name}
status: active
type: {type_}
classification: {classification}
---

# {name}

## Log (newest first)

{rows}
"""


def _crm_contact(name, entries, *, rel_type="partner", company="Acme"):
    rows = "\n".join(f"### {d} | Note | {txt}" for d, txt in entries)
    return f"""---
name: {name}
relationship_type: {rel_type}
pipeline_company: {company}
---

# {name}

## Interaction Log

{rows}
"""


def _episode(eid, status, entities, keywords, created=None):
    ent = ", ".join(entities)
    kw = ", ".join(keywords)
    created_line = f"created: {created}\n" if created else ""
    return f"""---
id: "{eid}"
type: episode
date: 2026-05-21
{created_line}entities: [{ent}]
keywords: [{kw}]
status: {status}
---

# {eid}

## What happened

{SENTINEL} episode body text.
"""


def _snapshot(root: Path):
    return {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


def main():
    ok = True
    today = date.today()
    iso = lambda d: d.isoformat()  # noqa: E731

    # ============================================================
    # air_gap predicate is the shared one the script relies on
    # ============================================================
    ok &= _check("is_denied personal segment", is_denied("threads/personal/x.md"))
    ok &= _check("is_denied _secure prefix", is_denied("_secure/y.md"))
    ok &= _check("is_denied business false", not is_denied("threads/business/z.md"))

    # ============================================================
    # Full fixture: threads + CRM + episodes + air-gap + allowlist
    # ============================================================
    root = Path(tempfile.mkdtemp(prefix="odin-cadence-"))
    marker = iso(today)  # collected today -> days_since 0
    _write(root, "knowledge/odin-brain/.last-collect", marker + "\n")

    # business thread: 3 dated entries >= marker (all today), 1 stale (well before)
    _write(root, "threads/business/biz1.md", _biz_thread(
        "biz1",
        [(iso(today), f"deal note {SENTINEL}"),
         (iso(today), f"call note {SENTINEL}"),
         (iso(today), f"decision {SENTINEL}"),
         ("2020-01-01", f"ancient {SENTINEL}")],
    ))
    # frontmatter guard: type personal -> skipped despite living under business/
    _write(root, "threads/business/notbiz.md", _biz_thread(
        "notbiz", [(iso(today), f"should not count {SENTINEL}")], type_="personal"))
    # frontmatter guard: classification not ceo-only -> skipped
    _write(root, "threads/business/shared.md", _biz_thread(
        "shared", [(iso(today), f"shared no count {SENTINEL}")], classification="corporate"))
    # allowlist scope: a personal-segment thread is never globbed/counted
    _write(root, "threads/personal/p1.md", _biz_thread(
        "p1", [(iso(today), f"personal no count {SENTINEL}")]))

    # CRM: 2 rows >= marker for an external contact; exclusions present but uncounted
    _write(root, "crm/contacts/acme-corp.md", _crm_contact(
        "Acme Corporation",
        [(iso(today), f"meeting {SENTINEL}"), (iso(today), f"follow up {SENTINEL}"),
         ("2019-01-01", f"old row {SENTINEL}")]))
    _write(root, "crm/.migration-backup/old.md", _crm_contact(
        "Backup Co", [(iso(today), f"excluded {SENTINEL}")]))
    _write(root, "crm/aggregated/agg.md", _crm_contact(
        "Agg Co", [(iso(today), f"excluded {SENTINEL}")]))

    # VIRAID: one admitted (external Acme) + one tribe-only/no-counterpart (dropped)
    _write(root, "outputs/operations/viraid/state.json", json.dumps({"messages": {
        "1": {"disposition": "task", "date": iso(today),
              "text": f"Call with Acme about the deal {SENTINEL}",
              "action_summary": "Acme follow-up"},
        "2": {"disposition": "task", "date": iso(today),
              "text": f"internal housekeeping {SENTINEL}",
              "action_summary": "no counterpart"},
    }}))

    # episodes: a 2-node raw cluster sharing an entity (+ keyword)
    _write(root, "knowledge/odin-brain/episodes/e1.md",
           _episode("e1", "raw", ["acme", "bob"], ["mnda"]))
    _write(root, "knowledge/odin-brain/episodes/e2.md",
           _episode("e2", "raw", ["acme", "carol"], ["demo"]))

    before = _snapshot(root)
    r = oc.compute(root, min_entries=5)
    after = _snapshot(root)

    # --- read-only invariant ---
    ok &= _check("read-only: no files added/removed", set(before) == set(after))
    ok &= _check("read-only: no mtimes changed", before == after)

    # --- counts (allowlist + air-gap + frontmatter guard) ---
    ok &= _check(f"threads counted == 3 (got {r['by_source']['thread']})",
                 r["by_source"]["thread"] == 3)
    ok &= _check(f"crm counted == 2 (got {r['by_source']['crm']})",
                 r["by_source"]["crm"] == 2)
    ok &= _check(f"viraid counted == 1 (got {r['by_source']['viraid']})",
                 r["by_source"]["viraid"] == 1)
    ok &= _check(f"unharvested_total == 6 (got {r['unharvested_total']})",
                 r["unharvested_total"] == 6)
    ok &= _check(f"reflect_clusters == 1 (got {r['reflect_clusters']})",
                 r["reflect_clusters"] == 1)

    # --- counts, NOT content: sentinel must not appear in any output ---
    line = oc.suggestion_line(r)
    blob = line + "\n" + json.dumps(r, default=str)
    ok &= _check("sentinel absent from suggestion line + json", SENTINEL not in blob)

    # --- --json shape: all documented keys present and typed ---
    for key, typ in [("last_collect", str), ("days_since", int), ("unharvested_total", int),
                     ("by_source", dict), ("reflect_clusters", int), ("min_entries", int),
                     ("nudge", bool), ("reasons", list), ("skipped", list)]:
        ok &= _check(f"json key {key}:{typ.__name__}", isinstance(r[key], typ))
    ok &= _check("by_source has thread/crm/viraid",
                 set(r["by_source"]) == {"thread", "crm", "viraid"})

    # ============================================================
    # Threshold boundaries (each flips nudge independently)
    # ============================================================
    def fresh_marker(days_ago):
        rr = Path(tempfile.mkdtemp(prefix="odin-cad-thr-"))
        _write(rr, "knowledge/odin-brain/.last-collect", iso(today - timedelta(days=days_ago)))
        return rr

    # days_since 6 vs 7 (no entries, no clusters)
    r6 = oc.compute(fresh_marker(6), min_entries=5)
    ok &= _check("days_since 6 -> no nudge", not r6["nudge"])
    r7 = oc.compute(fresh_marker(7), min_entries=5)
    ok &= _check("days_since 7 -> nudge", r7["nudge"] and "days_since>=7" in r7["reasons"])

    # unharvested min-1 vs min (marker today so days_since 0, no clusters)
    def root_with_entries(n_entries):
        rr = Path(tempfile.mkdtemp(prefix="odin-cad-ent-"))
        _write(rr, "knowledge/odin-brain/.last-collect", iso(today))
        _write(rr, "threads/business/b.md", _biz_thread(
            "b", [(iso(today), f"e{i}") for i in range(n_entries)]))
        return rr

    r4 = oc.compute(root_with_entries(4), min_entries=5)
    ok &= _check("4 entries, min 5 -> no nudge", not r4["nudge"])
    r5 = oc.compute(root_with_entries(5), min_entries=5)
    ok &= _check("5 entries, min 5 -> nudge", r5["nudge"] and "unharvested>=5" in r5["reasons"])

    # ============================================================
    # Reflect clustering cases
    # ============================================================
    def cluster_root(episodes):
        rr = Path(tempfile.mkdtemp(prefix="odin-cad-clu-"))
        _write(rr, "knowledge/odin-brain/.last-collect", iso(today))
        for i, (status, ents, kws) in enumerate(episodes):
            _write(rr, f"knowledge/odin-brain/episodes/e{i}.md",
                   _episode(f"e{i}", status, ents, kws))
        return rr

    ok &= _check("1 raw -> 0 clusters",
                 oc.count_reflect_clusters(cluster_root([("raw", ["a"], ["k"])])) == 0)
    ok &= _check("2 raw share entity -> 1 cluster",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a", "x"], ["k1"]), ("raw", ["a", "y"], ["k2"])])) == 1)
    ok &= _check("2 raw share keyword only -> 1 cluster",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a"], ["shared"]), ("raw", ["b"], ["shared"])])) == 1)
    # transitive: A~B via entity, B~C via keyword -> single size-3 component
    ok &= _check("transitive A-B-C -> 1 cluster",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a", "shared_ent"], ["k1"]),
                     ("raw", ["shared_ent"], ["shared_kw"]),
                     ("raw", ["c"], ["shared_kw"])])) == 1)
    ok &= _check("2 graduated -> 0 clusters",
                 oc.count_reflect_clusters(cluster_root([
                     ("graduated", ["a"], ["k"]), ("graduated", ["a"], ["k"])])) == 0)

    # ============================================================
    # Stale-cluster escalation (age = days since NEWEST episode logged)
    # ============================================================
    def cluster_root_created(episodes):
        rr = Path(tempfile.mkdtemp(prefix="odin-cad-stale-"))
        _write(rr, "knowledge/odin-brain/.last-collect", iso(today))
        for i, (status, ents, kws, created) in enumerate(episodes):
            _write(rr, f"knowledge/odin-brain/episodes/e{i}.md",
                   _episode(f"e{i}", status, ents, kws, created=created))
        return rr

    # fresh: both episodes logged today -> cluster, but not stale
    rf = oc.compute(cluster_root_created([
        ("raw", ["a"], ["k"], iso(today)),
        ("raw", ["a"], ["k"], iso(today))]), min_entries=5)
    ok &= _check("fresh cluster -> 1 cluster, 0 stale",
                 rf["reflect_clusters"] == 1 and rf["stale_clusters"] == 0)

    # stale: both logged 20d ago -> cluster aged 20d, escalates
    rs = oc.compute(cluster_root_created([
        ("raw", ["a"], ["k"], iso(today - timedelta(days=20))),
        ("raw", ["a"], ["k"], iso(today - timedelta(days=20)))]), min_entries=5)
    ok &= _check("stale cluster -> 1 stale, oldest 20d",
                 rs["reflect_clusters"] == 1 and rs["stale_clusters"] == 1
                 and rs["oldest_cluster_age_days"] == 20)
    ok &= _check("stale cluster -> reason recorded",
                 "stale_clusters>=1" in rs["reasons"])
    ok &= _check("stale escalation in suggestion line",
                 "1 stale, oldest 20d" in oc.suggestion_line(rs))

    # mixed: newest logged today, other 40d ago -> uses NEWEST -> not stale
    rm = oc.compute(cluster_root_created([
        ("raw", ["a"], ["k"], iso(today)),
        ("raw", ["a"], ["k"], iso(today - timedelta(days=40)))]), min_entries=5)
    ok &= _check("mixed cluster uses newest -> 0 stale",
                 rm["reflect_clusters"] == 1 and rm["stale_clusters"] == 0)

    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
