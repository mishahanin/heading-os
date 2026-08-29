"""The one radar line an operator could not silence was the radar's own.

`autoheal_signals` mints its escalated Tier-A signal under the key
f"{target}_autoheal". Neither `ollama_autoheal` nor `memory_index_autoheal` was
in `KNOWN_KEYS`, so once a Tier-A heal had failed AUTOHEAL_ESCALATE times the
critical line fired on every run, crunch could not suppress it (critical pierces
the floor by design), and `ack ollama_autoheal` exited 2 with "unknown signal
key". Measured: cmd_ack returned 2 and the signal stayed in `displayed`.

Accepting the key was only half of it. `cmd_ack` resolves the acked band from
`gather_live_signals`, which never carries a synthetic key, so the band fell
through to "ok" and `ack_suppressed` compared severity_rank("critical") <=
severity_rank("ok") -- false. Measured: an ack banded "ok" left the critical
line in `displayed`; banding it "critical" cleared it. So the band assertion
below is load-bearing, not decoration.

The remaining two cases pin documentation that contradicted the code: `--quiet`
was described as "counts-only" in three places while emitting the full
summaries, and the module docstring promised "Exit 0 always" while `ack` exits 2
on a typo.

Run: python3 -m pytest tests/test_a_radar_that_refused_to_silence_its_own_alarm.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "ops-radar.py"


@pytest.fixture()
def orad():
    spec = importlib.util.spec_from_file_location("ops_radar_ack_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Args:
    def __init__(self, key, ttl=None):
        self.key = key
        self.ttl = ttl


def _sig(key, *, tier, due, severity, summary=None):
    return {"key": key, "value": None, "threshold": None, "due": due,
            "severity": severity, "tier": tier,
            "summary": summary or f"{key}: due"}


def _offline(orad, signals):
    """Pin the live gatherer. `gather_live_signals` probes the ollama host over
    the network; a unit test that reaches a host measures that host."""
    orad.gather_live_signals = lambda engine_root, data_root: list(signals)


def test_every_tier_a_target_has_an_ackable_autoheal_key(orad):
    assert orad.TIER_A_TARGETS, "no Tier-A targets: the assertion below is vacuous"
    for target in orad.TIER_A_TARGETS:
        assert f"{target}_autoheal" in orad.KNOWN_KEYS


def test_the_escalated_autoheal_line_can_be_acked_into_silence(orad, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    live = [_sig("ollama", tier="A", due=True, severity="high"),
            _sig("memory_index", tier="A", due=False, severity="ok")]
    _offline(orad, live)
    orad.save_json_atomic(state / orad.AUTOHEAL_FILE,
                          {"ollama": {"failures": orad.ops.AUTOHEAL_ESCALATE + 3}})

    before = orad.assess(ROOT, ROOT, state, signals=list(live))
    assert "ollama_autoheal" in {s["key"] for s in before["displayed"]}, (
        "nothing was firing, so acking it proves nothing")

    assert orad.cmd_ack(_Args("ollama_autoheal"), state, ROOT, ROOT) == 0

    after = orad.assess(ROOT, ROOT, state, signals=list(live))
    assert "ollama_autoheal" not in {s["key"] for s in after["displayed"]}
    assert ("ollama_autoheal", "ack") in {(s["key"], s["suppressed_by"])
                                          for s in after["suppressed"]}


def test_the_ack_records_the_synthetic_signals_own_severity_band(orad, tmp_path):
    """Banding it "ok" would store an ack that silences nothing."""
    state = tmp_path / "state"
    state.mkdir()
    live = [_sig("ollama", tier="A", due=True, severity="high"),
            _sig("memory_index", tier="A", due=False, severity="ok")]
    _offline(orad, live)
    orad.save_json_atomic(state / orad.AUTOHEAL_FILE,
                          {"ollama": {"failures": orad.ops.AUTOHEAL_ESCALATE}})

    orad.cmd_ack(_Args("ollama_autoheal"), state, ROOT, ROOT)

    entry = orad.load_json(state / orad.ACK_FILE)["ollama_autoheal"]
    assert entry["acked_band"] == "critical"


def test_an_unknown_key_is_still_refused_with_exit_2(orad, tmp_path):
    """The fix widens the accepted set; it must not open it."""
    state = tmp_path / "state"
    state.mkdir()
    _offline(orad, [])
    assert orad.cmd_ack(_Args("ollama_autoheel"), state, ROOT, ROOT) == 2
    assert not (state / orad.ACK_FILE).exists()


def test_quiet_carries_the_summaries_and_says_so(orad, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    signals = [_sig("backup", tier="B", due=True, severity="warn",
                    summary="backup: 2 uncommitted (30h old)"),
               _sig("cold_sweep", tier="B", due=True, severity="high",
                    summary="cold-sweep: 9 contacts in red debt")]
    result = orad.assess(ROOT, ROOT, state, signals=signals, autoheal={})
    assert "cold-sweep: 9 contacts in red debt" in result["quiet_line"]
    assert "backup: 2 uncommitted (30h old)" in result["quiet_line"]

    quiet_help = next(a for a in orad.build_parser()._actions
                      if a.dest == "quiet").help
    assert "counts-only" not in quiet_help.lower()
    assert "counts-only" not in orad.__doc__.lower()


def test_the_docstring_does_not_promise_exit_0_always(orad):
    assert "Exit 0 always" not in orad.__doc__
    assert "ack" in orad.__doc__ and "exits 2" in orad.__doc__
