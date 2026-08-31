"""Two dashboards that dropped data, and a revert that stopped the updates.

Covers the k3 audit shard `scripts-06-p3` for `scripts/generate-dashboard.py`,
`scripts/generate-crm-dashboard.py` and `scripts/generate-newsletter-html.py`,
plus the operator's 2026-08-24 ruling on `--revert-config` in
`scripts/bridge_daemon/config.py`.

*A parser that deleted data it could not fit.* `parse_md_table` existed twice,
byte-for-byte, in the two dashboard generators. Both did
`cells = [c for c in cells if c != ""]`, which removes an empty cell instead of
holding its place, so every value after a blank shifted one column left. Then
`generate-dashboard.py` DROPPED any row that had come out shorter than the
header. One empty Notes cell removed a whole deal from the count, the total
value, the weighted value, the stage counts and the top three, and wrote
nothing anywhere. The CEO's pipeline was quietly smaller than the document it
was built from. Both copies also treated a blank line inside the row loop as
something to skip, so two tables separated by one blank line merged and the
second table's header was parsed as data of the first.

*A dashboard that died on one bad character.* `collect_freshness` matched
`Last verified:` with `\\d{4}-\\d{2}-\\d{2}`, which is a SHAPE, not a date.
`2026-02-30` matched and `date.fromisoformat` raised, uncaught, before anything
was written. One impossible date in one of four files meant no dashboard.

*A revert that froze the thing it reverted.* A config snapshot held the single
merged blob of defaults, corporate and user, and `--revert-config` wrote that
whole blob into the USER layer. Every corporate value became a user override,
so corporate pushes stopped reaching those keys, permanently and in silence.
Snapshots now hold the layers apart and a revert restores only `user`.

No dashboard is rendered here and no daemon is started. Every test drives a
pure function over text it supplies itself.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from scripts.bridge_daemon.config import (
    SNAPSHOT_SCHEMA,
    list_snapshots,
    load_config,
    revert_config_to,
    snapshot_config,
)
from scripts.utils.html_templates import templates_dir
from scripts.utils.markdown import parse_md_table, split_table_row

ROOT = Path(__file__).resolve().parent.parent
_ISO_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# `generate_newsletter` inlines the stylesheet, and the stylesheet is a BRAND
# ASSET that ships in the private DATA overlay, not in the engine. On a clone
# without the overlay the file is simply not on disk, and `load_template`
# refuses to render an unstyled document. That refusal is the designed
# behaviour, so the whole-page tests below are gated on the asset itself rather
# than on which data root resolved: an operator who has the overlay but has
# deleted the file should still see these fail.
_NEWSLETTER_CSS = templates_dir() / "newsletter.css"
needs_newsletter_css = pytest.mark.skipif(
    not _NEWSLETTER_CSS.is_file(),
    reason=(
        f"brand stylesheet {_NEWSLETTER_CSS} is not on disk, so whole-page assembly "
        "(div balance, bar sanitising, region aliasing, a numeric date field) is "
        "NOT measured on this runner"
    ),
)


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code(name: str) -> str:
    """Source minus whole-line comments.

    Each fix left a comment quoting the code it removed, so a plain grep for
    the old shape finds its own tombstone and passes for the wrong reason.
    """
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


# ============================================================
# An empty cell is a cell
# ============================================================

def test_an_empty_middle_cell_keeps_its_position():
    assert split_table_row("| Alice | | Smith | 14 |") == ["Alice", "", "Smith", "14"]


def test_a_row_with_no_empties_is_unchanged():
    assert split_table_row("| a | b | c |") == ["a", "b", "c"]


def test_a_trailing_empty_cell_is_kept():
    assert split_table_row("| a | b | |") == ["a", "b", ""]


def test_a_leading_empty_cell_is_kept():
    assert split_table_row("| | b | c |") == ["", "b", "c"]


def test_the_blank_after_a_blank_column_no_longer_shifts_the_row():
    table = (
        "| Name | Company | Owner | Days | Health |\n"
        "|---|---|---|---|---|\n"
        "| Alice | | Bond | 14 | RED |\n"
    )
    row = parse_md_table(table)[0]
    assert row["Company"] == ""
    assert row["Owner"] == "Bond", "Owner used to show the company"
    assert row["Days"] == "14"
    assert row["Health"] == "RED"


# ============================================================
# A short row is reported, never dropped
# ============================================================

def test_a_short_row_survives_and_is_padded():
    table = (
        "| Company | Region | Stage | Notes | Value |\n"
        "|---|---|---|---|---|\n"
        "| Acme | UAE | Proposal | | 2000000 |\n"
        "| Umbrella | UAE | Lead |\n"
    )
    rows = parse_md_table(table, warn=lambda _m: None)
    assert len(rows) == 2, "the short row used to vanish from every total"
    assert rows[1]["Company"] == "Umbrella"
    assert rows[1]["Value"] == ""


def test_a_short_row_is_reported():
    seen: list[str] = []
    parse_md_table(
        "| A | B | C |\n|---|---|---|\n| only-one |\n",
        source="pipeline.md", warn=seen.append,
    )
    assert len(seen) == 1
    assert "pipeline.md" in seen[0]
    assert "1 cells" in seen[0] and "3" in seen[0]


def test_an_over_long_row_is_reported_and_trimmed():
    seen: list[str] = []
    rows = parse_md_table("| A | B |\n|---|---|\n| 1 | 2 | 3 |\n", warn=seen.append)
    assert rows == [{"A": "1", "B": "2"}]
    assert len(seen) == 1 and "extra cells dropped" in seen[0]


def test_a_well_formed_table_reports_nothing():
    seen: list[str] = []
    parse_md_table("| A | B |\n|---|---|\n| 1 | 2 |\n", warn=seen.append)
    assert seen == []


# ============================================================
# A blank line ends the table
# ============================================================

def test_two_tables_separated_by_a_blank_line_do_not_merge():
    text = (
        "| A | B |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
        "\n"
        "| C | D |\n"
        "|---|---|\n"
        "| 3 | 4 |\n"
    )
    rows = parse_md_table(text, warn=lambda _m: None)
    assert rows == [{"A": "1", "B": "2"}], (
        "the second table's header used to be parsed as a data row of the first"
    )


def test_a_heading_ends_the_table():
    text = "| A | B |\n|---|---|\n| 1 | 2 |\n## Next\n| C | D |\n"
    assert parse_md_table(text, warn=lambda _m: None) == [{"A": "1", "B": "2"}]


def test_a_missing_table_under_a_named_heading_is_reported():
    seen: list[str] = []
    text = "## Active Deals\n" + "prose\n" * 30 + "| A |\n|---|\n| 1 |\n"
    assert parse_md_table(text, r"##\s*Active Deals", warn=seen.append) == []
    assert len(seen) == 1 and "no table found" in seen[0]


def test_a_heading_that_is_absent_returns_empty_without_a_warning():
    seen: list[str] = []
    assert parse_md_table("nothing here", r"##\s*Nope", warn=seen.append) == []
    assert seen == []


# ============================================================
# One parser, not two copies
# ============================================================

@pytest.mark.parametrize("script", ["generate-dashboard.py",
                                    "generate-crm-dashboard.py"])
def test_the_dashboards_no_longer_carry_their_own_parser(script):
    code = _code(script)
    assert "def parse_md_table(" not in code, (
        "two copies is how the same defect got fixed in neither"
    )
    assert "from scripts.utils.markdown import parse_md_table" in code


@pytest.mark.parametrize("script", ["generate-dashboard.py",
                                    "generate-crm-dashboard.py"])
def test_no_copy_of_the_cell_deleting_line_survives(script):
    assert '[c for c in cells if c != ""]' not in _code(script)
    assert "[c for c in cells if c]" not in _code(script)


# ============================================================
# A shape is not a date
# ============================================================

def test_an_impossible_last_verified_date_degrades_one_row(tmp_path, monkeypatch,
                                                           capsys):
    dash = _load("generate-dashboard.py")
    monkeypatch.setattr(dash, "context_dir", lambda p=tmp_path: p)
    # people.md comes from the identity seam now, not from a second literal
    # under `context_dir()`, so the seam is what a test has to redirect.
    monkeypatch.setattr(dash, "get_people_file", lambda: tmp_path / "people.md")
    good = dash.TODAY.isoformat()
    (tmp_path / "pipeline.md").write_text(f"Last verified: {good}\n", encoding="utf-8")
    (tmp_path / "current-data.md").write_text("Last verified: 2026-02-30\n",
                                              encoding="utf-8")
    (tmp_path / "strategy.md").write_text("no marker\n", encoding="utf-8")
    (tmp_path / "people.md").write_text("Last verified: 2026-13-01\n", encoding="utf-8")

    rows = {r["name"]: r for r in dash.collect_freshness()}

    assert rows["pipeline.md"]["health"] == "green"
    assert rows["current-data.md"]["health"] == "gray", (
        "2026-02-30 matches the regex and used to kill the whole run"
    )
    assert rows["current-data.md"]["age"] is None
    assert rows["people.md"]["health"] == "gray"
    assert rows["strategy.md"]["health"] == "gray"
    err = capsys.readouterr().err
    assert "2026-02-30" in err and "2026-13-01" in err, (
        "degrading in silence would look like a legitimately stale file"
    )


def test_the_freshness_date_parse_is_guarded_in_source():
    code = _code("generate-dashboard.py")
    idx = code.index("verified = date.fromisoformat(date_str)")
    window = code[max(0, idx - 200):idx + 300]
    assert "try:" in window and "except ValueError:" in window


# ============================================================
# A revert restores the operator's layer, not the company's
# ============================================================

def _layers(root: Path, corporate: dict | None, user: dict | None) -> None:
    if corporate is not None:
        corp = root / "corporate" / "daemon" / "config.yaml"
        corp.parent.mkdir(parents=True, exist_ok=True)
        corp.write_text(yaml.safe_dump(corporate), encoding="utf-8")
    if user is not None:
        u = root / ".daemon-state" / "config.yaml"
        u.parent.mkdir(parents=True, exist_ok=True)
        u.write_text(yaml.safe_dump(user), encoding="utf-8")


def test_a_snapshot_holds_the_two_layers_apart(tmp_path):
    _layers(tmp_path, {"stop_prompt_timeout_s": 9}, {"port_range_start": 40000})
    out = snapshot_config(tmp_path, load_config(tmp_path))
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["schema"] == SNAPSHOT_SCHEMA
    assert doc["corporate"] == {"stop_prompt_timeout_s": 9}
    assert doc["user"] == {"port_range_start": 40000}
    assert doc["merged"]["stop_prompt_timeout_s"] == 9
    assert doc["merged"]["port_range_start"] == 40000


def test_a_revert_does_not_write_corporate_values_into_the_user_layer(tmp_path):
    _layers(tmp_path, {"stop_prompt_timeout_s": 9}, {"port_range_start": 40000})
    snapshot_config(tmp_path, load_config(tmp_path))
    _layers(tmp_path, None, {"port_range_start": 50000})

    revert_config_to(tmp_path, list_snapshots(tmp_path)[0].name)

    restored = yaml.safe_load(
        (tmp_path / ".daemon-state" / "config.yaml").read_text(encoding="utf-8"))
    assert restored == {"port_range_start": 40000}
    assert "stop_prompt_timeout_s" not in restored, (
        "a corporate value written here stops every future corporate push"
    )


def test_a_corporate_push_still_reaches_the_daemon_after_a_revert(tmp_path):
    """The whole point. The old revert made this assertion impossible to pass."""
    _layers(tmp_path, {"stop_prompt_timeout_s": 9}, {"port_range_start": 40000})
    snapshot_config(tmp_path, load_config(tmp_path))
    revert_config_to(tmp_path, list_snapshots(tmp_path)[0].name)

    _layers(tmp_path, {"stop_prompt_timeout_s": 22}, None)  # corporate pushes
    assert load_config(tmp_path)["stop_prompt_timeout_s"] == 22
    assert load_config(tmp_path)["port_range_start"] == 40000


def test_reverting_with_no_user_overrides_clears_the_user_layer(tmp_path):
    _layers(tmp_path, {"stop_prompt_timeout_s": 9}, None)
    snapshot_config(tmp_path, load_config(tmp_path))
    _layers(tmp_path, None, {"port_range_start": 50000})

    revert_config_to(tmp_path, list_snapshots(tmp_path)[0].name)

    text = (tmp_path / ".daemon-state" / "config.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text) == {}
    assert load_config(tmp_path)["port_range_start"] == 31415  # back to the default


def test_a_legacy_snapshot_restores_exactly_as_it_always_did(tmp_path, caplog):
    """A schema-1 file on disk must not be reinterpreted under the new rule.

    Guessing "this blob is a user layer" would be right; guessing what INSIDE
    it was the user layer would not be. It is restored whole, as before, and
    the consequence is stated in the log.
    """
    _layers(tmp_path, {"stop_prompt_timeout_s": 9}, None)
    history = tmp_path / ".daemon-state" / "config-history"
    history.mkdir(parents=True, exist_ok=True)
    legacy = history / "000000000_20260101T000000_000000Z.yaml"
    legacy.write_text(yaml.safe_dump({"stop_prompt_timeout_s": 9,
                                      "port_range_start": 40000}), encoding="utf-8")

    with caplog.at_level("WARNING"):
        revert_config_to(tmp_path, legacy.name)

    restored = yaml.safe_load(
        (tmp_path / ".daemon-state" / "config.yaml").read_text(encoding="utf-8"))
    assert restored == {"stop_prompt_timeout_s": 9, "port_range_start": 40000}
    assert "predates layered snapshots" in caplog.text
    assert "stop_prompt_timeout_s" in caplog.text


def test_a_layered_revert_does_not_cry_wolf_about_shadowed_keys(tmp_path, caplog):
    _layers(tmp_path, {"stop_prompt_timeout_s": 9}, {"stop_prompt_timeout_s": 3})
    snapshot_config(tmp_path, load_config(tmp_path))
    with caplog.at_level("WARNING"):
        revert_config_to(tmp_path, list_snapshots(tmp_path)[0].name)
    assert "frozen user overrides" not in caplog.text, (
        "restoring the operator's own override is what a revert is for"
    )


def test_an_unparseable_snapshot_is_treated_as_legacy_not_as_empty(tmp_path):
    """Reading it as layered would restore `{}` and silently delete overrides."""
    _layers(tmp_path, None, {"port_range_start": 40000})
    history = tmp_path / ".daemon-state" / "config-history"
    history.mkdir(parents=True, exist_ok=True)
    bad = history / "000000000_20260101T000000_000000Z.yaml"
    bad.write_text("{ not: valid: yaml\n", encoding="utf-8")

    revert_config_to(tmp_path, bad.name)

    text = (tmp_path / ".daemon-state" / "config.yaml").read_text(encoding="utf-8")
    assert text == "{ not: valid: yaml\n"


# ============================================================
# A dashboard that keeps going, and totals that add up
# ============================================================

@pytest.fixture(scope="module")
def dash():
    return _load("generate-dashboard.py")


@pytest.mark.parametrize("raw,expected", [
    ("$1.5M", 1_500_000), ("2.5m", 2_500_000), ("~500000", 500_000),
    ("500k", 500_000), ("$2,000,000", 2_000_000), ("1.2B", 1_200_000_000),
    ("", 0), ("  ", 0),
])
def test_a_deal_value_is_read_in_the_shapes_people_write(dash, raw, expected):
    assert dash.parse_money(raw) == expected


def test_an_unreadable_deal_value_is_zero_and_says_so(dash, capsys):
    assert dash.parse_money("about half a million", where="pipeline.md") == 0
    err = capsys.readouterr().err
    assert "about half a million" in err and "counted as 0" in err


@pytest.mark.parametrize("raw,expected", [
    (3, 3), ([1, 2], 2), ({}, 0), (2.7, 2), (None, None), ("x", None), (True, None),
])
def test_a_cluster_count_survives_any_shape_the_producer_sends(dash, raw, expected):
    assert dash._as_int_or_count(raw) == expected


def test_a_bool_is_not_a_cluster_count(dash):
    """isinstance(True, int) is True, so a bool would have rendered as 1."""
    assert dash._as_int_or_count(True) is None
    assert dash._as_int_or_count(False) is None


def test_the_viraid_active_section_ends_at_any_heading():
    code = _code("generate-dashboard.py")
    assert 'if re.match(r"##(?!#)", line.strip()):' in code
    assert 'if re.match(r"##\\s*Completed", line, re.IGNORECASE):' not in code


def test_the_calendar_no_longer_carries_a_fixed_utc_offset():
    """CORRECTED 2026-08-24. This test pinned the wrong fix in place.

    It was written on 2026-08-23 and asserted TWO things: that the hardcoded
    `CALENDAR_UTC_OFFSET_HOURS` was gone, and that `astimezone(tz)` had
    replaced it. The first half is still right. The second half encoded a
    false premise as a requirement.

    `upcoming.md` holds LOCAL times and LOCAL section dates -- written by
    `sync-exchange._event_time_str`, which is
    `event.start.astimezone(local_tz)`, and grouped by `_to_local(...).date()`,
    both since the engine's initial import. There was never a UTC value to
    convert. Replacing a constant offset with a tz-aware conversion removed
    the hardcoding and kept the error, and the added date filter then made it
    worse: on Asia/Dubai a 09:00 meeting still rendered as 13:00, and a 21:00
    meeting became 01:00 tomorrow and vanished from the CEO's day entirely.

    So the assertion is inverted, not deleted. The conversion must NOT come
    back in any form. Behaviour is pinned in
    `tests/test_a_morning_calendar_shifted_by_its_own_timezone.py`, which
    renders both meetings from a fixture and checks the clock face.
    """
    code = _code("generate-dashboard.py")
    assert "CALENDAR_UTC_OFFSET_HOURS" not in code
    assert "astimezone(tz)" not in code


def test_the_cadence_value_is_escaped_like_everything_beside_it():
    code = _code("generate-dashboard.py")
    assert "Cadence: {esc(c.get('cadence', '?'))} days" in code


# ============================================================
# A newsletter link that cannot execute
# ============================================================

@pytest.fixture(scope="module")
def nl():
    return _load("generate-newsletter-html.py")


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "data:text/html;base64,xxx", 'x" onmouseover="alert(1)',
    "/relative/path", "", None, "JavaScript:alert(1)",
])
def test_an_unsafe_url_never_reaches_an_href(nl, bad):
    assert nl.safe_url(bad) == "#"


@pytest.mark.parametrize("good", [
    "https://a.example/x", "http://a.example", "mailto:bond@acme.example",
])
def test_a_safe_url_is_kept(nl, good):
    assert nl.safe_url(good) == good


def test_a_url_with_a_query_string_is_escaped_not_mangled(nl):
    assert nl.safe_url("https://a.example/x?a=1&b=2") == "https://a.example/x?a=1&amp;b=2"


def test_a_markdown_link_with_a_dangerous_scheme_renders_as_plain_text(nl):
    out = nl.markdown_to_html("See [this](javascript:alert(1))")
    assert "href=" not in out


def test_a_markdown_link_keeps_its_query_string_intact(nl):
    out = nl.markdown_to_html("See [this](https://a.example/x?a=1&b=2)")
    assert 'href="https://a.example/x?a=1&amp;b=2"' in out
    assert "&amp;amp;" not in out, "escaping twice corrupts every query string"


def test_consecutive_bullet_lines_are_separate_list_items(nl):
    out = nl.markdown_to_html("- alpha\n- beta\n- gamma")
    assert out.count("<li>") == 3
    assert "<li>alpha</li>" in out


def test_a_paragraph_before_a_bullet_list_is_not_swallowed(nl):
    out = nl.markdown_to_html("Intro line\n- alpha\n- beta")
    assert "<p>Intro line</p>" in out and out.count("<li>") == 2


def test_the_masthead_closes_every_div_it_opens(nl):
    html_out = nl.build_masthead("", "24 August 2026", 7, ["GCC"], "ELEVATED")
    assert html_out.count("<div") == html_out.count("</div>")


@needs_newsletter_css
def test_the_whole_page_closes_every_div_it_opens(nl):
    out = nl.generate_newsletter({"date": "2026-08-24", "issue_number": 7})
    assert out.count("<div") == out.count("</div>")


@needs_newsletter_css
def test_a_heading_section_without_a_body_does_not_crash(nl):
    out = nl.generate_newsletter({"date": "2026-08-24", "the_heading": {"kicker": "x"}})
    assert isinstance(out, str) and out


@needs_newsletter_css
@pytest.mark.parametrize("bars,expected", [
    ([50, 70], 2),
    (["50", 70], 2),
    (["50;position:fixed;background:url(x)", 70], 1),
    (["nonsense"], 0),
])
def test_a_bar_chart_takes_numbers_and_refuses_the_rest(nl, bars, expected, capsys):
    out = nl.generate_newsletter({"date": "2026-08-24", "market_depth": {"bars": bars}})
    assert out.count('class="chart-bar') == expected
    assert "position:fixed" not in out


@needs_newsletter_css
def test_a_bar_percentage_is_clamped(nl):
    out = nl.generate_newsletter({"date": "2026-08-24",
                                  "market_depth": {"bars": [500, -20]}})
    assert "height:100%" in out and "height:0%" in out


@needs_newsletter_css
def test_a_region_carried_under_both_aliases_renders_once(nl):
    out = nl.generate_newsletter({"date": "2026-08-24",
                                  "navigation_chart": {"afr": "A", "africa": "B"}})
    assert out.count('class="region-row"') == 1


@pytest.mark.parametrize("bad", ["../../tmp/escape", "20260824", "2026-02-30",
                                 "not-a-date", "2026-13-01"])
def test_a_date_that_is_not_a_date_never_becomes_a_directory(nl, bad):
    with pytest.raises(SystemExit):
        nl.safe_date_segment(bad)


def test_a_real_date_is_accepted_verbatim(nl):
    assert nl.safe_date_segment("2026-08-24") == "2026-08-24"


def test_a_missing_date_falls_back_to_today(nl):
    assert _ISO_SHAPE.match(nl.safe_date_segment(None))


@needs_newsletter_css
def test_a_numeric_date_field_does_not_crash_the_render(nl):
    """`date.fromisoformat` got an int and raised TypeError, uncaught.

    3.11 also parses the compact "20260824" form, so once the value is
    stringified this renders as a date rather than falling back to the raw
    text. Either outcome is fine; crashing the render was not.
    """
    out = nl.generate_newsletter({"date": 20260824, "issue_number": 1})
    assert "24 August 2026" in out


def test_the_pdf_file_uri_is_well_formed():
    code = _code("generate-newsletter-html.py")
    assert '"file:///" + quote(' not in code, "produced file://// on POSIX"
    assert ".as_uri()" in code


def test_the_no_op_region_replace_is_gone():
    code = _code("generate-newsletter-html.py")
    assert """body_html.replace("<p>", '<p>', -1)""" not in code


# ============================================================
# Gaps the mutation harness found
# ============================================================

def test_an_empty_header_cell_keeps_the_columns_lined_up():
    """Dropping an empty HEADER cell shifts every row under it, not just one."""
    table = "| A | | C |\n|---|---|---|\n| 1 | 2 | 3 |\n"
    rows = parse_md_table(table, warn=lambda _m: None)
    assert rows == [{"A": "1", "": "2", "C": "3"}]


def test_a_contact_with_an_unknown_health_lands_on_the_grey_card(dash, capsys):
    assert dash.health_bucket({"name": "Bond", "health": "BLUE"}) == "gray"
    assert "BLUE" in capsys.readouterr().err


@pytest.mark.parametrize("raw,expected", [
    ("RED", "red"), ("red", "red"), ("  Yellow  ", "yellow"),
    (None, "gray"), ("", "gray"), ("amber", "gray"),
])
def test_health_is_normalised_before_it_is_bucketed(dash, raw, expected):
    assert dash.health_bucket({"name": "x", "health": raw}) == expected


def test_a_known_health_value_is_bucketed_without_a_warning(dash, capsys):
    assert dash.health_bucket({"name": "x", "health": "green"}) == "green"
    assert capsys.readouterr().err == ""


@pytest.fixture(scope="module")
def crm():
    return _load("generate-crm-dashboard.py")


def test_every_contact_lands_on_exactly_one_health_card(crm, capsys):
    counts = crm._health_counts([
        {"name": "a", "health": "RED"}, {"name": "b", "health": "green"},
        {"name": "c", "health": "BLUE"}, {"name": "d", "health": ""},
    ])
    assert sum(counts.values()) == 4, "the cards used to sum to less than the total"
    assert counts == {"RED": 1, "YELLOW": 0, "GREEN": 1, "GRAY": 2}
    assert "BLUE" in capsys.readouterr().err


def test_the_exec_badge_counts_active_executives_only(crm):
    registry = {"executives": [
        {"slug": "a", "status": "active"}, {"slug": "b", "status": "active"},
        {"slug": "c", "status": "inactive"}, {"slug": "d"},
    ]}
    assert crm.active_exec_count(registry) == 2


def test_the_exec_badge_on_an_empty_registry_is_zero(crm):
    assert crm.active_exec_count({}) == 0


def test_one_broken_exec_does_not_end_the_heartbeat_sweep(crm, monkeypatch, capsys):
    """The try used to wrap the whole loop, so execs after the bad one vanished."""
    import scripts.utils.workspace as ws
    # No raising=False: both names are ordinary top-level defs in
    # scripts/utils/workspace.py, so a strict setattr is what turns a rename
    # into an AttributeError here instead of a patch that binds a stranger.
    monkeypatch.setattr(ws, "get_all_active_exec_slugs",
                        lambda: ["one", "boom", "three"])

    def _dir(slug):
        if slug == "boom":
            raise KeyError("no repo for boom")
        return Path("/nonexistent") / slug

    monkeypatch.setattr(ws, "get_per_exec_contacts_dir", _dir)
    monkeypatch.setattr(crm, "count_files_in_dir", lambda _p: 7)

    beats = crm.collect_heartbeat()

    assert set(beats) == {"one", "three"}, "execs after the failure used to be lost"
    assert beats["three"] == 7
    assert "boom" in capsys.readouterr().err


def test_an_unreadable_exec_roster_is_reported_and_empty(crm, monkeypatch, capsys):
    import scripts.utils.workspace as ws

    def _boom():
        raise OSError("registry unreadable")

    monkeypatch.setattr(ws, "get_all_active_exec_slugs", _boom)
    assert crm.collect_heartbeat() == {}
    assert "roster unreadable" in capsys.readouterr().err


def test_a_date_shaped_like_one_but_carrying_a_path_is_refused(nl):
    """The calendar check alone passes this: 2026, 08, 24 are all valid."""
    with pytest.raises(SystemExit):
        nl.safe_date_segment("2026-08-24/../../etc")


def test_the_snapshot_schema_number_is_pinned():
    """Asserting `doc["schema"] == SNAPSHOT_SCHEMA` compares a value to itself.

    The legacy-versus-layered decision reads this number, so a silent bump
    would make every existing schema-2 snapshot look like a legacy blob.
    """
    assert SNAPSHOT_SCHEMA == 2
