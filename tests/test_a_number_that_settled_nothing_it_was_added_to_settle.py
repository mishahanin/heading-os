#!/usr/bin/env python3
"""Four defects in `.claude/hooks/checkpoint-statusline.py`, measured 2026-08-31.

**1. The one absolute number recorded, off by five orders of magnitude.**
`context_input_tokens` was `current_usage["input_tokens"]`, the last request's
UNCACHED input, written under a comment saying the absolute numbers exist because
"the percentage alone cannot settle an argument about where compaction fires".
Measured on the live state file at three points that morning:

    used%=58.0  win=1000000  context_input_tokens=2
    used%=21.0  win=1000000  context_input_tokens=2
    (just after the 10:21 compaction)  used%=0.0  win=1000000  input_tokens=0

58% of 1,000,000 is 580,000 and 21% is 210,000, and both readings recorded 2, so
the pair is not proportional at any scale. The argument was live: this session's
`compact_history` shows compactions firing at 42% and 58% against a configured
75%, and 2 is the number an operator would have checked 750,000 against. Read from
this session's own transcript, 35,667 usage objects, newest:

    input_tokens 2 + cache_creation 21123 + cache_read 193725 = 214850
    214850 / 1000000 = 21.485%, against a recorded used_percentage of 21.0

The sum is the percentage back. Summing beat dropping the key because a token
count is what tells a threshold in force from a threshold ignored.

WHY IT WAS INVISIBLE: `tests/test_statusline_blind_render.py` seeded
`context_input_tokens=382500` as PRE-EXISTING state beside `used_percentage=51.0`
and `context_window_size=750000`, then asserted only that a blind render preserved
it. Seeded that way the number is exactly proportional, so the fixture asserted
the hook's contract while hiding that the hook never produced such a value. Every
test below asserts what the hook PRODUCED into a key nothing seeded correctly.

**2. A non-numeric `last_offered_bucket` printed the blank bar the module says it
cannot print.** `int(state.get("last_offered_bucket") or 0)`. Driving the real
hook against a hand-written state file:

    'high' -> ValueError    {'a': 1} -> TypeError    [1] -> TypeError
    '5%'   -> ValueError    ''       -> 0

Each of the raisers exited 1 with an EMPTY stdout. `_session_hard` in
scripts/utils/checkpoint_paths.py had already decided this class for this same
file, and chose the fallback: "a status line that crashes on it is worse than one
that falls back to the workspace default".

**3. `NaN` or `Infinity` in `used_percentage` crashed the render.**
`json.loads` accepts both as bare literals BY DEFAULT, so they arrive through the
same malformed-payload surface this file hardened on 2026-08-20:

    coerce_used 'nan' -> nan ; 'Infinity' -> inf ; '1e400' -> inf ; True -> 1.0
    int(nan // 5) -> ValueError: cannot convert float NaN to integer
    inf // 5 -> nan, so both inputs meet on the same raise

Driven through the real hook, all three exited 1 printing nothing. `True` did not
crash, which is worse: it rendered a confident 99% remaining off a boolean.

**4. The lock protected the merge, not the decision.** `previous_last_offered` and
`cfg` were read from a PRE-LOCK copy of the state file and the offer decision
derived from them was written inside the lock. The exposed span is the one the
file already measured on 2026-08-20, 0.814 ms median and 3.686 ms at worst. The
prior auditor reasoned this defect and built no harness for it; the two tests here
force the overlap deterministically, by wrapping `CP.locked_state` so a competing
write lands after the hook's own read and before the lock is taken.

WHAT THESE TESTS PIN: the recorded token count is the whole context and is
proportional to `used_percentage * context_window_size`; a state field or a
payload field of the wrong shape degrades to a printed line rather than to
nothing; and the offer decision reads its inputs from the same locked dict it
writes into. Every guard carries both directions, so none of them can pass over a
one-sided corpus: a bad shape must not crash AND a good shape must still be
obeyed.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "checkpoint-statusline.py"

# Pinned rather than inherited. `CP.config` falls back to these env vars, and the
# operator's own shell carries 40/45 today, so a test that read the ambient pair
# would assert against whatever `--compact-at` last set.
THRESHOLDS = {
    "CLAUDE_HANDOFF_SOFT_THRESHOLD": "25",
    "CLAUDE_HANDOFF_HARD_THRESHOLD": "30",
    "CLAUDE_HANDOFF_REMIND_STEP": "5",
    "CLAUDE_HANDOFF_AUTO": "0",
}

# The newest usage object in this session's transcript on 2026-08-31, verbatim.
# `output_tokens` is present on purpose: it must NOT be counted, since output is
# not context the next request carries in.
LIVE_USAGE = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 21123,
    "cache_read_input_tokens": 193725,
    "output_tokens": 726,
}
LIVE_WINDOW = 1_000_000
LIVE_USED_PCT = 21.485
LIVE_CONTEXT_TOKENS = 214_850


def _session_state(tmp_path: Path, pre: dict, session: str) -> Path:
    project = tmp_path / "project"
    (project / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    state_path = project / ".claude" / "state" / f"checkpoint-{session[:32]}.json"
    state_path.write_text(json.dumps(pre), encoding="utf-8")
    return state_path


def _run_raw(tmp_path: Path, context_window_json: str, pre: dict) -> tuple[dict, str]:
    """Drive the real hook as a subprocess. Returns (state after, stdout).

    The context_window arrives as raw JSON TEXT, never through `json.dumps`,
    because `NaN` and `Infinity` are bare literals no dumper will emit and the
    payload surface under test is the one that accepts them.
    """
    session = "probe-session-0000"
    state_path = _session_state(tmp_path, pre, session)
    project = state_path.parent.parent.parent
    env = dict(
        os.environ,
        CLAUDE_CODE_SESSION_ID=session,
        HEADING_OS_DATA=str(tmp_path / "data"),
        **THRESHOLDS,
    )
    payload = (
        '{"session_id": "%s", "cwd": "%s", "transcript_path": "", '
        '"context_window": %s}' % (session, project, context_window_json)
    )
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=project,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), (
        "the hook printed NOTHING, which is what a dead hook looks like: "
        f"{result.stderr}"
    )
    return json.loads(state_path.read_text(encoding="utf-8")), result.stdout


# ---------------------------------------------------------------------------
# Defect 1 - the recorded absolute number
# ---------------------------------------------------------------------------


def test_the_recorded_token_count_is_the_whole_context_not_one_component(tmp_path):
    """Asserts what the hook PRODUCED, against a seed that is already wrong.

    `context_input_tokens` is seeded to 2, the defective value, so a render that
    merely preserved what it found would fail here. Seeding the plausible value
    instead is exactly what hid this defect for eleven days.
    """
    cw = json.dumps(
        {
            "used_percentage": LIVE_USED_PCT,
            "context_window_size": LIVE_WINDOW,
            "current_usage": LIVE_USAGE,
        }
    )
    after, _ = _run_raw(tmp_path, cw, {"context_input_tokens": 2})

    produced = after.get("context_input_tokens")
    assert produced == LIVE_CONTEXT_TOKENS, (
        "the hook recorded %r for a context of %d tokens" % (produced, LIVE_CONTEXT_TOKENS)
    )
    assert produced != LIVE_USAGE["input_tokens"], (
        "the recorded figure is still the last request's uncached input"
    )


def test_the_recorded_token_count_is_proportional_to_the_percentage(tmp_path):
    """The property the key exists for, checked as a property.

    An operator settles "did the configured threshold hold" by comparing this
    number against the window. So the test compares it against the percentage the
    same render recorded, which is the comparison that failed by five orders of
    magnitude before the fix.
    """
    cw = json.dumps(
        {
            "used_percentage": LIVE_USED_PCT,
            "context_window_size": LIVE_WINDOW,
            "current_usage": LIVE_USAGE,
        }
    )
    after, _ = _run_raw(tmp_path, cw, {})

    tokens = after["context_input_tokens"]
    expected = after["used_percentage"] / 100.0 * after["context_window_size"]
    assert abs(tokens - expected) < 0.005 * after["context_window_size"], (
        "recorded %r tokens against a reading that implies %r" % (tokens, expected)
    )


@pytest.mark.parametrize(
    "usage,expected,label",
    [
        ({"input_tokens": 7, "cache_read_input_tokens": 100}, 107,
         "an absent class counts as zero, because the API omits what it has none of"),
        ({"cache_read_input_tokens": 193725, "cache_creation_input_tokens": 21123},
         214848, "a cold-cache payload with no uncached input at all"),
        ({"input_tokens": 0, "cache_read_input_tokens": 0,
          "cache_creation_input_tokens": 0}, 0,
         "a genuinely empty context is 0, which is not None"),
    ],
)
def test_the_sum_survives_a_partial_usage_breakdown(tmp_path, usage, expected, label):
    cw = json.dumps({"used_percentage": 20.0, "current_usage": usage})
    after, _ = _run_raw(tmp_path, cw, {})
    assert after.get("context_input_tokens") == expected, label


@pytest.mark.parametrize(
    "usage_json,label",
    [
        ("null", "current_usage absent from the payload"),
        ('"not-a-mapping"', "current_usage of the wrong type"),
        ("{}", "an empty current_usage"),
        ('{"input_tokens": "lots"}', "the only class present is a string"),
        ('{"input_tokens": null}', "the only class present is null"),
    ],
)
def test_a_payload_that_cannot_say_records_nothing_rather_than_a_number(
    tmp_path, usage_json, label
):
    """The other direction. A render that learned nothing about the token count
    must record None, never 0: a zero here reads as an empty context, which is a
    measurement the payload never made."""
    cw = '{"used_percentage": 20.0, "current_usage": %s}' % usage_json
    after, _ = _run_raw(tmp_path, cw, {})
    assert after.get("context_input_tokens", "missing") is None, label


@pytest.mark.parametrize(
    "usage_json,expected,label",
    [
        ('{"input_tokens": true, "cache_read_input_tokens": 100}', 100,
         "a boolean is not a token count, and float(True) is 1.0"),
        ('{"input_tokens": Infinity, "cache_read_input_tokens": 5}', 5,
         "a non-finite class is dropped, not added"),
        ('{"input_tokens": [1], "cache_read_input_tokens": 5}', 5,
         "a list class is dropped, not added"),
    ],
)
def test_a_component_of_the_wrong_shape_is_dropped_from_the_sum(
    tmp_path, usage_json, expected, label
):
    cw = '{"used_percentage": 20.0, "current_usage": %s}' % usage_json
    after, _ = _run_raw(tmp_path, cw, {})
    assert after.get("context_input_tokens") == expected, label


# ---------------------------------------------------------------------------
# Defect 2 - a state field of the wrong shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["high", {"a": 1}, [1], "5%", "  ", "30.5"],
    ids=["a-word", "a-dict", "a-list", "a-percent-sign", "whitespace", "a-decimal-string"],
)
def test_a_hand_edited_last_offered_bucket_still_prints_a_line(tmp_path, bad):
    """`_run_raw` asserts exit 0 and non-empty stdout, which is the whole defect:
    every one of these exited 1 printing nothing before the fix."""
    after, stdout = _run_raw(
        tmp_path, '{"used_percentage": 50.0}', {"last_offered_bucket": bad}
    )
    assert "context: n/a" not in stdout, (
        "the reading was fine; only the state field was bad, so the bar must show it"
    )
    assert after.get("needs_compact_offer") is True, (
        "reading the bad field as 0 must leave a 50% render above the threshold"
    )


def test_a_numeric_last_offered_bucket_is_still_obeyed(tmp_path):
    """The other direction, and the one that stops the fallback becoming a
    blanket ignore. A bucket already offered must NOT be offered again, which is
    the hysteresis the field exists for."""
    after, _ = _run_raw(
        tmp_path, '{"used_percentage": 50.0}', {"last_offered_bucket": 50}
    )
    assert after.get("needs_compact_offer") is False
    assert after.get("offer_bucket") == 50


@pytest.mark.parametrize("empty", [None, 0, "", False])
def test_a_falsy_last_offered_bucket_still_resolves_to_zero_in_silence(tmp_path, empty):
    """Every value that already resolved to 0 under `or 0` must keep doing so, so
    the fix cannot be read as widening what counts as a bad field."""
    after, _ = _run_raw(
        tmp_path, '{"used_percentage": 50.0}', {"last_offered_bucket": empty}
    )
    assert after.get("needs_compact_offer") is True
    assert after.get("offer_bucket") == 50


# ---------------------------------------------------------------------------
# Defect 3 - non-finite and boolean readings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cw,label",
    [
        ('{"used_percentage": NaN}', "a bare NaN literal, which json.loads accepts"),
        ('{"used_percentage": Infinity}', "a bare Infinity literal"),
        ('{"used_percentage": -Infinity}', "a bare -Infinity literal"),
        ('{"used_percentage": 1e400}', "a literal that overflows to inf"),
        ('{"used_percentage": true}', "a boolean, which float() reads as 1.0"),
        ('{"remaining_percentage": NaN}', "NaN on the fallback field"),
        ('{"remaining_percentage": Infinity}', "Infinity on the fallback field"),
        ('{"remaining_percentage": false}', "a boolean on the fallback field"),
        ('{"used_percentage": "nan"}', "NaN spelled as a string"),
        ('{"used_percentage": "Infinity"}', "Infinity spelled as a string"),
    ],
)
def test_a_non_finite_reading_degrades_to_n_a_rather_than_to_nothing(
    tmp_path, cw, label
):
    after, stdout = _run_raw(tmp_path, cw, {})
    assert "context: n/a" in stdout, f"{label}: expected the promised degradation"
    assert after.get("used_percentage") is None, (
        f"{label}: a render that could not read the window recorded a reading"
    )


@pytest.mark.parametrize(
    "cw,expected_used",
    [
        ('{"used_percentage": 0}', 0.0),
        ('{"used_percentage": 50.0}', 50.0),
        ('{"used_percentage": "50.0"}', 50.0),
        ('{"remaining_percentage": 20.0}', 80.0),
        ('{"used_percentage": null, "remaining_percentage": 20.0}', 80.0),
        ('{"used_percentage": "oops", "remaining_percentage": 20.0}', 80.0),
    ],
)
def test_a_finite_reading_is_still_read(tmp_path, cw, expected_used):
    """The other direction. Rejecting the non-finite values must not reject the
    real ones, including 0.0, a numeric string, and the fallback to
    `remaining_percentage` when `used_percentage` is unreadable."""
    after, stdout = _run_raw(tmp_path, cw, {})
    assert after.get("used_percentage") == expected_used
    assert "context: n/a" not in stdout


# ---------------------------------------------------------------------------
# Defect 4 - the decision must read its inputs under the lock
# ---------------------------------------------------------------------------


def _load_hook_module():
    """Import the hook in-process. Its filename carries hyphens, so it cannot be
    imported by name."""
    spec = importlib.util.spec_from_file_location("checkpoint_statusline_probe", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drive_with_a_competing_write(
    monkeypatch, tmp_path, pre: dict, cw: dict, competitor: dict
) -> dict:
    """Run the hook with one competing write forced into the exposed span.

    `CP.locked_state` is wrapped so the competitor lands at the last possible
    moment before the lock is taken: after any read the hook made outside it, and
    before the read the lock covers. That is the window the 2026-08-20 comment
    measured at 0.814 ms median, made deterministic instead of raced for. Run
    in-process because the injection point is inside the hook's own call.
    """
    session = "overlap-session-000"
    state_path = _session_state(tmp_path, pre, session)
    project = state_path.parent.parent.parent

    module = _load_hook_module()
    real_locked_state = module.CP.locked_state

    @contextlib.contextmanager
    def racing_locked_state(path, **kwargs):
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        on_disk.update(competitor)
        path.write_text(json.dumps(on_disk), encoding="utf-8")
        with real_locked_state(path, **kwargs) as fresh:
            yield fresh

    monkeypatch.setattr(module.CP, "locked_state", racing_locked_state)
    for name, value in THRESHOLDS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    payload = {
        "session_id": session,
        "cwd": str(project),
        "transcript_path": "",
        "context_window": cw,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert module.main() == 0
    return json.loads(state_path.read_text(encoding="utf-8"))


def test_a_bucket_stamped_inside_the_window_is_not_offered_again(monkeypatch, tmp_path):
    """The Stop hook consumes bucket 30 while this render is deciding.

    Before the fix `previous_last_offered` came from the pre-lock read and still
    said 25, so `30 > 25` queued an offer for a bucket already consumed and the
    next Stop re-offered a checkpoint the operator had just been given.
    """
    after = _drive_with_a_competing_write(
        monkeypatch,
        tmp_path,
        pre={"last_offered_bucket": 25},
        cw={"used_percentage": 30.0},
        competitor={"last_offered_bucket": 30},
    )
    assert after.get("last_offered_bucket") == 30, "the competing write was lost"
    assert after.get("needs_compact_offer") is False, (
        "bucket 30 was offered again after the Stop hook had already consumed it"
    )
    assert after.get("offer_level") is None


def test_a_bucket_nobody_consumed_is_still_offered(monkeypatch, tmp_path):
    """The other direction, with the identical harness and no competing write.
    Without this, the assertion above would also pass on a hook that never
    offered anything."""
    after = _drive_with_a_competing_write(
        monkeypatch,
        tmp_path,
        pre={"last_offered_bucket": 25},
        cw={"used_percentage": 30.0},
        competitor={},
    )
    assert after.get("last_offered_bucket") == 25
    assert after.get("needs_compact_offer") is True
    assert after.get("offer_bucket") == 30


def test_a_threshold_set_inside_the_window_decides_this_render(monkeypatch, tmp_path):
    """`--compact-at 60` lands in the exposed span while the render is deciding.

    Before the fix `cfg` came from the pre-lock read, so this render picked its
    level against the workspace default of 30, called a 50% reading "hard", and
    echoed 30 back into `hard_threshold` over the operator's 60.
    """
    after = _drive_with_a_competing_write(
        monkeypatch,
        tmp_path,
        pre={},
        cw={"used_percentage": 50.0},
        competitor={"session_hard_threshold": 60},
    )
    assert after.get("hard_threshold") == 60, (
        "the render echoed the superseded threshold back into the file"
    )
    assert after.get("soft_threshold") == 55
    assert after.get("offer_level") is None, (
        "50% was called an offer against a threshold of 60"
    )


def test_a_threshold_already_on_disk_still_decides_the_render(monkeypatch, tmp_path):
    """The other direction: the same reading, the same expectation, with the
    session threshold present before the render rather than racing it. This is
    what proves the assertions above are about the LOCK and not about the hook
    having stopped reading `session_hard_threshold` at all."""
    after = _drive_with_a_competing_write(
        monkeypatch,
        tmp_path,
        pre={"session_hard_threshold": 60},
        cw={"used_percentage": 50.0},
        competitor={},
    )
    assert after.get("hard_threshold") == 60
    assert after.get("offer_level") is None


def test_a_render_above_a_racing_threshold_still_queues_an_offer(monkeypatch, tmp_path):
    """And the last direction: a threshold arriving in the window that the
    reading DOES cross must still queue the offer, so the fix cannot be a hook
    that has quietly stopped offering."""
    after = _drive_with_a_competing_write(
        monkeypatch,
        tmp_path,
        pre={},
        cw={"used_percentage": 70.0},
        competitor={"session_hard_threshold": 60},
    )
    assert after.get("hard_threshold") == 60
    assert after.get("offer_level") == "hard"
    assert after.get("needs_compact_offer") is True
