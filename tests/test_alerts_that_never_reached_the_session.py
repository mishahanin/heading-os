"""Shard hooks-h2 + h3 + h4: the session-boundary hooks, and the alerts,
handoffs and counters that did not survive them.

* ``session-start.py`` wrote its alerts as a JSON object onto the same stdout
  that already carries a plain-text setup banner. Whichever way the harness reads
  that stream the pair is wrong, and the whole alert pipeline exited 0 reporting
  success. It also had no else branch on a failed crm-health run and one unlogged
  handler around the data-root resolve, so two alarms failed toward silence.

* ``checkpoint-precompact.py`` bounded its output by CHARACTER and then told the
  summariser the tree carries "the plan file named above" - when the cut was what
  removed it. Measured on this repository: 4001 characters, handoff pointer and
  plan gone, the written-files list ending on the fragment ``.claude/ski``, all
  under an instruction reading "Preserve the following VERBATIM".

* ``checkpoint-save.py`` called ``.strip()`` on ``compact_summary`` outside every
  try block, so a non-string field lost the handoff with no systemMessage; its
  module docstring argued the shared pointer pair was "left unlocked
  deliberately" while the code wrote it under a lock; and its quarantine comment
  claimed the SessionStart inject still points the next session at the alarm,
  through a directory that inject never reads.

* ``checkpoint-inject.py`` asserted "A handoff saved by this session" over a slug
  that collapses every id-less session into one shared bucket.

* ``checkpoint-offer.py``'s ``_queue_pending`` excluded its own ``/compact``
  enqueue but not the contentless ``dequeue`` that consumes it. Measured against
  this session's transcript: ``enqueue`` (327) and ``remove`` (258) always carry
  ``content``, ``dequeue`` (68) never does. Its unattended auto-save branch also
  claimed the operator's turn before the window reset that keys on the same id.

* ``checkpoint-statusline.py`` promised a wrong-shaped field never produces a
  blank bar; a non-string cwd raised TypeError and printed nothing.

* ``unattended-resume.py`` kept a local copy of the compact literal and swallowed
  a failed clear of the pause window with no record.

Run: python3 -m pytest tests/test_alerts_that_never_reached_the_session.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOKS = ROOT / ".claude" / "hooks"
PY = shutil.which("python3") or sys.executable


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(hook: str, payload: dict, cwd: Path | None = None,
         data_root: Path | None = None):
    """Run a hook in a child process.

    `data_root` redirects HEADING_OS_DATA, and any hook that WRITES needs it.
    `cwd` alone does not: `checkpoint-save.py` resolves its archive through
    `get_data_root()`, which reads the environment, so a child launched with
    `cwd=tmp_path` and no env override wrote a real handoff into the operator's
    live overlay every time this file ran. Measured 2026-08-27: 114 archives
    named `..._handoff_compact-manual_probe-session.md` had accumulated in
    `outputs/operations/handoff-archive/`, and the shared `.latest/summary.md`
    and `.latest/prompt.md` - the pair `/next` reads as "the newest handoff in
    this workspace" - were pointing at one of them.
    """
    env = dict(os.environ)
    if data_root is not None:
        env["HEADING_OS_DATA"] = str(data_root)
    return subprocess.run(
        [PY, str(HOOKS / hook)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=200, check=False,
        env=env, cwd=str(cwd) if cwd else None)


# ============================================================
# The alerts that were written in a shape nothing reads
# ============================================================

def test_session_alerts_are_plain_text_not_a_json_blob():
    """SessionStart injects stdout; a JSON object arrives as a literal blob.

    And this hook already prints a plain-text setup banner onto the same
    stream, so a single JSON document could not be parsed either way.
    """
    proc = _run("session-start.py", {"cwd": str(ROOT)})
    assert proc.returncode == 0
    out = proc.stdout.strip()
    if not out:
        pytest.skip("this workspace currently raises no session alerts")
    assert out.startswith("Session alerts:")
    with pytest.raises(ValueError):
        json.loads(out)


def test_no_json_object_is_written_to_the_alert_stream():
    source = (HOOKS / "session-start.py").read_text(encoding="utf-8")
    live = [ln for ln in source.splitlines()
            if '"additionalContext"' in ln and not ln.lstrip().startswith("#")]
    assert live == []


def test_a_failed_crm_health_run_becomes_an_alert(tmp_path, monkeypatch):
    """Silence read as "nothing is overdue" while the red debt grew."""
    hook = _load("session_start_crm_probe", ".claude/hooks/session-start.py")

    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "Traceback ...\nValueError: malformed frontmatter"

    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: _Proc())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "crm-health.py").write_text("", encoding="utf-8")

    alerts = hook.check_crm_health(str(tmp_path))
    assert alerts is not None
    assert any("DID NOT RUN" in a for a in alerts)
    assert any("UNKNOWN, not zero" in a for a in alerts)


def test_a_missing_context_directory_becomes_an_alert(tmp_path, monkeypatch):
    """"nothing to check" and "could not look" are different answers.

    And the answer has to be in the SHAPE the caller unpacks. This test used to
    assert `any("NOT CHECKED" in a for a in alerts)` over a list the function
    filled with a bare string, which read as true and was: the function's own
    docstring promises `(filename, days_old, severity)` tuples, `main()`
    unpacks three values per item, and a string of length 61 unpacks
    character by character. Every session on a workspace with no `context/`
    directory - every fresh public clone - died with
    `ValueError: too many values to unpack (expected 3)` at SessionStart.

    A unit test of a function that never checks the contract with its one
    caller is how that shipped. `test_the_hook_survives_a_missing_context_tree`
    below is that missing half.
    """
    hook = _load("session_start_stale_probe", ".claude/hooks/session-start.py")
    monkeypatch.setattr("scripts.utils.workspace.get_data_root",
                        lambda: tmp_path / "no-such-overlay")
    alerts = hook.check_stale_files(str(tmp_path), {"type": "ceo"})
    assert len(alerts) == 1, alerts
    entry = alerts[0]
    assert isinstance(entry, tuple) and len(entry) == 3, (
        f"check_stale_files returned {entry!r}, which main() cannot unpack"
    )
    name, days, severity = entry
    assert severity == "NOT_CHECKED"
    assert days == 0
    assert "no context directory" in name


def test_the_hook_survives_a_missing_context_tree(tmp_path):
    """The contract, end to end, in the child process the harness runs.

    The unit test above can be satisfied by any three-element tuple. This one
    runs the real hook against an overlay with no `context/` and refuses a
    traceback, which is the thing a first-time user actually sees.
    """
    overlay = tmp_path / "data"
    (overlay / "outputs").mkdir(parents=True)
    proc = _run("session-start.py", {"cwd": str(tmp_path)}, data_root=overlay)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "no context directory" in proc.stderr, (
        "the hook checked nothing and said nothing about it"
    )
    # And on STDOUT, which is the stream SessionStart injects into the session.
    # stderr goes to the journal; the operator reads stdout. Asserting only the
    # stderr line let a mutation that deletes the alert survive, because the
    # two messages come from different places.
    assert "CONTEXT STALENESS NOT CHECKED" in proc.stdout, (
        f"the operator is told nothing in-session:\n{proc.stdout!r}"
    )


def test_a_data_root_failure_is_reported(tmp_path, monkeypatch, capsys):
    hook = _load("session_start_root_probe", ".claude/hooks/session-start.py")

    def _boom():
        raise RuntimeError("overlay has moved")

    monkeypatch.setattr("scripts.utils.workspace.get_data_root", _boom)
    hook.check_stale_files(str(tmp_path), {"type": "ceo"})
    assert "data-root resolve failed" in capsys.readouterr().err


# ============================================================
# The status line that went blank on a wrong-shaped field
# ============================================================

@pytest.mark.parametrize("payload", [
    {"cwd": 5},
    {"workspace": {"current_dir": ["/srv/elsewhere"]}},
    {"cwd": None, "workspace": {"current_dir": 7}},
])
def test_a_wrong_shaped_directory_still_renders_a_bar(payload):
    """A blank bar is what a dead hook looks like; the docstring forbids it."""
    proc = _run("checkpoint-statusline.py", payload)
    assert proc.returncode == 0
    assert proc.stdout.strip() != ""
    assert "Traceback" not in proc.stderr


# ============================================================
# The resume hook that kept its own copy of the literal
# ============================================================

def test_the_compact_literal_comes_from_its_one_owner():
    from scripts.utils.herdr_agent import COMPACT_COMMAND as owner
    hook = _load("unattended_resume_probe", ".claude/hooks/unattended-resume.py")
    assert owner == hook.COMPACT_COMMAND


def test_a_failed_clear_of_the_pause_window_is_recorded(monkeypatch, capsys):
    hook = _load("unattended_resume_fail", ".claude/hooks/unattended-resume.py")

    def _boom(_payload):
        raise OSError("read-only file system")

    monkeypatch.setattr(hook, "main", hook.main)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
        json.dumps({"prompt": "hello", "cwd": str(ROOT)})))
    monkeypatch.setattr("scripts.utils.checkpoint_paths.project_root", _boom)
    assert hook.main() == 0
    assert "could not clear the pause window" in capsys.readouterr().err


def test_the_docstring_no_longer_claims_total_silence():
    hook = _load("unattended_resume_doc", ".claude/hooks/unattended-resume.py")
    doc = " ".join(hook.__doc__.split())
    assert "silent on every path including failure" not in doc
    assert "never writes to STDOUT and never blocks" in doc


# ============================================================
# The keep-set that cut out the two facts it then referred to
# ============================================================

@pytest.fixture(scope="module")
def precompact():
    return _load("precompact_under_test", ".claude/hooks/checkpoint-precompact.py")


def _facts(handoff: str = "pointer line", plan: str = "plan line",
           filler: int = 400) -> dict:
    return {
        "branch": "main",
        "status": "\n".join(f" M scripts/file_{i}.py" for i in range(filler)),
        "log": "\n".join(f"abc{i} a commit" for i in range(20)),
        "written": "\n".join(f".claude/skills/skill_{i}/SKILL.md" for i in range(60)),
        "handoff": handoff,
        "plan": plan,
    }


@pytest.mark.parametrize("filler", [0, 40, 120, 400, 900])
def test_the_two_facts_a_session_cannot_re_derive_survive_the_bound(precompact, filler):
    """At every size, not just one.

    A single size can sit in the range where the note's headroom does not
    matter. Without that headroom the assembled body fills the bound exactly,
    the note no longer fits, and the whole keep-set falls back to the fixed
    block - losing every fact rather than the recoverable ones.
    """
    out = precompact.render(_facts(filler=filler))
    assert len(out) <= precompact.MAX_OUTPUT
    assert "pointer line" in out, "the handoff pointer was dropped"
    assert "plan line" in out, "the plan was dropped"


def test_the_note_names_exactly_what_it_dropped(precompact):
    out = precompact.render(_facts())
    assert "Omitted whole:" in out
    assert "Uncommitted changes" in out.split("Omitted whole:")[1]


def test_nothing_is_cut_mid_string(precompact):
    """`.claude/ski` was a real fragment of a real path, shipped verbatim.

    The corpus has to SURVIVE for this to measure anything. With the default
    `_facts()` it does not: 60 written paths are about 2100 characters, the
    fixed instruction block leaves under 2000 of the 4000 budget, and
    `DROP_ORDER` puts `written` second, so the whole block is omitted at every
    filler size. Measured 2026-08-27 at fillers 0, 5, 40, 120 and 400: zero
    lines starting `.claude/` in the body, so the loop body never ran and the
    only live assertion was `note.endswith("]")`.

    Three paths fit, which is enough to show a path is either whole or gone.
    """
    facts = _facts(filler=0)
    facts["written"] = "\n".join(
        f".claude/skills/skill_{i}/SKILL.md" for i in range(3))
    out = precompact.render(facts)
    body, _, note = out.partition("\n\n[Cut to fit")

    kept = [line for line in body.splitlines()
            if line.startswith(".claude/skills/skill_")]
    assert len(kept) == 3, (
        f"the written block did not survive, so nothing was checked for "
        f"mid-string truncation. Body was:\n{body[-600:]}"
    )
    for line in kept:
        assert line.endswith("/SKILL.md"), f"truncated path: {line!r}"


def test_a_dropped_block_is_named_whole_and_never_half_written(precompact):
    """The other half of the same rule: when the block does NOT fit, it goes
    entirely, and the note says which fact went. A partial write is what
    shipped `.claude/ski`."""
    out = precompact.render(_facts())          # 60 paths: the block is dropped
    body, _, note = out.partition("\n\n[Cut to fit")
    assert not [line for line in body.splitlines()
                if line.startswith(".claude/skills/skill_")], (
        "a dropped block left fragments behind"
    )
    assert "Files this session wrote" in note, note
    assert note.endswith("]")


@pytest.mark.parametrize("plan_chars", list(range(2200, 2420, 20)))
def test_the_note_always_fits_beside_what_it_describes(precompact, plan_chars):
    """Swept, because the failure lives in a window about 80 characters wide.

    Without headroom reserved for the note, the drop loop stops the moment the
    body is under the bound - and the note then does not fit beside it, so the
    whole keep-set falls back to the fixed block and EVERY fact is lost to make
    room for a sentence describing the loss.

    The window was located rather than guessed: `KEEP_SET` is 1374 characters,
    so a body of `KEEP_SET` + header + handoff + a plan of N characters crosses
    the plain bound at N around 2354 and the reserved bound at around 2034. The
    measured sizes where the two disagree are 2280 to 2340; the sweep brackets
    them. A first attempt swept 3200-4200 and proved nothing, because a plan
    that large is simply dropped and the body collapses far below either bound.
    """
    facts = {"status": "s" * 50, "handoff": "pointer line", "plan": "p" * plan_chars}
    out = precompact.render(facts)
    assert len(out) <= precompact.MAX_OUTPUT
    assert out != precompact.KEEP_SET, (
        "every fact was dropped; the note has to fit beside what survives"
    )


def test_an_output_that_fits_carries_no_note(precompact):
    out = precompact.render({"branch": "main", "handoff": "pointer line"})
    assert "Cut to fit" not in out
    assert "pointer line" in out


def test_a_dropped_block_is_still_redacted(precompact, monkeypatch):
    """Re-assembling after the bound must not reach around the redactor."""
    seen = []

    def _redact(text):
        seen.append(text)
        return text.replace("SECRET-VALUE", "[REDACTED]")

    monkeypatch.setattr("scripts.utils.secret_patterns.redact", _redact)
    facts = _facts(handoff="pointer SECRET-VALUE here")
    out = precompact.render(facts)
    assert "SECRET-VALUE" not in out
    assert "[REDACTED]" in out
    assert seen, "the redactor was never called"


def test_a_redactor_that_cannot_load_costs_the_facts_not_the_block(precompact,
                                                                   monkeypatch):
    def _boom(_text):
        raise RuntimeError("module broken mid-edit")

    monkeypatch.setattr("scripts.utils.secret_patterns.redact", _boom)
    out = precompact.render(_facts())
    assert out == precompact.KEEP_SET
    assert "pointer line" not in out


def test_the_drop_order_puts_the_recoverable_facts_first(precompact):
    order = precompact.DROP_ORDER
    assert order.index("status") < order.index("plan")
    assert order.index("log") < order.index("handoff")
    assert set(order) == {key for key, _label in precompact.FACT_LABELS}


# ============================================================
# The handoff that a wrong-shaped field threw away
# ============================================================

def _save_payload(tmp_path: Path, **over) -> dict:
    payload = {"session_id": "probe-session", "trigger": "manual",
               "cwd": str(tmp_path), "compact_summary": "a summary"}
    payload.update(over)
    return payload


def test_a_non_string_summary_still_saves_the_handoff(tmp_path):
    """It exited 1 before anything was written: no archive, no pointer, nothing."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / "CLAUDE.md").write_text("# probe\n", encoding="utf-8")
    overlay = tmp_path / "data"
    overlay.mkdir()
    proc = _run("checkpoint-save.py",
                _save_payload(tmp_path, compact_summary={"a": 1}),
                cwd=tmp_path, data_root=overlay)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "not a string" in proc.stderr
    assert "Saved handoff" in proc.stdout

    # And it landed in the SCRATCH overlay, not the operator's. Without this
    # the test passes just as well while writing into the live tree, which is
    # exactly what it did until 2026-08-27.
    written = sorted((overlay / "outputs" / "operations" / "handoff-archive")
                     .glob("*probe-session*.md"))
    assert len(written) == 1, (
        f"expected one archive under {overlay}, found {written}. If this is "
        "empty the hook wrote somewhere else, and 'somewhere else' is the "
        "operator's own handoff archive."
    )


def test_the_docstring_states_that_the_pair_is_locked():
    hook = _load("checkpoint_save_doc", ".claude/hooks/checkpoint-save.py")
    doc = " ".join(hook.__doc__.split())
    # The FIRST mention of "unlocked" must be the correction. Anchoring on the
    # quoted form alone let a fresh unquoted claim slip in ahead of it.
    assert doc.index("The pair IS written under one lock") < doc.index("unlocked")


def test_the_quarantine_comment_no_longer_promises_an_inject():
    source = (HOOKS / "checkpoint-save.py").read_text(encoding="utf-8")
    assert "The SessionStart inject does NOT reach the next session" in source
    assert "`.latest/` is not" in source


# ============================================================
# The authorship claim over a shared bucket
# ============================================================

def test_a_session_with_an_id_still_claims_its_own_handoff(monkeypatch):
    from scripts.utils import checkpoint_paths as CP
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert CP.session_id_is_known({"session_id": "abc"}) is True
    assert CP.session_id({"session_id": "abc"}) == "abc"


def test_an_id_less_session_is_not_credited_with_the_handoff(monkeypatch):
    from scripts.utils import checkpoint_paths as CP
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert CP.session_id_is_known({}) is False
    assert CP.session_id({}) == CP.FALLBACK_SESSION_ID


def test_the_inject_wording_depends_on_whether_the_id_is_known():
    source = (HOOKS / "checkpoint-inject.py").read_text(encoding="utf-8")
    assert "session_id_is_known(payload)" in source
    # The wording is built across two f-string lines, so match a contiguous
    # fragment rather than the rendered sentence.
    assert "belong to a DIFFERENT session" in source
    assert "shared pointer slug" in source


# ============================================================
# The queue counter that went negative and stayed deaf
# ============================================================

@pytest.fixture(scope="module")
def offer():
    return _load("checkpoint_offer_under_test", ".claude/hooks/checkpoint-offer.py")


def _transcript(tmp_path: Path, records: list[dict], session: str = "s1") -> Path:
    path = tmp_path / "transcript.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps({"type": "queue-operation", "sessionId": session,
                                 **record}) + "\n")
    return path


@pytest.mark.parametrize("records,expected,why", [
    ([{"operation": "enqueue", "content": "COMPACT"}, {"operation": "dequeue"},
      {"operation": "enqueue", "content": "stop, look at this"}],
     True, "a real message after one driven compaction"),
    ([{"operation": "enqueue", "content": "COMPACT"}, {"operation": "dequeue"}],
     False, "a driven compaction on its own"),
    ([{"operation": "enqueue", "content": "COMPACT"}, {"operation": "dequeue"},
      {"operation": "enqueue", "content": "COMPACT"}, {"operation": "dequeue"},
      {"operation": "enqueue", "content": "hey"}],
     True, "a real message after two driven compactions"),
    ([{"operation": "enqueue", "content": "hi"},
      {"operation": "remove", "content": "hi"}],
     False, "an ordinary message and its removal"),
    ([{"operation": "dequeue"}, {"operation": "enqueue", "content": "hey"}],
     True, "an unmatched decrement must not deafen the count"),
    ([{"operation": "enqueue", "content": "a"}, {"operation": "enqueue", "content": "b"},
      {"operation": "popAll", "content": ""}],
     False, "popAll clears the queue"),
    # The interleaving the floor alone cannot save: the operator speaks BETWEEN
    # our own submission and the record that consumes it. Charging that
    # contentless dequeue to him takes his message back off the count, and the
    # floor never sees a negative number to clamp.
    ([{"operation": "enqueue", "content": "COMPACT"},
      {"operation": "enqueue", "content": "stop, look at this"},
      {"operation": "dequeue"}],
     True, "a real message arriving before our own submission is consumed"),
    # popAll clears the debt with the queue. Carrying it past a flush lets the
    # NEXT ordinary dequeue be cancelled, so a consumed operator message keeps
    # counting as pending.
    ([{"operation": "enqueue", "content": "COMPACT"},
      {"operation": "popAll", "content": ""},
      {"operation": "enqueue", "content": "a"},
      {"operation": "dequeue"}],
     False, "a debt from before a flush must not cancel a later dequeue"),
])
def test_the_pending_count_tracks_the_operator_not_our_own_submission(
        offer, tmp_path, records, expected, why):
    resolved = [
        {**r, "content": offer.HA.COMPACT_COMMAND} if r.get("content") == "COMPACT" else r
        for r in records
    ]
    path = _transcript(tmp_path, resolved)
    assert offer._queue_pending(path, "s1") is expected, why


def test_an_unreadable_stop_payload_is_recorded(offer, monkeypatch, capsys):
    """In unattended mode the stretch stopped with nothing recording why."""
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("{not json"))
    assert offer.main() == 0
    assert "unreadable Stop payload" in capsys.readouterr().err


def test_the_window_reset_is_shared_by_both_call_sites(offer):
    """A closure inside one caller is what let the other disarm it."""
    assert callable(offer._new_unattended_window)
    source = (HOOKS / "checkpoint-offer.py").read_text(encoding="utf-8")
    assert source.count("_mutate=_new_unattended_window") == 2
    body = source[source.index("def main("):]
    assert body.index("_new_unattended_window") < body.index("unattended_turn_id=turn"), (
        "the turn is claimed before the window is reset"
    )


def test_an_unconsumed_done_marker_survives_a_new_window(offer):
    fresh = {"unattended_done_at": "2026-08-25T00:00:00+00:00",
             "unattended_done_note": "finished the plan",
             "unattended_continuations": 40}
    out = offer._new_unattended_window(dict(fresh))
    assert out["unattended_done_at"] == fresh["unattended_done_at"]
    assert out["unattended_done_note"] == "finished the plan"
    assert not out.get("unattended_continuations")


def test_a_consumed_done_marker_is_retired(offer):
    fresh = {"unattended_done_at": "2026-08-25T00:00:00+00:00",
             "unattended_paused_at": "2026-08-25T00:01:00+00:00",
             "unattended_continuations": 40}
    out = offer._new_unattended_window(dict(fresh))
    assert not out.get("unattended_done_at")
