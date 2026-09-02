#!/usr/bin/env python3
"""Shard 10-p1 and 10-p2: three places Inbox Pulse described itself wrongly.

Three separate readers, one theme: the words next to a number, or the words
above some code, said something the code did not do.

The report's raw-distribution table was headed "7-day avg". Its divisor is the
count of past days that CARRIED MAIL, which is at most seven and routinely
fewer, because `_compute_daily_distribution` excludes silent days on purpose
(a day with no log is absent data, not a measured zero, and
`fetch_jsonl_for_date` cannot tell a missing log from an empty one anyway).
That exclusion is deliberate and pinned in two other files, so the arithmetic
is right and the HEADING was the part that lied: six quiet days plus one day of
70 LOW items reported "7-day avg 70.0" when the calendar-week mean is 10.0, and
the trend arrow beside it flipped accordingly. Per `.claude/rules/scope-claims.md`,
a tool states the coverage its method establishes; this one named a week it had
not measured.

The saved markdown never recorded an unreadable day. The coverage warning lived
only on stderr and in the exit code, so the file written to
`outputs/operations/inbox-pulse/`, opened in VS Code and archived, stated
"Total emails classified: N" and, when today was the unread day, "No emails
classified today." A later reader, or a scheduler that files the artifact
without propagating the exit status, was handed exactly the quiet-inbox
misreading the whole `ssh_read` redesign exists to prevent.

And the comment above `_pattern_matches_domain`'s split described `rpartition`
while the code calls `partition`, and called a live guard unreachable. Both
splitters happen to reject the same inputs, so nothing misbehaved, but a reader
trusting the note would have deleted the only test that refuses the pattern
`"*"`.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "inbox_pulse_report_10p1", ROOT / "scripts" / "inbox-pulse-report.py")
report = importlib.util.module_from_spec(_spec)
sys.modules["inbox_pulse_report_10p1"] = report
_spec.loader.exec_module(report)


def _entry(tier: str) -> dict:
    return {"tier_guess": tier, "sender_domain": "example.test", "ts": ""}


def _agg(daily_dist: dict) -> dict:
    """The minimum aggregate shape `render_report` reads."""
    return {
        "total": 0, "high": [], "maybe": [], "low": [],
        "known_good_low": {}, "top_unknown": [], "suggestions": [],
        "daily_dist": daily_dist,
    }


def _render(daily_dist: dict, today: date, **kwargs) -> str:
    return report.render_report(
        agg=_agg(daily_dist), today=today, days=1,
        window_start=today, state_json={}, entries_total_in_window=0,
        **kwargs)


# ---------------------------------------------------------------------------
# The average column named a week it had not measured
# ---------------------------------------------------------------------------


def test_the_average_column_does_not_call_itself_a_seven_day_average():
    """Six silent days and one busy one: the divisor is 1, not 7.

    Reported through the rendered table rather than the dict, because the
    heading is the artifact the operator reads.
    """
    today = date(2026, 9, 2)
    by_date = {today: []}
    for i in range(1, 8):
        by_date[today - timedelta(days=i)] = []
    by_date[today - timedelta(days=3)] = [_entry("LOW") for _ in range(70)]

    dist = report._compute_daily_distribution(by_date, today)
    assert dist["avg"]["LOW"] == pytest.approx(70.0), (
        "the divisor changed; this test pins the LABEL over the existing "
        "active-day arithmetic, not the arithmetic itself")

    out = _render(dist, today)
    assert "7-day avg" not in out, (
        f"the column still claims a seven-day denominator while dividing by "
        f"the 1 day that carried mail:\n{out}")


def test_the_report_states_how_many_days_actually_entered_the_average():
    """Naming the real divisor is what makes 70.0 readable. Without it the
    number is unfalsifiable to the reader."""
    today = date(2026, 9, 2)
    by_date = {today: [_entry("LOW")]}
    for i in range(1, 8):
        by_date[today - timedelta(days=i)] = []
    by_date[today - timedelta(days=2)] = [_entry("LOW")]
    by_date[today - timedelta(days=4)] = [_entry("LOW")]

    dist = report._compute_daily_distribution(by_date, today)
    assert dist["days_in_avg"] == 2, dist

    out = _render(dist, today)
    assert "2 of the past 7" in out, (
        f"the report does not say how many days the mean was taken over:\n"
        f"{out}")


def test_a_full_week_of_mail_still_reports_seven_days_in_the_average():
    """The counter-case. Hardcoding a divisor of 1, or dropping the count
    entirely, would pass both tests above."""
    today = date(2026, 9, 2)
    by_date = {today: [_entry("LOW")]}
    for i in range(1, 8):
        by_date[today - timedelta(days=i)] = [_entry("LOW")]

    dist = report._compute_daily_distribution(by_date, today)

    assert dist["days_in_avg"] == 7, dist
    assert "7 of the past 7" in _render(dist, today)


# ---------------------------------------------------------------------------
# The saved markdown never recorded an unreadable day
# ---------------------------------------------------------------------------


def _empty_dist(today: date) -> dict:
    return report._compute_daily_distribution({today: []}, today)


def test_an_unreadable_today_is_not_written_up_as_a_quiet_inbox():
    """"No emails classified today." for a day nobody could read is the exact
    misreading `ssh_read` was rebuilt to prevent, and it was reaching the
    archived file while stderr said otherwise."""
    today = date(2026, 9, 2)

    out = _render(_empty_dist(today), today, unreachable=[today])

    assert "No emails classified today." not in out, (
        f"the archived report still calls an unreadable day a quiet one:\n"
        f"{out}")
    assert "Today's log could not be read" in out, out


def test_the_saved_report_names_the_days_it_could_not_read():
    """The coverage warning has to be IN the artifact, not only on stderr: the
    file is archived and opened separately from the run that produced it."""
    today = date(2026, 9, 2)
    missing = today - timedelta(days=1)

    out = _render(_empty_dist(today), today, unreachable=[missing])

    assert missing.isoformat() in out, (
        f"the unreadable day is not named anywhere in the saved report:\n{out}")
    assert "lower bound" in out, out


def test_a_genuinely_quiet_day_still_reads_as_quiet():
    """The counter-case. Printing the coverage warning unconditionally would
    pass both tests above and cry wolf on every quiet morning."""
    today = date(2026, 9, 2)

    out = _render(_empty_dist(today), today, unreachable=[])

    assert "No emails classified today." in out, out
    assert "Today's log could not be read" not in out, out
    assert "Incomplete coverage" not in out, out


# ---------------------------------------------------------------------------
# The comment that described the wrong splitter
# ---------------------------------------------------------------------------


def test_the_split_comment_describes_partition_and_not_rpartition():
    """The comment named `rpartition` and the code calls `partition`, and the
    two differ on the very case it discussed: with `partition`, a pattern
    carrying no `@` leaves the WHOLE pattern in `local`, not an empty string."""
    src = (ROOT / "scripts" / "inbox-pulse-report.py").read_text(encoding="utf-8")
    start = src.index("def _pattern_matches_domain")
    body = src[start:src.index("\ndef ", start + 1)]

    # The two false claims themselves, not the word `rpartition`: the corrected
    # comment names the old splitter in order to say it was wrong, which is the
    # opposite of asserting it.
    assert "so a separate emptiness test" not in body, (
        "the comment still calls the `not pat_domain` guard redundant")
    assert "was unreachable" not in body, (
        "the comment still declares a live guard unreachable, which is the "
        "sentence that would get it deleted")
    assert "`partition`" in body, (
        "the comment no longer names the splitter the code actually calls")


def test_a_pattern_with_nothing_after_the_separator_never_covers_a_domain():
    """`"*"` and `"*@"` both pass `local != "*"` and must still be refused.

    Pinned as an OUTCOME, not as "the `not pat_domain` guard is the only thing
    refusing them": measured with that guard removed, both still answer False,
    because `fnmatch(domain, "")` and `fnmatch(domain, "*.")` are both False.
    The corrected comment in the source says exactly that. Asserting the guard
    were load-bearing would be a new false claim in place of the old one.
    """
    assert report._pattern_matches_domain("*", "example.test") is False, (
        "the pattern '*' is being read as covering a domain")
    assert report._pattern_matches_domain("*@", "example.test") is False

    # The real catch-all still gets through, which is the whole point of the
    # surrounding function and the defect it was written to fix.
    assert report._pattern_matches_domain("*@*", "example.test") is True
    assert report._pattern_matches_domain("*@example.test", "example.test") is True
