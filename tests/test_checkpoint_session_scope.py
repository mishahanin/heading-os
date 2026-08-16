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
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
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
    assert "auto-compact" not in reason.lower() or "not configured" in reason.lower(), (
        f"the hook asserts a compaction point nothing configured:\n{reason}"
    )


def test_auto_mode_names_the_compaction_point_when_it_is_configured(env):
    env["env"]["CLAUDE_HANDOFF_AUTO"] = "1"
    env["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "50"
    _statusline(env, SESSION_A, 46)
    reason = json.loads(_stop(env, SESSION_A).stdout)["reason"]
    assert "50" in reason, f"the configured compaction point is not surfaced:\n{reason}"
