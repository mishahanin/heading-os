"""Tests for scripts/inbox-pulse-report.py.

Seven tests covering:
1. test_parses_jsonl_correctly: sample JSONL bytes parse to correct dict structure.
2. test_groups_by_tier: 3 HIGH_LIKELY + 5 MAYBE + 10 LOW -> counts correct.
3. test_known_domains_from_crm: CRM md with email frontmatter -> domain extracted.
4. test_tuning_suggestion_always_normal_for_high_volume_low_signal: 6 LOW unknown domain -> always_normal suggestion.
5. test_tuning_suggestion_skipped_when_already_in_yaml: domain already in always_normal -> no suggestion.
6. test_renders_markdown_without_hidden_chars: render report, sanitize-text scan returns clean.
7. test_handles_empty_jsonl_gracefully: 0 entries -> no crash, total == 0.

Uses monkeypatch + tmp_path to avoid real SSH or workspace side effects.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import textwrap
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

# Scripts use a workspace-relative sys.path insert; replicate it here.
_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_WORKSPACE_ROOT))

# The script filename contains a hyphen so we must load it via importlib.
import importlib.util as _ilu

_SCRIPT_PATH = _WORKSPACE_ROOT / "scripts" / "inbox-pulse-report.py"


def _load_module() -> ModuleType:
    spec = _ilu.spec_from_file_location("inbox_pulse_report", _SCRIPT_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()


# ---------------------------------------------------------------------------
# Sample JSONL fixtures
# ---------------------------------------------------------------------------

_SAMPLE_ENTRY_HIGH: dict[str, Any] = {
    "ts": "2026-05-29T09:12:00.000000+04:00",
    "event_type": "NewMail",
    "message_id": "AAMkHigh==",
    "sender_domain": "northgate.com",
    "subject_length": 45,
    "mode": "shadow",
    "tier_guess": "HIGH_LIKELY",
    "weight": 5,
    "reason_breakdown": {
        "sender_override": "always_critical",
        "keyword_override": None,
        "crm_contact": 0,
        "pipeline": 0,
        "threads": 0,
        "calendar": 0,
        "time_sensitivity": 0,
    },
}

_SAMPLE_ENTRY_MAYBE: dict[str, Any] = {
    "ts": "2026-05-29T11:00:00.000000+04:00",
    "event_type": "NewMail",
    "message_id": "AAMkMaybe==",
    "sender_domain": "stripe.com",
    "subject_length": 30,
    "mode": "shadow",
    "tier_guess": "MAYBE",
    "weight": 2,
    "reason_breakdown": {
        "sender_override": None,
        "keyword_override": None,
        "crm_contact": 1,
        "pipeline": 1,
        "threads": 0,
        "calendar": 0,
        "time_sensitivity": 0,
    },
}

_SAMPLE_ENTRY_LOW: dict[str, Any] = {
    "ts": "2026-05-29T14:00:00.000000+04:00",
    "event_type": "NewMail",
    "message_id": "AAMkLow==",
    "sender_domain": "newsletter.example.com",
    "subject_length": 20,
    "mode": "shadow",
    "tier_guess": "LOW",
    "weight": 0,
    "reason_breakdown": {
        "sender_override": None,
        "keyword_override": None,
        "crm_contact": 0,
        "pipeline": 0,
        "threads": 0,
        "calendar": 0,
        "time_sensitivity": 0,
    },
}


def _make_entry(tier: str, domain: str, weight: int = 0, breakdown: dict | None = None) -> dict[str, Any]:
    bd = breakdown or {
        "sender_override": None,
        "keyword_override": None,
        "crm_contact": 0,
        "pipeline": 0,
        "threads": 0,
        "calendar": 0,
        "time_sensitivity": 0,
    }
    return {
        "ts": "2026-05-29T10:00:00.000000+04:00",
        "event_type": "NewMail",
        "message_id": f"AAMk{tier}{domain}==",
        "sender_domain": domain,
        "subject_length": 20,
        "mode": "shadow",
        "tier_guess": tier,
        "weight": weight,
        "reason_breakdown": bd,
    }


# ---------------------------------------------------------------------------
# Test 1: JSONL parsing
# ---------------------------------------------------------------------------


def test_parses_jsonl_correctly():
    """fetch_jsonl_for_date correctly parses valid JSONL, skips blank + invalid lines."""
    sample_lines = [
        json.dumps(_SAMPLE_ENTRY_HIGH),
        "",  # blank line - skip
        "not-valid-json",  # bad line - skip
        json.dumps(_SAMPLE_ENTRY_LOW),
    ]
    raw_output = "\n".join(sample_lines)

    with patch.object(_mod.subprocess, "run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = raw_output
        mock_run.return_value = mock_proc

        entries = _mod.fetch_jsonl_for_date(date(2026, 5, 29))

    assert len(entries) == 2
    assert entries[0]["tier_guess"] == "HIGH_LIKELY"
    assert entries[0]["sender_domain"] == "northgate.com"
    assert entries[1]["tier_guess"] == "LOW"
    assert entries[1]["sender_domain"] == "newsletter.example.com"


# ---------------------------------------------------------------------------
# Test 2: Grouping by tier
# ---------------------------------------------------------------------------


def test_groups_by_tier():
    """aggregate() correctly counts entries per tier."""
    entries: list[dict[str, Any]] = []
    for _ in range(3):
        entries.append(_make_entry("HIGH_LIKELY", "high-domain.com", weight=5))
    for _ in range(5):
        entries.append(_make_entry("MAYBE", "maybe-domain.com", weight=2))
    for _ in range(10):
        entries.append(_make_entry("LOW", "low-domain.com", weight=0))

    today = date(2026, 5, 29)
    all_entries_by_date = {today: entries}

    agg = _mod.aggregate(
        entries=entries,
        today=today,
        days=1,
        all_entries_by_date=all_entries_by_date,
        known_crm_domains=set(),
        yaml_overrides={"always_critical": set(), "always_important": set(), "always_normal": set()},
    )

    assert len(agg["high"]) == 3
    assert len(agg["maybe"]) == 5
    assert len(agg["low"]) == 10
    assert agg["total"] == 18


# ---------------------------------------------------------------------------
# Test 3: Known domains from CRM
# ---------------------------------------------------------------------------


def test_known_domains_from_crm(tmp_path: Path):
    """load_known_crm_domains extracts email domains from CRM YAML frontmatter."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)

    # Write a contact file with an email field
    (contacts_dir / "alice-smith.md").write_text(
        textwrap.dedent("""\
            ---
            entity_ref: alice-smith
            relationship_type: partner
            email: alice@example.com
            last_touch: 2026-05-01
            created: 2026-04-01
            status: active
            ---

            # Alice Smith

            ## Interaction Log
        """),
        encoding="utf-8",
    )
    # Write a contact with no email
    (contacts_dir / "bob-jones.md").write_text(
        textwrap.dedent("""\
            ---
            entity_ref: bob-jones
            relationship_type: investor
            last_touch: 2026-05-01
            status: active
            ---
        """),
        encoding="utf-8",
    )

    # load_known_crm_domains resolves the CRM dir via get_crm_contacts_dir()
    # (data-root seam), so patch that to the fixture's contacts dir.
    with patch.object(_mod, "get_crm_contacts_dir", return_value=contacts_dir):
        domains = _mod.load_known_crm_domains(tmp_path)

    assert "example.com" in domains
    assert len(domains) == 1  # bob has no email


# ---------------------------------------------------------------------------
# Test 4: Suggestion - always_normal for high-volume LOW unknown domain
# ---------------------------------------------------------------------------


def test_tuning_suggestion_always_normal_for_high_volume_low_signal():
    """6 LOW entries from an unknown domain with no breakdown signal -> always_normal suggestion."""
    low_entries = [
        _make_entry("LOW", "spammy.io", weight=0)
        for _ in range(6)
    ]
    # No breakdown signal fires (all zeros in _make_entry default)

    suggestions = _mod._compute_suggestions(
        low=low_entries,
        known_crm_domains=set(),
        yaml_overrides={"always_critical": set(), "always_important": set(), "always_normal": set()},
    )

    assert len(suggestions) == 1
    assert "always_normal" in suggestions[0]
    assert "spammy.io" in suggestions[0]


# ---------------------------------------------------------------------------
# Test 5: Suggestion suppressed when domain already in YAML always_normal
# ---------------------------------------------------------------------------


def test_tuning_suggestion_skipped_when_already_in_yaml():
    """6 LOW entries from a domain already in always_normal -> no suggestion generated."""
    low_entries = [
        _make_entry("LOW", "noreply.com", weight=0)
        for _ in range(6)
    ]

    yaml_overrides = {
        "always_critical": set(),
        "always_important": set(),
        "always_normal": {"*@noreply.com"},
    }

    suggestions = _mod._compute_suggestions(
        low=low_entries,
        known_crm_domains=set(),
        yaml_overrides=yaml_overrides,
    )

    assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Test 6: Rendered markdown is free of hidden characters
# ---------------------------------------------------------------------------


def test_renders_markdown_without_hidden_chars(tmp_path: Path):
    """render_report produces markdown that passes the hidden-char sanitizer."""
    today = date(2026, 5, 29)
    entries = [
        _make_entry("HIGH_LIKELY", "partner.com", weight=4),
        _make_entry("MAYBE", "prospect.com", weight=2),
        _make_entry("LOW", "newsletter.org", weight=0),
    ]
    all_entries_by_date = {today: entries}

    agg = _mod.aggregate(
        entries=entries,
        today=today,
        days=1,
        all_entries_by_date=all_entries_by_date,
        known_crm_domains=set(),
        yaml_overrides={"always_critical": set(), "always_important": set(), "always_normal": set()},
    )

    report_md = _mod.render_report(
        agg=agg,
        today=today,
        days=1,
        window_start=today,
        state_json={"last_heartbeat": "2026-05-29T09:00:00+04:00", "daemon_pid": 12345},
        entries_total_in_window=len(entries),
    )

    # Write to tmp file and run sanitize-text --scan
    out_file = tmp_path / "test-report.md"
    out_file.write_text(report_md, encoding="utf-8")

    sanitizer = _WORKSPACE_ROOT / "scripts" / "sanitize-text.py"
    result = subprocess.run(
        [sys.executable, str(sanitizer), str(out_file), "--scan"],
        capture_output=True,
        text=True,
    )

    # sanitize-text --scan exits 0 when clean (or when file has no hidden chars)
    stdout_lower = result.stdout.lower()
    assert "hidden" not in stdout_lower or "0 hidden" in stdout_lower or result.returncode == 0, (
        f"Hidden character scan found issues: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 7: Empty JSONL handled gracefully
# ---------------------------------------------------------------------------


def test_handles_empty_jsonl_gracefully():
    """aggregate() with zero entries does not crash and total == 0."""
    today = date(2026, 5, 29)
    entries: list[dict[str, Any]] = []
    all_entries_by_date: dict[date, list[dict[str, Any]]] = {today: []}

    agg = _mod.aggregate(
        entries=entries,
        today=today,
        days=1,
        all_entries_by_date=all_entries_by_date,
        known_crm_domains=set(),
        yaml_overrides={"always_critical": set(), "always_important": set(), "always_normal": set()},
    )

    assert agg["total"] == 0
    assert agg["high"] == []
    assert agg["maybe"] == []
    assert agg["low"] == []
    assert agg["suggestions"] == []

    # render_report should not crash with empty data
    report_md = _mod.render_report(
        agg=agg,
        today=today,
        days=1,
        window_start=today,
        state_json={},
        entries_total_in_window=0,
    )
    assert "Total emails classified: 0" in report_md
