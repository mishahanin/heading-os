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
import time
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
    schema: str = "hybrid",
) -> None:
    """Write one CRM contact card in a named schema.

    Three shapes exist on the operator's tree and a reader must handle two of
    them; the third is the one this fixture used to write unconditionally.

      `legacy` the address inline on the card, no `entity_ref`. 14 of 169 cards.
      `entity` `entity_ref` pointing at `crm/address-book/<slug>.md`, which
               carries `canonical_email`. This is what `/crm` add-contact writes
               and what `crm_migrate_to_entity_model.py --apply` produces, so it
               is the CURRENT schema. 66 of 169 cards inline nothing at all.
      `hybrid` both keys at once. 89 of 169 cards, all of them mid-migration.

    The fixture wrote `hybrid` only, and that is why twelve green tests missed a
    reader that could see nothing but the inline key: the `entity_ref` line made
    every one of them LOOK migration-aware while the inline `email:` quietly
    fed the reader the one shape it could parse. Measured 2026-08-29, the old
    reader saw 89 of the operator's 148 CRM addresses.

    `entity` also writes the address-book record, because a card pointing at a
    file that does not exist is a dangling ref, not the entity schema.
    """
    if schema not in ("legacy", "entity", "hybrid"):
        raise ValueError(f"unknown schema {schema!r}")

    lines = ["---"]
    if schema in ("entity", "hybrid"):
        lines.append(f"entity_ref: {slug}")
    lines.append(f"relationship_type: {relationship_type}")
    if schema in ("legacy", "hybrid"):
        lines.append(f"email: {email}")
    lines += ["last_touch: 2026-05-28", "created: 2026-05-01",
              "status: active", "tags: []", "---", "", f"# {slug}", ""]
    (contacts_dir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")

    if schema in ("entity", "hybrid"):
        book = contacts_dir.parent / "address-book"
        book.mkdir(parents=True, exist_ok=True)
        # `hybrid` gets an entity with NO canonical_email, which is the real
        # mid-migration state: the address is still on the card. Four live
        # contacts are in exactly this state.
        canonical = f"canonical_email: {email}\n" if schema == "entity" else ""
        (book / f"{slug}.md").write_text(
            f"---\nslug: {slug}\nname: {slug}\n{canonical}---\n\n# {slug}\n",
            encoding="utf-8")


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
        sender_email="tamsin@bigtelco.example",
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
    """14. 'deadline' past char 500 reaches NEITHER reader of the body.

    `time_sensitivity == 0` was the whole assertion until 2026-09-02, and the
    body is read TWICE inside `classify`: once by `match_keywords` at
    scripts/inbox_pulse/rules.py:250 and once by `_TIME_SENSITIVITY_RE` at
    :369. The word this fixture puts at position 600 is `deadline`, which the
    shared rules.yaml at the top of this file lists as a keyword override, so
    a reader that saw past the cut would promote on a word the other reader
    cannot see. Naming every detector is the point: the docstring's claim is
    "NOT detected".

    What the `keyword_override` assertion does NOT establish, corrected
    2026-09-02. This paragraph said both readers "draw on the single
    truncation at :224". There are TWO cuts, not one: `classify` truncates the
    local at rules.py:224 and hands the already-short value to
    `match_keywords`, which truncates again at overrides.py:290. So neither
    site can be removed alone and be seen from here. Drop :224 and the regex
    reads position 600, failing the older `time_sensitivity` assertion while
    `keyword_override` stays None because the second cut still holds. Drop
    overrides.py:290 and NOTHING here moves, because the input already arrived
    short. The belt is real and it is worth keeping; what pins it is
    `test_match_keywords_truncates_the_body_it_is_handed` below, which calls
    the method directly with a body nobody cut first.
    """
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
    assert result["reason_breakdown"]["keyword_override"] is None, (
        "the keyword matcher read past the 500-char cut and promoted on a word "
        "the time-sensitivity check could not see")
    assert result["weight"] == 0, result["reason_breakdown"]


def test_match_keywords_truncates_the_body_it_is_handed(rules_engine):
    """The second cut, asked about directly rather than through `classify`.

    `match_keywords` is public, its docstring promises "subject + first 500
    chars of body_preview", and every caller inside `classify` hands it a body
    that rules.py:224 already shortened. That is why the test above cannot see
    this cut removed: the belt is invisible from behind the braces. Called
    with a body nobody truncated first, it is the only assertion in this file
    that overrides.py:290 has to satisfy.
    """
    body = "x" * 600 + "deadline"
    assert rules_engine.match_keywords("Normal subject", body) is None, (
        "match_keywords matched a keyword past the 500-char cut its own "
        "docstring promises, so a caller that does not pre-truncate promotes "
        "on text the rest of the classifier never reads")
    near = "x" * 400 + " deadline"
    assert rules_engine.match_keywords("Normal subject", near) == (
        "promote_to_important"), (
        "the anchor: a keyword INSIDE the window must still match, or a cut "
        "that dropped the body entirely would satisfy the assertion above")


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


@pytest.mark.parametrize("extra_tokens,expected_weight,expected_tier", [
    (0, 3, "MAYBE"),        # just under
    (1, 4, "HIGH_LIKELY"),  # ON the line
    (2, 5, "HIGH_LIKELY"),  # just over
])
def test_the_high_likely_boundary_is_four_not_five(
        rules_engine, make_workspace, extra_tokens, expected_weight,
        expected_tier):
    """Weight 4 is HIGH_LIKELY. Nothing put a case ON that line. NEW 2026-09-01.

    The aggregate has two boundaries. The MAYBE one is held: tests 6 and 16
    sit at weight 1 and 2, so `elif weight >= 2` cannot move without a failure.
    The HIGH_LIKELY one had cases at 3 (test 7) and 5 (test 15) and none at 4,
    which is the only value that distinguishes `>= 4` from `>= 5`.

    MEASURED 2026-09-01:

        -   if weight >= 4:
        +   if weight >= 5:

        .venv/bin/python -m pytest tests/inbox_pulse -q
            -> 226 passed        (baseline: 226 passed)
        the 45 test files anywhere in tests/ that name inbox_pulse,
        observability_safe, healthchecks or hc_ping, plus tests/contract
            -> 7 failed, 1199 passed, 3 skipped
               (baseline: the identical 7 failed, 1199 passed, 3 skipped;
                those 7 are sandbox-environment failures, present either way)

    The same mutation applied to `elif weight >= 2` fails 2 tests at once, so
    the two boundaries were not equally covered and only one of them knew it.

    What moving this line costs: HIGH_LIKELY is the tier that reaches the
    operator. A silent shift to 5 demotes every email whose signals sum to
    exactly 4, which is an ordinary combination rather than a rare one. The
    parametrisation below builds 4 as always_important (3) plus
    time_sensitivity (1), both from the shared rules YAML.

    `extra_tokens` is the count of time-sensitivity words in the subject: the
    signal is a flag worth 1, not a per-word tally, so 2 words still add 1 and
    the third row reaches 5 through a CRM contact instead.
    """
    if expected_weight == 5:
        _write_crm_contact(
            make_workspace / "crm" / "contacts",
            slug="ordinary-contact",
            email="important@example.com",
            relationship_type="lead",          # +1, not +3
        )
    subject = "Please review asap" if extra_tokens else "Please review"

    clf = _make_classifier(rules_engine, make_workspace)
    result = clf.classify(
        sender_email="important@example.com",   # always_important -> +3
        subject=subject,
        now=_fixed_now(),
    )

    assert result["weight"] == expected_weight, (
        f"the fixture produced weight {result['weight']}, not "
        f"{expected_weight}; breakdown {result['reason_breakdown']}")
    assert result["tier_guess"] == expected_tier, (
        f"weight {expected_weight} classified {result['tier_guess']}; the "
        f"HIGH_LIKELY floor is 4")


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
    """CRM email in mixed case, recipient in all-caps -> still matches correctly.

    MEASURED 2026-08-31: until that day every value in this test was entirely
    lowercase, while the inline comments read `# mixed case in CRM` and
    `# all-caps in Exchange`. A grep for any uppercase address across this
    whole file returned zero hits, so nothing anywhere exercised case folding.
    The test was green over nothing for the property it is named for: deleting
    `sender_email.lower()` at `scripts/inbox_pulse/rules.py:383` and
    `addr.lower()` at `:257` left it passing.

    The values below now differ in case from what the code compares them
    against, so each `.lower()` in the implementation is load-bearing here.
    """
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-stein",
        email="Alice@31C.io",  # mixed case in CRM, folded by contact_index_by_email
        relationship_type="tribe-leadership",
    )
    clf = _make_tl_classifier(rules_engine, make_workspace, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="ALICE@31c.io",  # all-caps local part from the daemon
        subject="All caps test",
        now=_fixed_now(),
        recipients_to=["CEO@31C.IO"],  # all-caps in Exchange
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
    """External sender (contoso.com not in internal_domains) -> no short-circuit."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31c.io"])
    engine = RulesEngine(yaml_path=yaml_path)

    # Give the external sender a CRM score so the result is not trivially LOW
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="nolan-contoso",
        email="nolan@contoso.com",
        relationship_type="investor-active",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="nolan@contoso.com",
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
    """Sender domain 31C.IO (uppercase) matches internal_domains: ['31c.io'].

    MEASURED 2026-08-31: the docstring named `31C.IO` and every input was
    lowercase, so the test asserted nothing about case at all. Removing
    `sender_domain.lower()` at `scripts/inbox_pulse/rules.py:251` left it green.

    The sender domain below is now uppercase and the configured domain is
    lowercase, so the fold at `:251` is the only thing that makes them match.
    """
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
        sender_email="alice@31C.IO",   # uppercase domain against a lowercase config
        subject="Test",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"


def test_classify_internal_domain_matches_an_uppercase_configured_domain(
    make_workspace, tmp_path
):
    """The other side of the same fold, which nothing covered.

    `internal_domains_lower` at `scripts/inbox_pulse/rules.py:252` folds the
    CONFIGURED list. The test above only folds the sender, so deleting that line
    while keeping `:251` would still leave it green. An operator who writes
    `internal_domains: ["31C.IO"]` in their YAML is the case this covers.
    """
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    yaml_path = _make_rules_yaml_with_domains(tmp_path, ["31C.IO"])
    engine = RulesEngine(yaml_path=yaml_path)

    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="victor-uppercase-config",
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

    # Fictional persona, per the engine's placeholder set. The slug carried a real
    # given name until 2026-08-26, which the content gate reads as a leak because
    # it matches a live CRM contact. The slug and the address did not even agree.
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="marlow-31concept",
        email="marlow@31concept.com",
        relationship_type="tribe-leadership",
    )
    clf = CheapClassifier(rules=engine, workspace_root=make_workspace, account=None, my_email="ceo@31c.io")
    result = clf.classify(
        sender_email="marlow@31concept.com",
        subject="Board update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "tl_to_important"


# ---------------------------------------------------------------------------
# "Sender overrides take absolute precedence" -- made true
#
# Found by the 2026-08-23 audit. The comment at step 1 of `classify` says
# sender overrides take absolute precedence, and step 0 -- the recipient-aware
# block added by the 2026-05-29 directive -- ran before it and returned first.
#
# So an internal colleague the operator had explicitly put on `always_critical`
# was silently demoted to LOW whenever they wrote to the operator directly. That
# is the one case the allowlist exists for: it is the operator saying "always
# show me this person", and it stopped working for everyone inside the Tribe,
# with the breakdown blaming `internal_nonlead_to_normal`.
# ---------------------------------------------------------------------------


def _rules_yaml_with_overrides(tmp_path: Path, *, critical=(), normal=()) -> Path:
    lines = ["sender_overrides:"]
    if critical:
        lines.append("  always_critical:")
        lines.extend(f'    - "{a}"' for a in critical)
    else:
        lines.append("  always_critical: []")
    lines.append("  always_important: []")
    if normal:
        lines.append("  always_normal:")
        lines.extend(f'    - "{a}"' for a in normal)
    else:
        lines.append("  always_normal: []")
    lines += [
        "keyword_overrides:",
        "  promote_to_critical: []",
        "  promote_to_important: []",
        "quiet_hours:",
        '  start: "23:00"',
        '  end: "07:00"',
        '  timezone: "Etc/GMT-4"',
        "breakthrough_allowlist: []",
        "internal_domains:",
        '  - "31c.io"',
        "cost_ceiling:",
        "  monthly_anthropic_usd: 50",
        "  warn_at_percent: 80",
    ]
    p = tmp_path / "override_precedence_rules.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _internal_classifier(make_workspace, tmp_path, *, critical=(), normal=()):
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    engine = RulesEngine(
        yaml_path=_rules_yaml_with_overrides(tmp_path, critical=critical, normal=normal))
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="alice-31c",
        email="alice@31c.io",
        relationship_type="customer-active",   # NOT leadership
    )
    return CheapClassifier(rules=engine, workspace_root=make_workspace,
                           account=None, my_email="ceo@31c.io")


def test_always_critical_beats_the_internal_recipient_demotion(make_workspace, tmp_path):
    """The defect, in the shape it fires: an allowlisted colleague writes to you."""
    clf = _internal_classifier(make_workspace, tmp_path, critical=["alice@31c.io"])
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Status update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=[],
    )
    assert result["tier_guess"] == "HIGH_LIKELY", (
        "an always_critical sender was demoted to LOW because the mail was "
        "internal -- the allowlist does not work for anyone in the Tribe"
    )
    assert result["reason_breakdown"]["sender_override"] == "always_critical"


def test_always_critical_beats_the_cc_demotion_too(make_workspace, tmp_path):
    clf = _internal_classifier(make_workspace, tmp_path, critical=["alice@31c.io"])
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Weekly summary",
        now=_fixed_now(),
        recipients_to=["someone@31c.io"],
        recipients_cc=["ceo@31c.io"],
    )
    assert result["tier_guess"] == "HIGH_LIKELY"
    assert result["reason_breakdown"]["sender_override"] == "always_critical"


def test_always_normal_reports_itself_rather_than_the_recipient_rule(make_workspace, tmp_path):
    """Same tier either way, but the breakdown must name the rule that decided."""
    clf = _internal_classifier(make_workspace, tmp_path, normal=["alice@31c.io"])
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Status update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=[],
    )
    assert result["tier_guess"] == "LOW"
    assert result["reason_breakdown"]["sender_override"] == "always_normal"


def test_the_recipient_demotion_still_fires_without_an_override(make_workspace, tmp_path):
    """The mutation guard. The 2026-05-29 directive must survive the fix."""
    clf = _internal_classifier(make_workspace, tmp_path)
    result = clf.classify(
        sender_email="alice@31c.io",
        subject="Status update",
        now=_fixed_now(),
        recipients_to=["ceo@31c.io"],
        recipients_cc=[],
    )
    assert result["tier_guess"] == "LOW"
    assert result["reason_breakdown"]["sender_override"] == "internal_nonlead_to_normal"


# ---------------------------------------------------------------------------
# Shard 10-p2: two readers of one field, and a `now` that had to carry a zone
# ---------------------------------------------------------------------------


def test_a_naive_now_does_not_kill_the_whole_classification_run(
        make_workspace, rules_engine):
    """`_parse_date` returns UTC-AWARE datetimes and `_score_threads` compares
    against them, so a naive `now` made `cutoff` naive and raised
    "can't compare offset-naive and offset-aware datetimes" straight out of
    `classify`. Nothing on that path swallows it, unlike `_score_calendar`.

    The docstring offered `now` as an override for testability and never said
    it had to carry a zone, so `datetime(2026, 9, 1)` or a `datetime.utcnow()`
    was a reasonable thing for a caller to pass.
    """
    _write_thread(
        make_workspace / "threads" / "business",
        slug="2026-08-01-naive-now",
        last_touched="2026-08-30",
        body_extra="Contact: bob@vendor.test",
    )
    clf = _make_classifier(rules_engine, make_workspace)

    # Naive on purpose. `fromisoformat` on a string carrying no offset is the
    # naive constructor that does not trip DTZ001, and naiveness is precisely
    # the input under test here rather than a lapse.
    result = clf.classify(
        sender_email="bob@vendor.test",
        subject="Status",
        now=datetime.fromisoformat("2026-09-01T00:00:00"),
    )

    assert result["reason_breakdown"]["threads"] == 1, (
        "the naive `now` was accepted but the thread window moved; it must be "
        "read as UTC, which is what the default already is")


def test_a_naive_now_is_read_as_utc_rather_than_as_the_host_timezone(
        make_workspace, rules_engine, monkeypatch):
    """The counter-case. Coercing with `.astimezone()` instead of
    `.replace(tzinfo=utc)` would also stop the TypeError, and would then make
    the answer depend on the machine's offset.

    TZ is pinned rather than inherited, so the assertion cannot go vacuous on a
    UTC runner while passing on the operator's UTC+4 laptop. The window edge is
    chosen so a 14-hour shift crosses it: `last_touched` is exactly
    `_RECENT_THREAD_DAYS` days before the UTC instant under test.
    """
    monkeypatch.setenv("TZ", "Etc/GMT+12")  # UTC-12, no DST
    time.tzset()

    _write_thread(
        make_workspace / "threads" / "business",
        slug="2026-08-01-boundary",
        last_touched="2026-08-02",
        body_extra="Contact: bob@vendor.test",
    )
    clf = _make_classifier(rules_engine, make_workspace)

    # The offset has to be NEGATIVE for a date-only `last_touched` to fall on
    # different sides of the cutoff, which is why a UTC+14 zone was no test at
    # all here. MEASURED: read as UTC the cutoff is 2026-08-02 00:00, and
    # `last_touched < cutoff` is False, so the thread counts. Read as UTC-12
    # the cutoff moves to 2026-08-02 12:00 and the same thread drops out.
    #
    # Naive on purpose: a naive `now` is the input the defect was about, so
    # the DTZ001 the linter would raise here is the test's subject rather than
    # an oversight.
    naive = datetime.fromisoformat("2026-09-01T00:00:00")
    aware = naive.replace(tzinfo=timezone.utc)

    assert (clf.classify("bob@vendor.test", "s", now=naive)
            == clf.classify("bob@vendor.test", "s", now=aware)), (
        "a naive `now` and the same instant in UTC classified differently, so "
        "the answer depends on the host's timezone")


def test_a_relationship_type_with_trailing_space_still_scores_as_high_value(
        make_workspace, rules_engine):
    """`classify` normalises this field with `.strip().lower()` for the
    Tribe-Leadership test; `_score_crm_contact` only lowercased.

    A card written `type: "customer "` (an ordinary hand-edited YAML artifact)
    was therefore read as current leadership by one reader and scored 1 instead
    of 3 by the other. Two readers of one field disagreeing is enough to drop a
    high-value external contact a whole tier.
    """
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="trailing-space",
        email="carol@customer.test",
        relationship_type='"customer "',
    )
    clf = _make_classifier(rules_engine, make_workspace)

    result = clf.classify(
        sender_email="carol@customer.test",
        subject="Renewal",
        now=_fixed_now(),
    )

    assert result["reason_breakdown"]["crm_contact"] == 3, (
        f"a trailing space in relationship_type cost the contact the +2 "
        f"high-value bonus: {result}")
    assert result["tier_guess"] == "MAYBE", result


def test_a_relationship_type_that_is_not_high_value_still_scores_one(
        make_workspace, rules_engine):
    """The counter-case. Returning 3 unconditionally would pass the test above
    and hand every stranger in the CRM a high-value score."""
    _write_crm_contact(
        make_workspace / "crm" / "contacts",
        slug="ordinary-lead",
        email="dave@lead.test",
        relationship_type='"lead "',
    )
    clf = _make_classifier(rules_engine, make_workspace)

    result = clf.classify(
        sender_email="dave@lead.test",
        subject="Hello",
        now=_fixed_now(),
    )

    assert result["reason_breakdown"]["crm_contact"] == 1, result
    assert result["tier_guess"] == "LOW", result
