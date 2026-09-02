"""A KPI counted the rows the page shows, not the rows the pipeline has.

`scripts/bridge_daemon/sources/pulse.py:pulse_data` computed the headline deal
count as `len(pipe.get("deals", []))`. That list is CAPPED:
`sources/pipeline.py` slices `deals = deals[:PIPELINE_ROW_CAP]` (100) on the
last line before it returns, and it returns `total` beside the list for exactly
this reason, documented as "rows PARSED, which is not len(deals) past the cap".

The two figures on the same KPI card then diverged. `total_value_usd` is summed
BEFORE the slice, over every parsed row, so past 100 deals `kpi.pipeline_value`
kept climbing while `kpi.active_deals` sat at 100. Worse, `pipeline_summary_drift`
one line below compares that count against the summary row the operator
maintains in pipeline.md: a correct summary saying 105 was reported as drift
against an "actual" of 100, so the dashboard raised a warning about a
discrepancy it had created itself.

That is the same defect `list_pipeline` fixed on its own side. The comment at
its parse loop records dropping the `break` at PIPELINE_ROW_CAP precisely
because the aggregates "were published with no sign of it, and `pulse.py`
compares that value against the file's own summary line". The cap moved off the
parse and onto the returned list, and this one consumer kept measuring the list.

Two smaller findings in the same module, both about text that had stopped
describing its own code:

* `today_activity`'s pipeline block said its missing `undo` filter was unlike
  "the four blocks above". It is the SECOND of five: only the investors block
  sits above it, and inbox, approvals and tasks are below.

* `signals` documented its ordering as "then `days_at_stage` DESC". Two of the
  three kinds sort on that; `pipeline-overdue-action` sorts on `-days_late` and
  carries no `days_at_stage` key at all. The code is right (lateness is the
  urgency an overdue action has, and its `days_at_stage` can be None), so the
  docstring is the half that was corrected.

Run: .venv/bin/python -m pytest tests/bridge/test_a_deal_count_that_stopped_at_its_own_display_cap.py
"""
from datetime import date
from pathlib import Path

from scripts.bridge_daemon.sources import pulse as pulse_src
from scripts.bridge_daemon.sources.pipeline import PIPELINE_ROW_CAP, list_pipeline
from scripts.bridge_daemon.sources.pulse import pulse_data, signals

# The day this file speaks about, matching the convention in
# tests/bridge/test_sources_pulse.py: every date-sensitive assertion states it.
PINNED_TODAY = date(2026, 5, 18)

_HEADER = (
    "# Pipeline\n\n"
    "## Active Deals\n\n"
    "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
    "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
)


def _pipeline(root: Path, rows: list[str], summary: str = "") -> Path:
    p = root / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(summary + _HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _rows(n: int, value_usd: int = 1_000_000) -> list[str]:
    """`n` priced Lead-stage rows. Lead never fires a signal, so the fixture
    exercises the count and nothing else."""
    return [
        f"| Company{i:03d} | UAE | Lead | ${value_usd:,} | 2026-05-01 | M | hold | - |"
        for i in range(n)
    ]


# ============================================================
# 1. The count past the display cap
# ============================================================

def test_the_deal_count_is_the_rows_parsed_not_the_rows_shipped(tmp_path):
    over = PIPELINE_ROW_CAP + 5
    _pipeline(tmp_path, _rows(over))

    pipe = list_pipeline(tmp_path, today=PINNED_TODAY)
    assert len(pipe["deals"]) == PIPELINE_ROW_CAP, "fixture must exceed the cap"
    assert pipe["total"] == over
    assert pipe["truncated"] is True

    kpi = pulse_data(tmp_path, today=PINNED_TODAY)["kpi"]
    assert kpi["active_deals"] == over, (
        "active_deals pinned at the display cap while the pipeline grew")


def test_the_value_and_the_count_are_measured_over_the_same_rows(tmp_path):
    """The divergence is what makes the wrong count invisible: one figure on
    the card keeps climbing and the other silently stops."""
    over = PIPELINE_ROW_CAP + 5
    _pipeline(tmp_path, _rows(over, value_usd=1_000_000))
    kpi = pulse_data(tmp_path, today=PINNED_TODAY)["kpi"]
    assert kpi["pipeline_value"] == over * 1_000_000
    assert kpi["pipeline_value"] == kpi["active_deals"] * 1_000_000


def test_a_correct_summary_row_past_the_cap_reports_no_drift(tmp_path):
    """`pipeline_summary_drift` exists to catch an operator's stale summary. It
    was firing on a truncation the dashboard had done to itself."""
    over = PIPELINE_ROW_CAP + 5
    summary = (
        "| Total active deals | " + str(over) + " |\n"
        "| Total pipeline value | $" + f"{over * 1_000_000:,}" + " |\n\n"
    )
    _pipeline(tmp_path, _rows(over), summary=summary)
    kpi = pulse_data(tmp_path, today=PINNED_TODAY)["kpi"]
    assert kpi["pipeline_summary_drift"] == {}, (
        "the page reported drift against its own truncated count")


def test_a_genuinely_stale_summary_row_still_reports_drift(tmp_path):
    """The negative case. Reading `total` must not silence the real warning."""
    over = PIPELINE_ROW_CAP + 5
    summary = "| Total active deals | 7 |\n\n"
    _pipeline(tmp_path, _rows(over), summary=summary)
    drift = pulse_data(tmp_path, today=PINNED_TODAY)["kpi"]["pipeline_summary_drift"]
    assert drift["deals"] == {"stated": 7, "actual": over}


def test_a_pipeline_under_the_cap_is_unchanged(tmp_path):
    """Below the cap the two readings agree, which is why nobody saw this."""
    _pipeline(tmp_path, _rows(3))
    kpi = pulse_data(tmp_path, today=PINNED_TODAY)["kpi"]
    assert kpi["active_deals"] == 3


def test_a_missing_pipeline_still_counts_zero(tmp_path):
    """`pulse_data` sets `pipe = {}` when `list_pipeline` raises, so the new
    read has to degrade the same way the old one did."""
    kpi = pulse_data(tmp_path, today=PINNED_TODAY)["kpi"]
    assert kpi["active_deals"] == 0
    assert kpi["pipeline_value"] == 0


# ============================================================
# 2. A comment that mislocated its own siblings
# ============================================================

def test_the_touch_log_comment_counts_the_blocks_that_are_there():
    src = Path(pulse_src.__file__).read_text(encoding="utf-8")
    assert "unlike the four blocks above" not in src, (
        "the pipeline touch-log block is second of five; one block is above it")
    block = src.split("# Pipeline touch-log.", 1)[1].split("except Exception", 1)[0]
    assert "investors" in block, (
        "the comment must name the one block that really is above it")


# ============================================================
# 3. A docstring that named a key one of its three kinds never sets
# ============================================================

def _signal_rows(spec: list[tuple[str, str, str]]) -> list[str]:
    """(company, stage, due_date) rows, all stage-dated 2026-05-01."""
    return [
        f"| {c} | UAE | {stage} | $1,000,000 | 2026-05-01 | M | chase | {due} |"
        for c, stage, due in spec
    ]


def test_an_overdue_action_signal_carries_no_days_at_stage(tmp_path):
    """The key the docstring named is absent from a third of the output."""
    _pipeline(tmp_path, _signal_rows([("Alfa", "Demo/POC", "2026-05-10")]))
    out = signals(tmp_path, today=PINNED_TODAY)
    assert [s["kind"] for s in out] == ["pipeline-overdue-action"]
    assert "days_at_stage" not in out[0]


def test_overdue_signals_order_on_lateness_not_on_time_in_stage(tmp_path):
    """Both deals entered their stage on the same day, so `days_at_stage` is
    identical and only `days_late` separates them. The later one leads."""
    _pipeline(tmp_path, _signal_rows([
        ("Alfa", "Demo/POC", "2026-05-16"),   # 2 days late on PINNED_TODAY
        ("Bravo", "Demo/POC", "2026-04-18"),  # 30 days late
    ]))
    out = signals(tmp_path, today=PINNED_TODAY)
    assert [s["title"].split(" - ")[0] for s in out] == ["Bravo", "Alfa"]


def test_the_docstring_states_the_key_each_kind_really_sorts_on():
    doc = signals.__doc__ or ""
    # The needle carries the whole old sentence, not the fragment. The corrected
    # docstring QUOTES the fragment when it records what it used to say, and a
    # test that cannot tell a live claim from its own changelog entry would fail
    # on the fix.
    assert "Sorted by severity (red first), then days_at_stage DESC" not in doc, (
        "one of the three kinds sorts on days_late and has no days_at_stage")
    assert "days_late" in doc
