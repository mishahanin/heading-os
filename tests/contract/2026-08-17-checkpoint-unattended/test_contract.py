"""The contract for the three checkpoint changes, written RED before the code.

Step 3 of the Canopus standard for `plans/2026-08-17-checkpoint-unattended.md`. Ten
criteria, one test per criterion at least, each claimed in a docstring opening.

Four properties this file holds that the ordinary suite does not, and why each
belongs here.

**Every fixture carries a shape read off the real source.** The Stop payload, the
PreCompact payload, the `queue-operation` transcript line and the ralph-loop state
file were all read out of Claude Code 2.1.228 and out of the installed plugin on
2026-08-17, never inferred from what the code expects. The gate-yield contract of
2026-08-02 was 28 tests green against a timestamp shape no real record has ever
carried; a fixture that cannot produce the real shape proves nothing about it.

**Each test takes its own scratch root.** The hooks resolve their project root
from the payload's own `cwd`, so every case here points that at a `tmp_path` and
no test reads the engine's live `.claude/state/`. Two 2026-08-02 contract tests
were unpassable by construction because they read the tree they ran in.

**The three modes are asserted apart.** `off` and `on` must not move at all, and
the four tests that hold them are ALREADY GREEN today. They decide nothing about
the work being approved and are named as such in the probe reading; they are here
because the cheapest way to break this slice is to regress a mode nobody was
looking at.

**Silence is asserted as empty stdout, never as "no error".** A hook that crashed
and a hook that correctly said nothing both exit without a decision. Every
silence case asserts `returncode == 0` AND `stdout == ""` AND that stderr carries
no traceback, because accepting any of the three alone accepts the crash.

Do NOT weaken an assertion here to make the implementation pass.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

OFFER_HOOK = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"
PRECOMPACT_HOOK = ROOT / ".claude" / "hooks" / "checkpoint-precompact.py"
SWITCH = ROOT / "scripts" / "checkpoint-paths.py"

SESSION = "8c765efb-81ea-4397-a80c-620d7b9fc4c3"
SLUG = "8c765efb-81ea-4397-a80c-620d7b9f"

# The shape of a mid-turn operator message, copied from this workspace's own
# transcript on 2026-08-17: enqueue at 13:00:18.072Z, remove at 13:01:12.952Z.
ENQUEUE = {
    "type": "queue-operation",
    "operation": "enqueue",
    "timestamp": "2026-08-17T13:00:18.072Z",
    "sessionId": SESSION,
    "content": "стоп, я тут",
}
REMOVE = dict(ENQUEUE, operation="remove", timestamp="2026-08-17T13:01:12.952Z")


def _state(**over) -> dict:
    """The state file's real key set, as checkpoint-statusline.py writes it."""
    base = {
        "needs_compact_offer": True,
        "offer_level": "soft",
        "offer_bucket": 8,
        "current_bucket": 8,
        "last_offered_bucket": 0,
        "used_percentage": 41.0,
        "remaining_percentage": 59.0,
        "auto": False,
    }
    base.update(over)
    return base


def _seed(tmp: Path, state: dict, transcript_lines: list[dict] | None = None) -> Path:
    """Lay out a scratch project the hooks will resolve to, and its transcript."""
    (tmp / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "state" / f"checkpoint-{SLUG}.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    transcript = tmp / "transcript.jsonl"
    lines = transcript_lines if transcript_lines is not None else []
    transcript.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )
    return transcript


def _payload(tmp: Path, transcript: Path, **over) -> dict:
    """A Stop payload with 2.1.228's real field set."""
    base = {
        "session_id": SESSION,
        "transcript_path": str(transcript),
        "cwd": str(tmp),
        "prompt_id": "11111111-2222-3333-4444-555555555555",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "Wrote the guard and its test.",
        "background_tasks": [],
        "session_crons": [],
    }
    base.update(over)
    return base


def _env(**over) -> dict:
    env = dict(os.environ)
    for leak in ("CLAUDE_PROJECT_DIR", "WORKSPACE_ROOT", "CLAUDE_HANDOFF_AUTO"):
        env.pop(leak, None)
    env.update(
        {
            "CLAUDE_HANDOFF_SOFT_THRESHOLD": "40",
            "CLAUDE_HANDOFF_HARD_THRESHOLD": "45",
            "CLAUDE_CODE_SESSION_ID": SESSION,
            # Short enough to run in a suite, long enough that a poll happens.
            "CLAUDE_HANDOFF_UNATTENDED_WAIT": "4",
            "CLAUDE_HANDOFF_UNATTENDED_POLL": "1",
            "CLAUDE_HANDOFF_UNATTENDED_MAX": "100",
            # Never let a stall test reach a real transport.
            "CHECKPOINT_TELEGRAM_TARGET": "",
            "OPS_RADAR_TELEGRAM_TARGET": "",
            "ODIN_CADENCE_TELEGRAM_TARGET": "",
        }
    )
    env.update({k: str(v) for k, v in over.items()})
    return env


class _Result:
    def __init__(self, proc, seconds: float, state: dict):
        self.out = proc.stdout
        self.err = proc.stderr
        self.code = proc.returncode
        self.seconds = seconds
        self.state = state

    @property
    def decision(self) -> dict | None:
        if not self.out.strip():
            return None
        return json.loads(self.out)


def _run(hook: Path, payload: dict, env: dict, tmp: Path) -> _Result:
    started = time.monotonic()
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(hook)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp),
        timeout=120,
    )
    elapsed = time.monotonic() - started
    after = tmp / ".claude" / "state" / f"checkpoint-{SLUG}.json"
    state = json.loads(after.read_text(encoding="utf-8")) if after.exists() else {}
    return _Result(proc, elapsed, state)


def _assert_silent(res: _Result, why: str) -> None:
    assert res.code == 0, f"{why}: exited {res.code}\n{res.err}"
    assert "Traceback" not in res.err, f"{why}: crashed\n{res.err}"
    assert res.out.strip() == "", f"{why}: spoke when it should not:\n{res.out}"


def _ralph(tmp: Path, session: str) -> None:
    """The plugin's own state file, byte-shape from setup-ralph-loop.sh."""
    (tmp / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "ralph-loop.local.md").write_text(
        "---\n"
        "active: true\n"
        "iteration: 4\n"
        f"session_id: {session}\n"
        "max_iterations: 30\n"
        'completion_promise: "DONE"\n'
        'started_at: "2026-08-17T09:00:00Z"\n'
        "---\n\n"
        "Drive ste-check --skills to zero errors.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# SC-1 - the offer goes silent for a live continuation loop
# ---------------------------------------------------------------------------
#
# Six tests in this file carry no `assert` of their own - the three below, plus
# `test_unattended_stops_at_the_continuation_ceiling`,
# `test_bails_on_stop_hook_active` and `test_silent_below_the_soft_threshold`.
# They were checked on 2026-08-20 for the "a test that cannot fail" shape and
# LEFT UNCHANGED, because each delegates to `_assert_silent`, which carries three
# asserts. Measured rather than reasoned: each of the six was driven red by a
# mutation of the product code it covers, with PYTHONDONTWRITEBYTECODE=1 and the
# caches cleared -
#   session_crons dropped from `continuation_claimant`  -> line 199 red
#   background_tasks dropped from `continuation_claimant` -> line 217 red
#   the ralph-loop branch disabled                      -> line 234 red
#   `if done >= maximum` disabled                       -> the ceiling test red
#   the `stop_hook_active` guard disabled               -> that test red
#   the unattended soft-threshold gate removed          -> that test red
# so the delegation is real and moving the asserts inline would buy nothing.

def test_silent_when_a_wakeup_is_scheduled(tmp_path):
    """SC-1. A scheduled `/loop` wakeup means the session continues on its own.

    `session_crons` is 2.1.228's own field and its description says it carries the
    tasks that "will wake this session later". A menu injected here spends the
    loop's turn on a question nobody is at the keyboard to read.
    """
    transcript = _seed(tmp_path, _state())
    payload = _payload(
        tmp_path,
        transcript,
        session_crons=[
            {"cron": "*/30 * * * *", "recurring": True, "prompt": "check CI"}
        ],
    )
    _assert_silent(_run(OFFER_HOOK, payload, _env(), tmp_path), "a /loop is scheduled")


def test_silent_when_background_work_is_in_flight(tmp_path):
    """SC-1. In-flight background work means the session is paused, not finished.

    The field's own description draws exactly this distinction, so a hook that
    speaks here has read "paused waiting to be woken" as "done".
    """
    transcript = _seed(tmp_path, _state())
    payload = _payload(
        tmp_path,
        transcript,
        background_tasks=[{"task_id": "t-1", "status": "running"}],
    )
    _assert_silent(
        _run(OFFER_HOOK, payload, _env(), tmp_path), "background work in flight"
    )


def test_silent_when_ralph_owns_this_session(tmp_path):
    """SC-1. A ralph-loop state file naming THIS session claims the Stop event.

    Two Stop hooks stand on one event. Ours has no business adding a second
    blocking message to a turn the plugin is already driving.
    """
    transcript = _seed(tmp_path, _state())
    _ralph(tmp_path, SESSION)
    _assert_silent(
        _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path),
        "ralph owns this session",
    )


def test_speaks_when_ralph_belongs_to_another_session(tmp_path):
    """SC-1. The guard is session-scoped, so a sibling's loop must not silence us.

    The plugin checks `session_id` for this reason. A guard keyed on the file's
    mere existence would let one window's loop mute every other window on the same
    tree, which is the shared-state defect the state files were keyed by session
    to end.
    """
    transcript = _seed(tmp_path, _state())
    _ralph(tmp_path, "99999999-0000-0000-0000-000000000000")
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    assert res.code == 0, res.err
    assert res.decision is not None, "another session's ralph file silenced this one"
    assert res.decision["decision"] == "block"


# ---------------------------------------------------------------------------
# SC-2, SC-3, SC-8, SC-9 - the PreCompact keep-set
# ---------------------------------------------------------------------------

def _precompact_module():
    spec = importlib.util.spec_from_file_location(
        "checkpoint_precompact", str(PRECOMPACT_HOOK)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DROP_MARKERS = ("drop the following", "discard the following")


def _split_keep_set(out: str) -> tuple[str, str]:
    """The keep half and the drop half of the rendered keep-set, lowercased.

    Both halves are asserted separately because a word present anywhere in the
    output proves nothing about the half that is supposed to carry it. Two
    spellings of the divider are accepted so renaming the header is not a false
    failure; a header that matches neither returns an empty drop half, which
    fails loudly in the caller rather than silently passing.
    """
    body = out.lower()
    for marker in _DROP_MARKERS:
        head, found, tail = body.partition(marker)
        if found:
            return head, tail
    return body, ""


def _keep_section(out: str) -> str:
    return _split_keep_set(out)[0]


def _drop_section(out: str) -> str:
    return _split_keep_set(out)[1]


def _precompact_payload(tmp: Path, trigger: str) -> dict:
    """PreCompact's real field set: no stop fields, a trigger, and instructions."""
    return {
        "session_id": SESSION,
        "transcript_path": str(tmp / "transcript.jsonl"),
        "cwd": str(tmp),
        "hook_event_name": "PreCompact",
        "trigger": trigger,
        "custom_instructions": None,
    }


@pytest.mark.parametrize("trigger", ["manual", "auto"])
def test_keep_set_on_either_trigger(tmp_path, trigger):
    """SC-2. Both compaction triggers get the keep-set on stdout.

    The automatic path is the one that matters, because it is the path that fires
    without anybody present. 2.1.228 collects the stdout of a succeeded hook as
    the custom compact instructions, so this text is the whole lever.
    """
    _seed(tmp_path, _state())
    res = _run(
        PRECOMPACT_HOOK, _precompact_payload(tmp_path, trigger), _env(), tmp_path
    )
    assert res.code == 0, f"trigger {trigger}: exited {res.code}\n{res.err}"
    keep = _keep_section(res.out)
    # Scoped to the KEEP half and phrased distinctively, both since 2026-08-20.
    # The probe was five bare words against the WHOLE output, and two of them
    # could not fail: deleting the "next concrete action" bullet outright left
    # `"next" in body` satisfied by "re-litigated by the next turn" three bullets
    # up, and `"path"` by any mention of paths anywhere. Measured - that deletion
    # kept all 198 checkpoint tests green. The single most operationally
    # important line of the keep-set could be removed with the contract passing.
    #
    # The cost is deliberate and is the smaller one: these phrases pin WORDING,
    # so re-writing a bullet fails this test. That is the right trade here
    # because the wording IS the product - a summariser reads these exact
    # sentences - and a rewrite should be a decision, not a silent loss.
    for wanted in (
        "objective",
        "acceptance",
        "decision",
        "next concrete action",
        "exact file path",
        "last instruction",
        "constraint",
    ):
        assert wanted in keep, (
            f"trigger {trigger}: keep-set omits {wanted!r}:\n{res.out}"
        )


def test_keep_set_names_what_to_drop(tmp_path):
    """SC-2. The keep-set says what to discard, not only what to keep.

    A summariser told only what to preserve keeps everything it is unsure about,
    which spends the budget the compaction was run to reclaim.

    Both bullets are asserted INSIDE the drop section since 2026-08-20. Against
    the whole output the probe was `"file" in body and "output" in body`, and
    "file" is satisfied by the KEEP section's "Exact file paths", so the message
    "the two bulk items are not named" claimed more than the method established.
    Measured: deleting the file-contents bullet, and separately the
    command-output bullet, each left all 198 checkpoint tests green.
    """
    _seed(tmp_path, _state())
    res = _run(
        PRECOMPACT_HOOK, _precompact_payload(tmp_path, "auto"), _env(), tmp_path
    )
    drop = _drop_section(res.out)
    assert drop, f"nothing is named droppable:\n{res.out}"
    assert "contents of file" in drop, (
        f"the drop list does not name file contents:\n{res.out}"
    )
    assert "output of" in drop, (
        f"the drop list does not name command output:\n{res.out}"
    )


def test_keep_set_survives_a_factless_environment(tmp_path):
    """SC-3. With no git tree and no readable transcript, the fixed block still ships.

    `tmp_path` is not a repository, so every machine fact here is uncollectable.
    A hook that prints nothing in this case has handed the summariser the default
    behaviour precisely when the environment was already degraded.
    """
    payload = _precompact_payload(tmp_path, "auto")
    payload["transcript_path"] = str(tmp_path / "absent.jsonl")
    res = _run(PRECOMPACT_HOOK, payload, _env(), tmp_path)
    assert res.code == 0, res.err
    assert len(res.out.strip()) > 200, (
        f"a factless environment produced no keep-set:\n{res.out!r}"
    )


def test_precompact_never_blocks_compaction(tmp_path):
    """SC-3. Exit 2 blocks compaction in 2.1.228, so this hook must never return it.

    Asserted against a deliberately hostile input rather than the happy path: an
    unparseable stdin is the cheapest way a hook reaches an unplanned exit code.
    """
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(PRECOMPACT_HOOK)],
        input="not json at all",
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode != 2, "a malformed payload blocked compaction"
    assert proc.returncode == 0, f"exited {proc.returncode}\n{proc.stderr}"


def test_collected_facts_are_redacted(tmp_path):
    """SC-8. A credential-shaped fact never reaches stdout.

    The output becomes part of a compact summary, and PostCompact commits that
    summary to a tracked file, so an unredacted fact here is a secret in git. The
    assertion is on the VALUE, not on the presence of a redaction marker: a hook
    printing both would pass a marker check.

    The planted value is assembled rather than written as a literal. A literal
    key-shaped string in this file is refused by the commit gate, correctly, and
    the 2026-08-02 gate-yield contract lost a day to exactly that.
    """
    mod = _precompact_module()
    planted = "AKIA" + "Q" * 16
    rendered = mod.render({"branch": "main", "status": f"?? key.txt {planted}"})
    assert planted not in rendered, f"the planted credential survived:\n{rendered}"


def test_keep_set_is_bounded(tmp_path):
    """SC-9. Oversized facts are cut, and the cut says where the rest lives.

    Unbounded, this text competes for the very budget the compaction is
    reclaiming. The existing pointer writer already bounds at the write for the
    same reason.
    """
    mod = _precompact_module()
    rendered = mod.render({"status": "M a-very-long-path.py\n" * 4000})
    assert len(rendered) <= 4000, f"rendered {len(rendered)} characters, cap is 4000"
    assert "cut" in rendered.lower(), "the output was truncated without saying so"


# ---------------------------------------------------------------------------
# SC-4 - the operator interrupts
# ---------------------------------------------------------------------------

def test_unattended_yields_to_a_pending_queued_message(tmp_path):
    """SC-4. A message already queued at hook entry ends the wait before it starts.

    The harness delivers a queued message the moment the turn ends, so continuing
    here would overwrite an instruction the operator has already sent. Measured
    shape: an `enqueue` with no matching `remove` is a message still in the queue.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [ENQUEUE])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    _assert_silent(res, "a queued message was pending")
    assert res.seconds < 3, (
        f"waited {res.seconds:.1f}s on a message that had already arrived"
    )


_WINDOW = {
    "unattended_continuations": 42,
    "unattended_done_at": "2026-08-19T03:14:00+00:00",
    "unattended_done_note": "last night's plan",
    "unattended_paused_at": "2026-08-19T03:14:01+00:00",
    "unattended_stop_reason": "the plan is finished: last night's plan",
}


def test_an_operator_turn_starts_a_new_window(tmp_path):
    """SC-4. A Stop the hook did not continue clears the counters and the marker.

    Added 2026-08-19 with the done marker, and it is the half that makes the
    marker survivable. Without it the FIRST finished plan ends the mode for the
    rest of the session: the marker is checked before the wait, so every later
    pause hands the turn straight back, and the operator's next instruction never
    reaches the line that would retire it. He would have to re-run
    `--unattended on` after every completed plan, having been told the switch is
    his and nothing lowers it.

    A differing `prompt_id` is the signal. The operator typing during the grace
    period is visible to `_wait_out_the_grace`, but by the Stop that closes the
    turn his message opened, that queue entry is consumed; the turn identity is
    not.

    The switch is asserted untouched in the same breath, because "clear the
    window" and "clear the mode" are one careless line apart.
    """
    transcript = _seed(
        tmp_path,
        _state(session_unattended=True, unattended_turn_id="an-older-turn", **_WINDOW),
        [ENQUEUE],
    )
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    _assert_silent(res, "a queued message was pending")
    for key in _WINDOW:
        assert key not in res.state, (
            f"{key} survived the operator's instruction: {res.state}"
        )
    assert res.state.get("session_unattended") is True, (
        f"clearing the window also cleared the mode: {res.state}"
    )


def test_a_continued_turn_does_not_reset_the_ceiling(tmp_path):
    """SC-6. The hook's own continuations keep counting toward the ceiling.

    The other side of the test above, and the one that keeps it honest: if a
    matching `prompt_id` cleared the window too, the ceiling would reset at every
    pause and bound nothing at all. It is the only bound left behind the marker.
    """
    turn = "11111111-2222-3333-4444-555555555555"
    transcript = _seed(
        tmp_path,
        _state(
            session_unattended=True,
            unattended_turn_id=turn,
            unattended_continuations=100,
        ),
        [],
    )
    res = _run(
        OFFER_HOOK,
        _payload(tmp_path, transcript, prompt_id=turn),
        _env(CLAUDE_HANDOFF_UNATTENDED_MAX=100),
        tmp_path,
    )
    _assert_silent(res, "the ceiling was already reached on this hook's own turn")
    assert res.state.get("unattended_paused_at"), (
        f"a continued turn reset the ceiling instead of hitting it: {res.state}"
    )


def test_a_consumed_queue_is_not_a_pending_one(tmp_path):
    """SC-4. An enqueue with its matching remove is spent and must not stop the wait.

    Both interjections in the 2026-08-17 session left an enqueue AND a remove in
    the transcript. Counting enqueues alone would read every past interruption as
    a live one and disable the mode permanently after its first use.

    The assertion reaches past `decision == "block"` to the reason, because
    today's menu blocks too: a test satisfied by any block is green whether the
    mode ran or was ignored, which is the vacuous shape this contract is written
    to avoid.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [ENQUEUE, REMOVE])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    assert res.decision is not None, (
        "a spent queue entry was read as a live interruption"
    )
    assert res.decision["decision"] == "block"
    assert "Ask the user" not in res.decision["reason"], (
        "the spent queue produced the menu rather than a continuation:\n"
        + res.decision["reason"]
    )
    assert res.seconds >= 3.5, (
        f"returned after {res.seconds:.1f}s without serving the wait"
    )


@pytest.mark.parametrize(
    "break_it", ["absent-file", "no-field"],
    ids=["transcript_deleted", "transcript_path_missing"],
)
def test_unattended_yields_when_the_transcript_cannot_be_read(tmp_path, break_it):
    """SC-4. Unknown counts as spoke: an unreadable transcript hands the turn back.

    Added 2026-08-20 after a mutation survived the whole suite. The transcript is
    the ONLY channel by which the operator's typing reaches this hook - pressing
    Enter clears the input line, so the screen is indistinguishable from silence.
    Flip `_wait_out_the_grace`'s missing-file branch from True to False and the
    hook continues blind: it can no longer see any interruption, so the run keeps
    going past every attempt to stop it until the ceiling fires 100 continuations
    later. All 198 checkpoint tests stayed green on that mutation.

    Both entry doors are covered because they are different lines: a payload with
    no `transcript_path` at all, and a path naming a file that is not there.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [])
    payload = _payload(tmp_path, transcript)
    if break_it == "absent-file":
        transcript.unlink()
    else:
        payload.pop("transcript_path")

    res = _run(OFFER_HOOK, payload, _env(), tmp_path)
    _assert_silent(res, f"the transcript was unreadable ({break_it})")
    assert res.seconds < 3, (
        f"waited {res.seconds:.1f}s on a transcript it could not read; an "
        "unreadable channel must end the wait, not serve it"
    )


def test_unattended_yields_to_an_enqueue_during_the_wait(tmp_path):
    """SC-4. Typing inside the minute hands the turn back within one poll.

    This is the branch the operator is actually buying: he is awake, he sees the
    work stop, and one minute is enough to intervene. The signal is the transcript
    line, not the screen, because pressing Enter clears the input line and leaves
    the screen indistinguishable from silence.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [])

    def interrupt():
        time.sleep(1.5)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(ENQUEUE, ensure_ascii=False) + "\n")

    worker = threading.Thread(target=interrupt, daemon=True)
    worker.start()
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    worker.join(timeout=5)
    _assert_silent(res, "the operator typed during the wait")
    assert res.seconds < 4, (
        f"took {res.seconds:.1f}s to notice; the wait budget is 4s and one poll is 1s"
    )


# ---------------------------------------------------------------------------
# SC-5 - silence continues the work
# ---------------------------------------------------------------------------
#
# Timing, measured 2026-08-20 and left as it is. This file runs in 15.5s and four
# tests serve the 4s grace wait for 14.2s of that, 92%. The two below are the
# duplicated pair: identical fixture, identical hook invocation, and they assert
# on different halves of the SAME output, so merging them would return 4.04s -
# 26% of the file - and hold every property that is held today.
#
# NOT merged, deliberately. The two claims fail for different reasons and a
# merged test would name only the first one it hit, which costs more on the day
# it goes red than the four seconds cost on every green day. The cheaper lever if
# the file ever needs to be faster is `CLAUDE_HANDOFF_UNATTENDED_WAIT` in `_env`:
# at 4s with a 1s poll the wait is served four times over, and 2s would still
# poll twice while halving all four tests. Not taken either, because the margins
# in `assert res.seconds >= 3.5` are what keep these from flaking under load.

def test_unattended_continues_after_silence(tmp_path):
    """SC-5. A silent wait ends in a continuation, not a question.

    The whole point of the mode: he is asleep, so the session must not sit on a
    menu until morning. `decision: block` is the harness's own continuation
    channel, proven by the ralph-loop plugin and by this hook's existing menu.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    assert res.decision is not None, f"the wait ended in silence:\n{res.err}"
    assert res.decision["decision"] == "block"
    assert res.seconds >= 3.5, (
        f"returned after {res.seconds:.1f}s without serving the 4s wait"
    )


def test_the_continuation_leaves_the_call_to_the_model(tmp_path):
    """SC-5. The continuation is conditional and asks the operator nothing.

    A hook cannot know whether work remains. One that instructs an unconditional
    "carry on" manufactures activity at 3am; one that asks a question has not
    solved the problem it was built for. Both branches must be present and no
    branch may route to the operator.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    reason = res.decision["reason"].lower()
    assert "ask the user" not in reason, (
        f"the continuation asks the operator:\n{reason}"
    )
    # The probe was `"if" in reason` until 2026-08-19, when the conditional moved
    # from an if-clause to a question ("Finished the plan, ...? Run ... and stop.")
    # and this test failed on prose that satisfied every word of its own docstring.
    # A contract test pins the property; binding it to one conjunction pinned the
    # wording instead. Both question marks and if-clauses express the condition.
    assert "?" in reason or "if" in reason, "the continuation is unconditional"
    assert "stop" in reason, "the stop branch is missing"
    assert "continue" in reason or "resume" in reason, "the continue branch is missing"


# ---------------------------------------------------------------------------
# SC-6 - what ends a stretch
# ---------------------------------------------------------------------------
#
# The fingerprint fuse this section tested until 2026-08-19 is GONE, and its two
# tests went with it rather than being adapted. It asked whether any file had
# changed across three continuations and called an unchanged answer a finished
# plan; it could not tell that apart from a night of reading, research and
# thinking, and it stopped all three unattended runs ever attempted, at three and
# five continuations. Its replacement is an explicit marker the assistant writes.
#
# What replaced them is below: the marker stops a stretch, the ceiling backstops
# a marker that never arrives, and NEITHER lowers the operator's switch.

def test_unattended_stops_on_the_done_marker(tmp_path):
    """SC-6. A declared-finished plan stops the stretch and records why.

    The record is asserted as well as the silence, because a mode that stops
    without saying so is indistinguishable from a mode that crashed.
    """
    transcript = _seed(
        tmp_path,
        _state(
            session_unattended=True,
            unattended_turn_id="11111111-2222-3333-4444-555555555555",
            unattended_done_at="2026-08-19T03:14:00+00:00",
            unattended_done_note="plan compaction-control: 5 of 5",
            unattended_continuations=7,
        ),
        [],
    )
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    _assert_silent(res, "the plan was already declared finished")
    assert res.state.get("unattended_paused_at"), (
        f"the pause was not recorded in the state file: {res.state}"
    )
    assert "5 of 5" in (res.state.get("unattended_stop_reason") or ""), (
        f"the note did not reach the recorded reason: {res.state}"
    )


def test_a_stopped_stretch_leaves_the_operator_switch_alone(tmp_path):
    """SC-6. Nothing in this hook lowers `session_unattended`. Only the operator does.

    This is the property the 2026-08-19 defect turned on. The switch being up is a
    precondition of the driven compaction, and a hook that lowered it at the end of
    a stretch took the mechanism down with it: the mode ran twice in one session
    and compacted zero times. It is also simply not the hook's call - the switch
    says the operator is away, and he is still away at 3am.
    """
    transcript = _seed(
        tmp_path,
        _state(
            session_unattended=True,
            unattended_turn_id="11111111-2222-3333-4444-555555555555",
            unattended_done_at="2026-08-19T03:14:00+00:00",
            unattended_continuations=7,
        ),
        [],
    )
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    _assert_silent(res, "the plan was already declared finished")
    assert res.state.get("session_unattended") is True, (
        f"the hook lowered the operator's switch: {res.state}"
    )


def test_a_turn_with_no_prompt_id_does_not_retire_the_ceiling(tmp_path):
    """SC-6. An EMPTY turn id clears nothing, so the one hard bound survives.

    Added 2026-08-20 after a mutation survived the whole suite. Drop the `turn
    and` half of the new-window guard and a Stop that carries no `prompt_id`
    compares "" against the recorded id, finds them different, and clears the
    window - the continuation count AND the done marker - on EVERY such Stop. The
    ceiling then resets before it is ever read and bounds nothing at all, which
    is the failure it is the last backstop against: the marker may never be
    written, and nothing else stops a run that keeps moving.

    `test_an_operator_turn_starts_a_new_window` is the other side and cannot see
    this: its payload carries a prompt_id, so the guard's two halves are
    indistinguishable there. All 198 checkpoint tests stayed green on the
    mutation.
    """
    transcript = _seed(
        tmp_path,
        _state(
            session_unattended=True,
            unattended_turn_id="an-older-turn",
            unattended_continuations=100,
        ),
        [],
    )
    payload = _payload(tmp_path, transcript)
    payload.pop("prompt_id")

    res = _run(
        OFFER_HOOK, payload, _env(CLAUDE_HANDOFF_UNATTENDED_MAX=100), tmp_path
    )
    _assert_silent(res, "the ceiling was reached on a turn carrying no prompt_id")
    assert res.state.get("unattended_continuations") == 100, (
        f"a turn with no prompt_id reset the continuation count: {res.state}"
    )
    assert res.state.get("unattended_paused_at"), (
        f"the ceiling did not fire, so it bounds nothing: {res.state}"
    )


def test_unattended_stops_at_the_continuation_ceiling(tmp_path):
    """SC-6. The continuation count has a hard ceiling, independent of the marker.

    The backstop for the marker never being written - a run that keeps moving and
    never ends, which is the failure the ralph-loop plugin bounds with
    `--max-iterations` for the same reason.
    """
    transcript = _seed(
        tmp_path,
        _state(
            session_unattended=True,
            unattended_turn_id="11111111-2222-3333-4444-555555555555",
            unattended_continuations=100,
        ),
        [],
    )
    res = _run(
        OFFER_HOOK,
        _payload(tmp_path, transcript),
        _env(CLAUDE_HANDOFF_UNATTENDED_MAX=100),
        tmp_path,
    )
    _assert_silent(res, "the continuation ceiling was reached")


# ---------------------------------------------------------------------------
# SC-7, SC-10 - the properties that must not move
# ---------------------------------------------------------------------------

def test_mode_off_still_offers_the_menu(tmp_path):
    """SC-7. With the mode off, the offer text and the four options are unchanged.

    ALREADY GREEN today. Here as the regression floor: the wait must be reachable
    only through the new switch, never as a new default the operator discovers at
    3am.
    """
    transcript = _seed(tmp_path, _state(), [])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    assert res.decision["decision"] == "block"
    reason = res.decision["reason"]
    assert "Ask the user" in reason, "the menu stopped asking the operator"
    assert reason.count("`/compact`") == 1
    assert res.seconds < 3, f"mode off waited {res.seconds:.1f}s; it must not wait"


def test_mode_on_still_saves_silently(tmp_path):
    """SC-7. With the old auto mode on, the hands-off save text is unchanged.

    ALREADY GREEN today. `session_auto` keeps its boolean meaning, so a
    three-valued switch must not turn this into a wait.
    """
    transcript = _seed(tmp_path, _state(session_auto=True), [])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    reason = res.decision["reason"]
    assert "AUTO MODE is on" in reason, f"the auto text changed:\n{reason}"
    assert res.seconds < 3, f"mode on waited {res.seconds:.1f}s; it must not wait"


def test_the_auto_reason_forbids_the_assistant_from_compacting(tmp_path):
    """SC-7. Auto mode tells the assistant to save and NOT to compact.

    Added 2026-08-20 after a mutation survived the whole suite: delete the
    sentence and all 198 checkpoint tests stay green. It is not decoration. The
    driven path's whole guarantee is the ORDER - handoff on disk first, boundary
    second - and the hook enforces it by refusing to submit until an
    `_handoff_auto_` archive newer than `last_offer_at` exists. An assistant that
    runs /compact itself on reading this text compacts BEFORE its own save, which
    is the ordering failure `_handoff_since` exists to prevent, and then the
    hook's own submit lands on top as a second compaction.
    """
    transcript = _seed(tmp_path, _state(session_auto=True), [])
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    reason = res.decision["reason"].lower()
    assert "not run /compact" in reason, (
        f"auto mode no longer forbids the assistant its own /compact:\n{reason}"
    )


def test_bails_on_stop_hook_active(tmp_path):
    """SC-7. The anti-loop guard still holds on a turn this hook did not continue.

    ALREADY GREEN today. 2.1.228 warns a hook that blocks eight consecutive times
    and names `stop_hook_active` as the field to check. Unattended mode may only
    ignore it on a turn it continued itself.
    """
    transcript = _seed(tmp_path, _state(session_unattended=True), [])
    payload = _payload(tmp_path, transcript, stop_hook_active=True)
    _assert_silent(_run(OFFER_HOOK, payload, _env(), tmp_path), "stop_hook_active set")


def test_silent_below_the_soft_threshold(tmp_path):
    """SC-10. Under the soft threshold nothing speaks, in any mode.

    ALREADY GREEN for the two old modes and new for unattended, which reads the
    threshold directly rather than the once-per-bucket flag. Without this the mode
    would hold every turn of the day for a minute.
    """
    transcript = _seed(
        tmp_path,
        _state(
            session_unattended=True,
            needs_compact_offer=False,
            offer_level=None,
            used_percentage=22.0,
            remaining_percentage=78.0,
        ),
        [],
    )
    res = _run(OFFER_HOOK, _payload(tmp_path, transcript), _env(), tmp_path)
    _assert_silent(res, "context was at 22%")


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------

def test_the_switch_takes_unattended_and_leaves_auto_alone(tmp_path):
    """SC-7. `--unattended on` sets the new flag and implies the silent save.

    A second switch beside `--auto`, never a third value inside it. Two decisions
    live here: whether a checkpoint saves silently, and whether a pause hands the
    turn back. Collapsing them into one three-valued field would put `auto_mode()`
    in the path of every later change, and `auto_mode()` is what the statusline and
    both existing modes read.
    """
    from scripts.utils import checkpoint_paths as CP

    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(SWITCH), "--unattended", "on"],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 0, f"the switch refused `unattended`:\n{proc.stderr}"
    state = CP.read_json(CP.state_path(tmp_path, SLUG))
    assert state.get("session_unattended") is True, f"unattended not recorded: {state}"
    assert state.get("session_auto") is True, (
        f"unattended must imply the silent save: {state}"
    )


def test_the_switch_reports_the_mode_without_changing_it(tmp_path):
    """SC-7. `--unattended status` names the mode and writes nothing.

    A status that cannot say whether this window will halt at a pause leaves the
    operator guessing, which is the question the switch exists to answer.
    """
    from scripts.utils import checkpoint_paths as CP

    CP.write_json_atomic(
        CP.state_path(tmp_path, SLUG),
        {"session_auto": True, "session_unattended": True},
    )
    before = CP.state_path(tmp_path, SLUG).read_text(encoding="utf-8")
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(SWITCH), "--unattended", "status"],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "unattended" in proc.stdout.lower(), f"status hides the mode:\n{proc.stdout}"
    assert CP.state_path(tmp_path, SLUG).read_text(encoding="utf-8") == before, (
        "status wrote to the state file"
    )
