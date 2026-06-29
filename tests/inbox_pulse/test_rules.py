"""Tests for scripts/inbox_pulse/rules.py (CheapClassifier).

All tests use pytest tmp_path to avoid touching the real workspace.
A shared make_workspace fixture creates the expected directory tree.
Most tests construct a RulesEngine against a minimal rules.yaml and
pass it to CheapClassifier.

Coverage targets (20 tests):
 1. always_critical sender short-circuits to HIGH_LIKELY
 2. always_normal sender short-circuits to LOW
 3. promote_to_critical keyword short-circuits to HIGH_LIKELY
 4. promote_to_important keyword adds weight 3 -> MAYBE
 5. no signals -> weight 0 -> LOW
 6. CRM contact match (no relationship) adds 1 -> LOW
 7. CRM tribe relationship adds 3 -> MAYBE
 8. CRM customer relationship adds 3 -> MAYBE
 9. pipeline.md domain match adds 2 -> MAYBE
10. threads recent mention adds 1 -> LOW (alone)
11. threads old mention ignored -> 0
12. time-sensitivity regex in subject adds 1
13. time-sensitivity regex in body-only adds 1
14. body_preview truncated to 500 chars (deadline past char 500 not detected)
15. combined signals: CRM tribe(3) + pipeline(2) = 5 -> HIGH_LIKELY
16. combined signals: CRM contact(1) + time-sensitivity(1) = 2 -> MAYBE
17. calendar skipped when account=None -> calendar=0
18. calendar exception silently returns 0
19. missing crm/contacts/ dir handled gracefully
20. returned dict has all required keys
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared YAML for RulesEngine
# ---------------------------------------------------------------------------

_RULES_YAML = textwrap.dedent("""\
    sender_overrides:
      always_critical:
        - critical@example.com
      always_important:
        - important@example.com
      always_normal:
        - "noreply@*"

    keyword_overrides:
      promote_to_critical:
        - "term sheet"
        - "series b"
      promote_to_important:
        - "deadline"
        - "by friday"

    quiet_hours:
      start: "23:00"
      end: "07:00"
      timezone: "Etc/GMT-4"

    breakthrough_allowlist: []

    internal_domains:
      - "31c.io"

    cost_ceiling:
      monthly_anthropic_usd: 50
      warn_at_percent: 80
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_workspace(tmp_path: Path) -> Path:
    """Create the minimal workspace directory tree and return the root."""
    (tmp_path / "crm" / "contacts").mkdir(parents=True)
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "threads" / "business").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def rules_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(_RULES_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def rules_engine(rules_yaml: Path):
    from scripts.inbox_pulse.overrides import RulesEngine
    return RulesEngine(yaml_path=rules_yaml)


def _make_classifier(rules_engine, workspace_root: Path, account=None):
    from scripts.inbox_pulse.rules import CheapClassifier
    return CheapClassifier(
        rules=rules_engine,
        workspace_root=workspace_root,
        account=account,
    )


def _fixed_now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def _write_crm_contact(
    contacts_dir: Path,
    slug: str,
    email: str,
    relationship_type: str = "lead",
) -> None:
    content = textwrap.dedent(f"""\
        ---
        entity_ref: {slug}
        relationship_type: {relationship_type}
        email: {email}
        last_touch: 2026-05-28
        created: 2026-05-01
        status: active
        tags: []
        ---

        # {slug}
    """)
    (contacts_dir / f"{slug}.md").write_text(content, encoding="utf-8")


def _write_thread(
    threads_dir: Path,
    slug: str,
    last_touched: str,
    body_extra: str = "",
) -> None:
    content = textwrap.dedent(f"""\
        ---
        id: {slug}
        title: Test Thread
        status: active
        type: business
        classification: ceo-only
        opened: '2026-05-01'
        last_touched: '{last_touched}'
        counterparties: []
        ---

        # Test Thread

        {body_extra}
    """)
    (threads_dir / f"{slug}.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_classify_always_critical_sender_short_circuits(rules_engine, make_workspace):
    """1. always_critical sender -> HIGH_LIKELY, weight=99, breakdown correct."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="critical@example.com",
        subject="Hello",
        now=_fixed_now(),
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["weight"] == 99
    assert result["reason_breakdown"]["sender_override"] == "always_critical"


def test_classify_always_normal_sender_short_circuits(rules_engine, make_workspace):
    """2. always_normal sender -> LOW, weight=0."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="noreply@anyservice.com",
        subject="Your receipt",
        now=_fixed_now(),
    )
    assert result["tier_guess"] == "LOW"
    assert result["weight"] == 0
    assert result["reason_breakdown"]["sender_override"] == "always_normal"


def test_classify_promote_to_critical_keyword_short_circuits(rules_engine, make_workspace):
    """3. Subject with 'term sheet' -> HIGH_LIKELY (no sender match needed)."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="random@example.org",
        subject="Re: term sheet attached",
        now=_fixed_now(),
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["weight"] == 99
    assert result["reason_breakdown"]["keyword_override"] == "promote_to_critical"


def test_classify_promote_to_important_adds_weight_3(rules_engine, make_workspace, tmp_path):
    """4. A custom rules.yaml with a promote_to_important keyword that does NOT
    overlap with the time-sensitivity regex ('by friday') confirms the +3 weight.

    We use a keyword 'custom-flag-xyz' that only appears in promote_to_important
    and has no overlap with the time-sensitivity regex, giving a clean weight=3.
    """
    import textwrap as tw
    custom_yaml = tmp_path / "custom_rules.yaml"
    custom_yaml.write_text(tw.dedent("""\
        sender_overrides:
          always_critical: []
          always_important: []
          always_normal: []
        keyword_overrides:
          promote_to_critical: []
          promote_to_important:
            - "custom-flag-xyz"
        quiet_hours:
          start: "23:00"
          end: "07:00"
          timezone: "Etc/GMT-4"
        breakthrough_allowlist: []
        cost_ceiling:
          monthly_anthropic_usd: 50
          warn_at_percent: 80
    """), encoding="utf-8")
    from scripts.inbox_pulse.overrides import RulesEngine
    eng = RulesEngine(yaml_path=custom_yaml)
    clf = _make_classifier(eng, make_workspace)
    result = clf.classify(
        sender_email="random@example.org",
        subject="This email has custom-flag-xyz in it",
        now=_fixed_now(),
    )
    assert result["tier_guess"] == "MAYBE"
    assert result["weight"] == 3
    assert result["reason_breakdown"]["keyword_override"] == "promote_to_important"
    assert result["reason_breakdown"]["time_sensitivity"] == 0


def test_classify_no_signals_returns_low(rules_engine, make_workspace):
    """5. Unknown sender, generic subject, no CRM/pipeline/thread match -> LOW."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="someone@unknown.org",
        subject="Hello there",
        now=_fixed_now(),
    )
    assert result["tier_guess"] == "LOW"
    assert result["weight"] == 0


def test_classify_crm_contact_match_adds_1(rules_engine, make_workspace):
    """6. CRM contact found (no high-value relationship) -> +1 -> weight=1 -> LOW."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="joe-smith",
        email="joe@example.org",
        relationship_type="lead",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="joe@example.org",
        subject="Hi",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 1
    assert result["weight"] == 1
    assert result["tier_guess"] == "LOW"


def test_classify_crm_tribe_relationship_adds_3(rules_engine, make_workspace):
    """7. CRM contact with relationship_type=tribe -> +3 -> weight=3 -> MAYBE."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="tribe-member",
        email="ivan@31c.io",
        relationship_type="tribe",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="ivan@31c.io",
        subject="Quick question",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 3
    assert result["weight"] == 3
    assert result["tier_guess"] == "MAYBE"


def test_classify_crm_customer_relationship_adds_3(rules_engine, make_workspace):
    """8. CRM contact with relationship_type=customer -> +3 -> MAYBE."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="big-customer",
        email="cto@telco.example",
        relationship_type="customer",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="cto@telco.example",
        subject="Meeting request",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 3
    assert result["tier_guess"] == "MAYBE"


def test_classify_pipeline_domain_match_adds_2(rules_engine, make_workspace):
    """9. Sender domain appears in context/pipeline.md -> +2 -> MAYBE."""
    pipeline_path = make_workspace / "context" / "pipeline.md"
    pipeline_path.write_text(
        "# Pipeline\n\nActive deal with bigtelco.example -- POC in progress.\n",
        encoding="utf-8",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="sara@bigtelco.example",
        subject="POC update",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["pipeline"] == 2
    assert result["weight"] == 2
    assert result["tier_guess"] == "MAYBE"


def test_classify_threads_recent_mention_adds_1(rules_engine, make_workspace):
    """10. Thread mentions sender and last_touched is today -> +1."""
    _write_thread(
        make_workspace / "threads" / "business",
        slug="2026-05-01-some-deal",
        last_touched="2026-05-28",
        body_extra="Contact: partner@acme.io for follow-up.",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="partner@acme.io",
        subject="Checking in",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["threads"] == 1
    assert result["weight"] == 1


def test_classify_threads_old_mention_ignored(rules_engine, make_workspace):
    """11. Thread mentions sender but last_touched is 60 days ago -> 0."""
    old_date = (_fixed_now() - timedelta(days=60)).strftime("%Y-%m-%d")
    _write_thread(
        make_workspace / "threads" / "business",
        slug="2026-03-01-old-deal",
        last_touched=old_date,
        body_extra="Contact: partner@acme.io for follow-up.",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="partner@acme.io",
        subject="Checking in",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["threads"] == 0


def test_classify_time_sensitivity_regex_adds_1(rules_engine, make_workspace):
    """12. Subject contains 'asap' -> +1 time_sensitivity only (not in keyword list).

    'asap' matches the time-sensitivity regex but is NOT in the promote_to_important
    keyword list, so it contributes exactly +1 with no keyword_override firing.
    """
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="random@other.org",
        subject="Please review this asap",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["time_sensitivity"] == 1
    assert result["reason_breakdown"]["keyword_override"] is None
    assert result["weight"] == 1


def test_classify_time_sensitivity_in_body_only(rules_engine, make_workspace):
    """13. Subject is neutral; body_preview contains 'eod' -> +1 time_sensitivity.

    'eod' is in the time-sensitivity regex but NOT in the keyword_overrides list,
    giving a clean +1 with no keyword_override contribution.
    """
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="random@other.org",
        subject="Following up",
        body_preview="Please send the report by eod.",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["time_sensitivity"] == 1
    assert result["reason_breakdown"]["keyword_override"] is None
    assert result["weight"] == 1


def test_classify_body_preview_truncated_to_500_chars(rules_engine, make_workspace):
    """14. body_preview is 5000 chars with 'deadline' at position 600 -> NOT detected."""
    clf = _make_classifier(rules_engine, make_workspace)
    # Build 600 chars of filler, then append 'deadline'
    filler = "x" * 600
    body = filler + "deadline"
    result = clf.classify(
        sender_email="random@other.org",
        subject="Normal subject",
        body_preview=body,
        now=_fixed_now(),
    )
    # The truncation to 500 chars means 'deadline' at position 600 is cut off
    assert result["reason_breakdown"]["time_sensitivity"] == 0


def test_classify_combined_signals_aggregate_to_high_likely(rules_engine, make_workspace):
    """15. CRM tribe (+3) + pipeline (+2) = weight 5 -> HIGH_LIKELY."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="vip-contact",
        email="cto@sovereign.example",
        relationship_type="tribe-leadership",
    )
    pipeline_path = make_workspace / "context" / "pipeline.md"
    pipeline_path.write_text(
        "# Pipeline\n\nKey partner: sovereign.example -- deal in negotiation.\n",
        encoding="utf-8",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="cto@sovereign.example",
        subject="Quick sync",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 3
    assert result["reason_breakdown"]["pipeline"] == 2
    assert result["weight"] == 5
    assert result["tier_guess"] == "HIGH_LIKELY"


def test_classify_combined_signals_aggregate_to_maybe(rules_engine, make_workspace):
    """16. CRM contact match (+1) + time-sensitivity (+1) = weight 2 -> MAYBE."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="ordinary-contact",
        email="ops@regular.com",
        relationship_type="lead",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="ops@regular.com",
        subject="Please review asap",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 1
    assert result["reason_breakdown"]["time_sensitivity"] == 1
    assert result["weight"] == 2
    assert result["tier_guess"] == "MAYBE"


def test_classify_calendar_skipped_when_account_none(rules_engine, make_workspace):
    """17. No account passed -> calendar=0 in breakdown, no AttributeError."""
    clf = _make_classifier(rules_engine, make_workspace, account=None)
    result = clf.classify(
        sender_email="anyone@example.org",
        subject="Meeting",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["calendar"] == 0


def test_classify_calendar_exception_returns_zero_silently(rules_engine, make_workspace):
    """18. Account.calendar.view() raises -> calendar=0, no exception propagated."""
    mock_account = MagicMock()
    mock_account.calendar.view.side_effect = Exception("EWS timeout")

    clf = _make_classifier(rules_engine, make_workspace, account=mock_account)
    # Should not raise; calendar signal silently returns 0
    result = clf.classify(
        sender_email="someone@example.org",
        subject="Quick call",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["calendar"] == 0
    assert result["tier_guess"] == "LOW"


def test_classify_handles_missing_crm_dir_gracefully(rules_engine, tmp_path):
    """19. crm/contacts/ dir doesn't exist -> crm_contact=0, no crash."""
    # Do NOT create the crm/contacts/ directory
    clf = _make_classifier(rules_engine, tmp_path)
    result = clf.classify(
        sender_email="someone@example.org",
        subject="Hello",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 0
    assert result["tier_guess"] == "LOW"


def test_classify_returns_breakdown_with_all_keys(rules_engine, make_workspace):
    """20. Return dict always has tier_guess, weight, and all 7 breakdown keys."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="x@y.com",
        subject="Test",
        now=_fixed_now(),
    )

    assert "tier_guess" in result
    assert "weight" in result
    assert "reason_breakdown" in result

    breakdown_keys = {
        "sender_override",
        "keyword_override",
        "crm_contact",
        "pipeline",
        "threads",
        "calendar",
        "time_sensitivity",
    }
    assert set(result["reason_breakdown"].keys()) == breakdown_keys


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_classify_always_important_sender_adds_3_weight(rules_engine, make_workspace):
    """always_important sender does not short-circuit but contributes weight=3."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="important@example.com",
        subject="Just checking in",
        now=_fixed_now(),
    )
    # No short-circuit -> weight=3 -> MAYBE
    assert result["tier_guess"] == "MAYBE"
    assert result["weight"] == 3
    assert result["reason_breakdown"]["sender_override"] == "always_important"


def test_classify_crm_investor_active_relationship_adds_3(rules_engine, make_workspace):
    """investor-active relationship_type is in the high-value set -> +3."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="active-investor",
        email="gp@vcfund.com",
        relationship_type="investor-active",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="gp@vcfund.com",
        subject="Portfolio update",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 3


def test_classify_pipeline_missing_file_returns_0(rules_engine, make_workspace):
    """pipeline.md missing -> pipeline score 0, no crash."""
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="cto@somecompany.com",
        subject="Hi",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["pipeline"] == 0


def test_classify_threads_dir_missing_returns_0(rules_engine, tmp_path):
    """threads/business/ dir missing -> threads score 0, no crash."""
    # tmp_path has no subdirs created
    clf = _make_classifier(rules_engine, tmp_path)
    result = clf.classify(
        sender_email="x@y.com",
        subject="Hi",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["threads"] == 0


def test_classify_crm_case_insensitive_email_match(rules_engine, make_workspace):
    """CRM email match is case-insensitive."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="mixed-case",
        email="alice@31c.io",
        relationship_type="tribe",
    )
    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Hello",
        now=_fixed_now(),
    )
    assert result["reason_breakdown"]["crm_contact"] == 3


# ---------------------------------------------------------------------------
# Tribe-Leadership + To/CC recipient-aware rule (8 new tests)
# ---------------------------------------------------------------------------


def _make_tl_classifier(rules_engine, workspace_root: Path, my_email: str):
    """CheapClassifier with my_email set (TL+To/CC rule enabled)."""
    from scripts.inbox_pulse.rules import CheapClassifier
    return CheapClassifier(
        rules=rules_engine,
        workspace_root=workspace_root,
        account=None,
        my_email=my_email,
    )


def test_classify_tl_in_to_short_circuits_to_high_likely(rules_engine, make_workspace):
    """TL sender + CEO in To -> HIGH_LIKELY, weight=99, breakdown=tl_to_important."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Important update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=["team@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["weight"] == 99
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"
    assert result["reason_breakdown"]["keyword_override"] is None
    assert result["reason_breakdown"]["crm_contact"] == 0


def test_classify_tl_in_cc_short_circuits_to_low(rules_engine, make_workspace):
    """TL sender + CEO only in CC (not To) -> LOW, weight=0, breakdown=internal_cc_normal.

    Note: marker changed from tl_cc_normal to internal_cc_normal (2026-05-29 extension)
    because CC always == normal for ALL internal senders, not just TL.
    """
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="FYI: team update",
        now=_fixed_now(),
        recipients_to=["alice@example.com"],
        recipients_cc=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "LOW"
    assert result["weight"] == 0
    assert result["reason_breakdown"]["sender_override"] == "internal_cc_normal"
    assert result["reason_breakdown"]["keyword_override"] is None


def test_classify_tl_in_both_to_and_cc_uses_to_wins(rules_engine, make_workspace):
    """TL sender + CEO in both To and CC -> To wins -> HIGH_LIKELY."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Reply-all thread",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"


def test_classify_tl_relationship_subtype_match(rules_engine, make_workspace):
    """relationship_type='tribe-leadership-active' (substring) still triggers rule."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="vince-hale",
        email="ivan@31c.io",
        relationship_type="tribe-leadership-active",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="ivan@31c.io",
        subject="Engineering update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"


def test_classify_non_tl_sender_recipient_logic_skipped(rules_engine, make_workspace):
    """External sender (telco.example, not in internal_domains) -- recipient rule does NOT fire.

    Even with CEO in To, the rule is bypassed entirely for external senders;
    normal 7-signal classifier flow runs.
    """
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="customer-cto",
        email="cto@telco.example",
        relationship_type="customer",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="cto@telco.example",
        subject="Meeting request",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    # External sender -- no short-circuit. Normal CRM score of 3 applies.
    assert result["reason_breakdown"]["sender_override"] not in (
        "tl_to_important", "internal_nonlead_to_normal", "internal_cc_normal"
    )
    assert result["reason_breakdown"]["crm_contact"] == 3  # customer earns +3
    assert result["tier_guess"] == "MAYBE"


def test_classify_missing_recipients_skips_rule_gracefully(rules_engine, make_workspace):
    """TL sender but recipients_to=None, recipients_cc=None -> falls through to normal flow."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Quick check",
        now=_fixed_now(),
        # recipients_to and recipients_cc both absent
    )
    # Falls through to normal flow: TL in _HIGH_VALUE_RELATIONSHIPS -> crm_contact=3 -> MAYBE
    assert result["reason_breakdown"]["sender_override"] not in (
        "tl_to_important", "tl_cc_normal", "internal_nonlead_to_normal", "internal_cc_normal"
    )
    assert result["reason_breakdown"]["crm_contact"] == 3
    assert result["tier_guess"] == "MAYBE"


def test_classify_missing_my_email_skips_rule_gracefully(rules_engine, make_workspace):
    """my_email=None on classifier -> rule is bypassed even with TL sender + recipients."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    # Construct without my_email (simulates old construction path or backward compat)
    from scripts.inbox_pulse.rules import CheapClassifier
    clf = CheapClassifier(
        rules=rules_engine,
        workspace_root=make_workspace,
        account=None,
        my_email=None,
    )
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Hello",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=["ceo@31c.io"],
    )
    # my_email is None -> short-circuit never fires -> normal flow
    assert result["reason_breakdown"]["sender_override"] not in (
        "tl_to_important", "tl_cc_normal", "internal_nonlead_to_normal", "internal_cc_normal"
    )
    assert result["reason_breakdown"]["crm_contact"] == 3


def test_classify_case_insensitive_email_matching(rules_engine, make_workspace):
    """CRM email in mixed case, recipient in all-caps -> still matches correctly."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",  # mixed case in CRM
        relationship_type="tribe-leadership",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",  # lowercase in daemon
        subject="All caps test",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],  # all-caps in Exchange
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"


# ---------------------------------------------------------------------------
# Extended recipient-aware rule: all internal senders (8 new tests, 2026-05-29)
# ---------------------------------------------------------------------------


def _make_rules_yaml_with_domains(tmp_path: Path, domains: list) -> Path:
    """Write a minimal rules YAML with configurable internal_domains to a tmp file."""
    if not domains:
        domains_section = "internal_domains: []"
    else:
        items = "\n".join(f'  - "{d}"' for d in domains)
        domains_section = f"internal_domains:\n{items}"

    lines = [
        "sender_overrides:",
        "  always_critical: []",
        "  always_important: []",
        "  always_normal: []",
        "keyword_overrides:",
        "  promote_to_critical: []",
        "  promote_to_important: []",
        "quiet_hours:",
        '  start: "23:00"',
        '  end: "07:00"',
        '  timezone: "Etc/GMT-4"',
        "breakthrough_allowlist: []",
        domains_section,
        "cost_ceiling:",
        "  monthly_anthropic_usd: 50",
        "  warn_at_percent: 80",
    ]
    content = "\n".join(lines) + "\n"
    p = tmp_path / "extended_rules.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_classify_internal_nonlead_in_to_short_circuits_to_normal(make_workspace, tmp_path):
    """Internal non-leadership sender + CEO in To -> LOW, internal_nonlead_to_normal."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="alice-31c",
        email="alice@31c.io",
        relationship_type="customer-active",  # NOT leadership
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Status update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=[],
    )
    assert result["tier_guess"] == "LOW"
    assert result["weight"] == 0
    assert result["reason_breakdown"]["sender_override"] == "internal_nonlead_to_normal"
    assert result["reason_breakdown"]["keyword_override"] is None
    assert result["reason_breakdown"]["crm_contact"] == 0


def test_classify_internal_nonlead_in_cc_short_circuits_to_normal(make_workspace, tmp_path):
    """Internal non-leadership sender + CEO in CC only -> LOW, internal_cc_normal."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="alice-31c",
        email="alice@31c.io",
        relationship_type="customer-active",  # NOT leadership
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Weekly summary",
        now=_fixed_now(),
        recipients_to=["alice@example.com"],
        recipients_cc=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "LOW"
    assert result["weight"] == 0
    assert result["reason_breakdown"]["sender_override"] == "internal_cc_normal"


def test_classify_external_sender_bypasses_rule_falls_through(make_workspace, tmp_path):
    """External sender (northgate.com not in internal_domains) -> no short-circuit."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    # Give vincent a CRM score so the result is not trivially LOW
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-northgate",
        email="victor@northgate.com",
        relationship_type="investor-active",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="victor@northgate.com",
        subject="Series B follow-up",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    # External sender -> rule never short-circuits; CRM score applies
    assert result["reason_breakdown"]["sender_override"] not in (
        "tl_to_important", "internal_nonlead_to_normal", "internal_cc_normal"
    )
    # investor-active earns +3 via existing classifier
    assert result["reason_breakdown"]["crm_contact"] == 3


def test_classify_internal_sender_no_crm_contact_treated_as_non_leadership(make_workspace, tmp_path):
    """Internal sender with no CRM record -> treated as non-leadership -> LOW (in To)."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    # No CRM contact written for unknown@31c.io
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="unknown@31c.io",
        subject="Question",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "LOW"
    assert result["reason_breakdown"]["sender_override"] == "internal_nonlead_to_normal"


def test_classify_internal_sender_in_neither_to_nor_cc_falls_through(make_workspace, tmp_path):
    """Internal TL sender + CEO in neither To nor CC -> falls through to 7-signal classifier."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Message to others",
        now=_fixed_now(),
        recipients_to=["alice@31c.io"],
        recipients_cc=["carol@31c.io"],
    )
    # CEO not in To or CC -> rule falls through -> CRM score applies
    assert result["reason_breakdown"]["sender_override"] not in (
        "tl_to_important", "internal_nonlead_to_normal", "internal_cc_normal"
    )
    assert result["reason_breakdown"]["crm_contact"] == 3  # tribe-leadership in _HIGH_VALUE_RELATIONSHIPS


def test_classify_internal_domain_case_insensitive(make_workspace, tmp_path):
    """Sender domain 31C.IO (uppercase) matches internal_domains: ['31c.io'] (case-insensitive)."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-uppercase-domain",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Test",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"


def test_classify_no_internal_domains_configured_skips_rule_entirely(make_workspace, tmp_path):
    """internal_domains: [] -> recipient-aware rule disabled; TL+To falls through to classifier."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, [])  # empty list
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="alice@31c.io",
        relationship_type="tribe-leadership",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Test",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    # No internal domains -> rule never fires -> normal CRM scoring applies
    assert result["reason_breakdown"]["sender_override"] not in (
        "tl_to_important", "internal_nonlead_to_normal", "internal_cc_normal"
    )
    assert result["reason_breakdown"]["crm_contact"] == 3


def test_classify_multiple_internal_domains(make_workspace, tmp_path):
    """Two internal domains configured; sender from second domain + TL -> HIGH_LIKELY."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io", "31concept.com"])
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="andrey-31concept",
        email="alex@31concept.com",
        relationship_type="tribe-leadership",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="alex@31concept.com",
        subject="Board update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"
