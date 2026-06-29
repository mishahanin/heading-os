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
