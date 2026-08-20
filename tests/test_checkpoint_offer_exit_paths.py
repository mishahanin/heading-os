"""The Stop hook's exit paths, driven end to end rather than reasoned about.

`checkpoint-offer.py` is the piece of the checkpoint mechanism that decides
whether a session pauses, prompts, or continues on its own. Three properties in
here were measured on 2026-08-20 and none of them had a test:

  1. **The hook can outrun its own registered timeout.** The Stop registration
     allows 90 seconds and Claude Code DISCARDS the output of a hook that
     outruns it, so the failure is total and silent: no block decision, no state
     write, no stall notice, and a session that halts having been told in
     writing that it would carry on. With a `herdr` answering just inside each
     of its own timeouts (`agent list` 9.5s, `agent prompt` 9.5s, `agent rename`
     1.9s) and the grace period at its 60s ceiling, the hook took **92.0s**.
     `CP.UNATTENDED_WAIT_MAX`'s own comment justifies 60 by adding three
     out-of-loop HERDR calls for a worst case "near 79 seconds"; it omits the
     two calls `_request_compaction` makes before the wait on the same Stop.

  2. **Unattended mode died at the moment it succeeded.** Full cycle measured
     with SOFT=40 / HARD=45: save at 46%, drive the compaction through HERDR,
     PostCompact resets the hysteresis, the statusline reads 11% used - and the
     next pause hit `main()`'s soft-threshold gate and returned silently, with
     `unattended_paused_at` and `unattended_stop_reason` both unset. Nothing for
     `--unattended status` to report, no Telegram notice, and `OPTIONS_DETAIL`
     had promised the operator that from that threshold on "the session also
     works through ordinary pauses".

  3. **`_operator_spoke` had no end-to-end test at all.** Branch coverage over
     the whole checkpoint suite plus the contract suite showed the transcript
     branch of `_wait_out_the_grace` never taken: the mechanism that stops an
     unattended run from talking over a message the operator just typed was
     carried entirely by inspection.

The tests here drive the real hook as a subprocess with a real `herdr` stand-in,
so they exercise the actual argument vectors, the actual JSON parsing, and the
actual failure branches.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOKS = ROOT / ".claude" / "hooks"
STATUSLINE = HOOKS / "checkpoint-statusline.py"
OFFER = HOOKS / "checkpoint-offer.py"
SAVE = HOOKS / "checkpoint-save.py"

SESSION = "eeeeeeee-1111-2222-3333-444444444444"


def _offer_module():
    spec = importlib.util.spec_from_file_location("offer_exit_paths_mod", OFFER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["offer_exit_paths_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cp():
    from scripts.utils import checkpoint_paths as CP

    return CP


# A `herdr` whose per-subcommand latency is set from the environment. Slow but
# SUCCESSFUL is the case that matters: a herdr that times out raises early and
# costs one timeout, while one that answers at 9.5s costs the full 9.5s and then
# lets the caller go on to spend the next one.
FAKE_HERDR = '''#!/usr/bin/env python3
import json, os, sys, time

argv = sys.argv[1:]
delay = float(os.environ.get("FAKE_HERDR_DELAY", "0"))
if argv[:2] == ["agent", "list"]:
    delay = float(os.environ.get("FAKE_HERDR_LIST_DELAY", delay))
elif argv[:2] == ["agent", "prompt"]:
    delay = float(os.environ.get("FAKE_HERDR_PROMPT_DELAY", delay))
elif argv[:2] == ["agent", "rename"]:
    delay = float(os.environ.get("FAKE_HERDR_RENAME_DELAY", delay))
time.sleep(delay)
log = os.environ.get("FAKE_HERDR_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\\n")
if argv[:2] == ["agent", "list"]:
    print(json.dumps({"result": {"agents": [{
        "pane_id": "w1:p1",
        "agent_status": "working",
        "agent_session": {"kind": "id",
                          "value": os.environ["FAKE_HERDR_SESSION"]},
    }]}}))
else:
    print(json.dumps({"result": {"type": "agent_prompted",
                                 "agent": {"agent_status": "working"}}}))
'''


@pytest.fixture()
def env(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    e = dict(os.environ)
    for key in list(e):
        if key.startswith("CLAUDE_HANDOFF"):
            e.pop(key, None)
    e["HEADING_OS_DATA"] = str(data)
    e["CLAUDE_HANDOFF_SOFT_THRESHOLD"] = "40"
    e["CLAUDE_HANDOFF_HARD_THRESHOLD"] = "45"
    e["CLAUDE_HANDOFF_REMIND_STEP"] = "5"
    # `_notify_stall` reads exactly these three. Blanked at the seam rather than
    # by hope; the session-wide containment behind it lives in tests/conftest.py.
    for name in (
        "CHECKPOINT_TELEGRAM_TARGET",
        "OPS_RADAR_TELEGRAM_TARGET",
        "ODIN_CADENCE_TELEGRAM_TARGET",
    ):
        e[name] = ""
    return {"env": e, "project": project, "data": data, "tmp": tmp_path}


def _install_fake_herdr(env, session, **delays):
    bindir = env["tmp"] / "fakebin"
    bindir.mkdir(exist_ok=True)
    binary = bindir / "herdr"
    binary.write_text(FAKE_HERDR, encoding="utf-8")
    binary.chmod(0o755)
    log = env["tmp"] / "herdr-calls.log"
    log.write_text("", encoding="utf-8")
    env["env"]["PATH"] = f"{bindir}{os.pathsep}{env['env']['PATH']}"
    env["env"]["FAKE_HERDR_LOG"] = str(log)
    env["env"]["FAKE_HERDR_SESSION"] = session
    for key, value in delays.items():
        env["env"][f"FAKE_HERDR_{key.upper()}_DELAY"] = str(value)
    return log


def _statusline(env, session, used, transcript=None):
    return subprocess.run(
        [sys.executable, str(STATUSLINE)],
        input=json.dumps({
            "session_id": session,
            "cwd": str(env["project"]),
            "workspace": {"project_dir": str(env["project"])},
            "transcript_path": str(transcript) if transcript else None,
            "context_window": {
                "used_percentage": used,
                "remaining_percentage": 100 - used,
            },
        }),
        capture_output=True, text=True, env=env["env"],
    )


def _stop(env, session, *, turn=None, active=False, transcript=None):
    payload = {
        "session_id": session,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "stop_hook_active": active,
    }
    if turn is not None:
        payload["prompt_id"] = turn
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    return subprocess.run(
        [sys.executable, str(OFFER)],
        input=json.dumps(payload), capture_output=True, text=True, env=env["env"],
    )


def _state_path(env, session):
    return (
        env["project"] / ".claude" / "state"
        / f"checkpoint-{_cp().safe_slug(session)}.json"
    )


def _state(env, session):
    path = _state_path(env, session)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _set_state(env, session, **updates):
    path = _state_path(env, session)
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(updates)
    path.write_text(json.dumps(state), encoding="utf-8")


def _verdict(result):
    """Which of the four printed shapes this Stop produced, or SILENT."""
    out = result.stdout.strip()
    if not out:
        return "SILENT"
    reason = json.loads(out)["reason"]
    if "AUTO MODE is on" in reason:
        return "AUTO-SAVE"
    if "unattended on," in reason and "continues" in reason:
        return "CONTINUE"
    if "Ask the user" in reason:
        return "OFFER"
    return "UNKNOWN"


# --------------------------------------------------------------------------
# 1. The hook must fit inside the timeout it is registered with
# --------------------------------------------------------------------------

def test_the_grace_period_is_bounded_by_the_hooks_own_clock():
    """Arithmetic, so the boundary is provable without spending 90s of wall time.

    `CP.wait_seconds()` answers what the OPERATOR configured. What may actually
    be spent waiting is that number minus everything the hook has already spent
    on this Stop - two HERDR calls in `_request_compaction`, the pane lookup for
    the countdown, the state reads - because the harness's timeout starts at
    process start and not at the start of the wait.
    """
    mod = _offer_module()
    started = 1000.0

    # Nothing spent yet: the operator's number stands.
    assert mod._effective_wait(60, started, started) == 60.0
    # 30s already spent: 90 - 8 reserve - 30 leaves 52, so 60 is cut to 52.
    assert mod._effective_wait(60, started, started + 30) == pytest.approx(52.0)
    # 19s of compaction calls plus a 9.5s pane lookup - the measured shape that
    # produced 92.0s end to end before this bound existed.
    assert mod._effective_wait(60, started, started + 28.5) == pytest.approx(53.5)
    # Past the budget entirely: no wait at all, never a negative one.
    assert mod._effective_wait(60, started, started + 200) == 0.0
    # The bound only ever shortens. A small configured wait is not stretched.
    assert mod._effective_wait(5, started, started) == 5.0

    CP = _cp()
    ceiling = CP.UNATTENDED_WAIT_MAX
    worst = ceiling + mod.POST_WAIT_RESERVE_SECONDS
    assert worst <= mod.HOOK_TIMEOUT_SECONDS, (
        f"a full {ceiling}s wait plus its {mod.POST_WAIT_RESERVE_SECONDS}s tail "
        f"reaches {worst}s against a registered {mod.HOOK_TIMEOUT_SECONDS}s "
        "timeout, so the continuation would be discarded"
    )


def test_a_hook_that_has_already_spent_its_budget_waits_no_longer(env, monkeypatch):
    """The bound, exercised through the real wait rather than through its helper.

    `_HOOK_STARTED` is moved back to simulate a Stop that already spent its
    budget upstream. Before the bound existed the wait ran its configured length
    regardless, which is how 92.0s happened.
    """
    mod = _offer_module()
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")
    payload = {"transcript_path": str(transcript)}

    monkeypatch.setattr(mod.CP, "wait_seconds", lambda: 30)
    mod._HOOK_STARTED = time.monotonic() - (
        mod.HOOK_TIMEOUT_SECONDS - mod.POST_WAIT_RESERVE_SECONDS
    )
    started = time.monotonic()
    spoke, granted = mod._wait_out_the_grace(payload, SESSION)
    elapsed = time.monotonic() - started

    assert spoke is False, "an exhausted budget must still continue the run"
    assert granted == 0.0
    assert elapsed < 3.0, (
        f"the wait ran for {elapsed:.1f}s with no budget left; a 30s wait here "
        "would push the hook past its registered timeout and the harness would "
        "discard the continuation"
    )


def test_the_continuation_reports_the_wait_it_actually_got(env, monkeypatch):
    """`{wait}s grace passed with no input` is read by the operator.

    It interpolated `CP.wait_seconds()` - the CONFIGURED number - which parts
    company with reality the moment the budget shortens the window.
    """
    mod = _offer_module()
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")

    monkeypatch.setattr(mod.CP, "wait_seconds", lambda: 45)
    mod._HOOK_STARTED = time.monotonic() - 79.0  # 90 - 8 - 79 = 3s of room
    _spoke, granted = mod._wait_out_the_grace(
        {"transcript_path": str(transcript)}, SESSION
    )
    assert 0 < granted < 45, f"the budget did not shorten the wait: {granted}"

    # Rendering the wrapper HERE with `wait=int(granted)` would assert this
    # test's own arithmetic and nothing else: the production call site could go
    # back to `CP.wait_seconds()` and this would stay green. Measured on
    # 2026-08-20, that exact mutation left all 17 tests in this file passing.
    # So the call site itself is what gets pinned.
    import re

    source = (mod.__file__ and Path(mod.__file__).read_text(encoding="utf-8")) or ""
    call = re.search(r"UNATTENDED_WRAPPER\.format\((?:[^()]|\([^()]*\))*\)", source, re.S)
    assert call, "the continuation's format call moved; update this guard"
    body = call.group(0)
    assert "wait=int(granted)" in body, (
        "the continuation must report the wait it was GRANTED, not the one that "
        f"was configured. The call site reads:\n{body}"
    )
    assert "CP.wait_seconds()" not in body, (
        "the continuation is interpolating the configured wait again; on a "
        f"shortened budget it claims a window it never gave:\n{body}"
    )


@pytest.mark.slow
def test_the_whole_stop_fits_under_the_registered_timeout_with_a_slow_herdr(env):
    """The end-to-end measurement, against a real registered budget.

    This is the shape that produced 92.0s at the 60s ceiling: an unattended
    session at the hard threshold whose bucket is already consumed, with a
    handoff on disk. `herdr` answers just inside each of its own timeouts, which
    is worse than one that times out - a timeout raises and costs one budget, an
    answer costs the budget in full and lets the next call start.

    `compact_requested_bucket` is pre-set to the current bucket so
    `_request_compaction` returns early WITHOUT submitting. That is not a
    convenience: since 2026-08-20 a Stop that submits a compaction returns 0
    immediately, because a block decision prevents the turn boundary the queued
    `/compact` needs. So the submit path no longer reaches the wait at all, and
    the longest surviving path - the one this measures - is the pane lookup plus
    the countdown plus the grace period. The submit path's own exit is held by
    tests/test_compaction_is_not_self_strangling.py.

    Run against a 30s registered budget rather than the shipped 90s, with the
    herdr latencies scaled to match, so the suite pays 23 seconds instead of 86
    for the same invariant. Deliberately NOT a projection: once the wait is
    bounded, total time stops being linear in the configured wait, so
    `ceiling + measured overhead` would describe a hook that no longer exists.
    The assertion is the wall clock against the budget, measured.

    Before the bound: the wait ran its configured 30s on top of the 9s of HERDR
    calls ahead of it, for about 40s against a 30s budget. After: the wait is
    cut to what is left.
    """
    CP = _cp()
    _install_fake_herdr(env, SESSION, list=3, prompt=3, rename=0.6)
    env["env"]["CLAUDE_HANDOFF_HOOK_TIMEOUT"] = "30"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "30"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "2"
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")

    _statusline(env, SESSION, 46, transcript=transcript)
    _set_state(
        env, SESSION,
        session_unattended=True,
        needs_compact_offer=False, offer_level=None,
        offer_bucket=45, last_offered_bucket=45,
        last_offer_at="2020-01-01T00:00:00+00:00",
        unattended_turn_id="t1",
        compact_requested_bucket=45,
    )
    archive = env["data"] / "outputs" / "operations" / "handoff-archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"2099-01-01-000000_handoff_auto_{CP.safe_slug(SESSION)}.md").write_text(
        "body", encoding="utf-8"
    )

    started = time.monotonic()
    result = _stop(env, SESSION, turn="t1", transcript=transcript)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert _verdict(result) == "CONTINUE", (
        f"the run did not continue: {_verdict(result)}\n{result.stderr}"
    )
    assert elapsed < 30, (
        f"the hook took {elapsed:.1f}s against a registered 30s timeout, so "
        "Claude Code would discard its output: no block decision, no state "
        "write, no notice, and a session that halts having been told it would "
        "carry on."
    )
    # The shipped numbers have to leave the same room, or the scaled run above
    # proves nothing about the real one.
    assert CP.UNATTENDED_WAIT_MAX + 8 <= 90, (
        "the shipped grace ceiling no longer fits under the registered timeout"
    )


# --------------------------------------------------------------------------
# 2. Unattended mode survives its own compaction
# --------------------------------------------------------------------------

def _drive_to_compaction(env, session):
    """Save at the hard threshold, drive the compaction, run PostCompact."""
    CP = _cp()
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "1"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"

    _statusline(env, session, 46, transcript=transcript)
    _set_state(env, session, session_unattended=True)
    saved = _stop(env, session, turn="t1", transcript=transcript)
    assert _verdict(saved) == "AUTO-SAVE", _verdict(saved)

    archive = env["data"] / "outputs" / "operations" / "handoff-archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"2099-01-01-000000_handoff_auto_{CP.safe_slug(session)}.md").write_text(
        "body", encoding="utf-8"
    )
    _stop(env, session, turn="t1", transcript=transcript, active=True)

    subprocess.run(
        [sys.executable, str(SAVE)],
        input=json.dumps({
            "session_id": session,
            "cwd": str(env["project"]),
            "workspace": {"project_dir": str(env["project"])},
            "trigger": "manual",
            "compact_summary": "the driven compaction summary",
            "transcript_path": "",
        }),
        capture_output=True, text=True, env=env["env"],
    )
    return transcript


def test_unattended_keeps_going_after_its_own_compaction(env):
    """The measured death: succeed, then halt silently for the rest of the night.

    Compaction is the point of the mode, and it is also what drops `used` from
    46% to about 11%. The soft-threshold gate then returned before anything
    else ran: no continuation, no `unattended_paused_at`, no stop reason, no
    notice - a run that stopped with nothing anywhere saying it had.
    """
    _install_fake_herdr(env, SESSION)
    transcript = _drive_to_compaction(env, SESSION)

    before = _state(env, SESSION)
    assert before.get("last_compact_at"), "the compaction was not recorded"
    assert before.get("session_unattended") is True, "the switch was lowered"

    _statusline(env, SESSION, 11, transcript=transcript)
    result = _stop(env, SESSION, turn="t2", transcript=transcript)

    assert _verdict(result) == "CONTINUE", (
        "unattended mode stopped at the first pause after its own compaction, "
        f"which is the moment it exists for. Got {_verdict(result)}.\n"
        f"stderr: {result.stderr}"
    )


def test_a_session_that_never_compacted_still_halts_below_soft(env):
    """The other half: the documented floor stays for a stretch that has not begun.

    The skill documents the mode as engaging above the soft threshold, and that
    is right for a session that has not filled up yet. Only the post-compaction
    case was wrong, so the fix must not turn `--unattended on` at 11% used into
    a session that continues on its own from the first pause.
    """
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "1"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"

    _statusline(env, SESSION, 11, transcript=transcript)
    _set_state(env, SESSION, session_unattended=True)
    result = _stop(env, SESSION, turn="t1", transcript=transcript)

    assert _verdict(result) == "SILENT", (
        "a session that has never compacted now continues below the soft "
        f"threshold, which the skill documents as halting: {_verdict(result)}"
    )
    assert not _state(env, SESSION).get("unattended_continuations")


@pytest.mark.parametrize("used,expected", [
    (39, "SILENT"), (40, "CONTINUE"), (44, "CONTINUE"),
    (45, "AUTO-SAVE"), (46, "AUTO-SAVE"),
])
def test_the_unattended_boundaries_land_on_the_configured_numbers(env, used, expected):
    """SOFT=40 and HARD=45, inclusive at both, with no off-by-one either side."""
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "1"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"

    _statusline(env, SESSION, used, transcript=transcript)
    _set_state(env, SESSION, session_unattended=True)
    result = _stop(env, SESSION, turn="t1", transcript=transcript)

    assert _verdict(result) == expected, (
        f"at {used}% used the hook chose {_verdict(result)}, not {expected}"
    )


# --------------------------------------------------------------------------
# 3. The operator gets the turn back the moment they type
# --------------------------------------------------------------------------

def _append_after(path, entry, delay=1.0):
    def later():
        time.sleep(delay)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    thread = threading.Thread(target=later)
    thread.start()
    return thread


@pytest.mark.parametrize("entry,spoke", [
    ({"type": "user", "message": {"content": [{"type": "text", "text": "wait"}]}}, True),
    ({"type": "user", "message": {"content": "wait"}}, True),
    ({"type": "queue-operation", "operation": "enqueue",
      "sessionId": SESSION, "content": "hold on"}, True),
    ({"type": "user", "message": {"content": [{"type": "tool_result",
                                               "content": "x"}]}}, False),
    ({"type": "user", "isMeta": True,
      "message": {"content": [{"type": "text", "text": "meta"}]}}, False),
    ({"type": "assistant",
      "message": {"content": [{"type": "text", "text": "hi"}]}}, False),
])
def test_a_message_typed_during_the_wait_takes_the_turn_back(env, entry, spoke):
    """The half `_wait_out_the_grace` exists for, and the one nothing covered.

    Branch coverage over the whole checkpoint suite plus the contract suite on
    2026-08-20 showed the transcript branch of the wait never taken: the
    protection against an unattended run talking over a message the operator
    just sent was carried by inspection alone.

    The negatives are the load-bearing half. Most `user` lines in a transcript
    are tool results, so a check on the role alone would read every tool
    response as the operator arriving and halt the run at its first tool call.
    """
    transcript = env["project"] / "t.jsonl"
    transcript.write_text("", encoding="utf-8")
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "6"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"

    _statusline(env, SESSION, 42, transcript=transcript)
    _set_state(env, SESSION, session_unattended=True)

    thread = _append_after(transcript, entry)
    started = time.monotonic()
    result = _stop(env, SESSION, turn="t1", transcript=transcript)
    elapsed = time.monotonic() - started
    thread.join()

    if spoke:
        assert _verdict(result) == "SILENT", (
            "the operator typed during the grace period and the run continued "
            f"over them: {_verdict(result)}"
        )
        assert elapsed < 5, (
            f"the turn came back after {elapsed:.1f}s of a 6s wait; the offer "
            "text promises it inside about two seconds"
        )
    else:
        assert _verdict(result) == "CONTINUE", (
            f"{entry.get('type')} was read as the operator speaking, which "
            "would halt an unattended run at its first tool call"
        )
