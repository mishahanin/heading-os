"""A YAML boolean in a cadence field must fall back to the default.

`bool` subclasses `int`, so both numeric guards in `watchdog_core` let one
through and turned it into the number 1:

  - `_seconds` inside `load_cadence` called `int(entry[key])`, so
    `cadence.fireside.expected: true` became a ONE-SECOND expected cadence.
    Every beat then looked overdue, a healthy daemon classified `silent`, and
    the CEO was paged about a process that was running normally.
  - `_realert_minutes` tested `isinstance(v, (int, float)) and v > 0`, so
    `realert_minutes: yes` became a one-MINUTE re-alert window, turning a
    sustained outage into a critical alert every minute. That is the exact spam
    the dedup model exists to prevent.

`yes`, `true` and `on` are all `True` under a YAML 1.1 loader, so this is a
plain configuration typo, not an exotic input. The rest of this file already
hardens malformed cadence VALUES (TypeError/ValueError) and malformed
CONTAINERS; `bool` passed both because it raises nothing.

No daemon is started and no config file on disk is touched: `load_config` is
replaced with a callable returning the mapping under test.

Run: python3 -m pytest tests/test_a_cadence_that_read_yes_as_one_second.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import watchdog_core
from scripts.bridge_daemon import config as bridge_config

# Every YAML 1.1 spelling of true that a hand-edited config can carry. The
# loader has already collapsed them to one object by the time watchdog_core
# sees them, which is precisely why the numeric guard could not tell them from
# a number the operator meant.
TRUTHY_YAML_SPELLINGS = ("yes", "true", "on", "True")


@pytest.fixture
def config(monkeypatch):
    """Install a load_config double; return a setter for the mapping it yields."""
    def _set(mapping):
        monkeypatch.setattr(bridge_config, "load_config", lambda root: mapping)
    return _set


def test_the_spellings_this_pins_all_parse_to_one_object(config):
    """The corpus is non-empty and really does collapse to `True`."""
    yaml = pytest.importorskip("yaml")
    assert TRUTHY_YAML_SPELLINGS
    for spelling in TRUTHY_YAML_SPELLINGS:
        loaded = yaml.safe_load(f"v: {spelling}")["v"]
        assert loaded is True, (spelling, loaded)


def test_a_boolean_expected_does_not_become_a_one_second_cadence(tmp_path, config):
    config({"daemon": {"watchdog": {
        "expect": ["fireside"],
        "cadence": {"fireside": {"expected": True, "grace": True}},
    }}})

    cadence = watchdog_core.load_cadence(tmp_path)

    assert cadence == {"fireside": (watchdog_core.DEFAULT_EXPECTED_S,
                                    watchdog_core.DEFAULT_GRACE_S)}


def test_a_boolean_false_is_rejected_too(tmp_path, config):
    """`expected: no` is `False`, which `int()` reads as 0: never-overdue."""
    config({"daemon": {"watchdog": {
        "expect": ["bridge"],
        "cadence": {"bridge": {"expected": False, "grace": False}},
    }}})

    assert watchdog_core.load_cadence(tmp_path) == {
        "bridge": (watchdog_core.DEFAULT_EXPECTED_S, watchdog_core.DEFAULT_GRACE_S)}


def test_a_boolean_realert_does_not_become_a_one_minute_window(tmp_path, config):
    config({"daemon": {"watchdog": {"realert_minutes": True}}})

    assert watchdog_core._realert_minutes(tmp_path) == watchdog_core.DEFAULT_REALERT_MIN


def test_a_real_number_still_wins_in_both_guards(tmp_path, config):
    """The bool exclusion must not reject the values operators actually set."""
    config({"daemon": {"watchdog": {
        "expect": ["sentinel"],
        "realert_minutes": 45,
        "cadence": {"sentinel": {"expected": 300, "grace": 600}},
    }}})

    assert watchdog_core.load_cadence(tmp_path) == {"sentinel": (300, 600)}
    assert watchdog_core._realert_minutes(tmp_path) == 45
