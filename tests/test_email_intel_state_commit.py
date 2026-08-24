"""The /email-intel fetch must not burn message ids before the CEO approves.

`scripts/email-intelligence.py` used to mark every fetched message processed
and stamp `last_run` at the END OF THE FETCH, inside the same run that only
PROPOSES actions. The skill's Phase 3 approval gate came afterwards, so a
digest the CEO skipped entirely -- or a session that died between the fetch
and the digest -- still left those messages recorded as handled. Phase 1's
dedupe filter then dropped them on the next run and they were never seen
again. Observed 2026-08-09.

The fix splits fetch from commit: `--json` (the skill-consumption mode) emits
a `state_commit` block and writes nothing, and `--commit-state FILE` replays
that block once the approved actions have been executed.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "email_intelligence", ROOT / "scripts" / "email-intelligence.py"
)
ei = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ei)


@pytest.fixture
def state(tmp_path):
    return ei.StateManager(path=tmp_path / "state.json")


def _payload(**over):
    base = {
        "message_ids": ["<a@31c.io>", "<b@31c.io>"],
        "conversations": [{"id": "conv-1", "topic": "ACME | Northwind | Demo"}],
        "inbox_count": 2,
        "sent_count": 0,
        "noise_filtered": 1,
        "cutoff": "2026-08-08T19:00:00+00:00",
    }
    base.update(over)
    return base


# --- the seam itself -------------------------------------------------


def test_commit_state_records_ids_conversations_and_stamps(state):
    ei.commit_state(state, _payload())

    assert state.data["processed_message_ids"] == ["<a@31c.io>", "<b@31c.io>"]
    assert "conv-1" in state.data["conversations"]
    assert state.data["last_run"] is not None
    assert state.data["last_run_status"] == "complete"
    assert state.data["last_inbox_datetime"] == "2026-08-08T19:00:00+00:00"
    assert state.data["stats"]["total_runs"] == 1
    assert state.data["stats"]["total_conversations"] == 1
    assert state.data["stats"]["total_filtered"] == 1


def test_commit_state_leaves_sent_stamp_alone_when_nothing_was_sent(state):
    ei.commit_state(state, _payload(sent_count=0))
    assert state.data["last_sent_datetime"] is None


def test_commit_state_is_idempotent(state):
    """Replaying the same payload must not double-count ids."""
    payload = _payload()
    ei.commit_state(state, payload)
    ei.commit_state(state, payload)

    assert state.data["processed_message_ids"] == ["<a@31c.io>", "<b@31c.io>"]


def test_commit_state_persists_to_disk(state):
    ei.commit_state(state, _payload())
    state.save()

    reloaded = json.loads(state.path.read_text(encoding="utf-8"))
    assert reloaded["processed_message_ids"] == ["<a@31c.io>", "<b@31c.io>"]


# --- the deferral contract -------------------------------------------


def test_json_output_carries_a_commit_block_covering_filtered_messages():
    """The block must carry EVERY id the fetch consumed, not only the ids that
    survived into a conversation. Internal-only and noise-filtered threads are
    dropped before `conversations` is built; committing from `conversations`
    alone would resurface them on every subsequent run."""
    out = ei.build_output(
        conversations=[],
        analyses=[],
        run_info={"timestamp": "2026-08-09T19:15:05+00:00"},
        state_commit=_payload(),
    )

    assert out["state_commit"]["message_ids"] == ["<a@31c.io>", "<b@31c.io>"]
    assert out["conversations"] == []


def test_build_output_without_a_commit_block_omits_the_key():
    out = ei.build_output(conversations=[], analyses=[], run_info={})
    assert "state_commit" not in out


def test_commit_from_file_reads_the_block_a_json_run_emitted(tmp_path):
    """End to end across the seam: what --json writes is what --commit-state eats."""
    emitted = ei.build_output(
        conversations=[], analyses=[], run_info={}, state_commit=_payload()
    )
    path = tmp_path / "run.json"
    path.write_text(json.dumps(emitted), encoding="utf-8")

    st = ei.StateManager(path=tmp_path / "state.json")
    ei.commit_state_from_file(path, state=st)

    assert st.data["processed_message_ids"] == ["<a@31c.io>", "<b@31c.io>"]
    assert json.loads((tmp_path / "state.json").read_text())["last_run"] is not None


def test_commit_from_file_rejects_output_with_no_commit_block(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"run_info": {}, "conversations": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="state_commit"):
        ei.commit_state_from_file(path, state=ei.StateManager(path=tmp_path / "s.json"))


# --- the wiring in main(), which is what actually regressed ----------


@pytest.fixture
def fake_fetch(monkeypatch, tmp_path):
    """Drive main()'s time-window path with no Exchange and no LLM.

    One inbox message, filtered clean, grouped into one external
    conversation. Returns the StateManager main() will use, so a test can
    assert on what the run did or did not record.
    """
    st = ei.StateManager(path=tmp_path / "state.json")
    monkeypatch.setattr(ei, "StateManager", lambda *a, **k: st)
    monkeypatch.setattr(ei, "connect_exchange", lambda: object())
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)
    monkeypatch.setattr(ei, "enrich_conversation", lambda *a, **k: None)
    monkeypatch.setattr(
        ei, "analyze_conversations",
        lambda convs, *a, **k: [{"priority": "P1", "summary": "s"} for _ in convs],
    )

    msg = {
        "message_id": "<fetched@31c.io>",
        "conversation_id": "conv-x",
        "conversation_topic": "Northwind demo",
        "item_class": "IPM.Note",
        "subject": "Northwind demo",
        "sender_email": "them@example.com",
        "sender_name": "Them",
        "to": [{"email": "misha.hanin@31c.io", "name": "Misha"}],
        "cc": [],
        "body_preview": "hello",
        "body": "hello",
        "datetime": "2026-08-09T18:24:08+00:00",
        "direction": "incoming",
    }
    # `fetch_emails` returns (messages, truncated) since 2026-08-24: the 100-row
    # cap was invisible, so the caller could not report it.
    monkeypatch.setattr(
        ei, "fetch_emails",
        lambda account, folder, cutoff: ([msg], False) if folder == "inbox" else ([], False),
    )
    return st


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["email-intelligence.py", *argv])
    ei.main()
    return capsys.readouterr().out


def test_json_run_does_not_commit_state(fake_fetch, monkeypatch, capsys):
    """THE REGRESSION. --json only proposes; approval happens afterwards in
    the skill, so the fetch must leave state untouched."""
    out = _run(monkeypatch, capsys, "--json")

    assert fake_fetch.data["processed_message_ids"] == []
    assert fake_fetch.data["last_run"] is None
    assert not fake_fetch.path.exists(), "--json wrote state.json"

    assert json.loads(out)["state_commit"]["message_ids"] == ["<fetched@31c.io>"]


def test_terminal_run_still_commits_inline(fake_fetch, monkeypatch, capsys):
    """Terminal mode has no approval phase - behaviour is unchanged."""
    _run(monkeypatch, capsys, "--verbose")

    assert fake_fetch.data["processed_message_ids"] == ["<fetched@31c.io>"]
    assert fake_fetch.data["last_run"] is not None


def test_dry_run_commits_nothing_in_terminal_mode(fake_fetch, monkeypatch, capsys):
    _run(monkeypatch, capsys, "--dry-run")
    assert fake_fetch.data["processed_message_ids"] == []


def test_json_then_commit_state_is_the_full_round_trip(
    fake_fetch, monkeypatch, capsys, tmp_path
):
    """What the skill actually does: fetch, approve, then commit."""
    out = _run(monkeypatch, capsys, "--json")
    run_file = tmp_path / "run.json"
    run_file.write_text(out, encoding="utf-8")
    assert fake_fetch.data["processed_message_ids"] == []

    _run(monkeypatch, capsys, "--commit-state", str(run_file))

    assert fake_fetch.data["processed_message_ids"] == ["<fetched@31c.io>"]
    assert "conv-x" in fake_fetch.data["conversations"]
    assert fake_fetch.path.exists()


def test_commit_state_mode_never_touches_exchange(monkeypatch, capsys, tmp_path):
    """It is a pure state replay - a broken mailbox must not block a commit."""
    def explode():
        raise AssertionError("connect_exchange called in --commit-state mode")

    monkeypatch.setattr(ei, "connect_exchange", explode)
    st = ei.StateManager(path=tmp_path / "state.json")
    monkeypatch.setattr(ei, "StateManager", lambda *a, **k: st)

    run_file = tmp_path / "run.json"
    run_file.write_text(
        json.dumps(ei.build_output([], [], {}, state_commit=_payload())), encoding="utf-8"
    )
    _run(monkeypatch, capsys, "--commit-state", str(run_file))

    assert st.data["processed_message_ids"] == ["<a@31c.io>", "<b@31c.io>"]


def test_commit_state_mode_exits_nonzero_on_a_block_less_file(
    monkeypatch, capsys, tmp_path
):
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps({"conversations": []}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, capsys, "--commit-state", str(run_file))
    assert exc.value.code == 1


# --- the cap still holds ---------------------------------------------


def test_commit_state_respects_the_500_id_cap(state):
    state.data["processed_message_ids"] = [f"<{i}@x>" for i in range(500)]
    ei.commit_state(state, _payload(message_ids=["<new@x>"]))

    ids = state.data["processed_message_ids"]
    assert len(ids) == 500
    assert ids[-1] == "<new@x>"
    assert "<0@x>" not in ids
