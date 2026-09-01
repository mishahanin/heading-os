#!/usr/bin/env python3
"""`ops-radar.py` read its own three state files and crashed on valid JSON.

`load_json` was widened on 2026-09-01 to catch `UnicodeDecodeError` beside
`json.JSONDecodeError`, so a file that cannot be DECODED now degrades to `{}`.
That closed half the hole. The other half is a file that decodes and parses
cleanly and is the wrong SHAPE: `load_json` hands it straight back, and every
consumer in this module then called `.get` or `int()` on it.

MEASURED 2026-09-01, calling `assess` with each payload written to the named
file and the other two absent:

    autoheal.json  {"ollama": "broken"}            AttributeError: 'str' object has no attribute 'get'
    autoheal.json  {"ollama": {"failures": "many"}} ValueError: invalid literal for int() with base 10: 'many'
    ack.json       ["not", "a", "dict"]            AttributeError: 'list' object has no attribute 'get'
    ack.json       {"backup": "yesterday"}         AttributeError: 'str' object has no attribute 'get'
    crunch.json    [1, 2, 3]                       AttributeError: 'list' object has no attribute 'get'

and separately `record_heal_result({"ollama": "broken"}, "ollama", False)`
raised `ValueError: dictionary update sequence element #0 has length 1`.

None of those five is in any except clause in this module, so ONE malformed
state file took the entire radar pass down - every other signal with it. That is
the signal-killing shape `ops_signals` has now been fixed for five times
(`queue_state`, `odin_cadence_state`, `_read_trend_records`, `publish_state`,
`_index_source_globs`, each guarding with `isinstance`); `ops-radar.py`'s own
state readers were the copy nobody updated.

A sixth defect sits in `parse_ttl`, whose `except ValueError` exists so a
malformed `--ttl` falls back to the per-key default. `OverflowError` is not a
`ValueError`: MEASURED, `parse_ttl("infd", "backup")` and `parse_ttl("1e400",
"backup")` both raised `OverflowError: cannot convert float infinity to
integer` out of `cmd_ack`, a traceback where the module docstring contracts
exit 2 for bad usage. `"nand"` was already fine, because `int(nan)` IS a
ValueError - which is exactly why the gap was invisible.

The degradation is NOT silent. `_state_dict` names the file it dropped on
stderr, so a radar running on defaults says which file it stopped trusting;
a run that degrades quietly is the other half of this defect class.

Run: .venv/bin/python -m pytest \
     tests/test_a_radar_that_died_on_the_state_it_writes_itself.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "ops-radar.py"


@pytest.fixture()
def orad():
    spec = importlib.util.spec_from_file_location("ops_radar_shape_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sig(key, *, tier, due, severity):
    return {"key": key, "value": None, "threshold": None, "due": due,
            "severity": severity, "tier": tier, "summary": f"{key}: due"}


LIVE = [_sig("ollama", tier="A", due=True, severity="high"),
        _sig("backup", tier="B", due=True, severity="warn")]


@pytest.fixture()
def state(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


# ============================================================
# 1 - one malformed state file must not take the pass down
# ============================================================

WRONG_SHAPES = [
    ("autoheal.json", {"ollama": "broken"}, "autoheal-entry-is-a-string"),
    ("autoheal.json", {"ollama": {"failures": "many"}}, "autoheal-failures-not-a-number"),
    ("autoheal.json", {"ollama": {"failures": None}}, "autoheal-failures-is-null"),
    ("autoheal.json", ["ollama"], "autoheal-is-a-list"),
    ("autoheal.json", "broken", "autoheal-is-a-string"),
    ("ack.json", ["not", "a", "dict"], "ack-is-a-list"),
    ("ack.json", {"backup": "yesterday"}, "ack-entry-is-a-string"),
    ("ack.json", {"backup": {"acked_at": "soon", "ttl_seconds": 10}}, "ack-acked-at-not-a-number"),
    ("ack.json", None, "ack-is-null"),
    ("crunch.json", [1, 2, 3], "crunch-is-a-list"),
    ("crunch.json", 7, "crunch-is-a-number"),
]


@pytest.mark.parametrize("name,payload,_id", WRONG_SHAPES,
                         ids=[c[2] for c in WRONG_SHAPES])
def test_a_state_file_of_the_wrong_shape_does_not_kill_the_pass(
        orad, state, name, payload, _id):
    (state / name).write_text(json.dumps(payload), encoding="utf-8")
    result = orad.assess(ROOT, ROOT, state, signals=list(LIVE))
    # The due Tier-B signal is what the operator loses when the pass dies.
    assert "backup" in {s["key"] for s in result["displayed"]}, result


def test_the_dropped_state_file_is_named_on_stderr(orad, state, capsys):
    """A degraded read that says nothing is the other half of this defect."""
    (state / "ack.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    orad.assess(ROOT, ROOT, state, signals=list(LIVE))
    err = capsys.readouterr().err
    assert "ack.json" in err, err
    assert "list" in err, err


def test_a_well_formed_state_file_is_still_read_and_says_nothing(orad, state, capsys):
    """The negative case: the guard must not fire on, or discard, good state."""
    orad.save_json_atomic(state / "crunch.json", {"on": True, "since": 0})
    result = orad.assess(ROOT, ROOT, state, signals=list(LIVE))
    assert result["crunch_on"] is True
    # crunch suppresses non-critical Tier-B, which proves the value was USED.
    assert "backup" not in {s["key"] for s in result["displayed"]}
    assert ("backup", "crunch") in {(s["key"], s["suppressed_by"])
                                    for s in result["suppressed"]}
    assert capsys.readouterr().err == ""


def test_a_well_formed_ack_still_suppresses(orad, state):
    """Bind the guard to the thing it models, not to itself."""
    orad.save_json_atomic(
        state / "ack.json",
        {"backup": {"acked_at": 1_000.0, "ttl_seconds": 86_400, "acked_band": "warn"}})
    result = orad.assess(ROOT, ROOT, state, signals=list(LIVE), now=1_100.0)
    assert ("backup", "ack") in {(s["key"], s["suppressed_by"])
                                 for s in result["suppressed"]}


def test_a_well_formed_autoheal_still_escalates(orad, state):
    orad.save_json_atomic(state / "autoheal.json",
                          {"ollama": {"failures": orad.ops.AUTOHEAL_ESCALATE + 1}})
    result = orad.assess(ROOT, ROOT, state, signals=list(LIVE))
    assert "ollama_autoheal" in {s["key"] for s in result["displayed"]}


def test_a_malformed_autoheal_entry_does_not_invent_an_escalation(orad, state):
    """Degrading must fail toward silence on THIS signal, not toward a false
    critical line the operator cannot act on."""
    (state / "autoheal.json").write_text(json.dumps({"ollama": "broken"}),
                                         encoding="utf-8")
    result = orad.assess(ROOT, ROOT, state, signals=list(LIVE))
    assert "ollama_autoheal" not in {s["key"] for s in result["displayed"]}


# ============================================================
# 2 - record_heal_result survives the counter file it reads
# ============================================================

@pytest.mark.parametrize("autoheal", [
    {"ollama": "broken"},
    {"ollama": {"failures": "many"}},
    {"ollama": None},
    {"ollama": ["a"]},
])
def test_record_heal_result_restarts_a_corrupt_counter(orad, autoheal):
    out = orad.record_heal_result(autoheal, "ollama", False)
    assert out["ollama"]["failures"] == 1


def test_record_heal_result_still_counts_a_good_streak(orad):
    """The negative case ON the line: a readable counter must still increment."""
    out = orad.record_heal_result({"ollama": {"failures": 4}}, "ollama", False)
    assert out["ollama"]["failures"] == 5
    assert orad.record_heal_result(out, "ollama", True)["ollama"]["failures"] == 0


def test_record_heal_result_preserves_a_sibling_target(orad):
    out = orad.record_heal_result(
        {"ollama": "broken", "memory_index": {"failures": 2}}, "ollama", False)
    assert out["memory_index"]["failures"] == 2


# ============================================================
# 3 - parse_ttl: OverflowError is not a ValueError
# ============================================================

@pytest.mark.parametrize("bad", ["infd", "1e400", "inf", "-infh", "1e400s", "1e309m"])
def test_an_unrepresentable_ttl_falls_back_instead_of_raising(orad, bad):
    assert orad.parse_ttl(bad, "backup") == orad.DEFAULT_TTL_DAILY
    assert orad.parse_ttl(bad, "weekly_review") == orad.DEFAULT_TTL_WEEKLY


@pytest.mark.parametrize("good,expected", [
    ("24h", 86_400), ("7d", 604_800), ("30m", 1_800), ("3600s", 3_600),
    ("900", 900), ("1.5h", 5_400),
])
def test_a_representable_ttl_is_still_parsed(orad, good, expected):
    """The guard widens the fallback; it must not swallow a real value."""
    assert orad.parse_ttl(good, "backup") == expected


def test_an_unrepresentable_ttl_does_not_traceback_out_of_ack(orad, state, monkeypatch):
    """The reachable path: `ack backup --ttl 1e400` from the CLI."""
    monkeypatch.setattr(orad, "gather_live_signals",
                        lambda engine_root, data_root: list(LIVE))

    class _Args:
        key = "backup"
        ttl = "1e400"

    assert orad.cmd_ack(_Args(), state, ROOT, ROOT) == 0
    entry = orad.load_json(state / orad.ACK_FILE)["backup"]
    assert entry["ttl_seconds"] == orad.DEFAULT_TTL_DAILY


# ============================================================
# 4 - the floor: the guarded call sites still exist
# ============================================================

def test_every_state_read_in_this_module_goes_through_the_shape_guard(orad):
    """A guard is green over an empty corpus. Pin the count of readers it covers
    so a future state file added with a bare `load_json` fails here."""
    source = SCRIPT.read_text(encoding="utf-8")
    guarded = source.count("_state_dict(")
    # 1 definition + ACK (assess), CRUNCH (assess), AUTOHEAL (assess),
    # AUTOHEAL (cmd_ack), ACK (cmd_ack, under the lock), AUTOHEAL (_autoheal_locked)
    assert guarded == 7, (
        f"{guarded} `_state_dict(` sites; a state read was added or removed "
        f"without updating this floor")
