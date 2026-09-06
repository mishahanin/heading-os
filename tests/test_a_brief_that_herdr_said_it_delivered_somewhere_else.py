#!/usr/bin/env python3
"""`herdr agent prompt` answers with the pane it was GIVEN, not the one that got it.

MEASURED 2026-09-06. A brief addressed to `w59:p1` was answered

    {"result":{"agent":{"pane_id":"w59:p1","agent_status":"working", ...}}}
    Sent to w59:p1.

and landed in a DIFFERENT yard, `presentation-book-4-uz`, where the operator had
live work in flight. In that yard's transcript:

    line 3879  "type":"queue-operation","operation":"enqueue"   21:30:09Z
    line 3886  "type":"user","promptSource":"queued"            21:30:15Z

The addressed workspace, `yard-cognee-research`, had NO transcript at all: its
Claude project directory held only an empty `memory/` subdirectory, so no agent
had ever run there.

**Second occurrence of the same shape.** Register item 19 records 2026-09-05, a
brief for `w56:p1` executing in `w55` while `w56` had zero transcripts. Both
times the addressed pane had no running agent, and both times herdr reported
success naming the addressed pane. One occurrence was a report; two with the
same discriminating feature is a rule.

What the fix asks that herdr's reply cannot answer:

* BEFORE: does the addressed checkout have a session at all? No transcript means
  no agent has ever run there, which is the state both misdeliveries shared.
* AFTER: did the text arrive THERE? Every brief now carries a unique id line,
  and delivery is confirmed by finding that id in the addressed session's own
  transcript, not by reading herdr's exit code.

Neither check trusts herdr. Both read the transcripts the harness writes.

Run: .venv/bin/python -m pytest \\
     tests/test_a_brief_that_herdr_said_it_delivered_somewhere_else.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.agent_stub import agent_in

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/herdr-brief.py"

# `herdr-brief.py` calls `require_main_clone` right after `parse_args`, because
# sending a brief is HELM's job and a yard must not do it. Every test below
# that SPAWNS the script therefore exits 2 with the guard's refusal in any
# worktree, and the whole file went red on a yard's own full-suite run. MEASURED 2026-09-06 in
# `yard-memory-recalibration`: 5 failed, 4 passed, against 9 passed in HELM.
#
# The condition is asked of the SAME guard the script uses, not of a path shape:
# a hand-rolled "am I in .yard/" test would drift from what actually refuses.
# The refusal itself is asserted below rather than merely skipped around, so a
# yard's run still measures something instead of quietly measuring nothing.
from scripts.utils.clone_guard import is_main_clone  # noqa: E402

IN_MAIN_CLONE = is_main_clone(ROOT)
helm_only = pytest.mark.skipif(
    not IN_MAIN_CLONE,
    reason="herdr-brief.py refuses to run outside HELM (scripts/utils/clone_guard.py); "
           "the refusal is asserted by test_a_worktree_cannot_send_a_brief_at_all")


@pytest.fixture(scope="module")
def brief():
    spec = importlib.util.spec_from_file_location("herdr_brief_under_test",
                                                  SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# The slug: a wrong one makes every check below look at the wrong tree
# ============================================================

def test_the_target_directory_comes_from_the_shared_owner(brief):
    """The slug rule has ONE owner and this script is not it.

    The first version of this file spelled the two-replacement mangle again.
    `tests/test_transcript_dir_has_one_owner.py` caught it IN THE PUSH GATE on
    2026-09-06, which is obligation 3 of the development standards working as
    designed: a fix that lands in one of N copies is this repository's dominant
    defect shape, and the copy never reached anyone.
    """
    from scripts.utils.checkpoint_paths import transcript_dir

    assert brief.target_dir(ROOT, None) == transcript_dir(ROOT), (
        "herdr-brief no longer resolves the transcript directory through "
        "checkpoint_paths.transcript_dir, so the rule now has two homes")


def test_a_fixture_root_relocates_only_the_root(brief, tmp_path):
    """`--projects-root` must not become a second slug rule either.

    The directory NAME still comes from the owner; only the parent moves. A
    test that could choose its own name would pass against a mangle the real
    run never performs.
    """
    from scripts.utils.checkpoint_paths import transcript_dir

    relocated = brief.target_dir(ROOT, tmp_path)
    assert relocated.parent == tmp_path
    assert relocated.name == transcript_dir(ROOT).name


def test_the_real_checkout_resolves_to_a_real_directory(brief):
    """The floor: the owner's rule still matches what the harness writes."""
    resolved = brief.target_dir(ROOT, None)
    if resolved is None:
        pytest.skip("transcript_dir declines off POSIX")
    if not resolved.parent.is_dir():
        pytest.skip("no Claude projects directory on this machine")
    assert resolved.is_dir(), (
        f"the engine checkout's own transcripts are not under {resolved}; the "
        f"slug rule has drifted from what the harness writes")


# ============================================================
# THE PRE-FLIGHT: a pane with no session is refused, not sent to
# ============================================================

def _fake_herdr(tmp_path: Path, checkout: Path, record: Path) -> Path:
    """A `herdr` on PATH that answers `workspace list` and records `prompt`."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    payload = json.dumps({"result": {"workspaces": [
        {"workspace_id": "w99",
         "worktree": {"checkout_path": str(checkout)}},
    ]}})
    script = bindir / "herdr"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "workspace" ]; then\n'
        f"  cat <<'JSON'\n{payload}\nJSON\n"
        "  exit 0\n"
        "fi\n"
        f'printf "%s" "$4" > "{record}"\n'
        'echo "Sent."\n',
        encoding="utf-8")
    script.chmod(0o755)
    return bindir


def _run(bindir: Path, projects: Path, text: str = "BODY"):
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "w99:p1", text,
         "--projects-root", str(projects)],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=180)


@helm_only
def test_a_pane_whose_session_never_started_is_refused(tmp_path):
    """The direction that would have stopped both misdeliveries."""
    checkout = tmp_path / "yard-empty"
    checkout.mkdir()
    projects = tmp_path / "projects"
    # The project directory exists but holds no transcript, exactly the state
    # `yard-cognee-research` was in: only an empty `memory/` subdirectory.
    target = projects / str(checkout).replace("/", "-").replace(".", "-")
    (target / "memory").mkdir(parents=True)
    record = tmp_path / "delivered.txt"

    result = _run(_fake_herdr(tmp_path, checkout, record), projects)

    assert result.returncode == 5, (
        f"a pane with no session was not refused; that is the state both "
        f"misdeliveries shared.\nstdout={result.stdout}\nstderr={result.stderr}")
    assert not record.exists(), (
        "the brief was handed to herdr anyway, so the refusal is a message "
        "rather than a guard")
    # No process is running in this fixture checkout, so the DIRECT check is
    # what refuses. Before 2026-09-06 the absent transcript did, and the wording
    # was "No agent has ever run" -- a claim about history that the method never
    # established. The present tense is the one the evidence supports.
    assert "No Claude agent is running" in result.stderr


# ============================================================
# THE VERIFICATION: arrival is read from the addressed transcript
# ============================================================

def _prepared(tmp_path: Path):
    checkout = tmp_path / "yard-live"
    checkout.mkdir()
    projects = tmp_path / "projects"
    target = projects / str(checkout).replace("/", "-").replace(".", "-")
    target.mkdir(parents=True)
    (target / "session.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    return checkout, projects, target


@helm_only
def test_delivery_that_never_arrives_fails_loudly(tmp_path):
    """herdr says Sent, the transcript never grows, the script must not agree."""
    checkout, projects, _ = _prepared(tmp_path)
    record = tmp_path / "delivered.txt"

    with agent_in(checkout, tmp_path):
        result = _run(_fake_herdr(tmp_path, checkout, record), projects)

    assert record.exists(), "the brief was never handed to herdr at all"
    assert result.returncode == 4, (
        f"an unverified delivery exited {result.returncode}; herdr's success "
        f"line was taken at face value.\nstderr={result.stderr}")
    assert "DELIVERY NOT VERIFIED" in result.stderr


@helm_only
def test_delivery_that_lands_in_another_yard_names_that_yard(tmp_path):
    """The half that turned a silent misdelivery into a named one.

    The fake herdr writes the payload into a DIFFERENT project directory, which
    is what actually happened on 2026-09-06.
    """
    checkout, projects, _ = _prepared(tmp_path)
    stray = projects / "-some-other-yard"
    stray.mkdir()
    bindir = _fake_herdr(tmp_path, checkout, stray / "session.jsonl")

    with agent_in(checkout, tmp_path):
        result = _run(bindir, projects)

    assert result.returncode == 4
    assert "-some-other-yard" in result.stderr, (
        f"the misdelivery was not named, so the operator learns nothing about "
        f"which session is now holding a brief for another tree.\n"
        f"stderr={result.stderr}")


@helm_only
def test_a_delivery_that_does_arrive_succeeds(tmp_path):
    """The other direction. A guard that refuses everything is not a guard."""
    checkout, projects, target = _prepared(tmp_path)
    bindir = _fake_herdr(tmp_path, checkout, target / "session.jsonl")

    with agent_in(checkout, tmp_path):
        result = _run(bindir, projects)

    assert result.returncode == 0, (
        f"a brief that reached the addressed session was reported as a "
        f"failure.\nstdout={result.stdout}\nstderr={result.stderr}")
    assert "Delivery verified" in result.stdout


# ============================================================
# The id must be unique per send, or verification proves nothing
# ============================================================

@helm_only
def test_each_brief_carries_a_fresh_id(tmp_path, brief):
    """Two sends of the SAME text must not share an id.

    A constant id would be found in the target's transcript from the PREVIOUS
    brief and every send after the first would verify against history.
    """
    checkout, projects, target = _prepared(tmp_path)
    seen = set()
    for _ in range(2):
        record = target / "session.jsonl"
        with agent_in(checkout, tmp_path):
            _run(_fake_herdr(tmp_path, checkout, record), projects, "SAME TEXT")
        body = record.read_text(encoding="utf-8")
        line = [l for l in body.splitlines()
                if l.startswith(brief.BRIEF_ID_PREFIX)]
        assert line, f"no id line in the delivered payload: {body[:200]!r}"
        seen.add(line[0])
    assert len(seen) == 2, (
        f"both sends carried the same id {seen}; verification would then pass "
        f"on a transcript that only holds the earlier brief")


@pytest.mark.skipif(IN_MAIN_CLONE, reason="this checkout IS the main clone")
def test_a_worktree_cannot_send_a_brief_at_all(tmp_path):
    """The other half of the skip above: in a yard the script must REFUSE.

    Without this, a yard's run of this file would assert nothing at all, and a
    file that measures nothing is indistinguishable from a file that passes.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "w99:p1", "BODY",
         "--projects-root", str(tmp_path)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)

    assert result.returncode == 2, (
        f"a YARD was allowed past the clone guard.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}")
    assert "HELM" in result.stderr


def test_there_is_no_flag_to_skip_the_delivery_checks(brief):
    """A guard with an off switch is a default, not a guard."""
    source = SCRIPT.read_text(encoding="utf-8")
    for escape in ("--no-verify", "--skip-verify", "--force"):
        assert escape not in source, (
            f"{escape} appeared in herdr-brief.py; the two checks exist "
            f"because herdr's own reply cannot establish delivery, and a flag "
            f"that turns them off restores the defect on the day someone is "
            f"in a hurry")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
