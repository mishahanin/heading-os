"""A commit and a push went out on the model's own reading of an old approval.

`~/.claude/CLAUDE.md` has said "Commit or push only when the user asks" for
months, and the operator's auto-memory carries the same rule as
`commit-and-push-only-when-asked`, written after the first breach on
2026-08-20. It happened again on 2026-08-30. Two written copies of a sentence
did not hold, and a third would not have either.

The mechanism, which is the part worth pinning:

    The operator authorised ONE push. That authorisation was written into a
    handoff summary as "the operator's approval is already given", survived a
    context compaction, and was read back afterwards as a standing fact about
    the world rather than a spent event. Every later decision cited the
    summary. An approval had been promoted from an event into a state.

At no point was the rule knowingly broken: at the moment of the second push the
model believed permission existed. That is exactly why prose cannot fix it. The
wall in `.claude/hooks/_dispatch.py` re-reads the operator's ACTUAL most recent
words at the instant of the action, so a stale belief cannot outlive one turn.

`last-prompt` records in the session transcript carry `lastPrompt`, the
operator's typed text verbatim. Task notifications, Stop-hook feedback and tool
results are NOT last-prompts, so nothing the harness generates can authorise a
release, and the model does not write the transcript.

Run: python3 -m pytest tests/test_a_release_the_operator_never_asked_for.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "dispatch_release_probe", ROOT / ".claude" / "hooks" / "_dispatch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dispatch_release_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load()


def _transcript(tmp_path: Path, prompts: list[str], noise: bool = True) -> Path:
    """A transcript in the real shape, with the harness's own chatter in it.

    The noise matters: a task notification and Stop-hook feedback both arrive as
    `type: "user"` records, and if the wall read those it could authorise itself
    by finishing a background task.
    """
    p = tmp_path / "session.jsonl"
    lines = []
    for text in prompts:
        if noise:
            lines.append(json.dumps({
                "type": "user",
                "message": {"content": "<task-notification>done</task-notification>"},
            }))
            lines.append(json.dumps({
                "type": "user",
                "message": {"content": "Stop hook feedback: push the thing"},
            }))
        lines.append(json.dumps({"type": "last-prompt", "lastPrompt": text}))
        lines.append(json.dumps({"type": "assistant", "message": {"content": "ok"}}))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _payload(command: str, transcript: Path | str | None) -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": "" if transcript is None else str(transcript),
    }


# ==========================================================================
# release_action: what counts as a release at all
# ==========================================================================

@pytest.mark.parametrize("command,expected", [
    ("git commit -F msg.txt", "commit"),
    ("git commit --amend", "commit"),
    ("git -c core.hooksPath=.git/hooks commit -m x", "commit"),
    ("git tag -a v1 -m x", "commit"),
    ("git push origin main", "push"),
    ("git push --force", "push"),
    ("ls && git push", "push"),
    (".venv/bin/python scripts/push-all.py --no-commit", "push"),
    ("uv run python scripts/publish-corporate.py", "push"),
    ("gh release create v1", "push"),
])
def test_a_release_command_is_recognised(command, expected):
    assert D.release_action(command) == expected


@pytest.mark.parametrize("command", [
    "git status --short",
    "git log --oneline origin/main..HEAD",
    "git diff --name-only origin/main..HEAD",
    'grep -n "git commit" file.py',
    "pytest tests/test_a_commit_that_swept_up_the_bystanders.py",
    "ls -la",
])
def test_ordinary_work_is_not_a_release(command):
    """The negative half, and it is load-bearing in a way that is easy to miss.

    A wall that also blocks `git status` and `grep "git commit"` is a wall the
    next person turns off, and then nothing guards the real thing. Quoted spans
    are stripped before matching for exactly the grep case.
    """
    assert D.release_action(command) is None


def test_the_command_this_workspace_actually_pushes_with_is_caught():
    """The first version of the wall anchored every pattern at a command
    boundary, so `scripts/push-all.py` with a path in front of it matched
    nothing. MEASURED 2026-08-30 before the fix: `release_action` returned None
    for the one command this workspace pushes with. A wall that misses the only
    door is decoration, and it would have looked green in every other case."""
    assert D.release_action(".venv/bin/python scripts/push-all.py --no-commit") == "push"


# ==========================================================================
# prompt_authorises: whose words count
# ==========================================================================

@pytest.mark.parametrize("prompt,action,expected", [
    ("закончи пуш", "push", True),
    ("закончи пуш", "commit", True),
    ("push it", "push", True),
    ("/backup", "push", True),
    ("закоммить это", "commit", True),
    ("закоммить это", "push", False),
    ("чини всё", "push", False),
    ("чини всё", "commit", False),
    ("да, делаем как рекомендуешь", "push", False),
    ("продолжай", "push", False),
    ("", "push", False),
])
def test_only_the_operators_own_words_authorise(prompt, action, expected):
    assert D.prompt_authorises(prompt, action) is expected


@pytest.mark.parametrize("prompt", [
    "не пушь пока",
    "don't push yet",
    "do not commit this",
    "push it but don't commit",
])
def test_a_negation_anywhere_refuses(prompt):
    """Blunt on purpose. "не пушь пока" and "пуш" differ by one token, and a
    wall that has to parse intent gets it wrong in the expensive direction."""
    assert D.prompt_authorises(prompt, "push") is False
    assert D.prompt_authorises(prompt, "commit") is False


# ==========================================================================
# The wall itself
# ==========================================================================

def test_a_push_the_operator_did_not_ask_for_is_refused(tmp_path):
    """THE case. This is the exact shape of the 2026-08-30 breach: the work was
    approved, the release was not, and the model pushed anyway."""
    t = _transcript(tmp_path, ["чини всё, продолжай без остановки"])

    out = D.check_release_gate(
        _payload(".venv/bin/python scripts/push-all.py --no-commit", t))

    assert out is not None, "the wall let an unauthorised push through"
    assert out["decision"] == "block"
    assert "did not ask for a push" in out["reason"]
    assert "чини всё" in out["reason"], (
        "the refusal must quote what the operator actually said, so the model "
        "cannot argue with it from memory")


def test_a_commit_the_operator_did_not_ask_for_is_refused(tmp_path):
    t = _transcript(tmp_path, ["посмотри что не так"])

    out = D.check_release_gate(_payload("git commit -m 'wip'", t))

    assert out is not None and out["decision"] == "block"


def test_the_operators_word_opens_it(tmp_path):
    """The negative control, and it carries the whole file. A wall that refuses
    everything passes all three tests above and makes the workspace unusable,
    which is how walls get deleted."""
    t = _transcript(tmp_path, ["закончи пуш"])

    assert D.check_release_gate(
        _payload(".venv/bin/python scripts/push-all.py", t)) is None
    assert D.check_release_gate(_payload("git commit -F msg.txt", t)) is None


def test_a_spent_authorisation_does_not_survive_the_next_turn(tmp_path):
    """The defect, stated as a test. The operator approved a push HOURS earlier;
    the model carried that approval forward and released on it again.

    Same transcript, the authorising prompt is still in it, but it is no longer
    the last one. That has to be a refusal, or the wall reproduces the bug.
    """
    t = _transcript(tmp_path, ["да, пушь", "теперь почини тесты"])

    out = D.check_release_gate(_payload("git push origin main", t))

    assert out is not None and out["decision"] == "block", (
        "an authorisation from an earlier turn was still being honoured")
    assert "теперь почини тесты" in out["reason"]


def test_the_harness_cannot_authorise_itself(tmp_path):
    """A task notification and Stop-hook feedback both arrive as `user` records
    and both can contain the word "push". Neither is a `last-prompt`.

    Without this, finishing a background job whose report mentions pushing would
    open the gate, which is authorisation by coincidence.
    """
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join([
        json.dumps({"type": "last-prompt", "lastPrompt": "почини тесты"}),
        json.dumps({"type": "user",
                    "message": {"content": "Stop hook feedback: now push it"}}),
        json.dumps({"type": "user",
                    "message": {"content": "<task-notification>push done</task-notification>"}}),
    ]) + "\n", encoding="utf-8")

    out = D.check_release_gate(_payload("git push", p))

    assert out is not None and out["decision"] == "block"


# ==========================================================================
# Fail closed
# ==========================================================================

@pytest.mark.parametrize("transcript", [None, "", "/nonexistent/session.jsonl"])
def test_an_unreadable_transcript_refuses(tmp_path, transcript):
    """A gate that opens when it cannot see is not a gate."""
    out = D.check_release_gate(_payload("git push", transcript))

    assert out is not None and out["decision"] == "block"
    assert "cannot read" in out["reason"]


def test_a_transcript_with_no_last_prompt_refuses(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {"content": "hi"}}) + "\n",
                 encoding="utf-8")

    out = D.check_release_gate(_payload("git push", p))

    assert out is not None and out["decision"] == "block"


def test_a_corrupt_line_does_not_take_the_wall_down(tmp_path):
    """Half a JSON line must not turn a refusal into a crash, and a crashing
    check is advisory-and-continue in this dispatcher, which means the release
    would proceed."""
    p = tmp_path / "session.jsonl"
    p.write_text(
        "{not json at all\n"
        + json.dumps({"type": "last-prompt", "lastPrompt": "закончи пуш"}) + "\n"
        + '{"type": "last-prompt", "lastPro\n',
        encoding="utf-8")

    assert D.check_release_gate(_payload("git push", p)) is None


def test_the_prompt_is_found_past_a_very_long_turn(tmp_path):
    """The reader tails the file first, because the transcript reached 127,337
    lines in the session this wall was written in and it runs inside a
    synchronous hook. A turn longer than the tail window must fall back to the
    whole file rather than reading "no authorisation"."""
    p = tmp_path / "session.jsonl"
    filler = json.dumps({"type": "assistant", "message": {"content": "x" * 512}})
    body = ([json.dumps({"type": "last-prompt", "lastPrompt": "закончи пуш"})]
            + [filler] * 1200)
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    assert p.stat().st_size > (1 << 18), "the fixture is smaller than the tail window"

    assert D.check_release_gate(_payload("git push", p)) is None


# ==========================================================================
# Wiring
# ==========================================================================

def test_the_wall_is_registered_second_behind_the_secret_scanner():
    """An unregistered check is a file nobody calls.

    SECOND, not first, and the placement was argued rather than picked. This
    dispatcher is first-block-wins: whichever check refuses owns the message the
    model sees. `check_prevent_secrets` keeps the first slot because a command
    that both releases AND carries a credential must be refused for the
    credential -- that is the more dangerous of the two facts, and the one whose
    message must not be buried under a permission complaint.

    Everything else runs after: an unauthorised release should not depend on
    eight later checks happening to have no opinion.
    """
    assert D.check_release_gate in D.CHECKS
    assert D.CHECKS[0] is D.check_prevent_secrets
    assert D.CHECKS[1] is D.check_release_gate


def test_a_non_bash_payload_is_none():
    """Write and Read payloads reach every check in this dispatcher."""
    assert D.check_release_gate(
        {"tool_name": "Write", "tool_input": {"file_path": "x.py"}}) is None
