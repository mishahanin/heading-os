#!/usr/bin/env python3
"""Five decode fixes landed with no test, because the agent died mid-batch.

On 2026-09-01 a shard auditor measured five reads, confirmed all five raised,
applied the fixes, and was killed by a session rate limit at the moment it
started writing the guard. The production edits survived; the test did not
exist. Five unguarded fixes is the same defect this campaign keeps finding in
the tree itself, so the guard is written here rather than left for the tree to
lose the reasoning again.

`UnicodeDecodeError` is a `ValueError` and a SIBLING of `json.JSONDecodeError`,
not a subclass. The decode fails inside `read_text` before `json.loads` or
`yaml.safe_load` is handed a single byte, so `except (OSError,
json.JSONDecodeError)` and `except yaml.YAMLError` both walk straight past it.

MEASURED here, each function driven with a file holding one lone `0xff`:

    action-queue-execute.main    RAISED -> prints `[]`, names the file, exit 1
    action-queue.cmd_deposit     RAISED -> names the file on stderr, exit 1
    aggregate-crm.write_audit_log RAISED -> warns and keeps the written entry
    memory-index.load_config     RAISED -> SystemExit(1) with a named message
    ops-radar.load_json          RAISED -> `{}`

Two of the five are worse than a crash report.

`action-queue-execute` is the send queue. Its own comment says the caller
contract is "capture this stdout and apply the status changes", so a traceback
with no JSON array on stdout means the caller applies nothing and reports
success, and every approved card is dropped from the run with no diagnostic.

`ops-radar.load_json` reads the ack state that silences alarms. It took the
whole radar pass out, which is the signal-killing shape `queue_state` was
already fixed for: one of N copies, again.

The over-refusal anchor matters as much as the defect. A fix that skipped every
file containing a high byte would satisfy every defect case below while
silently dropping real data, so each reader is also driven with genuine,
valid, accented UTF-8 and must still read it. Those cases are written as
Python escapes so this file stays pure ASCII.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# One lone 0xff. A legal filesystem byte, and never legal UTF-8 anywhere.
BAD = b"\xff"
# Real UTF-8 above ASCII. The anchor against a fix that refuses any high byte.
ACCENTED = "caf\u00e9 latt\u00e9 r\u00e9sum\u00e9"


def _load(rel: str, name: str) -> types.ModuleType:
    """Load a hyphenated script by path; it cannot be imported by name.

    Loaded fresh inside each test rather than at module scope, because these
    modules resolve the data root when they run and a module cached from an
    earlier test would carry the earlier test's `tmp_path`.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    """Pin the data-root seam at a scratch tree for the whole test."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# 1 - the send queue. The worst of the five to lose.
# ---------------------------------------------------------------------------

def _queue_path(data_root: Path) -> Path:
    p = data_root / "outputs" / "operations" / "action-queue" / "queue.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def test_the_send_queue_refuses_an_undecodable_store_and_says_so(
        data_root, capsys):
    """The defect: a raw traceback, and no JSON array on stdout at all.

    Both halves are asserted. Exiting non-zero is not enough on its own: the
    documented contract is that this command always prints a JSON array for
    the caller to apply, so a refusal that printed nothing would leave the
    caller parsing an empty string.
    """
    _queue_path(data_root).write_bytes(b'{"actions": [' + BAD + b"]}")
    rc = _load("scripts/action-queue-execute.py", "aqe_bad").main()
    captured = capsys.readouterr()

    assert rc == 1, (
        "an unreadable send queue did not report a failure, so the caller "
        "applies nothing and reports success")
    assert captured.out.strip() == "[]", (
        f"no JSON array reached stdout: {captured.out!r}. The caller contract "
        f"is 'capture this stdout and apply the status changes'.")
    assert "queue.json" in captured.err, (
        f"the unreadable file was never named: {captured.err!r}")


def test_the_send_queue_still_reads_a_document_with_accented_text(
        data_root, capsys):
    """The over-refusal anchor, and it must not send anything.

    The single card is deliberately NOT approved, so the executor's loop skips
    it. This test asserts the document is READ, never that a send happens.
    """
    _queue_path(data_root).write_text(
        json.dumps({"actions": [{"status": "draft", "subject": ACCENTED}]}),
        encoding="utf-8")
    rc = _load("scripts/action-queue-execute.py", "aqe_ok").main()
    captured = capsys.readouterr()

    assert rc == 0, (
        f"a valid queue holding accented UTF-8 was refused, so the decode fix "
        f"widened into a blanket refusal: {captured.err!r}")
    assert json.loads(captured.out) == [], (
        "an unapproved card produced a result, which means this anchor is "
        "exercising a send path it was written to stay out of")


def test_an_absent_send_queue_is_still_not_a_failure(data_root, capsys):
    """The clean-path anchor. Absent and unreadable must not share an answer.

    Without this, widening the handler until it swallowed everything would
    pass both tests above.
    """
    rc = _load("scripts/action-queue-execute.py", "aqe_absent").main()
    captured = capsys.readouterr()

    assert rc == 0, "an absent queue is an empty queue, not a failure"
    assert captured.out.strip() == "[]"


# ---------------------------------------------------------------------------
# 2 - the deposit command, which reads a file named on the command line
# ---------------------------------------------------------------------------

def _args(path: Path):
    return types.SimpleNamespace(file=str(path))


def test_deposit_refuses_an_undecodable_cards_file(data_root, tmp_path, capsys):
    cards = tmp_path / "cards.json"
    cards.write_bytes(b"[" + BAD + b"]")
    rc = _load("scripts/action-queue.py", "aq_bad").cmd_deposit(
        ROOT, data_root, _args(cards))
    err = capsys.readouterr().err

    assert rc == 1, (
        "an unreadable cards file did not produce the documented exit 1; the "
        "module contract is 'Exit codes: 0 ok, 1 request/usage error'")
    assert "cannot read" in err, f"no diagnostic was printed: {err!r}"


def test_deposit_still_reads_a_cards_file_with_accented_text(
        data_root, tmp_path, capsys):
    """Over-refusal anchor. It must get PAST the read.

    Asserted by what the failure says, not by the exit code: a card missing
    required fields may still be rejected downstream, and that rejection is
    not this test's subject. What matters is that the refusal is no longer the
    READ.
    """
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps([{"subject": ACCENTED}]), encoding="utf-8")
    _load("scripts/action-queue.py", "aq_ok").cmd_deposit(
        ROOT, data_root, _args(cards))
    err = capsys.readouterr().err

    assert "cannot read" not in err, (
        f"a valid cards file holding accented UTF-8 was refused at the read, "
        f"so the decode fix became a blanket refusal: {err!r}")


def test_deposit_still_refuses_a_cards_file_that_is_not_a_list(
        data_root, tmp_path, capsys):
    """The shape check below the read must survive the handler widening."""
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    rc = _load("scripts/action-queue.py", "aq_shape").cmd_deposit(
        ROOT, data_root, _args(cards))
    err = capsys.readouterr().err

    assert rc == 1
    assert "must be a JSON array" in err, (
        f"the shape refusal was lost when the read handler widened: {err!r}")


# ---------------------------------------------------------------------------
# 3 - the aggregation audit log. The entry is already on disk when this runs.
# ---------------------------------------------------------------------------

def test_the_audit_trim_does_not_raise_over_a_completed_aggregation(
        tmp_path, capsys):
    """A whole finished run must not be lost to the log-capping step.

    Two jaws. The call must return, AND the entry it appended must still be on
    disk: a handler that swallowed the error while losing the write would pass
    a return-only assertion.
    """
    aggregated = tmp_path / "aggregated"
    audit = aggregated / "audit"
    audit.mkdir(parents=True)
    log = audit / "aggregation-log.jsonl"
    log.write_bytes(b'{"timestamp": "old"}\n' + BAD + b"\n")

    mod = _load("scripts/aggregate-crm.py", "agg_bad")
    mod.write_audit_log(aggregated, 12, 3, 4, [])
    err = capsys.readouterr().err

    assert log.exists(), "the audit log was destroyed by the trim step"
    raw = log.read_bytes()
    assert b'"contacts_processed": 12' in raw, (
        "the entry this run appended is not on disk, so the aggregation was "
        "recorded as done while its record was lost")
    assert "aggregation-log.jsonl" in err, (
        f"the trim failed and said nothing, which is the silent-drop half of "
        f"this defect class: {err!r}")


def test_the_audit_trim_is_silent_when_the_log_reads_cleanly(tmp_path, capsys):
    """The clean-path anchor, and it is the one that catches over-warning.

    A fix that printed the warning unconditionally would satisfy the test
    above on every run, and a warning that fires on a healthy run is noise
    that teaches the operator to skip the log.
    """
    aggregated = tmp_path / "aggregated"
    (aggregated / "audit").mkdir(parents=True)
    log = aggregated / "audit" / "aggregation-log.jsonl"
    log.write_text(json.dumps({"timestamp": ACCENTED}) + "\n", encoding="utf-8")

    mod = _load("scripts/aggregate-crm.py", "agg_ok")
    mod.write_audit_log(aggregated, 7, 1, 2, [])
    err = capsys.readouterr().err

    assert "could not trim" not in err, (
        f"a healthy audit log produced a trim warning: {err!r}")
    assert b'"contacts_processed": 7' in log.read_bytes()


# ---------------------------------------------------------------------------
# 4 - the memory index config. Its contract is a named message and exit 1.
# ---------------------------------------------------------------------------

def _config_root(tmp_path: Path, payload: bytes) -> Path:
    mod = _load("scripts/memory-index.py", "mi_probe")
    path = tmp_path / mod.CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return tmp_path


def test_the_index_config_exits_with_a_named_message(tmp_path, capsys):
    """The defect was a raw traceback where two lines promise exit 1."""
    root = _config_root(tmp_path, b"model: bge-m3\nhost: " + BAD + b"\n")
    mod = _load("scripts/memory-index.py", "mi_bad")

    with pytest.raises(SystemExit) as exited:
        mod.load_config(root)

    assert exited.value.code == 1, (
        "an unreadable config did not produce the documented exit 1")
    assert "Cannot parse" in capsys.readouterr().err


def test_the_index_config_still_loads_with_accented_values(tmp_path):
    """Over-refusal anchor: a config carrying real accented UTF-8 must load."""
    root = _config_root(
        tmp_path, f"model: bge-m3\nnote: {ACCENTED}\n".encode("utf-8"))
    cfg = _load("scripts/memory-index.py", "mi_ok").load_config(root)

    assert cfg["note"] == ACCENTED, (
        "a config holding valid accented UTF-8 did not survive the read")


def test_an_absent_index_config_is_still_a_different_message(tmp_path, capsys):
    """Absent and unreadable must not collapse into one answer."""
    mod = _load("scripts/memory-index.py", "mi_absent")
    with pytest.raises(SystemExit):
        mod.load_config(tmp_path)

    err = capsys.readouterr().err
    assert "not found" in err and "Cannot parse" not in err, (
        f"an absent config reported as an unparseable one: {err!r}")


# ---------------------------------------------------------------------------
# 5 - the radar ack state, which silences alarms
# ---------------------------------------------------------------------------

def test_the_radar_state_reader_returns_empty_over_an_unreadable_file(
        tmp_path):
    """The defect took the whole radar pass out on one torn write."""
    state = tmp_path / "ack.json"
    state.write_bytes(b'{"ack": "' + BAD + b'"}')

    assert _load("scripts/ops-radar.py", "radar_bad").load_json(state) == {}, (
        "load_json did not degrade to {} over an unreadable ack file, so one "
        "torn write ends the radar run that reads it")


def test_the_radar_state_reader_still_reads_accented_values(tmp_path):
    state = tmp_path / "ack.json"
    state.write_text(json.dumps({"note": ACCENTED}), encoding="utf-8")

    assert _load("scripts/ops-radar.py", "radar_ok").load_json(state) == {
        "note": ACCENTED}, "a valid ack file with accented text was discarded"


# ---------------------------------------------------------------------------
# The structural half. Cheap, and it fails on the exact edit that reverts any
# one of the five, even if someone deleted a behavioural test above.
# ---------------------------------------------------------------------------

FIXED = [
    ("scripts/action-queue-execute.py", "main"),
    ("scripts/action-queue.py", "cmd_deposit"),
    ("scripts/aggregate-crm.py", "write_audit_log"),
    ("scripts/memory-index.py", "load_config"),
    ("scripts/ops-radar.py", "load_json"),
]


@pytest.mark.parametrize("rel,func", FIXED)
def test_every_decode_in_the_function_sits_under_a_handler_that_catches_it(
        rel, func):
    """Asked of the AST. A grep matches the comment that explains the fix.

    Two things are checked together, because either alone is satisfied by the
    wrong code: every decoding read in the function must sit inside a `try`,
    and some handler in it must name an exception that can catch a
    `UnicodeDecodeError`.
    """
    path = ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == func), None)
    assert node is not None, f"{func} is no longer defined in {rel}"

    guarded: set[int] = set()
    catches: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Try):
            continue
        for stmt in sub.body:
            for inner in ast.walk(stmt):
                guarded.add(id(inner))
        for handler in sub.handlers:
            if handler.type is None:
                catches.add("<bare>")
                continue
            parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
                     else [handler.type])
            for p in parts:
                if isinstance(p, ast.Name):
                    catches.add(p.id)
                elif isinstance(p, ast.Attribute):
                    catches.add(p.attr)

    reads = [c for c in ast.walk(node)
             if isinstance(c, ast.Call)
             and getattr(c.func, "attr", None) == "read_text"
             and not any(k.arg == "errors" for k in c.keywords)]
    assert reads, (
        f"{rel}::{func} no longer calls read_text, so either it was rewritten "
        f"or this test is looking at the wrong shape, and either way it is "
        f"measuring nothing until that is resolved")

    unguarded = [c.lineno for c in reads if id(c) not in guarded]
    assert not unguarded, (
        f"{rel}::{func} decodes a file at line(s) {unguarded} with no try at "
        f"all")
    assert catches & {"UnicodeDecodeError", "UnicodeError", "ValueError"}, (
        f"{rel}::{func} catches {sorted(catches)}. None of those catch a "
        f"UnicodeDecodeError, which is a ValueError and a SIBLING of "
        f"json.JSONDecodeError, not a subclass of it.")
