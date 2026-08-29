"""Three ways a parseable email-triage config broke the rules engine.

All measured 2026-08-29 against `scripts/inbox_pulse/overrides.py`:

1. A bucket written as a bare scalar, `always_critical: "*@acme-telecom.example"`,
   was iterated CHARACTER by character. The first character is `*`, and
   `fnmatch(addr, "*")` is true for everything, so `match_sender` returned
   "always_critical" for `nobody@unrelated.example`. Every inbound message
   short-circuits to HIGH_LIKELY from there. The same shape made every sender a
   breakthrough sender, and the scalar keyword "DUE" degraded to `"d" in
   haystack`, which matched "lunch tomorrow".

2. `cost_ceiling: {warn_at_percent:}` is a valid YAML null, and `int(None)`
   raised TypeError out of a property whose class docstring promises the daemon
   keeps running. `monthly_anthropic_usd: "fifty"` raised ValueError the same way.

3. `is_quiet_hours` fed a naive datetime to `astimezone()`, which assumes the
   HOST's local time. The same 23:30 against the same UTC 23:00-07:00 window
   answered True on a UTC host and False on an Asia/Dubai one.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_WORKSPACE = Path(__file__).resolve().parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from scripts.inbox_pulse.overrides import RulesEngine  # noqa: E402


def _engine(tmp_path: Path, body: str) -> RulesEngine:
    path = tmp_path / "email-triage-rules.yaml"
    path.write_text(body, encoding="utf-8")
    return RulesEngine(yaml_path=path)


# ---------------------------------------------------------------------------
# 1. Scalar buckets
# ---------------------------------------------------------------------------


def test_a_scalar_sender_bucket_does_not_match_every_sender(tmp_path):
    engine = _engine(
        tmp_path,
        'sender_overrides:\n  always_critical: "*@acme-telecom.example"\n',
    )

    assert engine.match_sender("nobody@unrelated.example") is None
    assert engine.match_sender("james.bond@example.com") is None


def test_a_scalar_sender_bucket_still_matches_what_it_names(tmp_path):
    """Wrapping the scalar must read it the way the operator meant it."""
    engine = _engine(
        tmp_path,
        'sender_overrides:\n  always_critical: "*@acme-telecom.example"\n',
    )

    assert engine.match_sender("m@acme-telecom.example") == "always_critical"
    assert engine.match_sender("M@Acme-Telecom.Example") == "always_critical"


def test_a_list_sender_bucket_is_unchanged(tmp_path):
    engine = _engine(
        tmp_path,
        "sender_overrides:\n"
        '  always_critical: ["*@acme-telecom.example"]\n'
        '  always_normal: ["newsletter@*"]\n',
    )

    assert engine.match_sender("q@acme-telecom.example") == "always_critical"
    assert engine.match_sender("newsletter@example.com") == "always_normal"
    assert engine.match_sender("nobody@unrelated.example") is None


def test_a_scalar_breakthrough_allowlist_does_not_admit_everyone(tmp_path):
    engine = _engine(tmp_path, 'breakthrough_allowlist: "*@acme-telecom.example"\n')

    assert engine.is_breakthrough_sender("nobody@unrelated.example") is False
    assert engine.is_breakthrough_sender("q@acme-telecom.example") is True


def test_a_scalar_keyword_bucket_is_not_a_single_character_search(tmp_path):
    engine = _engine(
        tmp_path, 'keyword_overrides:\n  promote_to_critical: "DUE"\n'
    )

    assert engine.match_keywords("lunch tomorrow") is None
    assert engine.match_keywords("invoice is due friday") == "promote_to_critical"


def test_a_scalar_bucket_is_reported_once_not_once_per_email(tmp_path, caplog):
    engine = _engine(
        tmp_path,
        'sender_overrides:\n  always_critical: "*@acme-telecom.example"\n',
    )

    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        for _ in range(5):
            engine.match_sender("nobody@unrelated.example")

    hits = [r for r in caplog.records if "not a list" in r.message]
    assert len(hits) == 1, f"expected one warning, got {len(hits)}"


# ---------------------------------------------------------------------------
# 2. Cost properties
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, expected_ceiling, expected_percent",
    [
        ("cost_ceiling:\n  monthly_anthropic_usd:\n  warn_at_percent:\n", 50.0, 80),
        ('cost_ceiling:\n  monthly_anthropic_usd: "fifty"\n', 50.0, 80),
        ("cost_ceiling:\n  warn_at_percent: not-a-number\n", 50.0, 80),
        ("sender_overrides: {}\n", 50.0, 80),
    ],
)
def test_a_bad_cost_ceiling_falls_back_instead_of_raising(
    tmp_path, body, expected_ceiling, expected_percent
):
    engine = _engine(tmp_path, body)

    assert engine.cost_ceiling_usd == expected_ceiling
    assert engine.cost_warn_at_percent == expected_percent


def test_a_configured_cost_ceiling_is_still_honoured(tmp_path):
    engine = _engine(
        tmp_path,
        "cost_ceiling:\n  monthly_anthropic_usd: 12.5\n  warn_at_percent: 65\n",
    )

    assert engine.cost_ceiling_usd == 12.5
    assert engine.cost_warn_at_percent == 65


# ---------------------------------------------------------------------------
# 3. Quiet hours and the host clock
# ---------------------------------------------------------------------------


_QUIET_YAML = 'quiet_hours:\n  start: "23:00"\n  end: "07:00"\n  timezone: "UTC"\n'


@pytest.mark.parametrize("host_tz", ["UTC", "Asia/Dubai", "America/New_York"])
def test_a_naive_time_answers_the_same_on_every_host(tmp_path, monkeypatch, host_tz):
    import time as _time

    engine = _engine(tmp_path, _QUIET_YAML)

    monkeypatch.setenv("TZ", host_tz)
    _time.tzset()
    try:
        # noqa DTZ001: naive is the case under test. An aware datetime cannot
        # reproduce the defect, because the host offset is only consulted when
        # tzinfo is absent.
        assert engine.is_quiet_hours(datetime(2026, 9, 1, 23, 30)) is True  # noqa: DTZ001
        assert engine.is_quiet_hours(datetime(2026, 9, 1, 12, 0)) is False  # noqa: DTZ001
    finally:
        monkeypatch.undo()
        _time.tzset()


def test_an_aware_time_is_not_reinterpreted(tmp_path):
    engine = _engine(tmp_path, _QUIET_YAML)

    # 23:30 in Asia/Dubai is 19:30 UTC, which is outside the UTC quiet window.
    import zoneinfo

    dubai = zoneinfo.ZoneInfo("Asia/Dubai")
    assert engine.is_quiet_hours(datetime(2026, 9, 1, 23, 30, tzinfo=dubai)) is False
    assert (
        engine.is_quiet_hours(datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc)) is True
    )
