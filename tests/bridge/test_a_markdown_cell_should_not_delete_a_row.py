"""Parser defects that quietly change what the operator is shown.

Found by the 2026-08-23 engine audit, shard `scripts-02-p1`. None of these
raises. Each one silently alters the numbers on a page the operator steers by,
which is worse than a crash: a crash is investigated.

`pipeline.py`
    A deal row whose Next Action cell is ZERO-WIDTH (`||`) failed `_ROW_RE`
    and was skipped with no count and no error. The deal vanished from
    /pipeline and from `total_value_usd`. An empty next action is exactly the
    state a deal is in when it most needs to be seen. Narrower than the audit
    reported: its repro used two spaces, and `[^|]+?` matches a space, so that
    case always worked.

    The Active Deals section ended only at an H2, so a later `# Archive` heading
    carrying an eight-column table had its rows ingested as live deals.

`investors.py`
    `"parallel" in slot and "wave 1" in slot.replace(...) or "parallel" in slot`
    -- `and` binds tighter than `or`, so the whole expression is the second term.
    Measured against the live shortlist, the dead conjunct never matched anyway:
    its parallel slot reads "parallel-track week 1-2" and contains no "wave 1".
    The visible consequence was ordering: a slot naming a later wave alongside
    the word "parallel" outranked the firms actually being contacted first.

    The out-of-scope bullet list ended only at an H1, so an ordinary `## Notes`
    after it left the flag set and every later `- **Name**` bullet was filed
    out-of-scope. `_match_status` matches substrings, so one stray key sank a
    live firm to the bottom of the raise dashboard.

    `_acronym` matched `[A-Z][a-zA-Z]*` against keys that are lowercased when
    the decisions table is parsed, so it returned "" for all of them and the
    documented initials fallback could never fire.

    A firm row with a blank Notes cell failed the row regex and disappeared.

`ops.py`
    `lines[-n_lines:]` with `n_lines=0` is `lines[0:]` -- everything, up to
    200 KB, the exact inverse of the request.

    The oversized-tail read dropped the first line unconditionally, losing a
    complete one whenever the byte offset happened to land on a boundary.

    `except UnicodeDecodeError` after `decode(errors="replace")` is unreachable.

    "Today" was a UTC date compared as a STRING PREFIX of the raw stamp, while
    every other surface in the tree defines today via `get_default_tz()`.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import investors as INV  # noqa: E402
from scripts.bridge_daemon.sources import ops as OPS        # noqa: E402
from scripts.bridge_daemon.sources import pipeline as PIPE  # noqa: E402
from scripts.utils.workspace import get_default_tz          # noqa: E402


# ============================================================
# pipeline.py
# ============================================================

_HEADER = ("| Company | Country | Stage | Est. Value | Stage Date | Owner "
           "| Next Action | Due Date |\n"
           "|---|---|---|---|---|---|---|---|\n")


def _pipeline(tmp_path: Path, body: str) -> dict:
    p = tmp_path / PIPE.PIPELINE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return PIPE.list_pipeline(tmp_path)


def test_a_deal_with_a_next_action_is_read(tmp_path):
    """Anchor: the happy path, so the two guards below mean something."""
    got = _pipeline(tmp_path, "## Active Deals\n\n" + _HEADER +
                    "| Spectre | UAE | Lead | $1,000,000 | 2026-08-01 | CEO | Call them | 2026-09-01 |\n")
    assert [d["company"] for d in got["deals"]] == ["Spectre"]


def test_a_deal_with_a_zero_width_next_action_is_still_a_deal(tmp_path):
    """Measured 2026-08-24: `[^|]+?` needed ONE character, and a space is one,
    so `|  |` and `| |` already matched. Only a zero-width `||` failed. The
    audit's own reproduction used two spaces and would not have shown this;
    the defect is real but narrower than reported."""
    got = _pipeline(tmp_path, "## Active Deals\n\n" + _HEADER +
                    "| Spectre | UAE | Lead | $1,000,000 | 2026-08-01 | CEO || 2026-09-01 |\n")
    assert [d["company"] for d in got["deals"]] == ["Spectre"], (
        "a deal with nothing planned is hidden from the page that exists to "
        "show what needs planning"
    )
    assert got["total_value_usd"] == 1_000_000, "the book was understated too"


def test_a_whitespace_only_next_action_was_never_the_bug(tmp_path):
    """Pins the measurement, so nobody re-widens the fix for a case that
    always worked."""
    got = _pipeline(tmp_path, "## Active Deals\n\n" + _HEADER +
                    "| Spectre | UAE | Lead | $1,000,000 | 2026-08-01 | CEO |  | 2026-09-01 |\n")
    assert [d["company"] for d in got["deals"]] == ["Spectre"]


def test_an_h1_section_after_active_deals_is_not_ingested(tmp_path):
    got = _pipeline(tmp_path,
        "## Active Deals\n\n" + _HEADER +
        "| Spectre | UAE | Lead | $1,000,000 | 2026-08-01 | CEO | Call | 2026-09-01 |\n"
        "\n# Archive\n\n" + _HEADER +
        "| Quantum | UK | Closed | $9,000,000 | 2020-01-01 | CEO | Nothing | 2020-02-01 |\n")
    names = [d["company"] for d in got["deals"]]
    assert names == ["Spectre"], f"an archived table was read as live: {names}"


def test_an_h2_section_after_active_deals_still_ends_it(tmp_path):
    got = _pipeline(tmp_path,
        "## Active Deals\n\n" + _HEADER +
        "| Spectre | UAE | Lead | $1,000,000 | 2026-08-01 | CEO | Call | 2026-09-01 |\n"
        "\n## Closed\n\n" + _HEADER +
        "| Quantum | UK | Closed | $9,000,000 | 2020-01-01 | CEO | Nothing | 2020-02-01 |\n")
    assert [d["company"] for d in got["deals"]] == ["Spectre"]


# ============================================================
# investors.py -- slot classification
# ============================================================

def _statuses(slot: str, firm: str = "Universal Exports") -> dict:
    text = ("# Decisions locked\n\n"
            "| Slot | Firm | Wave | Notes |\n|---|---|---|---|\n"
            f"| {slot} | {firm} | x | y |\n")
    return INV._parse_status_from_decisions(text)


def test_the_live_slot_vocabulary_still_classifies(tmp_path):
    """Anchor against the four slot spellings the real shortlist uses."""
    assert _statuses("First 5 (this week)")["universal exports"] == "first-5"
    assert _statuses("Parallel-track week 1-2")["universal exports"] == "parallel-week-1-2"
    assert _statuses("Wave 2 (warm-intro-first)")["universal exports"] == "wave-2"
    assert _statuses("Wave 3 (deferred)")["universal exports"] == "wave-3"


def test_an_explicit_wave_beats_the_parallel_catch_all():
    got = _statuses("Parallel to Wave 3")["universal exports"]
    assert got == "wave-3", (
        f"a slot naming wave 3 was filed as {got} and sorted above the firms "
        "being contacted first"
    )


# ============================================================
# investors.py -- out-of-scope leakage
# ============================================================

def test_an_ordinary_h2_ends_the_out_of_scope_list():
    text = ("# Decisions locked\n\n"
            "| Slot | Firm | Wave | Notes |\n|---|---|---|---|\n"
            "| First 5 (this week) | Universal Exports | x | y |\n"
            "\n## Out-of-scope\n\n"
            "- **Spectre** -- dropped, wrong stage\n"
            "\n## Notes\n\n"
            "- **Universal Exports** -- keep warm for next fund\n")
    st = INV._parse_status_from_decisions(text)
    assert st.get("spectre") == "out-of-scope", "the real out-of-scope bullet was lost"
    assert st.get("universal exports") == "first-5", (
        "a bullet under `## Notes` was filed out-of-scope, so a live firm sank "
        "to the bottom of the raise dashboard"
    )


# ============================================================
# investors.py -- the acronym fallback
# ============================================================

def test_the_initialism_is_built_case_insensitively():
    assert INV._acronym("Universal Exports Fund") == "UEF"
    assert INV._acronym("universal exports fund") == "UEF", (
        "the keys this is called against are lowercased, so a capital-letter "
        "regex made the whole fallback dead code"
    )
    assert INV._acronym("Fund of Funds") == "FF", "stopwords must not become initials"


def test_a_firm_named_by_initials_resolves_to_the_full_name():
    statuses = {"universal exports fund": "wave-2"}
    assert INV._match_status("UEF", statuses) == "wave-2"


def test_an_ambiguous_initialism_refuses_rather_than_guesses():
    statuses = {"universal exports fund": "wave-2",
                "unified equity finance": "wave-3"}
    assert INV._match_status("UEF", statuses) == INV.DEFAULT_WAVE, (
        "two funds share initials and the dashboard picked one of their waves"
    )


def test_an_unambiguous_duplicate_still_resolves():
    """Same status twice is not ambiguity."""
    statuses = {"universal exports fund": "wave-2",
                "unified equity finance": "wave-2"}
    assert INV._match_status("UEF", statuses) == "wave-2"


# ============================================================
# investors.py -- the blank Notes cell
# ============================================================

def test_a_firm_row_with_a_blank_notes_cell_survives():
    row = "| 1 | Universal Exports | VC | UK | $10m | High |  |"
    m = INV._REGION_ROW_RE.match(row)
    assert m is not None, "the firm vanished from the shortlist over an empty note"
    assert m.group("firm").strip() == "Universal Exports"
    assert m.group("notes") == ""


def test_a_firm_row_with_notes_still_parses():
    row = "| 1 | Universal Exports | VC | UK | $10m | High | warm intro via Q |"
    m = INV._REGION_ROW_RE.match(row)
    assert m and m.group("notes") == "warm intro via Q"


# ============================================================
# ops.py
# ============================================================

def _log(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".daemon-state" / "bridge.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_the_tail_returns_the_lines_asked_for(tmp_path):
    _log(tmp_path, "".join(f"line {i}\n" for i in range(10)))
    got = OPS.read_log_tail(tmp_path, 3)
    assert got["lines"] == ["line 7", "line 8", "line 9"]


def test_zero_lines_means_zero_lines(tmp_path):
    _log(tmp_path, "".join(f"line {i}\n" for i in range(10)))
    got = OPS.read_log_tail(tmp_path, 0)
    assert got["lines"] == [], (
        "`lines[-0:]` is `lines[0:]`; asking for nothing returned everything"
    )


def test_a_negative_count_also_returns_nothing(tmp_path):
    _log(tmp_path, "".join(f"line {i}\n" for i in range(10)))
    assert OPS.read_log_tail(tmp_path, -5)["lines"] == []


def test_a_complete_first_line_is_not_dropped(tmp_path, monkeypatch):
    """The seek offset lands exactly on a newline, so nothing is partial."""
    lines = [f"{i:04d}-padding\n" for i in range(20)]
    width = len(lines[0].encode())          # derived, not assumed
    _log(tmp_path, "".join(lines))
    monkeypatch.setattr(OPS, "LOG_TAIL_MAX_BYTES", width * 5)   # exact boundary
    got = OPS.read_log_tail(tmp_path, 50)
    assert len(got["lines"]) == 5, (
        f"a complete line was discarded at the boundary: {got['lines']}"
    )
    assert got["lines"][0] == "0015-padding"


def test_a_truncated_first_line_is_still_dropped(tmp_path, monkeypatch):
    lines = [f"{i:04d}-padding\n" for i in range(20)]
    width = len(lines[0].encode())
    _log(tmp_path, "".join(lines))
    monkeypatch.setattr(OPS, "LOG_TAIL_MAX_BYTES", width * 5 - 4)   # mid-line
    got = OPS.read_log_tail(tmp_path, 50)
    # 4, not 5: the window opens mid-line 15, that partial line is dropped, and
    # lines 16-19 remain. Arithmetic, not tolerance.
    assert len(got["lines"]) == 4, got["lines"]
    assert all(ln.endswith("padding") for ln in got["lines"]), got["lines"]


def test_undecodable_bytes_do_not_fail_the_read(tmp_path):
    p = _log(tmp_path, "")
    p.write_bytes(b"good line\n\xff\xfe bad bytes\n")
    got = OPS.read_log_tail(tmp_path, 10)
    assert got["ok"] is True, got


def _usage(tmp_path: Path, rows: list[str]) -> None:
    p = tmp_path / ".daemon-state" / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(rows), encoding="utf-8")


def test_today_is_the_operators_calendar_day_not_utc(tmp_path):
    """An event at 01:00 local on a +04:00 host is 21:00Z the day BEFORE. The
    old string-prefix test on the UTC stamp filed it under yesterday."""
    tz = get_default_tz()
    now_local = datetime(2026, 8, 24, 9, 0, tzinfo=tz)
    early = (now_local.replace(hour=1)).astimezone(timezone.utc)

    # The day flip is a PRECONDITION of this test, not a case to skip past.
    #
    # Fixed 2026-08-30. An early return sat here guarded by
    # `if early.date() == now_local.date()`, commented "host is at or east of
    # UTC+0 with no day flip; nothing to prove". Two things were wrong with it.
    # The autouse `_pin_the_operator_zone` fixture in tests/bridge/conftest.py
    # pins HEADING_OS_TZ to Etc/GMT-4 for every test in this directory, and
    # `get_default_tz()` re-reads it per call, so `tz` is always UTC+4 here:
    # 01:00+04:00 is 2026-08-23T21:00Z, whose UTC date can never equal
    # 2026-08-24. The branch was unreachable. And the direction was backwards
    # even unpinned - a 01:00-local event flips into the previous UTC day when
    # the zone is EAST of UTC, which is precisely where the comment said the
    # guard would fire. An assertion is the honest form: if the fixture ever
    # stops pinning an eastern zone, this says so instead of passing silently.
    assert early.date() != now_local.date(), (
        f"this test needs a zone where 01:00 local falls on the previous UTC "
        f"day; the pinned zone {tz} gives no flip, so nothing below is measured")

    _usage(tmp_path, ['{"ts": "%s", "event": "launch"}\n' % early.isoformat()])
    got = OPS.read_telemetry_summary(tmp_path, now=now_local.astimezone(timezone.utc))
    assert got["today_total"] == 1, (
        "an event from this morning was counted under yesterday because "
        "'today' came from UTC"
    )


def test_an_event_from_last_week_is_not_today(tmp_path):
    tz = get_default_tz()
    now_local = datetime(2026, 8, 24, 9, 0, tzinfo=tz)
    old = (now_local - timedelta(days=3)).astimezone(timezone.utc)
    _usage(tmp_path, ['{"ts": "%s", "event": "launch"}\n' % old.isoformat()])
    got = OPS.read_telemetry_summary(tmp_path, now=now_local.astimezone(timezone.utc))
    assert got["today_total"] == 0 and got["last_7d_total"] == 1, got


def test_every_event_type_is_counted_not_just_the_four(tmp_path):
    """The docstring used to name four types the code never filtered on. Keep
    the open behaviour: a new writer-side event must be visible, not hidden."""
    tz = get_default_tz()
    now_local = datetime(2026, 8, 24, 9, 0, tzinfo=tz)
    ts = now_local.astimezone(timezone.utc).isoformat()
    _usage(tmp_path, ['{"ts": "%s", "event": "brand_new_event"}\n' % ts])
    got = OPS.read_telemetry_summary(tmp_path, now=now_local.astimezone(timezone.utc))
    assert got["today"].get("brand_new_event") == 1, got
