"""The adoption report renders the gate; it must not compute a second one.

The Phase 1 -> Phase 2 gate decides whether the bridge ships to executives. Until
2026-08-23 two tools in this repo answered that question with different numbers:
`scripts/bridge-adoption-report.py` carried its own copy of the three metrics,
and `scripts/bridge_daemon/adoption.py` — the copy the daemon serves at
`/telemetry/summary` — carried the other. On the fortnight built below they
reported 5.0 min against 0.8 min, 1.0 clicks against 0.14, and 50% browser-first
against 20%.

The metric SEMANTICS are pinned in `tests/bridge/test_adoption.py`, against the
single implementation. What is pinned here is that the report has no second one:
its output is a rendering of `adoption.summarize()` and nothing else.
"""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon import adoption  # noqa: E402

# Load scripts/bridge-adoption-report.py via importlib (hyphen-in-name).
_REPORT_PATH = ROOT / "scripts" / "bridge-adoption-report.py"
_spec = importlib.util.spec_from_file_location("bridge_adoption_report", _REPORT_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["bridge_adoption_report"] = _mod
_spec.loader.exec_module(_mod)


# The fortnight that separated the two implementations. Every line is a trap for
# one of the four divergences: a page_view with no `duration_s` (the 30-second
# floor), an out-of-order append (timestamp ordering vs file ordering), and two
# active days inside a ten-weekday window (denominator).
DIVERGENT_EVENTS = [
    {"ts": "2026-05-18T09:00:00+00:00", "event": "page_view"},
    {"ts": "2026-05-18T10:00:00+00:00", "event": "launch"},
    {"ts": "2026-05-19T11:00:00+00:00", "event": "launch"},
    {"ts": "2026-05-19T08:00:00+00:00", "event": "page_view", "duration_s": 600},
]
TODAY = date(2026, 5, 20)

# Captured before any test can monkeypatch it.
_real_summarize = adoption.summarize


@pytest.fixture()
def workspace(tmp_path):
    state = tmp_path / ".daemon-state"
    state.mkdir()
    (state / "usage.jsonl").write_text(
        "\n".join(json.dumps(e) for e in DIVERGENT_EVENTS) + "\n", encoding="utf-8")
    return tmp_path


def test_the_report_has_no_metric_math_of_its_own(workspace):
    """The structural half: no second implementation to drift."""
    source = _REPORT_PATH.read_text(encoding="utf-8")
    for banned in ("def compute_metrics", "def _evaluate_gate", "def _load_events"):
        assert banned not in source, (
            f"{banned} is back in the report; the gate has two answers again"
        )
    assert "adoption.summarize" in source


def test_the_printed_numbers_are_the_shared_summary(workspace, monkeypatch, capsys):
    """The behavioural half, on the data that used to separate the two."""
    monkeypatch.setattr(_mod, "WORKSPACE", workspace)
    monkeypatch.setattr(_mod, "USAGE", workspace / ".daemon-state" / "usage.jsonl")
    monkeypatch.setattr(
        adoption, "summarize",
        lambda root, days=14, today=None: _real_summarize(root, days, TODAY))

    assert _mod.main() == 0
    out = capsys.readouterr().out

    truth = _real_summarize(workspace, 14, TODAY)
    assert str(truth["metrics"]["avg_tab_time_min_per_day"]) in out
    assert str(truth["metrics"]["avg_actions_per_day"]) in out
    assert f"{round(truth['metrics']['browser_first_pct_weekdays'] * 100, 1)}%" in out
    assert f"{truth['totals']['weekdays_in_window']} weekdays" in out



def test_the_old_numbers_are_gone(workspace, monkeypatch, capsys):
    """A regression pin with real figures, so a silent revert is visible.

    5.0 min, 1.0 clicks and 50.0% are what the deleted copy printed on
    DIVERGENT_EVENTS. The shared summary says 0.8, 0.14 and 20.0%.
    """
    monkeypatch.setattr(_mod, "WORKSPACE", workspace)
    monkeypatch.setattr(_mod, "USAGE", workspace / ".daemon-state" / "usage.jsonl")
    monkeypatch.setattr(
        adoption, "summarize",
        lambda root, days=14, today=None: _real_summarize(root, days, TODAY))
    _mod.main()
    out = capsys.readouterr().out
    assert "0.8 min" in out and "0.14" in out and "20.0%" in out
    assert "5.0 min" not in out and "50.0%" not in out


def test_a_missing_usage_log_exits_non_zero_with_a_plain_message(tmp_path, monkeypatch, capsys):
    """Console-first: degrade clearly, never silently."""
    monkeypatch.setattr(_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(_mod, "USAGE", tmp_path / ".daemon-state" / "usage.jsonl")
    assert _mod.main() == 1
    assert "No usage data yet" in capsys.readouterr().err


def test_an_empty_window_exits_non_zero(tmp_path, monkeypatch, capsys):
    """A log that exists but holds nothing usable is not a zero-metric PASS."""
    state = tmp_path / ".daemon-state"
    state.mkdir()
    (state / "usage.jsonl").write_text("not json\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(_mod, "USAGE", state / "usage.jsonl")
    assert _mod.main() == 1
    assert "no parseable events" in capsys.readouterr().err
