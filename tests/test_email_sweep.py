"""Tests for scripts/email-sweep.py -- the /email-intel action state machine.

Covers: propose assigns sequential ids + tiers + proposed status; list/pending
filtering; approve/skip/edit/set transitions; the gated-default for unknown
types; the illegal-transition guard; and crash-resume (pending after partial
execution). The module is loaded by path because its filename is kebab-case.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("email_sweep", ROOT / "scripts" / "email-sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

import argparse


def _args(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _write_payload(tmp_path, actions):
    p = tmp_path / "proposed.json"
    p.write_text(json.dumps(actions), encoding="utf-8")
    return p


SAMPLE = [
    {"type": "crm_log", "title": "CRM-log Chris Doyle - mNDA", "priority": "P1", "target": "chris-doyle"},
    {"type": "send_reply", "title": "Reply Pat Nolan", "priority": "P1"},
    {"type": "pipeline", "title": "Northwind stage -> 2026-06-02", "priority": "P1"},
    {"type": "weird_unknown", "title": "Mystery action"},
]


def test_propose_assigns_ids_tiers_and_status(tmp_path):
    payload = _write_payload(tmp_path, SAMPLE)
    rc = sweep.cmd_propose(tmp_path, _args(file=str(payload), date="2026-06-09"))
    assert rc == 0
    data = sweep._load(tmp_path, "2026-06-09")
    acts = data["actions"]
    assert [a["id"] for a in acts] == [1, 2, 3, 4]
    assert all(a["status"] == "proposed" for a in acts)
    assert acts[0]["tier"] == "local"      # crm_log
    assert acts[1]["tier"] == "gated"      # send_reply
    assert acts[2]["tier"] == "notify"     # pipeline
    assert acts[3]["tier"] == "gated"      # unknown type floors at gated


def test_propose_second_call_continues_ids(tmp_path):
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE[:2])), date="2026-06-09"))
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE[2:])), date="2026-06-09"))
    data = sweep._load(tmp_path, "2026-06-09")
    assert [a["id"] for a in data["actions"]] == [1, 2, 3, 4]


def test_propose_skips_malformed(tmp_path):
    bad = [{"title": "no type"}, {"type": "crm_log"}, {"type": "task", "title": "ok"}]
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, bad)), date="2026-06-09"))
    data = sweep._load(tmp_path, "2026-06-09")
    assert len(data["actions"]) == 1
    assert data["actions"][0]["title"] == "ok"


def test_approve_and_skip(tmp_path):
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE)), date="2026-06-09"))
    assert sweep.cmd_approve(tmp_path, _args(ids=[1, 3], date="2026-06-09")) == 0
    assert sweep.cmd_skip(tmp_path, _args(ids=[4], date="2026-06-09", note="not needed")) == 0
    data = sweep._load(tmp_path, "2026-06-09")
    by_id = {a["id"]: a for a in data["actions"]}
    assert by_id[1]["status"] == "approved"
    assert by_id[3]["status"] == "approved"
    assert by_id[4]["status"] == "skipped"
    assert by_id[4]["note"] == "not needed"
    assert by_id[2]["status"] == "proposed"


def test_edit_records_note_and_approves(tmp_path):
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE)), date="2026-06-09"))
    assert sweep.cmd_edit(tmp_path, _args(id=2, note="replace Yerk with Kesha", date="2026-06-09")) == 0
    a = next(a for a in sweep._load(tmp_path, "2026-06-09")["actions"] if a["id"] == 2)
    assert a["status"] == "approved"
    assert a["note"] == "replace Yerk with Kesha"


def test_set_execution_outcome_and_illegal_transition(tmp_path):
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE)), date="2026-06-09"))
    # cannot mark a still-proposed action done (must be approved/executing first)
    assert sweep.cmd_set(tmp_path, _args(id=1, status="done", note=None, date="2026-06-09")) == 1
    # approve -> executing -> done is the legal path
    sweep.cmd_approve(tmp_path, _args(ids=[1], date="2026-06-09"))
    assert sweep.cmd_set(tmp_path, _args(id=1, status="executing", note=None, date="2026-06-09")) == 0
    assert sweep.cmd_set(tmp_path, _args(id=1, status="done", note="logged", date="2026-06-09")) == 0
    a = next(a for a in sweep._load(tmp_path, "2026-06-09")["actions"] if a["id"] == 1)
    assert a["status"] == "done"
    assert a["note"] == "logged"


def test_pending_is_resume_set_after_partial_execution(tmp_path):
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE)), date="2026-06-09"))
    sweep.cmd_approve(tmp_path, _args(ids=[1, 2, 3], date="2026-06-09"))
    sweep.cmd_set(tmp_path, _args(id=1, status="done", note=None, date="2026-06-09"))
    sweep.cmd_skip(tmp_path, _args(ids=[4], date="2026-06-09", note=None))
    # #1 done, #4 skipped -> terminal; #2 approved, #3 approved -> still pending
    data = sweep._load(tmp_path, "2026-06-09")
    pending = [a for a in data["actions"] if a["status"] not in sweep.TERMINAL]
    assert sorted(a["id"] for a in pending) == [2, 3]


def test_mutate_missing_file_returns_2(tmp_path):
    assert sweep.cmd_approve(tmp_path, _args(ids=[1], date="2026-06-09")) == 2


def test_mutate_unknown_id_returns_1(tmp_path):
    sweep.cmd_propose(tmp_path, _args(file=str(_write_payload(tmp_path, SAMPLE)), date="2026-06-09"))
    assert sweep.cmd_approve(tmp_path, _args(ids=[99], date="2026-06-09")) == 1


def test_unknown_and_send_types_floor_at_gated():
    """M1: this table is a separate namespace from tool_risk.py's ledger, so it
    needs its own pin on the send-gate floor (mirrors test_tool_risk.py). Every
    send_* type and any unknown type must resolve to the gated tier."""
    for t in ("send_reply", "send_reply_all", "send_forward", "send_new"):
        tier, tag = sweep._tier_for(t)
        assert tier == "gated", f"{t} must be gated, got {tier}"
        assert tag == "send-gated"
    # Unknown / unclassified types floor at gated (friction-maximal default).
    for t in ("totally_new_send_type", "", "telegram_send", "wire_transfer"):
        assert sweep._tier_for(t)[0] == "gated", f"unknown type {t!r} must floor at gated"
    # Non-send types keep their declared, lower-friction tiers.
    assert sweep._tier_for("crm_log")[0] == "local"
    assert sweep._tier_for("pipeline")[0] == "notify"
