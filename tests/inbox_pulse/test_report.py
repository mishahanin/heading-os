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
    "sender_domain": "contoso.com",
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
    assert entries[0]["sender_domain"] == "contoso.com"
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


def test_known_domains_from_crm(tmp_path: Path, monkeypatch):
    """Every domain the CRM knows, in BOTH card schemas.

    This set is the report's safety net: its "LOW items from known good domains"
    section exists to surface a contact the classifier under-scored, and a
    domain missing here is instead filed under "Unknown domains" with an
    `Add to always_normal` tuning suggestion beside it. So a blind spot in this
    function does not merely hide a contact, it advises the operator to suppress
    one permanently.

    Until 2026-08-29 the function scanned frontmatter text for a line starting
    `email:` and this test wrote only that shape, so both were blind to the
    entity schema that `/crm` and the migration actually produce. Measured on
    the operator's tree the same day: 89 addresses reachable, 148 real.

    Driven through the real data-root seam rather than by patching a symbol, so
    a later refactor of how the CRM directory is resolved cannot leave this
    green over the wrong tree.
    """
    contacts_dir = tmp_path / "crm" / "contacts"
    book = tmp_path / "crm" / "address-book"
    contacts_dir.mkdir(parents=True)
    book.mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))

    def card(slug, body):
        (contacts_dir / f"{slug}.md").write_text(
            textwrap.dedent(body), encoding="utf-8")

    # Legacy: the address inline on the card.
    card("alice-smith", """\
        ---
        relationship_type: partner
        email: alice@legacy-example.test
        last_touch: 2026-05-01
        status: active
        ---
    """)
    # Entity: the address lives on the address-book record. THE REGRESSION.
    card("carol-nwosu", """\
        ---
        entity_ref: carol-nwosu
        relationship_type: customer
        last_touch: 2026-05-01
        status: active
        ---
    """)
    (book / "carol-nwosu.md").write_text(
        "---\nslug: carol-nwosu\nname: Carol Nwosu\n"
        "canonical_email: carol@entity-example.test\n---\n", encoding="utf-8")
    # Dangling: an entity_ref pointing at nothing. No address exists anywhere,
    # so contributing no domain is correct, and it keeps the assertion below
    # from passing on a function that simply returns every domain it can find.
    card("bob-jones", """\
        ---
        entity_ref: bob-jones
        relationship_type: investor
        last_touch: 2026-05-01
        status: active
        ---
    """)

    domains = _mod.load_known_crm_domains(tmp_path)

    assert domains == {"legacy-example.test", "entity-example.test"}, (
        f"got {sorted(domains)}; the entity-schema contact is the one a text "
        f"scan for `email:` cannot see")


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
# Test 4b/5b: the two suggestion thresholds, with a case ON each line.
#
# Tests 4 and 5 both use SIX entries against a floor of FIVE, so both sit
# BESIDE the boundary and neither is on it. MEASURED 2026-09-01:
#
#     -   SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES = 5
#     +   SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES = 6
#
#     tests/inbox_pulse             -> 226 passed  (baseline: 226 passed)
#     the 45-file wide set + contract -> 7 failed, 1199 passed, 3 skipped
#                                        (identical to baseline)
#
# Six is still >= six, so the off-by-one sailed through. The counts below are
# written as literals rather than derived from the constant on purpose: a test
# that computes its inputs from the very number it is pinning moves with the
# mutation and can never catch it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count,expected", [(4, 0), (5, 1), (6, 1)])
def test_the_always_normal_suggestion_fires_at_five_not_six(count, expected):
    """Five is the documented floor, so five must fire and four must not."""
    suggestions = _mod._compute_suggestions(
        low=[_make_entry("LOW", "spammy.io", weight=0) for _ in range(count)],
        known_crm_domains=set(),
        yaml_overrides={"always_critical": set(), "always_important": set(),
                        "always_normal": set()},
    )
    assert len(suggestions) == expected, (
        f"{count} LOW items produced {len(suggestions)} suggestion(s); the "
        f"always_normal floor is 5 entries")


def test_the_always_normal_suggestion_names_the_constant_it_claims():
    """The literals above are only right while the constant is 5. Pinned here
    rather than by deriving the counts, so a deliberate change to the floor
    fails in ONE obvious place instead of silently re-tuning the test."""
    assert _mod.SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES == 5
    assert _mod.SUGGEST_CRM_KNOWN_LOW_MIN_ENTRIES == 3


# ---------------------------------------------------------------------------
# Test 5c: the OTHER suggestion, which nothing exercised at all.
#
# `_compute_suggestions` has two branches. Every test in this repository drove
# the always_normal one. MEASURED 2026-09-01 by deleting the second branch
# outright, body and all:
#
#     tests/inbox_pulse             -> 226 passed  (baseline: 226 passed)
#     the 45-file wide set + contract -> 7 failed, 1199 passed, 3 skipped
#                                        (identical to baseline)
#
# It could have been removed and nothing would have said so. What it does is
# the report's one actionable nudge for the failure
# `tests/inbox_pulse/test_a_reader_the_entity_migration_never_reached.py`
# documents: a CRM contact the classifier scored at zero. Its sibling branch
# would then propose suppressing that contact's domain permanently, so the two
# branches are the two halves of one decision and only one was held.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count,expected", [(2, 0), (3, 1), (4, 1)])
def test_a_known_crm_domain_stuck_at_low_is_flagged_from_three_entries(
        count, expected):
    """Three is the documented floor for the CRM-miss nudge."""
    suggestions = _mod._compute_suggestions(
        low=[_make_entry("LOW", "nimbus-freight.test", weight=0)
             for _ in range(count)],
        known_crm_domains={"nimbus-freight.test"},
        yaml_overrides={"always_critical": set(), "always_important": set(),
                        "always_normal": set()},
    )
    assert len(suggestions) == expected, (
        f"{count} LOW items from a CRM-known domain produced "
        f"{len(suggestions)} suggestion(s)")
    if expected:
        assert "always_important" in suggestions[0]
        assert "nimbus-freight.test" in suggestions[0]


def test_a_crm_known_domain_is_never_proposed_for_always_normal():
    """The two branches are exclusive, and this is the direction that matters.

    Six LOW items from a domain the CRM knows must NOT produce
    "Add to always_normal": that is the report advising the operator to
    permanently silence a contact the classifier has already failed to
    recognise, which is the compound failure the entity-migration shard
    documented.
    """
    suggestions = _mod._compute_suggestions(
        low=[_make_entry("LOW", "nimbus-freight.test", weight=0) for _ in range(6)],
        known_crm_domains={"nimbus-freight.test"},
        yaml_overrides={"always_critical": set(), "always_important": set(),
                        "always_normal": set()},
    )
    assert len(suggestions) == 1
    assert "always_normal" not in suggestions[0], suggestions[0]


def test_a_domain_with_a_live_signal_is_not_suggested_at_all():
    """Anchor for both branches. Each is gated on `not any_signal`, and a
    version that ignored that would suggest tuning away traffic the classifier
    is already scoring on purpose."""
    scored = _make_entry("LOW", "nimbus-freight.test", weight=0,
                         breakdown={"sender_override": None,
                                    "keyword_override": None,
                                    "crm_contact": 1, "pipeline": 0,
                                    "threads": 0, "calendar": 0,
                                    "time_sensitivity": 0})
    for known in (set(), {"nimbus-freight.test"}):
        assert _mod._compute_suggestions(
            low=[scored for _ in range(6)],
            known_crm_domains=known,
            yaml_overrides={"always_critical": set(),
                            "always_important": set(),
                            "always_normal": set()},
        ) == []


# ---------------------------------------------------------------------------
# Test 1b: a JSONL line that will not parse is reported, not silently dropped
# ---------------------------------------------------------------------------


def _stub_ssh_read(monkeypatch_target: list, lines: list[str]):
    """Drive `fetch_jsonl_for_date` through the module's own remote-read seam.

    Test 1 above reaches one layer lower and patches `subprocess.run`, which is
    the STDLIB object: `_mod.subprocess` is not a private copy, so that patch is
    process-wide for its duration. It is restored on exit and it has not bitten
    anyone here, but there is no reason for a new test to take the risk when the
    function under test calls `ssh_read` by name and `ssh_read` is the seam.

    The stub also records the path it was given instead of discarding it, so the
    caller can assert which day was actually read.
    """
    real = _mod.ssh_read
    try:
        _mod.ssh_read = lambda remote_path: (
            monkeypatch_target.append(remote_path) or "\n".join(lines))
        return _mod.fetch_jsonl_for_date(date(2026, 5, 29))
    finally:
        _mod.ssh_read = real


def test_an_unparseable_jsonl_line_is_counted_and_reported(capsys):
    """Test 1 above pins the SKIPPING and says nothing about the silence.

    Every number this report prints comes from the surviving rows: the headline
    "Total emails classified", the tier split, the per-domain counts, and the
    thresholds the two suggestion branches above are measured against. A
    dropped row moves all of them, and until 2026-09-01 it moved them with
    nothing written anywhere. The report already shouts about a day it could
    not REACH, in red, for exactly this reason; a day it could not fully READ
    was the quiet case.

    The blank line must NOT be counted: it carries no record, so reporting it
    would train the operator to ignore the warning.
    """
    reads: list[str] = []
    entries = _stub_ssh_read(monkeypatch_target=reads, lines=[
        json.dumps(_SAMPLE_ENTRY_HIGH),
        "",                       # blank: skipped, not a loss
        "not-valid-json",         # a lost record
        '{"tier_guess": "LOW"',   # a torn line, also a lost record
        json.dumps(_SAMPLE_ENTRY_LOW),
    ])

    assert len(entries) == 2
    # The stub RECORDS the path it was handed rather than discarding it, so a
    # `fetch_jsonl_for_date` that read the wrong day cannot pass here.
    assert reads == [f"{_mod.VM_STATE_DIR}/log-2026-05-29.jsonl"], reads

    err = capsys.readouterr().err
    assert "2 unparseable line(s)" in err, (
        f"the two lost records were dropped without a word: {err!r}")
    assert "log-2026-05-29.jsonl" in err, (
        f"the warning does not name the day that lost rows: {err!r}")
    assert "LOWER bound" in err, err


def test_a_clean_day_produces_no_unparseable_warning(capsys):
    """Anchor. A warning printed unconditionally is a warning nobody reads."""
    reads: list[str] = []
    entries = _stub_ssh_read(monkeypatch_target=reads, lines=[
        json.dumps(_SAMPLE_ENTRY_HIGH), "", json.dumps(_SAMPLE_ENTRY_LOW)])

    assert len(entries) == 2
    assert reads, "the stub was never called, so nothing was parsed"
    assert "unparseable" not in capsys.readouterr().err


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

    # Assert the POSITIVE outcome, not the absence of a word.
    #
    # This was a three-way `or` until 2026-08-31:
    #
    #     assert ("hidden" not in stdout_lower
    #             or "0 hidden" in stdout_lower
    #             or result.returncode == 0)
    #
    # The first clause is satisfied by EMPTY stdout, so the whole expression was
    # True whenever the scanner failed to run at all. MEASURED that day: pointed
    # at a path that does not exist, `sanitize-text.py --scan` exits 2 and prints
    # its error to stderr, leaving stdout empty, and the assertion passed. The
    # third clause was worse on its own: `returncode == 0` alone let any stdout
    # through, so a scan that reported hidden characters AND exited 0 would also
    # have passed.
    #
    # The scanner's real contract, measured the same day:
    #   clean file : rc 0, stdout "<path>: Clean - no hidden characters found."
    #   dirty file : rc 1, stdout "<path>: Found N hidden character(s):"
    #   unreadable : rc 2, stdout EMPTY, message on stderr
    #
    # So the check is now both halves of the clean outcome, and nothing else can
    # satisfy it.
    assert result.returncode == 0, (
        f"the hidden-character scan did not report a clean file "
        f"(rc={result.returncode}). stdout: {result.stdout!r} "
        f"stderr: {result.stderr!r}"
    )
    assert "clean - no hidden characters found" in result.stdout.lower(), (
        f"the scan exited 0 without saying the file is clean, so this test "
        f"cannot tell a clean report from a scan that never examined it. "
        f"stdout: {result.stdout!r}"
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
