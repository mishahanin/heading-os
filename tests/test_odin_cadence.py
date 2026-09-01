#!/usr/bin/env python3
"""Regression tests for the Odin cadence checker (scripts/odin-cadence.py).

Synthetic fixtures in temp dirs, plain asserts, standalone-runnable. Anchored to
the invariants the cadence nudge must never break:
  - read-only (no file ever created or modified by a run)
  - air-gap (personal / _secure never counted; business is)
  - allowlist scope bound to collect's allowlist
  - counts, never content (no fixture body text in any output)
  - reflect clustering = connected components over >= CLUSTER_MIN_SHARED tags
  - a cluster counts only while it holds material logged after `.last-reflect`
  - threshold boundaries flip the nudge at exactly the right point

This file was written to be standalone-runnable and, until 2026-08-11, was ONLY
that: it carried a `main()` and no `test_` function, so pytest collected nothing
from it and the suite had never once run these cases. The `test_odin_cadence`
wrapper at the bottom is what puts them in the suite; keep it there. A test file
the runner skips is worse than no test file, because the directory listing says
the behaviour is covered.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "odin_cadence", ROOT / "scripts" / "odin-cadence.py"
)
oc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oc)

from scripts.utils.air_gap import is_denied
from scripts.utils.workspace import get_default_tz

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
    today = datetime.now(get_default_tz()).date()
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

    # CRM: 2 rows >= marker for an external contact. The two cards below are OUT
    # OF SCOPE, not excluded: `count_crm` globs `crm/contacts/*.md`, which is
    # non-recursive and rooted one level below `crm/`, so neither file is ever
    # opened and the CRM_EXCLUDE clause is never reached. The comment here read
    # "exclusions present but uncounted" until 2026-09-01, which is why deleting
    # `any(x in f"/{rel}" for x in CRM_EXCLUDE)` left this whole file green.
    # They stay, because "a sibling directory of crm/contacts/ is not counted"
    # is worth pinning; `test_the_crm_count_never_leaves_crm_contacts` below
    # pins the scope directly rather than through this aggregate.
    _write(root, "crm/contacts/acme-corp.md", _crm_contact(
        "Acme Corporation",
        [(iso(today), f"meeting {SENTINEL}"), (iso(today), f"follow up {SENTINEL}"),
         ("2019-01-01", f"old row {SENTINEL}")]))
    _write(root, "crm/.migration-backup/old.md", _crm_contact(
        "Backup Co", [(iso(today), f"out of scope {SENTINEL}")]))
    _write(root, "crm/aggregated/agg.md", _crm_contact(
        "Agg Co", [(iso(today), f"out of scope {SENTINEL}")]))

    # VIRAID: one admitted (external Acme) + one tribe-only/no-counterpart (dropped)
    _write(root, "outputs/operations/viraid/state.json", json.dumps({"messages": {
        "1": {"disposition": "task", "date": iso(today),
              "text": f"Call with Acme about the deal {SENTINEL}",
              "action_summary": "Acme follow-up"},
        "2": {"disposition": "task", "date": iso(today),
              "text": f"internal housekeeping {SENTINEL}",
              "action_summary": "no counterpart"},
    }}))

    # episodes: a 2-node raw cluster sharing three tags (acme, bob, mnda)
    _write(root, "knowledge/odin-brain/episodes/e1.md",
           _episode("e1", "raw", ["acme", "bob"], ["mnda"]))
    _write(root, "knowledge/odin-brain/episodes/e2.md",
           _episode("e2", "raw", ["acme", "bob"], ["mnda", "demo"]))

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
    ok &= _check(f"2 raw share {oc.CLUSTER_MIN_SHARED} tags -> 1 cluster",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a", "x"], ["k1", "z1"]),
                     ("raw", ["a", "x"], ["k1", "z2"])])) == 1)
    # The threshold is the whole point: one shared tag is a topic coincidence, and
    # with union-find the coincidences chain until every episode is one component.
    ok &= _check("2 raw share ONE keyword -> 0 clusters",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a"], ["shared"]), ("raw", ["b"], ["shared"])])) == 0)
    ok &= _check("2 raw share two tags -> 0 clusters (below the threshold)",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a"], ["shared"]), ("raw", ["a"], ["shared"])])) == 0)
    # transitive: A~B on {a,x,k1}, B~C on {k1,k2,k3}; A and C share only k1, so the
    # component exists only through B.
    ok &= _check("transitive A-B-C -> 1 cluster",
                 oc.count_reflect_clusters(cluster_root([
                     ("raw", ["a", "x"], ["k1", "z9"]),
                     ("raw", ["a", "x"], ["k1", "k2", "k3"]),
                     ("raw", ["c"], ["k1", "k2", "k3"])])) == 1)
    ok &= _check("2 graduated -> 0 clusters",
                 oc.count_reflect_clusters(cluster_root([
                     ("graduated", ["a", "x"], ["k1"]),
                     ("graduated", ["a", "x"], ["k1"])])) == 0)

    # ============================================================
    # `.last-reflect` gating: doing the work must clear the nudge
    # ============================================================
    def cluster_root_reflected(episodes, last_reflect=None):
        rr = Path(tempfile.mkdtemp(prefix="odin-cad-refl-"))
        _write(rr, "knowledge/odin-brain/.last-collect", iso(today))
        if last_reflect:
            _write(rr, "knowledge/odin-brain/.last-reflect", last_reflect)
        for i, (status, ents, kws, created) in enumerate(episodes):
            _write(rr, f"knowledge/odin-brain/episodes/e{i}.md",
                   _episode(f"e{i}", status, ents, kws, created=created))
        return rr

    pair = [("raw", ["a", "x"], ["k1"], iso(today - timedelta(days=3))),
            ("raw", ["a", "x"], ["k1"], iso(today - timedelta(days=3)))]

    ok &= _check("no reflect marker -> cluster counts",
                 oc.count_reflect_clusters(cluster_root_reflected(pair)) == 0 + 1)
    ok &= _check("reflect pass after the episodes -> cluster goes quiet",
                 oc.count_reflect_clusters(
                     cluster_root_reflected(pair, iso(today - timedelta(days=1)))) == 0)
    ok &= _check("reflect pass BEFORE the episodes -> still counts",
                 oc.count_reflect_clusters(
                     cluster_root_reflected(pair, iso(today - timedelta(days=10)))) == 1)
    # A cluster reviewed and deliberately not graduated, then fed one new episode,
    # is material again -- and the whole cluster is what the CEO must look at.
    fed = pair + [("raw", ["a", "x"], ["k1"], iso(today))]
    ok &= _check("a reviewed cluster fed a new episode counts again",
                 oc.count_reflect_clusters(
                     cluster_root_reflected(fed, iso(today - timedelta(days=1)))) == 1)
    ok &= _check("an unparseable reflect marker is treated as no marker",
                 oc.count_reflect_clusters(cluster_root_reflected(pair, "not-a-date")) == 1)

    # ============================================================
    # Stale-cluster escalation (age = wait of the OLDEST unreviewed episode)
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
        ("raw", ["a", "x"], ["k"], iso(today)),
        ("raw", ["a", "x"], ["k"], iso(today))]), min_entries=5)
    ok &= _check("fresh cluster -> 1 cluster, 0 stale",
                 rf["reflect_clusters"] == 1 and rf["stale_clusters"] == 0)

    # stale: both logged 20d ago -> the oldest unreviewed has waited 20d
    rs = oc.compute(cluster_root_created([
        ("raw", ["a", "x"], ["k"], iso(today - timedelta(days=20))),
        ("raw", ["a", "x"], ["k"], iso(today - timedelta(days=20)))]), min_entries=5)
    ok &= _check("stale cluster -> 1 stale, oldest 20d",
                 rs["reflect_clusters"] == 1 and rs["stale_clusters"] == 1
                 and rs["oldest_cluster_age_days"] == 20)
    ok &= _check("stale cluster -> reason recorded",
                 "stale_clusters>=1" in rs["reasons"])
    ok &= _check("stale escalation in suggestion line",
                 "1 stale, oldest 20d" in oc.suggestion_line(rs))

    # mixed: one logged today, one 40d ago, neither reviewed. The 40d one is the
    # thing that has been sitting, so the cluster is stale at 40. Reading the
    # NEWEST episode (the pre-2026-08-11 rule) reported 0 stale here, which let a
    # cluster hide an ignored member behind every fresh arrival.
    rm = oc.compute(cluster_root_created([
        ("raw", ["a", "x"], ["k"], iso(today)),
        ("raw", ["a", "x"], ["k"], iso(today - timedelta(days=40)))]), min_entries=5)
    ok &= _check("mixed cluster is stale at the OLDEST unreviewed wait",
                 rm["reflect_clusters"] == 1 and rm["stale_clusters"] == 1
                 and rm["oldest_cluster_age_days"] == 40)

    # ============================================================
    # Gap #5 enrichment: cluster_detail membership + write_cadence_report
    # ============================================================
    detail_root = cluster_root([
        ("raw", ["acme", "bob"], ["mnda"]),
        ("raw", ["acme", "bob"], ["mnda", "demo"]),
    ])
    ca = oc.analyze_reflect_clusters(detail_root, today)
    ok &= _check("cluster_detail has 1 cluster", len(ca["clusters"]) == 1)
    cd = ca["clusters"][0]
    ok &= _check("cluster_detail episodes lists both filenames",
                 set(cd["episodes"]) == {"e0.md", "e1.md"})
    ok &= _check("cluster_detail shared_tags is the tag union",
                 set(cd["shared_tags"]) == {"acme", "bob", "mnda", "demo"})

    r_detail = oc.compute(detail_root, min_entries=5)
    ok &= _check("compute() r has cluster_detail matching analyze_reflect_clusters",
                 r_detail["cluster_detail"] == ca["clusters"])

    # write_cadence_report: no-op (returns None, writes nothing) when empty
    empty_root = cluster_root([])
    r_empty = oc.compute(empty_root, min_entries=5)
    before_files = _snapshot(empty_root)
    rep_none = oc.write_cadence_report(empty_root, r_empty, today)
    after_files = _snapshot(empty_root)
    ok &= _check("write_cadence_report returns None on empty cluster_detail", rep_none is None)
    ok &= _check("write_cadence_report writes nothing on empty cluster_detail",
                 before_files == after_files)

    # write_cadence_report: writes the expected file when clusters exist
    rep_path = oc.write_cadence_report(detail_root, r_detail, today)
    ok &= _check("write_cadence_report writes a file when clusters exist",
                 rep_path is not None and rep_path.is_file())
    ok &= _check("write_cadence_report filename matches convention",
                 rep_path is not None and rep_path.name == f"{today.isoformat()}_odin-cadence_report.md")
    report_text = rep_path.read_text(encoding="utf-8") if rep_path else ""
    ok &= _check("report mentions both episode filenames",
                 "e0.md" in report_text and "e1.md" in report_text)

    # suggestion_line(): includes the report path exactly when one was written
    line_with_report = oc.suggestion_line(r_detail, "outputs/operations/odin-cadence/x.md")
    ok &= _check("suggestion_line appends report path when given",
                 "report: outputs/operations/odin-cadence/x.md" in line_with_report)
    line_without_report = oc.suggestion_line(r_detail)
    ok &= _check("suggestion_line omits report mention when not given",
                 "report:" not in line_without_report)

    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


def test_odin_cadence():
    """Collect the whole script into the suite. Failures print above as [FAIL]."""
    assert main() == 0, "see the [FAIL] lines in captured stdout"


# ---------------------------------------------------------------------------
# `_is_unreviewed` compared dates as TEXT
# ---------------------------------------------------------------------------
#
# Nothing in this repository referenced `_is_unreviewed` before 2026-08-31.
# `main()` above covers the clustering it feeds, but only ever with well-formed
# ISO dates, so the whole malformed-input half of the function was uncovered and
# the defect below survived every run.
#
# Written as a real parametrized test rather than another `_check` inside
# `main()`, so a failure names the case instead of collapsing into one assert.

@pytest.mark.parametrize("created,unreviewed,why", [
    ("2026-06-15", True,
     "a normal date after the marker is unreviewed"),
    ("2026-03-01", False,
     "the marker date itself is not after the marker"),
    ("2026-01-01", False,
     "a normal date before the marker was reviewed"),
    ("15 March 2026", True,
     "unparseable and sorting BELOW the marker: the defect. Text comparison "
     "returned False here, so an undatable episode read as already reviewed"),
    ("1999-13-40", True,
     "an impossible month and day parse as nothing and sort below the marker"),
    ("not-a-date", True,
     "unparseable and sorting ABOVE the marker. Text comparison got this one "
     "right, by accident of sort order, which is why the defect looked covered"),
    ("", True,
     "no date at all cannot establish that anything was reviewed"),
])
def test_an_undatable_episode_counts_as_unreviewed(created, unreviewed, why):
    """The safe direction is to SURFACE, and the docstring says so.

    An episode nobody can date, that then went silent, is material lost from the
    signal. `analyze_reflect_clusters` drops a reviewed episode from
    `unreviewed`, and a cluster whose only unreviewed member was dropped is
    skipped with nothing added to `skipped`, so the nudge goes quiet and cannot
    say why.
    """
    block = f"status: raw\ncreated: {created}\n"
    assert oc._is_unreviewed(block, "2026-03-01") is unreviewed, why


def test_a_malformed_created_does_not_hide_a_good_date():
    """The docstring says "date fieldS", plural, and the sibling already does this.

    `_episode_age_days` falls through from an unparseable `created` to `date`.
    `_is_unreviewed` returned on the first NON-EMPTY field regardless of whether
    it parsed, so one malformed `created` threw away a perfectly good `date`.
    """
    before = "status: raw\ncreated: 15 March 2026\ndate: 2026-01-01\n"
    after = "status: raw\ncreated: 15 March 2026\ndate: 2026-06-15\n"
    assert oc._is_unreviewed(before, "2026-03-01") is False, (
        "the good `date` is before the marker, so this episode was reviewed")
    assert oc._is_unreviewed(after, "2026-03-01") is True, (
        "the good `date` is after the marker, so this episode is unreviewed")


def test_the_crm_count_never_leaves_crm_contacts(tmp_path):
    """Scope, asserted where the aggregate above could not assert it.

    `count_crm` globs `crm/contacts/*.md`. Non-recursive, so a card one level
    deeper is out of scope, and rooted below `crm/`, so a sibling directory is
    too. `main()` writes its two "excluded" cards as siblings of `crm/contacts/`
    and therefore never reaches the CRM_EXCLUDE clause at all -- measured
    2026-09-01 by deleting that clause, which changed nothing anywhere in the
    suite. This test states the scope that is actually enforced, so a later
    widening of the glob to `**` fails here rather than silently pulling the
    derived `crm/aggregated/` view into a count the operator reads as original
    interactions.
    """
    today = datetime.now(get_default_tz()).date().isoformat()
    rows = [(today, "counted")]
    _write(tmp_path, "crm/contacts/in-scope.md", _crm_contact("In Scope", rows))
    _write(tmp_path, "crm/contacts/nested/deeper.md", _crm_contact("Deeper", rows))
    _write(tmp_path, "crm/aggregated/agg.md", _crm_contact("Agg", rows))
    _write(tmp_path, "crm/.migration-backup/old.md", _crm_contact("Backup", rows))

    assert oc.count_crm(tmp_path, "1970-01-01") == 1


def test_an_unreadable_viraid_state_is_reported_not_fatal(tmp_path):
    """`except (OSError, ValueError)` around the viraid state read.

    `read_text(encoding="utf-8")` raises UnicodeDecodeError -- a ValueError and
    a SIBLING of JSONDecodeError, not a subclass -- so a state file torn
    mid-write escapes an `except OSError`. The handler's own comment says this
    was MEASURED, and nothing in the suite held it: narrowing the tuple to
    `except OSError` left every cadence test green, because no fixture ever fed
    a state file that could not be read.

    Both halves of the class are exercised: bytes that are not UTF-8, and
    well-decoded text that is not JSON. Either way the count is 0 AND `skipped`
    says so, because the `--json` output must never assert a complete pass it
    did not make.
    """
    for label, payload in (("bad bytes", b"\xe9\xff not utf-8"),
                           ("bad json", b"{not: json,,,")):
        root = tmp_path / label.replace(" ", "-")
        state = root / oc.VIRAID_STATE
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_bytes(payload)

        skipped: list = []
        assert oc.count_viraid(root, "1970-01-01", skipped) == 0, label
        assert any("viraid" in s and "unreadable" in s for s in skipped), (
            f"{label}: the run was silent about a state file it could not read: {skipped}")


def test_an_unreadable_marker_leaves_everything_unreviewed():
    """A marker nobody can parse establishes nothing, so nothing is reviewed."""
    block = "status: raw\ncreated: 2026-01-01\n"
    assert oc._is_unreviewed(block, "whenever") is True
    assert oc._is_unreviewed(block, None) is True
    assert oc._is_unreviewed(block, "") is True


if __name__ == "__main__":
    raise SystemExit(main())
