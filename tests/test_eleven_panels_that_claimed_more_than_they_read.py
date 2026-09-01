"""Shard 39: eleven places where the morning dashboard said more than it read.

`scripts/generate-dashboard.py` is 1662 lines and renders eleven panels from
nine sources. The defects below are one shape seen eleven times: a panel that
states a fact its source never supported, and a sibling panel three functions
away that already handles the identical case correctly.

Every test here patches the module-level path constants. They resolve at import
through the data-root helpers, so a test that leaves them alone reads whatever
overlay the host happens to carry - green on a workstation, and a different
program on a runner with no overlay.
"""
import ast
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate-dashboard.py"


def _load():
    spec = importlib.util.spec_from_file_location("generate_dashboard_shard39", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gd():
    return _load()


@pytest.fixture
def source():
    """The generator's own text, for the meta-tests that read structure."""
    return SCRIPT.read_text(encoding="utf-8")


# ============================================================
# A. The two sync files, whose age nothing ever measured
# ============================================================
def test_a_sync_stamp_becomes_an_age(gd, monkeypatch):
    monkeypatch.setattr(gd, "NOW", datetime(2026, 5, 12, 12, 0, tzinfo=gd.get_default_tz()))
    assert gd.sync_age_hours("2026-05-12 09:00") == pytest.approx(3.0)
    assert gd.sync_age_hours("2026-05-10 12:00 (Asia/Dubai)") == pytest.approx(48.0)


def test_an_unreadable_stamp_is_unknown_not_fresh(gd):
    """None, never 0. A stamp that cannot be read is not evidence of freshness."""
    assert gd.sync_age_hours("") is None
    assert gd.sync_age_hours(None) is None
    assert gd.sync_age_hours("yesterday afternoon") is None


def test_an_impossible_stamp_degrades_instead_of_raising(gd):
    """The regex matches the SHAPE of a timestamp. 2026-02-30 has that shape."""
    assert gd.sync_age_hours("2026-02-30 09:00") is None


def test_an_absent_calendar_file_is_not_a_read_one(gd, tmp_path, monkeypatch):
    monkeypatch.setattr(gd, "calendar_file", lambda p=tmp_path / "nothing-here.md": p)
    result = gd.collect_calendar()
    assert result["source_read"] is False
    assert result["meetings"] == []


def test_an_absent_email_file_is_not_an_empty_inbox(gd, tmp_path, monkeypatch):
    monkeypatch.setattr(gd, "email_file", lambda p=tmp_path / "nothing-here.md": p)
    assert gd.collect_emails()["source_read"] is False


def test_a_read_calendar_says_so(gd, tmp_path, monkeypatch):
    f = tmp_path / "upcoming.md"
    f.write_text("> Synced: 2026-05-12 09:00 (Asia/Dubai)\n\n## 2026-05-12\n",
                 encoding="utf-8")
    monkeypatch.setattr(gd, "calendar_file", lambda p=f: p)
    monkeypatch.setattr(gd, "NOW", datetime(2026, 5, 12, 10, 0, tzinfo=gd.get_default_tz()))
    result = gd.collect_calendar()
    assert result["source_read"] is True
    assert result["age_hours"] == pytest.approx(1.0)


def test_an_unread_source_never_claims_the_day_is_free(gd):
    """The whole point. "No meetings scheduled today" is a claim about the CEO's
    day; produced from a file nothing opened, it is a false one."""
    unread = {"meetings": [], "sync_time": "", "source_read": False, "age_hours": None}
    empty = {"emails": [], "sync_time": "", "count": 0,
             "source_read": False, "age_hours": None}
    html = gd_module_build_bridge(unread, empty)
    assert "No meetings scheduled today" not in html
    assert "No recent emails" not in html
    assert "EMPTY, not clear" in html


def gd_module_build_bridge(calendar, emails):
    return _load().build_bridge(calendar, emails)


def test_a_read_but_quiet_day_still_reads_as_quiet(gd):
    """The fix must not swallow the legitimate case it was distinguishing from."""
    read_empty = {"meetings": [], "sync_time": "2026-05-12 09:00",
                  "source_read": True, "age_hours": 1.0}
    html = gd.build_bridge(read_empty, dict(read_empty, emails=[], count=0))
    assert "No meetings scheduled today" in html


def test_a_stale_sync_is_labelled_stale(gd):
    fresh = {"sync_time": "2026-05-12 09:00", "source_read": True, "age_hours": 2.0}
    stale = {"sync_time": "2026-05-05 09:00", "source_read": True, "age_hours": 168.0}
    assert "STALE" not in gd._sync_label("Calendar", fresh)
    assert "STALE" in gd._sync_label("Calendar", stale)


def test_the_stale_threshold_has_a_case_on_the_line(gd):
    """A bound needs a test ON it, or an off-by-one moves it unnoticed."""
    on_the_line = {"sync_time": "x", "source_read": True,
                   "age_hours": float(gd.SYNC_STALE_HOURS)}
    just_under = {"sync_time": "x", "source_read": True,
                  "age_hours": gd.SYNC_STALE_HOURS - 0.01}
    assert "STALE" in gd._sync_label("Calendar", on_the_line)
    assert "STALE" not in gd._sync_label("Calendar", just_under)


def test_an_unread_source_is_labelled_not_synced(gd):
    assert "NOT SYNCED" in gd._sync_label("Calendar", {"source_read": False})


def test_an_unknown_age_is_named_rather_than_assumed_fresh(gd):
    label = gd._sync_label("Calendar", {"sync_time": "who knows", "source_read": True,
                                        "age_hours": None})
    assert "age unknown" in label
    assert "STALE" not in label


# ============================================================
# B. Fifteen market figures, one of which was read
# ============================================================
FIXTURE_METRICS = """# Current data

| Metric | Value | Notes |
|--------|-------|-------|
| Headcount | ~61 | Across 14 countries |
| Hiring target | 250 | As fast as we can find them |

| Metric | Value | CAGR | Source |
|--------|-------|------|--------|
| Global market (2030) | **$91.00B** | **25.50%** | Example Research |

| Metric | Value | Source |
|--------|-------|--------|
| CIS DPI market (2024 - 2030) | $420M -> $2.50B | Example Playbook |

| Vendor | Status | Notes | Relevance |
|--------|--------|-------|-----------|
| ExampleVendor | Defunct | exited 71 countries | We are the replacement |
"""


def test_the_market_panel_follows_its_source_file(gd, tmp_path, monkeypatch):
    """Editing current-data.md used to produce byte-identical output."""
    f = tmp_path / "current-data.md"
    f.write_text(FIXTURE_METRICS, encoding="utf-8")
    monkeypatch.setattr(gd, "metrics_file", lambda p=f: p)
    m = gd.collect_metrics()
    assert m["headcount"] == "61"
    assert m["hiring_target"] == "250"
    assert m["dpi_tam_2030"] == "$91.00B"
    assert m["cagr"] == "25.50%"
    assert m["cis_2030"] == "$2.50B"
    assert m["predecessor_vacuum_countries"] == "71"


def test_the_rendered_panel_carries_the_file_values_not_the_constants(gd, tmp_path,
                                                                     monkeypatch):
    f = tmp_path / "current-data.md"
    f.write_text(FIXTURE_METRICS, encoding="utf-8")
    monkeypatch.setattr(gd, "metrics_file", lambda p=f: p)
    html = gd.build_market(gd.collect_metrics())
    assert "$91.00B" in html
    assert "$78.04B" not in html


def test_the_country_pattern_does_not_match_the_headcount_row(gd):
    """The loose form `(\\d+) countries` matched "Across 14 countries" thirty
    lines earlier in the real file and would have printed 14 under "Incumbent
    Vacuum" - a NEW wrong number shipped by the fix for a stale one."""
    import re
    pattern = gd._METRIC_PATTERNS["predecessor_vacuum_countries"][0]
    assert re.search(pattern, "| Headcount | ~58 | Across 14 countries |") is None
    assert re.search(pattern, "exited 56 countries").group(1) == "56"


def test_a_figure_that_cannot_be_found_says_so_by_name(gd, tmp_path, monkeypatch, capsys):
    f = tmp_path / "current-data.md"
    f.write_text("# Nothing this page needs\n", encoding="utf-8")
    monkeypatch.setattr(gd, "metrics_file", lambda p=f: p)
    m = gd.collect_metrics()
    err = capsys.readouterr().err
    for field in gd._METRIC_PATTERNS:
        assert field in err, f"{field} went missing without a word"
    # And the page still draws, on the last known reading.
    assert m["dpi_tam_2030"] == "$78.04B"


def test_an_absent_metrics_file_stays_silent(gd, tmp_path, monkeypatch, capsys):
    """No overlay is not a drifted file. A public clone must not print seven
    warnings about figures it was never going to have."""
    monkeypatch.setattr(gd, "metrics_file", lambda p=tmp_path / "absent.md": p)
    gd.collect_metrics()
    assert capsys.readouterr().err == ""


def test_mea_2030_deliberately_has_no_pattern(gd):
    """Its source row carries a bare `$3.47` where the global table writes
    `$78.04B`, and that table states no unit. Extracting it would render
    "$3.47" under the label "MEA 2030". The page will not guess a unit."""
    assert "mea_2030" not in gd._METRIC_PATTERNS
    assert gd.collect_metrics.__doc__ and "mea_2030" in gd.collect_metrics.__doc__


def test_every_figure_the_market_panel_draws_is_read_or_explained(gd, source):
    """A guard against the next figure being added as a silent constant."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_market")
    drawn = {n.slice.value for n in ast.walk(fn)
             if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)}
    doc = gd.collect_metrics.__doc__ or ""
    for field in drawn:
        assert field in gd._METRIC_PATTERNS or field in doc, (
            f"{field} is drawn on the page, has no pattern, and is not named in "
            f"collect_metrics' docstring as a deliberate constant")


# ============================================================
# C. Five heading indicators, three of which were computed
# ============================================================
def _pipeline(**over):
    base = {"total_won": 0, "partnerships": [], "deals": [], "total_investors": 0}
    base.update(over)
    return base


def _hiring(total=0, urgent=0):
    return {"p1": [], "p2": [], "p3": [], "total": total,
            "urgent": [{"Role": f"r{i}"} for i in range(urgent)]}


def _states(html):
    """The dot classes, in order, as rendered."""
    import re
    return re.findall(r'heading-dot ([gyr])"', html)


def test_hiring_momentum_reads_the_hiring_data(gd):
    metrics = {"headcount": "58", "hiring_target": "200"}
    strategy = {}
    red = gd.build_heading(strategy, _pipeline(), metrics, _hiring(total=4, urgent=2))
    amber = gd.build_heading(strategy, _pipeline(), metrics, _hiring(total=4))
    green = gd.build_heading(strategy, _pipeline(), metrics, _hiring(total=0))
    assert _states(red)[3] == "r"
    assert _states(amber)[3] == "y"
    assert _states(green)[3] == "g"


def test_the_hiring_caption_names_what_the_dot_was_read_from(gd):
    html = gd.build_heading({}, _pipeline(), {"headcount": "58", "hiring_target": "200"},
                            _hiring(total=4, urgent=2))
    assert "4 open, 2 urgent" in html


def test_fundraising_progress_reads_the_investor_count(gd):
    metrics = {"headcount": "58", "hiring_target": "200"}
    for investors, expected in ((0, "r"), (1, "y"), (2, "y"), (3, "g")):
        html = gd.build_heading({}, _pipeline(total_investors=investors), metrics,
                                _hiring())
        assert _states(html)[4] == expected, f"{investors} investors -> {expected}"


def test_no_heading_indicator_is_a_constant(gd):
    """Each of the five must be able to reach at least two different states.
    Indicators 4 and 5 were the literal "y" and could reach exactly one."""
    metrics = {"headcount": "58", "hiring_target": "200"}
    worst = gd.build_heading({}, _pipeline(), metrics, _hiring(total=4, urgent=1))
    best = gd.build_heading(
        {}, _pipeline(total_won=5, total_investors=9,
                      partnerships=[{"Stage": "Active"}] * 4,
                      deals=[{"Stage": "post-mwc", "Notes": "mwc"}] * 6),
        metrics, _hiring(total=0))
    # strict: the two renders must expose the same five indicators. A silent
    # zip truncation would let this pass while one of them drew four.
    for i, (a, b) in enumerate(zip(_states(worst), _states(best), strict=True)):
        assert a != b, f"indicator {i + 1} rendered {a!r} for both extremes"


def test_the_caption_carries_no_raw_html_entity(gd):
    """The status string goes through esc(), which turns a `&bull;` into the
    literal characters "&bull;" on the page."""
    html = gd.build_heading({}, _pipeline(), {"headcount": "58", "hiring_target": "200"},
                            _hiring(total=1))
    assert "&amp;bull;" not in html


# ============================================================
# D + E. A cadence counter that fell when you published
# ============================================================
def _cadence_dirs(gd, tmp_path, monkeypatch):
    live = tmp_path / "linkedin"
    drafts = tmp_path / "linkedin-drafts"
    archive = tmp_path / "linkedin-archive"
    monkeypatch.setattr(gd, "linkedin_dir", lambda p=live: p)
    monkeypatch.setattr(gd, "linkedin_drafts_dir", lambda p=drafts: p)
    monkeypatch.setattr(gd, "linkedin_archive_dir", lambda p=archive: p)
    monkeypatch.setattr(gd, "newsletters_dir", lambda p=tmp_path / "newsletters": p)
    return live, drafts, archive


def test_publishing_a_post_does_not_remove_it_from_the_count(gd, tmp_path, monkeypatch):
    """`/linkedin-archive` git-mv's a published post out of the staged directory.
    Counting the staged directory alone meant publishing two posts moved the
    indicator from ON TRACK to BEHIND, and the way to stay green was to leave
    the work unpublished."""
    live, _drafts, archive = _cadence_dirs(gd, tmp_path, monkeypatch)
    live.mkdir()
    (live / "draft-a.md").write_text("x", encoding="utf-8")
    (live / "draft-b.md").write_text("x", encoding="utf-8")
    before = gd.collect_content_cadence()
    assert before["linkedin_status"] == "ON TRACK"

    # Publish both: the archive nests one folder per slug.
    (live / "draft-a.md").unlink()
    (live / "draft-b.md").unlink()
    for slug in ("draft-a", "draft-b"):
        folder = archive / "posts" / slug
        folder.mkdir(parents=True)
        (folder / f"{slug}.md").write_text("x", encoding="utf-8")

    after = gd.collect_content_cadence()
    assert after["linkedin_count_week"] == 2
    assert after["linkedin_status"] == "ON TRACK"


def test_the_archive_is_walked_deep_not_flat(gd, tmp_path, monkeypatch):
    """A flat iterdir over the archive sees only slug directories and counts
    nothing, which would have looked exactly like the bug it fixes."""
    _live, _drafts, archive = _cadence_dirs(gd, tmp_path, monkeypatch)
    folder = archive / "articles" / "some-slug"
    folder.mkdir(parents=True)
    (folder / "some-slug.md").write_text("x", encoding="utf-8")
    assert gd.collect_content_cadence()["linkedin_count_week"] == 1


def test_no_linkedin_directory_at_all_is_no_data_not_behind(gd, tmp_path, monkeypatch):
    """The status line ran unconditionally, so "NO DATA" was unreachable and a
    workspace with no content directory was told it was BEHIND on a cadence
    nothing had measured. The newsletter half has always guarded its own."""
    _cadence_dirs(gd, tmp_path, monkeypatch)
    assert gd.collect_content_cadence()["linkedin_status"] == "NO DATA"


def test_an_existing_but_empty_directory_is_behind_not_no_data(gd, tmp_path, monkeypatch):
    """Measured and found wanting is a different fact from never measured."""
    live, _drafts, _archive = _cadence_dirs(gd, tmp_path, monkeypatch)
    live.mkdir()
    assert gd.collect_content_cadence()["linkedin_status"] == "BEHIND"


def test_the_no_data_state_reaches_the_page(gd):
    """A collector state no builder renders is the same defect one layer down."""
    html = gd.build_content_cadence({
        "newsletter_days": None, "newsletter_status": "NO DATA",
        "newsletter_last": None,
        "linkedin_count_week": 0, "linkedin_status": "NO DATA"})
    assert html.count("NO DATA") == 2
    assert "No LinkedIn content directory found" in html
    assert "0 posts/drafts this week" not in html


def test_a_measured_linkedin_week_still_reports_its_count(gd):
    html = gd.build_content_cadence({
        "newsletter_days": 2, "newsletter_status": "ON TRACK",
        "newsletter_last": "2026-05-10",
        "linkedin_count_week": 3, "linkedin_status": "ON TRACK"})
    assert "3 posts/drafts this week" in html


# ============================================================
# F + I. Viraid: a swallowed read, and a zero that meant two things
# ============================================================
def _viraid_files(gd, tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    state = tmp_path / "state.json"
    monkeypatch.setattr(gd, "viraid_tasks_file", lambda p=tasks: p)
    monkeypatch.setattr(gd, "viraid_state_file", lambda p=state: p)
    return tasks, state


def test_a_corrupt_state_file_is_reported_not_swallowed(gd, tmp_path, monkeypatch,
                                                        capsys):
    """The only handler in the file that said nothing at all."""
    tasks, state = _viraid_files(gd, tmp_path, monkeypatch)
    tasks.write_text("## Active\n- [ ] `P1` | do a thing\n", encoding="utf-8")
    state.write_text("{not json at all", encoding="utf-8")
    result = gd.collect_viraid()
    err = capsys.readouterr().err
    assert "state.json unreadable" in err
    assert result["rate_known"] is False


def test_an_unreadable_rate_is_a_dash_not_a_measured_zero(gd):
    unknown = {"active_total": 3, "p1": 1, "p2": 1, "p3": 1, "aging": 0,
               "completion_rate": 0.0, "tasks_read": True, "rate_known": False}
    html = gd.build_viraid(unknown)
    assert "0%" not in html
    assert ">-<" in html


def test_a_genuine_zero_percent_still_prints_as_a_percentage(gd):
    measured = {"active_total": 3, "p1": 1, "p2": 1, "p3": 1, "aging": 0,
                "completion_rate": 0.0, "tasks_read": True, "rate_known": True}
    assert "0%" in gd.build_viraid(measured)


def test_an_unread_tasks_file_is_not_an_empty_in_tray(gd, tmp_path, monkeypatch):
    _tasks, _state = _viraid_files(gd, tmp_path, monkeypatch)
    result = gd.collect_viraid()
    assert result["tasks_read"] is False
    assert "No Viraid tasks data available" in gd.build_viraid(result)


def test_a_read_file_with_nothing_outstanding_shows_its_zeroes(gd, tmp_path,
                                                               monkeypatch):
    """`active == 0` was the test, so an empty in-tray was reported as missing
    data - and the branch threw away a completion rate read from another file."""
    tasks, state = _viraid_files(gd, tmp_path, monkeypatch)
    tasks.write_text("## Active\n\n## Completed\n- [x] `P1` | done\n", encoding="utf-8")
    state.write_text(json.dumps({"stats": {"completion_rate": 87}}), encoding="utf-8")
    result = gd.collect_viraid()
    assert result["active_total"] == 0
    assert result["tasks_read"] is True
    html = gd.build_viraid(result)
    assert "No Viraid tasks data available" not in html
    assert "87%" in html


# ============================================================
# G. The fourth CRM bucket
# ============================================================
def _crm(red=0, yellow=0, green=0, gray=0):
    def rows(n, tag):
        return [{"name": f"{tag}{i}", "company": "", "type": "", "days_since": 1}
                for i in range(n)]
    return {"red": rows(red, "r"), "yellow": rows(yellow, "y"),
            "green": rows(green, "g"), "gray": rows(gray, "n"),
            "commitments_due": [], "total": red + yellow + green + gray,
            "contacts": [], "failed": ""}


def test_the_unreadable_bucket_reaches_the_radar(gd):
    """HEALTH_BUCKETS has four values and `total` counts all four; three were
    drawn. The circles summed to less than the number beside them, and the
    contacts a person most needs to see were the ones nothing showed."""
    html = gd.build_radar(_crm(red=1, yellow=2, green=3, gray=4))
    assert "Health Unreadable" in html
    assert ">4<" in html


def test_the_fourth_circle_is_absent_on_an_ordinary_morning(gd):
    assert "Health Unreadable" not in gd.build_radar(_crm(red=1, green=9))


def test_the_radar_circles_account_for_the_total(gd):
    import re
    crm = _crm(red=1, yellow=2, green=3, gray=4)
    nums = [int(n) for n in re.findall(r'class="radar-num"[^>]*>(\d+)<', gd.build_radar(crm))]
    assert sum(nums) == crm["total"]


def test_the_terminal_summary_names_every_bucket(gd, source):
    """The console line is what a cron log keeps. It printed three of four."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    text = " ".join(
        ast.get_source_segment(source, n) or ""
        for n in ast.walk(fn)
        if isinstance(n, ast.JoinedStr) and "CRM:" in (ast.get_source_segment(source, n) or "")
    )
    for bucket in gd.HEALTH_BUCKETS:
        assert f"crm['{bucket}']" in text, f"the CRM summary line never names {bucket}"


# ============================================================
# H. A stage nobody recognised, priced at 5% in silence
# ============================================================
PIPELINE_FIXTURE = """## Active Deals

| Company | Stage | Est. Value | Stage Date |
|---------|-------|-----------|-----------|
| Universal Exports | Proposal | $1M | 2026-05-01 |
| Spectre Ltd | Demo/PoC | $2M | 2026-05-01 |
"""


def test_an_off_list_stage_is_named_on_stderr(gd, tmp_path, monkeypatch, capsys):
    """`parse_money`, reading the cell one column to the left, has warned about
    an unreadable value since it was written. The same loop said nothing about
    an unreadable stage, which costs up to 95% of the deal's weight."""
    f = tmp_path / "pipeline.md"
    f.write_text(PIPELINE_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(gd, "pipeline_file", lambda p=f: p)
    result = gd.collect_pipeline()
    err = capsys.readouterr().err
    assert "Demo/PoC" in err
    assert "Spectre Ltd" in err
    assert result["off_stages"] == {"Demo/PoC": 1}


def test_a_canonical_stage_stays_quiet(gd, tmp_path, monkeypatch, capsys):
    f = tmp_path / "pipeline.md"
    f.write_text(PIPELINE_FIXTURE.replace("Demo/PoC", "Negotiation"), encoding="utf-8")
    monkeypatch.setattr(gd, "pipeline_file", lambda p=f: p)
    result = gd.collect_pipeline()
    assert "not a canonical stage" not in capsys.readouterr().err
    assert result["off_stages"] == {}


def test_an_off_list_deal_is_drawn_somewhere(gd):
    """It counted in Active Deals and its money in Total Value, then appeared in
    no bar: the chart summed to fewer deals than the number printed beside it."""
    pipeline = {"stages": {"Lead": 2, "Demo/PoC": 3}, "off_stages": {"Demo/PoC": 3},
                "total_value": 0, "weighted_value": 0, "stale_count": 0,
                "total_deals": 5, "total_won": 0, "total_investors": 0,
                "total_partnerships": 0, "top_deals": []}
    html = gd.build_pipeline(pipeline)
    assert "Other" in html
    import re
    counts = [int(n) for n in re.findall(r'class="bar-count">(\d+)<', html)]
    assert sum(counts) == pipeline["total_deals"]


def test_no_other_column_when_every_stage_is_canonical(gd):
    pipeline = {"stages": {"Lead": 2}, "off_stages": {},
                "total_value": 0, "weighted_value": 0, "stale_count": 0,
                "total_deals": 2, "total_won": 0, "total_investors": 0,
                "total_partnerships": 0, "top_deals": []}
    assert ">Other<" not in gd.build_pipeline(pipeline)


# ============================================================
# J. Six of thirty, under a heading carrying no count
# ============================================================
def _emails(n):
    return {"emails": [{"From": f"p{i}@example.test", "Subject": f"s{i}", "Read": "yes"}
                       for i in range(n)],
            "sync_time": "2026-05-12 09:00", "count": n,
            "source_read": True, "age_hours": 1.0}


def test_a_truncated_email_table_admits_the_cut(gd):
    html = gd.build_bridge({"meetings": [], "sync_time": "2026-05-12 09:00",
                            "source_read": True, "age_hours": 1.0}, _emails(30))
    assert f"Showing {gd.EMAIL_PREVIEW_ROWS} of 30 synced" in html


def test_an_untruncated_table_says_nothing_about_a_cut(gd):
    html = gd.build_bridge({"meetings": [], "sync_time": "2026-05-12 09:00",
                            "source_read": True, "age_hours": 1.0},
                           _emails(gd.EMAIL_PREVIEW_ROWS))
    assert "Showing" not in html


def test_the_table_draws_exactly_the_preview_rows(gd):
    html = gd.build_bridge({"meetings": [], "sync_time": "x",
                            "source_read": True, "age_hours": 1.0}, _emails(30))
    assert html.count("@example.test") == gd.EMAIL_PREVIEW_ROWS


# ============================================================
# K. A run that produced no PDF and reported success
# ============================================================
def _mainable(gd, tmp_path, monkeypatch, *argv):
    """main() without the render.

    `generate_html` reaches dashboard.css in the private data overlay and
    REFUSES to draw an unstyled page when it is absent, which is correct and is
    not what these two tests are about. Stubbing it keeps them on the exit code.
    """
    monkeypatch.setattr(gd, "collect_capture_payoff", lambda: {"available": False})
    monkeypatch.setattr(gd, "generate_html", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(sys, "argv",
                        ["generate-dashboard.py", "--output-dir", str(tmp_path), *argv])


def test_a_failed_pdf_returns_non_zero(gd, tmp_path, monkeypatch, capsys):
    _mainable(gd, tmp_path, monkeypatch, "--pdf")

    def _boom(*a, **k):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(gd.subprocess, "run", _boom)
    rc = gd.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "WITHOUT the requested PDF" in out


def test_a_successful_pdf_still_returns_zero(gd, tmp_path, monkeypatch):
    """The guard must not report failure for a run that produced what was asked."""
    _mainable(gd, tmp_path, monkeypatch, "--pdf")
    monkeypatch.setattr(gd.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert gd.main() == 0


def test_a_run_with_no_pdf_asked_for_still_succeeds(gd, tmp_path, monkeypatch):
    _mainable(gd, tmp_path, monkeypatch)
    assert gd.main() == 0
    assert (tmp_path / "morning-dashboard.html").exists()


def test_the_entry_point_carries_the_exit_code(source):
    """`main()` alone discards the return value, so the exit status was 0
    whatever happened inside. The return value is only a claim if something
    reads it."""
    tree = ast.parse(source)
    guard = [n for n in tree.body
             if isinstance(n, ast.If) and ast.get_source_segment(source, n.test)
             == '__name__ == "__main__"']
    assert guard, "no __main__ guard"
    body = ast.get_source_segment(source, guard[0]) or ""
    assert "sys.exit(main())" in body


# ============================================================
# Meta: the page must not learn to lie again in a new place
# ============================================================
def test_no_panel_asserts_an_absence_without_asking_whether_it_read(source):
    """Every affirmative empty-state sentence on this page must sit behind a
    check that the source was actually read. The four here are the ones that
    say something about the world rather than about a file."""
    tree = ast.parse(source)
    claims = ("No meetings scheduled today", "No recent emails",
              "No Viraid tasks data available")
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        text = ast.get_source_segment(source, fn) or ""
        for claim in claims:
            if claim not in text:
                continue
            assert ("source_read" in text or "tasks_read" in text
                    or "_empty_row" in text), (
                f"{fn.name} prints {claim!r} without asking whether its source "
                f"was read")


def _silent_handlers(src):
    """Lines of every `except: pass` that is NOT guarding a date parse.

    A count would have been the easy assertion and the wrong one: it passes a
    new swallowed read the moment an old one is deleted. The rule this file
    actually holds is narrower. Four handlers here end in a bare `pass`, and
    all four wrap a single `date.fromisoformat` on one row of a loop whose
    totals get reported regardless. Swallowing a FILE read is the defect.
    """
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guarded = " ".join(ast.get_source_segment(src, s) or "" for s in node.body)
        for handler in node.handlers:
            body = [s for s in handler.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            silent = len(body) == 1 and isinstance(body[0], ast.Pass)
            if silent and "fromisoformat" not in guarded:
                offenders.append(handler.lineno)
    return offenders


def test_the_generator_has_no_silent_exception_handler(source):
    """A `pass` under an except is how the viraid rate came to read 0%."""
    assert _silent_handlers(source) == []


def _inject_swallowed_read(src: str) -> str:
    """Put a bare `pass` back on the handler of every file read in `src`.

    Located by AST, not by a source literal. This was
    `src.replace('        except (json.JSONDecodeError, OSError) as e:', ...)`,
    an exact string lifted out of `collect_viraid` including its indentation
    and its precise exception tuple. On 2026-09-01 that tuple gained
    `UnicodeError` (a `read_text` decode failure is a ValueError, so neither
    named clause caught it) and the replace stopped matching: `restored` was
    the unmodified source, `_silent_handlers` correctly returned nothing, and
    the only case that ever proved this detector can fire failed.

    It failed loudly here only because the live file happens to have zero
    offenders. Had one existed, the assertion would have passed on the real
    file's offender while the injection did nothing at all, which is the shape
    this whole file exists to refuse: a control that reports on something other
    than what it claims to measure.
    """
    tree = ast.parse(src)
    lines = src.splitlines()
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guarded = " ".join(ast.get_source_segment(src, s) or "" for s in node.body)
        if "read_text" not in guarded or "fromisoformat" in guarded:
            continue
        for handler in node.handlers:
            first = handler.body[0]
            indent = " " * (first.col_offset)
            edits.append((first.lineno - 1, handler.end_lineno, indent))
    for start, end, indent in reversed(edits):
        lines[start:end] = [f"{indent}pass"]
    return "\n".join(lines) + "\n"


def test_the_detector_refuses_a_swallowed_read(source):
    """A detector never shown a real offender is a claim, not a control."""
    restored = _inject_swallowed_read(source)
    assert restored != source, (
        "no file read in the generator was turned into a swallowed one, so the "
        "case below would pass or fail for a reason other than the detector")
    assert _silent_handlers(restored), "the detector accepted a swallowed file read"


def test_the_detector_refuses_a_swallowed_read_it_has_never_seen():
    """The same claim on input no other shard can move out from under it.

    The case above reads the live generator, so any edit to that file can
    silence it. This one cannot drift: the offender is written here.
    """
    offender = (
        "import json\n"
        "def read_it(path):\n"
        "    try:\n"
        "        return json.loads(path.read_text(encoding='utf-8'))\n"
        "    except OSError:\n"
        "        pass\n"
    )
    assert _silent_handlers(offender), "the detector accepted a swallowed file read"


def test_the_detector_still_permits_the_date_parsers(source):
    """And it must not fire on the four it is written to allow.

    This asserted `_silent_handlers(source) == []`, which is the assertion
    `test_the_generator_has_no_silent_exception_handler` already makes eight
    lines up: over a file with no offenders it is satisfied by a detector that
    permits everything, so the carve-out it names had no case. The synthetic
    input below has a date-parse handler and nothing else, so the detector must
    be quiet FOR THE STATED REASON, and the pair beneath it shows the same
    handler reported once `fromisoformat` is gone.
    """
    permitted = (
        "from datetime import date\n"
        "def age(raw):\n"
        "    try:\n"
        "        return date.fromisoformat(raw)\n"
        "    except ValueError:\n"
        "        pass\n"
    )
    assert _silent_handlers(permitted) == []
    assert _silent_handlers(permitted.replace("date.fromisoformat(raw)", "int(raw)")), (
        "the carve-out is not keyed on the date parse at all")
    assert _silent_handlers(source) == []
