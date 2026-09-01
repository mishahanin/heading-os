"""Six parseable email-triage configs that took the whole rules engine down.

`scripts/inbox_pulse/overrides.RulesEngine` carries a class docstring headed
"Empty-posture fallback" which ends: "The daemon keeps running without
crashing." `reload()` checks only that the YAML ROOT is a mapping, so every
section below it can hold whatever the operator typed, and
`self._config.get(key, {})` returns the STORED value rather than the default
whenever the key is present with a null value.

MEASURED 2026-09-01 against the shipped module, one line of YAML each:

    sender_overrides:                     AttributeError 'NoneType' .get
    keyword_overrides:                    AttributeError 'NoneType' .get
    sender_overrides: "*@x.example"       AttributeError 'str' .get
    quiet_hours: "23:00-07:00"            AttributeError 'str' .get
    quiet_hours: {start: 23:00}           AttributeError 'int' .strip
    sender_overrides: {always_critical: [31337]}
                                          AttributeError 'int' .lower

The fifth is the one an operator reaches without doing anything odd. PyYAML
implements YAML 1.1, whose integer resolver accepts base 60, so an UNQUOTED
`start: 23:00` loads as the int 1380 and `_parse_hhmm` met `1380.strip()`.

`scripts/inbox_pulse/rules.CheapClassifier.classify` calls `match_sender` and
`match_keywords` with no guard of its own, and `scripts/inbox_pulse/daemon.py`
catches around `classify` and logs "Classification failed" per message. So the
consequence was not a dead daemon: it was every inbound email falling through to
LOW with the operator's whole override set silently uninvolved, one warning line
per message and nothing anywhere naming the config as the cause.

The same shape had already been fixed twice in this module, in `_coerce_number`
and in the `internal_domains` property, and the three match helpers were left
out of that pass. `tests/test_a_config_scalar_that_matched_every_sender.py`
covers the SCALAR-instead-of-list bucket and the naive-datetime defect; it never
gave a section a non-mapping value, so every case here was invisible to it.

The fix adds `_mapping`, warned once per config through the same set
`_pattern_list` uses, stringifies list entries, and makes `_parse_hhmm` refuse a
non-string so the caller emits its own "cannot parse quiet_hours" line.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

_WORKSPACE = Path(__file__).resolve().parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from scripts.inbox_pulse.overrides import RulesEngine, _parse_hhmm  # noqa: E402

_LOGGER = "scripts.inbox_pulse.overrides"


def _engine(tmp_path: Path, body: str) -> RulesEngine:
    path = tmp_path / "email-triage-rules.yaml"
    path.write_text(body, encoding="utf-8")
    return RulesEngine(yaml_path=path)


# ---------------------------------------------------------------------------
# The premise: these configs really do parse
# ---------------------------------------------------------------------------

BROKEN_SECTIONS = [
    "sender_overrides:\n",
    "keyword_overrides:\n",
    "quiet_hours:\n",
    'sender_overrides: "*@acme-telecom.example"\n',
    'keyword_overrides: "DUE"\n',
    'quiet_hours: "23:00-07:00"\n',
    "sender_overrides:\n  - always_critical\n",
    "quiet_hours:\n  - 23:00\n",
    "sender_overrides: 7\n",
]


@pytest.mark.parametrize("body", BROKEN_SECTIONS)
def test_every_config_here_is_valid_yaml_with_a_mapping_root(body):
    """Without this the file could be measuring `reload`'s parse guard instead.

    `reload()` keeps the prior config when the ROOT is not a mapping, and an
    empty config makes every helper below return None for a reason that has
    nothing to do with the finding.
    """
    parsed = yaml.safe_load(body)
    assert isinstance(parsed, dict), body
    assert len(parsed) == 1, body


def test_an_unquoted_clock_time_really_does_load_as_an_integer():
    """The premise of the `_parse_hhmm` case, stated rather than assumed.

    If PyYAML ever stops resolving base-60 integers, the case below becomes a
    string and stops measuring anything, and this line says so out loud.
    """
    assert yaml.safe_load("start: 23:00\n") == {"start": 1380}


# ---------------------------------------------------------------------------
# A section that is not a mapping is ignored, never fatal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", BROKEN_SECTIONS)
def test_no_broken_section_can_raise_out_of_match_sender(tmp_path, body):
    engine = _engine(tmp_path, body)
    assert engine.match_sender("nobody@unrelated.example") is None


@pytest.mark.parametrize("body", BROKEN_SECTIONS)
def test_no_broken_section_can_raise_out_of_match_keywords(tmp_path, body):
    engine = _engine(tmp_path, body)
    assert engine.match_keywords("invoice is due friday") is None


@pytest.mark.parametrize("body", BROKEN_SECTIONS)
def test_no_broken_section_can_raise_out_of_is_quiet_hours(tmp_path, body):
    engine = _engine(tmp_path, body)
    assert engine.is_quiet_hours(datetime(2026, 9, 1, 23, 30,
                                          tzinfo=timezone.utc)) is False


@pytest.mark.parametrize("body", BROKEN_SECTIONS)
def test_no_broken_section_can_raise_out_of_is_breakthrough_sender(tmp_path, body):
    engine = _engine(tmp_path, body)
    assert engine.is_breakthrough_sender("nobody@unrelated.example") is False


@pytest.mark.parametrize("body", BROKEN_SECTIONS)
def test_no_broken_section_can_raise_out_of_the_cost_properties(tmp_path, body):
    engine = _engine(tmp_path, body)
    assert engine.cost_ceiling_usd == 50.0
    assert engine.cost_warn_at_percent == 80


# ---------------------------------------------------------------------------
# Ignoring is not the same as being silent
# ---------------------------------------------------------------------------

def test_a_non_mapping_section_names_itself_in_the_log(tmp_path, caplog):
    """A dropped section with no log line is the failure mode this repo keeps
    finding: the override set stops applying and nothing says why."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        engine = _engine(tmp_path, 'sender_overrides: "*@acme-telecom.example"\n')
        engine.match_sender("nobody@unrelated.example")

    hits = [r.getMessage() for r in caplog.records if "not a mapping" in r.getMessage()]
    assert len(hits) == 1, hits
    assert "sender_overrides" in hits[0]
    assert "str" in hits[0]


def test_the_mapping_warning_fires_once_per_config_not_once_per_email(tmp_path,
                                                                      caplog):
    """The daemon calls `match_sender` for every inbound message."""
    engine = _engine(tmp_path, "sender_overrides:\n  - always_critical\n")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(5):
            engine.match_sender("nobody@unrelated.example")

    hits = [r for r in caplog.records if "not a mapping" in r.getMessage()]
    assert len(hits) == 1, f"expected one warning, got {len(hits)}"


def test_an_absent_section_says_nothing_at_all(tmp_path, caplog):
    """The other jaw. A config that simply omits a section is the normal case,
    and warning about it would train the operator to ignore the line."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        engine = _engine(tmp_path, "internal_domains: [acme-telecom.example]\n")
        engine.match_sender("nobody@unrelated.example")
        engine.match_keywords("anything")
        engine.is_quiet_hours(datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))

    assert [r.getMessage() for r in caplog.records
            if "not a mapping" in r.getMessage()] == []


def test_an_unquoted_quiet_hour_is_reported_rather_than_ignored(tmp_path, caplog):
    """`quiet_hours` IS a mapping here, so `_mapping` says nothing; the refusal
    has to come from the parse-failure branch, which names both values."""
    body = 'quiet_hours:\n  start: 23:00\n  end: "07:00"\n  timezone: "UTC"\n'
    engine = _engine(tmp_path, body)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert engine.is_quiet_hours(datetime(2026, 9, 1, 23, 30,
                                              tzinfo=timezone.utc)) is False

    hits = [r.getMessage() for r in caplog.records
            if "Cannot parse quiet_hours" in r.getMessage()]
    assert len(hits) == 1, hits
    assert "1380" in hits[0], hits


# ---------------------------------------------------------------------------
# A list entry that YAML typed for you
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry", ["31337", "23:00", "true", "1.5", "null"])
def test_a_natively_typed_list_entry_does_not_raise(tmp_path, entry):
    """`fnmatch` and `.lower()` both need a string, and an unquoted YAML entry
    arrives as int, float, bool or None. Only the quoted forms were ever tried."""
    engine = _engine(
        tmp_path, f"sender_overrides:\n  always_critical: [{entry}]\n")

    assert engine.match_sender("nobody@unrelated.example") is None


def test_a_natively_typed_entry_still_matches_what_it_spells(tmp_path):
    """Stringifying must read the entry the way the operator meant it, not drop
    it. A bucket of one bare number is a pattern, however odd a one."""
    engine = _engine(tmp_path, "sender_overrides:\n  always_critical: [31337]\n")

    assert engine.match_sender("31337") == "always_critical"


def test_a_null_entry_in_a_list_is_dropped_rather_than_matching_none(tmp_path):
    """`str(None)` is the four characters "None", which would be a pattern that
    matches an address nobody has. Dropping it is the honest reading."""
    engine = _engine(
        tmp_path, 'sender_overrides:\n  always_critical:\n    - \n    - "*@x.example"\n')

    assert engine.match_sender("None") is None
    assert engine.match_sender("a@x.example") == "always_critical"


# ---------------------------------------------------------------------------
# The cost casters meet a container
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["[50]", "{a: 1}", "[]", "{}"])
def test_a_container_cost_value_falls_back_instead_of_raising(tmp_path, value):
    """`_coerce_number` catches `(TypeError, ValueError)` and only the ValueError
    half had a case: the sibling file's parameters are a null, the string
    "fifty" and the string "not-a-number", all three of which are ValueError or
    the early `raw is None` return. MEASURED 2026-09-01 by narrowing the handler
    to `except ValueError` alone: the whole shard stayed green, while
    `float([50])` raises TypeError out of a property the class docstring says
    cannot crash. A YAML list is one keystroke from the scalar an operator meant.
    """
    engine = _engine(tmp_path, f"cost_ceiling:\n  monthly_anthropic_usd: {value}\n")
    assert engine.cost_ceiling_usd == 50.0

    engine = _engine(tmp_path, f"cost_ceiling:\n  warn_at_percent: {value}\n")
    assert engine.cost_warn_at_percent == 80


def test_the_container_cost_value_is_named_in_the_log(tmp_path, caplog):
    """Falling back silently would leave the operator's ceiling quietly wrong."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        engine = _engine(tmp_path, "cost_ceiling:\n  monthly_anthropic_usd: [50]\n")
        assert engine.cost_ceiling_usd == 50.0

    hits = [r.getMessage() for r in caplog.records if "is not a number" in r.getMessage()]
    assert len(hits) == 1, hits
    assert "monthly_anthropic_usd" in hits[0]


# ---------------------------------------------------------------------------
# _parse_hhmm on its own
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [1380, None, 23.0, True, ["23", "00"],
                                   {"h": 23}])
def test_parse_hhmm_refuses_every_non_string(value):
    assert _parse_hhmm(value) is None


@pytest.mark.parametrize(("value", "hour", "minute"), [
    ("23:00", 23, 0), (" 07:00 ", 7, 0), ("0:05", 0, 5),
])
def test_parse_hhmm_still_reads_the_strings_it_always_did(value, hour, minute):
    """The other direction. A refusal that refuses everything disables quiet
    hours entirely and would pass every test above."""
    parsed = _parse_hhmm(value)
    assert parsed is not None
    assert (parsed.hour, parsed.minute) == (hour, minute)


# ---------------------------------------------------------------------------
# Controls: the working config is untouched
# ---------------------------------------------------------------------------

_GOOD = (
    "sender_overrides:\n"
    '  always_critical: ["*@acme-telecom.example"]\n'
    '  always_normal: ["newsletter@*"]\n'
    "keyword_overrides:\n"
    '  promote_to_critical: ["term sheet"]\n'
    'breakthrough_allowlist: ["q@acme-telecom.example"]\n'
    'quiet_hours:\n  start: "23:00"\n  end: "07:00"\n  timezone: "UTC"\n'
    "cost_ceiling:\n  monthly_anthropic_usd: 12.5\n  warn_at_percent: 65\n"
)


def test_a_well_formed_config_is_read_exactly_as_before(tmp_path, caplog):
    engine = _engine(tmp_path, _GOOD)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert engine.match_sender("m@acme-telecom.example") == "always_critical"
        assert engine.match_sender("newsletter@example.com") == "always_normal"
        assert engine.match_sender("nobody@unrelated.example") is None
        assert engine.match_keywords("Re: term sheet") == "promote_to_critical"
        assert engine.match_keywords("lunch tomorrow") is None
        assert engine.is_breakthrough_sender("q@acme-telecom.example") is True
        assert engine.is_breakthrough_sender("nobody@unrelated.example") is False
        assert engine.is_quiet_hours(
            datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc)) is True
        assert engine.is_quiet_hours(
            datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)) is False
        assert engine.cost_ceiling_usd == 12.5
        assert engine.cost_warn_at_percent == 65

    assert [r.getMessage() for r in caplog.records] == []


def test_one_broken_section_does_not_disable_the_others(tmp_path):
    """The reason `_mapping` is per section rather than a whole-config refusal:
    an operator who breaks one line keeps the rest of their rules."""
    body = ("sender_overrides:\n"
            '  always_critical: ["*@acme-telecom.example"]\n'
            'quiet_hours: "23:00-07:00"\n')
    engine = _engine(tmp_path, body)

    assert engine.match_sender("m@acme-telecom.example") == "always_critical"
    assert engine.is_quiet_hours(
        datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc)) is False
