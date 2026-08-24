"""Shard 04-p3: three sentences that described a tool other than the one running.

`deep-research-advance.py` says its Phase 2 retry runs "with a longer timeout".
Both attempts passed a flat `timeout=180.0`, and the stated cause -- "cloud
latency on a large reasoning prompt" -- is exactly the case where a slow prompt
hits the same ceiling twice. The retry burned a second full request to fail
identically, and the run degraded to corpus-without-analysis with a mitigation
that existed only on paper.

Its exit-code table said a bad `--depth` is an argparse usage error, exit 2. The
code clamps: `--depth 0` becomes 1 and `--depth 99` becomes 8, silently, exit 0.
The same table mapped the `RuntimeError` safety net onto exit 2 as well, so a
caller could not tell "you typed it wrong" from "the proxy is down" -- and only
one of those is worth retrying.

`design-engine.py` starts its poll clock AFTER the initial POST. That POST
carries `Prefer: wait`, which the comment beside the clock says Replicate holds
open for up to a minute, and it runs under its own `POLL_TIMEOUT`-second urlopen
timeout. So the single longest wait sat outside the budget the timeout message
advertises, and "budget 120s" could print after 240 seconds of real blocking.
The earlier fix made the LOOP honest and left this.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dra = _load("deep-research-advance", "deep_research_advance_04p3")
de = _load("design-engine", "design_engine_04p3")


# ==========================================================================
# 1 - the retry that promised a longer timeout
# ==========================================================================

def _run_capturing_reason_timeouts(tmp_path, reason_side_effect):
    """Run once, returning the `timeout=` of every kimi_reason call."""
    timeouts = []

    def _spy(prompt, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        result = reason_side_effect.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with mock.patch.object(dra, "kimi_reason", side_effect=_spy), \
         mock.patch.object(dra, "probe_proxy", return_value=["k3"]), \
         mock.patch.object(dra, "pplx_research",
                           return_value=("finding", ["https://a.com"])), \
         mock.patch.object(dra, "get_outputs_dir", return_value=tmp_path):
        run_dir = dra.run("what is X?", depth=1, critical=False)
    return timeouts, run_dir


def test_the_retry_runs_with_a_longer_timeout(tmp_path):
    """A flat timeout on both attempts is a plain retry, not a mitigation."""
    timeouts, _ = _run_capturing_reason_timeouts(tmp_path, [
        json.dumps(["sub one"]),                       # Phase 0 decompose
        RuntimeError("read timed out"),                # Phase 2, attempt 0
        json.dumps({"summary": "s", "claims": [], "contradictions": []}),
    ])
    phase2 = timeouts[1:]
    assert len(phase2) == 2, "the retry did not happen"
    assert phase2[1] > phase2[0], \
        "the second attempt reused the ceiling the first one hit"


def test_the_first_attempt_keeps_the_original_timeout(tmp_path):
    timeouts, _ = _run_capturing_reason_timeouts(tmp_path, [
        json.dumps(["sub one"]),
        json.dumps({"summary": "s", "claims": [], "contradictions": []}),
    ])
    assert timeouts[1] == dra.REASON_TIMEOUTS_S[0] == 180.0


def test_the_timeouts_are_indexed_by_attempt():
    assert len(dra.REASON_TIMEOUTS_S) == 2
    assert dra.REASON_TIMEOUTS_S[1] > dra.REASON_TIMEOUTS_S[0]


def test_a_success_on_the_first_attempt_makes_no_second_call(tmp_path):
    timeouts, _ = _run_capturing_reason_timeouts(tmp_path, [
        json.dumps(["sub one"]),
        json.dumps({"summary": "s", "claims": [], "contradictions": []}),
    ])
    assert len(timeouts) == 2, "a successful attempt was retried anyway"


def test_two_failures_still_degrade_rather_than_abort(tmp_path):
    _timeouts, run_dir = _run_capturing_reason_timeouts(tmp_path, [
        json.dumps(["sub one"]),
        RuntimeError("read timed out"),
        RuntimeError("read timed out"),
    ])
    data = json.loads((run_dir / "intermediate.json").read_text())
    assert data["degraded"] is True
    assert data["corpus"], "the corpus was thrown away with the analysis"


# ==========================================================================
# 2 - the exit code that called a proxy failure a typo
# ==========================================================================

@pytest.mark.parametrize("given,expected", [(0, 1), (1, 1), (99, 8), (8, 8), (-5, 1)])
def test_an_out_of_range_depth_is_clamped_not_refused(given, expected, tmp_path):
    seen = {}

    def _fake_run(question, *, depth, **kwargs):
        seen["depth"] = depth
        return tmp_path

    with mock.patch.object(dra, "run", _fake_run):
        assert dra.main(["q", "--depth", str(given)]) == 0
    assert seen["depth"] == expected, \
        "the documented exit-2 refusal never existed; the clamp is the behaviour"


def test_a_non_integer_depth_is_still_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        dra.main(["q", "--depth", "abc"])
    assert exc.value.code == 2


def test_conflicting_domain_flags_are_still_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        dra.main(["q", "--domains", "a.com", "--exclude-domains", "b.com"])
    assert exc.value.code == 2


def test_a_runtime_failure_is_distinguishable_from_a_typo(capsys):
    """The proxy being down and the operator mistyping are different answers."""
    with mock.patch.object(dra, "run", side_effect=RuntimeError("proxy unreachable")):
        code = dra.main(["q"])
    assert code != 2, "a transport failure was reported as an argparse usage error"
    assert code == 4
    assert "proxy unreachable" in capsys.readouterr().err


def test_the_exit_table_documents_the_code_it_returns():
    """The table is the contract a wrapper reads; it must name every code."""
    doc = (ROOT / "scripts" / "deep-research-advance.py").read_text(encoding="utf-8")
    header = doc.split('"""')[1]
    assert "  4  " in header, "exit 4 is returned and undocumented"
    assert "clamped" in header, "the table still implies an out-of-range depth is refused"


# ==========================================================================
# 3 - the budget that started after the longest wait
# ==========================================================================

class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(de.time, "monotonic", clock)
    monkeypatch.setattr(de.time, "sleep", lambda s: clock.advance(s))
    return clock


def test_a_slow_initial_post_counts_against_the_budget(fake_clock, monkeypatch, capsys):
    """`Prefer: wait` can hold the POST open; that wait is part of the total.

    A timeout eventually fires either way -- the poll sleeps reach the budget
    on their own -- so THAT is not the discriminator. What separates an honest
    clock from a dishonest one is how much budget is left when the first poll
    comes round: none at all, so no GET is ever issued.
    """
    gets = {"n": 0}

    def _api(method, path, token, payload=None):
        if method == "POST":
            fake_clock.advance(de.POLL_TIMEOUT + 1)   # held open past the budget
            return {"id": "p1", "status": "starting"}
        gets["n"] += 1
        return {"id": "p1", "status": "starting"}

    monkeypatch.setattr(de, "_api_request", _api)
    with pytest.raises(SystemExit) as exc:
        de._create_prediction("tok", "owner/name", {})
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Timed out" in err, "the POST's own wait never reached the budget"
    assert gets["n"] == 0, \
        "the budget was already spent, yet polling started as if it were untouched"


def test_the_reported_elapsed_includes_the_post(fake_clock, monkeypatch, capsys):
    """A 100s POST leaves 20s of a 120s budget, which is ~10 polls, not ~60."""
    gets = {"n": 0}

    def _api(method, path, token, payload=None):
        if method == "POST":
            fake_clock.advance(100.0)
            return {"id": "p1", "status": "starting"}
        gets["n"] += 1
        return {"id": "p1", "status": "starting"}

    monkeypatch.setattr(de, "_api_request", _api)
    with pytest.raises(SystemExit):
        de._create_prediction("tok", "owner/name", {})
    reported = capsys.readouterr().err
    seconds = int(reported.split("Timed out after ")[1].split("s")[0])
    assert seconds > de.POLL_TIMEOUT, \
        "the elapsed figure excluded the longest single wait"
    remaining = de.POLL_TIMEOUT - 100.0
    assert gets["n"] <= remaining / de.POLL_INTERVAL, \
        f"{gets['n']} polls ran on a budget with only {remaining:.0f}s left"


def test_a_prompt_success_does_not_time_out(fake_clock, monkeypatch):
    def _api(method, path, token, payload=None):
        return {"id": "p1", "status": "succeeded", "output": "u"}

    monkeypatch.setattr(de, "_api_request", _api)
    assert de._create_prediction("tok", "owner/name", {})["status"] == "succeeded"


def test_a_failed_prediction_still_exits_one(fake_clock, monkeypatch, capsys):
    def _api(method, path, token, payload=None):
        return {"id": "p1", "status": "failed", "error": "bad input"}

    monkeypatch.setattr(de, "_api_request", _api)
    with pytest.raises(SystemExit) as exc:
        de._create_prediction("tok", "owner/name", {})
    assert exc.value.code == 1
    assert "bad input" in capsys.readouterr().err


def test_the_poll_loop_still_measures_request_time(fake_clock, monkeypatch, capsys):
    """Each GET's own duration counts, not just the sleeps."""
    calls = {"n": 0}

    def _api(method, path, token, payload=None):
        if method == "POST":
            return {"id": "p1", "status": "starting"}
        calls["n"] += 1
        fake_clock.advance(de.POLL_TIMEOUT)   # one slow GET blows the budget
        return {"id": "p1", "status": "starting"}

    monkeypatch.setattr(de, "_api_request", _api)
    with pytest.raises(SystemExit):
        de._create_prediction("tok", "owner/name", {})
    assert calls["n"] <= 2, "a slow request was invisible to the budget"
