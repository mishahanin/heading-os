"""The /pulse assembler: one root, one unguarded call, and two deal counts.

Found by the 2026-08-23 engine audit, shard `scripts-02-p2`, plus one defect
the audit did not report and this test measured on live data.

**The root.** Fourteen functions here took `(workspace_root, data_root=None)`,
and `pulse_data` called each of them with `data_root` in the FIRST slot, so
`data_root` stayed None and every read resolved against the global
`get_data_root()` instead of the root the caller asked for. Measured
2026-08-24: `workspace_root` was never once used as a path in this module --
zero occurrences of `workspace_root / ...`. The parameter was dead, and its
NAME is the whole defect: it made every correct-in-practice call look wrong and
made one genuinely wrong call (`list_active_tasks(data_root, ...,
data_root=data_root)`) invisible. Both parameters are now one honest name.

**The unguarded call.** Every sub-source in `pulse_data` is wrapped except
`list_pipeline`, whose result was then read with direct `pipe["overdue_count"]`
indexing. The comment above it claimed silent degradation; `signals()` in the
same file already wraps the identical call, which is the tell. One parser
exception took down the whole page whose stated invariant is per-source
isolation.

**The two deal counts (not in the audit).** `list_pipeline` computes the value
and the count from the deal ROWS. `pipeline_value_and_deals` reads a
hand-maintained summary table in the same markdown. `pulse_data` showed the
summary for the headline KPI and the computed rollup for the stage breakdown
beside it, and nothing compared them. Measured on the live pipeline on
2026-08-24: the summary said 29 active deals, the table held 28, and the
dashboard showed 29. The value agreed at $11,000,000, which is exactly why the
drift went unnoticed. The headline is now DERIVED from the rows; the summary is
still read and any disagreement is reported as `pipeline_summary_drift` rather
than discarded, because the markdown is the operator's document and being told
it has drifted beats being silently overruled.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import pulse as P  # noqa: E402

_HEADER = ("| Company | Country | Stage | Est. Value | Stage Date | Owner "
           "| Next Action | Due Date |\n|---|---|---|---|---|---|---|---|\n")


def _pipeline_md(root: Path, rows: str, stated_deals: int | None = None,
                 stated_value: str | None = None) -> None:
    body = "## Active Deals\n\n" + _HEADER + rows
    if stated_deals is not None or stated_value is not None:
        body += "\n## Summary\n\n| Metric | Value |\n|---|---|\n"
        if stated_deals is not None:
            body += f"| Total active deals | {stated_deals} |\n"
        if stated_value is not None:
            body += f"| Total pipeline value (priced deals only) | {stated_value} |\n"
    p = root / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


_ROW = "| Spectre | UK | Lead | $7,000,000 | 2026-08-01 | CEO | Call | 2026-09-01 |\n"


# --- the root the caller passes is the root that is read ---------------------

def test_pulse_reads_the_root_it_was_handed_not_the_global_seam(tmp_path, monkeypatch):
    """The regression the audit asked for: two roots, and the passed one wins."""
    seam, given = tmp_path / "seam", tmp_path / "given"
    (seam / "context").mkdir(parents=True)
    (seam / "context" / "pipeline.md").write_text(
        "## Active Deals\n\n" + _HEADER +
        "| Decoy | UK | Lead | $1 | 2026-08-01 | CEO | x | 2026-09-01 |\n",
        encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(seam))
    _pipeline_md(given, _ROW)

    kpi = P.pulse_data(given)["kpi"]
    assert kpi["pipeline_stages"] == {"Lead": 1}
    assert kpi["pipeline_value"] == 7_000_000, (
        "the assembly read the global seam instead of the root it was given"
    )


def test_no_pulse_function_still_takes_a_dead_workspace_root():
    """Structural: the name that caused the defect must not come back."""
    src = (ROOT / "scripts" / "bridge_daemon" / "sources" / "pulse.py").read_text(encoding="utf-8")
    assert "workspace_root" not in src.replace("``workspace_root``", ""), (
        "a `workspace_root` parameter is back in pulse.py; this module reads "
        "only DATA, so a second root name can only mislead"
    )


def test_no_call_in_pulse_passes_the_root_twice():
    """`f(data_root, ..., data_root=data_root)` is a TypeError waiting for the
    one code path a test does not cover. The rename created exactly one."""
    import re
    src = (ROOT / "scripts" / "bridge_daemon" / "sources" / "pulse.py").read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if re.search(r"\(\s*data_root\b[^)]*\bdata_root\s*=", ln)]
    assert not bad, bad


# --- the pipeline call is guarded like every other sub-source ---------------

def test_a_raising_pipeline_parser_does_not_take_the_page_down(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW)

    def boom(*a, **kw):
        raise RuntimeError("corrupt pipeline row")

    monkeypatch.setattr(P, "list_pipeline", boom)
    kpi = P.pulse_data(tmp_path)["kpi"]          # must not raise
    assert kpi["pipeline_overdue"] == 0
    assert kpi["pipeline_stages"] == {}
    assert kpi["active_deals"] == 0


def test_a_partial_pipeline_return_does_not_key_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW)
    monkeypatch.setattr(P, "list_pipeline", lambda *a, **kw: {"deals": []})
    kpi = P.pulse_data(tmp_path)["kpi"]
    assert kpi["pipeline_overdue"] == 0 and kpi["pipeline_stages"] == {}


# --- the headline number is derived, and drift is reported ------------------

def test_the_headline_count_comes_from_the_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW, stated_deals=29, stated_value="$7,000,000")
    kpi = P.pulse_data(tmp_path)["kpi"]
    assert kpi["active_deals"] == 1, (
        "the dashboard showed a hand-maintained summary row over the actual "
        "rows; that is how 29 was displayed against 28 real deals"
    )


def test_a_disagreeing_summary_row_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW, stated_deals=29, stated_value="$7,000,000")
    drift = P.pulse_data(tmp_path)["kpi"]["pipeline_summary_drift"]
    assert drift == {"deals": {"stated": 29, "actual": 1}}, drift


def test_an_agreeing_summary_row_reports_no_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW, stated_deals=1, stated_value="$7,000,000")
    assert P.pulse_data(tmp_path)["kpi"]["pipeline_summary_drift"] == {}


# --- the small ones ----------------------------------------------------------

def test_a_company_name_with_an_ampersand_survives_the_deep_link():
    """Structural. Kept for what it is, and it is not a measurement.

    This asserts a source LITERAL. It fails on any respelling of a correct
    fix, and it passes as long as the literal appears somewhere in the file,
    so a second `focus=` link built without `quote` would leave it green. It
    catches the exact regression it names and nothing wider. The behavioural
    half is the test below; both are here on purpose, because the structural
    one is what pins the ONE construction site and the behavioural one is what
    proves the link it emits is usable.
    """
    from urllib.parse import quote
    assert "&" not in quote("A&B Telecom"), "the fixture no longer exercises this"
    src = (ROOT / "scripts" / "bridge_daemon" / "sources" / "pulse.py").read_text(encoding="utf-8")
    assert 'focus={quote(ref)}' in src, (
        "the raw company name is interpolated into a query string again; "
        "`#/pipeline?focus=A&B Telecom` focuses 'A' and a '#' truncates it"
    )


def test_the_emitted_deep_link_is_actually_escaped(tmp_path, monkeypatch):
    """The same claim, decided on the value the browser receives.

    The structural test above never calls `suggestions()`, so it establishes
    nothing about the string that reaches the page. This drives a stalled-deal
    signal with the four characters that break a hash route (space, `&`, `#` and
    `?`), then reads the link off the returned row.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW)
    company = "A&B Telecom #2 (who?)"
    monkeypatch.setattr(P, "signals", lambda *a, **kw: [
        {"kind": "pipeline-stalled", "ref": company, "title": "no touch in 30d"},
    ])

    rows = [s for s in P.suggestions(tmp_path) if s["agent"] == "/follow-up"]
    assert rows, "the stalled-deal rule did not fire, so nothing was measured"
    link = rows[0]["link"]

    # The route survives being read back: one fragment, one query, one focus.
    assert link.startswith("#/pipeline?focus=")
    query = link.split("?", 1)[1]
    assert parse_qs(query) == {"focus": [company]}, link
    assert unquote(link) == f"#/pipeline?focus={company}"
    # And none of the four characters is left raw to be reinterpreted.
    for ch in (" ", "&", "#", "?"):
        assert ch not in link.split("focus=", 1)[1], (ch, link)


def test_a_stalled_deal_with_no_company_falls_back_to_the_bare_route(tmp_path,
                                                                    monkeypatch):
    """The other side of the `if ref else` bound, which nothing asserted.

    A signal with an empty ref must not emit `#/pipeline?focus=`, a route that
    focuses the empty string.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    _pipeline_md(tmp_path, _ROW)
    monkeypatch.setattr(P, "signals", lambda *a, **kw: [
        {"kind": "pipeline-stalled", "ref": "", "title": "no touch in 30d"},
    ])
    rows = [s for s in P.suggestions(tmp_path) if s["agent"] == "/follow-up"]
    assert rows and rows[0]["link"] == "#/pipeline"


def test_a_malformed_odin_target_falls_back_instead_of_raising():
    for bad in ("2026-31-12", "end-2026", "", "  ", "not a date"):
        assert isinstance(P.days_to_odin_5(bad), int), bad
    assert P.days_to_odin_5("2026-31-12") == P.days_to_odin_5(None)


def test_a_naive_approval_stamp_does_not_kill_the_whole_watch_source(tmp_path, monkeypatch):
    """One bad row must cost its own row. A naive datetime compared with the
    aware cutoff raises TypeError, which the inner `except ValueError` missed."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    old_naive = (datetime.now(timezone.utc) - timedelta(days=3)).replace(tzinfo=None)
    old_aware = datetime.now(timezone.utc) - timedelta(days=4)
    monkeypatch.setattr(
        "scripts.bridge_daemon.sources.approvals.list_approvals",
        lambda *a, **kw: {"total": 2, "items": [
            {"mtime": old_naive.isoformat()},
            {"mtime": old_aware.isoformat()},
        ]})
    items = P.watch_items(tmp_path)
    drafts = [i for i in items if "draft" in json.dumps(i).lower()]
    assert drafts, (
        "the stale-drafts watchpoint vanished entirely because one row's "
        "timestamp carried no offset"
    )


# --- telemetry ---------------------------------------------------------------

def _self_referential() -> dict:
    """A value `default=str` cannot rescue: json refuses a cycle outright."""
    d: dict = {}
    d["self"] = d
    return d


def test_a_non_serialisable_event_field_does_not_reach_the_caller(tmp_path):
    """The module's own hardening promise: a failed telemetry write is never a
    500. `json.dumps` used to sit ABOVE the try, and it raises TypeError.

    Split in two on purpose. The call is `json.dumps(rec, default=str)`, and
    `default=str` rescues a timedelta, a Path and a set, so a test built only
    on those three raises nothing at all: it survived deleting the whole
    except clause AND survived hoisting the dumps back above the try, which is
    the exact regression it was written to hold down. The rescued values are
    now asserted to land ON DISK, and two values json refuses outright carry
    the guard itself, one per member of the except clause.
    """
    from scripts.bridge_daemon.telemetry import Telemetry
    t = Telemetry(tmp_path)
    log = tmp_path / ".daemon-state" / "usage.jsonl"

    t.event("page_view", page="pulse", duration=timedelta(seconds=3))   # must not raise
    t.event("page_view", page="pulse", where=tmp_path / "x")
    t.event("page_view", page="pulse", tags={"a", "b"})

    rows = [json.loads(ln) for ln
            in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 3, "default=str rescues these three; they belong on disk"
    assert rows[0]["duration"] == "0:00:03", "rescued, not dropped"
    assert rows[1]["where"].endswith("x")
    assert isinstance(rows[2]["tags"], str)

    # Refused by json whatever `default` says: a non-string dict key raises
    # TypeError, a cycle raises ValueError. Both come out of `json.dumps`, so
    # both die if the serialise moves back above the try.
    t.event("page_view", page="pulse", nested={(1, 2): "tuple key"})    # must not raise
    t.event("page_view", page="pulse", loop=_self_referential())        # must not raise

    after = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(after) == 3, "a row json refused must not reach the file either"


def test_an_ordinary_event_is_still_written(tmp_path):
    """Anchor: the guard above must not have turned every write into a no-op."""
    from scripts.bridge_daemon.telemetry import Telemetry
    t = Telemetry(tmp_path)
    t.event("launch", page="pulse")
    rows = [json.loads(ln) for ln in
            (tmp_path / ".daemon-state" / "usage.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows[-1]["event"] == "launch" and rows[-1]["page"] == "pulse"


@pytest.mark.parametrize("field", ["event", "ts"])
def test_a_caller_cannot_overwrite_the_event_name_or_timestamp(tmp_path, field):
    from scripts.bridge_daemon.telemetry import Telemetry
    t = Telemetry(tmp_path)
    t.event("launch", **{field: "spoofed"})
    row = json.loads((tmp_path / ".daemon-state" / "usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[-1])
    assert row[field] != "spoofed", f"a kwarg silently replaced {field}"
