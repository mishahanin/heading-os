"""Tests for scripts/inbox_pulse/overrides.py (RulesEngine).

All tests use pytest tmp_path to avoid touching the real workspace config.
A shared sample_yaml fixture writes a representative rules file so tests
can construct RulesEngine(tmp_path / "rules.yaml") and exercise matching.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SAMPLE_YAML = textwrap.dedent("""\
    sender_overrides:
      always_critical:
        - alice@31c.io
        - frank@31c.io
      always_important: []
      always_normal:
        - "newsletter@*"
        - "noreply@*"
        - "no-reply@*"
        - "*@linkedin.com"
        - "*@notifications.google.com"

    keyword_overrides:
      promote_to_critical:
        - "series b"
        - "security incident"
        - "term sheet"
        - "acquisition offer"
      promote_to_important:
        - "deadline"
        - "by friday"
        - "urgent"

    quiet_hours:
      start: "23:00"
      end: "07:00"
      timezone: "Etc/GMT-4"

    breakthrough_allowlist: []

    cost_ceiling:
      monthly_anthropic_usd: 50
      warn_at_percent: 80
""")


@pytest.fixture()
def rules_yaml(tmp_path: Path) -> Path:
    """Write SAMPLE_YAML to a temp file and return the path."""
    p = tmp_path / "rules.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def engine(rules_yaml: Path):
    """RulesEngine loaded from the sample YAML."""
    from scripts.inbox_pulse.overrides import RulesEngine
    return RulesEngine(yaml_path=rules_yaml)


# ---------------------------------------------------------------------------
# Helper: local (UTC+4)-aware datetime
# ---------------------------------------------------------------------------

def _local_dt(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Return an aware datetime in UTC+4."""
    local_offset = timezone(timedelta(hours=4))
    return datetime(year, month, day, hour, minute, second, tzinfo=local_offset)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_valid_yaml_sets_config(engine):
    """1. Loading valid YAML populates internal _config."""
    assert engine._config  # non-empty dict
    assert "sender_overrides" in engine._config
    assert "keyword_overrides" in engine._config
    assert "quiet_hours" in engine._config
    assert "cost_ceiling" in engine._config


def test_load_missing_file_returns_empty_posture_no_crash(tmp_path):
    """2. Instantiating with a nonexistent path does not raise; match_sender returns None."""
    from scripts.inbox_pulse.overrides import RulesEngine
    engine = RulesEngine(yaml_path=tmp_path / "nonexistent.yaml")
    # No exception during __post_init__ (calls reload()).
    assert engine.match_sender("anyone@example.com") is None


def test_load_invalid_yaml_keeps_prior_config_no_crash(rules_yaml):
    """3. After loading valid YAML, overwriting with garbage keeps prior config; no crash."""
    from scripts.inbox_pulse.overrides import RulesEngine
    engine = RulesEngine(yaml_path=rules_yaml)
    prior_config = dict(engine._config)

    # Overwrite with invalid YAML (force a new mtime by changing content).
    rules_yaml.write_text(": : : invalid {{yaml", encoding="utf-8")

    result = engine.reload()
    # reload() returns False (no successful change) on bad YAML.
    assert result is False
    # Prior config is preserved.
    assert engine._config == prior_config


def test_match_sender_exact(engine):
    """4. Exact match on always_critical address."""
    assert engine.match_sender("alice@31c.io") == "always_critical"
    assert engine.match_sender("frank@31c.io") == "always_critical"


def test_match_sender_glob_prefix(engine):
    """5. Glob prefix pattern 'newsletter@*' matches newsletter@example.com."""
    assert engine.match_sender("newsletter@example.com") == "always_normal"
    assert engine.match_sender("newsletter@anyplace.io") == "always_normal"


def test_match_sender_glob_suffix(engine):
    """6. Glob suffix pattern '*@linkedin.com' matches alice@linkedin.com."""
    assert engine.match_sender("alice@linkedin.com") == "always_normal"
    assert engine.match_sender("jobs@linkedin.com") == "always_normal"


def test_match_sender_case_insensitive(engine):
    """7. Matching is case-insensitive for both pattern and address."""
    assert engine.match_sender("alice@31c.io") == "always_critical"
    assert engine.match_sender("NEWSLETTER@EXAMPLE.COM") == "always_normal"


def test_match_sender_no_match_returns_none(engine):
    """8. Unknown sender returns None (no match in any bucket)."""
    assert engine.match_sender("random@example.org") is None
    assert engine.match_sender("info@somecompany.example") is None


def test_match_sender_priority_critical_over_normal(tmp_path):
    """9. always_critical wins when a sender appears in both always_critical and always_normal."""
    yaml_content = textwrap.dedent("""\
        sender_overrides:
          always_critical:
            - "*@special.com"
          always_important: []
          always_normal:
            - "*@special.com"
    """)
    p = tmp_path / "conflict.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    from scripts.inbox_pulse.overrides import RulesEngine
    eng = RulesEngine(yaml_path=p)
    assert eng.match_sender("anyone@special.com") == "always_critical"


def test_match_keywords_promotes_critical(engine):
    """10. Subject containing 'term sheet' promotes to critical."""
    assert engine.match_keywords("Re: term sheet attached") == "promote_to_critical"
    assert engine.match_keywords("series b update") == "promote_to_critical"


def test_match_keywords_case_insensitive(engine):
    """11. Keyword match is case-insensitive."""
    assert engine.match_keywords("Re: TERM SHEET attached") == "promote_to_critical"
    assert engine.match_keywords("SERIES B Update") == "promote_to_critical"


def test_match_keywords_body_preview_searched(engine):
    """12. Keywords in body_preview are matched even when subject is generic."""
    result = engine.match_keywords(
        subject="Re: meeting",
        body_preview="Please respond by Friday as this is the deadline for submission.",
    )
    assert result == "promote_to_important"


def test_match_keywords_critical_wins_over_important(engine):
    """13. promote_to_critical wins when both critical and important keywords appear."""
    # "deadline" is important, "series b" is critical.
    result = engine.match_keywords("Deadline for the Series B update")
    assert result == "promote_to_critical"


def test_is_quiet_hours_handles_wrap_around(engine):
    """14. Wrap-around quiet hours (23:00-07:00 local (UTC+4)) work correctly.

    - 03:00 local (UTC+4) -> True  (inside window)
    - 15:00 local (UTC+4) -> False (outside window)
    - 23:30 local (UTC+4) -> True  (inside window, post-midnight wrap)
    - 07:00 local (UTC+4) -> False (exclusive end boundary)
    - 06:59:59 local (UTC+4) -> True (just before end)
    """
    assert engine.is_quiet_hours(_local_dt(2026, 5, 27, 3, 0, 0)) is True
    assert engine.is_quiet_hours(_local_dt(2026, 5, 27, 15, 0, 0)) is False
    assert engine.is_quiet_hours(_local_dt(2026, 5, 27, 23, 30, 0)) is True
    assert engine.is_quiet_hours(_local_dt(2026, 5, 27, 7, 0, 0)) is False
    assert engine.is_quiet_hours(_local_dt(2026, 5, 27, 6, 59, 59)) is True


def test_is_breakthrough_sender_glob_match(tmp_path):
    """15. is_breakthrough_sender returns True for listed senders, False for others."""
    yaml_content = textwrap.dedent("""\
        breakthrough_allowlist:
          - alice@31c.io
          - "*@northgate.com"
    """)
    p = tmp_path / "breakthrough.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    from scripts.inbox_pulse.overrides import RulesEngine
    eng = RulesEngine(yaml_path=p)
    assert eng.is_breakthrough_sender("alice@31c.io") is True
    assert eng.is_breakthrough_sender("partner@northgate.com") is True
    assert eng.is_breakthrough_sender("victor@northgate.com") is True
    assert eng.is_breakthrough_sender("unknown@other.com") is False


def test_reload_if_changed_skips_unchanged(rules_yaml, engine):
    """16. reload_if_changed() returns False when mtime is unchanged; True after file edit."""
    # File has not changed since initial load.
    assert engine.reload_if_changed() is False

    # Modify the YAML (add a new always_important entry).
    updated = SAMPLE_YAML.replace("always_important: []", "always_important:\n    - ceo@example.com")
    rules_yaml.write_text(updated, encoding="utf-8")
    # Coarse filesystem mtime granularity can make two rapid writes share an
    # st_mtime tick; force a strictly-later mtime so the mtime-based change
    # detection fires deterministically (production edits are seconds+ apart).
    import os
    _st = rules_yaml.stat()
    os.utime(rules_yaml, (_st.st_atime, _st.st_mtime + 2))

    assert engine.reload_if_changed() is True
    assert engine._config["sender_overrides"]["always_important"] == ["ceo@example.com"]


def test_reload_if_changed_warns_once_on_missing_file(rules_yaml, engine, caplog):
    """16b. reload_if_changed() logs WARNING once when file disappears, suppresses repeats.

    Once the file reappears the missing-warned flag resets so a subsequent
    disappearance re-warns.
    """
    import logging

    # Delete the file mid-run
    rules_yaml.unlink()

    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        # First call: warning fires
        assert engine.reload_if_changed() is False
        first_warnings = [r for r in caplog.records if "stat() failed" in r.getMessage()]
        assert len(first_warnings) == 1

        # Second call: no new warning (throttled)
        caplog.clear()
        assert engine.reload_if_changed() is False
        repeat_warnings = [r for r in caplog.records if "stat() failed" in r.getMessage()]
        assert len(repeat_warnings) == 0

    # File comes back -> throttle resets so a future disappearance re-warns
    rules_yaml.write_text(SAMPLE_YAML, encoding="utf-8")
    assert engine.reload_if_changed() in (True, False)  # may or may not differ; flag reset is what matters
    assert engine._missing_warned is False


def test_cost_ceiling_defaults_when_missing(tmp_path):
    """17. YAML without cost_ceiling returns cost defaults (50.0 and 80)."""
    yaml_content = textwrap.dedent("""\
        sender_overrides:
          always_critical: []
          always_important: []
          always_normal: []
        keyword_overrides:
          promote_to_critical: []
          promote_to_important: []
    """)
    p = tmp_path / "no_cost.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    from scripts.inbox_pulse.overrides import RulesEngine
    eng = RulesEngine(yaml_path=p)
    assert eng.cost_ceiling_usd == 50.0
    assert eng.cost_warn_at_percent == 80


def test_internal_domains_returns_configured_list(tmp_path):
    """18. internal_domains property returns the list from YAML when configured."""
    yaml_content = textwrap.dedent("""\
        internal_domains:
          - "31c.io"
          - "example.org"
    """)
    p = tmp_path / "domains.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    from scripts.inbox_pulse.overrides import RulesEngine
    eng = RulesEngine(yaml_path=p)
    assert eng.internal_domains == ["31c.io", "example.org"]


def test_internal_domains_returns_empty_when_missing(tmp_path):
    """19. internal_domains property returns [] when field is absent from YAML."""
    yaml_content = textwrap.dedent("""\
        sender_overrides:
          always_critical: []
          always_important: []
          always_normal: []
    """)
    p = tmp_path / "no_domains.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    from scripts.inbox_pulse.overrides import RulesEngine
    eng = RulesEngine(yaml_path=p)
    assert eng.internal_domains == []
