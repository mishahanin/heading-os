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

THE VERDICTS WERE NOT PART OF THAT RENDERING UNTIL 2026-08-31. Three numbers
were asserted and the three `[PASS]`/`[FAIL]` markers beside them were not, nor
was the closing line. Measured against this tree, each mutation applied alone to
`scripts/bridge-adoption-report.py`:

    baseline (unmutated)                          -> 1312 passed, 1 skipped
    _verdict: "FAIL" if passed else "PASS"        -> 1312 passed, 1 skipped
    if not gate["all_pass"]: (closing line swap)  -> 1312 passed, 1 skipped

So this CLI could have printed `[PASS]` on all three gates of a fortnight that
failed all three, and "All three quantitative gates PASS" underneath, with
nothing red anywhere in the suite. It is the console-first surface for the
Phase 1 to Phase 2 decision; the number and the verdict beside it are one
claim, and a reader acts on the verdict.

The three fortnights below are all-fail, all-pass, and exactly-one-fail. The
third is what pins each verdict to its own LINE: swapping which boolean feeds
which row is invisible when every row agrees.
"""
import importlib.util
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon import adoption  # noqa: E402
from scripts.utils.workspace import get_default_tz  # noqa: E402

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


# ============================================================
# The verdicts, which are as much of the output as the numbers
# ============================================================

def _weekday_fortnight(today: date, tab_minutes: int, launches: int) -> list[dict]:
    """A 14-day window whose every weekday opens on the dashboard.

    Window 2026-05-07 .. 2026-05-20 holds 10 weekdays. The page_view is at 07:30
    local so the day reads browser-first; the launches follow from 09:00.
    """
    tz = get_default_tz()
    events: list[dict] = []
    for offset in range(14):
        d = today - timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        first = datetime(d.year, d.month, d.day, 7, 30, tzinfo=tz)
        events.append({"ts": first.isoformat(), "event": "page_view",
                       "page": "pulse", "duration_s": tab_minutes * 60})
        for h in range(9, 9 + launches):
            events.append({"ts": datetime(d.year, d.month, d.day, h, 0,
                                          tzinfo=tz).isoformat(),
                           "event": "launch", "action": "x"})
    return events


def _run(tmp_path: Path, events: list[dict], monkeypatch, capsys) -> str:
    state = tmp_path / ".daemon-state"
    state.mkdir(exist_ok=True)
    (state / "usage.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    monkeypatch.setattr(_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(_mod, "USAGE", state / "usage.jsonl")
    monkeypatch.setattr(
        adoption, "summarize",
        lambda root, days=14, today=None: _real_summarize(root, days, TODAY))
    assert _mod.main() == 0
    return capsys.readouterr().out


_VERDICT_RE = re.compile(r"^  (?P<label>[^:]+):.*\[(?P<verdict>PASS|FAIL)\]$", re.M)


def _verdicts(out: str) -> dict:
    """{gate label -> PASS|FAIL}, read off the rendered rows.

    Per row, not as a set: the point is which verdict landed on which line.
    """
    found = {m.group("label").strip(): m.group("verdict")
             for m in _VERDICT_RE.finditer(out)}
    assert len(found) == 3, f"expected three verdict rows, parsed {found} from:\n{out}"
    return found


def test_a_fortnight_that_fails_every_gate_prints_three_fails(workspace, monkeypatch, capsys):
    """DIVERGENT_EVENTS is 0.8 min, 0.14 clicks and 20% browser-first."""
    out = _run(workspace, DIVERGENT_EVENTS, monkeypatch, capsys)
    assert _verdicts(out) == {
        "Avg daily tab-time": "FAIL",
        "Avg daily action-clicks": "FAIL",
        "Browser-first mornings": "FAIL",
    }
    assert "At least one quantitative gate FAIL" in out
    assert "All three quantitative gates PASS" not in out


def test_a_fortnight_that_passes_every_gate_prints_three_passes(tmp_path, monkeypatch, capsys):
    """10 weekdays * 50 min = 35.7 min/day, * 8 launches = 5.71 clicks/day."""
    out = _run(tmp_path, _weekday_fortnight(TODAY, tab_minutes=50, launches=8),
               monkeypatch, capsys)
    assert _verdicts(out) == {
        "Avg daily tab-time": "PASS",
        "Avg daily action-clicks": "PASS",
        "Browser-first mornings": "PASS",
    }
    assert "All three quantitative gates PASS" in out
    assert "At least one quantitative gate FAIL" not in out


def test_each_verdict_lands_on_its_own_row(tmp_path, monkeypatch, capsys):
    """One failing gate out of three, so the rows cannot agree by accident.

    10 weekdays * 50 min = 35.7 min/day (pass), * 2 launches = 1.43 clicks/day
    (fail), 10 of 10 browser-first mornings (pass). A report that read
    `actions_pass` for the tab-time row would print the FAIL one line up.
    """
    out = _run(tmp_path, _weekday_fortnight(TODAY, tab_minutes=50, launches=2),
               monkeypatch, capsys)
    assert _verdicts(out) == {
        "Avg daily tab-time": "PASS",
        "Avg daily action-clicks": "FAIL",
        "Browser-first mornings": "PASS",
    }
    assert "At least one quantitative gate FAIL" in out


def test_the_printed_verdicts_agree_with_the_summary_they_render(tmp_path, monkeypatch, capsys):
    """The invariant behind the three fixtures above, stated once.

    Whatever the window, the row verdicts are the shared summary's booleans.
    Nothing here decides PASS or FAIL for itself.
    """
    events = _weekday_fortnight(TODAY, tab_minutes=50, launches=2)
    out = _run(tmp_path, events, monkeypatch, capsys)
    gate = _real_summarize(tmp_path, 14, TODAY)["gate"]
    expected = {
        "Avg daily tab-time": "PASS" if gate["tab_time_pass"] else "FAIL",
        "Avg daily action-clicks": "PASS" if gate["actions_pass"] else "FAIL",
        "Browser-first mornings": "PASS" if gate["browser_first_pass"] else "FAIL",
    }
    assert _verdicts(out) == expected
