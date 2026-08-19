"""The checkpoint system must not confuse one session with another.

Until 2026-08-16 every path below the workspace root was shared: one
`.claude/state/checkpoint-state.json` and one `.latest/{summary,prompt}.md` for
the whole tree. Three sessions run on this workspace routinely, and the shared
files made them one:

  - the statusline of session A wrote A's context usage into the file the Stop
    hook of session B reads, so B (idle) was told to checkpoint at A's 46% and
    A, whose context was actually filling, got nothing: `last_offered_bucket`
    had already been consumed by B;
  - the inject hook read the one shared pointer, so a resumed session could be
    handed the handoff of a DIFFERENT session while the text asserted "a
    previous checkpoint was found", a claim nothing in the hook had established.

Both were reproduced against the pre-fix hooks before this file was written.

The fix keys state and the injected pointer by session id. It deliberately KEEPS
the shared `.latest/{summary,prompt}.md`, because that pair has a second reader
with a different question: `scripts/next-signal.py` wants "the newest handoff in
this workspace", where last-writer-wins is the correct answer, not a race. The
per-session dir answers "the handoff of THIS session", which is the only thing
safe to inject.
"""
import json
import os
import re
import subprocess
import time
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.colors import supports_ansi  # noqa: E402

HOOKS = ROOT / ".claude" / "hooks"
STATUSLINE = HOOKS / "checkpoint-statusline.py"
OFFER = HOOKS / "checkpoint-offer.py"
INJECT = HOOKS / "checkpoint-inject.py"
SAVE = HOOKS / "checkpoint-save.py"

SESSION_A = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B = "bbbbbbbb-9999-8888-7777-666666666666"


@pytest.fixture()
def env(tmp_path):
    """A project root and a data root that are both scratch.

    The state files follow the payload's project root; the handoff archive
    follows the DATA root, exactly as they do on the live workspace.
    """
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    e = dict(os.environ)
    e["HEADING_OS_DATA"] = str(data)
    e.pop("CLAUDE_HANDOFF_AUTO", None)
    e["CLAUDE_HANDOFF_SOFT_THRESHOLD"] = "40"
    e["CLAUDE_HANDOFF_HARD_THRESHOLD"] = "45"
    return {"env": e, "project": project, "data": data}


def _run(hook: Path, env: dict, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env["env"],
    )


def _statusline(env, session, used):
    return _run(STATUSLINE, env, {
        "session_id": session,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "context_window": {"used_percentage": used, "remaining_percentage": 100 - used},
    })


def _stop(env, session):
    return _run(OFFER, env, {
        "session_id": session,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "stop_hook_active": False,
    })


def _compact(env, session, summary, trigger="manual"):
    return _run(SAVE, env, {
        "session_id": session,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "trigger": trigger,
        "compact_summary": summary,
        "transcript_path": "",
    })


def _inject(env, session, source="resume"):
    return _run(INJECT, env, {
        "session_id": session,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "source": source,
    })


def _archive_dir(env) -> Path:
    return env["data"] / "outputs" / "operations" / "handoff-archive"


def _state_dir(env) -> Path:
    return env["project"] / ".claude" / "state"


# --------------------------------------------------------------------------
# The offer goes to the session whose context is actually filling
# --------------------------------------------------------------------------

def test_a_full_session_does_not_trip_an_idle_sessions_stop_hook(env):
    """The measured failure: B is idle, A is at 46%, and B got A's offer."""
    _statusline(env, SESSION_A, 46)

    idle = _stop(env, SESSION_B)
    assert idle.stdout.strip() == "", (
        "an idle session was offered a checkpoint computed from another "
        f"session's context usage:\n{idle.stdout}"
    )


def test_the_full_session_still_gets_its_own_offer(env):
    """The other half of the same bug: A was silenced by B consuming the bucket."""
    _statusline(env, SESSION_A, 46)
    _stop(env, SESSION_B)

    mine = _stop(env, SESSION_A)
    assert mine.stdout.strip(), "the session that crossed the threshold got no offer"
    decision = json.loads(mine.stdout)
    assert decision["decision"] == "block"
    assert "46%" in decision["reason"]


def test_each_session_keeps_its_own_state_file(env):
    _statusline(env, SESSION_A, 46)
    _statusline(env, SESSION_B, 12)

    files = sorted(p.name for p in _state_dir(env).glob("checkpoint-*.json"))
    assert len(files) == 2, f"expected one state file per session, got {files}"
    assert not (_state_dir(env) / "checkpoint-state.json").exists(), (
        "the shared state file is back; it is what made two sessions one"
    )


# --------------------------------------------------------------------------
# Injection never carries a foreign session's handoff
# --------------------------------------------------------------------------

def test_inject_carries_only_this_sessions_handoff(env):
    _compact(env, SESSION_A, "SESSION-A-WORK refactor the parser")
    _compact(env, SESSION_B, "SESSION-B-WORK fix the CI pipeline")

    out = _inject(env, SESSION_B).stdout
    assert "SESSION-B-WORK" in out, f"session B did not get its own handoff:\n{out}"
    assert "SESSION-A-WORK" not in out, (
        f"session B was injected session A's handoff:\n{out}"
    )


def test_inject_is_silent_for_a_session_that_never_saved(env):
    """No handoff beats a foreign one. Silence is the intended answer."""
    _compact(env, SESSION_A, "SESSION-A-WORK refactor the parser")

    out = _inject(env, "cccccccc-0000-0000-0000-000000000000").stdout
    assert out.strip() == "", f"a session with no handoff was injected one:\n{out}"


def test_inject_claims_only_its_own_session(env):
    """scope-claims: the text may not assert more than the lookup established."""
    _compact(env, SESSION_A, "SESSION-A-WORK refactor the parser")
    out = _inject(env, SESSION_A).stdout
    assert "this session" in out.lower(), (
        f"the injected header does not say whose handoff this is:\n{out}"
    )


def test_inject_carries_one_language(env):
    """The offer hook dropped its second language for the public engine; the
    inject hook shipped a full Russian paragraph beside the English one."""
    _compact(env, SESSION_A, "summary body")
    out = _inject(env, SESSION_A).stdout
    cyrillic = "".join(ch for ch in out if "Ѐ" <= ch <= "ӿ")
    assert not cyrillic, f"inject carries a second language: {cyrillic!r}"


def test_compact_source_injects_no_stale_body(env):
    """On SessionStart source=compact the harness has just put a summary of this
    same session in context. Re-injecting the pointer only competes with it."""
    _compact(env, SESSION_A, "SESSION-A-WORK refactor the parser")
    out = _inject(env, SESSION_A, source="compact").stdout
    assert "SESSION-A-WORK" not in out, (
        f"a handoff body was injected on top of the compaction summary:\n{out}"
    )


# --------------------------------------------------------------------------
# The shared pointer survives, because /next reads it
# --------------------------------------------------------------------------

def test_shared_latest_pointer_still_written(env):
    """scripts/next-signal.py reads .latest/summary.md. Keying pointers by
    session without keeping this one would leave /next printing an empty block."""
    _compact(env, SESSION_A, "SESSION-A-WORK refactor the parser")

    shared = _archive_dir(env) / ".latest" / "summary.md"
    assert shared.is_file(), "the shared .latest/summary.md is gone; /next reads it"
    text = shared.read_text(encoding="utf-8")
    assert "## Objective" in text and "## Next steps" in text, (
        "next-signal.py parses these two headings out of the pointer"
    )


def test_shared_pointer_is_the_newest_session(env):
    _compact(env, SESSION_A, "SESSION-A-WORK refactor the parser")
    _compact(env, SESSION_B, "SESSION-B-WORK fix the CI pipeline")
    shared = (_archive_dir(env) / ".latest" / "summary.md").read_text(encoding="utf-8")
    assert "SESSION-B-WORK" in shared, "the shared pointer is not the newest handoff"


# --------------------------------------------------------------------------
# What the injected pointer costs
# --------------------------------------------------------------------------

def test_pointer_summary_is_bounded(env):
    """The live pointer was 32261 bytes against an 8000-character inject cap, so
    three quarters of it never reached a session and what did arrive was cut
    mid-sentence. Bound it where it is WRITTEN, so the file equals the injection."""
    _compact(env, SESSION_A, "x" * 40000)

    pointer = (_archive_dir(env) / ".latest" / f"{SESSION_A[:32]}" / "summary.md")
    assert pointer.is_file(), "per-session pointer not written"
    body = pointer.read_text(encoding="utf-8")
    assert len(body) < 12000, f"pointer is {len(body)} bytes; it will be truncated blind"
    assert "handoff-archive/" in body, "the bounded pointer must name the full archive"


# --------------------------------------------------------------------------
# Per-session artifacts are cleaned up
# --------------------------------------------------------------------------

def test_dead_session_artifacts_are_pruned(env):
    """One dir and one state file per session, never revisited once the session
    ends. The nexi plugin pruned the pointer dirs and left the state files to
    grow without bound; both are pruned here."""
    for i in range(30):
        sid = f"sess-{i:04d}-0000-0000-000000000000"
        _statusline(env, sid, 10)
        _compact(env, sid, f"work {i}")

    pointer_dirs = [p for p in (_archive_dir(env) / ".latest").iterdir() if p.is_dir()]
    state_files = list(_state_dir(env).glob("checkpoint-*.json"))
    assert len(pointer_dirs) <= 26, f"pointer dirs grew to {len(pointer_dirs)}"
    assert len(state_files) <= 26, f"state files grew to {len(state_files)}"


def test_pruning_never_touches_the_archive(env):
    """Pointers are disposable. Archives are the record."""
    for i in range(30):
        sid = f"sess-{i:04d}-0000-0000-000000000000"
        _compact(env, sid, f"work {i}")
    archives = list(_archive_dir(env).glob("*_handoff_*.md"))
    assert len(archives) == 30, f"an archive file was pruned: {len(archives)} of 30 left"


def test_pruning_keeps_the_live_session(env):
    for i in range(30):
        _compact(env, f"sess-{i:04d}-0000-0000-000000000000", f"work {i}")
    _compact(env, SESSION_A, "the live session")
    assert (_archive_dir(env) / ".latest" / SESSION_A[:32] / "summary.md").is_file(), (
        "the running session's own pointer was pruned"
    )


# --------------------------------------------------------------------------
# Auto mode: built, off by default, and honest about what it knows
# --------------------------------------------------------------------------

def test_prompt_mode_is_the_default(env):
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "Ask the user" in reason, "auto mode is on without being asked for"
    assert "AUTO MODE" not in reason


def test_auto_mode_saves_without_asking(env):
    env["env"]["CLAUDE_HANDOFF_AUTO"] = "1"
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "AUTO MODE" in reason
    assert "Ask the user" not in reason


def test_auto_mode_points_at_the_skill_instead_of_restating_it(env):
    """The nexi plugin inlined the whole section list into the hook text, which
    is a second copy of the skill's contract that nothing keeps in step."""
    env["env"]["CLAUDE_HANDOFF_AUTO"] = "1"
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert ".claude/skills/checkpoint/SKILL.md" in reason, (
        "auto mode does not name the one file that defines the handoff format"
    )
    assert "Acceptance criteria" not in reason, (
        "the hook restates the skill's sections; that is the copy that goes stale"
    )


def test_auto_mode_does_not_claim_a_compaction_point_it_never_verified(env):
    """scope-claims: nothing in the hook establishes where auto-compact fires
    unless the window is actually configured."""
    env["env"]["CLAUDE_HANDOFF_AUTO"] = "1"
    env["env"].pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
    env["env"].pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    # The honest phrasings, either of which satisfies the rule. "not configured"
    # was the only accepted wording until 2026-08-19; the sentence now says "a
    # point this hook cannot determine" instead, which is the same claim in
    # plainer words. The property under test is unchanged: no NUMBER may appear
    # when nothing configured one.
    lowered = reason.lower()
    honest = "not configured" in lowered or "cannot determine" in lowered
    assert "auto-compact" not in lowered or honest, (
        f"the hook asserts a compaction point nothing configured:\n{reason}"
    )


def test_auto_mode_names_the_compaction_point_when_it_is_configured(env):
    env["env"]["CLAUDE_HANDOFF_AUTO"] = "1"
    env["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "50"
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "50" in reason, f"the configured compaction point is not surfaced:\n{reason}"


# --------------------------------------------------------------------------
# Auto mode, flipped mid-session, for THIS session only
#
# CLAUDE_HANDOFF_AUTO is a launch-time decision for the whole workspace. The
# decision the operator actually makes is a running one, taken twenty minutes
# in ("this is going to be long, stop asking"), and it belongs to ONE window:
# three sessions on this tree routinely do three different sizes of work.
#
# The per-session state file already existed for the collision fix, so the
# session flag is one key in a file that is already written, already read, and
# already pruned with the session. `session_auto` is a separate key from `auto`
# on purpose: `auto` is the statusline's echo of the RESOLVED mode, rewritten on
# every render, so storing the operator's choice there would erase it a second
# later.
# --------------------------------------------------------------------------

CLI = ROOT / "scripts" / "checkpoint-paths.py"


def _auto(env, session, value):
    e = dict(env["env"])
    e["CLAUDE_CODE_SESSION_ID"] = session
    e["CLAUDE_PROJECT_DIR"] = str(env["project"])
    return subprocess.run(
        [sys.executable, str(CLI), "--auto", value],
        capture_output=True, text=True, env=e,
    )


def _state_of(env, session) -> dict:
    path = _state_dir(env) / f"checkpoint-{session[:32]}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def test_auto_on_writes_the_flag_for_this_session(env):
    result = _auto(env, SESSION_A, "on")
    assert result.returncode == 0, result.stderr
    assert _state_of(env, SESSION_A).get("session_auto") is True


def test_auto_off_writes_the_flag_for_this_session(env):
    _auto(env, SESSION_A, "on")
    _auto(env, SESSION_A, "off")
    assert _state_of(env, SESSION_A).get("session_auto") is False


def test_auto_status_reports_without_changing_anything(env):
    _auto(env, SESSION_A, "on")
    before = _state_of(env, SESSION_A)
    result = _auto(env, SESSION_A, "status")
    assert result.returncode == 0
    assert "on" in result.stdout.lower()
    assert _state_of(env, SESSION_A) == before, "status is not read-only"


def _unattended(env, session, value):
    e = dict(env["env"])
    e["CLAUDE_CODE_SESSION_ID"] = session
    e["CLAUDE_PROJECT_DIR"] = str(env["project"])
    return subprocess.run(
        [sys.executable, str(CLI), "--unattended", value],
        capture_output=True, text=True, env=e,
    )


def _offer_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("offer_mod", OFFER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["offer_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cp():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.utils import checkpoint_paths as CP

    return CP


def _wrote(tmp_path: Path, files: list[Path]) -> Path:
    """A transcript claiming this session wrote exactly these files."""
    path = tmp_path / "written.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(f)}}
            ]}})
            for f in files
        ) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("consumer", ["remove", "dequeue", "popAll"])
def test_every_consuming_queue_operation_empties_the_queue(tmp_path, consumer):
    """The harness has FOUR queue operations, and the first version knew two.

    Counting `enqueue` against `remove` alone reads a phantom pending message in
    any session where the harness consumed one by `dequeue`. Measured across all
    44 transcripts for this project: 660 enqueue, 422 remove, 231 dequeue, 1
    popAll - false-positive in 28 of the 44. A false positive here is not a
    cosmetic defect: `_wait_out_the_grace` returns at once, the run halts at its
    first pause, and it leaves no continuation, no stall record and no notice.
    """
    mod = _offer_module()
    transcript = tmp_path / "queue.jsonl"
    transcript.write_text("\n".join(json.dumps(entry) for entry in [
        {"type": "queue-operation", "operation": "enqueue",
         "sessionId": SESSION_A, "content": "wait, I am here"},
        {"type": "queue-operation", "operation": consumer, "sessionId": SESSION_A},
    ]) + "\n", encoding="utf-8")

    assert mod._queue_pending(transcript, SESSION_A) is False, (
        f"a queue consumed by {consumer!r} still reads as pending, which halts "
        "the mode in the majority of real sessions"
    )


def test_an_unconsumed_message_still_reads_as_pending(tmp_path):
    """The other direction: this must not become a way to ignore the operator."""
    mod = _offer_module()
    transcript = tmp_path / "queue.jsonl"
    transcript.write_text(json.dumps(
        {"type": "queue-operation", "operation": "enqueue",
         "sessionId": SESSION_A, "content": "stop"}
    ) + "\n", encoding="utf-8")

    assert mod._queue_pending(transcript, SESSION_A) is True


def test_a_sibling_write_does_not_reset_the_no_progress_fuse(tmp_path):
    """The fuse must measure THIS session, or an overnight run never stalls.

    Another session, a daemon, or a PostToolUse hook writing one file between two
    pauses moved `git status --short`, so the fingerprint changed and the stall
    counter went back to zero. A run with nothing left to do then reached the
    100-continuation ceiling inventing work.
    """
    CP = _cp()
    project = tmp_path / "p"
    project.mkdir()
    mine = project / "mine.py"
    mine.write_text("A\n", encoding="utf-8")
    payload = {"transcript_path": str(_wrote(tmp_path, [mine]))}

    before = CP.progress_fingerprint(project, payload)
    (project / "theirs.py").write_text("B\n", encoding="utf-8")

    assert CP.progress_fingerprint(project, payload) == before, (
        "a file this session never wrote moved this session's progress fuse"
    )


def test_a_second_edit_of_my_own_file_does_move_the_fuse(tmp_path):
    """And the opposite failure: the count could not see it, so real work that
    stayed inside files already written read as three dead continuations."""
    CP = _cp()
    project = tmp_path / "p"
    project.mkdir()
    mine = project / "mine.py"
    mine.write_text("A\n", encoding="utf-8")
    payload = {"transcript_path": str(_wrote(tmp_path, [mine]))}

    before = CP.progress_fingerprint(project, payload)
    mine.write_text("A much longer second version\n", encoding="utf-8")

    assert CP.progress_fingerprint(project, payload) != before


@pytest.mark.parametrize("configured", ["120", "600", "89", "90"])
def test_the_grace_period_cannot_be_set_past_the_registered_timeout(
    monkeypatch, configured
):
    """A wait above the hook's timeout is discarded output: the operator is told
    the session will carry on, and it silently does not.

    The bound is enforced by REFUSAL, not by clamping - `env_int` answers with
    the default when the value is out of range - so an over-large setting lands
    at 60 rather than at the ceiling. Either answer is safe; what matters is that
    no reachable value crosses the registered 90-second timeout.
    """
    CP = _cp()
    monkeypatch.setenv("CLAUDE_HANDOFF_UNATTENDED_WAIT", configured)
    assert CP.wait_seconds() <= CP.UNATTENDED_WAIT_MAX
    assert CP.UNATTENDED_WAIT_MAX < 90


def test_a_grace_period_inside_the_bound_is_honoured(monkeypatch):
    """The ceiling must not become a way to ignore the operator's setting.

    The value used here was 75 until 2026-08-19, when the ceiling itself dropped
    to 60 to make room for the countdown's out-of-loop HERDR calls, so 75 stopped
    being "inside the bound". It is written against the constant now rather than
    against a literal, so the next move of the ceiling cannot make this test
    assert the opposite of its own name.
    """
    CP = _cp()
    inside = CP.UNATTENDED_WAIT_MAX - 15
    assert inside > 0, "the ceiling fell far enough that this test tests nothing"
    monkeypatch.setenv("CLAUDE_HANDOFF_UNATTENDED_WAIT", str(inside))
    assert CP.wait_seconds() == inside


def test_a_finished_background_task_does_not_claim_the_stop_event():
    """Reading list non-emptiness let one completed task silence the checkpoint
    system for the rest of the session."""
    CP = _cp()
    assert CP.continuation_claimant(
        {"background_tasks": [{"task_id": "t-1", "status": "completed"}]}, None
    ) == ""
    assert CP.continuation_claimant(
        {"background_tasks": [{"task_id": "t-1", "status": "running"}]}, None
    ) == "background_tasks"
    assert CP.continuation_claimant(
        {"background_tasks": [{"task_id": "t-1"}]}, None
    ) == "background_tasks", "an unknown state must still claim"


def test_the_stall_record_is_written_once(tmp_path, monkeypatch):
    """`--unattended status` presents this timestamp as the moment the run
    stopped. Re-stamping it at every later pause turns a 03:00 stall into
    whatever time the operator happens to look."""
    monkeypatch.delenv("HEADING_OS_TELEGRAM_CHAT_ID", raising=False)
    mod = _offer_module()
    path = tmp_path / "state.json"

    mod._stop_unattended({}, path, "no progress across 3 consecutive continuations")
    first = json.loads(path.read_text(encoding="utf-8"))
    mod._stop_unattended(first, path, "reached the ceiling of 100 continuations")
    again = json.loads(path.read_text(encoding="utf-8"))

    assert again["unattended_stalled_at"] == first["unattended_stalled_at"]
    assert again["unattended_stop_reason"] == first["unattended_stop_reason"]


def test_a_suppressed_offer_is_not_recorded_as_delivered(env):
    """When something else drives the Stop event the offer is not shown, so it
    must not be marked shown - the operator would lose that threshold for good.

    Narrowed to BELOW the hard threshold on 2026-08-19. The claimant courtesy
    used to be unconditional and this case ran at 46%, above this fixture's hard
    of 45. It now holds only below hard; the case above it is the sibling test,
    and the reason for the split is that the suppressed notice at hard is the
    last save before compaction, which cannot wait a turn. 42 is chosen because
    it must sit ABOVE soft for an offer to be due at all - the fixture runs
    40/45, not the 25/30 defaults, so a value below 40 would test nothing.
    """
    CP = _cp()
    _statusline(env, SESSION_A, 42)
    res = _run(OFFER, env, {
        "session_id": SESSION_A,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "stop_hook_active": False,
        "background_tasks": [{"task_id": "t-1", "status": "running"}],
    })

    assert res.stdout.strip() == "", "the claimant guard did not suppress the offer"
    state = json.loads(
        (_state_dir(env) / f"checkpoint-{CP.safe_slug(SESSION_A)}.json")
        .read_text(encoding="utf-8")
    )
    assert state.get("needs_compact_offer") is True, (
        "an offer that was never delivered was recorded as delivered"
    )
    assert state.get("continuation_claimant"), "the claimant was not recorded"


def test_the_hard_threshold_outranks_claimant_suppression(env):
    """CAP-2. At or above hard the claimant no longer silences the hook.

    Below hard, a scheduled wakeup or in-flight background work deserves the
    turn to itself and the notice can wait. At hard it cannot: that notice is
    the last save before compaction frees the context, and an unattended run
    that grew to 617k tokens with nothing on disk left through this return on
    2026-08-19. The claimant is still recorded either way.
    """
    CP = _cp()
    _statusline(env, SESSION_A, 46)
    res = _run(OFFER, env, {
        "session_id": SESSION_A,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "stop_hook_active": False,
        "background_tasks": [{"task_id": "t-1", "status": "running"}],
    })

    assert res.stdout.strip(), "the hard threshold did not outrank the claimant"
    assert json.loads(res.stdout)["decision"] == "block"
    state = json.loads(
        (_state_dir(env) / f"checkpoint-{CP.safe_slug(SESSION_A)}.json")
        .read_text(encoding="utf-8")
    )
    assert state.get("continuation_claimant"), (
        "the claimant must still be recorded when the threshold outranks it"
    )


def _unattended_stop(env, session, *, turn, active, state_turn):
    CP = _cp()
    path = _state_dir(env) / f"checkpoint-{CP.safe_slug(session)}.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["session_unattended"] = True
    state["unattended_turn_id"] = state_turn
    path.write_text(json.dumps(state), encoding="utf-8")
    e = dict(env["env"])
    e["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "1"
    e["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"
    return subprocess.run(
        [sys.executable, str(OFFER)],
        input=json.dumps({
            "session_id": session,
            "cwd": str(env["project"]),
            "workspace": {"project_dir": str(env["project"])},
            "stop_hook_active": active,
            "prompt_id": turn,
            "transcript_path": str(env["project"] / "absent.jsonl"),
        }),
        capture_output=True, text=True, env=e,
    )


def test_the_mode_survives_stop_hook_active_on_a_turn_it_continued(env):
    """The clause the whole mode rests on, and the half no test covered.

    `stop_hook_active` stays true for the rest of a turn once anything blocked
    it. Honouring it unconditionally would continue exactly once per operator
    turn and then halt, which is the behaviour the mode exists to end.
    """
    _statusline(env, SESSION_A, 46)
    (env["project"] / "absent.jsonl").write_text("", encoding="utf-8")

    ours = _unattended_stop(env, SESSION_A, turn="p1", active=True, state_turn="p1")
    assert ours.stdout.strip(), (
        "the mode halted on a turn it had continued itself:\n" + ours.stderr
    )

    theirs = _unattended_stop(env, SESSION_A, turn="p2", active=True, state_turn="p1")
    assert theirs.stdout.strip() == "", (
        "the anti-loop guard was skipped on a turn this hook did not continue"
    )


def test_unattended_on_raises_auto_and_off_puts_it_back(env):
    """`--unattended off` undoes exactly what `on` did, and nothing more.

    Found by a live run rather than by a test: the first version cleared only its
    own key, so typing `--unattended on` and then `--unattended off` left the
    operator with a silent `auto=on` he never chose.
    """
    assert _unattended(env, SESSION_A, "on").returncode == 0
    assert _state_of(env, SESSION_A).get("session_auto") is True
    assert _unattended(env, SESSION_A, "off").returncode == 0
    state = _state_of(env, SESSION_A)
    assert state.get("session_unattended") is False
    # Narrowed 2026-08-19 (CAP-8). `off` now RESTORES the prior value rather
    # than pinning False. `session_auto` was ABSENT before `on`, and absent is a
    # real value: it means defer to CLAUDE_HANDOFF_AUTO. Writing False over it
    # was a behaviour change wearing the word "restore", because it overrides a
    # workspace default the operator may have set deliberately. The property
    # this test was written for is unchanged - the operator is not left with an
    # auto he never chose - and the sibling test below covers the case where he
    # did choose one.
    assert "session_auto" not in state, (
        "off pinned a value where the session had none, overriding the "
        "workspace default in a direction the operator never chose"
    )
    assert "unattended_prior_auto" not in state, (
        "the bookkeeping key outlived the switch it was recorded for"
    )


def test_unattended_off_leaves_a_deliberate_auto_alone(env):
    """A separately chosen `--auto on` survives an unattended round trip.

    The other half of the asymmetry. Undoing our own side effect must not reach
    a decision the operator made on its own.
    """
    assert _auto(env, SESSION_A, "on").returncode == 0
    _unattended(env, SESSION_A, "on")
    _unattended(env, SESSION_A, "off")
    assert _state_of(env, SESSION_A).get("session_auto") is True, (
        "unattended off clobbered a deliberate auto on"
    )


def test_the_session_flag_turns_auto_on_while_the_env_is_off(env):
    env["env"].pop("CLAUDE_HANDOFF_AUTO", None)
    _auto(env, SESSION_A, "on")
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "AUTO MODE" in reason, f"the session flag did not switch the offer:\n{reason}"


def test_the_session_flag_turns_auto_off_while_the_env_is_on(env):
    """Symmetric on purpose: one window may want the question even when the
    workspace default is silence."""
    env["env"]["CLAUDE_HANDOFF_AUTO"] = "1"
    _auto(env, SESSION_A, "off")
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "Ask the user" in reason, f"the session flag could not override the env:\n{reason}"


def test_the_session_flag_does_not_reach_a_sibling_session(env):
    _auto(env, SESSION_A, "on")
    _statusline(env, SESSION_B, 46)
    reason = json.loads(_stop(env, SESSION_B).stdout)["reason"]
    assert "Ask the user" in reason, (
        f"one session's auto flag silenced another session's offer:\n{reason}"
    )


def test_the_statusline_does_not_clobber_the_session_flag(env):
    """The statusline rewrites this file after every single turn."""
    _auto(env, SESSION_A, "on")
    _statusline(env, SESSION_A, 12)
    _statusline(env, SESSION_A, 46)
    assert _state_of(env, SESSION_A).get("session_auto") is True


def test_the_session_flag_survives_a_compaction(env):
    """The whole point is a long piece of work, which by definition compacts."""
    _auto(env, SESSION_A, "on")
    _statusline(env, SESSION_A, 46)
    _compact(env, SESSION_A, "SESSION-A-WORK the long migration")
    assert _state_of(env, SESSION_A).get("session_auto") is True, (
        "the post-compact reset erased the operator's choice"
    )


def test_the_soft_offer_carries_the_session_switch(env):
    """The operator is already looking at the list when they decide. A command
    they have to remember is a command they will not use.

    42 is deliberately between the fixture's soft (40) and hard (45): the two
    bodies are separate strings, so an assertion at 46 would cover the hard one
    twice and the soft one never.
    """
    _statusline(env, SESSION_A, 42)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    # The two bodies share one option list since 2026-08-19, so the marker has to
    # be the framing line above it rather than an option inside it.
    assert "Consider checkpointing now" in reason, "this is not the soft body"
    assert "/checkpoint unattended on" in reason, (
        f"the soft offer does not name the standing switch:\n{reason}"
    )
    # `/checkpoint auto on` is deliberately NOT here any more. `--unattended on`
    # already sets session_auto, so naming both presented a containment as a
    # choice, which is what made the list confusing.
    assert "/checkpoint auto on" not in reason, (
        f"the withdrawn second switch came back to the menu:\n{reason}"
    )


def test_the_hard_offer_also_carries_the_switch(env):
    """The hard threshold is where a long piece of work spends most of its time.

    Both switches, because the two bodies are separate strings and the hard one
    has already lost a line the soft one kept.
    """
    _statusline(env, SESSION_A, 46)
    _stop(env, SESSION_A)
    _statusline(env, SESSION_A, 52)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "hard threshold reached" in reason, "this is not the hard body"
    for switch in ("/checkpoint unattended on",):
        assert switch in reason, (
            f"the hard offer drops {switch}, which the soft one carries:\n{reason}"
        )


def test_option_two_says_when_the_compaction_happens(env):
    """"From here on the hook does it all" told the operator nothing.

    The option is the one that hands compaction to the hook, and the sentence
    that sold it named neither the action nor the moment. An operator choosing
    between four lines cannot pick the one whose effect is described as "it all".
    The wording now states both: what the hook does, and when it does it.
    """
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "compacts" in reason, (
        f"option 2 never says the hook compacts:\n{reason}"
    )
    assert "every one after" in reason, (
        "option 2 does not say the compaction repeats at each threshold, which "
        f"is the fact that separates it from option 3:\n{reason}"
    )
    assert "does it all" not in reason, (
        f"the vague wording came back to the menu:\n{reason}"
    )


def test_the_menu_reads_correctly_with_every_escape_stripped(env):
    """Colour is decoration on this line; the word is the recommendation.

    Added 2026-08-19 as an EXPERIMENT: the status line is a surface Claude Code
    documents as ANSI-capable, and this reason block is not, so whether the
    escape renders or prints raw is unknown until an operator crosses a threshold
    and looks. That is an acceptable thing to try and an unacceptable thing to
    depend on.

    So the test strips the escapes rather than trying to suppress them. Setting
    TERM=dumb would prove nothing here: `supports_ansi()` short-circuits to True
    off `os.name != "nt"`, so on this runner the no-colour branch is unreachable
    and a test that pretended to take it would be decoration itself. Stripping is
    the real question anyway - it is what a surface that discards ANSI leaves the
    operator holding.
    """
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    plain = re.sub(r"\x1b\[[0-9;]*m", "", reason)
    assert "- RECOMMENDED. The hook then saves and compacts" in plain, (
        "with the escapes gone the option line no longer reads as one sentence, "
        f"so the colour is load-bearing:\n{plain!r}"
    )


def test_no_escape_leaks_into_a_line_the_colour_was_not_meant_for(env):
    """One word is coloured. An escape anywhere else is a bug that only shows up
    on the surface that prints escapes raw, which is the surface this is an
    experiment about.

    Skipped where colour is correctly off, because zero escapes is the right
    answer there and failing on it would report a bare Windows console as a
    defect.
    """
    if not supports_ansi():
        pytest.skip("colour is off on this terminal, so there is no escape to place")
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    coloured = [ln for ln in reason.splitlines() if "\x1b[" in ln]
    assert len(coloured) == 1, (
        f"expected the escape on the option-2 line alone, found it on: {coloured}"
    )
    assert coloured[0].count("\x1b[") == 2, (
        f"the option-2 line carries an unbalanced set of escapes: {coloured[0]!r}"
    )


def test_inject_uses_the_auto_closing_for_a_flagged_session(env):
    env["env"].pop("CLAUDE_HANDOFF_AUTO", None)
    _auto(env, SESSION_A, "on")
    _compact(env, SESSION_A, "SESSION-A-WORK the long migration")
    out = _inject(env, SESSION_A).stdout
    assert "AUTO MODE" in out, f"a flagged session was not resumed hands-off:\n{out}"


def test_compact_resume_disarms_a_stale_stop_instruction(env):
    """Observed live on 2026-08-19, on the first driven compaction this system
    ever performed. The harness summary closes with a "next step" field, and that
    field describes the END of the turn it just compacted - on that run, "print
    the archive path, then stop and wait for the operator". Both instructions
    then sat in one context: AUTO MODE said continue, the summary said stop. The
    stop won, the session halted with verification still owed, and the operator
    had to ask why twice.

    The carve-out is prose in a hook, which is exactly the kind of paragraph a
    later edit drops without noticing, so it is pinned here rather than trusted.
    """
    env["env"].pop("CLAUDE_HANDOFF_AUTO", None)
    _auto(env, SESSION_A, "on")
    _compact(env, SESSION_A, "SESSION-A-WORK the long migration")
    out = _inject(env, SESSION_A, source="compact").stdout
    assert "AUTO MODE" in out, f"a flagged session was not resumed hands-off:\n{out}"
    assert "stop and wait" in out, (
        "the compaction resume no longer names the stale stop instruction, so a "
        f"summary that ends in one halts the session again:\n{out}"
    )
    assert "already done" in out, (
        f"the resume names the stale step but never says it is spent:\n{out}"
    )


# --------------------------------------------------------------------------
# The driven compaction: CAP-5 and CAP-7
#
# The hook runs as a subprocess here, so `herdr` itself is replaced rather than
# the seam that calls it. That is the stronger test: it exercises the real
# argument vector, the real JSON parsing, and the real failure branches, none of
# which a monkeypatched function would touch.
# --------------------------------------------------------------------------

FAKE_HERDR = '''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
with open(os.environ["FAKE_HERDR_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")

mode = os.environ.get("FAKE_HERDR_MODE", "ok")
if mode == "broken":
    print("this is not json")
    raise SystemExit(1)

if argv[:2] == ["agent", "list"]:
    agents = []
    if mode != "not-hosted":
        agents.append({
            "pane_id": "w1:p1",
            "agent_status": "working",
            "agent_session": {"kind": "id", "value": os.environ["FAKE_HERDR_SESSION"]},
        })
    print(json.dumps({"result": {"agents": agents}}))
else:
    print(json.dumps({"result": {"type": "agent_prompted",
                                 "agent": {"agent_status": "working"}}}))
'''


def _install_fake_herdr(env, session, mode="ok"):
    """Put a fake `herdr` first on PATH and return the call-log path."""
    bindir = env["project"].parent / "fakebin"
    bindir.mkdir(exist_ok=True)
    binary = bindir / "herdr"
    binary.write_text(FAKE_HERDR, encoding="utf-8")
    binary.chmod(0o755)
    log = env["project"].parent / f"herdr-calls-{mode}.log"
    log.write_text("", encoding="utf-8")
    env["env"]["PATH"] = f"{bindir}{os.pathsep}{env['env']['PATH']}"
    env["env"]["FAKE_HERDR_LOG"] = str(log)
    env["env"]["FAKE_HERDR_SESSION"] = session
    env["env"]["FAKE_HERDR_MODE"] = mode
    return log


def _calls(log: Path) -> list:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _set_state(env, session, **updates):
    CP = _cp()
    path = _state_dir(env) / f"checkpoint-{CP.safe_slug(session)}.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(updates)
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def _stop_turn(env, session, *, turn="t-1", active=False, transcript=True):
    """One Stop event, with the payload fields the driven block reads.

    A REAL transcript file by default. `_wait_out_the_grace` treats an absent
    one as "the operator may have spoken, hand the turn back" and returns
    immediately, so pointing at a missing file silently skips the wait these
    tests are timing.
    """
    path = env["project"] / f"{session}.jsonl"
    if transcript and not path.exists():
        path.write_text("", encoding="utf-8")
    return _run(OFFER, env, {
        "session_id": session,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "stop_hook_active": active,
        "prompt_id": turn,
        "transcript_path": str(path),
    })


def _write_handoff(env, session, kind, stamp):
    directory = _archive_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    CP = _cp()
    path = directory / f"{stamp}_handoff_{kind}_{CP.safe_slug(session)}.md"
    path.write_text("body", encoding="utf-8")
    return path


def _arm_unattended_save(env, session):
    """Drive the session to the state that follows a hard-threshold save.

    Returns the state after the save, so a caller can read `last_offer_at`.
    """
    _statusline(env, session, 46)
    _set_state(env, session, session_unattended=True)
    result = _stop_turn(env, session, turn="t-save")
    assert result.stdout.strip(), "the unattended save did not fire (CAP-1)"
    return json.loads(
        (_state_dir(env) / f"checkpoint-{_cp().safe_slug(session)}.json")
        .read_text(encoding="utf-8")
    )


def test_unattended_saves_at_the_hard_threshold(env):
    """CAP-1. The one thing this mode never did before 2026-08-19."""
    state = _arm_unattended_save(env, SESSION_A)
    assert state.get("needs_compact_offer") is False, "the bucket was not consumed"
    assert state.get("last_offer_at"), "the crossing was not stamped"
    assert state.get("unattended_turn_id") == "t-save", (
        "the save did not claim the turn, so the driven block cannot cross the "
        "stop_hook_active guard on the next Stop"
    )


def test_unattended_below_hard_does_not_save(env):
    """The negative half: a bucket below hard still just continues."""
    _statusline(env, SESSION_A, 42)
    _set_state(env, SESSION_A, session_unattended=True)
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "1"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"
    result = _stop_turn(env, SESSION_A, turn="t-soft")
    reason = json.loads(result.stdout)["reason"] if result.stdout.strip() else ""
    assert "Save a checkpoint silently" not in reason, (
        f"a save fired below the hard threshold:\n{reason}"
    )


def test_the_driven_compaction_submits_once_the_handoff_exists(env):
    """CAP-5, the whole point of the change.

    Includes both placement regressions: this Stop carries `stop_hook_active`
    true, and `needs_compact_offer` was already cleared by the save. Against the
    placement the plan originally specified - after the save paths - `main()`
    returns before the block in BOTH modes and nothing is ever submitted.
    """
    log = _install_fake_herdr(env, SESSION_A)
    state = _arm_unattended_save(env, SESSION_A)
    _write_handoff(env, SESSION_A, "auto", "2099-01-01-000000")

    _stop_turn(env, SESSION_A, turn="t-next", active=True)

    submissions = [c for c in _calls(log) if c[:2] == ["agent", "prompt"]]
    assert submissions == [["agent", "prompt", "w1:p1", "/compact"]], (
        f"the driven compaction did not submit exactly once: {submissions}"
    )
    fresh = json.loads(
        (_state_dir(env) / f"checkpoint-{_cp().safe_slug(SESSION_A)}.json")
        .read_text(encoding="utf-8")
    )
    assert fresh.get("compact_requested_at"), "the request was not recorded"
    assert fresh.get("compact_request_count") == 1
    assert fresh.get("compact_requests"), "the correlation list the probe reads is empty"


def test_the_driven_compaction_waits_for_the_handoff(env):
    """Ordering is the point: handoff first, boundary second."""
    log = _install_fake_herdr(env, SESSION_A)
    _arm_unattended_save(env, SESSION_A)
    # No handoff written at all.
    _stop_turn(env, SESSION_A, turn="t-next", active=True)
    assert not [c for c in _calls(log) if c[:2] == ["agent", "prompt"]], (
        "compaction was requested with no handoff on disk"
    )


def test_a_post_compaction_archive_does_not_satisfy_the_ordering(env):
    """The kind filter, at the hook rather than at the probe.

    `checkpoint-save.py` writes `_handoff_compact-*` AFTER every compaction. If
    the hook accepted those, the ordering condition would be permanently true
    after the first compaction of a session.
    """
    log = _install_fake_herdr(env, SESSION_A)
    _arm_unattended_save(env, SESSION_A)
    _write_handoff(env, SESSION_A, "compact-manual", "2099-01-01-000000")
    _stop_turn(env, SESSION_A, turn="t-next", active=True)
    assert not [c for c in _calls(log) if c[:2] == ["agent", "prompt"]], (
        "a post-compaction archive satisfied the handoff-first ordering"
    )


def test_the_driven_compaction_fires_once_per_bucket(env):
    log = _install_fake_herdr(env, SESSION_A)
    _arm_unattended_save(env, SESSION_A)
    _write_handoff(env, SESSION_A, "auto", "2099-01-01-000000")
    _stop_turn(env, SESSION_A, turn="t-next", active=True)
    _stop_turn(env, SESSION_A, turn="t-next-2", active=True)
    submissions = [c for c in _calls(log) if c[:2] == ["agent", "prompt"]]
    assert len(submissions) == 1, (
        f"the repeat guard let the same bucket submit twice: {submissions}"
    )


def test_an_unreachable_herdr_does_not_break_the_turn(env):
    """The net exists for this case; a compaction helper must never be fatal."""
    log = _install_fake_herdr(env, SESSION_A, mode="broken")
    _arm_unattended_save(env, SESSION_A)
    _write_handoff(env, SESSION_A, "auto", "2099-01-01-000000")
    result = _stop_turn(env, SESSION_A, turn="t-next", active=True)
    assert result.returncode == 0, f"the hook failed the turn:\n{result.stderr}"
    fresh = json.loads(
        (_state_dir(env) / f"checkpoint-{_cp().safe_slug(SESSION_A)}.json")
        .read_text(encoding="utf-8")
    )
    assert fresh.get("compact_request_error"), "the failure was not recorded"
    assert "compact_requested_at" not in fresh, (
        "a failed submission was recorded as a request, which would let the "
        "probe correlate a boundary this workspace never caused"
    )


def test_a_session_herdr_does_not_host_is_recorded_as_not_hosted(env):
    """"Not hosted" and "could not tell" are different facts (scope-claims)."""
    _install_fake_herdr(env, SESSION_A, mode="not-hosted")
    _arm_unattended_save(env, SESSION_A)
    _write_handoff(env, SESSION_A, "auto", "2099-01-01-000000")
    _stop_turn(env, SESSION_A, turn="t-next", active=True)
    fresh = json.loads(
        (_state_dir(env) / f"checkpoint-{_cp().safe_slug(SESSION_A)}.json")
        .read_text(encoding="utf-8")
    )
    assert fresh.get("compact_host") == "not-hosted"
    assert not fresh.get("compact_request_error"), (
        "a definite not-hosted was recorded as a lookup failure"
    )


def test_attended_mode_never_drives_a_compaction(env):
    """Open Question 3: an attended operator may be mid-thought."""
    log = _install_fake_herdr(env, SESSION_A)
    _statusline(env, SESSION_A, 46)
    _stop_turn(env, SESSION_A, turn="t-1")
    _write_handoff(env, SESSION_A, "auto", "2099-01-01-000000")
    _stop_turn(env, SESSION_A, turn="t-2", active=True)
    assert not [c for c in _calls(log) if c[:2] == ["agent", "prompt"]], (
        "attended mode drove a compaction"
    )


def test_the_wait_shows_a_countdown_and_always_clears_it(env):
    """CAP-7. A still terminal must be visibly a wait, and never stay one."""
    log = _install_fake_herdr(env, SESSION_A)
    _statusline(env, SESSION_A, 42)
    _set_state(env, SESSION_A, session_unattended=True)
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "6"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"
    _stop_turn(env, SESSION_A, turn="t-wait")

    renames = [c for c in _calls(log) if c[:2] == ["agent", "rename"]]
    assert renames, "the wait showed no countdown at all"
    assert renames[-1] == ["agent", "rename", "w1:p1", "--clear"], (
        f"the countdown was left frozen on screen: {renames[-1]}"
    )
    seconds = [
        int(c[3].split(" - ")[1].split("s")[0])
        for c in renames if c[3] != "--clear"
    ]
    assert seconds == sorted(seconds, reverse=True), (
        f"the countdown did not count down: {seconds}"
    )


def test_a_wait_without_herdr_still_runs_its_full_duration(env):
    """The countdown is decoration. Losing it must not shorten the wait."""
    _install_fake_herdr(env, SESSION_A, mode="not-hosted")
    _statusline(env, SESSION_A, 42)
    _set_state(env, SESSION_A, session_unattended=True)
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_WAIT"] = "3"
    env["env"]["CLAUDE_HANDOFF_UNATTENDED_POLL"] = "1"
    started = time.monotonic()
    result = _stop_turn(env, SESSION_A, turn="t-wait")
    elapsed = time.monotonic() - started
    assert result.returncode == 0
    assert elapsed >= 3, f"the wait was cut short to {elapsed:.1f}s without herdr"
