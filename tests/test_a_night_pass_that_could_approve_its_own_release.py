"""`scripts/night-repair.py` -- work at night, acceptance in the morning.

The operator asked for findings to be repaired overnight rather than filed. That
puts a Claude session in this workspace with nobody watching, which is one bad
sentence away from the worst defect this engine has ever had: on 2026-08-31 a
release went out on an approval nobody had given, because a permission was
REMEMBERED rather than READ.

A machine-written prompt is the same failure with better handwriting. If the
night prompt contained "commit" or "push", `check_release_gate` would read it as
the operator's own typed words and let a program grant itself permission.

So the properties this file holds are not style:

* the prompt carries no authorising word, checked against the gate's OWN lists
  imported from the hook, so a word added there tomorrow is checked here
  tonight;
* the batch is consumed before the session starts, so a crash or a second timer
  fire cannot repeat a half-done pass;
* nothing approves itself: `--run` refuses without an operator-approved batch;
* the night never records `fixed`, because an agent that repairs and then
  certifies its own repair is marking its own homework.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


night = _load("night_repair", "scripts/night-repair.py")
dsp = _load("dispatch_for_night_test", ".claude/hooks/_dispatch.py")


FINDINGS = [
    ("scripts/a.py", {"summary": "a guard that reads nothing", "severity": "high",
                      "estimate_minutes": 40}),
    ("scripts/b.py", {"summary": "a test green over an empty corpus",
                      "severity": "medium", "estimate_minutes": 30}),
    ("scripts/c.py", {"summary": "a stale docstring", "severity": "low",
                      "estimate_minutes": 20}),
]


# ============================================================
# The prompt cannot authorise a release
# ============================================================

def test_the_prompt_carries_no_word_that_authorises_a_release():
    """Against the gate's OWN lists, imported, never copied.

    A copy would drift the moment somebody adds a word to the hook, and this
    test would then pass over a prompt the gate accepts. `prompt_authorises` is
    the same function the wall calls, so the question asked here is the question
    asked at runtime.
    """
    prompt = night.build_prompt(night.build_batch(FINDINGS, None))
    for action in ("commit", "push"):
        assert dsp.prompt_authorises(prompt, action) is False, (
            f"the night prompt authorises a {action}; a machine-written "
            f"permission is not the operator's permission")


def test_the_gate_would_accept_a_prompt_that_did_carry_one():
    """The anchor. Without it, a `prompt_authorises` that returned False for
    everything would satisfy the test above while the real gate was disarmed."""
    assert dsp.prompt_authorises("please push this", "push") is True


def _strings_in(rel: str, function: str) -> list[str]:
    """Every string literal inside one function, its own docstring excluded.

    Scoped to a function, and the scope is the finding. Two earlier versions of
    the test below were wrong in opposite directions. Scanning the raw source
    went red on the module docstring, which exists to explain why no authorising
    word may appear -- a rule punishing a file for documenting its own trap.
    Scanning every code string went red on the line `--approve` prints to the
    operator, "The night run will not commit or push", which is a true sentence
    they need to read; a rule that forbids saying what the tool will not do buys
    nothing and costs the operator the one message that matters.

    What actually matters is narrower and exact: no authorising word inside the
    function whose return value BECOMES the prompt.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function):
            continue
        body = node.body
        skip = set()
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            skip.add(id(body[0].value))
        return [inner.value for inner in ast.walk(node)
                if isinstance(inner, ast.Constant)
                and isinstance(inner.value, str) and id(inner) not in skip]
    raise AssertionError(f"{function} not found in {rel}; the scan read nothing")


def test_no_authorising_word_appears_in_the_prompt_builder():
    """`build_prompt` is where a word would reach the gate. A literal there is
    one edit away from being the sentence that grants a program the operator's
    permission."""
    hits = sorted({word
                   for literal in _strings_in("scripts/night-repair.py", "build_prompt")
                   for word in dsp._PUSH_WORDS + dsp._COMMIT_WORDS
                   if word in literal.lower()})
    assert hits == [], f"authorising words inside build_prompt: {hits}"


def test_the_scan_sees_the_strings_it_claims_to_read():
    """A scan that returned nothing would pass the test above over any module.
    `build_prompt` carries many literals; reading fewer than several means the
    extractor is broken, not that the function is clean."""
    literals = _strings_in("scripts/night-repair.py", "build_prompt")
    assert len(literals) >= 8, f"read only {len(literals)} literals"
    assert any("Unattended repair pass" in literal for literal in literals)


def test_the_scan_refuses_a_function_that_is_not_there():
    """Otherwise a renamed `build_prompt` would leave the guard reading an empty
    list and reporting clean."""
    with pytest.raises(AssertionError, match="read nothing"):
        _strings_in("scripts/night-repair.py", "no_such_function")


def test_the_prompt_tells_the_session_to_leave_the_tree_dirty():
    prompt = night.build_prompt(night.build_batch(FINDINGS, None))
    assert "Leave the working tree dirty" in prompt
    assert "do not stage anything" in prompt.lower()


def test_every_approved_item_reaches_the_prompt():
    """A prompt that silently dropped an item would have the session report a
    complete pass over work it never saw."""
    batch = night.build_batch(FINDINGS, None)
    prompt = night.build_prompt(batch)
    for rel, finding in FINDINGS:
        assert rel in prompt
        assert finding["summary"] in prompt


# ============================================================
# The batch
# ============================================================

def test_the_batch_is_ordered_and_totalled():
    batch = night.build_batch(FINDINGS, None)
    assert [item["path"] for item in batch["items"]] == [
        "scripts/a.py", "scripts/b.py", "scripts/c.py"]
    assert batch["estimated_minutes"] == 90


def test_a_time_budget_stops_the_batch_and_never_exceeds_itself():
    """40 + 30 is 70, so with 60 minutes only the first item is approved.

    Stopping rather than skipping is the deliberate choice: packing a smaller
    item in behind the one that did not fit uses the night better and makes the
    batch stop being the top of the list, which is the property the operator
    reads it for.
    """
    batch = night.build_batch(FINDINGS, max_minutes=60)
    assert [item["path"] for item in batch["items"]] == ["scripts/a.py"]
    assert batch["estimated_minutes"] == 40
    assert batch["estimated_minutes"] <= 60


def test_a_budget_that_fits_two_items_takes_both():
    """The anchor. A cut that always kept one item would satisfy the test above
    and make every night a single-item night."""
    batch = night.build_batch(FINDINGS, max_minutes=80)
    assert [item["path"] for item in batch["items"]] == ["scripts/a.py",
                                                         "scripts/b.py"]
    assert batch["estimated_minutes"] == 70


def test_a_smaller_item_never_jumps_the_queue_past_one_that_did_not_fit():
    """The property the `break` buys. With a skip, `scripts/c.py` at 20 minutes
    would land in a 60-minute batch ahead of the more severe `scripts/b.py`."""
    batch = night.build_batch(FINDINGS, max_minutes=60)
    assert "scripts/c.py" not in [item["path"] for item in batch["items"]]


def test_an_item_larger_than_the_whole_budget_is_still_taken_first():
    """Otherwise the largest defect can never be scheduled and quietly outlives
    every night, which is how the worst thing in a queue becomes permanent."""
    huge = [("scripts/big.py", {"summary": "a rewrite", "severity": "high",
                                "estimate_minutes": 600})]
    batch = night.build_batch(huge, max_minutes=60)
    assert [item["path"] for item in batch["items"]] == ["scripts/big.py"]


def test_a_fresh_batch_is_pending():
    assert night.is_pending(night.build_batch(FINDINGS, None)) is True


def test_a_consumed_batch_is_not_pending():
    batch = night.build_batch(FINDINGS, None)
    batch["consumed_at"] = "2026-09-02T01:00:00+04:00"
    assert night.is_pending(batch) is False


def test_an_empty_batch_is_not_pending():
    assert night.is_pending(night.build_batch([], None)) is False


def test_no_batch_at_all_is_not_pending():
    assert night.is_pending(None) is False


# ============================================================
# The dated freeze
# ============================================================
#
# Operator instruction, 2026-09-02: build it, run nothing for five days. A hold
# an assistant merely remembers is not a hold. The release that went out on
# 2026-08-31 was a permission REMEMBERED rather than read, and a forgotten
# freeze is the same shape pointed the other way. So the date lives on disk,
# where it survives a compaction, a new session, and a different assistant.

def _hold(tmp_path: Path, payload) -> Path:
    path = tmp_path / "hold.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")
    return path


def test_a_future_date_holds(tmp_path):
    path = _hold(tmp_path, {"hold_until": "2026-09-07", "reason": "five days"})
    reason = night.hold_reason(path, "2026-09-02")
    assert reason is not None
    assert "2026-09-07" in reason and "five days" in reason


def test_the_hold_lapses_on_its_own_date(tmp_path):
    """`hold_until` is the first day work may run, not the last day it may not.
    An off-by-one here costs a day of silence nobody would notice."""
    path = _hold(tmp_path, {"hold_until": "2026-09-07"})
    assert night.hold_reason(path, "2026-09-07") is None


def test_a_past_date_does_not_hold(tmp_path):
    path = _hold(tmp_path, {"hold_until": "2026-09-07"})
    assert night.hold_reason(path, "2026-09-08") is None


def test_an_absent_hold_file_does_not_hold(tmp_path):
    """The anchor. Absent is the only state that means "no freeze", and a
    function that held on everything would freeze the workspace forever."""
    assert night.hold_reason(tmp_path / "nothing.json", "2026-09-02") is None


@pytest.mark.parametrize("payload,why", [
    ("{not json", "unreadable"),
    ('["a list"]', "malformed"),
    ('{"reason": "no date here"}', "names no date"),
    ('{"hold_until": ""}', "names no date"),
    ('{"hold_until": 20260907}', "names no date"),
])
def test_a_corrupt_hold_file_holds_rather_than_lapsing(tmp_path, payload, why):
    """Absent carries the operator's intent; corrupt carries none, and the safe
    reading of no intent is to do nothing. A guard that cannot tell absent from
    corrupt is not a guard."""
    path = _hold(tmp_path, payload)
    reason = night.hold_reason(path, "2026-09-02")
    assert reason is not None and why in reason


def test_a_held_run_refuses_and_does_not_consume_the_batch(tmp_path, monkeypatch, capsys):
    """The freeze must not silently spend the approval. Consuming the batch
    while refusing to work would mean the operator's approval evaporated on a
    night nothing ran."""
    batch_path = tmp_path / "batch.json"
    night.save_batch(batch_path, night.build_batch(FINDINGS, None))
    monkeypatch.setattr(night, "BATCH_PATH", batch_path)
    monkeypatch.setattr(night, "HOLD_PATH",
                        _hold(tmp_path, {"hold_until": "2099-01-01"}))
    monkeypatch.setattr(night.shutil, "which",
                        lambda _n: pytest.fail("a held run reached the CLI"))

    assert night.cmd_run(timeout_s=5) == 0
    assert "HELD" in capsys.readouterr().err
    assert json.loads(batch_path.read_text(encoding="utf-8"))["consumed_at"] is None


def test_the_live_hold_file_is_in_force_today():
    """The state the operator asked for, asserted rather than assumed.

    This test goes green on its own once the date passes, which is correct: it
    pins that a freeze the operator set is honoured while it stands, not that a
    freeze exists forever.
    """
    from datetime import datetime

    from scripts.utils.workspace import get_default_tz

    if not night.HOLD_PATH.exists():
        pytest.skip("no hold file in this clone; nothing is frozen")
    today = datetime.now(get_default_tz()).date().isoformat()
    payload = json.loads(night.HOLD_PATH.read_text(encoding="utf-8"))
    if today >= payload["hold_until"]:
        pytest.skip(f"the hold lapsed on {payload['hold_until']}")
    assert night.hold_reason(night.HOLD_PATH, today) is not None


# ============================================================
# Refusals
# ============================================================

def _lift_hold(monkeypatch, tmp_path: Path) -> None:
    """Point HOLD_PATH at a file that does not exist.

    The tests below describe what `--run` does once the freeze has lapsed. With
    the live `config/automation-hold.json` in force they would all stop at the
    hold, which is correct behaviour and useless evidence: a suite that only
    ever sees the refusal never checks the thing being refused.
    """
    monkeypatch.setattr(night, "HOLD_PATH", tmp_path / "no-hold.json")


def test_run_refuses_when_nothing_is_approved(tmp_path, monkeypatch, capsys):
    """Nothing approves itself. The day is when the operator decides."""
    _lift_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(night, "BATCH_PATH", tmp_path / "none.json")
    assert night.cmd_run(timeout_s=5) == 0
    assert "no approved batch" in capsys.readouterr().out


def test_run_refuses_a_batch_already_consumed(tmp_path, monkeypatch, capsys):
    batch = night.build_batch(FINDINGS, None)
    batch["consumed_at"] = "2026-09-02T01:00:00+04:00"
    path = tmp_path / "batch.json"
    night.save_batch(path, batch)
    monkeypatch.setattr(night, "BATCH_PATH", path)
    _lift_hold(monkeypatch, tmp_path)
    assert night.cmd_run(timeout_s=5) == 0
    assert "no approved batch" in capsys.readouterr().out


def test_an_unreadable_batch_refuses_rather_than_reading_as_none(tmp_path):
    """A corrupt file read as 'no batch' is a silent no-op, night after night,
    with a green exit code every morning."""
    path = tmp_path / "batch.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(SystemExit):
        night.load_batch(path)


def test_a_malformed_batch_refuses(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text('{"items": "not a list"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        night.load_batch(path)


def test_an_absent_batch_is_none_not_an_error(tmp_path):
    """The anchor for the two cases above: the first ever run has no file."""
    assert night.load_batch(tmp_path / "nothing.json") is None


# ============================================================
# The run, driven for real
# ============================================================

def test_the_batch_is_consumed_before_the_session_starts(tmp_path, monkeypatch):
    """A crash mid-pass must not leave a batch that fires again over a tree the
    first pass already changed."""
    path = tmp_path / "batch.json"
    night.save_batch(path, night.build_batch(FINDINGS, None))
    monkeypatch.setattr(night, "BATCH_PATH", path)
    monkeypatch.setattr(night, "LOG_DIR", tmp_path / "logs")
    _lift_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(night.shutil, "which", lambda _name: "/bin/true")

    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["consumed_at_launch"] = json.loads(
            path.read_text(encoding="utf-8"))["consumed_at"]
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(night.subprocess, "run", _fake_run)
    assert night.cmd_run(timeout_s=5) == 0
    assert seen["consumed_at_launch"] is not None


def test_the_session_is_launched_with_the_prompt_and_no_other_argument(
        tmp_path, monkeypatch):
    """`claude -p <prompt>` and nothing else. A flag that skipped permissions,
    or a `--dangerously-*` switch, would take the walls down on the one night
    nobody is watching."""
    path = tmp_path / "batch.json"
    night.save_batch(path, night.build_batch(FINDINGS, None))
    monkeypatch.setattr(night, "BATCH_PATH", path)
    monkeypatch.setattr(night, "LOG_DIR", tmp_path / "logs")
    _lift_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(night.shutil, "which", lambda _name: "/usr/bin/claude")

    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(night.subprocess, "run", _fake_run)
    night.cmd_run(timeout_s=5)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/claude"
    assert cmd[1] == "-p"
    assert len(cmd) == 3, f"unexpected extra arguments: {cmd[2:]}"
    assert not any(str(part).startswith("--") for part in cmd), cmd


def test_a_missing_claude_binary_is_reported_not_swallowed(tmp_path, monkeypatch, capsys):
    path = tmp_path / "batch.json"
    night.save_batch(path, night.build_batch(FINDINGS, None))
    monkeypatch.setattr(night, "BATCH_PATH", path)
    _lift_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(night.shutil, "which", lambda _name: None)

    assert night.cmd_run(timeout_s=5) == 2
    assert "not on PATH" in capsys.readouterr().err


def test_a_session_that_overruns_is_stopped_and_recorded(tmp_path, monkeypatch, capsys):
    path = tmp_path / "batch.json"
    night.save_batch(path, night.build_batch(FINDINGS, None))
    monkeypatch.setattr(night, "BATCH_PATH", path)
    monkeypatch.setattr(night, "LOG_DIR", tmp_path / "logs")
    _lift_hold(monkeypatch, tmp_path)
    monkeypatch.setattr(night.shutil, "which", lambda _name: "/usr/bin/claude")

    def _timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(night.subprocess, "run", _timeout)
    assert night.cmd_run(timeout_s=5) == 2
    assert "exceeded" in capsys.readouterr().err
    assert (tmp_path / "logs").exists()


# ============================================================
# The night never certifies its own work
# ============================================================

def test_nothing_in_this_module_writes_a_verdict_to_the_rotation_ledger():
    """Asked of the AST, so the module docstring can name the ledger while
    explaining exactly this boundary. A text scan would go red on the sentence
    that documents the rule, which teaches people to delete the sentence.
    """
    tree = ast.parse((ROOT / "scripts" / "night-repair.py")
                     .read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "save_ledger" not in called, (
        "the night pass writes the rotation ledger; a repair that records its "
        "own verdict is an agent marking its own homework")

    strings = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert "fixed" not in strings, (
        "the night pass names the 'fixed' verdict; only the operator's "
        "acceptance closes a finding")
