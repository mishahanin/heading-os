"""Tests for the dormancy detector."""

from datetime import date


def test_find_dormancy_candidates_silent_over_90_days():
    from scripts.utils.crm import find_dormancy_candidates
    today = date(2026, 5, 15)
    contacts = [
        {"slug": "old-silent", "type": "prospect", "last_touch": "2026-01-01",
         "status": "active"},  # ~135 days
        {"slug": "recent", "type": "prospect", "last_touch": "2026-05-10",
         "status": "active"},  # 5 days
        {"slug": "tribe-old", "type": "tribe", "last_touch": "2025-12-01",
         "status": "active"},  # tribe excluded
        {"slug": "already-dormant", "type": "prospect", "last_touch": "2026-01-01",
         "status": "dormant"},  # already demoted
        {"slug": "won", "type": "customer", "last_touch": "2026-01-01",
         "status": "won"},  # won is excluded
    ]
    candidates = find_dormancy_candidates(contacts, today=today, threshold_days=90)
    assert len(candidates) == 1
    assert candidates[0]["slug"] == "old-silent"


def test_find_dormancy_candidates_custom_threshold():
    from scripts.utils.crm import find_dormancy_candidates
    today = date(2026, 5, 15)
    contacts = [
        {"slug": "silent-65", "type": "prospect", "last_touch": "2026-03-11", "status": "active"},
    ]
    cands = find_dormancy_candidates(contacts, today=today, threshold_days=60)
    assert len(cands) == 1
    cands_90 = find_dormancy_candidates(contacts, today=today, threshold_days=90)
    assert len(cands_90) == 0  # 65 days < 90


# ----------------------------------------------------------- the bound itself
#
# Every case above sits well clear of the threshold (65 against 60, 134 against
# 90), so the comparison could be `>=` or `>` and no test above could tell.
# Measured 2026-09-01: changing `delta >= threshold_days` to `delta >` left the
# whole repository green. The three cases below are ON the line and one either
# side of it, which is the only shape that pins a bound.

def test_a_contact_silent_for_exactly_the_threshold_is_a_candidate():
    from scripts.utils.crm import find_dormancy_candidates
    today = date(2026, 5, 15)
    # 2026-02-14 is exactly 90 days before 2026-05-15.
    contacts = [{"slug": "exactly-90", "type": "prospect",
                 "last_touch": "2026-02-14", "status": "active"}]
    assert (today - date(2026, 2, 14)).days == 90, "fixture arithmetic drifted"
    assert [c["slug"] for c in
            find_dormancy_candidates(contacts, today=today, threshold_days=90)] \
        == ["exactly-90"]


def test_one_day_short_of_the_threshold_is_not_a_candidate():
    from scripts.utils.crm import find_dormancy_candidates
    today = date(2026, 5, 15)
    contacts = [{"slug": "eighty-nine", "type": "prospect",
                 "last_touch": "2026-02-15", "status": "active"}]
    assert (today - date(2026, 2, 15)).days == 89
    assert find_dormancy_candidates(contacts, today=today, threshold_days=90) == []


def test_a_contact_with_no_last_touch_is_never_a_candidate():
    """A blank `last_touch` is "we have no record", not "silent since the epoch".
    Reading it as an infinitely old date would sweep every never-contacted card
    into an auto-demote batch the operator is asked to approve in one click."""
    from scripts.utils.crm import find_dormancy_candidates
    today = date(2026, 5, 15)
    contacts = [
        {"slug": "no-touch-key", "type": "prospect", "status": "active"},
        {"slug": "blank-touch", "type": "prospect", "last_touch": "", "status": "active"},
        {"slug": "unparseable", "type": "prospect", "last_touch": "last spring",
         "status": "active"},
    ]
    assert find_dormancy_candidates(contacts, today=today, threshold_days=90) == []
