"""Four CRM defects that all answer a narrower question than they were asked.

`parse_aliases` was told to read the `## Aliases` section and read the rest of
the file with it: the in-section flag was set on the heading and never cleared,
so every `### ` name and every `- ` bullet below any LATER `## ` heading became
a live alias. Those feed `compute_stage_aware_cadence` and the stage resolution
in `scan_contacts`, so a retired company name can hand a contact the wrong
cadence, or a Won/Lost zero-cadence parking that stops tracking it entirely.

`is_radar_frozen` was told a freeze runs until an instant and compared dates:
`.date()` on the parsed datetime threw the time away and rounded DOWN, so a
freeze until 18:00 read as expired from midnight. That is the fail-OPEN
direction the function's own docstring calls the dangerous one - a message to
someone who was explicitly frozen.

`_cadence_override` was told to reject an unusable cadence and rejected only
unparseable ones: `cadence: -30`, the ordinary typo for `30`, made
`calculate_health` return red for `days_since >= -30`, which is every value
`last_touch` can hold.

`try_commit` was told to turn a commit failure into a boolean and called
`.decode()` on a `stderr` that is `str` whenever the injected `commit_fn`
captured with `text=True` - raising `AttributeError` from inside the handler and
skipping the caller's INCOMPLETE path.

The date here is a literal. Nothing reads the host clock.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import crm

TODAY = date(2026, 8, 27)


# --------------------------------------------------------------------------
# parse_aliases
# --------------------------------------------------------------------------

ALIASES_WITH_A_TRAILING_SECTION = """\
# Company aliases

## Aliases

### Spectre Telecom
- spectre
- spectre telecom ltd

## Retired

### Universal Exports
- universal exports plc

## Notes

### Naming convention
- lowercase everything
"""


@pytest.fixture
def aliases_file(tmp_path):
    path = tmp_path / "aliases.md"
    path.write_text(ALIASES_WITH_A_TRAILING_SECTION, encoding="utf-8")
    return path


def test_the_aliases_section_ends_at_the_next_heading(aliases_file):
    """The defect: `## Retired` and `## Notes` were ingested as live aliases."""
    aliases = crm.parse_aliases(aliases_file)

    assert aliases == {
        "spectre telecom": "spectre telecom",
        "spectre": "spectre telecom",
        "spectre telecom ltd": "spectre telecom",
    }


@pytest.mark.parametrize(
    "leaked",
    ["universal exports", "universal exports plc", "naming convention",
     "lowercase everything"],
)
def test_a_name_below_a_later_heading_is_not_an_alias(aliases_file, leaked):
    assert leaked not in crm.parse_aliases(aliases_file)


def test_a_bullet_after_a_new_heading_does_not_inherit_the_previous_canonical(tmp_path):
    """The canonical resets at the boundary, so a stray bullet cannot attach.

    Without the reset, a bullet sitting under `## Retired` before its own `### `
    heading mapped onto the LAST canonical seen inside `## Aliases`.
    """
    path = tmp_path / "aliases.md"
    path.write_text(
        "## Aliases\n### Spectre Telecom\n- spectre\n\n## Retired\n- quantum\n",
        encoding="utf-8",
    )
    assert "quantum" not in crm.parse_aliases(path)


def test_a_reentered_aliases_section_does_not_inherit_the_earlier_canonical(tmp_path):
    """The case only the `current_canonical = None` reset can catch.

    The test above it looks like the reset's binding case and is not: at
    `## Retired` the section flag is already False, so the bullet is dropped by
    the flag test whichever way the reset went. The reset earns its place only
    when the section is ENTERED again, which is what a file carrying two
    `## Aliases` headings does. Measured 2026-09-01: deleting the reset left
    all 47 tests over `parse_aliases` green, and this corpus returns
    `{'stray': 'spectre telecom'}` without it.
    """
    path = tmp_path / "aliases.md"
    path.write_text(
        "## Aliases\n### Spectre Telecom\n- spectre\n\n"
        "## Retired\n\n"
        "## Aliases\n- stray\n",
        encoding="utf-8",
    )
    aliases = crm.parse_aliases(path)

    assert "stray" not in aliases, (
        f"a bullet under a re-entered heading attached to the previous "
        f"canonical: {aliases}"
    )
    assert aliases["spectre"] == "spectre telecom"


def test_a_file_that_is_only_the_aliases_section_is_unchanged(tmp_path):
    """The fix must not narrow the section it was always meant to read."""
    path = tmp_path / "aliases.md"
    path.write_text(
        "## Aliases\n### Spectre Telecom\n- spectre\n- spectre ltd\n",
        encoding="utf-8",
    )
    assert crm.parse_aliases(path) == {
        "spectre telecom": "spectre telecom",
        "spectre": "spectre telecom",
        "spectre ltd": "spectre telecom",
    }


def test_content_above_the_aliases_heading_is_still_skipped(tmp_path):
    path = tmp_path / "aliases.md"
    path.write_text(
        "## Preamble\n### Ghost Co\n- ghost\n\n## Aliases\n### Spectre Telecom\n- spectre\n",
        encoding="utf-8",
    )
    aliases = crm.parse_aliases(path)
    assert "ghost" not in aliases
    assert aliases["spectre"] == "spectre telecom"


# --------------------------------------------------------------------------
# is_radar_frozen
# --------------------------------------------------------------------------

def test_a_freeze_until_an_afternoon_covers_that_whole_day():
    """The defect: this returned False from midnight, 18 hours early."""
    assert crm.is_radar_frozen("2026-08-27T18:00:00", today=TODAY) is True


def test_a_freeze_until_an_afternoon_expires_the_following_day():
    """Rounding up must not extend the freeze indefinitely."""
    assert crm.is_radar_frozen(
        "2026-08-27T18:00:00", today=TODAY + timedelta(days=1)
    ) is False


@pytest.mark.parametrize(
    "raw",
    ["2026-08-27T18:00:00", "2026-08-27T18:00:00Z", "2026-08-27T18:00:00+04:00",
     "2026-08-27T00:00:01", "2026-08-27T23:59:59"],
)
def test_any_time_of_day_holds_the_freeze_through_its_final_day(raw):
    assert crm.is_radar_frozen(raw, today=TODAY) is True


@pytest.mark.parametrize(
    "raw", ["2026-08-27T00:00:00", "2026-08-27T00:00:00Z", "2026-08-27"]
)
def test_a_freeze_that_lands_on_midnight_is_over_for_that_day(raw):
    """Midnight is genuinely expired, so it must not be rounded up.

    A date-only value and an explicit `T00:00:00` mean the same instant and had
    better answer the same way, or the fix has invented a distinction.
    """
    assert crm.is_radar_frozen(raw, today=TODAY) is False


def test_a_date_only_freeze_in_the_future_still_freezes():
    assert crm.is_radar_frozen("2026-08-28", today=TODAY) is True


def test_an_unparseable_freeze_still_fails_closed(capsys):
    assert crm.is_radar_frozen("next tuesday", today=TODAY) is True
    assert "not an ISO date" in capsys.readouterr().err


def test_an_absent_freeze_is_not_a_freeze():
    for raw in (None, "", "   "):
        assert crm.is_radar_frozen(raw, today=TODAY) is False


# --------------------------------------------------------------------------
# _cadence_override
# --------------------------------------------------------------------------

def test_a_negative_cadence_is_rejected_rather_than_honoured(capsys):
    """The defect: `-30` became `red = -30`, red for every possible last_touch."""
    assert crm._cadence_override("-30", "james-bond.md") is None
    assert "negative" in capsys.readouterr().err


def test_a_contact_touched_yesterday_is_not_red_under_a_typo_cadence():
    """The outcome the operator saw: a fresh contact pinned to the radar.

    The override is refused, so the caller's type default decides - and a
    contact touched yesterday under a 30-day default is green.
    """
    assert crm._cadence_override("-30", "james-bond.md") is None
    health, days = crm.calculate_health(
        "2026-08-26", 30, 21, 30, today=TODAY
    )
    assert (health, days) == ("green", 1)


def test_zero_still_means_no_tracking():
    """Zero was always meaningful; only below zero is rejected."""
    assert crm._cadence_override("0", "james-bond.md") == 0


@pytest.mark.parametrize("raw,expected", [("30", 30), (" 45 ", 45), ("1", 1)])
def test_a_usable_cadence_is_untouched(raw, expected):
    assert crm._cadence_override(raw, "james-bond.md") == expected


def test_an_unparseable_cadence_still_falls_back(capsys):
    assert crm._cadence_override("7 days", "james-bond.md") is None
    assert "not a whole" in capsys.readouterr().err


# --------------------------------------------------------------------------
# try_commit
# --------------------------------------------------------------------------

def _failing(stderr):
    def commit_fn(repo, files, message):
        raise subprocess.CalledProcessError(1, "git", stderr=stderr)

    return commit_fn


def test_a_text_mode_commit_failure_returns_false_instead_of_raising(capsys):
    """The defect: `.decode()` on a str stderr raised from inside the handler."""
    assert crm.try_commit(
        _failing("fatal: could not read Username\n"), Path("."), [], "m", "target"
    ) is False
    assert "could not read Username" in capsys.readouterr().out


def test_a_bytes_mode_commit_failure_still_reports_its_reason(capsys):
    assert crm.try_commit(
        _failing(b"fatal: nothing to commit\n"), Path("."), [], "m", "source"
    ) is False
    assert "nothing to commit" in capsys.readouterr().out


@pytest.mark.parametrize("stderr", [None, "", b"", "   "])
def test_a_failure_with_no_stderr_still_returns_false(stderr):
    assert crm.try_commit(
        _failing(stderr), Path("."), [], "m", "target"
    ) is False


def test_a_commit_that_lands_returns_true():
    calls = []
    assert crm.try_commit(
        lambda repo, files, message: calls.append(message),
        Path("."), [], "landed", "target",
    ) is True
    assert calls == ["landed"]
